from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.services.doc_context import build_context_snapshot, format_document_context_cards
from app.services.hybrid_retrieve import (
    _best_chunk_for_document,
    comparison_evidence_coverage,
    ensure_document_coverage,
    format_comparison_evidence_coverage,
    rerank_chunks,
)
from app.services.retrieve import RetrievedChunk
from app.models import Chunk, Document, Library, User


class _B:
    def __init__(self, role: str, text: str, page_start: int = 1):
        self.role = role
        self.text = text
        self.page_start = page_start


def _row(
    doc: str,
    name: str,
    cid: str,
    score: float = 0.5,
    *,
    text: str | None = None,
    role: str | None = None,
    section_path: str | None = None,
    chunk_index: int = 0,
    page_start: int = 1,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        document_id=doc,
        library_id="lib",
        file_name=name,
        text=text or f"text-{cid}",
        page_start=page_start,
        page_end=page_start,
        chunk_index=chunk_index,
        score=score,
        section_path=section_path,
        role=role,
    )


@pytest.fixture()
def coverage_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    user = User(
        username="coverage-user",
        email="coverage@example.com",
        password_hash="test",
    )
    db.add(user)
    db.flush()
    library = Library(owner_id=user.id, name="Coverage library")
    db.add(library)
    db.flush()
    try:
        yield db, str(user.id), str(library.id)
    finally:
        db.close()
        engine.dispose()


def _add_ready_document(db, *, owner_id: str, library_id: str, doc_id: str) -> Document:
    doc = Document(
        id=doc_id,
        owner_id=owner_id,
        library_id=library_id,
        file_name=f"{doc_id}.pdf",
        file_path=f"/{doc_id}.pdf",
        file_hash=f"hash-{doc_id}",
        status="ready",
    )
    db.add(doc)
    db.flush()
    return doc


def _add_chunk(
    db,
    *,
    owner_id: str,
    library_id: str,
    document_id: str,
    chunk_id: str,
    chunk_index: int,
    text: str,
    role: str,
    section_path: str,
    page_start: int,
) -> None:
    db.add(
        Chunk(
            id=chunk_id,
            owner_id=owner_id,
            library_id=library_id,
            document_id=document_id,
            chunk_index=chunk_index,
            text=text,
            role=role,
            section_path=section_path,
            page_start=page_start,
            page_end=page_start,
            embedding=None,
        )
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


def test_three_document_coverage_replaces_heading_only_hit_with_real_evidence(
    coverage_db,
):
    db, owner_id, library_id = coverage_db
    for doc_id in ("d1", "d2", "d3"):
        _add_ready_document(
            db,
            owner_id=owner_id,
            library_id=library_id,
            doc_id=doc_id,
        )

    # The third paper initially contributes only the exact failure mode seen in
    # production: an isolated Introduction heading.
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="d3",
        chunk_id="d3-heading",
        chunk_index=0,
        text="Introduction",
        role="section_heading",
        section_path="Introduction",
        page_start=1,
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="d3",
        chunk_id="d3-method",
        chunk_index=1,
        text=(
            "Methods describe the experimental design, participant sampling, "
            "implementation protocol, and reproducible training procedure. " * 3
        ),
        role="paragraph",
        section_path="Methods",
        page_start=3,
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="d3",
        chunk_id="d3-data",
        chunk_index=2,
        text=(
            "The dataset contains cohort measurements, benchmark samples, and "
            "statistical data collected under the stated inclusion criteria. " * 3
        ),
        role="paragraph",
        section_path="Data",
        page_start=4,
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="d3",
        chunk_id="d3-result",
        chunk_index=3,
        text=(
            "Results report evaluation performance, accuracy, significant effects, "
            "and findings across every benchmark used in the experiment. " * 3
        ),
        role="paragraph",
        section_path="Results",
        page_start=7,
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="d3",
        chunk_id="d3-limit",
        chunk_index=4,
        text=(
            "Limitations discuss cohort bias, constraints, threats to validity, "
            "remaining weaknesses, and concrete directions for future work. " * 3
        ),
        role="paragraph",
        section_path="Limitations",
        page_start=9,
    )
    db.commit()

    long_method = (
        "Methods and experimental design describe the algorithm, sample selection, "
        "implementation protocol, and evaluation procedure in reproducible detail. " * 3
    )
    long_result = (
        "Results and findings report significant performance effects, accuracy, "
        "benchmark evaluation, confidence intervals, and robustness measurements. " * 3
    )
    initial = [
        _row(
            "d1",
            "d1.pdf",
            "d1-method",
            0.95,
            text=long_method,
            role="paragraph",
            section_path="Methods",
        ),
        _row(
            "d1",
            "d1.pdf",
            "d1-result",
            0.90,
            text=long_result,
            role="paragraph",
            section_path="Results",
            page_start=5,
        ),
        _row(
            "d2",
            "d2.pdf",
            "d2-method",
            0.85,
            text=long_method,
            role="paragraph",
            section_path="Methods",
        ),
        _row(
            "d2",
            "d2.pdf",
            "d2-result",
            0.80,
            text=long_result,
            role="paragraph",
            section_path="Results",
            page_start=5,
        ),
        _row(
            "d3",
            "d3.pdf",
            "d3-heading",
            0.99,
            text="Introduction",
            role="section_heading",
            section_path="Introduction",
        ),
    ]

    out = ensure_document_coverage(
        db,
        owner_id=owner_id,
        library_ids=[library_id],
        question="Compare methods, data, results, and limitations across all papers",
        rows=initial,
        q_vec=None,
        per_doc=4,
        top_k=6,
    )

    # d1/d2 only have the two synthetic input rows; d3 has four substantive
    # facets in the database. The requested four-row quota must not be clipped
    # back to the caller's smaller top_k.
    assert len(out) == 8
    assert {row.document_id for row in out} == {"d1", "d2", "d3"}
    third_paper = [row for row in out if row.document_id == "d3"]
    assert len(third_paper) == 4
    assert all(row.role not in {"title", "section_heading"} for row in third_paper)
    assert all(len(row.text) >= 80 for row in third_paper)
    assert comparison_evidence_coverage(third_paper)["d3"] == [
        "method",
        "data",
        "result",
        "limitation",
    ]


def test_document_coverage_fills_deficit_when_one_substantive_hit_exists(
    coverage_db,
):
    db, owner_id, library_id = coverage_db
    _add_ready_document(
        db,
        owner_id=owner_id,
        library_id=library_id,
        doc_id="single-doc",
    )
    result_text = (
        "Results report evaluation performance, significant findings, accuracy, "
        "confidence intervals, and robustness across the complete benchmark. " * 3
    )
    method_text = (
        "Methods explain the experimental design, dataset sampling procedure, model "
        "implementation, training protocol, and all reproducibility controls. " * 3
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="single-doc",
        chunk_id="existing-result",
        chunk_index=0,
        text=result_text,
        role="paragraph",
        section_path="Results",
        page_start=6,
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="single-doc",
        chunk_id="missing-method",
        chunk_index=1,
        text=method_text,
        role="paragraph",
        section_path="Methods",
        page_start=3,
    )
    db.commit()

    initial = [
        _row(
            "single-doc",
            "single-doc.pdf",
            "existing-result",
            0.99,
            text=result_text,
            role="paragraph",
            section_path="Results",
            page_start=6,
        )
    ]
    out = ensure_document_coverage(
        db,
        owner_id=owner_id,
        library_ids=[library_id],
        question="Compare methods and results",
        rows=initial,
        q_vec=None,
        per_doc=2,
        top_k=2,
    )

    assert {row.chunk_id for row in out} == {"existing-result", "missing-method"}
    assert comparison_evidence_coverage(out)["single-doc"] == ["method", "data", "result"]

    hint = format_comparison_evidence_coverage(
        out,
        expected_documents=[
            ("single-doc", "single-doc.pdf"),
            ("empty-ready-doc", "empty-ready-doc.pdf"),
        ],
    )
    assert "single-doc.pdf" in hint
    assert "方法" in hint and "数据/样本" in hint and "结果/指标" in hint
    assert "局限/讨论" in hint
    assert "证据缺口" in hint
    assert "empty-ready-doc.pdf" in hint
    assert "暂无明确维度标签" in hint


def test_document_coverage_repairs_facet_gaps_even_when_quota_is_full(
    coverage_db,
):
    db, owner_id, library_id = coverage_db
    _add_ready_document(
        db,
        owner_id=owner_id,
        library_id=library_id,
        doc_id="facet-gap-doc",
    )
    facet_rows = {
        "data": (
            "Data",
            "The dataset contains 12,000 samples, cohort measurements, field observations, "
            "and benchmark records collected under explicit inclusion criteria. " * 2,
        ),
        "result": (
            "Results",
            "Results show significant performance gains, evaluation findings, accuracy, "
            "and robust effects across all reported experiments. " * 2,
        ),
        "limitation": (
            "Limitations",
            "Limitations include sampling bias, uncertainty, constraints, threats to validity, "
            "and several directions for future work. " * 2,
        ),
    }
    for index, (facet, (section, text)) in enumerate(facet_rows.items(), 10):
        _add_chunk(
            db,
            owner_id=owner_id,
            library_id=library_id,
            document_id="facet-gap-doc",
            chunk_id=f"db-{facet}",
            chunk_index=index,
            text=text,
            role="paragraph",
            section_path=section,
            page_start=index,
        )
    db.commit()

    method_text = (
        "Methods describe the algorithm, implementation protocol, model architecture, "
        "and experimental procedure in reproducible detail. " * 3
    )
    initial = [
        _row(
            "facet-gap-doc",
            "facet-gap-doc.pdf",
            f"method-{index}",
            1.0 - index / 100,
            text=method_text,
            role="paragraph",
            section_path="Methods",
            chunk_index=index,
        )
        for index in range(4)
    ]

    out = ensure_document_coverage(
        db,
        owner_id=owner_id,
        library_ids=[library_id],
        question="Compare methods, data, results, and limitations",
        rows=initial,
        q_vec=None,
        per_doc=4,
        top_k=4,
    )

    assert len(out) == 4
    assert comparison_evidence_coverage(out)["facet-gap-doc"] == [
        "method",
        "data",
        "result",
        "limitation",
    ]
    assert {row.chunk_id for row in out} >= {
        "db-data",
        "db-result",
        "db-limitation",
    }


def test_broad_abstract_cannot_impersonate_four_independent_facets(coverage_db):
    db, owner_id, library_id = coverage_db
    _add_ready_document(
        db,
        owner_id=owner_id,
        library_id=library_id,
        doc_id="abstract-doc",
    )
    specific = {
        "method": (
            "Methods",
            "The methodology uses a finite-difference inversion algorithm and a "
            "reproducible processing protocol. " * 3,
        ),
        "data": (
            "Data",
            "The dataset contains field measurements from 8,200 samples and three independent benchmark cohorts. " * 3,
        ),
        "result": (
            "Results",
            "Results show significant performance improvements and robust evaluation findings across benchmarks. " * 3,
        ),
        "limitation": (
            "Limitations",
            "Limitations include sampling bias, uncertainty, threats to validity, "
            "and constraints on generalization. " * 3,
        ),
    }
    for index, (facet, (section, text)) in enumerate(specific.items(), 1):
        _add_chunk(
            db,
            owner_id=owner_id,
            library_id=library_id,
            document_id="abstract-doc",
            chunk_id=f"specific-{facet}",
            chunk_index=index,
            text=text,
            role="paragraph",
            section_path=section,
            page_start=index + 1,
        )
    db.commit()

    abstract_text = (
        "This abstract summarizes the method, dataset, evaluation results, significant "
        "findings, limitations, constraints, and future work. " * 4
    )
    initial = [
        _row(
            "abstract-doc",
            "abstract-doc.pdf",
            f"abstract-{index}",
            1.0 - index / 100,
            text=abstract_text,
            role="abstract",
            section_path="Abstract",
            chunk_index=index,
        )
        for index in range(4)
    ]

    out = ensure_document_coverage(
        db,
        owner_id=owner_id,
        library_ids=[library_id],
        question="Compare methods, data, results, and limitations",
        rows=initial,
        q_vec=None,
        per_doc=4,
        top_k=4,
    )

    assert {row.chunk_id for row in out} == {
        "specific-method",
        "specific-data",
        "specific-result",
        "specific-limitation",
    }


def test_document_coverage_safely_falls_back_for_heading_only_index(coverage_db):
    db, owner_id, library_id = coverage_db
    _add_ready_document(
        db,
        owner_id=owner_id,
        library_id=library_id,
        doc_id="sparse-doc",
    )
    _add_chunk(
        db,
        owner_id=owner_id,
        library_id=library_id,
        document_id="sparse-doc",
        chunk_id="only-heading",
        chunk_index=0,
        text="Introduction",
        role="section_heading",
        section_path="Introduction",
        page_start=1,
    )
    db.commit()

    out = ensure_document_coverage(
        db,
        owner_id=owner_id,
        library_ids=[library_id],
        question="Compare this paper",
        rows=[],
        q_vec=None,
        per_doc=2,
        top_k=2,
    )

    assert [row.chunk_id for row in out] == ["only-heading"]
    assert comparison_evidence_coverage(out) == {"sparse-doc": []}
