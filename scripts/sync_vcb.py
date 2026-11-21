#!/usr/bin/env python3
"""Collect VCB daily rates into vcb-exchange-rates, replacing n8n v5.1.

Only the most recently verified payload for a calendar date is stored in raw/.
The historical cursor advances only after a valid older-day response is staged.
All writes are committed atomically by the surrounding GitHub Actions job.
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

API = 'https://www.vietcombank.com.vn/api/exchangerates'
CURRENCY_CODE = re.compile(r'^[A-Z]{3}$')
SENTINEL = '0001-01-01T00:00:00'
MAX_BODY_BYTES = 2_000_000


def vietnam_today():
    return dt.datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date()


def fetch_vcb(date: dt.date):
    url = API + '?' + urllib.parse.urlencode({'date': date.isoformat()})
    request = urllib.request.Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': 'REBOOSTLAB-VCB-FX-Collector/1.0',
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                code = response.status
                if code != 200:
                    raise ValueError(f'VCB API HTTP {code} for {date}')
                body = response.read(MAX_BODY_BYTES + 1)
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError('VCB response exceeds safe maximum size')
            return json.loads(body.decode('utf-8-sig'))
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise RuntimeError(f'VCB request failed after 3 attempts for {date}: {error}') from error
            time.sleep(3 * (attempt + 1))
        except json.JSONDecodeError as error:
            raise ValueError(f'VCB returned invalid JSON for {date}: {error}') from error
    raise AssertionError('Unreachable retry state')


def validate(payload, date: dt.date):
    day = date.isoformat()
    if not isinstance(payload, dict):
        raise ValueError(f'VCB JSON is not an object: {day}')
    count, rows = payload.get('Count'), payload.get('Data')
    if type(count) is not int or not isinstance(rows, list) or count != len(rows):
        raise ValueError(f'VCB Count/Data mismatch: {day}')
    api_date = str(payload.get('Date') or '')
    if count == 0:
        if api_date != SENTINEL and api_date[:10] != day:
            raise ValueError(f'VCB NO_DATA date mismatch: requested={day}, received={api_date}')
        return 'NO_DATA'
    if api_date[:10] != day:
        raise ValueError(f'VCB date mismatch: requested={day}, received={api_date}')
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            raise ValueError(f'Non-object VCB currency row {index}: {day}')
        code = item.get('currencyCode')
        if not isinstance(code, str) or not CURRENCY_CODE.fullmatch(code):
            raise ValueError(f'Invalid currencyCode on row {index}: {day}')
        if not isinstance(item.get('currencyName'), str) or not item['currencyName'].strip():
            raise ValueError(f'Missing currencyName for {code}: {day}')
        for field in ('cash', 'transfer', 'sell'):
            value = item.get(field)
            if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
                raise ValueError(f'Missing/non-numeric {field} for {code}: {day}')
            try:
                number = Decimal(str(value).strip().replace(',', ''))
            except InvalidOperation as error:
                raise ValueError(f'Non-numeric {field} for {code}: {day}') from error
            if not number.is_finite() or not (Decimal(0) <= number < Decimal('1e9')):
                raise ValueError(f'Invalid {field} for {code}: {day}')
    return 'RATES'


def save_if_changed(file: Path, data):
    if file.exists():
        try:
            if json.loads(file.read_text('utf-8')) == data:
                return False
        except (ValueError, UnicodeDecodeError):
            raise ValueError(f'Existing JSON cannot be read safely: {file}') from None
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return True


def stage_day(root: Path, day: dt.date, payload, historical: bool, now=None):
    kind = validate(payload, day)
    date = day.isoformat()
    if kind == 'NO_DATA' and not historical:
        return {'date': date, 'kind': 'NO_DATA_PENDING', 'changed': False, 'count': 0}
    if kind == 'RATES':
        path = root / 'raw' / str(day.year) / f'{date}.json'
        changed = save_if_changed(path, payload)
        marker = root / 'metadata' / 'no-data' / f'{date}.json'
        if marker.exists():
            marker.unlink()
            changed = True
    else:
        if (root / 'raw' / str(day.year) / f'{date}.json').exists():
            raise ValueError(f'VCB now returns NO_DATA for an existing valid rate day: {date}')
        path = root / 'metadata' / 'no-data' / f'{date}.json'
        if path.exists():
            return {'date': date, 'kind': kind, 'changed': False, 'count': 0}
        record = {
            'date': date, 'status': 'NO_DATA',
            'api_date': payload.get('Date'),
            'api_updated_date': payload.get('UpdatedDate'),
            'checked_at': (now or dt.datetime.now(dt.timezone.utc)).isoformat().replace('+00:00', 'Z'),
        }
        changed = save_if_changed(path, record)
    return {'date': date, 'kind': kind, 'changed': changed, 'count': payload['Count']}


def sync(root: Path, today: dt.date, fetch=fetch_vcb, now=None):
    cursor_path = root / 'metadata' / 'cursor.json'
    cursor = json.loads(cursor_path.read_text(encoding='utf-8'))
    try:
        next_date = dt.date.fromisoformat(cursor['next_date'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('metadata/cursor.json has invalid next_date') from error
    if next_date.year < 2020:
        raise ValueError('metadata/cursor.json points outside expected date range')

    current = stage_day(root, today, fetch(today), historical=False, now=now)
    historical = None
    if next_date < today:
        historical = stage_day(root, next_date, fetch(next_date), historical=True, now=now)
        stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat().replace('+00:00', 'Z')
        updated_cursor = {**cursor,
            'next_date': (next_date + dt.timedelta(days=1)).isoformat(),
            'failures': 0,
            'last_processed_date': next_date.isoformat(),
            'last_status': historical['kind'],
            'last_updated_at': stamp,
        }
        if historical['kind'] == 'RATES':
            updated_cursor['last_success_date'] = next_date.isoformat()
        save_if_changed(cursor_path, updated_cursor)
    return {'ok': True, 'today_vn': today.isoformat(),
            'current_day': current, 'backfill': historical,
            'next_backfill_date': (next_date + dt.timedelta(days=1)).isoformat() if historical else next_date.isoformat()}


def main():
    parser = argparse.ArgumentParser(description='Sync VCB source data into this repository')
    parser.add_argument('--root', type=Path, default=Path('.'))
    args = parser.parse_args()
    try:
        result = sync(args.root.resolve(), vietnam_today())
    except Exception as error:
        print(f'VCB_SYNC_FAILED: {error}', file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
