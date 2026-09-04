"""Xuat corpus vang, annotation doc lap va bang nghien cuu con nguoi tu MySQL."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

from core.annotation_service import ENTITY_TYPES, snapshot_sha256
from core.research_metrics import score_corpus
from core.research_reporting import descriptive, summarize_human_assignments


def db_config() -> dict:
    return {
        "user": os.getenv("DB_USER", "root"),
        "password": os.environ["DB_PASSWORD"],
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "database": os.getenv("DB_NAME", "yhoc_corpus"),
        "charset": "utf8mb4",
    }


def json_value(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--include-text",
        action="store_true",
        help="Chỉ bật khi giấy phép cho phép phân phối nguyên văn tóm tắt.",
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    conn = mysql.connector.connect(**db_config())
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM annotation_projects WHERE id=%s", (args.project_id,))
    project = cursor.fetchone()
    if not project:
        raise SystemExit("Không tìm thấy project")
    project["config_json"] = json_value(project.get("config_json"), {})
    cursor.execute(
        "SELECT ad.*,a.title,a.abstract,a.source_url,gs.entities_json,gs.snapshot_sha256,"
        "gs.lock_source,gs.locked_at FROM annotation_documents ad "
        "JOIN articles a ON a.id=ad.article_id "
        "LEFT JOIN annotation_gold_snapshots gs ON gs.document_id=ad.id "
        "WHERE ad.project_id=%s ORDER BY FIELD(ad.split_name,'pilot','development','test'),ad.id",
        (args.project_id,),
    )
    documents = cursor.fetchall()
    if not args.allow_incomplete and len(documents) != 300:
        raise SystemExit(f"Project phải có 300 tài liệu, hiện có {len(documents)}")
    locked = [row for row in documents if row.get("entities_json") is not None]
    if not args.allow_incomplete and len(locked) != 300:
        raise SystemExit(f"Chưa đủ 300 bản vàng bất biến, hiện có {len(locked)}")

    cursor.execute(
        "SELECT aa.*,ad.project_id,ad.split_name FROM annotation_assignments aa "
        "JOIN annotation_documents ad ON ad.id=aa.document_id "
        "WHERE ad.project_id=%s ORDER BY aa.document_id,aa.id",
        (args.project_id,),
    )
    assignments = cursor.fetchall()
    for assignment in assignments:
        assignment["preannotation_ready"] = assignment.get("preannotation_json") is not None
        cursor.execute(
            "SELECT start_offset AS start,end_offset AS end,surface,entity_type AS type,"
            "concept_code AS code,entity_source AS source,decision,reason,version "
            "FROM annotation_entities WHERE assignment_id=%s ORDER BY start_offset,end_offset,entity_type",
            (assignment["id"],),
        )
        assignment["entities"] = cursor.fetchall()
        assignment.pop("preannotation_json", None)
    cursor.close()
    conn.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    gold_rows = []
    gold_by_document = {}
    entity_distribution = Counter()
    for row in locked:
        entities = json_value(row["entities_json"], [])
        actual_digest = snapshot_sha256(entities)
        if actual_digest != row["snapshot_sha256"]:
            raise SystemExit(f"Checksum snapshot sai ở document {row['id']}")
        gold_by_document[row["id"]] = entities
        entity_distribution.update(item["type"] for item in entities if item.get("decision") != "rejected")
        exported = {
            "document_id": row["id"],
            "article_id": row["article_id"],
            "split": row["split_name"],
            "title": row["title"],
            "source_url": row["source_url"],
            "text_sha256": row["text_sha256"],
            "snapshot_sha256": row["snapshot_sha256"],
            "lock_source": row["lock_source"],
            "entities": entities,
        }
        if args.include_text:
            exported["abstract"] = row["abstract"]
        gold_rows.append(exported)
    write_jsonl(args.output_dir / "gold.jsonl", gold_rows)

    annotation_rows = [
        {
            "assignment_id": row["id"],
            "document_id": row["document_id"],
            "expert_id": row["expert_id"],
            "assignment_role": row["assignment_role"],
            "annotation_mode": row["annotation_mode"],
            "status": row["status"],
            "active_seconds": row["active_seconds"],
            "entities_version": row["entities_version"],
            "preannotation_ready": row["preannotation_ready"],
            "entities": row["entities"],
        }
        for row in assignments
    ]
    write_jsonl(args.output_dir / "independent_annotations.jsonl", annotation_rows)

    pairs = defaultdict(list)
    for row in assignments:
        if row["assignment_role"] == "annotator" and row["status"] in {
            "submitted",
            "conflict",
            "adjudicated",
            "locked",
        }:
            pairs[(row["split_name"], row["document_id"])].append(row["entities"])
    agreement = {}
    for split in ("pilot", "development", "test"):
        left = {}
        right = {}
        for (pair_split, document_id), versions in pairs.items():
            if pair_split == split and len(versions) == 2:
                left[document_id], right[document_id] = versions
        agreement[split] = score_corpus(left, right) if left else None

    development_assignments = [
        row for row in assignments if row["split_name"] == "development"
    ]
    human_study = summarize_human_assignments(development_assignments, gold_by_document)
    report = {
        "project": project,
        "completeness": {
            "documents": len(documents),
            "gold_snapshots": len(locked),
            "split_counts": dict(Counter(row["split_name"] for row in documents)),
            "lock_sources": dict(Counter(row.get("lock_source") for row in locked)),
            "assisted_preannotation_ready": sum(
                row["preannotation_ready"]
                for row in assignments
                if row["annotation_mode"] == "assisted"
            ),
            "assisted_assignments": sum(
                row["annotation_mode"] == "assisted" for row in assignments
            ),
        },
        "corpus": {
            "character_count": descriptive(row["char_count"] for row in documents),
            "source_domains": dict(Counter(row["source_domain"] for row in documents)),
            "publication_years": dict(Counter(str(row["publication_year"]) for row in documents)),
            "entity_distribution": {
                entity_type: entity_distribution.get(entity_type, 0) for entity_type in ENTITY_TYPES
            },
        },
        "pre_adjudication_agreement": agreement,
        "human_study_development": human_study,
        "text_distribution": (
            "included_by_explicit_flag" if args.include_text else "metadata_offsets_only_license_safe"
        ),
    }
    (args.output_dir / "corpus_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    write_csv(
        args.output_dir / "table_corpus_characteristics.csv",
        [
            {
                "documents": len(documents),
                "pilot": sum(row["split_name"] == "pilot" for row in documents),
                "development": sum(row["split_name"] == "development" for row in documents),
                "test": sum(row["split_name"] == "test" for row in documents),
                "characters_mean": report["corpus"]["character_count"]["mean"],
                "characters_median": report["corpus"]["character_count"]["median"],
                "characters_p95": report["corpus"]["character_count"]["p95"],
                "source_domains": len(report["corpus"]["source_domains"]),
            }
        ],
    )
    write_csv(
        args.output_dir / "table_entity_distribution.csv",
        [
            {"entity_type": entity_type, "count": entity_distribution.get(entity_type, 0)}
            for entity_type in ENTITY_TYPES
        ],
    )
    agreement_rows = []
    for split, value in agreement.items():
        if not value:
            continue
        agreement_rows.append({"split": split, "entity_type": "micro", **value["micro"]})
        for entity_type in ENTITY_TYPES:
            agreement_rows.append(
                {"split": split, "entity_type": entity_type, **value["per_type"][entity_type]}
            )
    write_csv(args.output_dir / "table_agreement.csv", agreement_rows)
    human_rows = []
    for mode, value in human_study.items():
        decisions = value["decisions"]
        quality = value["quality_against_gold"]["micro"] if value["quality_against_gold"] else {}
        human_rows.append(
            {
                "annotation_mode": mode,
                "assignments": value["assignments"],
                "active_seconds_mean": value["active_seconds"]["mean"],
                "active_seconds_median": value["active_seconds"]["median"],
                "active_seconds_p95": value["active_seconds"]["p95"],
                "accepted": decisions.get("accepted", 0),
                "modified": decisions.get("modified", 0),
                "rejected": decisions.get("rejected", 0),
                "added": decisions.get("added", 0),
                "micro_precision_vs_gold": quality.get("precision"),
                "micro_recall_vs_gold": quality.get("recall"),
                "micro_f1_vs_gold": quality.get("f1"),
            }
        )
    write_csv(args.output_dir / "table_human_study.csv", human_rows)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "documents": len(documents),
                "gold_snapshots": len(locked),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
