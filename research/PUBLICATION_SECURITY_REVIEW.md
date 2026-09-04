# Rà soát trước khi công khai — 2026-09-04

## Phát hiện và xử lý

- Mật khẩu DB mặc định trong main.py và bốn công cụ nghiên cứu: bỏ giá trị nhúng, đọc DB_PASSWORD từ môi trường. Giữ cấu hình cục bộ trong .env bị Git bỏ qua.
- Khóa ký token cố định trong expert_service.py: bỏ mặc định, yêu cầu EXPERT_TOKEN_SECRET ít nhất 32 ký tự. File .env cục bộ được bổ sung khóa ngẫu nhiên nếu thiếu.
- mysql.txt chứa tài khoản và password hash mẫu; test xác thực chứa mật khẩu khớp tài khoản mẫu: bỏ mysql.txt khỏi Git, thay test bằng tài khoản chỉ dành cho kiểm thử.
- 5.158 file được bỏ khỏi Git index, giữ nguyên trên đĩa: gồm các nhóm PDF, ngữ liệu, từ điển/dữ liệu nguồn và mysql.txt. Không khẳng định từng PDF có thông tin riêng tư; toàn bộ nguồn dữ liệu được giữ riêng do chưa rà soát nội dung và quyền công khai.
- Bổ sung ignore cho cấu hình bí mật, khóa riêng, database/dump/log, tài liệu và thư mục ngữ liệu, kể cả tài liệu không có đuôi file chuẩn.
- Quét mã còn theo dõi và file mới: không phát hiện mẫu khóa API phổ biến/khóa riêng/URL nhúng thông tin đăng nhập hoặc giá trị mật khẩu DB và khóa ký cũ. Đây không phải bảo đảm không còn mọi loại thông tin nhạy cảm.

## Trạng thái Git

Chưa commit hoặc push. Các thao tác bỏ file khỏi index đã được stage; sửa mã và ignore chưa được commit. core.hooksPath trỏ tới .githooks, pre-push luôn từ chối upload tại repository cục bộ này.

Lịch sử HEAD cũ vẫn chứa các file và bí mật. Commit xóa file không loại chúng khỏi lịch sử. Không bật lại push cho tới khi chuẩn bị và rà soát lịch sử phát hành sạch. Hook chỉ bảo vệ repository này, không phải chính sách trên GitHub và không đi theo một clone mới nếu chưa cấu hình.

Chưa kiểm tra nội dung repository trên GitHub. Nếu mật khẩu/khóa/tài khoản cũ đã từng công khai, phải thay mật khẩu DB, khóa ký và tài khoản chuyên gia; xóa file không thu hồi được bản đã tải. Mã nguồn công khai vẫn có thể bị sao chép; muốn giữ độc quyền truy cập cần giữ repository private.

## Kiểm chứng

15 kiểm thử xác thực và annotation service đạt. Không còn file bị ignore nhưng vẫn được Git index theo dõi. Các file riêng tư mẫu vẫn tồn tại trên máy. Bản mã nguồn lọc cần dữ liệu và schema riêng để vận hành.
