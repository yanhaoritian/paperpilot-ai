from __future__ import annotations

from types import SimpleNamespace

from app.services.acl import collection_key
from app.services.sparse_bm25 import bm25_rank, tokenize
from app.services.retrieve import RetrievedChunk


def test_collection_key():
    assert collection_key("u1", "l1") == "u1:l1"


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
