"""Chay/cached bon cau hinh ablation tren split test cua manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time
from pathlib import Path

import mysql.connector
from google import genai
from dotenv import load_dotenv

from core.research_ablation import GeminiGenerativeClient, run_document


def db_config() -> dict:
    return {
        "user": os.getenv("DB_USER", "root"),
        "password": os.environ["DB_PASSWORD"],
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "database": os.getenv("DB_NAME", "yhoc_corpus"),
        "charset": "utf8mb4",
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def estimated_cost(record: dict, input_price: float, output_price: float) -> float:
    return (
        int(record.get("input_tokens", 0)) * input_price
        + int(record.get("output_tokens", 0)) * output_price
    ) / 1_000_000


def persist_record(
    conn,
    record: dict,
    *,
    project_id: int,
    dictionary_sha256: str,
    input_price: float,
    output_price: float,
    pricing_as_of: str,
    pricing_source: str,
    phase: str = "test_benchmark",
) -> int:
    started = time.perf_counter()
    cursor = conn.cursor()
    try:
        metadata = {
            "phase": phase,
            "pricing_as_of": pricing_as_of,
            "pricing_source": pricing_source,
            "raw_model_sha256": hashlib.sha256(
                str(record.get("raw_model_text") or "").encode("utf-8")
            ).hexdigest(),
        }
        cursor.execute(
            "INSERT INTO ai_experiment_runs(project_id,article_id,run_uuid,configuration,"
            "repeat_index,model_name,model_version,prompt_sha256,dictionary_sha256,temperature,"
            "input_tokens,output_tokens,latency_ms,estimated_cost_usd,status,metadata_json,completed_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'completed',%s,CURRENT_TIMESTAMP)",
            (
                project_id,
                record["article_id"],
                record["run_uuid"],
                record["configuration"],
                record["repeat_index"],
                record["model_name"],
                record.get("model_version", ""),
                record.get("prompt_sha256", ""),
                dictionary_sha256,
                record.get("temperature", 0),
                record.get("input_tokens", 0),
                record.get("output_tokens", 0),
                record.get("stages_ms", {}).get("ai_call", 0),
                estimated_cost(record, input_price, output_price),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        run_id = int(cursor.lastrowid)
        for event in record.get("events", []):
            cursor.execute(
                "INSERT INTO ai_candidate_events(run_id,article_id,candidate_index,term_text,"
                "proposed_start,proposed_end,proposed_type,proposed_code,outcome,reason,final_entity_json) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    run_id,
                    record["article_id"],
                    event.get("candidate_index", 0),
                    event.get("term", ""),
                    event.get("proposed_start") if isinstance(event.get("proposed_start"), int) else None,
                    event.get("proposed_end") if isinstance(event.get("proposed_end"), int) else None,
                    event.get("proposed_type", ""),
                    event.get("proposed_code", ""),
                    event.get("outcome", "invalid_schema"),
                    event.get("reason", ""),
                    json.dumps(event.get("entity"), ensure_ascii=False) if event.get("entity") else None,
                ),
            )
        for stage, duration in record.get("stages_ms", {}).items():
            if stage in {"storage", "validation_merge"}:
                continue
            cursor.execute(
                "INSERT INTO benchmark_stage_metrics(run_id,article_id,stage_name,duration_ms) "
                "VALUES(%s,%s,%s,%s)",
                (run_id, record["article_id"], stage, max(0, int(duration))),
            )
        conn.commit()
        storage_ms = round((time.perf_counter() - started) * 1000)
        cursor.execute(
            "INSERT INTO benchmark_stage_metrics(run_id,article_id,stage_name,duration_ms) "
            "VALUES(%s,%s,'storage',%s)",
            (run_id, record["article_id"], storage_ms),
        )
        conn.commit()
        return storage_ms
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def environment_snapshot() -> dict:
    try:
        import psutil

        ram_bytes = int(psutil.virtual_memory().total)
    except (ImportError, AttributeError):
        ram_bytes = 0
    return {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "ram_bytes": ram_bytes,
        "gemini_sdk": getattr(genai, "__version__", "unknown"),
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--persist-db", action="store_true")
    parser.add_argument("--input-price-usd-per-million", type=float, required=True)
    parser.add_argument("--output-price-usd-per-million", type=float, required=True)
    parser.add_argument("--pricing-as-of", required=True, help="YYYY-MM-DD")
    parser.add_argument("--pricing-source", required=True)
    args = parser.parse_args()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("Thiếu GEMINI_API_KEY")
    if args.persist_db and not args.project_id:
        raise SystemExit("--persist-db yêu cầu --project-id")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    test_ids = [int(item["article_id"]) for item in manifest["documents"] if item["split"] == "test"]
    development_ids = [
        int(item["article_id"])
        for item in manifest["documents"]
        if item["split"] == "development"
    ]
    if len(test_ids) != 200:
        raise SystemExit(f"Manifest phải có đúng 200 test, hiện có {len(test_ids)}")
    conn = mysql.connector.connect(**db_config())
    cursor = conn.cursor(dictionary=True)
    required_ids = list(dict.fromkeys(test_ids + development_ids[: max(0, args.warmup)]))
    placeholders = ",".join(["%s"] * len(required_ids))
    cursor.execute(f"SELECT id,abstract FROM articles WHERE id IN ({placeholders})", required_ids)
    texts = {int(row["id"]): str(row["abstract"] or "") for row in cursor.fetchall()}
    cursor.close()
    if any(article_id not in texts for article_id in test_ids):
        raise SystemExit("Không tải đủ 200 văn bản test từ cơ sở dữ liệu")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    environment = environment_snapshot()
    environment.update(
        {
            "model": args.model,
            "temperature": args.temperature,
            "warmup_documents_per_configuration": args.warmup,
            "pricing_as_of": args.pricing_as_of,
            "pricing_source": args.pricing_source,
        }
    )
    (args.output_dir / "environment.json").write_text(
        json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    dictionary_manifest = Path(__file__).resolve().parents[1] / "core" / "Tu Dien Y Hoc" / "manifest_v1.json"
    dictionary_hash = file_sha256(dictionary_manifest)
    client = GeminiGenerativeClient(api_key)
    for configuration in ("dictionary", "ai_raw", "ai_constrained", "hybrid"):
        repeat_count = 1 if configuration == "dictionary" else args.repeats
        for repeat in range(repeat_count):
            for article_id in development_ids[: max(0, args.warmup)]:
                run_document(
                    texts[article_id],
                    configuration,
                    article_id=article_id,
                    repeat_index=repeat,
                    model_name=args.model,
                    temperature=args.temperature,
                    client=client,
                )
            output_path = args.output_dir / f"{configuration}-repeat-{repeat}.jsonl"
            with output_path.open("w", encoding="utf-8") as output:
                for article_id in test_ids:
                    cached = None
                    if configuration != "dictionary":
                        cache_path = args.cache_dir / f"{configuration}-{repeat}-{article_id}.json"
                        if cache_path.exists():
                            cached = json.loads(cache_path.read_text(encoding="utf-8"))
                    record = run_document(
                        texts[article_id],
                        configuration,
                        article_id=article_id,
                        repeat_index=repeat,
                        model_name=args.model,
                        temperature=args.temperature,
                        raw_model_response=cached,
                        client=client,
                    )
                    if configuration != "dictionary" and cached is None:
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
                    if args.persist_db:
                        record["stages_ms"]["storage"] = persist_record(
                            conn,
                            record,
                            project_id=args.project_id,
                            dictionary_sha256=dictionary_hash,
                            input_price=args.input_price_usd_per_million,
                            output_price=args.output_price_usd_per_million,
                            pricing_as_of=args.pricing_as_of,
                            pricing_source=args.pricing_source,
                        )
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps({"configuration": configuration, "repeat": repeat, "output": str(output_path)}, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
