from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB = Path(__file__).resolve().parent / "_test_auth.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-at-least-24-chars-xx"
os.environ["JWT_REQUIRE_STRONG"] = "0"
os.environ["OPENAI_API_KEY"] = ""
os.environ["AUTH_EXPOSE_CODE"] = "1"
os.environ["INDEX_RECOVER_ON_STARTUP"] = "0"
os.environ["PDF_STORAGE_DIR"] = str(Path(__file__).resolve().parent / "_test_pdfs")

from app.config import get_settings

get_settings.cache_clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_STORAGE_DIR", str(tmp_path / "pdfs"))
    monkeypatch.setenv("AUTH_EXPOSE_CODE", "1")
    monkeypatch.setenv("JWT_REQUIRE_STRONG", "0")
    monkeypatch.setenv("INDEX_RECOVER_ON_STARTUP", "0")
    get_settings.cache_clear()
    from app.db import init_db
    from app.main import create_app

    settings = get_settings()
    settings.pdf_storage_dir = str(tmp_path / "pdfs")
    settings.auth_expose_code = True
    settings.jwt_require_strong = False
    settings.index_recover_on_startup = False
    init_db()
    app = create_app()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


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
    listed = client.get("/api/conversations", headers=headers)
    assert listed.status_code == 200
    assert any(c["id"] == cid for c in listed.json())
    detail = client.get(f"/api/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["messages"] == []
    assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 204
