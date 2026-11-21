import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from sync_vcb import sync, stage_day, validate

TODAY = dt.date(2026, 11, 21)
NOW = dt.datetime(2026, 11, 21, 3, 5, tzinfo=dt.timezone.utc)


def payload(day, value='25000.00'):
    return {'Count': 1, 'Date': day.isoformat() + 'T00:00:00',
            'UpdatedDate': day.isoformat() + 'T09:00:00+07:00',
            'Data': [{'currencyName': 'US DOLLAR', 'currencyCode': 'USD',
                      'cash': value, 'transfer': '25600.00', 'sell': '25980.00'}]}


def empty():
    return {'Count': 0, 'Date': '0001-01-01T00:00:00',
            'UpdatedDate': '0001-01-01T00:00:00', 'Data': []}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'metadata').mkdir()
        self.cursor_path = self.root / 'metadata' / 'cursor.json'

    def tearDown(self):
        self.tmp.cleanup()

    def cursor(self, date):
        self.cursor_path.write_text(json.dumps({'next_date': date, 'failures': 0}) + '\n')

    def test_today_written_and_unchanged_run_idempotent(self):
        self.cursor(TODAY.isoformat())
        fetch = lambda day: payload(day)
        first = sync(self.root, TODAY, fetch, NOW)
        self.assertTrue(first['current_day']['changed'])
        file = self.root / 'raw/2026/2026-11-21.json'
        content = file.read_text()
        second = sync(self.root, TODAY, fetch, NOW)
        self.assertFalse(second['current_day']['changed'])
        self.assertEqual(content, file.read_text())
        self.assertEqual('2026-11-21', json.loads(self.cursor_path.read_text())['next_date'])

    def test_intraday_update_replaces_daily_file(self):
        self.cursor(TODAY.isoformat())
        sync(self.root, TODAY, lambda day: payload(day), NOW)
        changed = sync(self.root, TODAY, lambda day: payload(day, '25100.00'), NOW)
        self.assertTrue(changed['current_day']['changed'])
        self.assertEqual('25100.00', json.loads((self.root / 'raw/2026/2026-11-21.json').read_text())['Data'][0]['cash'])

    def test_no_data_today_never_erases_existing_good_rates(self):
        self.cursor(TODAY.isoformat())
        sync(self.root, TODAY, lambda day: payload(day), NOW)
        result = sync(self.root, TODAY, lambda day: empty(), NOW)
        self.assertEqual('NO_DATA_PENDING', result['current_day']['kind'])
        self.assertTrue((self.root / 'raw/2026/2026-11-21.json').exists())
        self.assertFalse((self.root / 'metadata/no-data/2026-11-21.json').exists())

    def test_historical_cursor_moves_once_and_no_data_is_recorded(self):
        self.cursor('2026-11-20')
        result = sync(self.root, TODAY, lambda day: empty() if day.isoformat() == '2026-11-20' else payload(day), NOW)
        self.assertEqual('NO_DATA', result['backfill']['kind'])
        marker = self.root / 'metadata/no-data/2026-11-20.json'
        self.assertTrue(marker.exists())
        self.assertEqual('2026-11-21', json.loads(self.cursor_path.read_text())['next_date'])
        self.assertIsNone(sync(self.root, TODAY, lambda day: payload(day), NOW)['backfill'])

    def test_backfill_rates_are_retained_and_cursor_advances(self):
        self.cursor('2026-11-20')
        result = sync(self.root, TODAY, lambda day: payload(day), NOW)
        self.assertEqual('RATES', result['backfill']['kind'])
        self.assertTrue((self.root / 'raw/2026/2026-11-20.json').exists())
        self.assertEqual('2026-11-20', json.loads(self.cursor_path.read_text())['last_success_date'])

    def test_invalid_today_does_not_modify_cursor_or_history(self):
        self.cursor('2026-11-20')
        before = self.cursor_path.read_text()
        wrong = payload(TODAY)
        wrong['Count'] = 2
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            sync(self.root, TODAY, lambda day: wrong, NOW)
        self.assertEqual(before, self.cursor_path.read_text())
        self.assertFalse((self.root / 'raw').exists())

    def test_invalid_backfill_cannot_advance_cursor(self):
        self.cursor('2026-11-20')
        bad = payload(dt.date(2026, 11, 20))
        bad['Date'] = '2026-11-19T00:00:00'
        with self.assertRaisesRegex(ValueError, 'date mismatch'):
            sync(self.root, TODAY, lambda day: bad if day.isoformat() == '2026-11-20' else payload(day), NOW)
        self.assertEqual('2026-11-20', json.loads(self.cursor_path.read_text())['next_date'])

    def test_missing_quote_and_wrong_day_are_rejected(self):
        data = payload(TODAY)
        data['Data'][0]['cash'] = None
        with self.assertRaises(ValueError):
            validate(data, TODAY)
        data = payload(TODAY)
        data['Date'] = '2026-11-20T00:00:00'
        with self.assertRaises(ValueError):
            validate(data, TODAY)

    def test_zero_is_valid_preserved_rate(self):
        data = payload(TODAY)
        data['Data'][0]['cash'] = '0.00'
        self.assertEqual('RATES', validate(data, TODAY))
        result = stage_day(self.root, TODAY, data, historical=False, now=NOW)
        self.assertTrue(result['changed'])
        self.assertEqual('0.00', json.loads((self.root / 'raw/2026/2026-11-21.json').read_text())['Data'][0]['cash'])

    def test_historical_no_data_cannot_erase_rates(self):
        self.cursor('2026-11-20')
        file = self.root / 'raw/2026/2026-11-20.json'
        file.parent.mkdir(parents=True)
        file.write_text(json.dumps(payload(dt.date(2026,11,20))))
        with self.assertRaisesRegex(ValueError, 'existing valid'):
            sync(self.root, TODAY, lambda day: empty() if day.isoformat() == '2026-11-20' else payload(day), NOW)
        self.assertEqual('2026-11-20', json.loads(self.cursor_path.read_text())['next_date'])


if __name__ == '__main__':
    unittest.main()
