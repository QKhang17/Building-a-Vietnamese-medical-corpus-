import threading
import json
import os
import io
import hashlib
import mysql.connector
from fastapi import APIRouter, HTTPException, UploadFile, File, Header
from pydantic import BaseModel, Field
from typing import Literal
from core.ner_engine import reset_ner_engine, run_ner
from core.ner_dict import (
    DICT_DIR,
    MANIFEST_PATH,
    VERSIONED_CUSTOM_PATH,
    dictionary_metadata,
    normalize_match_text,
    reload_ner_dictionary,
)
from core.scraper import scrape_status, run_scraping, clean_filename, stop_scraping
from core.ai_ner import extract_entities_with_ai
from core.ai_label import extract_with_ai_label
from core.expert_service import (
    ExpertDatabaseNotConfigured,
    configure as configure_expert_database,
    decode_token as decode_expert_token,
    get_ai_results,
    get_reviews,
    login as login_expert,
    save_ai_result,
    upsert_review,
)

router = APIRouter()

# Đường dẫn file Từ Điển
DICTIONARY_PATH = VERSIONED_CUSTOM_PATH
_db_config: dict = {}
_output_folder: str = ""

def init_router(db_config: dict, output_folder: str) -> None:
    """Khởi tạo các biến cấu hình cho router."""
    global _db_config, _output_folder
    _db_config = db_config
    _output_folder = output_folder
    configure_expert_database(db_config)

class HighlightRequest(BaseModel):
    text:                str
    threshold:           int  = 100
    enable_tone_restore: bool = False
    enable_noun_phrase:  bool = False

class NerRequest(BaseModel):
    text:                str
    threshold:           int  = 100
    enable_tone_restore: bool = False
    enable_noun_phrase:  bool = False

class AiLabelRequest(BaseModel):
    text: str
    article_id: int | None = None

class SaveAiLabelRequest(BaseModel):
    article_id: int
    result: dict

class ExpertLoginRequest(BaseModel):
    username: str
    password: str

class ExpertReviewRequest(BaseModel):
    note: str = ""
    label_source: Literal["manual", "ai"] = "manual"

class SaveHighlightRequest(BaseModel):
    article_id:       int
    highlighted_html: str
    matched_concepts: list

class ScrapeRequest(BaseModel):
    start_year: int
    end_year:   int
    target_url: str

class SaveDictionaryRequest(BaseModel):
    matched_concepts: list

def _get_conn():
    return mysql.connector.connect(**_db_config)


def _require_expert(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Vui lòng đăng nhập chuyên gia")
    try:
        return decode_expert_token(authorization.split(" ", 1)[1].strip())
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc

@router.post("/api/highlight-text")
def highlight_text_endpoint(req: HighlightRequest):
    """Phân tích văn bản — trả về HTML highlight + danh sách khái niệm."""
    highlighted, concepts, _, preproc_log = run_ner(
        req.text,
        req.threshold,
        enable_tone_restore=req.enable_tone_restore,
        enable_noun_phrase=req.enable_noun_phrase,
    )
    note = "" if concepts else "Đoạn văn mô tả cơ chế sinh lý, không chứa chẩn đoán bệnh lý cụ thể"
    return {
        "highlighted_html":  highlighted,
        "matched_concepts":  concepts,
        "note":              note,
        "preprocessing_log": preproc_log,
    }


@router.post("/api/ner")
def ner_endpoint(req: NerRequest):
    """Endpoint NER chuẩn — trả về JSON entities theo schema nghiên cứu."""
    highlighted, concepts, entities, preproc_log = run_ner(
        req.text,
        req.threshold,
        enable_tone_restore=req.enable_tone_restore,
        enable_noun_phrase=req.enable_noun_phrase,
    )
    note = "" if entities else "Đoạn văn mô tả cơ chế sinh lý, không chứa chẩn đoán bệnh lý cụ thể"
    return {
        "highlighted_html": highlighted,
        "entities": [
            {
                "text":         e["text"],
                "label":        e["entity_type"],   # schema mới
                "entity_type":  e["entity_type"],   # legacy
                "start":        e.get("start", -1),
                "end":          e.get("end", -1),
                "icd_code":     e.get("icd_code", ""),
                "icd_label_vn": e.get("icd_label_vn", ""),
                "matched_by":   e.get("matched_by", "exact"),
            }
            for e in entities
        ],
        "note":              note or None,
        "matched_concepts":  concepts,
        "preprocessing_log": preproc_log,
    }


@router.post("/api/ai-ner")
def ai_ner_endpoint(req: NerRequest):
    """Endpoint dùng AI (Gemini) để gán nhãn văn bản và tìm kiếm các thuật ngữ mới."""
    try:
        entities = extract_entities_with_ai(req.text)
        return {
            "entities": entities,
            "message": "Trích xuất thành công bằng AI"
        }
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Lỗi AI: {str(e)}")


@router.post("/api/ai-label")
def ai_label_endpoint(req: AiLabelRequest):
    """Tạo bản xem trước bằng Gemini; chỉ lưu khi người dùng xác nhận."""
    try:
        result = extract_with_ai_label(req.text)
        result["model"] = "gemini-2.5-flash"
        result["saved_for_expert_review"] = False
        return result
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        raise HTTPException(500, f"Lỗi AI Gán Nhãn: {str(e)}")


@router.post("/api/save-ai-label")
def save_ai_label_result_endpoint(req: SaveAiLabelRequest):
    """Lưu snapshot AI sau khi người dùng bấm nút Lưu kết quả."""
    allowed_categories = (
        "Bệnh lý",
        "Triệu chứng",
        "Điều trị",
        "Xét nghiệm",
        "Hình ảnh",
        "Sinh lý",
    )
    normalized_result = {}
    entities_saved = 0
    for category in allowed_categories:
        items = req.result.get(category, [])
        if not isinstance(items, list):
            raise HTTPException(400, f"Dữ liệu nhóm '{category}' không hợp lệ")
        normalized_result[category] = items
        entities_saved += len(items)
    normalized_result["model"] = str(req.result.get("model") or "gemini-2.5-flash")[:120]

    conn = cursor = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM articles WHERE id = %s", (req.article_id,))
        if not cursor.fetchone():
            raise HTTPException(404, f"Không tìm thấy bài báo id={req.article_id}")

        if not save_ai_result(req.article_id, normalized_result):
            raise HTTPException(
                503,
                "Chưa có bảng ai_label_results. Hãy chạy câu lệnh CREATE TABLE trong MySQL Workbench.",
            )
        return {
            "message": "Đã lưu kết quả gán nhãn AI",
            "article_id": req.article_id,
            "entities_saved": entities_saved,
            "saved_for_expert_review": True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/api/ai-label-results/{article_id}")
def get_latest_ai_label_result_endpoint(article_id: int):
    """Trả snapshot AI mới nhất để phục hồi giao diện sau khi refresh."""
    try:
        row = get_ai_results([article_id]).get(article_id)
        if not row:
            return {"article_id": article_id, "result": None, "created_at": None}
        return {
            "article_id": article_id,
            "result": row.get("result_json"),
            "created_at": row.get("created_at"),
        }
    except ExpertDatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except mysql.connector.Error as exc:
        if exc.errno == 1146:
            raise HTTPException(
                503,
                "Chưa có bảng ai_label_results. Hãy chạy câu lệnh CREATE TABLE trong MySQL Workbench.",
            ) from exc
        raise HTTPException(500, str(exc)) from exc


@router.post("/api/expert/login")
def expert_login_endpoint(req: ExpertLoginRequest):
    try:
        expert, token = login_expert(req.username, req.password)
        return {"access_token": token, "token_type": "bearer", "expert": expert}
    except ExpertDatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc


@router.get("/api/expert/me")
def expert_me_endpoint(authorization: str | None = Header(default=None)):
    return _require_expert(authorization)


@router.get("/api/expert/review-items")
def expert_review_items(
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    conn = cursor = None
    try:
        conn = _get_conn()
        cursor = conn.cursor(dictionary=True)
        manual_filter = (
            "(a.highlighted_html IS NOT NULL AND a.highlighted_html!='' "
            "OR EXISTS (SELECT 1 FROM extracted_concepts ec0 WHERE ec0.article_id=a.id))"
        )
        where = f"WHERE {manual_filter}"
        params = []
        if q:
            where += " AND a.title LIKE %s"
            params.append(f"%{q}%")
        cursor.execute(f"SELECT COUNT(*) AS total FROM articles a {where}", params)
        total = int(cursor.fetchone()["total"])
        cursor.execute(
            "SELECT a.id,a.title,a.authors,a.publication_year,a.source_url,"
            "(a.highlighted_html IS NOT NULL AND a.highlighted_html!='') AS manual_labeled,"
            "(SELECT COUNT(*) FROM extracted_concepts ec WHERE ec.article_id=a.id) AS manual_entity_count "
            "FROM articles a "
            f"{where} "
            "ORDER BY a.id DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        items = cursor.fetchall()
        article_ids = [int(item["id"]) for item in items]
        ai_by_article = get_ai_results(article_ids)
        reviews_by_article = get_reviews(article_ids)
        for item in items:
            article_id = int(item["id"])
            ai_row = ai_by_article.get(article_id)
            ai_json = ai_row.get("result_json", {}) if ai_row else {}
            entities = []
            if isinstance(ai_json, dict):
                for value in ai_json.values():
                    if isinstance(value, list):
                        entities.extend(value)
            item["ai_labeled"] = bool(ai_row)
            item["ai_entity_count"] = len(entities) if isinstance(entities, list) else 0
            item["reviews"] = reviews_by_article.get(article_id, [])
            item["my_reviews"] = {
                source: next(
                    (
                        review for review in item["reviews"]
                        if int(review["expert_id"]) == int(expert["sub"])
                        and review["label_source"] == source
                    ),
                    None,
                )
                for source in ("manual", "ai")
            }
        return {"items": items, "total": total, "limit": limit, "offset": offset}
    except ExpertDatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.get("/api/expert/review-items/{article_id}")
def expert_review_item_detail(
    article_id: int,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    conn = cursor = None
    try:
        conn = _get_conn()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id,title,authors,abstract,publication_year,source_url,highlighted_html "
            "FROM articles WHERE id=%s",
            (article_id,),
        )
        article = cursor.fetchone()
        if not article:
            raise HTTPException(404, "Không tìm thấy văn bản")
        cursor.execute(
            "SELECT concept_name AS name,concept_type AS type,concept_code AS code "
            "FROM extracted_concepts WHERE article_id=%s ORDER BY id",
            (article_id,),
        )
        article["manual_entities"] = cursor.fetchall()
        ai_row = get_ai_results([article_id]).get(article_id)
        article["ai_result"] = ai_row.get("result_json") if ai_row else None
        article["reviews"] = get_reviews([article_id]).get(article_id, [])
        article["my_reviews"] = {
            source: next(
                (
                    review for review in article["reviews"]
                    if int(review["expert_id"]) == int(expert["sub"])
                    and review["label_source"] == source
                ),
                None,
            )
            for source in ("manual", "ai")
        }
        return article
    except ExpertDatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@router.put("/api/expert/reviews/{article_id}")
def save_expert_review_endpoint(
    article_id: int,
    req: ExpertReviewRequest,
    authorization: str | None = Header(default=None),
):
    expert = _require_expert(authorization)
    try:
        review = upsert_review(
            int(expert["sub"]),
            article_id,
            req.note,
            req.label_source,
        )
        return {"message": "Đã lưu nhận xét chuyên gia", "review": review}
    except ExpertDatabaseNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc





# ── Endpoint mới: Tách PDF & Nhật ký ──────────────────────────────────

@router.get("/api/crawl-logs")
def get_crawl_logs():
    """Lấy danh sách nhật ký thu thập."""
    conn = cursor = None
    try:
        conn   = _get_conn()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM crawl_logs ORDER BY created_at DESC LIMIT 100")
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()

@router.get("/api/verify-data")
def verify_data(source: str = "", start_year: int | None = None, end_year: int | None = None):
    """Kiểm chứng file theo article_id/đường dẫn đã ghi, không suy đoán bằng tiêu đề."""
    conn = cursor = None
    try:
        conn = _get_conn()
        cursor = conn.cursor(dictionary=True)
        clauses = []
        params = []
        if source:
            clauses.append("source_url LIKE %s")
            params.append(f"%{source}%")
        if start_year is not None:
            clauses.append("archive_year >= %s")
            params.append(start_year)
        if end_year is not None:
            clauses.append("archive_year <= %s")
            params.append(end_year)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor.execute(
            "SELECT id,title,publication_year,archive_year,source_url,issue_title,"
            "abstract_status,pdf_status,pdf_path,txt_path,last_crawl_error "
            f"FROM articles{where} ORDER BY id",
            params,
        )
        articles = cursor.fetchall()
        missing_pdfs = []
        missing_txts = []
        valid_pdfs = 0
        valid_txts = 0
        unavailable_pdfs = 0
        for art in articles:
            pdf_path = art.get("pdf_path") or ""
            pdf_exists = bool(pdf_path and os.path.isfile(pdf_path))
            if art.get("pdf_status") == "not_available":
                unavailable_pdfs += 1
            elif pdf_exists:
                valid_pdfs += 1
            else:
                missing_pdfs.append({
                    "id": art["id"], "title": art["title"],
                    "year": art.get("archive_year") or art.get("publication_year"),
                    "issue": art.get("issue_title"), "source_url": art.get("source_url"),
                    "status": art.get("pdf_status"),
                    "reason": art.get("last_crawl_error") or "Đường dẫn PDF không tồn tại",
                })
            txt_path = art.get("txt_path") or ""
            if txt_path and os.path.isfile(txt_path):
                valid_txts += 1
            else:
                missing_txts.append({
                    "id": art["id"], "title": art["title"],
                    "year": art.get("archive_year") or art.get("publication_year"),
                    "issue": art.get("issue_title"), "source_url": art.get("source_url"),
                    "reason": "Đường dẫn TXT không tồn tại",
                })
        return {
            "total_articles_in_db": len(articles),
            "total_pdfs_on_disk": valid_pdfs,
            "total_txts_on_disk": valid_txts,
            "pdf_unavailable_count": unavailable_pdfs,
            "missing_pdfs_count": len(missing_pdfs),
            "missing_txts_count": len(missing_txts),
            "missing_pdfs": missing_pdfs[:50],
            "missing_txts": missing_txts[:50],
        }
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()

import shutil
import tempfile
from core.pdf_pipeline import ExtractorPipeline
from starlette.concurrency import run_in_threadpool

@router.post("/api/extract-pdf")
async def extract_pdf_endpoint(file: UploadFile = File(...)):
    """Dùng Gemini tách cấu trúc PDF và lưu mỗi mục thành một file TXT."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Chỉ hỗ trợ file PDF (.pdf)")

    temp_dir = tempfile.mkdtemp()
    
    # Lấy tên file an toàn (tránh lỗi khi filename chứa đường dẫn thư mục con)
    safe_filename = os.path.basename(file.filename)
    if not safe_filename:
        safe_filename = "temp.pdf"
        
    temp_path = os.path.join(temp_dir, safe_filename)
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        pipeline = ExtractorPipeline()
        # Gemini là lời gọi mạng đồng bộ và có thể mất vài phút. Chạy trong worker
        # thread để không khóa event loop của FastAPI trong lúc chờ.
        metadata = await run_in_threadpool(pipeline.run, temp_path)
        
        return {
            "message": "Gemini đã tách cấu trúc PDF thành công",
            "files_created": metadata.extracted_files,
            "hash": metadata.file_hash_sha256,
            "validation": metadata.validation_report,
            "article_title": metadata.validation_report.get("article_title", ""),
            "method": metadata.validation_report.get("method", ""),
            "model": metadata.validation_report.get("model", ""),
        }
    except Exception as e:
        raise HTTPException(500, f"Lỗi xử lý PDF: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        os.rmdir(temp_dir)

@router.post("/api/save-highlight")
def save_highlight_endpoint(req: SaveHighlightRequest):
    """Chạy lại NER chuẩn ở server rồi mới lưu, không tin HTML từ trình duyệt."""
    conn = cursor = None
    try:
        conn   = _get_conn()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT id, abstract FROM articles WHERE id = %s", (req.article_id,))
        article = cursor.fetchone()
        if not article:
            raise HTTPException(404, f"Không tìm thấy bài báo id={req.article_id}")

        canonical_html, canonical_concepts, _, _ = run_ner(
            article.get("abstract") or "",
            enable_tone_restore=False,
            enable_noun_phrase=False,
        )

        cursor.execute(
            "UPDATE articles SET highlighted_html = %s WHERE id = %s",
            (canonical_html, req.article_id),
        )
        cursor.execute(
            "DELETE FROM extracted_concepts WHERE article_id = %s",
            (req.article_id,),
        )

        seen, rows = set(), []
        for c in canonical_concepts:
            name  = (c.get("name") or "").strip()
            ctype = (c.get("type") or "DISEASE").strip()
            code  = (c.get("code") or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                rows.append((req.article_id, name, ctype, code))

        if rows:
            cursor.executemany(
                "INSERT INTO extracted_concepts "
                "(article_id, concept_name, concept_type, concept_code) "
                "VALUES (%s, %s, %s, %s)",
                rows,
            )
        conn.commit()
        return {
            "message": "Lưu thành công bằng luật + từ điển",
            "article_id": req.article_id,
            "concepts_saved": len(rows),
            "dictionary_version": dictionary_metadata.get("dictionary_version"),
            "highlighted_html": canonical_html,
            "matched_concepts": canonical_concepts,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@router.get("/api/dictionary")
def get_dictionary():
    """Trả về toàn bộ từ điển y khoa."""
    try:
        if DICTIONARY_PATH.is_file() and DICTIONARY_PATH.stat().st_size > 0:
            with DICTIONARY_PATH.open("r", encoding="utf-8") as f:
                payload = json.load(f)
                return payload.get("entries", payload if isinstance(payload, list) else [])
        return []
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/api/save-to-dictionary")
def save_to_dictionary_endpoint(req: SaveDictionaryRequest):
    """Chỉ thêm bí danh trỏ tới mã đã tồn tại trong ICD-10/YHCT nguồn."""
    try:
        payload = json.loads(DICTIONARY_PATH.read_text(encoding="utf-8"))
        dictionary = payload.get("entries", [])
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        valid_codes = {}
        for source_key in ("icd10", "yhct"):
            source_path = DICT_DIR / manifest["files"][source_key]["path"]
            source_payload = json.loads(source_path.read_text(encoding="utf-8"))
            for entry in source_payload.get("entries", []):
                if not entry.get("active_for_ner") or entry.get("ambiguous"):
                    continue
                code = str(entry.get("code") or "").strip()
                if code:
                    valid_codes[code.casefold()] = (
                        entry.get("canonical_term", ""), entry.get("category", "Bệnh Lý")
                    )

        existing_terms = {normalize_match_text(entry.get("term", "")) for entry in dictionary}
        added, skipped = [], []

        for c in req.matched_concepts:
            name  = (c.get("name") or "").strip()
            code  = (c.get("code") or "").strip()

            if not name:
                continue

            key = normalize_match_text(name)
            canonical = valid_codes.get(code.casefold())
            if not canonical:
                skipped.append({"term": name, "reason": "Mã không tồn tại trong ICD-10/YHCT nguồn"})
            elif key in existing_terms:
                skipped.append(name)
            else:
                existing_terms.add(key)
                dictionary.append({
                    "term": name,
                    "canonical_term": canonical[0],
                    "type": canonical[1],
                    "code": code,
                    "active_for_ner": True,
                    "ambiguous": False,
                    "case_sensitive": name.isupper() and len(name) <= 10,
                    "source": "user_alias",
                })
                added.append(name)

        payload["entries"] = dictionary
        payload["counts"] = {
            "all": len(dictionary),
            "active": sum(bool(item.get("active_for_ner", True)) for item in dictionary),
            "ambiguous": sum(bool(item.get("ambiguous", False)) for item in dictionary),
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        temp_path = DICTIONARY_PATH.with_suffix(".tmp")
        temp_path.write_bytes(encoded)
        os.replace(temp_path, DICTIONARY_PATH)

        manifest["files"]["custom"]["sha256"] = hashlib.sha256(encoded).hexdigest()
        manifest["files"]["custom"]["count"] = len(dictionary)
        manifest_temp = MANIFEST_PATH.with_suffix(".tmp")
        manifest_temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(manifest_temp, MANIFEST_PATH)
        reload_ner_dictionary()
        reset_ner_engine()

        return {
            "added": added,
            "skipped": skipped,
            "total_in_dictionary": len(dictionary),
            "dictionary_version": dictionary_metadata.get("dictionary_version"),
        }

    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/api/articles")
def get_articles(q: str = "", full: bool = False):
    """Lấy danh sách bài báo, hỗ trợ tìm kiếm theo tiêu đề. Mặc định tải siêu nhanh bằng cách chỉ lấy metadata."""
    conn = cursor = None
    try:
        conn   = _get_conn()
        cursor = conn.cursor(dictionary=True)
        if full:
            sql = (
                "SELECT a.id, a.title, a.authors, a.abstract, a.publication_year, a.highlighted_html, "
                "EXISTS(SELECT 1 FROM ai_label_results ar WHERE ar.article_id=a.id) AS ai_labeled "
                "FROM articles a "
            )
        else:
            sql = (
                "SELECT a.id, a.title, a.authors, a.publication_year, "
                "(a.highlighted_html IS NOT NULL AND a.highlighted_html != '') AS is_labeled, "
                "EXISTS(SELECT 1 FROM ai_label_results ar WHERE ar.article_id=a.id) AS ai_labeled "
                "FROM articles a "
            )
        if q:
            cursor.execute(sql + "WHERE title LIKE %s ORDER BY id DESC", (f"%{q}%",))
        else:
            cursor.execute(sql + "ORDER BY id DESC")
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@router.get("/api/articles/{article_id}")
def get_article_detail(article_id: int):
    """Lấy chi tiết 1 bài báo theo ID bao gồm tóm tắt và HTML highlight."""
    conn = cursor = None
    try:
        conn   = _get_conn()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, title, authors, abstract, publication_year, highlighted_html "
            "FROM articles WHERE id = %s",
            (article_id,)
        )
        article = cursor.fetchone()
        if not article:
            raise HTTPException(404, f"Không tìm thấy bài báo id={article_id}")
        cursor.execute(
            "SELECT concept_name AS name, concept_type AS type, concept_code AS code "
            "FROM extracted_concepts WHERE article_id = %s ORDER BY id",
            (article_id,),
        )
        article["matched_concepts"] = cursor.fetchall()
        return article
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()



@router.get("/api/top-concepts")
def get_top_concepts(limit: int = 20, label: str = ""):
    """Lấy các khái niệm xuất hiện nhiều nhất."""
    conn = cursor = None
    try:
        conn   = _get_conn()
        cursor = conn.cursor(dictionary=True)
        if label:
            cursor.execute(
                "SELECT concept_name, concept_type, COUNT(*) as frequency "
                "FROM extracted_concepts WHERE concept_type=%s "
                "GROUP BY concept_name, concept_type ORDER BY frequency DESC LIMIT %s",
                (label, limit),
            )
        else:
            cursor.execute(
                "SELECT concept_name, concept_type, COUNT(*) as frequency "
                "FROM extracted_concepts "
                "GROUP BY concept_name, concept_type ORDER BY frequency DESC LIMIT %s",
                (limit,),
            )
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if cursor: cursor.close()
        if conn:   conn.close()


@router.get("/api/status")
def get_status():
    """Trả về trạng thái tiến trình scraping."""
    return scrape_status


@router.post("/api/scrape")
def trigger_scraping(request: ScrapeRequest):
    """Kích hoạt thu thập dữ liệu ở nền."""
    if scrape_status["running"]:
        raise HTTPException(409, "Đang có tác vụ chạy, vui lòng đợi!")
    threading.Thread(
        target=run_scraping,
        args=(request, _db_config, _output_folder),
        daemon=True,
    ).start()
    return {"message": "Đã bắt đầu thu thập ở nền"}


@router.post("/api/scrape/stop")
def stop_scraping_endpoint():
    """Yêu cầu dừng tiến trình thu thập đang chạy nền."""
    stop_scraping()
    return {"message": "Đã gửi lệnh dừng crawler"}
