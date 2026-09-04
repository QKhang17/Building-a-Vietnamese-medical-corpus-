"""Lay mau phan tang tai lap cho benchmark 300 tom tat."""

from __future__ import annotations

import hashlib
import math
import random
import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import urlparse


DEFAULT_SEED = 20260830


def normalize_abstract(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()


def abstract_sha256(text: str) -> str:
    return hashlib.sha256(normalize_abstract(text).encode("utf-8")).hexdigest()


def _quantile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight)


def prepare_candidates(rows: Iterable[dict[str, Any]], min_chars: int = 80) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in rows:
        text = normalize_abstract(row.get("abstract", ""))
        if len(text) < min_chars:
            continue
        digest = abstract_sha256(text)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        source_url = str(row.get("source_url") or "")
        domain = (urlparse(source_url).hostname or "unknown").lower()
        item = dict(row)
        item.update(
            {
                "abstract": text,
                "text_sha256": digest,
                "char_count": len(text),
                "source_domain": domain,
                "publication_year": int(row["publication_year"])
                if row.get("publication_year") not in (None, "")
                else None,
            }
        )
        prepared.append(item)

    lengths = sorted(item["char_count"] for item in prepared)
    q1 = _quantile(lengths, 1 / 3)
    q2 = _quantile(lengths, 2 / 3)
    for item in prepared:
        if item["char_count"] <= q1:
            length_bin = "short"
        elif item["char_count"] <= q2:
            length_bin = "medium"
        else:
            length_bin = "long"
        year = item["publication_year"] if item["publication_year"] is not None else "unknown"
        item["length_bin"] = length_bin
        item["stratum_key"] = f"{item['source_domain']}|{year}|{length_bin}"
    return prepared


def _allocation(group_sizes: dict[str, int], sample_size: int) -> dict[str, int]:
    total = sum(group_sizes.values())
    if sample_size > total:
        raise ValueError(f"Chỉ có {total} mẫu hợp lệ, không đủ {sample_size}")
    if sample_size < 0:
        raise ValueError("sample_size không được âm")
    ideals = {key: size * sample_size / total for key, size in group_sizes.items()}
    allocation = {
        key: min(size, math.floor(ideals[key])) for key, size in group_sizes.items()
    }
    remaining = sample_size - sum(allocation.values())
    order = sorted(
        group_sizes,
        key=lambda key: (ideals[key] - math.floor(ideals[key]), group_sizes[key], key),
        reverse=True,
    )
    while remaining:
        progressed = False
        for key in order:
            if allocation[key] < group_sizes[key]:
                allocation[key] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise RuntimeError("Không thể phân bổ đủ cỡ mẫu")
    return allocation


def stratified_sample(
    rows: Iterable[dict[str, Any]], sample_size: int, seed: int = DEFAULT_SEED
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["stratum_key"])].append(dict(row))
    allocation = _allocation({key: len(items) for key, items in groups.items()}, sample_size)
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    for key in sorted(groups):
        items = list(groups[key])
        rng.shuffle(items)
        selected.extend(items[: allocation[key]])
    rng.shuffle(selected)
    return selected


def split_sample(
    sample: Iterable[dict[str, Any]], seed: int = DEFAULT_SEED
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in sample]
    if len(rows) != 300:
        raise ValueError("Thiết kế nghiên cứu yêu cầu đúng 300 tóm tắt")
    pilot = stratified_sample(rows, 30, seed + 1)
    pilot_ids = {int(row["id"]) for row in pilot}
    remaining = [row for row in rows if int(row["id"]) not in pilot_ids]
    development = stratified_sample(remaining, 70, seed + 2)
    development_ids = {int(row["id"]) for row in development}
    for row in rows:
        article_id = int(row["id"])
        if article_id in pilot_ids:
            row["split_name"] = "pilot"
        elif article_id in development_ids:
            row["split_name"] = "development"
        else:
            row["split_name"] = "test"
    return sorted(rows, key=lambda row: (row["split_name"], int(row["id"])))


def assignment_modes(
    split_rows: Iterable[dict[str, Any]], annotator_a: int, annotator_b: int, adjudicator: int
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in split_rows]
    development = sorted(
        (row for row in rows if row["split_name"] == "development"),
        key=lambda row: int(row["id"]),
    )
    assisted_for_a = {int(row["id"]) for row in development[:35]}
    assignments: list[dict[str, Any]] = []
    for row in rows:
        article_id = int(row["id"])
        if row["split_name"] == "development":
            mode_a = "assisted" if article_id in assisted_for_a else "blind"
            mode_b = "blind" if article_id in assisted_for_a else "assisted"
        else:
            mode_a = mode_b = "blind"
        assignments.extend(
            [
                {"article_id": article_id, "expert_id": annotator_a, "role": "annotator", "mode": mode_a},
                {"article_id": article_id, "expert_id": annotator_b, "role": "annotator", "mode": mode_b},
                {"article_id": article_id, "expert_id": adjudicator, "role": "adjudicator", "mode": "adjudication"},
            ]
        )
    return assignments
