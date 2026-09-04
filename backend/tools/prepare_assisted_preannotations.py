"""Tao/cached preannotation hybrid cho 70 development assignments theo doi trong."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

from core.research_ablation import GeminiGenerativeClient, run_document
from tools.run_ablation import db_config, file_sha256, persist_record


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--input-price-usd-per-million", type=float, required=True)
    parser.add_argument("--output-price-usd-per-million", type=float, required=True)
    parser.add_argument("--pricing-as-of", required=True)
    parser.add_argument("--pricing-source", required=True)
    args = parser.parse_args()

    conn = mysql.connector.connect(**db_config())
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT aa.id AS assignment_id,ad.article_id,a.abstract FROM annotation_assignments aa "
        "JOIN annotation_documents ad ON ad.id=aa.document_id "
        "JOIN articles a ON a.id=ad.article_id "
        "WHERE ad.project_id=%s AND ad.split_name='development' "
        "AND aa.assignment_role='annotator' AND aa.annotation_mode='assisted' "
        "AND aa.preannotation_json IS NULL AND aa.status='assigned' ORDER BY aa.id",
        (args.project_id,),
    )
    pending = cursor.fetchall()
    cursor.close()
    if not pending:
        conn.close()
        print(json.dumps({"project_id": args.project_id, "prepared": 0, "pending": 0}))
        return

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        conn.close()
        raise SystemExit("Thiếu GEMINI_API_KEY")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    dictionary_manifest = Path(__file__).resolve().parents[1] / "core" / "Tu Dien Y Hoc" / "manifest_v1.json"
    dictionary_hash = file_sha256(dictionary_manifest)
    client = GeminiGenerativeClient(api_key)
    prepared = 0
    for row in pending:
        article_id = int(row["article_id"])
        cache_path = args.cache_dir / f"development-preannotation-{article_id}.json"
        cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else None
        record = run_document(
            str(row["abstract"] or ""),
            "hybrid",
            article_id=article_id,
            repeat_index=0,
            model_name=args.model,
            temperature=args.temperature,
            raw_model_response=cached,
            client=client,
        )
        if cached is None:
            cache_path.write_text(
                json.dumps(
                    {
                        "text": record["raw_model_text"],
                        "latency_ms": record["stages_ms"]["ai_call"],
                        "input_tokens": record["input_tokens"],
                        "output_tokens": record["output_tokens"],
                        "model_version": record["model_version"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        record["stages_ms"]["storage"] = persist_record(
            conn,
            record,
            project_id=args.project_id,
            dictionary_sha256=dictionary_hash,
            input_price=args.input_price_usd_per_million,
            output_price=args.output_price_usd_per_million,
            pricing_as_of=args.pricing_as_of,
            pricing_source=args.pricing_source,
            phase="development_preannotation",
        )
        preannotation = [
            {
                "client_id": None,
                "start": entity["start"],
                "end": entity["end"],
                "surface": entity["surface"],
                "type": entity["type"],
                "code": entity.get("code", ""),
                "source": entity.get("source", "ai"),
                "decision": "proposed",
                "reason": "",
                "version": 1,
            }
            for entity in record["entities"]
        ]
        update = conn.cursor()
        update.execute(
            "UPDATE annotation_assignments SET preannotation_json=%s WHERE id=%s "
            "AND preannotation_json IS NULL AND status='assigned'",
            (json.dumps(preannotation, ensure_ascii=False), row["assignment_id"]),
        )
        if update.rowcount != 1:
            conn.rollback()
            update.close()
            raise RuntimeError(f"Assignment {row['assignment_id']} đã thay đổi đồng thời")
        conn.commit()
        update.close()
        prepared += 1
        print(json.dumps({"prepared": prepared, "total": len(pending), "article_id": article_id}))
    conn.close()
    print(json.dumps({"project_id": args.project_id, "prepared": prepared, "pending": 0}))


if __name__ == "__main__":
    main()
