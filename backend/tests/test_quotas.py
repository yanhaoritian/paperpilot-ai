from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Library, User, UsageDaily
from app.services import quotas


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    user = User(username="u1", email="u1@example.com", password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)

    class S:
        quota_daily_queries = 2
        quota_daily_uploads = 1

    monkeypatch.setattr(quotas, "get_settings", lambda: S())
    yield session, user.id
    session.close()


def test_query_quota_blocks_after_limit(db):
    session, uid = db
    quotas.consume_query_quota(session, uid)
    quotas.consume_query_quota(session, uid)
    with pytest.raises(HTTPException) as ei:
        quotas.consume_query_quota(session, uid)
    assert ei.value.status_code == 429


def test_upload_quota_blocks_after_limit(db):
    session, uid = db
    quotas.consume_upload_quota(session, uid)
    with pytest.raises(HTTPException) as ei:
        quotas.consume_upload_quota(session, uid)
    assert ei.value.status_code == 429


def test_quota_status_shape(db):
    session, uid = db
    st = quotas.quota_status(session, uid)
    assert st["queries_limit"] == 2
    assert st["uploads_limit"] == 1
    assert "day" in st


def test_query_quota_can_join_callers_transaction(db):
    session, uid = db
    library = Library(owner_id=uid, name="rolled back")
    session.add(library)
    session.flush()
    quotas.consume_query_quota(session, uid, commit=False)
    session.rollback()
    assert session.query(Library).filter_by(name="rolled back").first() is None
    assert session.query(UsageDaily).filter_by(user_id=uid).first() is None
