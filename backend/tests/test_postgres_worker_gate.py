from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import worker
from app.config import get_settings
from app.db import SessionLocal
from app.models import Document, IndexJob, Library, User
from app.services.index_recovery import reclaim_and_retry_indexing


pytestmark = pytest.mark.skipif(
    os.getenv("POSTGRES_GATE_TEST") != "1",
    reason="requires an isolated migrated PostgreSQL gate database",
)


def _create_owner_with_jobs(count: int) -> tuple[str, list[str]]:
    token = uuid4().hex
    db = SessionLocal()
    try:
        user = User(
            username=f"pg-gate-{token}",
            email=f"pg-gate-{token}@example.com",
            password_hash="test-only",
        )
        db.add(user)
        db.flush()
        library = Library(owner_id=user.id, name="Postgres worker gate")
        db.add(library)
        db.flush()
        document_ids: list[str] = []
        for index in range(count):
            document = Document(
                library_id=library.id,
                owner_id=user.id,
                file_name=f"gate-{index}.pdf",
                file_path=f"/gate-{token}-{index}.pdf",
                file_hash=f"{index:064x}",
                status="pending",
            )
            db.add(document)
            db.flush()
            db.add(
                IndexJob(
                    document_id=document.id,
                    owner_id=user.id,
                    status="pending",
                )
            )
            document_ids.append(str(document.id))
        db.commit()
        return str(user.id), document_ids
    finally:
        db.close()


def _delete_owner(user_id: str) -> None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is not None:
            db.delete(user)
            db.commit()
    finally:
        db.close()


def test_postgres_workers_claim_each_job_once():
    assert not get_settings().is_sqlite
    user_id, expected_ids = _create_owner_with_jobs(8)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = list(pool.map(lambda _index: worker._claim_next_job(), range(8)))

        assert None not in claimed
        assert len(set(claimed)) == len(expected_ids)
        assert set(claimed) == set(expected_ids)

        db = SessionLocal()
        try:
            jobs = list(
                db.scalars(
                    select(IndexJob).where(IndexJob.owner_id == user_id)
                ).all()
            )
            assert len(jobs) == len(expected_ids)
            assert all(job.status == "running" for job in jobs)
            assert all(job.lease_owner == worker._worker_id for job in jobs)
            assert all(job.lease_expires_at is not None for job in jobs)
        finally:
            db.close()
    finally:
        _delete_owner(user_id)


def test_postgres_recovery_respects_and_expires_leases():
    assert not get_settings().is_sqlite
    user_id, document_ids = _create_owner_with_jobs(2)
    active_id, expired_id = document_ids
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        documents = {
            str(document.id): document
            for document in db.scalars(
                select(Document).where(Document.owner_id == user_id)
            ).all()
        }
        jobs = {
            str(job.document_id): job
            for job in db.scalars(
                select(IndexJob).where(IndexJob.owner_id == user_id)
            ).all()
        }
        for document in documents.values():
            document.status = "processing"
            document.updated_at = now - timedelta(hours=2)
        jobs[active_id].status = "running"
        jobs[active_id].lease_owner = "active-worker"
        jobs[active_id].lease_expires_at = now + timedelta(minutes=5)
        jobs[expired_id].status = "running"
        jobs[expired_id].lease_owner = "dead-worker"
        jobs[expired_id].lease_expires_at = now - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    try:
        assert reclaim_and_retry_indexing(execute=False) == 1
        verify = SessionLocal()
        try:
            active_document = verify.get(Document, active_id)
            expired_document = verify.get(Document, expired_id)
            active_job = verify.scalar(
                select(IndexJob).where(IndexJob.document_id == active_id)
            )
            expired_job = verify.scalar(
                select(IndexJob).where(IndexJob.document_id == expired_id)
            )
            assert active_document.status == "processing"
            assert active_job.status == "running"
            assert active_job.lease_owner == "active-worker"
            assert expired_document.status == "pending"
            assert expired_job.status == "pending"
            assert expired_job.lease_owner is None
            assert expired_job.lease_expires_at is None
        finally:
            verify.close()
    finally:
        _delete_owner(user_id)
