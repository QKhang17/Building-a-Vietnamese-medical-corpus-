import re
import time
import random
import os
import threading
from pathlib import Path
import requests
import mysql.connector
import pymupdf
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime
from urllib.parse import urlparse, urljoin
from core.lang_detector import detect_language
from core.tamanh_scraper import is_tamanh_url, run_tamanh_scraping

# Trạng thái scraping toàn cục — được đọc bởi /api/status
scrape_status: dict = {
    "running": False, "success": 0, "skipped": 0, "duplicates": 0,
    "current_year": None, "current_url": None, "error": None,
    "done": False, "summary": None, "log_messages": [],
}

_JOURNAL_COUNTERS = (
    "issues_found", "articles_discovered", "articles_saved",
    "articles_existing", "articles_repaired", "missing_abstract",
    "pdf_saved", "pdf_unavailable", "pdf_failed",
    "reconciliation_added", "unresolved",
)

_MAX_LOGS = 300
_stop_requested = False
_log_id = None
_asset_inventory: dict[tuple[str, str], list[Path]] = {}

def stop_scraping():
    global _stop_requested
    _stop_requested = True

def _log(msg: str):
    """Ghi log vào scrape_status để frontend đọc realtime."""
    scrape_status["log_messages"].append(msg)
    if len(scrape_status["log_messages"]) > _MAX_LOGS:
        del scrape_status["log_messages"][:-_MAX_LOGS]
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode('utf-8', 'replace').decode('cp1252', 'ignore'))

def clean_filename(fn: str) -> str:
    fn = re.sub(r'[\t\n\r\f\v]+', ' ', fn)
    fn = re.sub(r'[\\/:*?"<>|]', "", fn)
    return re.sub(r'\s+', ' ', fn)[:150].strip(" .")


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    """Lấy metadata không phân biệt hoa-thường từ trang bài báo OJS."""
    wanted = {name.casefold() for name in names}
    for meta in soup.find_all("meta"):
        key = str(meta.get("name") or meta.get("property") or "").strip().casefold()
        if key in wanted:
            value = str(meta.get("content") or "").strip()
            if value:
                return value
    return ""


def _classify_article_language(soup: BeautifulSoup, text: str) -> str:
    """Ưu tiên metadata/ngôn ngữ HTML, fallback sang langdetect."""
    declared = _meta_content(
        soup,
        "citation_language",
        "DC.Language",
        "DCTERMS.Language",
        "og:locale",
    )
    if not declared:
        html_tag = soup.find("html")
        declared = str(html_tag.get("lang") or "") if html_tag else ""
    normalized = declared.strip().casefold().replace("_", "-")
    if normalized.startswith("vi") or "vietnam" in normalized:
        return "Vietnamese"
    if normalized.startswith("en") or "english" in normalized:
        return "English"
    return detect_language(text)


def _publication_metadata(
    soup: BeautifulSoup,
    fallback_journal: str,
    fallback_year: str,
    fallback_issue: str,
) -> dict[str, str]:
    """Chuẩn hóa tên tạp chí, năm, tập và số để tổ chức thư mục PDF."""
    journal = _meta_content(
        soup,
        "citation_journal_title",
        "prism.publicationName",
        "DC.Source",
    ) or fallback_journal

    raw_date = _meta_content(
        soup,
        "citation_publication_date",
        "citation_date",
        "prism.publicationDate",
        "DC.Date",
    )
    year_match = re.search(r"(?:19|20)\d{2}", raw_date)
    year = year_match.group(0) if year_match else str(fallback_year)

    volume = _meta_content(soup, "citation_volume", "prism.volume")
    issue = _meta_content(soup, "citation_issue", "prism.number")
    if volume and issue:
        issue_folder = f"Tập_{clean_filename(volume)}_Số_{clean_filename(issue)}"
    elif issue:
        issue_folder = f"Số_{clean_filename(issue)}"
    elif volume:
        issue_folder = f"Tập_{clean_filename(volume)}"
    else:
        issue_folder = clean_filename(fallback_issue) or "Không_rõ_số"

    return {
        "journal": clean_filename(journal) or clean_filename(fallback_journal) or "Không_rõ_tạp_chí",
        "year": year,
        "issue": clean_filename(issue_folder) or "Không_rõ_số",
        "volume": clean_filename(volume),
        "issue_number": clean_filename(issue),
    }


def _classified_storage_dir(
    root: str,
    language: str,
    publication: dict[str, str],
) -> str:
    """Tạo đường dẫn phân loại dùng chung cho PDF và TXT."""
    return os.path.join(
        root,
        language,
        publication["journal"],
        publication["year"],
        publication["issue"],
    )

def _make_absolute(href: str, base: str) -> str:
    """FIX #3: Chuyển mọi href (tương đối hoặc tuyệt đối) về URL đầy đủ."""
    return urljoin(base, href)

def _build_http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,
    )
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _fetch(session: requests.Session, url: str, *, timeout: int = 60, stream: bool = False):
    response = session.get(url, verify=False, timeout=timeout, stream=stream)
    response.raise_for_status()
    return response


def _column_names(cursor, database: str, table: str) -> set[str]:
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
        (database, table),
    )
    return {row[0] for row in cursor.fetchall()}


def ensure_journal_schema(db_config: dict) -> None:
    """Nâng schema crawler tại chỗ, an toàn khi chạy lặp lại trên DB cũ."""
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    try:
        article_columns = {
            "source_journal": "VARCHAR(255) NULL",
            "issue_url": "VARCHAR(512) NULL",
            "issue_title": "VARCHAR(500) NULL",
            "archive_year": "SMALLINT UNSIGNED NULL",
            "abstract_status": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
            "pdf_status": "VARCHAR(24) NOT NULL DEFAULT 'unknown'",
            "pdf_path": "TEXT NULL",
            "txt_path": "TEXT NULL",
            "last_crawl_error": "TEXT NULL",
            "last_checked_at": "DATETIME NULL",
        }
        existing = _column_names(cursor, db_config["database"], "articles")
        for name, definition in article_columns.items():
            if name not in existing:
                cursor.execute(f"ALTER TABLE articles ADD COLUMN {name} {definition}")

        counter_columns = {
            "issues_found": "INT UNSIGNED NOT NULL DEFAULT 0",
            "articles_discovered": "INT UNSIGNED NOT NULL DEFAULT 0",
            "articles_saved": "INT UNSIGNED NOT NULL DEFAULT 0",
            "articles_existing": "INT UNSIGNED NOT NULL DEFAULT 0",
            "articles_repaired": "INT UNSIGNED NOT NULL DEFAULT 0",
            "missing_abstract": "INT UNSIGNED NOT NULL DEFAULT 0",
            "pdf_saved": "INT UNSIGNED NOT NULL DEFAULT 0",
            "pdf_unavailable": "INT UNSIGNED NOT NULL DEFAULT 0",
            "pdf_failed": "INT UNSIGNED NOT NULL DEFAULT 0",
            "reconciliation_added": "INT UNSIGNED NOT NULL DEFAULT 0",
            "unresolved": "INT UNSIGNED NOT NULL DEFAULT 0",
        }
        for table in ("crawl_progress", "crawl_logs"):
            existing = _column_names(cursor, db_config["database"], table)
            for name, definition in counter_columns.items():
                if name not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _resolve_archive_url(session: requests.Session, target_url: str) -> tuple[str, str, str]:
    parsed = urlparse(target_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"URL không hợp lệ: '{target_url}'. Vui lòng nhập đầy đủ https://...")
    base_url = re.sub(r"/(article|issue)/.*", "", target_url).rstrip("/")
    site_folder = re.sub(r"[^\w\-\.]", "_", re.sub(r"^www\.", "", parsed.netloc))
    archive_url = f"{base_url}/issue/archive"
    home = _fetch(session, base_url)
    soup = BeautifulSoup(home.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True).casefold()
        if any(word in text for word in ("lưu trữ", "archives", "archive")):
            archive_url = urljoin(base_url, anchor["href"])
            break
    return base_url, archive_url, site_folder


def _discover_journal_manifest(
    session: requests.Session,
    archive_url: str,
    base_url: str,
    start_year: int,
    end_year: int,
) -> dict:
    """Quét hết phân trang và trả manifest URL bài theo tập, không ghi DB/file."""
    issues: dict[str, dict] = {}
    seen_page_issue_sets: set[tuple[str, ...]] = set()
    page = 1
    while page <= 200 and not _stop_requested:
        page_url = archive_url if page == 1 else f"{archive_url.rstrip('/')}/{page}"
        response = _fetch(session, page_url)
        soup = BeautifulSoup(response.text, "html.parser")
        page_issue_urls = tuple(sorted({
            urljoin(page_url, a["href"])
            for a in soup.find_all("a", href=True)
            if "/issue/view/" in a["href"]
        }))
        if not page_issue_urls or page_issue_urls in seen_page_issue_sets:
            break
        seen_page_issue_sets.add(page_issue_urls)

        current_year = None
        for tag in soup.find_all(["div", "h2", "h3", "a"]):
            text = tag.get_text(" ", strip=True)
            if tag.name in ("div", "h2", "h3") and re.fullmatch(r"20\d{2}", text):
                current_year = int(text)
            if tag.name != "a" or "/issue/view/" not in tag.get("href", ""):
                continue
            match = re.search(r"(?:19|20)\d{2}", text)
            issue_year = int(match.group(0)) if match else current_year
            if issue_year is None or not start_year <= issue_year <= end_year:
                continue
            issue_url = urljoin(page_url, tag["href"])
            issues.setdefault(issue_url, {
                "url": issue_url,
                "title": clean_filename(text) or f"Issue_{issue_year}",
                "archive_year": issue_year,
                "articles": [],
            })
        page += 1

    articles: dict[str, dict] = {}
    for issue in issues.values():
        if _stop_requested:
            break
        response = _fetch(session, issue["url"])
        soup = BeautifulSoup(response.text, "html.parser")
        if not issue["title"] or issue["title"] == str(issue["archive_year"]):
            heading = soup.find("h1")
            issue["title"] = clean_filename(heading.get_text(" ", strip=True)) if heading else issue["title"]
        links = sorted({
            urljoin(base_url, anchor["href"])
            for anchor in soup.find_all("a", href=True)
            if re.search(r"/article/view/\d+$", urljoin(base_url, anchor["href"]))
        })
        issue["articles"] = links
        for article_url in links:
            articles.setdefault(article_url, {
                "url": article_url,
                "issue_url": issue["url"],
                "issue_title": issue["title"],
                "archive_year": issue["archive_year"],
            })
    return {"issues": issues, "articles": articles}


def _extract_abstract(soup: BeautifulSoup) -> str | None:
    node = soup.select_one(
        ".item.abstract, section.abstract, .article-abstract, .abstract, .article-details-abstract"
    )
    value = node.get_text(" ", strip=True) if node else ""
    if not value:
        for heading in soup.find_all(["h2", "h3", "h4", "strong", "b", "span"]):
            if "tóm tắt" in heading.get_text(" ", strip=True).casefold():
                container = heading.find_next_sibling() or heading.find_parent("div") or heading.find_parent("section")
                if container:
                    value = container.get_text(" ", strip=True)
                    break
    value = re.sub(r"^(Tóm tắt|Abstract)[\s:\.\-]*", "", value, flags=re.IGNORECASE).strip()
    return value if len(value) >= 50 else None


def _find_pdf_url(soup: BeautifulSoup, base_url: str) -> str | None:
    meta_url = _meta_content(soup, "citation_pdf_url")
    if meta_url:
        return urljoin(base_url, meta_url)
    for anchor in soup.find_all("a", href=True):
        text = anchor.get_text(" ", strip=True).casefold()
        classes = " ".join(anchor.get("class", [])).casefold()
        href = urljoin(base_url, anchor["href"])
        if "pdf" in text or "pdf" in classes or "/article/download/" in href:
            if "/article/view/" in href or "/article/download/" in href:
                return href
    return None


def _is_valid_pdf(path: str | Path) -> bool:
    candidate = Path(path)
    if not candidate.is_file() or candidate.stat().st_size < 5:
        return False
    try:
        with candidate.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                return False
        with pymupdf.open(candidate) as document:
            return document.page_count > 0
    except Exception:
        return False


def _download_pdf(session: requests.Session, pdf_url: str, target: Path) -> tuple[str, str | None]:
    part = target.with_suffix(target.suffix + ".part")
    try:
        response = _fetch(session, pdf_url, timeout=120, stream=True)
        content_type = response.headers.get("Content-Type", "").casefold()
        if "pdf" not in content_type and "octet-stream" not in content_type:
            return "download_failed", f"Content-Type không phải PDF: {content_type or 'trống'}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with part.open("wb") as output:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    output.write(chunk)
        if not _is_valid_pdf(part):
            return "download_failed", "File tải về không phải PDF hợp lệ"
        os.replace(part, target)
        return "saved", None
    except Exception as exc:
        return "download_failed", str(exc)
    finally:
        if part.exists():
            part.unlink()


def _existing_asset(path_value: str | None, root: Path, article_id: int, title: str, suffix: str) -> Path | None:
    if path_value and Path(path_value).is_file():
        return Path(path_value).resolve()
    id_patterns = (f"{article_id:04d}_", f"_{article_id:04d}_", f"_{article_id:04d}.")
    title_key = clean_filename(title)[:45].casefold()
    cache_key = (str(root.resolve()), suffix.casefold())
    if cache_key not in _asset_inventory:
        _asset_inventory[cache_key] = list(root.rglob(f"*{suffix}")) if root.exists() else []
    title_matches = []
    for path in _asset_inventory[cache_key]:
        name = path.name.casefold()
        if any(pattern in name for pattern in id_patterns):
            return path.resolve()
        if title_key and title_key in name:
            title_matches.append(path)
    if len(title_matches) == 1:
        return title_matches[0].resolve()
    return None


def _process_journal_article(
    conn,
    session: requests.Session,
    item: dict,
    base_url: str,
    site_folder: str,
    output_folder: str,
    pdf_root: Path,
    *,
    reconciliation: bool = False,
) -> bool:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM articles WHERE source_url=%s", (item["url"],))
        existing = cursor.fetchone()
        is_new = existing is None
        if existing:
            scrape_status["articles_existing"] += 1 if not reconciliation else 0
            old_pdf = _existing_asset(existing.get("pdf_path"), pdf_root, existing["id"], existing["title"], ".pdf")
            old_txt = _existing_asset(existing.get("txt_path"), Path(output_folder), existing["id"], existing["title"], ".txt")
            pdf_ok = bool(old_pdf and _is_valid_pdf(old_pdf))
            abstract_ok = bool(existing.get("abstract") and len(existing["abstract"].strip()) >= 50)
            metadata_current = (
                existing.get("issue_url") == item["issue_url"]
                and existing.get("archive_year") == item["archive_year"]
            )
            terminal_pdf = existing.get("pdf_status") == "not_available"
            if abstract_ok and pdf_ok and old_txt and metadata_current:
                if existing.get("pdf_path") != str(old_pdf) or existing.get("txt_path") != str(old_txt):
                    cursor.execute(
                        "UPDATE articles SET pdf_status='saved',pdf_path=%s,txt_path=%s,last_checked_at=NOW() WHERE id=%s",
                        (str(old_pdf), str(old_txt), existing["id"]),
                    )
                    conn.commit()
                return True
            if reconciliation and abstract_ok and (pdf_ok or terminal_pdf) and old_txt and metadata_current:
                return True

        response = _fetch(session, item["url"])
        soup = BeautifulSoup(response.text, "html.parser")
        title = _meta_content(soup, "citation_title", "DC.Title")
        if not title:
            heading = soup.find("h1")
            title = heading.get_text(" ", strip=True) if heading else f"Bài báo {item['url'].rsplit('/', 1)[-1]}"
        author_nodes = soup.find_all("meta", attrs={"name": "citation_author"})
        authors = ", ".join(node.get("content", "").strip() for node in author_nodes if node.get("content"))
        abstract = _extract_abstract(soup)
        abstract_status = "complete" if abstract else "missing"
        if not abstract:
            scrape_status["missing_abstract"] += 1
        language = _classify_article_language(soup, f"{title}\n{abstract or ''}")
        publication = _publication_metadata(
            soup, site_folder, str(item["archive_year"]), item["issue_title"]
        )
        publication_year = int(publication["year"])

        if is_new:
            cursor.execute(
                "INSERT INTO articles "
                "(title,authors,abstract,publication_year,source_url,source_journal,issue_url,issue_title,"
                "archive_year,abstract_status,pdf_status,last_checked_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unknown',NOW())",
                (title, authors or None, abstract, publication_year, item["url"], publication["journal"],
                 item["issue_url"], item["issue_title"], item["archive_year"], abstract_status),
            )
            article_id = cursor.lastrowid
            scrape_status["articles_saved"] += 1
        else:
            article_id = existing["id"]
            cursor.execute(
                "UPDATE articles SET title=%s,authors=%s,abstract=%s,publication_year=%s,source_journal=%s,"
                "issue_url=%s,issue_title=%s,archive_year=%s,abstract_status=%s,last_checked_at=NOW() WHERE id=%s",
                (title, authors or None, abstract, publication_year, publication["journal"], item["issue_url"],
                 item["issue_title"], item["archive_year"], abstract_status, article_id),
            )
        conn.commit()

        safe_title = clean_filename(title)
        safe_title = safe_title if len(safe_title) <= 50 else safe_title[:47] + "..."
        pdf_dir = Path(_classified_storage_dir(str(pdf_root), language, publication))
        pdf_target = pdf_dir / f"{article_id:04d}_{safe_title}.pdf"
        prior_pdf = None if is_new else _existing_asset(existing.get("pdf_path"), pdf_root, article_id, title, ".pdf")
        pdf_status = "saved" if prior_pdf and _is_valid_pdf(prior_pdf) else "unknown"
        pdf_path = str(prior_pdf) if pdf_status == "saved" else None
        error = None
        if pdf_status != "saved":
            pdf_url = _find_pdf_url(soup, base_url)
            if not pdf_url:
                pdf_status = "not_available"
                scrape_status["pdf_unavailable"] += 1
            else:
                pdf_status, error = _download_pdf(session, pdf_url, pdf_target)
                if pdf_status == "saved":
                    pdf_path = str(pdf_target.resolve())
                    scrape_status["pdf_saved"] += 1
                    scrape_status["pdf_files"] += 1
                else:
                    scrape_status["pdf_failed"] += 1

        txt_dir = Path(_classified_storage_dir(output_folder, language, publication))
        txt_dir.mkdir(parents=True, exist_ok=True)
        txt_target = txt_dir / f"{article_id:04d}_{safe_title}.txt"
        txt_target.write_text(
            f"TIÊU ĐỀ: {title}\nTÁC GIẢ: {authors or 'Không rõ tác giả'}\n"
            + "-" * 40
            + f"\nTÓM TẮT:\n{abstract or '[Nguồn không cung cấp tóm tắt hợp lệ]'}\n",
            encoding="utf-8-sig",
        )
        scrape_status["files_created"] += 1
        cursor.execute(
            "UPDATE articles SET pdf_status=%s,pdf_path=%s,txt_path=%s,last_crawl_error=%s,last_checked_at=NOW() WHERE id=%s",
            (pdf_status, pdf_path, str(txt_target.resolve()), error, article_id),
        )
        conn.commit()
        if not is_new:
            scrape_status["articles_repaired"] += 1
        return True
    except Exception as exc:
        conn.rollback()
        scrape_status["unresolved"] += 1
        scrape_status["skipped"] += 1
        _log(f"    ❌ Lỗi bài {item['url']}: {exc}")
        return False
    finally:
        cursor.close()


def _save_crawl_counters(db_config: dict, request, final_status: str) -> None:
    if not _log_id:
        return
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    try:
        assignments = ",".join(f"{name}=%s" for name in _JOURNAL_COUNTERS)
        values = [int(scrape_status.get(name, 0)) for name in _JOURNAL_COUNTERS]
        cursor.execute(
            f"UPDATE crawl_progress SET success_count=%s,duplicate_count=%s,skipped_count=%s,{assignments},status=%s WHERE id=%s",
            [scrape_status["success"], scrape_status["duplicates"], scrape_status["skipped"], *values, final_status, _log_id],
        )
        if final_status != "paused":
            columns = ",".join(_JOURNAL_COUNTERS)
            placeholders = ",".join(["%s"] * len(_JOURNAL_COUNTERS))
            cursor.execute(
                "INSERT INTO crawl_logs "
                f"(crawl_date,target_url,total_urls,success_count,duplicate_count,error_count,status,start_year,end_year,{columns}) "
                f"VALUES(CURDATE(),%s,%s,%s,%s,%s,%s,%s,%s,{placeholders})",
                [request.target_url, scrape_status["articles_discovered"], scrape_status["success"],
                 scrape_status["duplicates"], scrape_status["unresolved"], final_status,
                 request.start_year, request.end_year, *values],
            )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _run_ojs_scraping(request, db_config: dict, output_folder: str):
    """Thu thập OJS theo manifest và luôn quét đối soát đúng một lần."""
    global _stop_requested, _log_id
    _stop_requested = False
    _log_id = None
    _asset_inventory.clear()
    scrape_status.clear()
    scrape_status.update({
        "running": True, "success": 0, "skipped": 0, "duplicates": 0,
        "current_year": None, "current_url": None, "error": None,
        "done": False, "summary": None, "log_messages": [], "source_type": "journal_pdf",
        "total_urls": 0, "pdf_files": 0, "files_created": 0,
        "phase": "discovery", "issue_details": [],
        **{name: 0 for name in _JOURNAL_COUNTERS},
    })
    conn = None
    try:
        ensure_journal_schema(db_config)
        session = _build_http_session()
        base_url, archive_url, site_folder = _resolve_archive_url(session, request.target_url)
        _log(f"Bắt đầu thu thập {base_url} | {request.start_year}–{request.end_year}")
        _log(f"Trang lưu trữ: {archive_url}")
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO crawl_progress (target_url,start_year,end_year,status) VALUES(%s,%s,%s,'running')",
            (request.target_url, request.start_year, request.end_year),
        )
        conn.commit()
        _log_id = cursor.lastrowid
        cursor.close()

        first = _discover_journal_manifest(
            session, archive_url, base_url, request.start_year, request.end_year
        )
        scrape_status["issues_found"] = len(first["issues"])
        scrape_status["articles_discovered"] = len(first["articles"])
        scrape_status["total_urls"] = len(first["articles"])
        scrape_status["issue_details"] = [
            {"year": issue["archive_year"], "title": issue["title"], "url": issue["url"],
             "articles": len(issue["articles"])}
            for issue in first["issues"].values()
        ]
        _log(f"Đã phát hiện {len(first['issues'])} tập và {len(first['articles'])} URL bài.")

        pdf_root = Path(__file__).resolve().parents[1] / "Văn_Bản_Y_Tế_PDF"
        scrape_status["phase"] = "initial"
        for index, item in enumerate(first["articles"].values(), start=1):
            if _stop_requested:
                break
            scrape_status["current_year"] = item["archive_year"]
            scrape_status["current_url"] = item["url"]
            _process_journal_article(conn, session, item, base_url, site_folder, output_folder, pdf_root)
            if index % 25 == 0 or index == len(first["articles"]):
                _log(f"Lượt đầu: {index}/{len(first['articles'])} bài")

        if not _stop_requested:
            scrape_status["phase"] = "reconciliation"
            _log("Bắt đầu quét đối soát lần hai...")
            second = _discover_journal_manifest(
                session, archive_url, base_url, request.start_year, request.end_year
            )
            added = set(second["articles"]) - set(first["articles"])
            scrape_status["reconciliation_added"] = len(added)
            scrape_status["issues_found"] = len(second["issues"])
            scrape_status["articles_discovered"] = len(second["articles"])
            scrape_status["total_urls"] = len(second["articles"])
            scrape_status["issue_details"] = [
                {"year": issue["archive_year"], "title": issue["title"], "url": issue["url"],
                 "articles": len(issue["articles"])}
                for issue in second["issues"].values()
            ]
            for item in second["articles"].values():
                if _stop_requested:
                    break
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id,abstract,abstract_status,pdf_status,pdf_path,txt_path,issue_url,archive_year "
                    "FROM articles WHERE source_url=%s",
                    (item["url"],),
                )
                row = cursor.fetchone()
                cursor.close()
                needs_repair = (
                    row is None
                    or not row.get("txt_path") or not Path(row["txt_path"]).is_file()
                    or row.get("abstract_status") != "complete"
                    or row.get("pdf_status") in (None, "unknown", "download_failed")
                    or (row.get("pdf_status") == "saved" and not _is_valid_pdf(row.get("pdf_path") or ""))
                    or row.get("issue_url") != item["issue_url"]
                    or row.get("archive_year") != item["archive_year"]
                )
                if needs_repair:
                    _process_journal_article(
                        conn, session, item, base_url, site_folder, output_folder, pdf_root,
                        reconciliation=True,
                    )

            cursor = conn.cursor(dictionary=True)
            expected_urls = list(second["articles"])
            found_rows = []
            for offset in range(0, len(expected_urls), 500):
                chunk = expected_urls[offset:offset + 500]
                placeholders = ",".join(["%s"] * len(chunk))
                cursor.execute(
                    f"SELECT source_url,abstract_status,pdf_status FROM articles "
                    f"WHERE source_url IN ({placeholders})",
                    chunk,
                )
                found_rows.extend(cursor.fetchall())
            cursor.close()
            scrape_status["unresolved"] = len(expected_urls) - len(found_rows)
            scrape_status["missing_abstract"] = sum(
                row.get("abstract_status") != "complete" for row in found_rows
            )
            scrape_status["pdf_saved"] = sum(row.get("pdf_status") == "saved" for row in found_rows)
            scrape_status["pdf_unavailable"] = sum(
                row.get("pdf_status") == "not_available" for row in found_rows
            )
            scrape_status["pdf_failed"] = sum(
                row.get("pdf_status") == "download_failed" for row in found_rows
            )

        scrape_status["success"] = scrape_status["articles_saved"]
        scrape_status["duplicates"] = scrape_status["articles_existing"]
        if scrape_status["unresolved"]:
            scrape_status["error"] = f"Còn {scrape_status['unresolved']} URL chưa có bản ghi DB"
    except Exception as exc:
        scrape_status["error"] = str(exc)
        _log(f"❌ LỖI CRAWLER TẠP CHÍ: {exc}")
    finally:
        final_status = "paused" if _stop_requested else ("error" if scrape_status.get("error") else "completed")
        scrape_status["phase"] = "done"
        scrape_status["summary"] = {
            **{name: scrape_status.get(name, 0) for name in _JOURNAL_COUNTERS},
            "success": scrape_status.get("articles_saved", 0),
            "duplicates": scrape_status.get("articles_existing", 0),
            "skipped": scrape_status.get("unresolved", 0),
            "total_processed": scrape_status.get("articles_discovered", 0),
            "pdf_files": scrape_status.get("pdf_saved", 0),
            "files_created": scrape_status.get("files_created", 0),
            "issue_details": scrape_status.get("issue_details", []),
        }
        try:
            _save_crawl_counters(db_config, request, final_status)
        except Exception as exc:
            _log(f"Không lưu được crawl log: {exc}")
        if conn:
            conn.close()
        scrape_status["running"] = False
        scrape_status["done"] = True
        _log(
            f"Hoàn thành: {scrape_status['articles_discovered']} URL | "
            f"mới {scrape_status['articles_saved']} | sửa {scrape_status['articles_repaired']} | "
            f"chưa xử lý {scrape_status['unresolved']}"
        )


def run_scraping(request, db_config: dict, output_folder: str):
    """Điều phối crawler theo loại website, giữ tương thích với crawler OJS cũ."""
    global _stop_requested
    if not is_tamanh_url(request.target_url):
        _run_ojs_scraping(request, db_config, output_folder)
        return

    scrape_status.clear()
    scrape_status.update(
        {
            "running": True,
            "success": 0,
            "skipped": 0,
            "duplicates": 0,
            "current_year": None,
            "current_url": None,
            "error": None,
            "done": False,
            "summary": None,
            "log_messages": [],
        }
    )
    _stop_requested = False
    try:
        run_tamanh_scraping(
            request,
            db_config,
            scrape_status,
            _log,
            lambda: _stop_requested,
        )
    except Exception as exc:
        scrape_status["error"] = str(exc)
        _log(f"❌ LỖI CRAWLER TÂM ANH: {exc}")
    finally:
        if scrape_status.get("summary") is None:
            scrape_status["summary"] = {
                "success": scrape_status.get("success", 0),
                "duplicates": scrape_status.get("duplicates", 0),
                "skipped": scrape_status.get("skipped", 0),
                "total_processed": scrape_status.get("total_urls", 0),
                "qas": scrape_status.get("qas", 0),
                "files_created": scrape_status.get("files_created", 0),
            }
        scrape_status["running"] = False
        scrape_status["done"] = True
