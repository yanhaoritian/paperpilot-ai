from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


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
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def _conversation(client: TestClient, tag: str) -> tuple[dict[str, str], str]:
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
    library_id = client.post(
        "/api/libraries",
        headers=headers,
        json={"name": f"{tag} library"},
    ).json()["id"]
    conversation_id = client.post(
        "/api/conversations",
        headers=headers,
        json={"title": f"{tag} conversation", "library_ids": [library_id]},
    ).json()["id"]
    return headers, conversation_id


def _sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        event = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


def test_closing_event_iterator_closes_nested_source_on_disconnect():
    from app.api.conversations import _closing_event_iterator

    class Source:
        def __init__(self) -> None:
            self.closed = False
            self.next_calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.next_calls += 1
            return "token", {"text": "partial"}

        def close(self) -> None:
            self.closed = True

    source = Source()

    def outer_stream() -> Iterator[tuple[str, dict]]:
        with _closing_event_iterator(source) as events:
            for event in events:
                yield event

    stream = outer_stream()
    assert next(stream) == ("token", {"text": "partial"})
    stream.close()

    assert source.closed is True
    assert source.next_calls == 1
    with pytest.raises(StopIteration):
        next(stream)


def test_stream_done_is_true_after_normal_completion(client, monkeypatch):
    from app.api import conversations

    headers, conversation_id = _conversation(client, "stream_ok")

    def successful_events(*_args, **_kwargs):
        yield "token", {"text": "grounded answer"}
        yield "final", {
            "answer": "grounded answer",
            "citations": [],
            "retrieval_hit": 0,
            "degraded": False,
            "confidence": "medium",
        }

    monkeypatch.setattr(conversations, "iter_agent_events", successful_events)
    monkeypatch.setattr(
        conversations,
        "refresh_conversation_memory",
        lambda *_args, **_kwargs: None,
    )

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"question": "normal completion"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[-1] == ("done", {"ok": True})


def test_stream_exception_is_logged_saved_and_done_is_false(client, monkeypatch):
    from app.api import conversations

    headers, conversation_id = _conversation(client, "stream_error")
    exception_log = Mock()

    def failing_events(*_args, **_kwargs):
        yield "status", {"text": "starting"}
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(conversations, "iter_agent_events", failing_events)
    monkeypatch.setattr(conversations.logger, "exception", exception_log)
    monkeypatch.setattr(
        conversations,
        "refresh_conversation_memory",
        lambda *_args, **_kwargs: None,
    )

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        headers=headers,
        json={"question": "exception completion"},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert any(
        event == "error" and data["detail"] == "synthetic provider failure"
        for event, data in events
    )
    assert events[-1] == ("done", {"ok": False})
    exception_log.assert_called_once()

    detail = client.get(
        f"/api/conversations/{conversation_id}",
        headers=headers,
    ).json()
    assert detail["messages"][-1]["role"] == "assistant"
    assert "生成失败：synthetic provider failure" in detail["messages"][-1]["content"]

