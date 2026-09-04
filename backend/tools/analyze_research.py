"""Tinh bang metric va bootstrap tu gold/prediction JSONL da cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.research_metrics import bootstrap_micro_ci, score_corpus


def load_jsonl(path: Path) -> tuple[dict[str, list[dict]], dict[str, int], dict[str, dict[str, int]]]:
    entities: dict[str, list[dict]] = {}
    extra: dict[str, int] = {}
    extra_by_type: dict[str, dict[str, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = str(row["article_id"])
        entities[key] = row.get("entities", [])
        extra[key] = int(row.get("extra_false_positives", 0))
        extra_by_type[key] = {
            str(entity_type): int(count)
            for entity_type, count in row.get("extra_false_positives_by_type", {}).items()
        }
    return entities, extra, extra_by_type


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()
    gold, _, _ = load_jsonl(args.gold)
    predictions, extra, extra_by_type = load_jsonl(args.predictions)
    report = score_corpus(
        gold,
        predictions,
        extra_false_positives=extra,
        extra_false_positives_by_type=extra_by_type,
    )
    report["bootstrap_95"] = bootstrap_micro_ci(
        gold,
        predictions,
        extra_false_positives=extra,
        extra_false_positives_by_type=extra_by_type,
        iterations=args.bootstrap,
        seed=args.seed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "documents": report["documents"], "micro_f1": report["micro"]["f1"]}))


if __name__ == "__main__":
    main()
