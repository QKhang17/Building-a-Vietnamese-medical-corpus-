"""Metric tai lap cho NER span: exact, relaxed, bootstrap va phan loai loi."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Iterable, Mapping

from core.annotation_service import ENTITY_TYPES


def _active(entities: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in entities
        if str(item.get("decision") or "accepted") != "rejected"
    ]


def _exact_key(item: Mapping[str, Any]) -> tuple[int, int, str]:
    return int(item["start"]), int(item["end"]), str(item["type"])


def _span_key(item: Mapping[str, Any]) -> tuple[int, int]:
    return int(item["start"]), int(item["end"])


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    return max(0, min(int(left["end"]), int(right["end"])) - max(int(left["start"]), int(right["start"])))


def _relaxed_counts(gold: list[dict[str, Any]], pred: list[dict[str, Any]]) -> tuple[int, int, int]:
    candidates: list[tuple[float, int, int]] = []
    for gi, g in enumerate(gold):
        for pi, p in enumerate(pred):
            if str(g["type"]) != str(p["type"]):
                continue
            intersection = _overlap(g, p)
            if not intersection:
                continue
            union = max(int(g["end"]), int(p["end"])) - min(int(g["start"]), int(p["start"]))
            candidates.append((intersection / union, gi, pi))
    matched_gold: set[int] = set()
    matched_pred: set[int] = set()
    for _, gi, pi in sorted(candidates, reverse=True):
        if gi not in matched_gold and pi not in matched_pred:
            matched_gold.add(gi)
            matched_pred.add(pi)
    tp = len(matched_gold)
    return tp, len(pred) - tp, len(gold) - tp


def classify_errors(
    gold: Iterable[dict[str, Any]], pred: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    gold_rows = _active(gold)
    pred_rows = _active(pred)
    unmatched_gold = set(range(len(gold_rows)))
    unmatched_pred = set(range(len(pred_rows)))
    confusion: dict[str, Counter[str]] = {entity_type: Counter() for entity_type in ENTITY_TYPES}
    counts = Counter({"wrong_type": 0, "boundary": 0, "missing": 0, "spurious": 0, "wrong_code": 0})

    # Exact span gets priority: same type is TP; different type is a type confusion.
    for gi, g in enumerate(gold_rows):
        same_span = [pi for pi in unmatched_pred if _span_key(pred_rows[pi]) == _span_key(g)]
        exact = next((pi for pi in same_span if pred_rows[pi]["type"] == g["type"]), None)
        if exact is not None:
            unmatched_gold.discard(gi)
            unmatched_pred.discard(exact)
            confusion.setdefault(str(g["type"]), Counter())[str(g["type"])] += 1
            gold_code = str(g.get("code") or "")
            if gold_code and gold_code != str(pred_rows[exact].get("code") or ""):
                counts["wrong_code"] += 1
            continue
        if same_span:
            pi = same_span[0]
            unmatched_gold.discard(gi)
            unmatched_pred.discard(pi)
            counts["wrong_type"] += 1
            confusion.setdefault(str(g["type"]), Counter())[str(pred_rows[pi]["type"])] += 1

    # Remaining overlap with the same type is a boundary error.
    overlap_pairs: list[tuple[int, int, int]] = []
    for gi in unmatched_gold:
        for pi in unmatched_pred:
            if gold_rows[gi]["type"] == pred_rows[pi]["type"]:
                overlap_pairs.append((_overlap(gold_rows[gi], pred_rows[pi]), gi, pi))
    for overlap, gi, pi in sorted(overlap_pairs, reverse=True):
        if overlap and gi in unmatched_gold and pi in unmatched_pred:
            unmatched_gold.remove(gi)
            unmatched_pred.remove(pi)
            counts["boundary"] += 1

    counts["missing"] = len(unmatched_gold)
    counts["spurious"] = len(unmatched_pred)
    return {
        "counts": dict(counts),
        "type_confusion": {
            gold_type: {pred_type: counter.get(pred_type, 0) for pred_type in ENTITY_TYPES}
            for gold_type, counter in confusion.items()
        },
    }


def score_corpus(
    gold_by_document: Mapping[Any, Iterable[dict[str, Any]]],
    pred_by_document: Mapping[Any, Iterable[dict[str, Any]]],
    *,
    extra_false_positives: Mapping[Any, int] | None = None,
    extra_false_positives_by_type: Mapping[Any, Mapping[str, int]] | None = None,
) -> dict[str, Any]:
    document_ids = sorted(set(gold_by_document) | set(pred_by_document), key=str)
    per_type_counts = {entity_type: Counter(tp=0, fp=0, fn=0) for entity_type in ENTITY_TYPES}
    relaxed = Counter(tp=0, fp=0, fn=0)
    code_correct = code_total = 0
    error_counts = Counter()
    type_confusion = {entity_type: Counter() for entity_type in ENTITY_TYPES}
    per_document: dict[str, dict[str, Any]] = {}

    for document_id in document_ids:
        gold = _active(gold_by_document.get(document_id, []))
        pred = _active(pred_by_document.get(document_id, []))
        gold_counter = Counter(_exact_key(item) for item in gold)
        pred_counter = Counter(_exact_key(item) for item in pred)
        tp_counter = gold_counter & pred_counter
        extra_fp = int((extra_false_positives or {}).get(document_id, 0))
        extra_by_type = (extra_false_positives_by_type or {}).get(document_id, {})

        for entity_type in ENTITY_TYPES:
            tp = sum(count for key, count in tp_counter.items() if key[2] == entity_type)
            gold_total = sum(count for key, count in gold_counter.items() if key[2] == entity_type)
            pred_total = sum(count for key, count in pred_counter.items() if key[2] == entity_type)
            per_type_counts[entity_type].update(
                tp=tp,
                fp=pred_total - tp + int(extra_by_type.get(entity_type, 0)),
                fn=gold_total - tp,
            )

        exact_tp = sum(tp_counter.values())
        exact_fp = sum(pred_counter.values()) - exact_tp + extra_fp
        exact_fn = sum(gold_counter.values()) - exact_tp
        per_document[str(document_id)] = _prf(exact_tp, exact_fp, exact_fn)

        relaxed_tp, relaxed_fp, relaxed_fn = _relaxed_counts(gold, pred)
        relaxed.update(tp=relaxed_tp, fp=relaxed_fp + extra_fp, fn=relaxed_fn)

        pred_exact = defaultdict(list)
        for item in pred:
            pred_exact[_exact_key(item)].append(item)
        for gold_item in gold:
            if pred_exact[_exact_key(gold_item)]:
                pred_item = pred_exact[_exact_key(gold_item)].pop()
                gold_code = str(gold_item.get("code") or "")
                if gold_code:
                    code_total += 1
                    code_correct += int(gold_code == str(pred_item.get("code") or ""))

        errors = classify_errors(gold, pred)
        error_counts.update(errors["counts"])
        error_counts["invalid_or_unmappable_ai"] += extra_fp
        for gold_type, row in errors["type_confusion"].items():
            type_confusion[gold_type].update(row)

    per_type = {
        entity_type: _prf(counts["tp"], counts["fp"], counts["fn"])
        for entity_type, counts in per_type_counts.items()
    }
    micro_counts = Counter(tp=0, fp=0, fn=0)
    for counts in per_type_counts.values():
        micro_counts.update(counts)
    micro_counts["fp"] += sum(
        max(
            0,
            int(total)
            - sum(
                int(value)
                for value in (extra_false_positives_by_type or {}).get(document_id, {}).values()
            ),
        )
        for document_id, total in (extra_false_positives or {}).items()
    )
    micro = _prf(micro_counts["tp"], micro_counts["fp"], micro_counts["fn"])
    macro = {
        metric: mean(float(per_type[entity_type][metric]) for entity_type in ENTITY_TYPES)
        for metric in ("precision", "recall", "f1")
    }
    return {
        "documents": len(document_ids),
        "micro": micro,
        "macro": macro,
        "per_type": per_type,
        "relaxed_overlap": _prf(relaxed["tp"], relaxed["fp"], relaxed["fn"]),
        "code_accuracy": {
            "correct": code_correct,
            "total": code_total,
            "accuracy": code_correct / code_total if code_total else None,
        },
        "errors": dict(error_counts),
        "type_confusion": {
            gold_type: {pred_type: type_confusion[gold_type].get(pred_type, 0) for pred_type in ENTITY_TYPES}
            for gold_type in ENTITY_TYPES
        },
        "per_document": per_document,
    }


def bootstrap_micro_ci(
    gold_by_document: Mapping[Any, Iterable[dict[str, Any]]],
    pred_by_document: Mapping[Any, Iterable[dict[str, Any]]],
    *,
    extra_false_positives: Mapping[Any, int] | None = None,
    extra_false_positives_by_type: Mapping[Any, Mapping[str, int]] | None = None,
    iterations: int = 1000,
    seed: int = 20260830,
) -> dict[str, dict[str, float]]:
    document_ids = list(gold_by_document)
    if not document_ids:
        raise ValueError("Không có tài liệu để bootstrap")
    rng = random.Random(seed)
    distributions = {"precision": [], "recall": [], "f1": []}
    for _ in range(iterations):
        sampled = [rng.choice(document_ids) for _ in document_ids]
        sampled_gold = {index: gold_by_document[doc_id] for index, doc_id in enumerate(sampled)}
        sampled_pred = {index: pred_by_document.get(doc_id, []) for index, doc_id in enumerate(sampled)}
        sampled_extra = {
            index: int((extra_false_positives or {}).get(doc_id, 0))
            for index, doc_id in enumerate(sampled)
        }
        sampled_extra_by_type = {
            index: dict((extra_false_positives_by_type or {}).get(doc_id, {}))
            for index, doc_id in enumerate(sampled)
        }
        score = score_corpus(
            sampled_gold,
            sampled_pred,
            extra_false_positives=sampled_extra,
            extra_false_positives_by_type=sampled_extra_by_type,
        )["micro"]
        for metric in distributions:
            distributions[metric].append(float(score[metric]))

    def percentile(values: list[float], fraction: float) -> float:
        ordered = sorted(values)
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        metric: {
            "lower_95": percentile(values, 0.025),
            "upper_95": percentile(values, 0.975),
            "bootstrap_mean": mean(values),
        }
        for metric, values in distributions.items()
    }


def paired_bootstrap_f1_difference(
    gold_by_document: Mapping[Any, Iterable[dict[str, Any]]],
    left_predictions: Mapping[Any, Iterable[dict[str, Any]]],
    right_predictions: Mapping[Any, Iterable[dict[str, Any]]],
    *,
    left_extra_false_positives: Mapping[Any, int] | None = None,
    right_extra_false_positives: Mapping[Any, int] | None = None,
    left_extra_false_positives_by_type: Mapping[Any, Mapping[str, int]] | None = None,
    right_extra_false_positives_by_type: Mapping[Any, Mapping[str, int]] | None = None,
    iterations: int = 1000,
    seed: int = 20260830,
) -> dict[str, float]:
    document_ids = list(gold_by_document)
    if not document_ids:
        raise ValueError("Không có tài liệu để bootstrap")
    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(iterations):
        sampled = [rng.choice(document_ids) for _ in document_ids]
        gold = {index: gold_by_document[doc_id] for index, doc_id in enumerate(sampled)}
        left = {index: left_predictions.get(doc_id, []) for index, doc_id in enumerate(sampled)}
        right = {index: right_predictions.get(doc_id, []) for index, doc_id in enumerate(sampled)}
        left_extra = {
            index: int((left_extra_false_positives or {}).get(doc_id, 0))
            for index, doc_id in enumerate(sampled)
        }
        right_extra = {
            index: int((right_extra_false_positives or {}).get(doc_id, 0))
            for index, doc_id in enumerate(sampled)
        }
        left_extra_by_type = {
            index: dict((left_extra_false_positives_by_type or {}).get(doc_id, {}))
            for index, doc_id in enumerate(sampled)
        }
        right_extra_by_type = {
            index: dict((right_extra_false_positives_by_type or {}).get(doc_id, {}))
            for index, doc_id in enumerate(sampled)
        }
        left_f1 = float(
            score_corpus(
                gold,
                left,
                extra_false_positives=left_extra,
                extra_false_positives_by_type=left_extra_by_type,
            )["micro"]["f1"]
        )
        right_f1 = float(
            score_corpus(
                gold,
                right,
                extra_false_positives=right_extra,
                extra_false_positives_by_type=right_extra_by_type,
            )["micro"]["f1"]
        )
        differences.append(right_f1 - left_f1)
    differences.sort()
    lower = differences[int(0.025 * (len(differences) - 1))]
    upper = differences[int(0.975 * (len(differences) - 1))]
    return {
        "mean_difference": mean(differences),
        "lower_95": lower,
        "upper_95": upper,
        "probability_right_better": sum(value > 0 for value in differences) / len(differences),
    }
