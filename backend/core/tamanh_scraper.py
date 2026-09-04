"""Crawler chuyên biệt cho kho hỏi đáp công khai của Bệnh viện Tâm Anh."""

from __future__ import annotations

import hashlib
import html
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import mysql.connector
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


TAMANH_HOSTS = {"tamanhhospital.vn", "www.tamanhhospital.vn"}
TAMANH_ROOT = "https://tamanhhospital.vn/tu-van/"
OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "Tamanh"
BN_DIR = OUTPUT_ROOT / "BN"
BS_DIR = OUTPUT_ROOT / "BS"


def is_tamanh_url(url: str) -> bool:
    return urlparse(url).netloc.casefold() in TAMANH_HOSTS


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    retry = Retry(
        total=3,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _clean_text(value: str) -> str:
    value = BeautifulSoup(html.unescape(value or ""), "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", value).strip()


def _safe_filename(value: str, limit: int = 90) -> str:
    value = re.sub(r'[\\/*?"<>|:\t\r\n]+', " ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "khong-co-tieu-de")[:limit].strip(" .")


def _qapage_from_json_ld(soup: BeautifulSoup) -> dict | None:
    def visit(node):
        if isinstance(node, dict):
            if node.get("@type") == "QAPage" and isinstance(node.get("mainEntity"), dict):
                return node["mainEntity"]
            if node.get("@type") == "Question" and node.get("acceptedAnswer"):
                return node
            for value in node.values():
                found = visit(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = visit(value)
                if found:
                    return found
        return None

    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            found = visit(json.loads(tag.string or tag.get_text() or "{}"))
            if found:
                return found
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _parse_detail(response_text: str, url: str, department: str) -> dict | None:
    soup = BeautifulSoup(response_text, "html.parser")
    question_data = _qapage_from_json_ld(soup)
    if not question_data:
        return None

    date_created = str(question_data.get("dateCreated") or "")
    year_match = re.search(r"(?:19|20)\d{2}", date_created)
    year = int(year_match.group(0)) if year_match else None

    title_node = soup.select_one("section.box_detail h1") or soup.find("h1")
    title = _clean_text(title_node.get_text(" ") if title_node else question_data.get("text", ""))
    question = _clean_text(question_data.get("name", ""))
    patient = _clean_text((question_data.get("author") or {}).get("name", ""))

    answer_node = soup.select_one("#ftwp-postcontent")
    if answer_node:
        for removable in answer_node.select(
            "script, style, noscript, #ftwp-container-outer, .content_insert, .sharedaddy"
        ):
            removable.decompose()
        answer = _clean_text(answer_node.get_text(" "))
    else:
        accepted = question_data.get("acceptedAnswer") or {}
        answer = _clean_text(accepted.get("text", ""))

    doctor_node = soup.select_one(".box_cgia_live img[alt]")
    if doctor_node:
        doctor = _clean_text(doctor_node.get("alt", ""))
    else:
        doctor = _clean_text((question_data.get("acceptedAnswer") or {}).get("author", {}).get("name", ""))

    tags = [_clean_text(a.get_text(" ")) for a in soup.select("section.box_detail .div_tag a")]
    return {
        "title": title,
        "question": question,
        "answer": answer,
        "patient": patient,
        "doctor": doctor,
        "department": department or (tags[0] if tags else "Chưa xác định"),
        "date_created": date_created,
        "year": year,
        "url": url,
    }


def _write_pair(item: dict) -> tuple[int, bool]:
    BN_DIR.mkdir(parents=True, exist_ok=True)
    BS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(item["url"].encode("utf-8")).hexdigest()[:10]
    stem = f"{item['year']}_{digest}_{_safe_filename(item['title'])}"
    bn_path = BN_DIR / f"{stem}_BN.txt"
    bs_path = BS_DIR / f"{stem}_BS.txt"
    duplicate = bn_path.exists() and bs_path.exists()
    created = 0

    metadata = (
        f"TIÊU ĐỀ: {item['title']}\n"
        f"CHUYÊN KHOA: {item['department']}\n"
        f"NGÀY ĐĂNG: {item['date_created']}\n"
        f"NGUỒN: {item['url']}\n"
    )
    payloads = (
        (
            bn_path,
            "LOẠI: CÂU HỎI CỦA BỆNH NHÂN\n"
            + metadata
            + f"NGƯỜI HỎI: {item['patient'] or 'Không công khai'}\n"
            + "-" * 70
            + f"\n{item['question']}\n",
        ),
        (
            bs_path,
            "LOẠI: CÂU TRẢ LỜI CỦA BÁC SĨ\n"
            + metadata
            + f"BÁC SĨ: {item['doctor'] or 'Không công khai'}\n"
            + "-" * 70
            + f"\n{item['answer']}\n",
        ),
    )
    for path, content in payloads:
        if path.exists():
            continue
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(content, encoding="utf-8-sig")
        os.replace(temp_path, path)
        created += 1
    return created, duplicate


def _department_links(soup: BeautifulSoup) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for anchor in soup.select("a.title_catetuvan[href]"):
        href = urljoin(TAMANH_ROOT, anchor.get("href", ""))
        if is_tamanh_url(href):
            found[href.rstrip("/") + "/"] = _clean_text(anchor.get_text(" "))
    return [(url, name or url.rstrip("/").split("/")[-1]) for url, name in found.items()]


def _listing_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    links: list[str] = []
    for heading in soup.select(".item_tuvan h2"):
        anchor = heading.find_parent("a", href=True)
        if not anchor:
            continue
        href = urljoin(base_url, anchor["href"])
        if is_tamanh_url(href) and href not in links:
            links.append(href)
    return links


def _max_page(soup: BeautifulSoup, department_url: str) -> int:
    base_path = urlparse(department_url).path.rstrip("/")
    maximum = 1
    for anchor in soup.select('a[href*="/page/"]'):
        parsed = urlparse(urljoin(department_url, anchor.get("href", "")))
        if not parsed.path.startswith(base_path + "/page/"):
            continue
        match = re.search(r"/page/(\d+)/?", parsed.path)
        if match:
            maximum = max(maximum, int(match.group(1)))
    return maximum


def _save_crawl_log(db_config: dict, request, status: dict, final_status: str, log: Callable[[str], None]) -> None:
    conn = cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO crawl_logs "
            "(crawl_date,target_url,total_urls,success_count,duplicate_count,error_count,status,start_year,end_year) "
            "VALUES(CURDATE(),%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                request.target_url,
                status.get("total_urls", 0),
                status.get("success", 0),
                status.get("duplicates", 0),
                status.get("skipped", 0),
                final_status,
                request.start_year,
                request.end_year,
            ),
        )
        conn.commit()
    except Exception as exc:
        log(f"⚠️ Không ghi được crawl_logs (file TXT vẫn đã được lưu): {exc}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def run_tamanh_scraping(
    request,
    db_config: dict,
    status: dict,
    log: Callable[[str], None],
    should_stop: Callable[[], bool],
) -> None:
    """Quét tuần tự toàn bộ chuyên khoa và phân trang trong khoảng năm."""
    if request.start_year > request.end_year:
        raise ValueError("Năm bắt đầu phải nhỏ hơn hoặc bằng năm kết thúc")
    if not is_tamanh_url(request.target_url):
        raise ValueError("Crawler Tâm Anh chỉ chấp nhận tên miền tamanhhospital.vn")

    status.update(
        {
            "source_type": "tamanh_qa",
            "departments_total": 0,
            "departments_completed": 0,
            "current_department": None,
            "pages": 0,
            "qas": 0,
            "files_created": 0,
            "total_urls": 0,
        }
    )
    session = _session()
    max_pages_cap = max(1, int(os.getenv("TAMANH_MAX_PAGES_PER_DEPARTMENT", "1000")))
    seen_details: set[str] = set()

    log(f"🏥 Nguồn Tâm Anh: {TAMANH_ROOT}")
    log(f"📅 Chỉ lưu Q&A có ngày đăng từ {request.start_year} đến {request.end_year}")
    log(f"📁 Câu hỏi BN: {BN_DIR}")
    log(f"📁 Trả lời BS: {BS_DIR}")

    root_response = session.get(TAMANH_ROOT, timeout=60, verify=False)
    root_response.raise_for_status()
    departments = _department_links(BeautifulSoup(root_response.text, "html.parser"))
    if not departments:
        raise RuntimeError("Không tìm thấy danh sách chuyên khoa trên trang Tâm Anh")
    status["departments_total"] = len(departments)
    log(f"✅ Tìm thấy {len(departments)} chuyên khoa; bắt đầu tự động quét lần lượt")

    for department_index, (department_url, department_name) in enumerate(departments, 1):
        if should_stop():
            break
        status["current_department"] = department_name
        log(f"\n=== KHOA {department_index}/{len(departments)}: {department_name} ===")
        page = 1
        max_page = 1
        previous_fingerprint = ""

        while page <= min(max_page, max_pages_cap) and not should_stop():
            page_url = department_url if page == 1 else urljoin(department_url, f"page/{page}/")
            status["current_url"] = page_url
            log(f"📄 Trang {page}: {page_url}")
            response = session.get(page_url, timeout=60, verify=False)
            if response.status_code == 404:
                break
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            links = _listing_links(soup, department_url)
            if page == 1:
                max_page = min(_max_page(soup, department_url), max_pages_cap)
                log(f"↳ Chuyên khoa có {max_page} trang")
            fingerprint = hashlib.sha1("\n".join(links).encode("utf-8")).hexdigest()
            if not links or (page > 1 and fingerprint == previous_fingerprint):
                break
            previous_fingerprint = fingerprint
            status["pages"] += 1
            page_years: list[int] = []

            for detail_url in links:
                if should_stop():
                    break
                if detail_url in seen_details:
                    continue
                seen_details.add(detail_url)
                status["total_urls"] += 1
                status["current_url"] = detail_url
                try:
                    detail_response = session.get(detail_url, timeout=60, verify=False)
                    detail_response.raise_for_status()
                    item = _parse_detail(detail_response.text, detail_url, department_name)
                    if not item or not item["question"] or not item["answer"]:
                        status["skipped"] += 1
                        log(f"⚠️ Không tìm thấy cấu trúc Q&A hợp lệ: {detail_url}")
                        continue
                    if item["year"] is None:
                        status["skipped"] += 1
                        log(f"⚠️ Bỏ qua vì không xác định được năm: {detail_url}")
                        continue
                    page_years.append(item["year"])
                    status["current_year"] = item["year"]
                    if not request.start_year <= item["year"] <= request.end_year:
                        continue

                    created, duplicate = _write_pair(item)
                    if duplicate:
                        status["duplicates"] += 1
                        log(f"🔁 Đã tồn tại: {item['title'][:80]}")
                    else:
                        status["success"] += 1
                        status["qas"] += 1
                        status["files_created"] += created
                        log(f"✅ Đã lưu {created} file BN/BS: {item['title'][:80]}")
                    time.sleep(random.uniform(0.25, 0.65))
                except Exception as exc:
                    status["skipped"] += 1
                    log(f"❌ Lỗi Q&A {detail_url}: {exc}")

            # Danh sách được sắp mới → cũ. Khi cả trang đã cũ hơn mốc, không cần sang trang sau.
            if page_years and max(page_years) < request.start_year:
                log(f"⏹ Trang đã cũ hơn năm {request.start_year}; chuyển sang chuyên khoa kế tiếp")
                break
            page += 1

        status["departments_completed"] += 1

    final_status = "paused" if should_stop() else ("error" if status.get("error") else "completed")
    status["summary"] = {
        "success": status["success"],
        "duplicates": status["duplicates"],
        "skipped": status["skipped"],
        "total_processed": status["total_urls"],
        "qas": status["qas"],
        "files_created": status["files_created"],
        "departments": status["departments_completed"],
        "pages": status["pages"],
    }
    _save_crawl_log(db_config, request, status, final_status, log)
    log(
        "\n=== HOÀN THÀNH TÂM ANH === "
        f"{status['departments_completed']}/{status['departments_total']} khoa | "
        f"{status['pages']} trang | {status['qas']} Q&A | {status['files_created']} file TXT"
    )

