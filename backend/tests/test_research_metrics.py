import pytest

from core.research_metrics import bootstrap_micro_ci, classify_errors, score_corpus


def e(start, end, entity_type, code=""):
    return {"start": start, "end": end, "surface": "x", "type": entity_type, "code": code}


def test_exact_metrics_and_hand_checked_error_taxonomy():
    gold = {"d1": [e(0, 4, "Bệnh lý", "D1"), e(10, 14, "Triệu chứng")]}
    pred = {"d1": [e(0, 4, "Bệnh lý", "D1"), e(10, 14, "Bệnh lý"), e(20, 22, "Xét nghiệm")]}
    report = score_corpus(gold, pred)
    assert report["micro"]["tp"] == 1
    assert report["micro"]["fp"] == 2
    assert report["micro"]["fn"] == 1
    assert report["micro"]["precision"] == pytest.approx(1 / 3)
    assert report["micro"]["recall"] == pytest.approx(1 / 2)
    assert report["micro"]["f1"] == pytest.approx(0.4)
    assert report["errors"]["wrong_type"] == 1
    assert report["errors"]["spurious"] == 1
    assert report["type_confusion"]["Bệnh lý"]["Bệnh lý"] == 1
    assert report["type_confusion"]["Triệu chứng"]["Bệnh lý"] == 1


def test_boundary_error_is_not_double_counted_as_missing_and_spurious():
    errors = classify_errors([e(2, 8, "Bệnh lý")], [e(3, 8, "Bệnh lý")])
    assert errors["counts"]["boundary"] == 1
    assert errors["counts"]["missing"] == 0
    assert errors["counts"]["spurious"] == 0


def test_invalid_raw_ai_candidates_are_counted_as_extra_false_positives():
    report = score_corpus(
        {"d": [e(0, 4, "Bệnh lý")]},
        {"d": [e(0, 4, "Bệnh lý")]},
        extra_false_positives={"d": 2},
        extra_false_positives_by_type={"d": {"Bệnh lý": 1}},
    )
    assert report["micro"]["tp"] == 1
    assert report["micro"]["fp"] == 2
    assert report["per_type"]["Bệnh lý"]["fp"] == 1


def test_bootstrap_is_reproducible():
    gold = {"a": [e(0, 1, "Bệnh lý")], "b": [e(0, 1, "Triệu chứng")]}
    pred = {"a": [e(0, 1, "Bệnh lý")], "b": []}
    assert bootstrap_micro_ci(gold, pred, iterations=50, seed=9) == bootstrap_micro_ci(gold, pred, iterations=50, seed=9)
