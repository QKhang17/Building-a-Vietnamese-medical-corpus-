# Giao thức benchmark MedNLP Studio 300 tóm tắt

Tài liệu này là hợp đồng thực nghiệm. Kết quả chỉ được đưa vào bài báo khi toàn bộ kiểm tra hoàn thành và có thể truy ngược về raw trace.

## Thiết kế khóa trước

- Population: các bản ghi trong `articles` có abstract sau chuẩn hóa dài tối thiểu 80 ký tự.
- Loại trùng: SHA-256 của văn bản sau NFKC, chuẩn hóa khoảng trắng và `casefold`.
- Phân tầng: domain nguồn × năm xuất bản × tam phân vị độ dài.
- Seed: `20260830`.
- Split: pilot 30, development 70, test 200. Danh sách ID test và checksum được khóa trong manifest trước khi sửa prompt.
- Hai annotator và một adjudicator; guideline `v1.0`, schema `mednlp-6types-v1`.

## Chống rò dữ liệu

1. Test không được dùng để sửa prompt, từ điển, luật hợp nhất hoặc guideline.
2. Test luôn blind; API chỉ trả preannotation khi `annotation_mode=assisted`.
3. Hai bản annotator chỉ được lộ cho assignment adjudicator sau trạng thái `conflict`.
4. Raw model output được cache theo cấu hình/repeat/article; phân tích lại không gọi API.
5. Mọi tệp kết quả phải giữ `article_id`, `run_uuid`, prompt hash, dictionary hash và repeat index.

## Cấu hình ablation

| Mã | Cấu hình | Hậu kiểm / hợp nhất |
|---|---|---|
| D | Dictionary | Chỉ matcher từ điển |
| R | AI raw | Không từ điển; ứng viên không thể map hợp lệ được tính false positive |
| C | AI constrained | Schema/type/offset/surface, loại trùng và giải quyết overlap |
| H | Hybrid | C + dictionary, metadata từ điển có quyền ưu tiên |

Dictionary chạy một lần. R/C/H chạy ba lần với cùng model, prompt, temperature và bảng giá ghi theo ngày chạy. Mỗi cấu hình dùng 10 development abstracts để warm-up trước khi đo 200 test abstracts.

## Chỉ số chính và phụ

- Chính: exact-span micro/macro P/R/F1 và P/R/F1 theo sáu loại.
- CI: bootstrap theo tài liệu 1.000 lần, seed `20260830`.
- So sánh ghép cặp: R→C, C→H, D→H cho từng repeat.
- Phụ: relaxed overlap, độ chính xác mã trên gold có mã, ma trận 6×6, lỗi boundary/type/missing/spurious/wrong-code/invalid-AI.
- Từ điển: exact recall, unique-surface coverage và valid-code rate theo loại.
- Hiệu năng: mean/median/p95 cho dictionary, AI, hậu kiểm, merge, storage và total; token và USD/document, USD/1.000 documents.
- Con người: active time, accepted/modified/rejected/added và chất lượng so gold theo blind/assisted.

## Điều kiện hoàn thành máy kiểm tra được

- Manifest có đúng 30/70/200 ID và không trùng checksum.
- Có 300 snapshot vàng, checksum snapshot hợp lệ.
- Có đủ 10 run files: 1 D, 3 R, 3 C, 3 H; mỗi file chứa đúng 200 ID test.
- Không thiếu token, latency, outcome, prompt/dictionary hash hoặc metadata giá ở run AI.
- Các CSV trong bảng bài báo được sinh bởi `tools.summarize_experiment`, không sửa tay.
- Chỉ phát hành abstract nguyên văn khi quyền sử dụng cho phép; mặc định export URL/ID/checksum/offset/entity.
