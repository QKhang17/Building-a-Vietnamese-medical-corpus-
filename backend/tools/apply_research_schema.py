"""Ap dung migration benchmark len database hien tai; chi tao bang moi."""

from __future__ import annotations

import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    config = {
        "user": os.getenv("DB_USER", "root"),
        "password": os.environ["DB_PASSWORD"],
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "database": os.getenv("DB_NAME", "yhoc_corpus"),
        "charset": "utf8mb4",
    }
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "annotation_research.sql"
    sql_without_comments = "\n".join(
        line
        for line in sql_path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    statements = [
        part.strip()
        for part in sql_without_comments.split(";")
        if part.strip()
    ]
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    try:
        for statement in statements:
            cursor.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    print(f"Applied {len(statements)} statements from {sql_path}")


if __name__ == "__main__":
    main()
