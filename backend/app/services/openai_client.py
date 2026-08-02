from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from app.config import get_settings
from app.services.usage_tracking import (
    UsageAttribution,
    current_usage_attribution,
    estimate_messages_tokens,
    estimate_text_tokens,
    normalize_usage,
    provider_from_base_url,
    record_ai_usage,
)

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_chat_client: httpx.Client | None = None
_embed_client: httpx.Client | None = None


def _get_chat_client() -> httpx.Client:
    global _chat_client
    with _client_lock:
        if _chat_client is None or _chat_client.is_closed:
            _chat_client = httpx.Client(
                timeout=180.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _chat_client


def _get_embed_client() -> httpx.Client:
    global _embed_client
    with _client_lock:
        if _embed_client is None or _embed_client.is_closed:
            _embed_client = httpx.Client(
                timeout=120.0,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return _embed_client


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


def _response_request_id(resp: httpx.Response) -> str | None:
    return (
        resp.headers.get("x-request-id")
        or resp.headers.get("request-id")
        or resp.headers.get("x-trace-id")
    )


def _embed_one_batch(
    batch_index: int,
    chunk: list[str],
    *,
    operation: str,
    attribution: UsageAttribution,
) -> tuple[int, list[list[float]]]:
    settings = get_settings()
    url = f"{_embed_base_url()}/embeddings"
    provider = provider_from_base_url(_embed_base_url())
    retries = max(1, settings.embedding_retries)
    client = _get_embed_client()
    last_exc: Exception | None = None
    started = time.perf_counter()
    for attempt in range(1, retries + 1):
        try:
            payload: dict[str, Any] = {
                "model": settings.embedding_model,
                "input": chunk,
            }
            if settings.embedding_dimensions and "embedding-3" in settings.embedding_model:
                payload["dimensions"] = settings.embedding_dimensions
            resp = client.post(url, headers=_embed_headers(), json=payload)
            if resp.status_code in {502, 503, 504} and attempt < retries:
                logger.warning(
                    "embedding %s on attempt %s/%s (batch %s), retrying…",
                    resp.status_code,
                    attempt,
                    retries,
                    batch_index,
                )
                time.sleep(1.5 * attempt)
                continue
            if resp.status_code >= 400:
                detail = resp.text[:300]
                logger.error("embedding failed: %s %s", resp.status_code, detail)
                raise RuntimeError(f"Embeddings HTTP {resp.status_code}: {detail}")
            body = resp.json()
            data = body["data"]
            data_sorted = sorted(data, key=lambda x: x["index"])
            normalized = normalize_usage(body.get("usage"))
            source = "provider" if isinstance(body.get("usage"), dict) else "estimated"
            if not normalized["total_tokens"]:
                normalized["input_tokens"] = sum(estimate_text_tokens(text) for text in chunk)
                normalized["total_tokens"] = normalized["input_tokens"]
                source = "estimated"
            record_ai_usage(
                operation=operation,
                provider=provider,
                model=settings.embedding_model,
                **normalized,
                item_count=len(chunk),
                usage_source=source,
                status="success",
                latency_ms=round((time.perf_counter() - started) * 1000),
                provider_request_id=_response_request_id(resp),
                attribution=attribution,
                extra={"batch_index": batch_index, "attempt": attempt},
            )
            return batch_index, [row["embedding"] for row in data_sorted]
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
                continue
            record_ai_usage(
                operation=operation,
                provider=provider,
                model=settings.embedding_model,
                input_tokens=sum(estimate_text_tokens(text) for text in chunk),
                total_tokens=sum(estimate_text_tokens(text) for text in chunk),
                item_count=len(chunk),
                usage_source="estimated",
                status="error",
                latency_ms=round((time.perf_counter() - started) * 1000),
                attribution=attribution,
                extra={"batch_index": batch_index, "attempt": attempt},
            )
            raise RuntimeError(f"Embeddings 请求失败: {exc}") from exc
        except Exception as exc:
            record_ai_usage(
                operation=operation,
                provider=provider,
                model=settings.embedding_model,
                input_tokens=sum(estimate_text_tokens(text) for text in chunk),
                total_tokens=sum(estimate_text_tokens(text) for text in chunk),
                item_count=len(chunk),
                usage_source="estimated",
                status="error",
                latency_ms=round((time.perf_counter() - started) * 1000),
                attribution=attribution,
                extra={"batch_index": batch_index, "attempt": attempt},
            )
            raise
    raise RuntimeError(f"Embeddings 请求失败: {last_exc}")


def embed_texts(
    texts: list[str],
    *,
    operation: str = "embedding",
) -> list[list[float]]:
    if not texts:
        return []
    settings = get_settings()
    attribution = current_usage_attribution()
    provider = (settings.embedding_provider or "api").strip().lower()
    if provider == "local":
        estimated = sum(estimate_text_tokens(text) for text in texts)
        record_ai_usage(
            operation=operation,
            provider="local",
            model="deterministic-hashing",
            input_tokens=estimated,
            total_tokens=estimated,
            item_count=len(texts),
            usage_source="local",
            status="success",
            attribution=attribution,
            extra={"billable": False},
        )
        return [_local_embed(t, settings.embedding_dimensions) for t in texts]

    batch = max(1, min(settings.embed_batch_size, 64))
    batches = [texts[i : i + batch] for i in range(0, len(texts), batch)]
    if len(batches) == 1:
        _, vectors = _embed_one_batch(
            0,
            batches[0],
            operation=operation,
            attribution=attribution,
        )
        return vectors

    workers = max(1, min(len(batches), int(settings.embed_concurrency or 3)))
    ordered: list[list[list[float]] | None] = [None] * len(batches)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _embed_one_batch,
                i,
                chunk,
                operation=operation,
                attribution=attribution,
            )
            for i, chunk in enumerate(batches)
        ]
        for fut in as_completed(futures):
            idx, vectors = fut.result()
            ordered[idx] = vectors

    out: list[list[float]] = []
    for part in ordered:
        if part is None:
            raise RuntimeError("Embeddings batch missing")
        out.extend(part)
    return out


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    operation: str = "chat_json",
) -> dict[str, Any]:
    settings = get_settings()
    url = f"{_chat_base_url()}/chat/completions"
    resolved_model = model or settings.default_model
    payload = {
        "model": resolved_model,
        "temperature": temperature,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    client = _get_chat_client()
    attribution = current_usage_attribution()
    provider = provider_from_base_url(_chat_base_url())
    started = time.perf_counter()
    try:
        resp = client.post(url, headers=_chat_headers(), json=payload)
    except Exception:
        estimated = estimate_messages_tokens(messages)
        record_ai_usage(
            operation=operation,
            provider=provider,
            model=resolved_model,
            input_tokens=estimated,
            total_tokens=estimated,
            usage_source="estimated",
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000),
            attribution=attribution,
            extra={"transport_error": True},
        )
        raise
    if resp.status_code >= 400:
        logger.error("chat failed: %s %s", resp.status_code, resp.text[:500])
        estimated = estimate_messages_tokens(messages)
        record_ai_usage(
            operation=operation,
            provider=provider,
            model=resolved_model,
            input_tokens=estimated,
            total_tokens=estimated,
            usage_source="estimated",
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000),
            provider_request_id=_response_request_id(resp),
            attribution=attribution,
            extra={"http_status": resp.status_code},
        )
        resp.raise_for_status()
    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    normalized = normalize_usage(body.get("usage"))
    source = "provider" if isinstance(body.get("usage"), dict) else "estimated"
    if not normalized["total_tokens"]:
        normalized["input_tokens"] = estimate_messages_tokens(messages)
        normalized["output_tokens"] = estimate_text_tokens(content)
        normalized["total_tokens"] = (
            normalized["input_tokens"] + normalized["output_tokens"]
        )
        source = "estimated"
    record_ai_usage(
        operation=operation,
        provider=provider,
        model=resolved_model,
        **normalized,
        usage_source=source,
        status="success",
        latency_ms=round((time.perf_counter() - started) * 1000),
        provider_request_id=_response_request_id(resp),
        attribution=attribution,
    )
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
    operation: str = "answer_generate",
):
    """Yield text deltas from OpenAI-compatible chat completions stream."""
    settings = get_settings()
    url = f"{_chat_base_url()}/chat/completions"
    resolved_model = model or settings.default_model
    base_payload = {
        "model": resolved_model,
        "temperature": temperature,
        "messages": messages,
        "stream": True,
    }
    client = _get_chat_client()
    attribution = current_usage_attribution()
    provider = provider_from_base_url(_chat_base_url())
    started = time.perf_counter()
    output_parts: list[str] = []
    usage_payload: dict[str, Any] | None = None
    provider_request_id: str | None = None
    status = "error"
    completed = False
    try:
        attempts = [True, False] if settings.chat_stream_include_usage else [False]
        last_error = ""
        for include_usage in attempts:
            payload = dict(base_payload)
            if include_usage:
                payload["stream_options"] = {"include_usage": True}
            with client.stream("POST", url, headers=_chat_headers(), json=payload) as resp:
                provider_request_id = _response_request_id(resp)
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")[:500]
                    last_error = f"Chat stream HTTP {resp.status_code}: {body}"
                    if include_usage and resp.status_code in {400, 404, 422}:
                        logger.warning(
                            "stream usage option unsupported; retrying without it: %s",
                            body[:200],
                        )
                        continue
                    logger.error("chat stream failed: %s %s", resp.status_code, body)
                    raise RuntimeError(last_error)
                for line in resp.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data = line[5:].strip()
                    else:
                        continue
                    if data == "[DONE]":
                        completed = True
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(chunk.get("usage"), dict):
                        usage_payload = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        output_parts.append(text)
                        yield text
                status = "success"
                completed = True
                break
        if status != "success":
            raise RuntimeError(last_error or "Chat stream failed")
    except GeneratorExit:
        status = "cancelled"
        raise
    finally:
        normalized = normalize_usage(usage_payload)
        source = "provider" if usage_payload is not None else "estimated"
        if not normalized["total_tokens"]:
            normalized["input_tokens"] = estimate_messages_tokens(messages)
            normalized["output_tokens"] = estimate_text_tokens("".join(output_parts))
            normalized["total_tokens"] = (
                normalized["input_tokens"] + normalized["output_tokens"]
            )
            source = "estimated"
        record_ai_usage(
            operation=operation,
            provider=provider,
            model=resolved_model,
            **normalized,
            usage_source=source,
            status=status,
            latency_ms=round((time.perf_counter() - started) * 1000),
            provider_request_id=provider_request_id,
            attribution=attribution,
            extra={"stream": True, "completed": completed},
        )


def chat_content(
    messages: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float = 0.2,
    operation: str = "vision_page",
) -> str:
    """One-shot multimodal-compatible chat call with shared accounting."""

    url = f"{base_url.rstrip('/')}/chat/completions"
    provider = provider_from_base_url(base_url)
    attribution = current_usage_attribution()
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    image_count = sum(
        1
        for message in messages
        for item in (
            message.get("content") if isinstance(message.get("content"), list) else []
        )
        if isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}
    )
    started = time.perf_counter()
    try:
        resp = _get_chat_client().post(url, headers=headers, json=payload)
    except Exception:
        estimated = estimate_messages_tokens(messages)
        record_ai_usage(
            operation=operation,
            provider=provider,
            model=model,
            input_tokens=estimated,
            total_tokens=estimated,
            item_count=max(1, image_count),
            usage_source="estimated",
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000),
            attribution=attribution,
            extra={"transport_error": True},
        )
        raise
    if resp.status_code >= 400:
        estimated = estimate_messages_tokens(messages)
        record_ai_usage(
            operation=operation,
            provider=provider,
            model=model,
            input_tokens=estimated,
            total_tokens=estimated,
            item_count=max(1, image_count),
            usage_source="estimated",
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000),
            provider_request_id=_response_request_id(resp),
            attribution=attribution,
            extra={"http_status": resp.status_code},
        )
        resp.raise_for_status()
    body = resp.json()
    content = str(body["choices"][0]["message"]["content"] or "")
    normalized = normalize_usage(body.get("usage"))
    source = "provider" if isinstance(body.get("usage"), dict) else "estimated"
    if not normalized["total_tokens"]:
        normalized["input_tokens"] = estimate_messages_tokens(messages)
        normalized["output_tokens"] = estimate_text_tokens(content)
        normalized["total_tokens"] = (
            normalized["input_tokens"] + normalized["output_tokens"]
        )
        source = "estimated"
    record_ai_usage(
        operation=operation,
        provider=provider,
        model=model,
        **normalized,
        item_count=max(1, image_count),
        usage_source=source,
        status="success",
        latency_ms=round((time.perf_counter() - started) * 1000),
        provider_request_id=_response_request_id(resp),
        attribution=attribution,
    )
    return content
