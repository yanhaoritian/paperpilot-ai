from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _chat_headers() -> dict[str, str]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY")
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def _embed_headers() -> dict[str, str]:
    settings = get_settings()
    key = (settings.embedding_api_key or settings.openai_api_key or "").strip()
    if not key:
        raise RuntimeError("缺少 EMBEDDING_API_KEY 或 OPENAI_API_KEY")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _chat_base_url() -> str:
    return get_settings().openai_base_url.rstrip("/")


def _embed_base_url() -> str:
    settings = get_settings()
    base = (settings.embedding_base_url or settings.openai_base_url or "").strip()
    return base.rstrip("/")


def _local_embed(text: str, dim: int) -> list[float]:
    """Deterministic hashing embedding for when remote /embeddings is unavailable."""
    vec = [0.0] * dim
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return vec
    grams = [normalized[i : i + 2] for i in range(max(0, len(normalized) - 1))]
    grams.extend(normalized.split())
    for g in grams:
        digest = hashlib.sha256(g.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    provider = (settings.embedding_provider or "api").strip().lower()
    if provider == "local":
        return [_local_embed(t, settings.embedding_dimensions) for t in texts]

    url = f"{_embed_base_url()}/embeddings"
    vectors: list[list[float]] = []
    batch = max(1, min(settings.embed_batch_size, 64))
    retries = max(1, settings.embedding_retries)

    with httpx.Client(timeout=120.0) as client:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            last_exc: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    payload: dict[str, Any] = {
                        "model": settings.embedding_model,
                        "input": chunk,
                    }
                    # Zhipu embedding-3 supports custom dimensions
                    if settings.embedding_dimensions and "embedding-3" in settings.embedding_model:
                        payload["dimensions"] = settings.embedding_dimensions
                    resp = client.post(url, headers=_embed_headers(), json=payload)
                    if resp.status_code in {502, 503, 504} and attempt < retries:
                        logger.warning(
                            "embedding %s on attempt %s/%s, retrying…",
                            resp.status_code,
                            attempt,
                            retries,
                        )
                        time.sleep(1.5 * attempt)
                        continue
                    if resp.status_code >= 400:
                        detail = resp.text[:300]
                        logger.error("embedding failed: %s %s", resp.status_code, detail)
                        raise RuntimeError(f"Embeddings HTTP {resp.status_code}: {detail}")
                    data = resp.json()["data"]
                    data_sorted = sorted(data, key=lambda x: x["index"])
                    vectors.extend([row["embedding"] for row in data_sorted])
                    last_exc = None
                    break
                except httpx.HTTPError as exc:
                    last_exc = exc
                    if attempt < retries:
                        time.sleep(1.5 * attempt)
                        continue
                    raise RuntimeError(f"Embeddings 请求失败: {exc}") from exc
            if last_exc:
                raise RuntimeError(f"Embeddings 请求失败: {last_exc}") from last_exc
    return vectors


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    settings = get_settings()
    url = f"{_chat_base_url()}/chat/completions"
    payload = {
        "model": model or settings.default_model,
        "temperature": temperature,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(url, headers=_chat_headers(), json=payload)
        if resp.status_code >= 400:
            logger.error("chat failed: %s %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


def chat_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
):
    """Yield text deltas from OpenAI-compatible chat completions stream."""
    settings = get_settings()
    url = f"{_chat_base_url()}/chat/completions"
    payload = {
        "model": model or settings.default_model,
        "temperature": temperature,
        "messages": messages,
        "stream": True,
    }
    with httpx.Client(timeout=180.0) as client:
        with client.stream("POST", url, headers=_chat_headers(), json=payload) as resp:
            if resp.status_code >= 400:
                body = resp.read().decode("utf-8", errors="replace")[:500]
                logger.error("chat stream failed: %s %s", resp.status_code, body)
                raise RuntimeError(f"Chat stream HTTP {resp.status_code}: {body}")
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    data = line[5:].strip()
                else:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text
