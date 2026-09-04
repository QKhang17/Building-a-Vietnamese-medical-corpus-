# Trạng thái triển khai benchmark 300 tóm tắt

Ngày chốt trạng thái: 2026-08-30  
Project cơ sở dữ liệu: `mednlp-gold-v1` (`project_id = 1`)  
Seed lấy mẫu: `20260830`

## 1. Phần đã triển khai và kiểm thử

- Đã lấy mẫu tái lập 300 tóm tắt sau chuẩn hóa và khử trùng, gồm 30 pilot, 70 development và 200 test. Manifest công bố ID, metadata phân tầng và checksum, không công bố nguyên văn mặc định.
- Đã tạo 900 assignment: hai lượt gán độc lập cho mỗi tài liệu và một lượt phân xử. Tập development dùng thiết kế đối trọng 35/35 cho mỗi annotator giữa gán trắng và rà tiền nhãn.
- Đã triển khai workflow `assigned → in_progress → submitted → conflict → adjudicated → locked`, kiểm soát vai trò, optimistic locking, audit event và snapshot vàng bất biến.
- Đã triển khai giao diện gán nhãn mù, rà nhãn hỗ trợ và phân xử: sửa span/type/code, thêm/xóa, accept/reject/modified, lý do, hoàn tác, đo thời gian hoạt động và xem song song hai bản khi phân xử.
- Đã triển khai bốn cấu hình ablation: dictionary, AI raw, AI constrained và hybrid; ba lần lặp cho cấu hình AI; cache raw response; log model, prompt hash, dictionary hash, token, latency, chi phí và nguyên nhân loại từng ứng viên.
- Đã triển khai exact-span micro/macro/per-type P/R/F1, relaxed overlap, độ chính xác mã, ma trận nhầm lẫn sáu loại, phân loại lỗi, bootstrap 1.000 lần, chênh lệch ghép cặp, độ phủ từ điển, latency/chi phí và nghiên cứu thời gian chuyên gia.
- Đã triển khai xuất gói dữ liệu an toàn giấy phép. Chế độ chính thức từ chối xuất nếu chưa đủ 300 snapshot vàng; `--allow-incomplete` chỉ dùng kiểm thử.
- Đã viết guideline v1.0, protocol benchmark, audit tài liệu liên quan, pricing lock, hai hình vector tiếng Việt và bản thảo Word mới. Bản thảo hiện ghi rõ là bản chờ dữ liệu, không điền P/R/F1 giả.
- Kiểm thử tự động: 75/75 test backend đạt; Python compileall đạt; frontend build và lint đạt. Lint chỉ còn cảnh báo ở mã giao diện cũ.

## 2. Dữ liệu thực nghiệm hiện có

| Hạng mục | Hiện có | Điều kiện hoàn tất |
|---|---:|---:|
| Tài liệu đã lấy mẫu | 300/300 | Đạt |
| Pilot / development / test | 30 / 70 / 200 | Đạt |
| Assignment | 900/900 | Đạt |
| Assisted assignment có snapshot tiền nhãn | 1/70 | 70/70 |
| Snapshot vàng đã khóa | 0/300 | 300/300 |
| Cấu hình ablation có kết quả trên test khóa | 0/4 | 4/4 |
| Lần chạy AI benchmark | 0/1.800 | 1.800 |
| Warm-up development | 0/90 | 90 |

Không được dùng preview hiện tại để công bố agreement, chất lượng NER hay hiệu quả hỗ trợ con người: các đại lượng này chưa có mẫu số hợp lệ vì chưa diễn ra gán nhãn và phân xử thực tế.

## 3. Phần còn phụ thuộc nguồn lực bên ngoài

1. Ba chuyên gia phải hoàn thành 600 bản gán độc lập và 300 lượt phân xử theo guideline đã khóa.
2. Cần tạo 69 snapshot tiền nhãn development còn thiếu bằng Gemini trước khi mở các assignment assisted tương ứng.
3. Sau khi đủ 300 bản vàng, cần khóa project/test set rồi chạy 90 lượt warm-up trên development và 1.800 lượt AI trên 200 test (ba cấu hình AI × ba lần lặp × 200 tài liệu). Cấu hình dictionary không phát sinh gọi API.
4. Giá và model phải được kiểm tra lại đúng ngày chạy. Pricing lock hiện tại chỉ áp dụng cho Gemini 2.5 Flash standard paid tại ngày 2026-08-30.
5. Chỉ sau các bước trên mới sinh các bảng kết quả chính thức và thay phần trạng thái trong bản thảo Word.

## 4. Trình tự vận hành còn lại

Chạy từ thư mục `backend` bằng `..\.venv\Scripts\python.exe`:

```powershell
# 1. Tạo đủ tiền nhãn development (có chi phí API)
..\.venv\Scripts\python.exe -m tools.prepare_assisted_preannotations `
  --project-id 1 `
  --cache-dir ..\output\cache\assisted-preannotations `
  --model gemini-2.5-flash `
  --input-price-usd-per-million 0.30 `
  --output-price-usd-per-million 2.50 `
  --pricing-as-of 2026-08-30 `
  --pricing-source https://ai.google.dev/gemini-api/docs/pricing

# 2. Gán nhãn và phân xử tại frontend/public/annotation.html

# 3. Xuất corpus chính thức; lệnh sẽ tự từ chối nếu chưa đủ 300 gold
..\.venv\Scripts\python.exe -m tools.export_research_package --project-id 1 --output-dir ..\output\research-package

# 4. Chạy benchmark sau khi test đã khóa; truyền bảng giá và URL nguồn đúng ngày chạy
..\.venv\Scripts\python.exe -m tools.run_ablation `
  --manifest ..\output\gold-v1-manifest.json `
  --output-dir ..\output\ablation-runs `
  --cache-dir ..\output\cache\ablation `
  --project-id 1 --persist-db `
  --model gemini-2.5-flash --repeats 3 --warmup 10 `
  --input-price-usd-per-million 0.30 `
  --output-price-usd-per-million 2.50 `
  --pricing-as-of 2026-08-30 `
  --pricing-source https://ai.google.dev/gemini-api/docs/pricing

# 5. Tổng hợp JSON/CSV và cập nhật bản thảo
..\.venv\Scripts\python.exe -m tools.summarize_experiment `
  --gold ..\output\research-package\gold.jsonl `
  --results-dir ..\output\ablation-runs `
  --output-dir ..\output\benchmark-report `
  --bootstrap 1000 --seed 20260830 `
  --input-price-usd-per-million 0.30 `
  --output-price-usd-per-million 2.50 `
  --pricing-as-of 2026-08-30 `
  --pricing-source https://ai.google.dev/gemini-api/docs/pricing
```

## 5. Tiêu chí không được nới lỏng

- Không dùng test để sửa prompt, từ điển hay quy tắc.
- Không cho annotator xem nhãn của nhau trước khi nộp.
- Không mở assisted assignment nếu chưa có snapshot tiền nhãn cố định.
- Không đổi model, prompt, nhiệt độ hoặc bảng giá giữa các cấu hình mà không tạo một protocol/run mới.
- Không báo cáo điểm số, khoảng tin cậy hoặc chi phí suy đoán; mọi ô định lượng trong bài phải truy ngược được tới gold snapshot, raw cache và run ID.
