from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import urllib3
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from api import routes as api_routes
from api import annotation_routes
from core.ner_dict import DICT_DIR, MANIFEST_PATH, load_ner_dictionary
from core.scraper import ensure_journal_schema
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Khởi tạo từ điển NER ngay khi app start ───────────────────
load_ner_dictionary()


def _print_dictionary_source_counts() -> None:
    """In số thuật ngữ lấy trực tiếp từ hai nguồn nghiên cứu."""
    def emit(line: str) -> None:
        try:
            sys.stdout.buffer.write((line + "\n").encode("utf-8"))
            sys.stdout.flush()
        except (AttributeError, UnicodeEncodeError):
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    try:
        import json

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        files = manifest["files"]
        icd_payload = json.loads(
            (DICT_DIR / files["icd10"]["path"]).read_text(encoding="utf-8")
        )
        yhct_payload = json.loads(
            (DICT_DIR / files["yhct"]["path"]).read_text(encoding="utf-8")
        )
        icd_count = int(icd_payload.get("counts", {}).get("source_rows") or len(icd_payload.get("entries", [])))
        yhct_count = int(yhct_payload.get("counts", {}).get("all") or len(yhct_payload.get("entries", [])))
        emit("=" * 66)
        emit(f"[TỪ ĐIỂN] ICD10VN (Excel): đã thu thập {icd_count:,} thuật ngữ")
        emit(f"[TỪ ĐIỂN] Phụ lục 1 (PDF): đã thu thập {yhct_count:,} thuật ngữ")
        emit(f"[TỪ ĐIỂN] Tổng hai nguồn: {icd_count + yhct_count:,} thuật ngữ")
        emit("=" * 66)
    except Exception as exc:
        emit(f"[TỪ ĐIỂN] Không đọc được thống kê nguồn: {exc}")


_print_dictionary_source_counts()

# ── Cấu hình DB ───────────────────────────────────────────────
db_config = {
    "user":     "root",
    "password": os.environ["DB_PASSWORD"],
    "host":     "127.0.0.1",
    "database": "yhoc_corpus", # fixed typo yhoc_corpuss -> yhoc_corpus
    "charset":  "utf8mb4",
}

OUTPUT_FOLDER = "Kho_Ngu_Lieu_Txt"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# ── Khởi tạo app ──────────────────────────────────────────────
app = FastAPI(title="NER Y Học Tiếng Việt", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ── Khởi tạo router và include vào app ────────────────────────
api_routes.init_router(db_config, OUTPUT_FOLDER)
annotation_routes.init_annotation_router(db_config)
app.include_router(api_routes.router)
app.include_router(annotation_routes.router)

# ── Migration: đảm bảo crawl_logs có cột status ──────────────
try:
    import mysql.connector
    _conn = mysql.connector.connect(**db_config)
    _cur = _conn.cursor()
    _cur.execute(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name='crawl_logs' AND column_name='status'",
        (db_config["database"],)
    )
    if _cur.fetchone()[0] == 0:
        _cur.execute("ALTER TABLE crawl_logs ADD COLUMN status VARCHAR(20) DEFAULT 'completed'")
        _conn.commit()
    _cur.close()
    _conn.close()
except Exception:
    pass

# ── Migration: metadata và bộ đếm cho crawler tạp chí đối soát ──
try:
    ensure_journal_schema(db_config)
except Exception as exc:
    print(f"[CRAWLER] Không thể tự động nâng schema: {exc}")

# ── Entry point ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, access_log=False)
