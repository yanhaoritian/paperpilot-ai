from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Document, IndexJob, Library, User, WorkerHeartbeat
from app.services import index_recovery, index_worker, indexing
from app import worker


def test_retry_loop_retries_pending_document(monkeypatch):
    doc = SimpleNamespace(status="pending", index_attempts=0)
    calls: list[int] = []

    class FakeSession:
        def expire_all(self):
            return None

        def get(self, _model, _document_id):
            return doc

    def fake_index_document(_db, _document_id):
        calls.append(1)
        doc.index_attempts += 1
        doc.status = "ready" if len(calls) == 2 else "pending"

    monkeypatch.setattr(indexing, "index_document", fake_index_document)
    monkeypatch.setattr(
        indexing,
        "get_settings",
        lambda: SimpleNamespace(index_max_attempts=3),
    )
    monkeypatch.setattr(indexing.time, "sleep", lambda _seconds: None)

    indexing.index_document_with_retries(FakeSession(), "doc-1")
    assert len(calls) == 2


def test_thread_pool_schedule_deduplicates_document(monkeypatch):
    submitted: list[str] = []

    class FakeExecutor:
        def submit(self, _fn, document_id):
            submitted.append(document_id)

    monkeypatch.setattr(index_worker, "_get_executor", lambda: FakeExecutor())
    monkeypatch.setattr(
        index_worker,
        "get_settings",
        lambda: SimpleNamespace(index_use_thread_pool=True),
    )
    with index_worker._scheduled_lock:
        index_worker._scheduled_ids.clear()
    try:
        index_worker.schedule_index("doc-1")
        index_worker.schedule_index("doc-1")
        assert submitted == ["doc-1"]
    finally:
        with index_worker._scheduled_lock:
            index_worker._scheduled_ids.clear()


def test_external_worker_claims_job_once(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    try:
        user = User(username="worker-user", email="worker@example.com", password_hash="x")
        db.add(user)
        db.flush()
        lib = Library(owner_id=user.id, name="worker-lib")
        db.add(lib)
        db.flush()
        doc = Document(
            library_id=lib.id,
            owner_id=user.id,
            file_name="worker.pdf",
            file_path="worker.pdf",
            file_hash="a" * 64,
            status="pending",
        )
        db.add(doc)
        db.flush()
        job = IndexJob(document_id=doc.id, owner_id=user.id, status="pending")
        db.add(job)
        db.commit()
        document_id = doc.id
        job_id = job.id
    finally:
        db.close()

    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(
            is_sqlite=True,
            index_max_attempts=3,
            index_worker_lease_seconds=90,
            index_worker_heartbeat_seconds=15,
        ),
    )

    assert worker._claim_next_job() == document_id
    assert worker._claim_next_job() is None
    verify = Session()
    try:
        claimed = verify.get(IndexJob, job_id)
        assert claimed.status == "running"
        assert claimed.lease_owner == worker._worker_id
        assert claimed.lease_expires_at is not None
        assert verify.get(Document, document_id).status == "processing"
    finally:
        verify.close()
        engine.dispose()


def test_recovery_does_not_steal_active_lease(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()
    try:
        user = User(username="lease-user", email="lease@example.com", password_hash="x")
        db.add(user)
        db.flush()
        lib = Library(owner_id=user.id, name="lease-lib")
        db.add(lib)
        db.flush()
        doc = Document(
            library_id=lib.id,
            owner_id=user.id,
            file_name="lease.pdf",
            file_path="lease.pdf",
            file_hash="b" * 64,
            status="processing",
            updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.add(doc)
        db.flush()
        job = IndexJob(
            document_id=doc.id,
            owner_id=user.id,
            status="running",
            lease_owner="live-worker",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        db.add(job)
        db.commit()
        document_id = doc.id
        job_id = job.id
    finally:
        db.close()

    monkeypatch.setattr(index_recovery, "SessionLocal", Session)
    monkeypatch.setattr(
        index_recovery,
        "get_settings",
        lambda: SimpleNamespace(
            is_sqlite=True,
            index_stale_minutes=30,
            index_max_attempts=3,
        ),
    )

    assert index_recovery.reclaim_and_retry_indexing(execute=False) == 0
    verify = Session()
    try:
        assert verify.get(Document, document_id).status == "processing"
        assert verify.get(IndexJob, job_id).status == "running"
    finally:
        verify.close()

    expire = Session()
    try:
        leased_job = expire.get(IndexJob, job_id)
        leased_job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        expire.commit()
    finally:
        expire.close()

    assert index_recovery.reclaim_and_retry_indexing(execute=False) == 1
    verify = Session()
    try:
        assert verify.get(Document, document_id).status == "pending"
        reclaimed_job = verify.get(IndexJob, job_id)
        assert reclaimed_job.status == "pending"
        assert reclaimed_job.lease_owner is None
        assert reclaimed_job.lease_expires_at is None
    finally:
        verify.close()
        engine.dispose()


def test_worker_heartbeat_lifecycle(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(worker, "_worker_id", "test-worker")

    worker._write_heartbeat()
    verify = Session()
    try:
        row = verify.get(WorkerHeartbeat, "test-worker")
        assert row is not None
        assert row.worker_kind == "index"
    finally:
        verify.close()

    worker._remove_heartbeat()
    verify = Session()
    try:
        assert verify.get(WorkerHeartbeat, "test-worker") is None
    finally:
        verify.close()
        engine.dispose()
