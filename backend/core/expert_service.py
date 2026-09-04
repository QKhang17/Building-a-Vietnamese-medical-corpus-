"""Xác thực và lưu đánh giá chuyên gia trong MySQL của MedNLP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import contextmanager

import mysql.connector


class ExpertDatabaseNotConfigured(RuntimeError):
    pass


_db_config: dict = {}


def configure(db_config: dict) -> None:
    """Dùng chung kết nối MySQL với các API bài viết hiện tại."""
    global _db_config
    _db_config = dict(db_config)


def is_configured() -> bool:
    return bool(_db_config)


@contextmanager
def mysql_connection():
    if not _db_config:
        raise ExpertDatabaseNotConfigured("Chưa khởi tạo cấu hình MySQL cho cổng chuyên gia")
    conn = mysql.connector.connect(**_db_config)
    try:
        yield conn
    finally:
        conn.close()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, algorithm, rounds, salt, expected = encoded.split("$", 4)
        if algorithm != "pbkdf2-sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(actual, _b64decode(expected))
    except (TypeError, ValueError):
        return False


def _token_secret() -> bytes:
    secret = os.environ.get("EXPERT_TOKEN_SECRET", "")
    if len(secret) < 32:
        raise ValueError("EXPERT_TOKEN_SECRET must contain at least 32 characters")
    return secret.encode("utf-8")


def create_token(expert: dict, lifetime_seconds: int = 8 * 60 * 60) -> str:
    payload = {
        "sub": int(expert["id"]),
        "username": expert["username"],
        "full_name": expert["full_name"],
        "role": "expert",
        "exp": int(time.time()) + lifetime_seconds,
    }
    body = _b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def decode_token(token: str) -> dict:
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            raise ValueError("Chữ ký token không hợp lệ")
        payload = json.loads(_b64decode(body).decode("utf-8"))
        if payload.get("role") != "expert" or int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Phiên đăng nhập đã hết hạn")
        return payload
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Token đăng nhập không hợp lệ") from exc


def _decode_json_fields(row: dict | None, *fields: str) -> dict | None:
    if not row:
        return row
    for field in fields:
        value = row.get(field)
        if isinstance(value, (str, bytes, bytearray)):
            try:
                row[field] = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                row[field] = [] if field == "corrected_entities" else {}
    return row


def login(username: str, password: str) -> tuple[dict, str]:
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT id,username,full_name,password_hash,is_active "
                "FROM mednlp_experts WHERE username=%s",
                (username.strip(),),
            )
            expert = cursor.fetchone()
            if not expert or not expert["is_active"] or not verify_password(password, expert["password_hash"]):
                raise ValueError("Tên đăng nhập hoặc mật khẩu không đúng")
            cursor.execute(
                "UPDATE mednlp_experts SET last_login_at=CURRENT_TIMESTAMP WHERE id=%s",
                (expert["id"],),
            )
            conn.commit()
        except mysql.connector.Error as exc:
            if exc.errno == 1146:
                raise ExpertDatabaseNotConfigured(
                    "Chưa có bảng chuyên gia. Hãy chạy file mysql.txt trong MySQL Workbench."
                ) from exc
            raise
        finally:
            cursor.close()
    public = {key: expert[key] for key in ("id", "username", "full_name")}
    return public, create_token(expert)


def save_ai_result(article_id: int, result: dict) -> bool:
    if not is_configured():
        return False
    with mysql_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO ai_label_results(article_id,result_json,model_name) VALUES(%s,%s,%s)",
                (article_id, json.dumps(result, ensure_ascii=False), result.get("model", "Gemini")),
            )
            conn.commit()
        except mysql.connector.Error as exc:
            if exc.errno == 1146:
                return False
            raise
        finally:
            cursor.close()
    return True


def _in_clause(values: list[int]) -> tuple[str, list[int]]:
    return ",".join(["%s"] * len(values)), list(values)


def get_ai_results(article_ids: list[int]) -> dict[int, dict]:
    if not article_ids:
        return {}
    placeholders, params = _in_clause(article_ids)
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT a.article_id,a.result_json,a.created_at FROM ai_label_results a "
                "JOIN (SELECT article_id,MAX(id) AS max_id FROM ai_label_results "
                f"WHERE article_id IN ({placeholders}) GROUP BY article_id) latest "
                "ON latest.max_id=a.id",
                params,
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
    return {
        int(row["article_id"]): _decode_json_fields(row, "result_json")
        for row in rows
    }


REVIEW_TABLES = {
    "manual": "expert_manual_reviews",
    "ai": "expert_ai_reviews",
}


def get_reviews(article_ids: list[int], expert_id: int | None = None) -> dict[int, list[dict]]:
    if not article_ids:
        return {}
    placeholders, article_params = _in_clause(article_ids)
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            rows = []
            for label_source, table_name in REVIEW_TABLES.items():
                params = list(article_params)
                query = (
                    "SELECT r.id,r.article_id,r.expert_id,e.full_name AS expert_name,"
                    f"r.note,r.created_at,r.updated_at,'{label_source}' AS label_source "
                    f"FROM {table_name} r JOIN mednlp_experts e ON e.id=r.expert_id "
                    f"WHERE r.article_id IN ({placeholders})"
                )
                if expert_id is not None:
                    query += " AND r.expert_id=%s"
                    params.append(expert_id)
                cursor.execute(query, params)
                rows.extend(cursor.fetchall())
        finally:
            cursor.close()
    rows.sort(key=lambda row: row.get("updated_at") or row.get("created_at"), reverse=True)
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["article_id"]), []).append(row)
    return grouped


def upsert_review(
    expert_id: int,
    article_id: int,
    note: str,
    label_source: str,
) -> dict:
    table_name = REVIEW_TABLES.get(label_source)
    if not table_name:
        raise ValueError("Nguồn nhãn phải là manual hoặc ai")
    with mysql_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                f"INSERT INTO {table_name}(expert_id,article_id,note) "
                "VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE "
                "id=LAST_INSERT_ID(id),note=VALUES(note),updated_at=CURRENT_TIMESTAMP",
                (expert_id, article_id, note.strip()),
            )
            review_id = cursor.lastrowid
            conn.commit()
            cursor.execute(
                "SELECT id,article_id,expert_id,note,created_at,updated_at "
                f"FROM {table_name} WHERE id=%s",
                (review_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()
    row["label_source"] = label_source
    return row
