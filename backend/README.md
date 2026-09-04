# MedNLP Studio Backend

Backend FastAPI cho thu thập dữ liệu, gán nhãn NER y khoa, tách PDF và duyệt nhãn chuyên gia.

## Chạy backend

Từ thư mục `backend`:

```powershell
..\.venv\Scripts\python.exe main.py
```

API chạy tại `http://127.0.0.1:8000`; tài liệu OpenAPI tại `/docs`.

Khi khởi động, terminal hiển thị số thuật ngữ lấy từ file Excel ICD10VN và PDF Phụ lục 1.

## Crawler hỏi đáp Tâm Anh

- URL mặc định: `https://tamanhhospital.vn/`, crawler tự chuyển đến `/tu-van/`.
- Tự phát hiện toàn bộ chuyên khoa, quét các trang và chỉ lưu bài có `dateCreated` thuộc khoảng năm đã chọn.
- Câu hỏi bệnh nhân: `backend/Tamanh/BN`.
- Câu trả lời bác sĩ: `backend/Tamanh/BS`.
- Nút **Dừng thu thập** dừng an toàn sau URL đang xử lý.

## Crawler tạp chí và PDF

- Chọn **Tạp chí & PDF** trong bộ chuyển đổi ở màn hình Thu thập dữ liệu.
- Hỗ trợ các website tạp chí OJS, tự duyệt số/tập theo năm.
- Đọc metadata của từng bài để xác định ngôn ngữ, tên tạp chí, năm xuất bản,
  tập và số tạp chí. Metadata khai báo trên trang được ưu tiên; khi thiếu, hệ
  thống dùng nội dung và thông tin của trang số tạp chí để dự phòng.
- PDF được lưu tại
  `backend/Văn_Bản_Y_Tế_PDF/<Vietnamese|English>/<Tạp chí>/<Năm>/<Tập_Số>/`.
- Tên PDF có dạng `<mã bài>_<tên bài>.pdf` để tránh ghi đè hai bài trùng tên.
- File TXT dùng cùng cấu trúc thư mục để có thể đối chiếu trực tiếp với PDF.

## Tách cấu trúc PDF bằng Gemini

- Cấu hình `GEMINI_API_KEY` trong `backend/.env`; có thể đổi model bằng
  `GEMINI_PDF_MODEL`, mặc định là `gemini-2.5-flash`.
- Gemini nhận toàn bộ file PDF trong một request, lưu riêng Tóm tắt và chỉ giữ
  các mục chính thực sự được đánh số La Mã từ `I.` đến `X.`. Mục con, bảng,
  hình, tài liệu tham khảo và phần số liệu chi tiết bị loại theo prompt.
- Đầu ra được ràng buộc bằng JSON Schema, kiểm tra lại ở backend, rồi lưu mỗi
  mục thành `Tên bài_Tên phần.txt` trong `backend/Văn_Bản_Y_Tế_TXT`.
- PDF được tải tạm lên Gemini Files API và được xóa khỏi Files API sau khi xử
  lý xong hoặc khi có lỗi.
- Thời gian chờ Gemini mặc định tối đa 5 phút. Có thể điều chỉnh bằng biến môi
  trường `GEMINI_PDF_TIMEOUT_MS` (đơn vị mili giây).

## Database MySQL Workbench

Mở [`../mysql.txt`](../mysql.txt) trong MySQL Workbench và chạy toàn bộ script. Script tạo database `yhoc_corpus`, bảy bảng cần thiết và ba tài khoản chuyên gia mặc định.

Backend và cổng chuyên gia dùng chung cấu hình MySQL trong `main.py`. Bắt buộc cấu hình `DB_PASSWORD` và `EXPERT_TOKEN_SECRET` trong `.env`; khóa ký phải là giá trị ngẫu nhiên riêng, dài ít nhất 32 ký tự. Không có mật khẩu DB hoặc khóa ký mặc định trong mã nguồn.

## Cài thư viện

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Benchmark nhãn vàng 300 tóm tắt

Schema nghiên cứu, workflow hai annotator + một adjudicator, ablation bốn cấu hình và bộ xuất bảng được mô tả tại [`../research/README.md`](../research/README.md). Giao diện gán nhãn độc lập nằm ở `http://localhost:5173/annotation.html` và dùng cùng token đăng nhập chuyên gia với cổng cũ.

Các script xuất corpus và bảng mặc định từ chối chạy báo cáo chính thức nếu chưa đủ 300 snapshot vàng hoặc chưa đủ 1+3+3+3 run. Không có số liệu giả hay placeholder được tự động điền vào bài báo.


## Trạng thái chuẩn bị công khai

Dữ liệu PDF/ngữ liệu, từ điển nguồn và `mysql.txt` chứa tài khoản mẫu được giữ riêng trên máy và không nằm trong bản mã nguồn công khai. Tự cung cấp dữ liệu và schema/tài khoản đã được rà soát để chạy hệ thống; bản mã nguồn được lọc không phải gói chạy đầy đủ kèm dữ liệu.

Repository hiện bị chặn push bằng `.githooks/pre-push` vì lịch sử cũ vẫn chứa dữ liệu và thông tin đăng nhập. Xem `../research/PUBLICATION_SECURITY_REVIEW.md`.
