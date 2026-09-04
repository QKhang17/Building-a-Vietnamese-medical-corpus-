"""Tong hop cac bang benchmark tu raw trace, khong phu thuoc giao dien."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any, Iterable, Mapping

from core.annotation_service import ENTITY_TYPES
from core.research_metrics import score_corpus


REJECTION_OUTCOMES = (
    "invalid_json",
    "invalid_schema",
    "invalid_type",
    "invalid_offset",
    "surface_mismatch",
    "duplicate",
    "overlap_removed",
    "dictionary_override",
)


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def descriptive(values: Iterable[float]) -> dict[str, float | int | None]:
    rows = [float(value) for value in values]
    return {
        "n": len(rows),
        "mean": mean(rows) if rows else None,
        "median": median(rows) if rows else None,
        "p95": percentile(rows, 0.95),
    }


def summarize_stage_timings(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    total: list[float] = []
    for record in records:
        stages = record.get("stages_ms") or {}
        row_total = 0.0
        for stage in ("dictionary", "ai_call", "post_validation", "merge", "storage"):
            value = float(stages.get(stage, 0) or 0)
            grouped[stage].append(value)
            row_total += value
        total.append(row_total)
    result = {stage: descriptive(values) for stage, values in grouped.items()}
    result["total"] = descriptive(total)
    return result


def summarize_candidate_outcomes(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for record in records:
        counts.update(str(event.get("outcome") or "unknown") for event in record.get("events", []))
    total = sum(counts.values())
    rejected = sum(counts[outcome] for outcome in REJECTION_OUTCOMES)
    return {
        "candidate_events": total,
        "accepted": counts["accepted"],
        "rejected_or_overridden": rejected,
        "rejection_rate": rejected / total if total else None,
        "by_outcome": {
            outcome: {
                "count": counts[outcome],
                "rate_of_all_candidates": counts[outcome] / total if total else None,
            }
            for outcome in ("accepted",) + REJECTION_OUTCOMES
        },
    }


def summarize_cost(
    records: Iterable[Mapping[str, Any]],
    *,
    input_usd_per_million: float,
    output_usd_per_million: float,
) -> dict[str, Any]:
    rows = list(records)
    input_tokens = sum(int(row.get("input_tokens", 0) or 0) for row in rows)
    output_tokens = sum(int(row.get("output_tokens", 0) or 0) for row in rows)
    total_cost = (
        input_tokens * input_usd_per_million + output_tokens * output_usd_per_million
    ) / 1_000_000
    n = len(rows)
    return {
        "documents": n,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_usd_per_million_tokens": input_usd_per_million,
        "output_usd_per_million_tokens": output_usd_per_million,
        "total_cost_usd": total_cost,
        "cost_per_document_usd": total_cost / n if n else None,
        "cost_per_1000_documents_usd": total_cost * 1000 / n if n else None,
    }


def dictionary_coverage(
    gold_by_document: Mapping[Any, Iterable[dict[str, Any]]],
    dictionary_by_document: Mapping[Any, Iterable[dict[str, Any]]],
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for entity_type in ENTITY_TYPES:
        gold = []
        dictionary = []
        for document_id, entities in gold_by_document.items():
            gold.extend(
                (document_id, int(item["start"]), int(item["end"]), str(item.get("surface") or ""))
                for item in entities
                if item.get("type") == entity_type and item.get("decision", "accepted") != "rejected"
            )
            dictionary.extend(
                (
                    document_id,
                    int(item["start"]),
                    int(item["end"]),
                    str(item.get("surface") or ""),
                    str(item.get("code") or ""),
                )
                for item in dictionary_by_document.get(document_id, [])
                if item.get("type") == entity_type and item.get("decision", "accepted") != "rejected"
            )
        gold_keys = {(doc, start, end) for doc, start, end, _ in gold}
        matched = [item for item in dictionary if item[:3] in gold_keys]
        gold_surfaces = {surface.casefold() for *_, surface in gold if surface}
        matched_surfaces = {surface.casefold() for *_, surface, _ in matched if surface}
        rows[entity_type] = {
            "gold_entities": len(gold),
            "exact_matches": len(matched),
            "exact_recall": len(matched) / len(gold) if gold else None,
            "gold_unique_surfaces": len(gold_surfaces),
            "matched_unique_surfaces": len(matched_surfaces),
            "unique_surface_coverage": len(matched_surfaces) / len(gold_surfaces) if gold_surfaces else None,
            "matched_with_valid_code": sum(bool(code.strip()) for *_, code in matched),
            "valid_code_rate": (
                sum(bool(code.strip()) for *_, code in matched) / len(matched) if matched else None
            ),
        }
    return rows


def summarize_human_assignments(
    assignments: Iterable[Mapping[str, Any]],
    gold_by_document: Mapping[Any, Iterable[dict[str, Any]]],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        if assignment.get("assignment_role", "annotator") == "annotator":
            grouped[str(assignment.get("annotation_mode") or "blind")].append(assignment)
    result: dict[str, Any] = {}
    for mode, rows in grouped.items():
        decisions: Counter[str] = Counter()
        gold = {}
        predictions = {}
        for index, row in enumerate(rows):
            entities = list(row.get("entities") or [])
            decisions.update(str(entity.get("decision") or "accepted") for entity in entities)
            document_id = row.get("document_id")
            if document_id in gold_by_document:
                gold[index] = gold_by_document[document_id]
                predictions[index] = entities
        result[mode] = {
            "assignments": len(rows),
            "active_seconds": descriptive(float(row.get("active_seconds", 0) or 0) for row in rows),
            "decisions": dict(decisions),
            "quality_against_gold": score_corpus(gold, predictions) if gold else None,
        }
    return result
