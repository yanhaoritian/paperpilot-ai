from __future__ import annotations

from types import SimpleNamespace

from app.services.doc_context import build_context_snapshot, format_document_context_cards
from app.services.hybrid_retrieve import _best_chunk_for_document, rerank_chunks
from app.services.retrieve import RetrievedChunk
from app.models import Document


class _B:
    def __init__(self, role: str, text: str, page_start: int = 1):
        self.role = role
        self.text = text
        self.page_start = page_start


def _row(doc: str, name: str, cid: str, score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        document_id=doc,
        library_id="lib",
        file_name=name,
        text=f"text-{cid}",
        page_start=1,
        page_end=1,
        chunk_index=0,
        score=score,
    )


def test_build_context_snapshot_from_blocks():
    snap = build_context_snapshot(
        file_name="a.pdf",
        page_count=10,
        blocks=[
            _B("title", "申扎裂谷研究"),
            _B("abstract", "本文基于深反射数据…"),
            _B("section_heading", "1 引言"),
            _B("paragraph", "方法细节……"),
        ],
    )
    assert snap["file_name"] == "a.pdf"
    assert "申扎" in snap["preview"]
    assert "1 引言" in snap["sections"]


def test_rerank_preserves_each_document(monkeypatch):
    rows = [
        _row("d1", "cai.pdf", "c1", 0.9),
        _row("d1", "cai.pdf", "c2", 0.8),
        _row("d1", "cai.pdf", "c3", 0.7),
        _row("d1", "cai.pdf", "c4", 0.6),
        _row("d2", "zhang.pdf", "z1", 0.1),
        _row("d2", "zhang.pdf", "z2", 0.05),
    ]

    # Simulate LLM rerank that puts all Cai first and Zhang last
    def fake_chat_json(_messages, **_kwargs):
        return {"ordered_ids": ["c1", "c2", "c3", "c4", "z1", "z2"]}

    monkeypatch.setattr("app.services.hybrid_retrieve.chat_json", fake_chat_json)
    monkeypatch.setattr(
        "app.services.hybrid_retrieve.get_settings",
        lambda: type("S", (), {"rerank_provider": "llm", "rerank_top_n": 10})(),
    )

    out = rerank_chunks(
        "两篇共同点",
        rows,
        top_n=4,  # old bug: would keep only Cai
        preserve_doc_ids={"d1", "d2"},
        min_per_doc=2,
    )
    docs = {r.document_id for r in out}
    assert docs == {"d1", "d2"}
    assert sum(1 for r in out if r.document_id == "d2") >= 2
    assert len(out) >= 4


def test_format_document_context_cards():
    doc = Document(id="x", library_id="l", owner_id="u", file_name="a.pdf", file_path="p", file_hash="h")
    text = format_document_context_cards([(doc, {"preview": "hello", "page_count": 3, "sections": ["A"]})])
    assert "文献 Context 卡片" in text
    assert "a.pdf" in text
    assert "hello" in text


def test_document_coverage_fallback_keeps_filename_column():
    chunk = SimpleNamespace(
        id="c1",
        document_id="d1",
        library_id="l1",
        text="目标证据",
        page_start=2,
        page_end=2,
        chunk_index=0,
        embedding=None,
        section_path="Results",
        role="paragraph",
    )

    class _Rows:
        def all(self):
            return [(chunk, "paper.pdf")]

    class _Session:
        def execute(self, _statement):
            return _Rows()

    result = _best_chunk_for_document(
        _Session(),
        owner_id="u1",
        document_id="d1",
        question="目标证据",
        q_vec=None,
    )
    assert result is not None
    assert result.file_name == "paper.pdf"
    assert result.chunk_id == "c1"
