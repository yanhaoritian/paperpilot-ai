"""Provider token normalization, attribution and effective-dated cost ledger."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterator
from urllib.parse import urlparse

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AIUsageEvent, ModelPriceVersion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageAttribution:
    request_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    document_id: str | None = None
    index_job_id: str | None = None
    skill_id: str | None = None
    skill_version: str | None = None


_ATTRIBUTION: ContextVar[UsageAttribution] = ContextVar(
    "paperpilot_usage_attribution",
    default=UsageAttribution(),
)


def current_usage_attribution() -> UsageAttribution:
    return _ATTRIBUTION.get()


@contextmanager
def usage_scope(**values: str | None) -> Iterator[UsageAttribution]:
    """Attach request/user/skill ownership to nested model calls."""

    current = current_usage_attribution()
    allowed = set(asdict(current))
    updates = {
        key: value
        for key, value in values.items()
        if key in allowed and value is not None
    }
    merged = replace(current, **updates)
    token = _ATTRIBUTION.set(merged)
    try:
        yield merged
    finally:
        _ATTRIBUTION.reset(token)


def provider_from_base_url(base_url: str, *, fallback: str = "compatible") -> str:
    host = (urlparse(base_url or "").hostname or "").lower()
    if "deepseek" in host:
        return "deepseek"
    if "bigmodel" in host or "zhipu" in host:
        return "zhipu"
    if "openai" in host:
        return "openai"
    if host:
        return host[:64]
    return fallback


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize OpenAI/DeepSeek-compatible usage payload variants."""

    raw = usage if isinstance(usage, dict) else {}
    prompt_details = raw.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    completion_details = raw.get("completion_tokens_details")
    completion_details = (
        completion_details if isinstance(completion_details, dict) else {}
    )
    cache_hit = _as_int(
        raw.get("prompt_cache_hit_tokens")
        or prompt_details.get("cached_tokens")
        or raw.get("cached_input_tokens")
    )
    cache_miss = _as_int(raw.get("prompt_cache_miss_tokens"))
    input_tokens = _as_int(raw.get("prompt_tokens") or raw.get("input_tokens"))
    if input_tokens == 0 and (cache_hit or cache_miss):
        input_tokens = cache_hit + cache_miss
    output_tokens = _as_int(
        raw.get("completion_tokens") or raw.get("output_tokens")
    )
    reasoning_tokens = _as_int(
        completion_details.get("reasoning_tokens")
        or raw.get("reasoning_tokens")
    )
    total_tokens = _as_int(raw.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": min(cache_hit, input_tokens),
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def estimate_text_tokens(text: str) -> int:
    """Conservative tokenizer-free fallback for CJK and Latin mixed text."""

    value = str(text or "")
    if not value:
        return 0
    cjk = sum(1 for char in value if "\u3400" <= char <= "\u9fff")
    other = len(value) - cjk
    return max(1, cjk + math.ceil(other / 4))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages or []:
        total += 4  # role and message framing
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    total += estimate_text_tokens(str(item.get("text") or ""))
                elif item.get("type") in {"image_url", "input_image"}:
                    # Provider-specific image tokenization is unavailable. This
                    # is deliberately marked estimated by the caller.
                    total += 1024
    return max(0, total + 2)


def _operation_kind(operation: str) -> str:
    value = (operation or "").lower()
    if "embedding" in value:
        return "embedding"
    if "vision" in value or "image" in value:
        return "vision"
    return "chat"


def _currency_to_microunits(value: Any) -> int:
    try:
        amount = Decimal(str(value or 0)) * Decimal(1_000_000)
        return max(0, int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _parse_effective_from(value: Any) -> datetime:
    if not value:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def sync_configured_model_prices() -> int:
    """Idempotently import effective prices from AI_MODEL_PRICES_JSON."""

    raw = (get_settings().ai_model_prices_json or "").strip()
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("AI_MODEL_PRICES_JSON is invalid: %s", exc)
        return 0
    if isinstance(payload, dict):
        payload = payload.get("prices") or []
    if not isinstance(payload, list):
        logger.error("AI_MODEL_PRICES_JSON must be a list or {prices:[...]}")
        return 0

    inserted = 0
    db = SessionLocal()
    try:
        for item in payload:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or "").strip().lower()
            model = str(item.get("model") or "").strip()
            kind = str(item.get("operation_kind") or "chat").strip().lower()
            if not provider or not model or kind not in {
                "chat",
                "embedding",
                "vision",
                "all",
            }:
                logger.warning("skipping malformed model price row: %s", item)
                continue
            canonical = json.dumps(item, sort_keys=True, ensure_ascii=False)
            version = str(item.get("version") or "").strip() or hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()[:16]
            exists = db.scalar(
                select(ModelPriceVersion.id).where(
                    ModelPriceVersion.provider == provider,
                    ModelPriceVersion.model == model,
                    ModelPriceVersion.operation_kind == kind,
                    ModelPriceVersion.version == version,
                )
            )
            if exists:
                continue
            db.add(
                ModelPriceVersion(
                    provider=provider,
                    model=model,
                    operation_kind=kind,
                    version=version,
                    currency=str(item.get("currency") or "CNY").upper()[:12],
                    input_price_microunits_per_million=_currency_to_microunits(
                        item.get("input_per_million")
                    ),
                    cached_input_price_microunits_per_million=_currency_to_microunits(
                        item.get("cached_input_per_million")
                    ),
                    output_price_microunits_per_million=_currency_to_microunits(
                        item.get("output_per_million")
                    ),
                    per_request_microunits=_currency_to_microunits(
                        item.get("per_request")
                    ),
                    effective_from=_parse_effective_from(item.get("effective_from")),
                )
            )
            inserted += 1
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("failed to sync configured model prices")
        return 0
    finally:
        db.close()
    return inserted


def _active_price(
    db,  # noqa: ANN001
    *,
    provider: str,
    model: str,
    operation: str,
    at: datetime,
) -> ModelPriceVersion | None:
    kind = _operation_kind(operation)
    return db.scalar(
        select(ModelPriceVersion)
        .where(
            ModelPriceVersion.provider == provider,
            ModelPriceVersion.model == model,
            ModelPriceVersion.operation_kind.in_([kind, "all"]),
            ModelPriceVersion.effective_from <= at,
        )
        .order_by(
            (ModelPriceVersion.operation_kind == kind).desc(),
            ModelPriceVersion.effective_from.desc(),
            ModelPriceVersion.created_at.desc(),
        )
        .limit(1)
    )


def _rounded_token_cost(tokens: int, price_per_million: int) -> int:
    if tokens <= 0 or price_per_million <= 0:
        return 0
    return int(
        (Decimal(tokens) * Decimal(price_per_million) / Decimal(1_000_000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def record_ai_usage(
    *,
    operation: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    total_tokens: int | None = None,
    item_count: int = 1,
    usage_source: str = "provider",
    status: str = "success",
    latency_ms: int | None = None,
    provider_request_id: str | None = None,
    attribution: UsageAttribution | None = None,
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Append one event. Accounting failure never breaks a user request."""

    if not get_settings().ai_usage_tracking_enabled:
        return None
    attr = attribution or current_usage_attribution()
    input_tokens = _as_int(input_tokens)
    cached_input_tokens = min(_as_int(cached_input_tokens), input_tokens)
    output_tokens = _as_int(output_tokens)
    reasoning_tokens = _as_int(reasoning_tokens)
    total_tokens = _as_int(total_tokens)
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        price = _active_price(
            db,
            provider=provider,
            model=model,
            operation=operation,
            at=now,
        )
        currency = "UNPRICED"
        price_version = None
        cost = 0
        # Failed compatibility probes and rejected requests are kept for
        # observability but are not booked as billable cost.
        if price is not None and status in {"success", "cancelled"}:
            currency = price.currency
            price_version = price.version
            ordinary_input = max(0, input_tokens - cached_input_tokens)
            cost = (
                _rounded_token_cost(
                    ordinary_input,
                    price.input_price_microunits_per_million,
                )
                + _rounded_token_cost(
                    cached_input_tokens,
                    price.cached_input_price_microunits_per_million,
                )
                + _rounded_token_cost(
                    output_tokens,
                    price.output_price_microunits_per_million,
                )
                # This field is per provider request, not per embedded item or
                # per image contained in that request.
                + int(price.per_request_microunits or 0)
            )
        event = AIUsageEvent(
            request_id=(attr.request_id or "")[:64] or None,
            user_id=(attr.user_id or "")[:36] or None,
            conversation_id=(attr.conversation_id or "")[:36] or None,
            message_id=(attr.message_id or "")[:36] or None,
            document_id=(attr.document_id or "")[:36] or None,
            index_job_id=(attr.index_job_id or "")[:36] or None,
            skill_id=(attr.skill_id or "")[:64] or None,
            skill_version=(attr.skill_version or "")[:32] or None,
            operation=(operation or "unknown")[:64],
            provider=(provider or "unknown")[:64],
            model=(model or "unknown")[:128],
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            item_count=max(0, int(item_count or 0)),
            usage_source=(usage_source or "estimated")[:16],
            price_version=price_version,
            currency=currency,
            cost_microunits=cost,
            latency_ms=max(0, int(latency_ms)) if latency_ms is not None else None,
            status=(status or "unknown")[:24],
            provider_request_id=(provider_request_id or "")[:128] or None,
            extra=extra or None,
            created_at=now,
        )
        db.add(event)
        db.commit()
        return str(event.id)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("AI usage event could not be persisted")
        return None
    finally:
        db.close()


def record_cache_hit(
    *,
    model: str,
    attribution: UsageAttribution | None = None,
) -> str | None:
    return record_ai_usage(
        operation="response_cache_hit",
        provider="cache",
        model=model,
        usage_source="cache",
        status="success",
        attribution=attribution,
        extra={"billable": False},
    )
