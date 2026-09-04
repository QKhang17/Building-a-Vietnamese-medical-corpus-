"""Tách cấu trúc bài báo PDF bằng Gemini và đầu ra JSON có ràng buộc."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

GEMINI_PDF_MODEL = os.getenv("GEMINI_PDF_MODEL", "gemini-2.5-flash")
GEMINI_PDF_TIMEOUT_MS = int(os.getenv("GEMINI_PDF_TIMEOUT_MS", "300000"))

logger = logging.getLogger(__name__)

PDF_STRUCTURE_PROMPT = r"""Bạn là hệ thống chuyên phân tích cấu trúc bài báo khoa học tiếng Việt.

Đọc toàn bộ bài báo được cung cấp và chỉ trích xuất các mục chính được đánh số bằng chữ số La Mã từ I. đến X. Chỉ một heading thực sự bắt đầu bằng số La Mã mới được đưa vào sections. Phải giữ nguyên tên tiêu đề như bài báo; không tự chuyển một tiêu đề không có số La Mã thành mục La Mã.

Ví dụ mục hợp lệ:
I. ĐẶT VẤN ĐỀ
II. ĐỐI TƯỢNG VÀ PHƯƠNG PHÁP NGHIÊN CỨU
III. KẾT QUẢ NGHIÊN CỨU
IV. BÀN LUẬN
V. KẾT LUẬN

## Mục con

Không tạo section riêng cho các mục con dạng 1., 1.1., 1.2., 1.2.1., 2., 2.1., 2.2. Tuy nhiên phải giữ phần văn bản khoa học nằm dưới các mục con và gộp nó vào content của mục La Mã cha. Không giữ chính dòng tiêu đề mục con trong content.

Content của một mục La Mã bắt đầu ngay sau tiêu đề đó và kết thúc ngay trước tiêu đề La Mã tiếp theo.

## Bảng, hình, biểu đồ và sơ đồ

Loại bỏ hoàn toàn Bảng/Table, gồm tiêu đề bảng, tên cột, tên hàng, dữ liệu, chú thích và số liệu chỉ xuất hiện trong bảng.

Loại bỏ hoàn toàn Hình/Figure, Biểu đồ/Chart và Sơ đồ/Diagram, gồm caption, dữ liệu, chú thích và nội dung OCR lấy từ hình.

## Số liệu thống kê chi tiết

Trong phần nội dung, loại các câu hoặc đoạn có chức năng chủ yếu là liệt kê số liệu thống kê như tỷ lệ, trung bình, độ lệch chuẩn, p, OR hoặc CI. Ví dụ "65,72 ± 46,81 phút; p = 0,005" phải bỏ.

Không xóa một đoạn khoa học chỉ vì có một vài con số. Phải giữ câu diễn giải ý nghĩa kết quả, ví dụ "Nhóm bệnh nhân có kích thước tổn thương lớn có thời gian thực hiện thủ thuật dài hơn nhóm còn lại." Mục tiêu là giữ nội dung diễn giải khoa học và bỏ phần trình bày số liệu chi tiết. Không tóm tắt hoặc viết lại phần diễn giải được giữ.

## Tài liệu tham khảo và metadata

Không lấy TÀI LIỆU THAM KHẢO, REFERENCES hoặc toàn bộ danh sách tài liệu phía sau, kể cả khi chúng xuất hiện sau section cuối. Không đưa References vào sections.

Loại bỏ tên tạp chí, tên tác giả, cơ quan công tác, email, DOI, ISSN, ngày nhận/sửa/duyệt bài, số trang, volume, issue, header, footer, copyright, URL và dòng tải PDF khỏi content.

## Tóm tắt

TÓM TẮT/ABSTRACT không phải mục La Mã và không được đưa vào sections. Nếu bài có phần TÓM TẮT hoặc ABSTRACT, lưu nguyên phần văn bản khoa học của nó vào trường abstract. Phạm vi abstract kết thúc trước heading La Mã đầu tiên. Loại bảng/hình/metadata khỏi abstract nhưng không tóm tắt hoặc diễn giải lại. Nếu không có phần tóm tắt thì trả abstract = "".

## Làm sạch PDF và Unicode

Nối các dòng wrap thuộc cùng đoạn. Có thể ghép "nghiên" + "cứu" thành "nghiên cứu". Nếu từ bị ngắt bằng dấu gạch ngang do xuống dòng, ví dụ "nghi-" + "ên cứu", chỉ nối khi chắc chắn đó là lỗi PDF. Không tự sửa nội dung chuyên môn.

Giữ chính xác dấu tiếng Việt, ký tự Unicode, tên riêng và thuật ngữ y khoa. Toàn bộ đầu vào và đầu ra phải là UTF-8.

## Định dạng JSON

Chỉ trả JSON hợp lệ theo schema:
{
  "article_title": "Tên bài báo",
  "abstract": "Nội dung tóm tắt nếu có",
  "sections": [
    {
      "order": 1,
      "roman_number": "I",
      "title": "I. ĐẶT VẤN ĐỀ",
      "normalized_title": "ĐẶT VẤN ĐỀ",
      "content": "Nội dung văn bản..."
    }
  ]
}

normalized_title là title sau khi bỏ đúng tiền tố số La Mã và dấu chấm; không đổi tên hoặc diễn giải tiêu đề.

## Thứ tự xử lý bắt buộc

Đọc toàn bộ PDF -> xác định tiêu đề bài -> xác định TÓM TẮT/ABSTRACT -> tìm heading số La Mã -> xác định phạm vi đến heading La Mã tiếp theo -> loại heading con -> loại bảng -> loại hình/biểu đồ/sơ đồ -> loại số liệu thống kê chi tiết -> giữ văn bản diễn giải -> làm sạch header/footer -> trả JSON.

## Kiểm tra trước khi trả

1. Chỉ có mục La Mã I-X trong sections.
2. Không có mục 1., 2., 2.1, 2.2 hoặc 3.1.
3. Không có bảng, dữ liệu bảng, hình, caption, biểu đồ hoặc sơ đồ.
4. Không có danh sách tài liệu tham khảo, header/footer hoặc metadata tạp chí.
5. Không làm mất nội dung khoa học quan trọng.
6. Không tóm tắt, viết lại, tự thêm thông tin hoặc tự tạo mục La Mã.
7. JSON hợp lệ và tiếng Việt giữ đúng Unicode UTF-8.

Chỉ trả JSON. Không Markdown. Không giải thích. Không thêm nội dung ngoài JSON."""


PDF_STRUCTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "article_title": {"type": "string"},
        "abstract": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "order": {"type": "integer"},
                    "roman_number": {
                        "type": "string",
                        "enum": ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"],
                    },
                    "title": {"type": "string"},
                    "normalized_title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": [
                    "order", "roman_number", "title", "normalized_title", "content"
                ],
            },
        },
    },
    "required": ["article_title", "abstract", "sections"],
}


class GeminiPdfExtractionError(RuntimeError):
    """Lỗi khi Gemini không thể trả cấu trúc PDF hợp lệ."""


def _prepare_ascii_upload_path(path: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    """Tạo tên tạm ASCII vì Files API không mã hóa được filename Unicode."""
    try:
        str(path).encode("ascii")
        return path, None
    except UnicodeEncodeError:
        temp_dir = tempfile.TemporaryDirectory(prefix="mednlp_gemini_pdf_")
        upload_path = Path(temp_dir.name) / "article.pdf"
        shutil.copyfile(path, upload_path)
        return upload_path, temp_dir


def _strip_json_fence(value: str) -> str:
    value = (value or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else value


def normalize_gemini_result(data: Any, fallback_title: str) -> dict[str, Any]:
    """Kiểm tra và chuẩn hóa JSON, không tự sáng tác tiêu đề hay nội dung."""
    if not isinstance(data, dict):
        raise GeminiPdfExtractionError("Gemini không trả về một JSON object")

    article_title = str(data.get("article_title") or "").strip() or fallback_title.strip()
    abstract = data.get("abstract", "")
    if abstract is None:
        abstract = ""
    if not isinstance(abstract, str):
        raise GeminiPdfExtractionError("Trường abstract không phải chuỗi")

    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list):
        raise GeminiPdfExtractionError("Gemini không trả về danh sách sections")

    sections: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    roman_pattern = re.compile(
        r"^\s*(?P<roman>X|IX|VIII|VII|VI|V|IV|III|II|I)\.\s*(?P<name>\S.*)$",
        flags=re.IGNORECASE,
    )
    for raw in raw_sections:
        if not isinstance(raw, dict):
            raise GeminiPdfExtractionError("Một section trong kết quả không phải JSON object")

        title = str(raw.get("title") or "").strip()
        if not title:
            raise GeminiPdfExtractionError("Gemini trả về section không có title")

        match = roman_pattern.match(title)
        if not match:
            raise GeminiPdfExtractionError(
                f"Section không bắt đầu bằng số La Mã I-X: {title}"
            )
        roman_number = match.group("roman").upper()
        returned_roman = str(raw.get("roman_number") or "").strip().upper()
        if returned_roman != roman_number:
            raise GeminiPdfExtractionError(
                f"roman_number không khớp title tại '{title}'"
            )

        title_key = re.sub(r"\s+", " ", title).casefold()
        if title_key in seen_titles:
            raise GeminiPdfExtractionError(f"Tiêu đề bị lặp trong kết quả Gemini: {title}")
        seen_titles.add(title_key)

        content = raw.get("content", "")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise GeminiPdfExtractionError(f"Content của '{title}' không phải chuỗi")

        # Theo prompt mới, normalized_title chỉ là title sau khi bỏ số La Mã.
        # Tính lại từ title để ngăn AI diễn giải hoặc đổi tên tiêu đề.
        normalized_title = match.group("name").strip()

        sections.append({
            "order": len(sections) + 1,
            "roman_number": roman_number,
            "level": "section",
            "level_number": 1,
            "heading_type": "section",
            "title": title,
            "heading": title,
            "normalized_title": normalized_title,
            "parent": None,
            "label": "section",
            "content": content.strip(),
        })

    return {
        "article_title": article_title,
        "abstract": abstract.strip(),
        "sections": sections,
    }


def _wait_until_active(client: Any, uploaded_file: Any, timeout_seconds: int = 60) -> Any:
    """Chờ Files API xử lý xong PDF trước khi gọi model."""
    deadline = time.monotonic() + timeout_seconds
    current = uploaded_file
    while time.monotonic() < deadline:
        state = getattr(current, "state", None)
        state_name = str(getattr(state, "name", state or "")).upper()
        if not state_name or state_name.endswith("ACTIVE"):
            return current
        if state_name.endswith("FAILED"):
            raise GeminiPdfExtractionError("Gemini Files API không xử lý được PDF")
        time.sleep(1)
        current = client.files.get(name=current.name)
    raise GeminiPdfExtractionError("Hết thời gian chờ Gemini xử lý PDF")


def extract_pdf_structure_with_gemini(pdf_path: str) -> dict[str, Any]:
    """Gửi toàn bộ PDF cho Gemini một lần và nhận cấu trúc bài báo có schema."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiPdfExtractionError("Chưa cấu hình GEMINI_API_KEY trong file .env")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiPdfExtractionError(
            "Thiếu google-genai. Hãy cài dependencies từ backend/requirements.txt"
        ) from exc

    path = Path(pdf_path)
    upload_path, local_upload_temp = _prepare_ascii_upload_path(path)
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_PDF_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(
                attempts=2,
                initial_delay=1,
                max_delay=4,
            ),
        ),
    )
    uploaded = None
    started_at = time.monotonic()
    try:
        logger.info("Bắt đầu tải PDF lên Gemini: %s", path.name)
        uploaded = client.files.upload(
            file=str(upload_path),
            config=types.UploadFileConfig(
                mime_type="application/pdf",
                # Tên hiển thị cũng phải là ASCII để tránh lỗi codec trong SDK.
                # Gemini vẫn đọc article_title trực tiếp từ nội dung PDF.
                display_name="article.pdf",
            ),
        )
        uploaded = _wait_until_active(client, uploaded)
        logger.info(
            "Gemini đã nhận PDF sau %.1f giây; bắt đầu tách cấu trúc",
            time.monotonic() - started_at,
        )
        response = client.models.generate_content(
            model=GEMINI_PDF_MODEL,
            contents=[PDF_STRUCTURE_PROMPT, uploaded],
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=65536,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
                response_mime_type="application/json",
                response_schema=PDF_STRUCTURE_SCHEMA,
            ),
        )
        raw_text = _strip_json_fence(response.text)
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise GeminiPdfExtractionError("Gemini trả về JSON không hợp lệ") from exc
        result = normalize_gemini_result(parsed, fallback_title=path.stem)
        logger.info(
            "Gemini tách xong %d mục sau %.1f giây",
            len(result["sections"]),
            time.monotonic() - started_at,
        )
        return result
    except GeminiPdfExtractionError:
        raise
    except Exception as exc:
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            raise GeminiPdfExtractionError(
                "Gemini xử lý quá 5 phút. Hãy thử lại hoặc dùng PDF ngắn hơn"
            ) from exc
        raise GeminiPdfExtractionError(f"Không thể tách PDF bằng Gemini: {exc}") from exc
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        client.close()
        if local_upload_temp is not None:
            local_upload_temp.cleanup()
