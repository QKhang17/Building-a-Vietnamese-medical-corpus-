"""Bon cau hinh ablation NER voi raw-output cache va trace tung ung vien."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from core.annotation_service import ENTITY_TYPES


AI_CATEGORIES = ENTITY_TYPES
DICTIONARY_TO_AI_CATEGORY = {
    "Bệnh Lý": "Bệnh lý",
    "Triệu Chứng": "Triệu chứng",
    "Đông Y / YHCT": "Bệnh lý",
}


CONFIGURATIONS = ("dictionary", "ai_raw", "ai_constrained", "hybrid")


class ModelClient(Protocol):
    def generate(self, prompt: str, *, model_name: str, temperature: float) -> dict[str, Any]: ...


@dataclass
class ValidationResult:
    entities: list[dict[str, Any]]
    events: list[dict[str, Any]]
    invalid_false_positives: int


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def build_prompt(text: str, configuration: str, dictionary_candidates: list[dict[str, Any]]) -> str:
    if configuration not in {"ai_raw", "ai_constrained", "hybrid"}:
        raise ValueError(f"Cấu hình không dùng AI: {configuration}")
    dictionary_context: list[dict[str, Any]] = []
    if configuration == "hybrid":
        dictionary_context = [
            {
                "term": item["term"],
                "start": item["start"],
                "end": item["end"],
                "category": DICTIONARY_TO_AI_CATEGORY.get(item.get("dictionary_type", ""), "Bệnh lý"),
                "code": item.get("code", ""),
            }
            for item in dictionary_candidates
        ]
    context = json.dumps(dictionary_context, ensure_ascii=False)
    return f"""Bạn là chuyên gia NER y khoa tiếng Việt. Trả đúng một JSON object có khóa
\"entities\". Mỗi phần tử phải có term, start, end, type và code. start/end là offset ký tự
theo chuẩn [start,end) trên đúng văn bản nguồn. type chỉ được thuộc: {', '.join(AI_CATEGORIES)}.
Không dùng Markdown, không giải thích.

Ngữ cảnh từ điển (rỗng nếu cấu hình không sử dụng từ điển):
{context}

Văn bản nguồn:
{text}
"""


def parse_model_output(raw_text: str) -> tuple[list[Any], list[dict[str, Any]]]:
    cleaned = str(raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    try:
        payload = json.loads(cleaned)
    except (TypeError, json.JSONDecodeError):
        return [], [{"candidate_index": 0, "term": "", "outcome": "invalid_json", "reason": "Không parse được JSON"}]
    if not isinstance(payload, dict) or not isinstance(payload.get("entities"), list):
        return [], [{"candidate_index": 0, "term": "", "outcome": "invalid_schema", "reason": "Thiếu mảng entities"}]
    return payload["entities"], []


def validate_candidates(text: str, candidates: list[Any], *, resolve_overlaps: bool) -> ValidationResult:
    accepted: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    identities: set[tuple[int, int, str, str]] = set()
    invalid_fp = 0

    for index, raw in enumerate(candidates):
        event = {"candidate_index": index, "term": "", "outcome": "accepted", "reason": ""}
        if not isinstance(raw, dict):
            event.update(outcome="invalid_schema", reason="Ứng viên không phải object")
            events.append(event)
            invalid_fp += 1
            continue
        term = str(raw.get("term") or "")
        event["term"] = term
        entity_type = str(raw.get("type") or "")
        event["proposed_type"] = entity_type
        event["proposed_code"] = str(raw.get("code") or "")
        event["proposed_start"] = raw.get("start")
        event["proposed_end"] = raw.get("end")
        if entity_type not in AI_CATEGORIES:
            event.update(outcome="invalid_type", reason=f"Loại ngoài whitelist: {entity_type}")
            events.append(event)
            invalid_fp += 1
            continue
        try:
            start = int(raw.get("start"))
            end = int(raw.get("end"))
        except (TypeError, ValueError):
            event.update(outcome="invalid_offset", reason="Offset không phải số nguyên")
            events.append(event)
            invalid_fp += 1
            continue
        if start < 0 or end <= start or end > len(text):
            event.update(outcome="invalid_offset", reason=f"Offset ngoài văn bản: [{start},{end})")
            events.append(event)
            invalid_fp += 1
            continue
        surface = text[start:end]
        if surface != term:
            event.update(outcome="surface_mismatch", reason=f"Văn bản gốc là {surface!r}")
            events.append(event)
            invalid_fp += 1
            continue
        code = str(raw.get("code") or "")
        identity = (start, end, entity_type, code)
        if identity in identities:
            event.update(outcome="duplicate", reason="Trùng span/type/code")
            events.append(event)
            invalid_fp += 1
            continue
        identities.add(identity)
        entity = {
            "start": start,
            "end": end,
            "surface": surface,
            "type": entity_type,
            "code": code,
            "source": "ai",
            "decision": "accepted",
        }
        event["entity"] = entity
        events.append(event)
        accepted.append(entity)

    if resolve_overlaps:
        kept: list[dict[str, Any]] = []
        for entity in sorted(accepted, key=lambda item: (-(item["end"] - item["start"]), item["start"], item["type"])):
            if any(entity["start"] < other["end"] and entity["end"] > other["start"] for other in kept):
                for event in events:
                    if event.get("entity") is entity:
                        event.update(outcome="overlap_removed", reason="Thua quy tắc longest non-overlap")
                        break
                continue
            kept.append(entity)
        accepted = sorted(kept, key=lambda item: (item["start"], item["end"], item["type"]))
    return ValidationResult(accepted, events, invalid_fp)


def dictionary_entities(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in candidates:
        result.append(
            {
                "start": int(item["start"]),
                "end": int(item["end"]),
                "surface": str(item["term"]),
                "type": DICTIONARY_TO_AI_CATEGORY.get(str(item.get("dictionary_type") or ""), "Bệnh lý"),
                "code": str(item.get("code") or ""),
                "source": "dictionary",
                "decision": "accepted",
            }
        )
    return sorted(result, key=lambda item: (item["start"], item["end"], item["type"]))


def merge_hybrid(
    dictionary: list[dict[str, Any]], ai: ValidationResult
) -> ValidationResult:
    kept = [dict(item) for item in dictionary]
    events = [dict(event) for event in ai.events]
    invalid_fp = ai.invalid_false_positives
    for entity in ai.entities:
        overlaps = [other for other in kept if entity["start"] < other["end"] and entity["end"] > other["start"]]
        if overlaps:
            exact_dictionary = next(
                (item for item in overlaps if item["start"] == entity["start"] and item["end"] == entity["end"]),
                None,
            )
            outcome = "dictionary_override" if exact_dictionary else "overlap_removed"
            for event in events:
                if event.get("entity") == entity:
                    event.update(outcome=outcome, reason="Từ điển có quyền ưu tiên trong pipeline lai")
                    break
            continue
        kept.append(entity)
    return ValidationResult(
        sorted(kept, key=lambda item: (item["start"], item["end"], item["type"])),
        events,
        invalid_fp,
    )


def process_configuration(
    text: str,
    configuration: str,
    raw_model_text: str | None,
    dictionary_candidates: list[dict[str, Any]],
) -> ValidationResult:
    if configuration == "dictionary":
        return ValidationResult(dictionary_entities(dictionary_candidates), [], 0)
    candidates, parse_events = parse_model_output(raw_model_text or "")
    if parse_events:
        return ValidationResult([], parse_events, len(parse_events))
    if configuration == "ai_raw":
        # Raw van can offset hop le de cham exact; moi ung vien khong map duoc duoc tinh FP bo sung.
        return validate_candidates(text, candidates, resolve_overlaps=False)
    constrained = validate_candidates(text, candidates, resolve_overlaps=True)
    if configuration == "ai_constrained":
        return constrained
    if configuration == "hybrid":
        return merge_hybrid(dictionary_entities(dictionary_candidates), constrained)
    raise ValueError(f"Cấu hình không hợp lệ: {configuration}")


class GeminiGenerativeClient:
    def __init__(self, api_key: str | None = None) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key or os.getenv("GEMINI_API_KEY"))

    def generate(self, prompt: str, *, model_name: str, temperature: float) -> dict[str, Any]:
        from google.genai import types

        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=temperature),
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage_metadata", None)
        return {
            "text": str(getattr(response, "text", "")),
            "latency_ms": latency_ms,
            "input_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
            "output_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
            "model_version": str(getattr(response, "model_version", "") or ""),
        }


def default_dictionary_provider(text: str) -> list[dict[str, Any]]:
    # Lazy import de cac cong cu metric/report khong khoi dong SDK Gemini cu cua app.
    from core.ai_label import _dictionary_candidates

    return _dictionary_candidates(text)


def run_document(
    text: str,
    configuration: str,
    *,
    article_id: int | None = None,
    repeat_index: int = 0,
    model_name: str = "gemini-2.5-flash",
    temperature: float = 0.0,
    raw_model_response: dict[str, Any] | None = None,
    client: ModelClient | None = None,
    dictionary_provider: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if configuration not in CONFIGURATIONS:
        raise ValueError(f"Cấu hình không hợp lệ: {configuration}")
    run_uuid = str(uuid.uuid4())
    stage_started = time.perf_counter()
    dictionary = (dictionary_provider or default_dictionary_provider)(text)
    dictionary_ms = round((time.perf_counter() - stage_started) * 1000)
    stages = {"dictionary": dictionary_ms, "ai_call": 0, "validation_merge": 0}
    prompt = ""
    response = raw_model_response or {"text": "", "latency_ms": 0, "input_tokens": 0, "output_tokens": 0, "model_version": ""}
    if configuration != "dictionary":
        prompt = build_prompt(text, configuration, dictionary)
        if raw_model_response is None:
            response = (client or GeminiGenerativeClient()).generate(
                prompt, model_name=model_name, temperature=temperature
            )
        stages["ai_call"] = int(response.get("latency_ms", 0) or 0)
    stages = {
        "dictionary": dictionary_ms,
        "ai_call": stages["ai_call"],
        "post_validation": 0,
        "merge": 0,
        "storage": 0,
    }
    validation_started = time.perf_counter()
    if configuration == "dictionary":
        validated = ValidationResult(dictionary_entities(dictionary), [], 0)
    else:
        candidates, parse_events = parse_model_output(response.get("text", ""))
        if parse_events:
            validated = ValidationResult([], parse_events, len(parse_events))
        else:
            validated = validate_candidates(
                text,
                candidates,
                resolve_overlaps=configuration != "ai_raw",
            )
    stages["post_validation"] = round((time.perf_counter() - validation_started) * 1000)
    if configuration == "hybrid":
        merge_started = time.perf_counter()
        validated = merge_hybrid(dictionary_entities(dictionary), validated)
        stages["merge"] = round((time.perf_counter() - merge_started) * 1000)
    stages["validation_merge"] = stages["post_validation"] + stages["merge"]
    extra_by_type = Counter(
        str(event.get("proposed_type"))
        for event in validated.events
        if configuration == "ai_raw"
        and event.get("outcome") != "accepted"
        and event.get("proposed_type") in AI_CATEGORIES
    )
    return {
        "run_uuid": run_uuid,
        "article_id": article_id,
        "configuration": configuration,
        "repeat_index": repeat_index,
        "model_name": model_name if configuration != "dictionary" else "none",
        "model_version": response.get("model_version", ""),
        "temperature": temperature,
        "prompt_sha256": prompt_sha256(prompt) if prompt else "",
        "raw_model_text": response.get("text", ""),
        "input_tokens": int(response.get("input_tokens", 0) or 0),
        "output_tokens": int(response.get("output_tokens", 0) or 0),
        "stages_ms": stages,
        "entities": validated.entities,
        "events": validated.events,
        "extra_false_positives": validated.invalid_false_positives if configuration == "ai_raw" else 0,
        "extra_false_positives_by_type": dict(extra_by_type),
    }
