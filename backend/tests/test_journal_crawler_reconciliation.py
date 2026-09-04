from pathlib import Path

import pymupdf

from core import scraper


class FakeResponse:
    def __init__(self, text="", body=b"", status=200, content_type="text/html"):
        self.text = text
        self._body = body
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        del chunk_size
        yield self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **kwargs):
        del kwargs
        if url not in self.responses:
            raise AssertionError(f"Unexpected URL: {url}")
        return self.responses[url]


def archive_page(year, issue_id, title):
    return f'<h2>{year}</h2><a href="/index.php/j/issue/view/{issue_id}">{title}</a>'


def issue_page(*article_ids):
    links = "".join(
        f'<a href="/index.php/j/article/view/{article_id}">article</a>'
        for article_id in article_ids
    )
    return f"<h1>Issue title</h1>{links}"


def test_discovery_scans_past_page_without_target_year_and_deduplicates_articles():
    archive = "https://journal.test/index.php/j/issue/archive"
    base = "https://journal.test/index.php/j"
    responses = {
        archive: FakeResponse(archive_page(2025, 10, "Tập 10 (2025)")),
        f"{archive}/2": FakeResponse(archive_page(2024, 9, "Tập 9 (2024)")),
        f"{archive}/3": FakeResponse(archive_page(2023, 8, "Tập 8 (2023)")),
        f"{archive}/4": FakeResponse(archive_page(2024, 7, "Tập 7 (2024)")),
        f"{archive}/5": FakeResponse("<p>Hết dữ liệu</p>"),
        "https://journal.test/index.php/j/issue/view/10": FakeResponse(issue_page(1, 2, 2)),
        "https://journal.test/index.php/j/issue/view/9": FakeResponse(issue_page(3)),
        "https://journal.test/index.php/j/issue/view/7": FakeResponse(issue_page(2, 4)),
    }

    manifest = scraper._discover_journal_manifest(
        FakeSession(responses), archive, base, 2024, 2025
    )

    assert len(manifest["issues"]) == 3
    assert len(manifest["articles"]) == 4
    assert manifest["articles"]["https://journal.test/index.php/j/article/view/4"]["archive_year"] == 2024


def test_second_discovery_can_reveal_article_added_after_first_pass():
    archive = "https://journal.test/index.php/j/issue/archive"
    base = "https://journal.test/index.php/j"
    common = {
        archive: FakeResponse(archive_page(2025, 10, "Tập 10 (2025)")),
        f"{archive}/2": FakeResponse("<p>Hết dữ liệu</p>"),
    }
    first = scraper._discover_journal_manifest(
        FakeSession({**common, "https://journal.test/index.php/j/issue/view/10": FakeResponse(issue_page(1))}),
        archive, base, 2025, 2025,
    )
    second = scraper._discover_journal_manifest(
        FakeSession({**common, "https://journal.test/index.php/j/issue/view/10": FakeResponse(issue_page(1, 2))}),
        archive, base, 2025, 2025,
    )

    assert set(second["articles"]) - set(first["articles"]) == {
        "https://journal.test/index.php/j/article/view/2"
    }


def test_download_pdf_rejects_html_even_when_server_labels_it_pdf(tmp_path):
    target = tmp_path / "bad.pdf"
    status, error = scraper._download_pdf(
        FakeSession({"https://journal.test/bad": FakeResponse(body=b"<html>bad</html>", content_type="application/pdf")}),
        "https://journal.test/bad",
        target,
    )

    assert status == "download_failed"
    assert "không phải PDF hợp lệ" in error
    assert not target.exists()
    assert not target.with_suffix(".pdf.part").exists()


def test_download_pdf_accepts_valid_document_and_renames_atomically(tmp_path):
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Valid journal PDF")
    payload = document.tobytes()
    document.close()
    target = tmp_path / "article.pdf"

    status, error = scraper._download_pdf(
        FakeSession({"https://journal.test/good": FakeResponse(body=payload, content_type="application/pdf")}),
        "https://journal.test/good",
        target,
    )

    assert status == "saved"
    assert error is None
    assert scraper._is_valid_pdf(target)
    assert not target.with_suffix(".pdf.part").exists()


def test_existing_asset_prefers_stable_article_id(tmp_path):
    root = Path(tmp_path)
    expected = root / "Vietnamese" / "2025" / "0123_Bài báo.pdf"
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"legacy")

    found = scraper._existing_asset(None, root, 123, "Tiêu đề đã thay đổi", ".pdf")

    assert found == expected.resolve()
