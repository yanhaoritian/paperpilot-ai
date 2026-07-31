from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Block, Chunk, Document, Library, User
from app.services import pdf_tools


def _scope_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    user = User(username="scope-user", email="scope@example.com", password_hash="x")
    db.add(user)
    db.flush()
    lib_a = Library(owner_id=user.id, name="A")
    lib_b = Library(owner_id=user.id, name="B")
    db.add_all([lib_a, lib_b])
    db.flush()
    doc = Document(
        library_id=lib_b.id,
        owner_id=user.id,
        file_name="b.pdf",
        file_path="missing.pdf",
        file_hash="b" * 64,
        status="ready",
        page_count=2,
    )
    db.add(doc)
    db.flush()
    block = Block(
        document_id=doc.id,
        library_id=lib_b.id,
        owner_id=user.id,
        role="paragraph",
        text="selected-library evidence",
        page_start=1,
        page_end=1,
    )
    chunk = Chunk(
        document_id=doc.id,
        library_id=lib_b.id,
        owner_id=user.id,
        chunk_index=1,
        text="selected-library evidence",
        page_start=1,
        page_end=1,
    )
    db.add_all([block, chunk])
    db.commit()
    return engine, db, user, lib_a, lib_b, doc, chunk


def test_page_and_quote_tools_enforce_selected_libraries():
    engine, db, user, lib_a, lib_b, doc, chunk = _scope_db()
    try:
        denied = pdf_tools.read_page(
            db,
            owner_id=user.id,
            document_id=doc.id,
            page=1,
            library_ids=[lib_a.id],
        )
        assert denied["ok"] is False
        allowed = pdf_tools.read_page(
            db,
            owner_id=user.id,
            document_id=doc.id,
            page=1,
            library_ids=[lib_b.id],
        )
        assert allowed["ok"] is True
        denied_quote = pdf_tools.quote_source(
            db,
            owner_id=user.id,
            chunk_id=chunk.id,
            library_ids=[lib_a.id],
        )
        assert denied_quote["ok"] is False
    finally:
        db.close()
        engine.dispose()


def test_followup_planner_rejects_hallucinated_ids(monkeypatch):
    monkeypatch.setattr(
        pdf_tools,
        "get_settings",
        lambda: SimpleNamespace(openai_api_key="test"),
    )
    monkeypatch.setattr(
        pdf_tools,
        "chat_json",
        lambda *_args, **_kwargs: {
            "steps": [
                {
                    "name": "read_page",
                    "arguments": {"document_id": "hallucinated", "page": 1},
                },
                {
                    "name": "read_page",
                    "arguments": {"document_id": "doc-1", "page": 3},
                },
                {
                    "name": "quote_source",
                    "arguments": {"chunk_id": "not-a-hit"},
                },
            ]
        },
    )
    steps = pdf_tools.plan_followup_tools(
        "核对原文",
        {
            "ok": True,
            "hits": [
                {
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "page_start": 3,
                }
            ],
        },
        remaining_steps=3,
    )
    assert steps == [
        {
            "name": "read_page",
            "arguments": {"document_id": "doc-1", "page": 3},
        }
    ]
