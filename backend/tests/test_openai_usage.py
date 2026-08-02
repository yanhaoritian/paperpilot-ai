from __future__ import annotations

import json

from app.services import openai_client


class _Response:
    def __init__(self, payload, *, status_code=200, lines=None):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.headers = {"x-request-id": "provider-request-1"}
        self._lines = lines or []

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)

    def read(self):
        return self.text.encode("utf-8")

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Client:
    def __init__(self, response):
        self.response = response
        self.last_payload = None

    def post(self, _url, *, headers, json):  # noqa: A002
        self.last_payload = json
        return self.response

    def stream(self, _method, _url, *, headers, json):  # noqa: A002
        self.last_payload = json
        return self.response


def test_chat_json_records_provider_usage(monkeypatch):
    response = _Response(
        {
            "choices": [{"message": {"content": '{"answer":"ok"}'}}],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
            },
        }
    )
    client = _Client(response)
    captured = []
    settings = openai_client.get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "default_model", "test-chat")
    monkeypatch.setattr(openai_client, "_get_chat_client", lambda: client)
    monkeypatch.setattr(openai_client, "record_ai_usage", lambda **kwargs: captured.append(kwargs))

    result = openai_client.chat_json(
        [{"role": "user", "content": "question"}],
        operation="rerank",
    )
    assert result == {"answer": "ok"}
    assert captured[0]["operation"] == "rerank"
    assert captured[0]["provider"] == "deepseek"
    assert captured[0]["input_tokens"] == 20
    assert captured[0]["output_tokens"] == 5
    assert captured[0]["usage_source"] == "provider"


def test_chat_stream_collects_final_usage_chunk(monkeypatch):
    lines = [
        'data: {"choices":[{"delta":{"content":"回答"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":30,"completion_tokens":2,"total_tokens":32}}',
        "data: [DONE]",
    ]
    response = _Response({}, lines=lines)
    client = _Client(response)
    captured = []
    settings = openai_client.get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(settings, "default_model", "test-chat")
    monkeypatch.setattr(settings, "chat_stream_include_usage", True)
    monkeypatch.setattr(openai_client, "_get_chat_client", lambda: client)
    monkeypatch.setattr(openai_client, "record_ai_usage", lambda **kwargs: captured.append(kwargs))

    answer = "".join(
        openai_client.chat_stream(
            [{"role": "user", "content": "question"}],
            operation="answer_generate",
        )
    )
    assert answer == "回答"
    assert client.last_payload["stream_options"] == {"include_usage": True}
    assert captured[0]["input_tokens"] == 30
    assert captured[0]["output_tokens"] == 2
    assert captured[0]["status"] == "success"
    assert captured[0]["usage_source"] == "provider"


def test_empty_provider_usage_is_marked_as_estimated(monkeypatch):
    response = _Response(
        {
            "choices": [{"message": {"content": '{"answer":"ok"}'}}],
            "usage": {},
        }
    )
    client = _Client(response)
    captured = []
    settings = openai_client.get_settings()
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(openai_client, "_get_chat_client", lambda: client)
    monkeypatch.setattr(openai_client, "record_ai_usage", lambda **kwargs: captured.append(kwargs))

    openai_client.chat_json(
        [{"role": "user", "content": "question"}],
        operation="answer_generate",
    )
    assert captured[0]["usage_source"] == "estimated"
    assert captured[0]["total_tokens"] > 0
