# Rà soát nguồn dữ liệu và nền tảng liên quan

Ngày chốt: **2026-08-30**. Tài liệu này ghi lại phạm vi tìm kiếm dùng cho phần tính cấp thiết và bảng đối sánh của bài báo; đây không phải một systematic review đầy đủ.

## 1. Corpus NER y–sinh tiếng Việt công khai

Chiến lược: tìm kết hợp `Vietnamese`, `medical|biomedical|clinical`, `NER|named entity`, `dataset|corpus` trên ACL Anthology, GitHub, Hugging Face và trang nhà xuất bản; lần theo repository từ bài báo. Đưa vào khi (i) có văn bản tiếng Việt, (ii) có span/token NER thuộc miền y–sinh, và (iii) có tệp dữ liệu công khai hoặc quy trình tải công khai tại ngày chốt. Loại tập chỉ làm QA/NLI/classification, corpus không có nhãn thực thể, tài nguyên chỉ có mô hình, hoặc nghiên cứu không công bố dữ liệu.

Kết quả rà soát xác nhận **sáu corpus công khai phù hợp tiêu chí**:

| Corpus | Miền/nguồn | Quy mô công bố | Nhãn/đặc điểm | Khác MedNLP-300 |
|---|---|---:|---|---|
| [PhoNER_COVID19](https://github.com/VinAIResearch/PhoNER_COVID19) | Tin COVID-19 | 10K câu, 35K entity | 10 loại; dữ liệu thủ công; giấy phép nghiên cứu, cấm tái phân phối | Dịch tễ COVID, không phải tóm tắt bài báo đa chuyên khoa và không gắn mã thuật ngữ |
| [VietBioNER](https://github.com/ptpuyen1511/VietBioNER) | Tài liệu y–sinh về lao | 1.706 câu | 5 loại; có brat và guideline; CC BY 4.0 ở repository | Miền lao hẹp; taxonomy gồm tổ chức/địa điểm/thời gian |
| [ViMQ](https://github.com/tadeephuy/ViMQ) | Câu hỏi bệnh nhân | 9.000 câu hỏi | 3 loại NER + intent | Ngôn ngữ hội thoại hỏi bệnh, không phải văn phong học thuật |
| [ViMedNER](https://github.com/tdtrinh11/ViMedNer) | Nội dung chẩn đoán/điều trị trên bốn website | Xem thống kê trong bài/repository | Bệnh, triệu chứng, nguyên nhân, chẩn đoán, điều trị | Không công bố workflow truy vết LLM có ràng buộc như đối tượng nghiên cứu |
| [VietMed-NER](https://github.com/leduckhai/MultiMed/tree/master/VietMed-NER) | Lời nói y khoa thực tế | 18 loại | Spoken NER; dữ liệu, mã và model công khai | Đơn vị là lời nói/transcript, chịu lỗi ASR |
| [En‑ViMedNER](https://huggingface.co/datasets/nhuvo/En-ViMedNER) | PubMed dịch song song Anh–Việt | 4.392 cặp abstract; 202.949 cặp mention | 21 UMLS semantic types; dịch, chiếu nhãn có LLM, hậu biên tập và phân xử | Corpus song song được dẫn xuất từ MedMentions; mục tiêu cross-lingual, không phải bài Việt bản địa |

Kết luận nên viết có giới hạn: “Theo chiến lược tìm kiếm và tiêu chí trên, chúng tôi xác nhận sáu corpus NER y–sinh có thành phần tiếng Việt công khai tại ngày 30/08/2026; chúng khác nhau mạnh về miền, nguồn và taxonomy.” Không dùng câu tuyệt đối “chưa có dữ liệu” hoặc “đây là corpus đầu tiên”.

## 2. So sánh nền tảng gần nhất

Quy ước: **Có** = tài liệu chính thức mô tả chức năng sẵn có; **Tùy biến** = làm được qua recipe/API/cấu hình nhưng không phải luồng chuyên biệt mặc định; **Không/không nêu** = không tìm thấy bằng chứng trong tài liệu đã rà, không phải khẳng định bất khả thi.

| Nền tảng | Việt y khoa sẵn có | Mã thuật ngữ | Pre-annotation | LLM ràng buộc span/schema | Provenance + audit thao tác | Gán độc lập + phân xử |
|---|---|---|---|---|---|---|
| MedNLP Studio (bản này) | Có | Có, code + dictionary manifest hash | Dictionary + Gemini | Có: type/offset/surface/overlap | Có: version, run/event, prompt/dictionary hash | Có: 2 annotator + adjudicator, khóa snapshot |
| [Prodigy](https://prodi.gy/docs/recipes) | Tùy biến | Tùy biến recipe | Có, model/pattern | Có LLM workflow; kiểm tra chuyên biệt cần recipe | Session/dataset; audit chi tiết tùy triển khai | Có qua session + `review` |
| [INCEpTION](https://inception-project.github.io/documentation/latest/user-guide) | Tùy biến | Có knowledge base/entity linking | Có recommender | Tùy biến recommender | Có quản lý project/document; mức audit phụ thuộc cấu hình | Có curation nhiều annotator |
| [doccano](https://doccano.github.io/doccano/tutorial/) | Tùy biến | Không nêu | Có qua API/import | Không nêu | Quản lý user/project; audit chuyên sâu không nêu | Có collaborative annotation; adjudication chuyên biệt không nêu |
| [brat](https://brat.nlplab.org/features.html) | Tùy biến | Tùy biến attribute | Tùy biến | Không nêu | Có log thời gian và từng edit tùy cấu hình | Cộng tác thời gian thực, không phải blind + adjudication mặc định |
| [Apache cTAKES](https://ctakes.apache.org/) | Không | Có ontology/UMLS trong pipeline | N/A – engine NLP | Không nêu | Provenance thực nghiệm do người dùng xây | Không – không phải annotation platform |
| [MedTagger](https://github.com/OHNLP/MedTagger) | Không | Có OMOP concept | N/A – dictionary/rule/ML engine | Không nêu | Provenance thực nghiệm do người dùng xây | Không – không phải annotation platform |

Định vị hợp lý: đóng góp không nằm ở việc “có giao diện tô span” hay “có human-in-the-loop” nói chung—Prodigy và INCEpTION đã làm tốt. Điểm kiểm chứng cần nhấn mạnh là gói tích hợp cho tóm tắt y khoa tiếng Việt: mã thuật ngữ + kiểm tra nguyên văn + trace từng ứng viên LLM + benchmark ablation khóa test + nghiên cứu đối trọng thời gian chuyên gia.
