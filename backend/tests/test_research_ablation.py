from core.research_ablation import (
    merge_hybrid,
    parse_model_output,
    process_configuration,
    run_document,
    validate_candidates,
)


def test_parse_model_output_reports_invalid_json():
    candidates, events = parse_model_output("not-json")
    assert candidates == []
    assert events[0]["outcome"] == "invalid_json"


def test_validation_logs_surface_type_duplicate_and_overlap():
    text = "tăng huyết áp và sốt"
    candidates = [
        {"term": "tăng huyết áp", "start": 0, "end": 13, "type": "Bệnh lý", "code": "I10"},
        {"term": "tăng huyết áp", "start": 0, "end": 13, "type": "Bệnh lý", "code": "I10"},
        {"term": "huyết áp", "start": 5, "end": 13, "type": "Triệu chứng", "code": ""},
        {"term": "không có", "start": 17, "end": 20, "type": "Triệu chứng", "code": ""},
        {"term": "sốt", "start": 17, "end": 20, "type": "Sai loại", "code": ""},
    ]
    result = validate_candidates(text, candidates, resolve_overlaps=True)
    outcomes = [event["outcome"] for event in result.events]
    assert "accepted" in outcomes
    assert "duplicate" in outcomes
    assert "overlap_removed" in outcomes
    assert "surface_mismatch" in outcomes
    assert "invalid_type" in outcomes
    assert len(result.entities) == 1


def test_hybrid_dictionary_is_authoritative():
    dictionary = [{"start": 0, "end": 3, "surface": "sốt", "type": "Triệu chứng", "code": "S", "source": "dictionary", "decision": "accepted"}]
    ai = validate_candidates("sốt cao", [{"term": "sốt", "start": 0, "end": 3, "type": "Bệnh lý", "code": ""}], resolve_overlaps=True)
    merged = merge_hybrid(dictionary, ai)
    assert merged.entities == dictionary
    assert merged.events[0]["outcome"] == "dictionary_override"


def test_run_document_uses_cached_response_and_exposes_trace():
    response = {"text": '{"entities":[{"term":"sốt","start":0,"end":3,"type":"Triệu chứng","code":""}]}', "latency_ms": 12, "input_tokens": 10, "output_tokens": 8, "model_version": "test"}
    record = run_document(
        "sốt",
        "ai_constrained",
        article_id=7,
        raw_model_response=response,
        dictionary_provider=lambda _text: [],
    )
    assert record["article_id"] == 7
    assert record["entities"][0]["surface"] == "sốt"
    assert record["input_tokens"] == 10
    assert record["stages_ms"]["ai_call"] == 12


def test_hybrid_keeps_dictionary_when_ai_json_is_invalid():
    record = run_document(
        "sốt",
        "hybrid",
        raw_model_response={"text": "not-json"},
        dictionary_provider=lambda _text: [
            {
                "term": "sốt",
                "start": 0,
                "end": 3,
                "dictionary_type": "Triệu Chứng",
                "code": "R50",
            }
        ],
    )
    assert record["entities"][0]["source"] == "dictionary"
    assert record["events"][0]["outcome"] == "invalid_json"
    assert set(("post_validation", "merge", "storage")) <= set(record["stages_ms"])


def test_raw_unmappable_candidate_is_counted_for_its_valid_type():
    record = run_document(
        "sốt",
        "ai_raw",
        raw_model_response={
            "text": '{"entities":[{"term":"ho","start":0,"end":3,"type":"Triệu chứng","code":""}]}'
        },
        dictionary_provider=lambda _text: [],
    )
    assert record["extra_false_positives"] == 1
    assert record["extra_false_positives_by_type"] == {"Triệu chứng": 1}
