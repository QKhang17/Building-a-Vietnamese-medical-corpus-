from core.research_sampling import (
    assignment_modes,
    prepare_candidates,
    split_sample,
    stratified_sample,
)


def rows(count=650):
    return [
        {
            "id": index + 1,
            "title": f"Bài {index}",
            "abstract": ("Nội dung y khoa có độ dài đủ để tạo một tóm tắt hợp lệ. " * (2 + index % 5)) + str(index),
            "publication_year": 2018 + index % 7,
            "source_url": f"https://journal{index % 4}.example.org/article/{index}",
        }
        for index in range(count)
    ]


def test_sampling_is_reproducible_and_split_is_exact():
    candidates = prepare_candidates(rows())
    first = stratified_sample(candidates, 300, seed=42)
    second = stratified_sample(candidates, 300, seed=42)
    assert [item["id"] for item in first] == [item["id"] for item in second]
    split = split_sample(first, seed=42)
    counts = {name: sum(item["split_name"] == name for item in split) for name in ("pilot", "development", "test")}
    assert counts == {"pilot": 30, "development": 70, "test": 200}
    assert len({item["text_sha256"] for item in split}) == 300


def test_assignment_plan_has_two_annotators_and_one_adjudicator_per_document():
    sample = split_sample(stratified_sample(prepare_candidates(rows()), 300, seed=7), seed=7)
    plan = assignment_modes(sample, 1, 2, 3)
    assert len(plan) == 900
    development = [item for item in plan if next(row for row in sample if row["id"] == item["article_id"])["split_name"] == "development"]
    assert sum(item["expert_id"] == 1 and item["mode"] == "assisted" for item in development) == 35
    assert sum(item["expert_id"] == 2 and item["mode"] == "assisted" for item in development) == 35
    assert sum(item["role"] == "adjudicator" for item in plan) == 300
