from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_STORAGE_DIR", str(tmp_path / "pdfs"))
    monkeypatch.setenv("AUTH_EXPOSE_CODE", "1")
    monkeypatch.setenv("JWT_REQUIRE_STRONG", "0")
    monkeypatch.setenv("INDEX_RECOVER_ON_STARTUP", "0")
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("SMTP_FROM", "")
    get_settings.cache_clear()
    from app.db import init_db
    from app.main import create_app

    settings = get_settings()
    settings.pdf_storage_dir = str(tmp_path / "pdfs")
    settings.auth_expose_code = True
    settings.jwt_require_strong = False
    settings.index_recover_on_startup = False
    settings.smtp_host = ""
    settings.smtp_user = ""
    settings.smtp_password = ""
    settings.smtp_from = ""
    init_db()
    app = create_app()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _register_headers(client: TestClient, tag: str) -> tuple[dict[str, str], str]:
    email = f"{tag}_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post(
        "/api/auth/send-code",
        json={"channel": "email", "target": email},
    ).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"{tag}_{uuid.uuid4().hex[:6]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    return headers, user_id


def test_register_with_email_code_and_login(client: TestClient):
    email = f"u_{uuid.uuid4().hex[:8]}@example.com"
    code_resp = client.post("/api/auth/send-code", json={"channel": "email", "target": email})
    assert code_resp.status_code == 200, code_resp.text
    code = code_resp.json()["dev_code"]
    assert code

    username = f"user_{uuid.uuid4().hex[:6]}"
    reg = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    )
    assert reg.status_code == 200, reg.text
    token = reg.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email

    login = client.post("/api/auth/login", json={"account": email, "password": "secret12"})
    assert login.status_code == 200


def test_research_skills_and_usage_summary_are_user_scoped(client: TestClient):
    email = f"research_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post(
        "/api/auth/send-code",
        json={"channel": "email", "target": email},
    ).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"research_{uuid.uuid4().hex[:6]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]

    from app.services.usage_tracking import record_ai_usage, usage_scope

    with usage_scope(user_id=user_id, skill_id="paper_qa"):
        record_ai_usage(
            operation="answer_generate",
            provider=f"api-test-{uuid.uuid4().hex[:6]}",
            model="test-model",
            input_tokens=10,
            output_tokens=2,
            total_tokens=12,
        )

    skills = client.get("/api/research/skills", headers=headers)
    assert skills.status_code == 200, skills.text
    ids = {row["id"] for row in skills.json()}
    assert "paper_qa" in ids
    assert "multi_paper_synthesis" in ids

    usage = client.get("/api/research/usage?days=30", headers=headers)
    assert usage.status_code == 200, usage.text
    payload = usage.json()
    assert payload["total_tokens"] == 12
    assert payload["provider_reported_events"] == 1
    assert payload["local_events"] == 0
    assert payload["failed_events"] == 0
    assert payload["unpriced_events"] == 1
    assert payload["costs"] == []
    assert payload["by_skill"][0]["key"] == "paper_qa"


def test_library_isolation(client: TestClient):
    def make_user(tag: str):
        email = f"{tag}_{uuid.uuid4().hex[:6]}@example.com"
        code = client.post("/api/auth/send-code", json={"channel": "email", "target": email}).json()["dev_code"]
        username = f"{tag}_{uuid.uuid4().hex[:5]}"
        token = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "secret12",
                "code": code,
                "channel": "email",
                "email": email,
            },
        ).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    headers_a = make_user("a")
    headers_b = make_user("b")
    lib_a = client.post("/api/libraries", headers=headers_a, json={"name": "A库"}).json()["id"]
    assert all(item["id"] != lib_a for item in client.get("/api/libraries", headers=headers_b).json())
    assert client.delete(f"/api/libraries/{lib_a}", headers=headers_b).status_code == 404


def test_phone_register_disabled(client: TestClient):
    phone = "13800138000"
    resp = client.post("/api/auth/send-code", json={"channel": "phone", "target": phone})
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"] or "短信" in resp.json()["detail"]


def test_conversation_crud(client: TestClient):
    email = f"c_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post("/api/auth/send-code", json={"channel": "email", "target": email}).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"cuser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    lib = client.post("/api/libraries", headers=headers, json={"name": "会话库"}).json()
    created = client.post(
        "/api/conversations",
        headers=headers,
        json={"title": "测试会话", "library_ids": [lib["id"]]},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["memory_enabled"] is True
    listed = client.get("/api/conversations", headers=headers)
    assert listed.status_code == 200
    assert any(c["id"] == cid for c in listed.json())
    detail = client.get(f"/api/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["messages"] == []
    assert detail.json()["memory_summary"] is None
    assert detail.json()["memory_entry_count"] == 0

    streamed = client.post(
        f"/api/conversations/{cid}/messages",
        headers=headers,
        json={"question": "空知识库里有什么？"},
    )
    assert streamed.status_code == 200
    assert "event: done" in streamed.text
    detail = client.get(f"/api/conversations/{cid}", headers=headers)
    assert [
        message["sequence"]
        for message in detail.json()["messages"]
    ] == [1, 2]

    disabled = client.patch(
        f"/api/conversations/{cid}",
        headers=headers,
        json={"memory_enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["memory_enabled"] is False
    cleared = client.delete(
        f"/api/conversations/{cid}/memory",
        headers=headers,
    )
    assert cleared.status_code == 204
    assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 204
    assert client.get(f"/api/conversations/{cid}", headers=headers).status_code == 404
    assert all(
        row["id"] != cid
        for row in client.get("/api/conversations", headers=headers).json()
    )


def test_delete_conversation_cascades_messages_and_memories(client: TestClient):
    headers, user_id = _register_headers(client, "conv_delete")
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "待删除会话库"},
    ).json()
    conversation = client.post(
        "/api/conversations",
        headers=headers,
        json={"title": "待删除会话", "library_ids": [library["id"]]},
    ).json()

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Conversation, ConversationMemory, Message

    db = SessionLocal()
    try:
        row = db.get(Conversation, conversation["id"])
        row.memory_summary = "派生摘要"
        row.summarized_message_count = 1
        db.add(
            Message(
                conversation_id=conversation["id"],
                sequence=1,
                role="user",
                content="需要删除的消息",
            )
        )
        db.add(
            ConversationMemory(
                owner_id=user_id,
                conversation_id=conversation["id"],
                content="需要删除的情景记忆",
                source_start_index=1,
                source_end_index=1,
            )
        )
        db.commit()
    finally:
        db.close()

    deleted = client.delete(
        f"/api/conversations/{conversation['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204, deleted.text
    assert client.get(
        f"/api/conversations/{conversation['id']}",
        headers=headers,
    ).status_code == 404

    db = SessionLocal()
    try:
        assert db.get(Conversation, conversation["id"]) is None
        assert int(
            db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == conversation["id"])
            )
            or 0
        ) == 0
        assert int(
            db.scalar(
                select(func.count())
                .select_from(ConversationMemory)
                .where(ConversationMemory.conversation_id == conversation["id"])
            )
            or 0
        ) == 0
    finally:
        db.close()


def test_delete_conversation_is_owner_scoped(client: TestClient):
    headers_a, _ = _register_headers(client, "conv_owner_a")
    headers_b, _ = _register_headers(client, "conv_owner_b")
    conversation = client.post(
        "/api/conversations",
        headers=headers_a,
        json={"title": "A 的会话"},
    ).json()

    denied = client.delete(
        f"/api/conversations/{conversation['id']}",
        headers=headers_b,
    )
    assert denied.status_code == 404
    assert client.get(
        f"/api/conversations/{conversation['id']}",
        headers=headers_a,
    ).status_code == 200


def test_upload_rejects_fake_pdf(client: TestClient):
    email = f"pdf_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post("/api/auth/send-code", json={"channel": "email", "target": email}).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"pdfuser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    lib = client.post("/api/libraries", headers=headers, json={"name": "PDF库"}).json()
    response = client.post(
        f"/api/libraries/{lib['id']}/documents",
        headers=headers,
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_security_headers_are_present(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["worker_alive"] is None
    assert "index_pending" in response.json()
    assert "queue_pending" in response.json()
    assert "queue_oldest_pending_seconds" in response.json()
    assert "jobs_completed_last_hour" in response.json()
    assert response.json()["conversation_memory_enabled"] is True
    assert "memory_episodes" in response.json()
    assert "memory_compaction_pending" in response.json()
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_original_pdf_download_requires_owner(client: TestClient, tmp_path):
    email = f"source_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post("/api/auth/send-code", json={"channel": "email", "target": email}).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"sourceuser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    lib = client.post("/api/libraries", headers=headers, json={"name": "来源库"}).json()

    source = Path(get_settings().pdf_storage_dir) / me["id"] / lib["id"] / "source.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"%PDF-1.4\n%%EOF")
    from app.db import SessionLocal
    from app.models import Document

    db = SessionLocal()
    try:
        doc = Document(
            library_id=lib["id"],
            owner_id=me["id"],
            file_name="source.pdf",
            file_path=str(source),
            file_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            status="ready",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        document_id = doc.id
    finally:
        db.close()

    denied = client.get(f"/api/documents/{document_id}/file")
    assert denied.status_code == 401
    response = client.get(f"/api/documents/{document_id}/file", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


def test_external_worker_mode_only_enqueues_upload(client: TestClient, monkeypatch):
    email = f"queue_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post("/api/auth/send-code", json={"channel": "email", "target": email}).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"queueuser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    lib = client.post("/api/libraries", headers=headers, json={"name": "队列库"}).json()

    from app.api import documents as documents_api

    scheduled: list[str] = []
    monkeypatch.setattr(documents_api.settings, "index_external_worker", True)
    monkeypatch.setattr(
        documents_api,
        "schedule_index",
        lambda document_id, _background_tasks: scheduled.append(document_id),
    )
    response = client.post(
        f"/api/libraries/{lib['id']}/documents",
        headers=headers,
        files={"file": ("queued.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"
    assert scheduled == []

    from app.db import SessionLocal
    from app.models import Document, IndexJob
    from app.services.document_storage import resolve_document_path
    from sqlalchemy import select

    db = SessionLocal()
    try:
        job = db.scalar(
            select(IndexJob).where(
                IndexJob.document_id == response.json()["id"],
                IndexJob.status == "pending",
            )
        )
        assert job is not None
        document = db.get(Document, response.json()["id"])
        assert document.file_path == (
            f"{document.owner_id}/{document.library_id}/{document.id}.pdf"
        )
        assert resolve_document_path(document).is_file()
    finally:
        db.close()


def test_duplicate_pending_upload_repairs_missing_index_job(
    client: TestClient,
    monkeypatch,
):
    headers, _ = _register_headers(client, "queue_repair")
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "空壳修复库"},
    ).json()
    payload = b"%PDF-1.4\nrepair-shell\n%%EOF"

    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api.settings, "index_external_worker", True)
    first = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("repair.pdf", payload, "application/pdf")},
    )
    assert first.status_code == 201, first.text

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Document, IndexJob

    db = SessionLocal()
    try:
        db.query(IndexJob).filter(
            IndexJob.document_id == first.json()["id"]
        ).delete(synchronize_session=False)
        document = db.get(Document, first.json()["id"])
        document.status = "pending"
        document.status_detail = "orphaned_without_job"
        document.index_attempts = 2
        db.commit()
    finally:
        db.close()

    repeated = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("repair.pdf", payload, "application/pdf")},
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["status"] == "pending"
    assert repeated.json()["status_detail"] == "queued_upload_repair"

    db = SessionLocal()
    try:
        document = db.get(Document, first.json()["id"])
        assert document.index_attempts == 0
        assert int(
            db.scalar(
                select(func.count())
                .select_from(IndexJob)
                .where(
                    IndexJob.document_id == first.json()["id"],
                    IndexJob.status == "pending",
                )
            )
            or 0
        ) == 1
    finally:
        db.close()


def test_failed_duplicate_upload_requeues_without_manual_reindex(
    client: TestClient,
    monkeypatch,
):
    headers, _ = _register_headers(client, "failed_repair")
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "失败恢复库"},
    ).json()
    payload = b"%PDF-1.4\nfailed-shell\n%%EOF"

    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api.settings, "index_external_worker", True)
    first = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("failed.pdf", payload, "application/pdf")},
    )
    assert first.status_code == 201, first.text

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Document, IndexJob

    db = SessionLocal()
    try:
        document = db.get(Document, first.json()["id"])
        document.status = "failed"
        document.status_detail = "temporary_provider_error"
        document.index_attempts = 3
        job = db.scalar(
            select(IndexJob).where(IndexJob.document_id == document.id)
        )
        job.status = "failed"
        job.last_error = "temporary_provider_error"
        db.commit()
    finally:
        db.close()

    repeated = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("failed.pdf", payload, "application/pdf")},
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["status"] == "pending"
    assert repeated.json()["status_detail"] == "queued_upload_repair"

    db = SessionLocal()
    try:
        document = db.get(Document, first.json()["id"])
        assert document.index_attempts == 0
        assert int(
            db.scalar(
                select(func.count())
                .select_from(IndexJob)
                .where(
                    IndexJob.document_id == document.id,
                    IndexJob.status == "pending",
                )
            )
            or 0
        ) == 1
    finally:
        db.close()


def test_reindex_does_not_reset_an_active_job(client: TestClient, monkeypatch):
    headers, _ = _register_headers(client, "active_reindex")
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "活动任务库"},
    ).json()

    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api.settings, "index_external_worker", True)
    uploaded = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={
            "file": (
                "active.pdf",
                b"%PDF-1.4\nactive-job\n%%EOF",
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Document, IndexJob

    db = SessionLocal()
    try:
        document = db.get(Document, uploaded.json()["id"])
        document.status = "processing"
        document.status_detail = "worker_claimed"
        document.index_attempts = 1
        job = db.scalar(
            select(IndexJob).where(IndexJob.document_id == document.id)
        )
        job.status = "running"
        db.commit()
    finally:
        db.close()

    response = client.post(
        f"/api/documents/{uploaded.json()['id']}/reindex",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "processing"
    assert response.json()["status_detail"] == "worker_claimed"

    db = SessionLocal()
    try:
        document = db.get(Document, uploaded.json()["id"])
        assert document.index_attempts == 1
        assert int(
            db.scalar(
                select(func.count())
                .select_from(IndexJob)
                .where(IndexJob.document_id == document.id)
            )
            or 0
        ) == 1
    finally:
        db.close()


def test_upload_pipeline_reaches_ready_without_manual_reindex(
    client: TestClient,
    monkeypatch,
):
    headers, _ = _register_headers(client, "pipeline")
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "首次索引闭环库"},
    ).json()
    fixture = (
        Path(__file__).resolve().parents[1]
        / "evals"
        / "fixtures"
        / "alpha_kinase.pdf"
    )
    payload = fixture.read_bytes()

    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api.settings, "index_external_worker", True)
    monkeypatch.setattr(documents_api.settings, "contextual_chunk_enabled", False)
    uploaded = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("alpha_kinase.pdf", payload, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["status"] == "pending"

    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import Block, Chunk, IndexJob
    from app.services.indexing import index_document_with_retries

    db = SessionLocal()
    try:
        index_document_with_retries(db, uploaded.json()["id"])
    finally:
        db.close()

    ready = client.get(
        f"/api/documents/{uploaded.json()['id']}",
        headers=headers,
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"
    assert ready.json()["page_count"] > 0

    db = SessionLocal()
    try:
        assert int(
            db.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.document_id == uploaded.json()["id"])
            )
            or 0
        ) > 0
        assert int(
            db.scalar(
                select(func.count())
                .select_from(Block)
                .where(Block.document_id == uploaded.json()["id"])
            )
            or 0
        ) > 0
        jobs_before = int(
            db.scalar(
                select(func.count())
                .select_from(IndexJob)
                .where(IndexJob.document_id == uploaded.json()["id"])
            )
            or 0
        )
        latest_job = db.scalar(
            select(IndexJob)
            .where(IndexJob.document_id == uploaded.json()["id"])
            .order_by(IndexJob.created_at.desc())
        )
        assert latest_job.status == "done"
    finally:
        db.close()

    repeated = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("alpha_kinase.pdf", payload, "application/pdf")},
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == uploaded.json()["id"]
    assert repeated.json()["status"] == "ready"

    db = SessionLocal()
    try:
        assert int(
            db.scalar(
                select(func.count())
                .select_from(IndexJob)
                .where(IndexJob.document_id == uploaded.json()["id"])
            )
            or 0
        ) == jobs_before
    finally:
        db.close()


def test_delete_library_removes_portable_pdf_and_database_rows(
    client: TestClient,
    monkeypatch,
):
    email = f"delete_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post(
        "/api/auth/send-code",
        json={"channel": "email", "target": email},
    ).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"deleteuser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "待删除库"},
    ).json()

    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api.settings, "index_external_worker", True)
    uploaded = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("delete.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Document, IndexJob
    from app.services.document_storage import resolve_document_path

    db = SessionLocal()
    try:
        document = db.get(Document, uploaded.json()["id"])
        path = resolve_document_path(document)
        assert path.is_file()
    finally:
        db.close()

    deleted = client.delete(f"/api/libraries/{library['id']}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert not path.exists()
    assert not path.parent.exists()

    db = SessionLocal()
    try:
        assert db.get(Document, uploaded.json()["id"]) is None
        assert (
            db.scalar(
                select(IndexJob).where(
                    IndexJob.document_id == uploaded.json()["id"]
                )
            )
            is None
        )
    finally:
        db.close()


def test_upload_rolls_back_database_and_pdf_when_enqueue_fails(
    client: TestClient,
    monkeypatch,
):
    email = f"rollback_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post(
        "/api/auth/send-code",
        json={"channel": "email", "target": email},
    ).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"rollbackuser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/api/auth/me", headers=headers).json()["id"]
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "回滚库"},
    ).json()

    from app.api import documents as documents_api

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(documents_api, "enqueue_index_job", fail_enqueue)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        client.post(
            f"/api/libraries/{library['id']}/documents",
            headers=headers,
            files={
                "file": (
                    "rollback.pdf",
                    b"%PDF-1.4\n%%EOF",
                    "application/pdf",
                )
            },
        )

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Document

    db = SessionLocal()
    try:
        assert (
            db.scalar(
                select(Document).where(
                    Document.library_id == library["id"],
                    Document.owner_id == user_id,
                )
            )
            is None
        )
    finally:
        db.close()

    storage_dir = (
        Path(get_settings().pdf_storage_dir) / user_id / library["id"]
    )
    assert not storage_dir.exists()


def test_upload_enforces_size_limit_while_streaming(
    client: TestClient,
    monkeypatch,
):
    email = f"limit_{uuid.uuid4().hex[:8]}@example.com"
    code = client.post(
        "/api/auth/send-code",
        json={"channel": "email", "target": email},
    ).json()["dev_code"]
    token = client.post(
        "/api/auth/register",
        json={
            "username": f"limituser_{uuid.uuid4().hex[:5]}",
            "password": "secret12",
            "code": code,
            "channel": "email",
            "email": email,
        },
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    library = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": "大小限制库"},
    ).json()

    from app.api import documents as documents_api

    monkeypatch.setattr(documents_api.settings, "pdf_max_upload_mb", 1)
    payload = b"%PDF-1.4\n" + (b"x" * (1024 * 1024))
    response = client.post(
        f"/api/libraries/{library['id']}/documents",
        headers=headers,
        files={"file": ("too-large.pdf", payload, "application/pdf")},
    )
    assert response.status_code == 400
    assert "超过上限" in response.json()["detail"]

    upload_dir = Path(get_settings().pdf_storage_dir) / ".uploads"
    assert not upload_dir.exists()
