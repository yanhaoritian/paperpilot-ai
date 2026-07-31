from __future__ import annotations

from types import SimpleNamespace

from app.services.acl import collection_key
from app.services.hybrid_retrieve import _keyword_candidates
from app.services.sparse_bm25 import bm25_rank, tokenize
from app.services.retrieve import RetrievedChunk
from app.services.vector_store.pgvector_store import _score_from_distance


def test_collection_key():
    assert collection_key("u1", "l1") == "u1:l1"


def test_pgvector_exact_match_keeps_full_score():
    assert _score_from_distance(0.0) == 1.0
    assert _score_from_distance(None) == 0.0


def test_tokenize_cjk_and_latin():
    toks = tokenize("面波 imaging 申扎")
    assert "imaging" in toks
    assert any("申" in t or "扎" in t or t == "申扎" or len(t) == 2 for t in toks)


def test_bm25_ranks_relevant_chunk_higher():
    rows = [
        (SimpleNamespace(section_path="摘要", text="深反射地震 面波 频散"), "a.pdf"),
        (SimpleNamespace(section_path="其他", text="今日天气晴朗适合出游"), "b.pdf"),
    ]

    def to_retrieved(chunk, file_name, score):
        return RetrievedChunk(
            chunk_id=file_name,
            document_id="d",
            library_id="l",
            file_name=file_name,
            text=chunk.text,
            page_start=1,
            page_end=1,
            chunk_index=0,
            score=score,
        )

    out = bm25_rank("面波频散曲线", rows, top_n=2, to_retrieved=to_retrieved)
    assert out
    assert out[0].file_name == "a.pdf"
    assert out[0].score > (out[1].score if len(out) > 1 else 0)


def test_postgres_sparse_path_uses_trigram_knn_prefilter(monkeypatch):
    chunk = SimpleNamespace(
        id="c1",
        document_id="d1",
        library_id="l1",
        text="面波频散曲线",
        page_start=1,
        page_end=1,
        chunk_index=0,
        section_path="结果",
        role="paragraph",
    )

    class _Rows:
        def all(self):
            return [(chunk, "paper.pdf")]

    class _Session:
        statement = None

        def execute(self, statement):
            self.statement = statement
            return _Rows()

        def rollback(self):
            raise AssertionError("trigram query should not fall back")

    settings = SimpleNamespace(
        is_sqlite=False,
        postgres_trigram_enabled=True,
        postgres_trigram_candidate_cap=100,
        bm25_candidate_cap=8000,
        bm25_enabled=True,
    )
    monkeypatch.setattr(
        "app.services.hybrid_retrieve.get_settings",
        lambda: settings,
    )
    session = _Session()
    rows = _keyword_candidates(
        session,
        owner_id="u1",
        library_ids=["l1"],
        question="面波频散",
        top_n=5,
    )
    assert rows
    assert rows[0].file_name == "paper.pdf"
    assert "<->" in str(session.statement)
