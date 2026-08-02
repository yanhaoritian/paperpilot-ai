from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import AIUsageEvent, User
from app.services.usage_tracking import (
    UsageAttribution,
    estimate_messages_tokens,
    normalize_usage,
    record_ai_usage,
    record_cache_hit,
    sync_configured_model_prices,
    usage_scope,
)


def test_normalize_usage_supports_cache_and_reasoning_fields():
    normalized = normalize_usage(
        {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 7},
        }
    )
    assert normalized == {
        "input_tokens": 120,
        "cached_input_tokens": 20,
        "output_tokens": 30,
        "reasoning_tokens": 7,
        "total_tokens": 150,
    }


def test_message_token_estimate_counts_cjk_and_images():
    estimated = estimate_messages_tokens(
        [
            {"role": "user", "content": "这是论文问题"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述图表"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
        ]
    )
    assert estimated > 1024


def test_usage_event_uses_effective_price_snapshot():
    init_db()
    settings = get_settings()
    provider = f"test-{uuid4().hex[:8]}"
    model = f"model-{uuid4().hex[:8]}"
    version = f"price-{uuid4().hex[:8]}"
    settings.ai_model_prices_json = json.dumps(
        [
            {
                "provider": provider,
                "model": model,
                "operation_kind": "chat",
                "version": version,
                "currency": "CNY",
                "input_per_million": 2,
                "cached_input_per_million": 0.5,
                "output_per_million": 8,
                "per_request": 0.1,
                "effective_from": "2026-01-01T00:00:00Z",
            }
        ]
    )
    assert sync_configured_model_prices() == 1

    user_id = str(uuid4())
    db = SessionLocal()
    try:
        db.add(
            User(
                id=user_id,
                username=f"usage-{uuid4().hex[:8]}",
                email=f"usage-{uuid4().hex[:8]}@example.com",
                password_hash="hash",
            )
        )
        db.commit()
    finally:
        db.close()

    with usage_scope(
        request_id="request-usage-test",
        user_id=user_id,
        skill_id="paper_qa",
        skill_version="v1",
    ):
        event_id = record_ai_usage(
            operation="answer_generate",
            provider=provider,
            model=model,
            input_tokens=1000,
            cached_input_tokens=200,
            output_tokens=500,
            total_tokens=1500,
            item_count=5,
            usage_source="provider",
            status="success",
        )
    assert event_id

    db = SessionLocal()
    try:
        event = db.scalar(select(AIUsageEvent).where(AIUsageEvent.id == event_id))
        assert event is not None
        assert event.request_id == "request-usage-test"
        assert event.skill_id == "paper_qa"
        assert event.price_version == version
        assert event.currency == "CNY"
        # The per-request fee is charged once, not once per embedded item.
        assert event.cost_microunits == 105700

        cache_event_id = record_cache_hit(
            model=model,
            attribution=UsageAttribution(user_id=user_id),
        )
        cache_event = db.get(AIUsageEvent, cache_event_id)
        assert cache_event is not None
        assert cache_event.usage_source == "cache"
        assert cache_event.cost_microunits == 0
        db.delete(db.get(User, user_id))
        db.commit()
    finally:
        db.close()
