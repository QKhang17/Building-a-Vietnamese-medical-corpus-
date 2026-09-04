# Gói nghiên cứu tái lập

Chạy các lệnh từ `backend` bằng môi trường `..\.venv\Scripts\python.exe`.

1. Tạo schema: `python -m tools.apply_research_schema`.
2. Tạo mẫu/assignment: `python -m tools.sample_annotation_project --name mednlp-gold-v1 --output ../output/gold-v1-manifest.json`.
3. Tạo đủ preannotation development bằng `python -m tools.prepare_assisted_preannotations` với model và bảng giá đã khóa. Assignment assisted thiếu snapshot sẽ bị API từ chối mở.
4. Gán nhãn tại `/annotation.html`; chỉ chạy benchmark sau khi đủ 300 snapshot vàng.
5. Xuất gold (mặc định không kèm nguyên văn): `python -m tools.export_research_package --project-id <ID> --output-dir ../output/research-package`.
6. Chạy ablation với các tham số giá/ngày/URL nguồn chính thức bắt buộc; thêm `--persist-db` để ghi trace vào MySQL. Với Gemini 2.5 Flash standard paid tại ngày 2026-08-30, pricing lock trong `pricing_lock_2026-08-30.json` ghi 0,30 USD/1M token vào và 2,50 USD/1M token ra; không dùng các mức này cho ngày chạy khác mà không kiểm tra lại nguồn.
7. Sinh báo cáo và CSV bằng `python -m tools.summarize_experiment` với cùng tham số giá.

Không dùng `--allow-incomplete` cho số liệu báo cáo chính thức. Cờ này chỉ dành cho kiểm thử pipeline trước khi nghiên cứu con người hoàn tất.
