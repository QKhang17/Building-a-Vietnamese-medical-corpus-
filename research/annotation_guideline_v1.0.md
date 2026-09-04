# Hướng dẫn gán nhãn MedNLP-6Types v1.0

Ngày khóa bản: 2026-08-30. Đơn vị gán nhãn là **tóm tắt nguyên văn**; offset dùng khoảng nửa mở `[start, end)` trên chuỗi Unicode mà API cung cấp. Mọi thay đổi quy tắc sau pilot phải tạo phiên bản guideline mới và không được âm thầm áp dụng hồi tố.

## 1. Sáu loại thực thể

| Loại | Bao gồm | Không bao gồm / cách phân biệt |
|---|---|---|
| Bệnh lý | Bệnh, hội chứng, chẩn đoán, biến chứng bệnh học | Biểu hiện đơn lẻ chưa được tác giả gọi là bệnh → Triệu chứng |
| Triệu chứng | Triệu chứng, dấu hiệu lâm sàng, than phiền của người bệnh | Chỉ số đo hoặc kết quả xét nghiệm → Xét nghiệm; trạng thái bình thường → Sinh lý |
| Điều trị | Thuốc, hoạt chất, thủ thuật, phẫu thuật, liệu pháp, can thiệp | Xét nghiệm/chẩn đoán hình ảnh chỉ dùng để đánh giá → loại tương ứng |
| Xét nghiệm | Tên xét nghiệm, phép đo cận lâm sàng, biomarker khi được dùng như xét nghiệm | Kỹ thuật tạo ảnh → Hình ảnh; giá trị sinh lý không gắn phép xét nghiệm → Sinh lý |
| Hình ảnh | Phương thức, kỹ thuật và thăm dò hình ảnh y khoa | Kết luận bệnh rút ra từ ảnh được gán Bệnh lý nếu xuất hiện như chẩn đoán |
| Sinh lý | Cấu trúc/chức năng/quá trình sinh lý và chỉ số sinh học bình thường có ý nghĩa nghiên cứu | Triệu chứng bất thường hoặc chẩn đoán bệnh lý |

## 2. Quy tắc ranh giới span

1. Chọn **cụm liên tục nhỏ nhất nhưng đủ nghĩa chuyên môn**; giữ thành tố định danh cần thiết, bỏ dấu câu và từ dẫn không thuộc thuật ngữ.
2. Mỗi lần xuất hiện được gán riêng. Không sao chép offset từ một lần xuất hiện sang lần khác.
3. Hai thực thể phối hợp bằng dấu phẩy/“và” được tách nếu từng thành phần vẫn là thuật ngữ độc lập.
4. Viết đầy đủ và chữ viết tắt được gán thành hai span nếu không chồng lấn và cả hai có nghĩa độc lập trong văn bản.
5. Phiên bản v1.0 không cho phép span gián đoạn hoặc hai nhãn hoạt động chồng lấn. Khi có khả năng chồng lấn, chọn span biểu đạt khái niệm chính theo ngữ cảnh và ghi lý do để pilot xem xét.
6. Vẫn gán thực thể bị phủ định hoặc được nêu trong tiền sử; schema hiện chưa mã hóa thuộc tính khẳng định/phủ định.
7. Không sửa chính tả văn bản nguồn. `surface` phải bằng chính xác `X[start:end]`, kể cả khác biệt Unicode tổ hợp.

## 3. Loại và mã thuật ngữ

- Chọn loại theo vai trò trong câu, không chỉ theo hình thức từ.
- Chỉ điền `code` khi mã đã có trong từ điển phiên bản khóa hoặc được chuyên gia kiểm chứng. Không suy đoán mã.
- Nếu từ điển đề xuất đúng span nhưng sai loại/mã, dùng thao tác **sửa** và ghi lý do. Nếu không có mã đáng tin cậy, để trống.
- Một span chỉ có một loại và một mã trong bản vàng v1.0.

## 4. Quy trình độc lập và phân xử

- Pilot 30 mẫu dùng để ghi nhận trường hợp khó và chốt v1.0; thay đổi định nghĩa phải được ghi trong changelog.
- Mỗi annotator gán độc lập. Ở chế độ blind, giao diện không trả pre-label và không trả bản của người còn lại trước khi nộp.
- Trên 70 development, thiết kế đối trọng: A assisted/B blind trên 35 mẫu; A blind/B assisted trên 35 mẫu còn lại. Không dùng development để báo cáo kết quả test cuối cùng.
- Test 200 mẫu luôn blind. Sau khi cả hai nộp, bản giống hệt có thể khóa tự động với `lock_source=auto_identical`; mọi bất đồng được giao cho adjudicator.
- Adjudicator xem song song hai bản, quyết định từng span/type/code và tạo đúng một snapshot vàng. Snapshot đã khóa là bất biến; sửa sai phải tạo project/version mới.

## 5. Quy ước thao tác

| Quyết định | Dùng khi |
|---|---|
| accepted | Giữ nguyên pre-label |
| modified | Sửa span, loại hoặc mã của pre-label |
| rejected | Loại pre-label khỏi bản nộp nhưng giữ dấu vết audit |
| added | Thêm thực thể bị máy bỏ sót hoặc gán từ đầu |
| proposed | Trạng thái pre-label chưa được chuyên gia quyết định |

Annotator phải chọn lý do ngắn cho `modified` và `rejected`. Thời gian chỉ cộng khi có hoạt động; rời tab lâu không được tính là thời gian gán nhãn.

## 6. Kiểm tra trước khi nộp

- Không còn nhãn `proposed` chưa xử lý ở assisted review.
- Không có span hoạt động chồng lấn, offset ngoài chuỗi hoặc surface sai nguyên văn.
- Các mã đã điền có thể truy ngược về manifest từ điển.
- Đã rà toàn bộ tóm tắt, không chỉ khu vực có pre-label.

## Changelog

- v1.0 (2026-08-30): định nghĩa ban đầu cho pilot; sáu loại, span liên tục, không chồng lấn, mã có thể để trống.
