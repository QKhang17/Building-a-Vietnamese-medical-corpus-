import tempfile
import unittest
from pathlib import Path

from core.ai_pdf_extractor import (
    GeminiPdfExtractionError,
    _prepare_ascii_upload_path,
    normalize_gemini_result,
)


class AiPdfExtractorTests(unittest.TestCase):
    def test_unicode_pdf_name_is_copied_to_ascii_upload_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "Tác dụng chống xơ gan.pdf"
            source.write_bytes(b"%PDF-test")
            upload_path, cleanup = _prepare_ascii_upload_path(source)
            try:
                str(upload_path).encode("ascii")
                self.assertEqual(upload_path.name, "article.pdf")
                self.assertEqual(upload_path.read_bytes(), source.read_bytes())
            finally:
                if cleanup is not None:
                    cleanup.cleanup()

    def test_accepts_abstract_and_only_roman_sections(self):
        result = normalize_gemini_result(
            {
                "article_title": "Nghiên cứu y khoa",
                "abstract": "Mục tiêu của nghiên cứu là...",
                "sections": [
                    {
                        "order": 9,
                        "roman_number": "I",
                        "title": "I. ĐẶT VẤN ĐỀ",
                        "normalized_title": "ĐẶT VẤN ĐỀ",
                        "content": "Nội dung đặt vấn đề.",
                    },
                    {
                        "order": 20,
                        "roman_number": "II",
                        "title": "II. PHƯƠNG PHÁP NGHIÊN CỨU",
                        "normalized_title": "PHƯƠNG PHÁP NGHIÊN CỨU",
                        "content": "Nội dung phương pháp.",
                    },
                ],
            },
            fallback_title="fallback",
        )

        self.assertEqual(result["article_title"], "Nghiên cứu y khoa")
        self.assertEqual(result["abstract"], "Mục tiêu của nghiên cứu là...")
        self.assertEqual([section["order"] for section in result["sections"]], [1, 2])
        self.assertEqual(result["sections"][0]["roman_number"], "I")
        self.assertEqual(result["sections"][1]["normalized_title"], "PHƯƠNG PHÁP NGHIÊN CỨU")

    def test_allows_empty_sections_when_article_has_no_roman_headings(self):
        result = normalize_gemini_result(
            {"article_title": "Paper", "abstract": "", "sections": []},
            fallback_title="fallback",
        )
        self.assertEqual(result["sections"], [])
        self.assertEqual(result["abstract"], "")

    def test_rejects_non_roman_heading(self):
        with self.assertRaises(GeminiPdfExtractionError):
            normalize_gemini_result(
                {
                    "article_title": "Paper",
                    "abstract": "",
                    "sections": [{
                        "order": 1,
                        "roman_number": "I",
                        "title": "1. ĐẶT VẤN ĐỀ",
                        "normalized_title": "ĐẶT VẤN ĐỀ",
                        "content": "A",
                    }],
                },
                fallback_title="fallback",
            )

    def test_rejects_mismatched_roman_number(self):
        with self.assertRaises(GeminiPdfExtractionError):
            normalize_gemini_result(
                {
                    "article_title": "Paper",
                    "abstract": "",
                    "sections": [{
                        "order": 1,
                        "roman_number": "II",
                        "title": "I. ĐẶT VẤN ĐỀ",
                        "normalized_title": "ĐẶT VẤN ĐỀ",
                        "content": "A",
                    }],
                },
                fallback_title="fallback",
            )

    def test_normalized_title_is_derived_from_original_title(self):
        result = normalize_gemini_result(
            {
                "article_title": "Paper",
                "abstract": "",
                "sections": [{
                    "order": 1,
                    "roman_number": "IV",
                    "title": "IV. BÀN LUẬN",
                    "normalized_title": "Discussion",
                    "content": "Nội dung.",
                }],
            },
            fallback_title="fallback",
        )
        self.assertEqual(result["sections"][0]["normalized_title"], "BÀN LUẬN")


if __name__ == "__main__":
    unittest.main()
