import unicodedata

import pytest

from core.annotation_service import (
    AnnotationConflictError,
    can_view_independent_submissions,
    entity_signature,
    normalize_entities,
    snapshot_sha256,
    validate_submission_entities,
    validate_transition,
    visible_preannotation,
)


def entity(text, start, end, entity_type="Bệnh lý", **extra):
    return {
        "start": start,
        "end": end,
        "surface": text[start:end],
        "type": entity_type,
        "source": "human",
        "decision": "accepted",
        **extra,
    }


def test_normalize_entities_preserves_unicode_offsets_and_sorts():
    text = unicodedata.normalize("NFD", "Đau đầu và sốt")
    fever_start = text.index("sốt")
    rows = normalize_entities(
        text,
        [entity(text, fever_start, len(text), "Triệu chứng"), entity(text, 0, 8, "Triệu chứng")],
    )
    assert rows[0]["surface"] == text[:8]
    assert rows[1]["surface"] == text[fever_start:]
    assert rows[1]["end"] == len(text)


@pytest.mark.parametrize(
    "row,error",
    [
        ({"start": -1, "end": 2, "surface": "ab", "type": "Bệnh lý"}, "Offset"),
        ({"start": 0, "end": 2, "surface": "sai", "type": "Bệnh lý"}, "Surface"),
        ({"start": 0, "end": 2, "surface": "ab", "type": "Không hợp lệ"}, "Loại"),
    ],
)
def test_normalize_entities_rejects_invalid_rows(row, error):
    with pytest.raises(ValueError, match=error):
        normalize_entities("abcdef", [row])


def test_normalize_entities_rejects_active_overlap_but_allows_rejected_overlap():
    text = "tăng huyết áp"
    first = entity(text, 0, len(text))
    second = entity(text, 0, 4, "Triệu chứng")
    with pytest.raises(ValueError, match="chồng lấn"):
        normalize_entities(text, [first, second])
    second["decision"] = "rejected"
    assert len(normalize_entities(text, [first, second])) == 2


def test_state_machine_blocks_reopening_locked_assignment():
    validate_transition("assigned", "in_progress")
    validate_transition("in_progress", "submitted")
    with pytest.raises(ValueError):
        validate_transition("locked", "in_progress")


def test_signature_and_snapshot_ignore_rejected_but_are_deterministic():
    text = "đau đầu sốt"
    accepted = entity(text, 0, 7, "Triệu chứng", code="S1")
    rejected = entity(text, 8, 11, "Triệu chứng", decision="rejected")
    assert entity_signature([accepted, rejected]) == ((0, 7, "Triệu chứng", "S1"),)
    assert snapshot_sha256([accepted, rejected]) == snapshot_sha256([accepted, rejected])


def test_blind_mode_never_exposes_preannotation():
    payload = '[{"surface":"sốt"}]'
    assert visible_preannotation("blind", payload) == []
    assert visible_preannotation("adjudication", payload) == []
    assert visible_preannotation("assisted", payload)[0]["surface"] == "sốt"


def test_only_adjudicator_sees_both_submissions_after_conflict():
    assert not can_view_independent_submissions("annotator", "conflict")
    assert not can_view_independent_submissions("adjudicator", "submitted")
    assert can_view_independent_submissions("adjudicator", "conflict")
    assert can_view_independent_submissions("adjudicator", "locked")


def test_assisted_submission_requires_decisions_and_reasons():
    with pytest.raises(ValueError, match="proposed"):
        validate_submission_entities("assisted", [{"decision": "proposed"}])
    with pytest.raises(ValueError, match="lý do"):
        validate_submission_entities("assisted", [{"decision": "modified", "reason": ""}])
    validate_submission_entities(
        "assisted",
        [
            {"decision": "accepted", "reason": ""},
            {"decision": "rejected", "reason": "Không phải thực thể y khoa"},
        ],
    )
