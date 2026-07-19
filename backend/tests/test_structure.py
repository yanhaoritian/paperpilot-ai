from app.services.pdf_parse import _classify_page
from app.services.structure import restore_structure
from app.services.pdf_parse import PageBundle, ParseResult


def test_classify_page_routes():
    assert _classify_page(200, 0.1, 40, 0.35) == "digital"
    assert _classify_page(5, 0.1, 40, 0.35) == "scan"
    assert _classify_page(5, 0.5, 40, 0.35) == "vision"
    assert _classify_page(80, 0.5, 40, 0.35) == "vision"


def test_restore_structure_pages():
    parse = ParseResult(
        pages=[
            PageBundle(page_no=1, route="digital", text="Abstract\n\nWe propose a method.", char_count=30),
            PageBundle(page_no=2, route="digital", text="1. Introduction\n\nMore details here.", char_count=40),
        ],
        page_count=2,
        full_text="...",
        routes_used={"digital": 2},
    )
    blocks = restore_structure(parse, file_name="demo.pdf")
    assert blocks
    assert all(b.page_start >= 1 for b in blocks)
    assert any(b.role in {"section_heading", "paragraph", "title"} for b in blocks)
