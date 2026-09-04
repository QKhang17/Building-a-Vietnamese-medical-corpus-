import json

from bs4 import BeautifulSoup

from core.tamanh_scraper import _department_links, _listing_links, _parse_detail


def test_discovers_departments_and_listing_links():
    root = BeautifulSoup(
        '<a class="title_catetuvan" href="/tu-van/tim-mach/">Tim mạch</a>',
        "html.parser",
    )
    assert _department_links(root) == [
        ("https://tamanhhospital.vn/tu-van/tim-mach/", "Tim mạch")
    ]

    listing = BeautifulSoup(
        '<div class="item_tuvan"><a href="/tu-van/cau-hoi-a/"><h2>Câu hỏi A</h2></a></div>',
        "html.parser",
    )
    assert _listing_links(listing, "https://tamanhhospital.vn/tu-van/tim-mach/") == [
        "https://tamanhhospital.vn/tu-van/cau-hoi-a/"
    ]


def test_parses_qapage_date_question_and_doctor_answer():
    qapage = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "QAPage",
                "mainEntity": {
                    "@type": "Question",
                    "name": "Tôi bị đau ngực thì nên làm gì?",
                    "text": "Đau ngực",
                    "dateCreated": "2025-08-10T09:00:00+07:00",
                    "author": {"name": "Người bệnh"},
                    "acceptedAnswer": {"@type": "Answer", "text": "Câu trả lời dự phòng"},
                },
            }
        ],
    }
    markup = f"""
    <script type="application/ld+json">{json.dumps(qapage)}</script>
    <section class="box_detail">
      <h1>Đau ngực cần khám khoa nào?</h1>
      <a class="box_cgia_live"><img alt="BS. Nguyễn Văn A"></a>
      <div id="ftwp-postcontent">
        <div id="ftwp-container-outer">Mục lục phải bỏ</div>
        <p>Người bệnh nên đến cơ sở y tế để được kiểm tra.</p>
      </div>
    </section>
    """
    result = _parse_detail(
        markup,
        "https://tamanhhospital.vn/tu-van/dau-nguc/",
        "Tim mạch",
    )
    assert result["year"] == 2025
    assert result["question"] == "Tôi bị đau ngực thì nên làm gì?"
    assert result["doctor"] == "BS. Nguyễn Văn A"
    assert result["answer"] == "Người bệnh nên đến cơ sở y tế để được kiểm tra."
    assert "Mục lục" not in result["answer"]

