import hashlib
import logging
import re
from pathlib import Path

from config.constants import PDF_MAGIC_BYTES, MAX_FILE_SIZE_BYTES
from models.metadata import ExtractedMetadata, ProcessingStep

logger = logging.getLogger(__name__)

class PipelineError(Exception):
    def __init__(self, message: str, step: str = ""):
        self.step = step
        super().__init__(message)

class ExtractorPipeline:
    """
    Pipeline xử lý PDF — trích xuất metadata từ file PDF và chia section.
    """

    def __init__(self):
        logger.info("ExtractorPipeline initialized")

    def run(self, file_path: str, source: str = "upload") -> ExtractedMetadata:
        """
        Chạy toàn bộ pipeline trên một file PDF.
        """
        metadata = ExtractedMetadata(source=source, file_path=file_path)

        # Step 1: Pre-check
        self._step_precheck(file_path, metadata)

        # Step 2: Send the complete PDF to Gemini and split its real headings.
        self._step_extract_text(file_path, metadata)

        return metadata

    def _step_precheck(self, file_path: str, metadata: ExtractedMetadata) -> None:
        """
        Pre-check file PDF:
        1. File tồn tại?
        2. File size ≤ giới hạn?
        3. Magic bytes = %PDF?
        4. Tính SHA-256 hash.
        """
        step = ProcessingStep(step_name="precheck")
        step.start()

        try:
            path = Path(file_path)

            if not path.exists():
                raise PipelineError(f"File not found: {file_path}", step="precheck")

            file_size = path.stat().st_size
            if file_size > MAX_FILE_SIZE_BYTES:
                size_mb = file_size / (1024 * 1024)
                max_mb = MAX_FILE_SIZE_BYTES / (1024 * 1024)
                raise PipelineError(f"File too large: {size_mb:.1f}MB (max {max_mb:.0f}MB)", step="precheck")

            with open(file_path, "rb") as f:
                magic = f.read(4)
            if magic != PDF_MAGIC_BYTES:
                raise PipelineError(f"Not a valid PDF file (magic bytes: {magic!r})", step="precheck")

            metadata.file_hash_sha256 = self._sha256(file_path)
            metadata.steps_completed.append("precheck")

            step.complete(success=True)
            logger.info(f"Pre-check passed: {path.name} ({file_size / 1024:.1f}KB, hash={metadata.file_hash_sha256[:12]}...)")

        except PipelineError:
            step.complete(success=False, error=str(step))
            raise
        except Exception as e:
            step.complete(success=False, error=str(e))
            raise PipelineError(f"Unexpected error in precheck: {e}", step="precheck")
        finally:
            metadata.processing_steps.append(step)

    def _step_extract_text(self, file_path: str, metadata: ExtractedMetadata) -> None:
        step = ProcessingStep(step_name="extract_text")
        step.start()
        try:
            from core.ai_pdf_extractor import GEMINI_PDF_MODEL, extract_pdf_structure_with_gemini

            result = extract_pdf_structure_with_gemini(file_path)
            sections = result["sections"]
            article_title = result["article_title"]
            abstract = result.get("abstract", "")

            text_parts = [abstract] if abstract else []
            text_parts.extend(
                section.get("content", "") for section in sections if section.get("content")
            )
            metadata.extracted_text = "\n\n".join(text_parts)
            metadata.headings = [
                {
                    "order": section["order"],
                    "level": section["level"],
                    "title": section["title"],
                    "normalized_title": section.get("normalized_title", section["title"]),
                    "parent": section.get("parent"),
                }
                for section in sections
            ]
            metadata.sections = sections
            has_abstract = bool(abstract.strip())
            metadata.validation_report = {
                "ok": True,
                "issues": [],
                "section_count": len(sections),
                "article_title": article_title,
                "method": "gemini_pdf_structure",
                "model": GEMINI_PDF_MODEL,
                "has_abstract": has_abstract,
            }

            self._save_sections(
                sections,
                Path(file_path).stem,
                metadata,
                article_title=article_title,
                abstract=abstract,
            )

            metadata.steps_completed.append("extract_text")
            step.complete(success=True)
        except Exception as e:
            step.complete(success=False, error=str(e))
            raise PipelineError(f"Tách cấu trúc PDF bằng Gemini thất bại: {e}", step="extract_text")
        finally:
            metadata.processing_steps.append(step)

    def _save_sections(
        self,
        sections: list,
        base_name: str,
        metadata: ExtractedMetadata,
        article_title: str | None = None,
        abstract: str = "",
    ) -> None:
        """Lưu mỗi section thành ``<tên bài>_<tiêu đề>.txt``."""
        out_dir = Path("Văn_Bản_Y_Tế_TXT")
        out_dir.mkdir(exist_ok=True)

        safe_article_title = self._safe_file_component(article_title or base_name, max_length=130)
        safe_base_name = self._safe_file_component(base_name, max_length=130)

        # Loại kết quả cũ của đúng bài này, gồm cả tên theo file PDF cũ và tên bài
        # do Gemini nhận diện, để lần chạy mới không lẫn section.
        output_prefixes = {f"{safe_base_name}_", f"{safe_article_title}_"}
        for old_file in out_dir.iterdir():
            if (
                old_file.is_file()
                and old_file.suffix.lower() == ".txt"
                and any(old_file.name.startswith(prefix) for prefix in output_prefixes)
            ):
                old_file.unlink()

        title_counts: dict[str, int] = {}
        abstract = abstract.strip()
        if abstract:
            abstract_path = out_dir / f"{safe_article_title}_TÓM TẮT.txt"
            with open(abstract_path, "w", encoding="utf-8") as file:
                file.write(abstract)
            metadata.extracted_files.append({
                "file_path": str(abstract_path),
                "section_name": "TÓM TẮT",
                "heading": "TÓM TẮT",
                "normalized_title": "TÓM TẮT",
                "label": "abstract",
                "level": "abstract",
                "order": 0,
                "content_preview": abstract[:300].strip() + ("..." if len(abstract) > 300 else ""),
            })

        for sec in sections:
            content = sec.get("content", "").strip()
            heading = str(sec.get("title") or sec.get("heading") or "section").strip()
            level = str(sec.get("level") or sec.get("label") or "section").strip()
            safe_heading = self._safe_file_component(heading, max_length=90)

            title_counts[safe_heading] = title_counts.get(safe_heading, 0) + 1
            occurrence = title_counts[safe_heading]
            suffix = "" if occurrence == 1 else f"_{occurrence}"
            file_name = f"{safe_article_title}_{safe_heading}{suffix}.txt"
            out_path  = out_dir / file_name
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(content)

            preview = content[:300].strip()
            if len(content) > 300:
                preview += "..."

            metadata.extracted_files.append({
                "file_path":       str(out_path),
                "section_name":    heading,
                "heading":         heading,
                "normalized_title": sec.get("normalized_title", heading),
                "label":           level,
                "level":           level,
                "order":           sec.get("order"),
                "content_preview": preview,
            })

        logger.info(
            "[%s]: Gemini đã tách %d tiêu đề và lưu theo tên bài '%s'",
            base_name,
            len(sections),
            safe_article_title,
        )

    @staticmethod
    def _safe_file_component(value: str, max_length: int = 100) -> str:
        """Giữ Unicode nhưng loại ký tự không hợp lệ trong tên file Windows."""
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(value))
        value = re.sub(r"\s+", " ", value).strip(" .")
        value = value[:max_length].rstrip(" .") or "không_tên"
        if value.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
            value = f"_{value}"
        return value

    @staticmethod
    def _sha256(file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
