# vcb-exchange-rates

Kho JSON tỷ giá niêm yết Vietcombank theo ngày, nguồn dữ liệu của `yesterdaymoon201/vcb-fx-dashboard`.

## GitHub Actions: thu thập tự động

Workflow: `.github/workflows/collect-vcb.yml`, tên **Thu thập tỷ giá Vietcombank**.
Lịch hằng ngày theo giờ Việt Nam (UTC+7): **09:05, 10:05, 23:05**; tương ứng GitHub UTC **02:05, 03:05, 16:05** với cron `5 2,3,16 * * *`. Có thể bị trễ do GitHub xếp hàng. Có nút chạy thủ công `workflow_dispatch`.

Mỗi lượt gọi API Vietcombank cho **ngày hiện tại tại Việt Nam**, kiểm tra `Count`, `Date`, danh sách tiền tệ và giá hợp lệ. Bản nguồn cuối cùng của ngày được lưu tại `raw/YYYY/YYYY-MM-DD.json`. Nếu tỷ giá biến động giữa ba lượt thì cùng tệp ngày được cập nhật, **không tạo ba bản lịch sử riêng**. Phản hồi rỗng của ngày hiện tại không làm mất dữ liệu hợp lệ đã lưu.

Chương trình cũng đọc `metadata/cursor.json`. Nếu ngày `next_date` đã ở quá khứ, mỗi lần chạy sẽ xử lý **một ngày lịch sử** giống như workflow n8n v5.1: ghi JSON hợp lệ vào `raw/` hoặc xác nhận `Count=0` vào `metadata/no-data/`, rồi mới cập nhật con trỏ. Lỗi HTTP, dữ liệu sai cấu trúc hoặc sai ngày sẽ làm job thất bại mà không tạo commit dữ liệu.

GitHub Actions dùng `GITHUB_TOKEN` với `contents: write` để ghi vào chính repository này; **không cần token n8n hoặc credential cá nhân**. Chỉ commit khi dữ liệu thực sự thay đổi. Không sửa dữ liệu gốc để tính toán; giá 0 được giữ nguyên và các mệnh giá USD riêng biệt không được tự xóa.

## Kích hoạt cập nhật dashboard ngay sau khi dữ liệu nguồn thay đổi

Workflow thu thập đã hỗ trợ gửi sự kiện `repository_dispatch` loại `vcb_source_updated` đến repository private `yesterdaymoon201/vcb-fx-dashboard`. Để kích hoạt, tạo **fine-grained personal access token** chỉ cấp quyền **Contents: Read and write** trên **repository dashboard** (quyền tối thiểu mà endpoint repository dispatch yêu cầu). Tạo Actions repository secret tên `DASHBOARD_DISPATCH_TOKEN` trong **vcb-exchange-rates**; không lưu token trong mã nguồn, nhật ký hay biến công khai. Khi có bản ghi tỷ giá thực sự thay đổi, workflow gửi sự kiện sau khi push dữ liệu thành công; dashboard sẽ chạy đồng bộ ngay khi GitHub tiếp nhận và xếp hàng sự kiện. Bước gửi sự kiện không chạy nếu nguồn chưa có thay đổi.

**Không có secret vẫn hoạt động:** dashboard tự kiểm tra bổ sung sau ba lượt thu thập vào 09:09/09:14/09:19, 10:09/10:14/10:19 và 23:09/23:14/23:19 giờ Việt Nam, đồng thời giữ lượt đối soát hai giờ một lần. Đây là đồng bộ dự phòng, **không phải bảo đảm cập nhật tức thời**; GitHub Actions có thể chạy trễ so với cron. Việc tạo secret chỉ cần thiết khi muốn kích hoạt theo sự kiện ngay sau commit thay vì chờ lịch.

## Kiểm soát và vận hành

Chạy kiểm thử: `python3 -m unittest discover -s tests -v`. Chạy lấy dữ liệu thật (tại máy có Python >=3.9): `python3 scripts/sync_vcb.py --root .`. Quan sát kết quả tại **GitHub → Actions → Thu thập tỷ giá Vietcombank** và sử dụng **Run workflow** khi cần.

Sau khi xác nhận GitHub Actions chạy và ghi dữ liệu đúng, **tắt lịch n8nlocal cũ** để tránh hai tiến trình cùng ghi `metadata/cursor.json`.

Repository dashboard `vcb-fx-dashboard` đã có workflow đồng bộ `.github/workflows/sync-vcb.yml`, tự kéo từ nguồn này mỗi 2 giờ và xuất bản các file tháng cho Cloudflare Pages. Dashboard vì vậy có thể hiển thị dữ liệu muộn hơn thời điểm lấy; có thể bấm Run workflow bên dashboard sau khi nguồn commit để đồng bộ sớm hơn.

Nếu Actions báo Failed, kiểm tra log API Vietcombank (HTTP/TLS/DNS/timeout), trạng thái nguồn và quyền `GITHUB_TOKEN`. Không xem một job thành công là cam kết tỷ giá đúng tại thời điểm giao dịch. GitHub có thể tự vô hiệu hóa lịch sau thời gian dài repository công khai không hoạt động.
