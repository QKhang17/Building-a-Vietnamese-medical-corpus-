"""Tao project 300 tom tat, assignment 2+1 va manifest tai lap.

Vi du (chay tu thu muc backend):
    python -m tools.sample_annotation_project --name mednlp-gold-v1 --output ../output/gold-v1-manifest.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

from core.research_sampling import (
    DEFAULT_SEED,
    assignment_modes,
    prepare_candidates,
    split_sample,
    stratified_sample,
)


def _db_config() -> dict:
    load_dotenv()
    return {
        "user": os.getenv("DB_USER", "root"),
        "password": os.environ["DB_PASSWORD"],
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "database": os.getenv("DB_NAME", "yhoc_corpus"),
        "charset": "utf8mb4",
    }


def _flatten_preannotation(result: object) -> list[dict]:
    entities: list[dict] = []
    if not isinstance(result, dict):
        return entities
    for category, items in result.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for span in item.get("spans", []):
                if not isinstance(span, dict):
                    continue
                entities.append(
                    {
                        "client_id": None,
                        "start": span.get("start"),
                        "end": span.get("end"),
                        "surface": item.get("term", ""),
                        "type": category,
                        "code": item.get("code", ""),
                        "source": item.get("source", "ai"),
                        "decision": "proposed",
                        "reason": "",
                        "version": 1,
                    }
                )
    return entities


def create_project(args: argparse.Namespace) -> dict:
    conn = mysql.connector.connect(**_db_config())
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id,title,abstract,publication_year,source_url FROM articles "
            "WHERE abstract IS NOT NULL AND TRIM(abstract)!=''"
        )
        candidates = prepare_candidates(cursor.fetchall(), min_chars=args.min_chars)
        selected = split_sample(stratified_sample(candidates, 300, args.seed), args.seed)
        plan = assignment_modes(selected, args.annotator_a, args.annotator_b, args.adjudicator)

        config = {
            "sample_size": 300,
            "splits": {"pilot": 30, "development": 70, "test": 200},
            "stratification": ["source_domain", "publication_year", "length_tertile"],
            "min_chars": args.min_chars,
            "annotators": [args.annotator_a, args.annotator_b],
            "adjudicator": args.adjudicator,
        }
        cursor.execute(
            "INSERT INTO annotation_projects(name,random_seed,annotation_unit,schema_version,"
            "guideline_version,status,config_json) VALUES(%s,%s,'abstract',%s,%s,'active',%s)",
            (args.name, args.seed, args.schema_version, args.guideline_version, json.dumps(config)),
        )
        project_id = int(cursor.lastrowid)

        document_ids: dict[int, int] = {}
        for row in selected:
            cursor.execute(
                "INSERT INTO annotation_documents(project_id,article_id,split_name,stratum_key,"
                "text_sha256,char_count,source_domain,publication_year) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    project_id,
                    row["id"],
                    row["split_name"],
                    row["stratum_key"],
                    row["text_sha256"],
                    row["char_count"],
                    row["source_domain"],
                    row["publication_year"],
                ),
            )
            document_ids[int(row["id"])] = int(cursor.lastrowid)

        cursor.execute(
            "SELECT ar.article_id,ar.result_json FROM ai_label_results ar JOIN "
            "(SELECT article_id,MAX(id) AS max_id FROM ai_label_results GROUP BY article_id) latest "
            "ON latest.max_id=ar.id"
        )
        preannotations = {
            int(row["article_id"]): _flatten_preannotation(
                row["result_json"] if isinstance(row["result_json"], dict) else json.loads(row["result_json"])
            )
            for row in cursor.fetchall()
        }
        assisted_missing = 0
        for assignment in plan:
            preannotation = None
            if assignment["mode"] == "assisted":
                preannotation = preannotations.get(assignment["article_id"])
                if not preannotation:
                    assisted_missing += 1
                    preannotation = None
            cursor.execute(
                "INSERT INTO annotation_assignments(document_id,expert_id,assignment_role,"
                "annotation_mode,preannotation_json) VALUES(%s,%s,%s,%s,%s)",
                (
                    document_ids[assignment["article_id"]],
                    assignment["expert_id"],
                    assignment["role"],
                    assignment["mode"],
                    json.dumps(preannotation, ensure_ascii=False) if preannotation is not None else None,
                ),
            )
        conn.commit()

        manifest = {
            "project_id": project_id,
            "name": args.name,
            "seed": args.seed,
            "schema_version": args.schema_version,
            "guideline_version": args.guideline_version,
            "config": config,
            "assisted_assignments_without_preannotation": assisted_missing,
            "documents": [
                {
                    "article_id": int(row["id"]),
                    "split": row["split_name"],
                    "stratum": row["stratum_key"],
                    "text_sha256": row["text_sha256"],
                    "char_count": row["char_count"],
                    "source_domain": row["source_domain"],
                    "publication_year": row["publication_year"],
                    "source_url": row.get("source_url", ""),
                }
                for row in selected
            ],
        }
        return manifest
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="mednlp-gold-v1")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-chars", type=int, default=80)
    parser.add_argument("--annotator-a", type=int, default=1)
    parser.add_argument("--annotator-b", type=int, default=2)
    parser.add_argument("--adjudicator", type=int, default=3)
    parser.add_argument("--schema-version", default="mednlp-6types-v1")
    parser.add_argument("--guideline-version", default="guideline-v1.0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = create_project(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"project_id": manifest["project_id"], "output": str(args.output), "documents": 300}, ensure_ascii=False))


if __name__ == "__main__":
    main()
