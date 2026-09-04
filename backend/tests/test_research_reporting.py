import pytest

from core.research_reporting import (
    dictionary_coverage,
    summarize_candidate_outcomes,
    summarize_cost,
    summarize_human_assignments,
    summarize_stage_timings,
)


def test_stage_rejection_and_cost_summaries_have_explicit_denominators():
    records = [
        {
            "stages_ms": {"dictionary": 1, "ai_call": 10, "post_validation": 2, "merge": 1, "storage": 1},
            "events": [{"outcome": "accepted"}, {"outcome": "surface_mismatch"}],
            "input_tokens": 100,
            "output_tokens": 50,
        },
        {
            "stages_ms": {"dictionary": 3, "ai_call": 20, "post_validation": 4, "merge": 0, "storage": 1},
            "events": [{"outcome": "invalid_offset"}],
            "input_tokens": 200,
            "output_tokens": 100,
        },
    ]
    timings = summarize_stage_timings(records)
    assert timings["ai_call"]["median"] == 15
    assert timings["total"]["p95"] == pytest.approx(27.35)
    outcomes = summarize_candidate_outcomes(records)
    assert outcomes["candidate_events"] == 3
    assert outcomes["rejection_rate"] == 2 / 3
    cost = summarize_cost(records, input_usd_per_million=1.0, output_usd_per_million=2.0)
    assert cost["total_cost_usd"] == 0.0006
    assert cost["cost_per_1000_documents_usd"] == 0.3


def test_dictionary_coverage_is_exact_and_per_type():
    gold = {
        1: [
            {"start": 0, "end": 3, "surface": "sốt", "type": "Triệu chứng"},
            {"start": 4, "end": 7, "surface": "ho", "type": "Triệu chứng"},
        ]
    }
    dictionary = {
        1: [
            {"start": 0, "end": 3, "surface": "sốt", "type": "Triệu chứng", "code": "R50"},
            {"start": 4, "end": 6, "surface": "ho", "type": "Triệu chứng", "code": ""},
        ]
    }
    row = dictionary_coverage(gold, dictionary)["Triệu chứng"]
    assert row["gold_entities"] == 2
    assert row["exact_recall"] == 0.5
    assert row["unique_surface_coverage"] == 0.5
    assert row["valid_code_rate"] == 1.0


def test_human_summary_separates_blind_and_assisted():
    gold = {1: [{"start": 0, "end": 3, "surface": "sốt", "type": "Triệu chứng"}]}
    assignments = [
        {
            "document_id": 1,
            "assignment_role": "annotator",
            "annotation_mode": "blind",
            "active_seconds": 30,
            "entities": [{"start": 0, "end": 3, "surface": "sốt", "type": "Triệu chứng", "decision": "added"}],
        },
        {
            "document_id": 1,
            "assignment_role": "annotator",
            "annotation_mode": "assisted",
            "active_seconds": 10,
            "entities": [{"start": 0, "end": 3, "surface": "sốt", "type": "Triệu chứng", "decision": "accepted"}],
        },
    ]
    result = summarize_human_assignments(assignments, gold)
    assert result["blind"]["active_seconds"]["mean"] == 30
    assert result["assisted"]["decisions"]["accepted"] == 1
    assert result["assisted"]["quality_against_gold"]["micro"]["f1"] == 1
