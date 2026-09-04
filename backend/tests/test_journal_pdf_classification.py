from pathlib import Path

from bs4 import BeautifulSoup

from core.scraper import (
    _classified_storage_dir,
    _classify_article_language,
    _publication_metadata,
    clean_filename,
)


def _soup(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, "html.parser")


def test_classifies_vietnamese_article_from_metadata():
    soup = _soup(
        '<html lang="en"><head>'
        '<meta name="citation_language" content="vi">'
        '</head></html>'
    )
    assert _classify_article_language(soup, "This fallback text is English.") == "Vietnamese"


def test_classifies_english_article_from_html_language():
    soup = _soup('<html lang="en-US"><head></head></html>')
    assert _classify_article_language(soup, "Bài viết dự phòng bằng tiếng Việt.") == "English"


def test_extracts_journal_year_volume_and_issue():
    soup = _soup(
        """
        <head>
          <meta name="citation_journal_title" content="Tạp chí Y học Việt Nam">
          <meta name="citation_publication_date" content="2024-08-15">
          <meta name="citation_volume" content="12">
          <meta name="citation_issue" content="3">
        </head>
        """
    )
    publication = _publication_metadata(soup, "fallback.example", "2023", "Số cũ")

    assert publication == {
        "journal": "Tạp chí Y học Việt Nam",
        "year": "2024",
        "issue": "Tập_12_Số_3",
        "volume": "12",
        "issue_number": "3",
    }


def test_metadata_fallback_and_classified_storage_path():
    publication = _publication_metadata(_soup("<html></html>"), "tapchi.vn", "2025", "Số 2")
    target = Path(_classified_storage_dir("PDF_ROOT", "Vietnamese", publication))

    assert publication["year"] == "2025"
    assert publication["issue"] == "Số 2"
    assert target.parts[-4:] == ("Vietnamese", "tapchi.vn", "2025", "Số 2")


def test_clean_filename_removes_windows_forbidden_characters():
    assert clean_filename('Tạp chí: Y học? Việt Nam. ') == "Tạp chí Y học Việt Nam"
