"""Sinh report JSON va cac bang CSV truy nguoc duoc tu gold + raw ablation trace."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

from core.annotation_service import ENTITY_TYPES
from core.research_metrics import (
    bootstrap_micro_ci,
    paired_bootstrap_f1_difference,
    score_corpus,
)
from core.research_reporting import (
    dictionary_coverage,
    summarize_candidate_outcomes,
    summarize_cost,
    summarize_stage_timings,
)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prediction_maps(
    records: list[dict],
) -> tuple[dict[str, list[dict]], dict[str, int], dict[str, dict[str, int]]]:
    predictions = {str(row["article_id"]): row.get("entities", []) for row in records}
    extras = {
        str(row["article_id"]): int(row.get("extra_false_positives", 0)) for row in records
    }
    extras_by_type = {
        str(row["article_id"]): {
            str(entity_type): int(count)
            for entity_type, count in row.get("extra_false_positives_by_type", {}).items()
        }
        for row in records
    }
    return predictions, extras, extras_by_type


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--input-price-usd-per-million", type=float, required=True)
    parser.add_argument("--output-price-usd-per-million", type=float, required=True)
    parser.add_argument("--pricing-as-of", required=True)
    parser.add_argument("--pricing-source", required=True)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    gold_rows = read_jsonl(args.gold)
    gold = {str(row["article_id"]): row.get("entities", []) for row in gold_rows}
    test_ids = {str(row["article_id"]) for row in gold_rows if row.get("split", "test") == "test"}
    if test_ids:
        gold = {article_id: gold[article_id] for article_id in test_ids}
    if not args.allow_incomplete and len(gold) != 200:
        raise SystemExit(f"Gold test phải có 200 tài liệu, hiện có {len(gold)}")

    files = sorted(args.results_dir.glob("*-repeat-*.jsonl"))
    if not files:
        raise SystemExit("Không tìm thấy raw ablation JSONL")
    groups: dict[tuple[str, int], dict] = {}
    ablation_rows = []
    per_type_rows = []
    rejection_rows = []
    latency_cost_rows = []
    confusion_rows = []

    for path in files:
        records = read_jsonl(path)
        if not records:
            continue
        configuration = str(records[0]["configuration"])
        repeat = int(records[0].get("repeat_index", 0))
        predictions, extras, extras_by_type = prediction_maps(records)
        if not args.allow_incomplete and set(predictions) != set(gold):
            raise SystemExit(f"{path.name} không khớp đúng 200 ID test")
        metrics = score_corpus(
            gold,
            predictions,
            extra_false_positives=extras,
            extra_false_positives_by_type=extras_by_type,
        )
        ci = bootstrap_micro_ci(
            gold,
            predictions,
            extra_false_positives=extras,
            extra_false_positives_by_type=extras_by_type,
            iterations=args.bootstrap,
            seed=args.seed,
        )
        outcome = summarize_candidate_outcomes(records)
        timing = summarize_stage_timings(records)
        cost = summarize_cost(
            records,
            input_usd_per_million=args.input_price_usd_per_million,
            output_usd_per_million=args.output_price_usd_per_million,
        )
        groups[(configuration, repeat)] = {
            "records": records,
            "predictions": predictions,
            "extras": extras,
            "extras_by_type": extras_by_type,
            "metrics": metrics,
            "bootstrap_95": ci,
            "candidate_outcomes": outcome,
            "stage_timings_ms": timing,
            "cost": cost,
            "source_file": str(path),
        }
        ablation_rows.append(
            {
                "configuration": configuration,
                "repeat": repeat,
                "documents": metrics["documents"],
                "micro_precision": metrics["micro"]["precision"],
                "micro_recall": metrics["micro"]["recall"],
                "micro_f1": metrics["micro"]["f1"],
                "f1_ci95_low": ci["f1"]["lower_95"],
                "f1_ci95_high": ci["f1"]["upper_95"],
                "macro_f1": metrics["macro"]["f1"],
                "relaxed_f1": metrics["relaxed_overlap"]["f1"],
                "code_accuracy": metrics["code_accuracy"]["accuracy"],
            }
        )
        for entity_type in ENTITY_TYPES:
            row = metrics["per_type"][entity_type]
            per_type_rows.append(
                {
                    "configuration": configuration,
                    "repeat": repeat,
                    "entity_type": entity_type,
                    **row,
                }
            )
            for predicted_type, count in metrics["type_confusion"][entity_type].items():
                confusion_rows.append(
                    {
                        "configuration": configuration,
                        "repeat": repeat,
                        "gold_type": entity_type,
                        "predicted_type": predicted_type,
                        "count": count,
                    }
                )
        for event_name, event_row in outcome["by_outcome"].items():
            rejection_rows.append(
                {
                    "configuration": configuration,
                    "repeat": repeat,
                    "outcome": event_name,
                    "count": event_row["count"],
                    "candidate_denominator": outcome["candidate_events"],
                    "rate": event_row["rate_of_all_candidates"],
                }
            )
        for stage, values in timing.items():
            latency_cost_rows.append(
                {
                    "configuration": configuration,
                    "repeat": repeat,
                    "stage": stage,
                    "n": values["n"],
                    "mean_ms": values["mean"],
                    "median_ms": values["median"],
                    "p95_ms": values["p95"],
                    "input_tokens_total": cost["input_tokens"] if stage == "total" else "",
                    "output_tokens_total": cost["output_tokens"] if stage == "total" else "",
                    "cost_per_document_usd": cost["cost_per_document_usd"] if stage == "total" else "",
                    "cost_per_1000_documents_usd": cost["cost_per_1000_documents_usd"] if stage == "total" else "",
                }
            )

    dictionary_group = groups.get(("dictionary", 0))
    if not dictionary_group:
        raise SystemExit("Thiếu cấu hình dictionary-repeat-0")
    expected_runs = {
        ("dictionary", 0),
        ("ai_raw", 0),
        ("ai_raw", 1),
        ("ai_raw", 2),
        ("ai_constrained", 0),
        ("ai_constrained", 1),
        ("ai_constrained", 2),
        ("hybrid", 0),
        ("hybrid", 1),
        ("hybrid", 2),
    }
    if not args.allow_incomplete and set(groups) != expected_runs:
        missing = sorted(expected_runs - set(groups))
        extra = sorted(set(groups) - expected_runs)
        raise SystemExit(f"Bộ run chưa đúng 1+3+3+3; thiếu={missing}, thừa={extra}")
    coverage = dictionary_coverage(gold, dictionary_group["predictions"])
    dictionary_rows = [{"entity_type": key, **value} for key, value in coverage.items()]

    comparisons = {}
    comparison_pairs = []
    for repeat in range(3):
        comparison_pairs.extend(
            [
                (("ai_raw", repeat), ("ai_constrained", repeat)),
                (("ai_constrained", repeat), ("hybrid", repeat)),
                (("dictionary", 0), ("hybrid", repeat)),
            ]
        )
    for left_key, right_key in comparison_pairs:
        if left_key not in groups or right_key not in groups:
            continue
        left = groups[left_key]
        right = groups[right_key]
        label = f"{left_key[0]}-r{left_key[1]}__to__{right_key[0]}-r{right_key[1]}"
        comparisons[label] = paired_bootstrap_f1_difference(
            gold,
            left["predictions"],
            right["predictions"],
            left_extra_false_positives=left["extras"],
            right_extra_false_positives=right["extras"],
            left_extra_false_positives_by_type=left["extras_by_type"],
            right_extra_false_positives_by_type=right["extras_by_type"],
            iterations=args.bootstrap,
            seed=args.seed,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    by_configuration = defaultdict(list)
    for row in ablation_rows:
        by_configuration[row["configuration"]].append(row)
    aggregate_rows = []
    for configuration, rows in by_configuration.items():
        aggregate = {"configuration": configuration, "repeats": len(rows)}
        for metric in (
            "micro_precision",
            "micro_recall",
            "micro_f1",
            "macro_f1",
            "relaxed_f1",
        ):
            values = [float(row[metric]) for row in rows]
            aggregate[f"{metric}_mean"] = mean(values)
            aggregate[f"{metric}_sd"] = pstdev(values) if len(values) > 1 else 0.0
        aggregate_rows.append(aggregate)
    write_csv(args.output_dir / "table_ablation.csv", ablation_rows)
    write_csv(args.output_dir / "table_ablation_aggregate.csv", aggregate_rows)
    write_csv(args.output_dir / "table_metrics_by_type.csv", per_type_rows)
    write_csv(args.output_dir / "table_candidate_rejections.csv", rejection_rows)
    write_csv(args.output_dir / "table_latency_cost.csv", latency_cost_rows)
    write_csv(args.output_dir / "table_type_confusion.csv", confusion_rows)
    write_csv(args.output_dir / "table_dictionary_coverage.csv", dictionary_rows)
    report = {
        "gold_source": str(args.gold),
        "test_documents": len(gold),
        "bootstrap_iterations": args.bootstrap,
        "bootstrap_seed": args.seed,
        "pricing": {
            "as_of": args.pricing_as_of,
            "source": args.pricing_source,
            "input_usd_per_million": args.input_price_usd_per_million,
            "output_usd_per_million": args.output_price_usd_per_million,
        },
        "runs": {
            f"{key[0]}-r{key[1]}": {
                "source_file": value["source_file"],
                "metrics": value["metrics"],
                "bootstrap_95": value["bootstrap_95"],
                "candidate_outcomes": value["candidate_outcomes"],
                "stage_timings_ms": value["stage_timings_ms"],
                "cost": value["cost"],
            }
            for key, value in groups.items()
        },
        "dictionary_coverage": coverage,
        "paired_bootstrap_f1_differences": comparisons,
        "aggregate_ablation": aggregate_rows,
    }
    (args.output_dir / "benchmark_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(args.output_dir), "runs": len(groups)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
