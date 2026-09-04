from core.ai_label import _merge_ai_and_dictionary
from api import routes


def test_dictionary_metadata_is_authoritative_and_keeps_all_spans():
    text = "Đột quỵ não cần theo dõi; tiền sử đột quỵ não."
    dictionary = [
        {
            "term": "Đột quỵ não",
            "start": 0,
            "end": 12,
            "code": "I64",
            "label_vn": "Đột quỵ, không xác định do xuất huyết hay nhồi máu",
            "dictionary_type": "Bệnh Lý",
            "matched_by": "alias",
        },
        {
            "term": "đột quỵ não",
            "start": 37,
            "end": 49,
            "code": "I64",
            "label_vn": "Đột quỵ, không xác định do xuất huyết hay nhồi máu",
            "dictionary_type": "Bệnh Lý",
            "matched_by": "alias",
        },
    ]

    result = _merge_ai_and_dictionary(
        text,
        {"Triệu chứng": ["đột quỵ não"]},
        dictionary,
    )

    assert result["Triệu chứng"] == []
    assert len(result["Bệnh lý"]) == 1
    entity = result["Bệnh lý"][0]
    assert entity["code"] == "I64"
    assert entity["label_vn"] == "Đột quỵ, không xác định do xuất huyết hay nhồi máu"
    assert entity["source"] == "ai+dictionary"
    assert entity["spans"] == [{"start": 0, "end": 12}, {"start": 37, "end": 49}]


def test_hallucinated_ai_term_is_rejected():
    result = _merge_ai_and_dictionary(
        "Bệnh nhân đau đầu.",
        {"Bệnh lý": ["tăng huyết áp"], "Triệu chứng": ["đau đầu"]},
        [],
    )

    assert result["Bệnh lý"] == []
    assert result["Triệu chứng"][0]["term"] == "đau đầu"
    assert result["Triệu chứng"][0]["spans"] == [{"start": 10, "end": 17}]


def test_dictionary_result_survives_empty_ai_output():
    text = "Bệnh nhân tăng huyết áp."
    dictionary = [
        {
            "term": "tăng huyết áp",
            "start": 10,
            "end": 24,
            "code": "I10",
            "label_vn": "Tăng huyết áp nguyên phát",
            "dictionary_type": "Bệnh Lý",
            "matched_by": "exact",
        }
    ]

    result = _merge_ai_and_dictionary(text, {}, dictionary)

    assert result["Bệnh lý"][0]["code"] == "I10"
    assert result["Bệnh lý"][0]["source"] == "ai+dictionary"


def test_ai_label_endpoint_creates_preview_without_saving(monkeypatch):
    preview = {
        "Bệnh lý": [{"term": "tăng huyết áp", "spans": [{"start": 0, "end": 13}]}],
        "Triệu chứng": [],
        "Điều trị": [],
        "Xét nghiệm": [],
        "Hình ảnh": [],
        "Sinh lý": [],
    }
    monkeypatch.setattr(routes, "extract_with_ai_label", lambda _text: preview.copy())
    monkeypatch.setattr(
        routes,
        "save_ai_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Không được tự lưu")),
    )

    result = routes.ai_label_endpoint(routes.AiLabelRequest(text="tăng huyết áp", article_id=7))

    assert result["saved_for_expert_review"] is False
    assert result["model"] == "gemini-2.5-flash"


def test_save_ai_label_endpoint_saves_only_after_confirmation(monkeypatch):
    class FakeCursor:
        def execute(self, _query, _params):
            pass

        def fetchone(self):
            return (1,)

        def close(self):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    captured = {}
    monkeypatch.setattr(routes, "_get_conn", lambda: FakeConnection())

    def fake_save(article_id, result):
        captured["article_id"] = article_id
        captured["result"] = result
        return True

    monkeypatch.setattr(routes, "save_ai_result", fake_save)
    request = routes.SaveAiLabelRequest(
        article_id=7,
        result={
            "Bệnh lý": [{"term": "tăng huyết áp"}],
            "Triệu chứng": [],
            "Điều trị": [],
            "Xét nghiệm": [],
            "Hình ảnh": [],
            "Sinh lý": [],
            "saved_for_expert_review": False,
        },
    )

    response = routes.save_ai_label_result_endpoint(request)

    assert response["entities_saved"] == 1
    assert response["saved_for_expert_review"] is True
    assert captured["article_id"] == 7
    assert "saved_for_expert_review" not in captured["result"]


def test_latest_ai_label_result_can_restore_saved_snapshot(monkeypatch):
    saved = {
        "article_id": 7,
        "result_json": {
            "Bệnh lý": [{"term": "tăng huyết áp"}],
            "Triệu chứng": [],
        },
        "created_at": "2026-08-23T10:30:00",
    }
    monkeypatch.setattr(routes, "get_ai_results", lambda article_ids: {article_ids[0]: saved})

    response = routes.get_latest_ai_label_result_endpoint(7)

    assert response["article_id"] == 7
    assert response["result"]["Bệnh lý"][0]["term"] == "tăng huyết áp"
    assert response["created_at"] == "2026-08-23T10:30:00"
