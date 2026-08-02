from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import AIUsageEvent, User
from app.schemas import (
    ResearchSkillOut,
    UsageBreakdownOut,
    UsageCostBucket,
    UsageSummaryOut,
)
from app.services.research_skills import list_research_skills

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/skills", response_model=list[ResearchSkillOut])
def research_skills(
    _user: User = Depends(get_current_user),
) -> list[ResearchSkillOut]:
    return [ResearchSkillOut(**skill.public_dict()) for skill in list_research_skills()]


def _cost_buckets(rows) -> list[UsageCostBucket]:  # noqa: ANN001
    return [
        UsageCostBucket(
            currency=str(currency),
            cost_microunits=int(cost or 0),
            cost=round(int(cost or 0) / 1_000_000, 6),
        )
        for currency, cost in rows
        if str(currency) != "UNPRICED"
    ]


def _unpriced_condition():  # noqa: ANN201
    return and_(
        AIUsageEvent.currency == "UNPRICED",
        AIUsageEvent.status.in_(["success", "cancelled"]),
        AIUsageEvent.usage_source.in_(["provider", "estimated"]),
        AIUsageEvent.provider.notin_(["local", "cache"]),
        AIUsageEvent.operation != "response_cache_hit",
    )


def _breakdown(
    db: Session,
    *,
    user_id: str,
    since: datetime,
    column,  # noqa: ANN001
) -> list[UsageBreakdownOut]:
    key_expr = func.coalesce(column, "unattributed")
    base = [
        AIUsageEvent.user_id == user_id,
        AIUsageEvent.created_at >= since,
    ]
    totals = db.execute(
        select(
            key_expr.label("key"),
            func.count(AIUsageEvent.id),
            func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
            func.coalesce(
                func.sum(case((_unpriced_condition(), 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((AIUsageEvent.usage_source == "local", 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (AIUsageEvent.operation == "response_cache_hit", 1),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .where(*base)
        .group_by(key_expr)
        .order_by(func.sum(AIUsageEvent.total_tokens).desc())
    ).all()
    currency_rows = db.execute(
        select(
            key_expr.label("key"),
            AIUsageEvent.currency,
            func.coalesce(func.sum(AIUsageEvent.cost_microunits), 0),
        )
        .where(*base, AIUsageEvent.currency != "UNPRICED")
        .group_by(key_expr, AIUsageEvent.currency)
    ).all()
    currencies: dict[str, list[tuple[str, int]]] = {}
    for key, currency, cost in currency_rows:
        currencies.setdefault(str(key), []).append((str(currency), int(cost or 0)))
    return [
        UsageBreakdownOut(
            key=str(key),
            events=int(events or 0),
            total_tokens=int(tokens or 0),
            unpriced_events=int(unpriced or 0),
            local_events=int(local or 0),
            cache_hits=int(cache_hits or 0),
            currencies=_cost_buckets(currencies.get(str(key), [])),
        )
        for key, events, tokens, unpriced, local, cache_hits in totals
    ]


@router.get("/usage", response_model=UsageSummaryOut)
def usage_summary(
    days: int = Query(default=30, ge=1, le=366),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsageSummaryOut:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    base = [
        AIUsageEvent.user_id == user.id,
        AIUsageEvent.created_at >= since,
    ]
    row = db.execute(
        select(
            func.count(AIUsageEvent.id),
            func.coalesce(
                func.sum(case((AIUsageEvent.usage_source == "provider", 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((AIUsageEvent.usage_source == "estimated", 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((AIUsageEvent.usage_source == "local", 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((AIUsageEvent.operation == "response_cache_hit", 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((AIUsageEvent.status == "error", 1), else_=0)),
                0,
            ),
            func.coalesce(func.sum(AIUsageEvent.input_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.cached_input_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.output_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.reasoning_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.total_tokens), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            _unpriced_condition(),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(*base)
    ).one()
    costs = _cost_buckets(
        db.execute(
            select(
                AIUsageEvent.currency,
                func.coalesce(func.sum(AIUsageEvent.cost_microunits), 0),
            )
            .where(*base, AIUsageEvent.currency != "UNPRICED")
            .group_by(AIUsageEvent.currency)
            .order_by(AIUsageEvent.currency)
        ).all()
    )
    return UsageSummaryOut(
        days=days,
        from_time=since,
        to_time=now,
        events=int(row[0] or 0),
        provider_reported_events=int(row[1] or 0),
        estimated_events=int(row[2] or 0),
        local_events=int(row[3] or 0),
        cache_hits=int(row[4] or 0),
        failed_events=int(row[5] or 0),
        input_tokens=int(row[6] or 0),
        cached_input_tokens=int(row[7] or 0),
        output_tokens=int(row[8] or 0),
        reasoning_tokens=int(row[9] or 0),
        total_tokens=int(row[10] or 0),
        costs=costs,
        unpriced_events=int(row[11] or 0),
        by_operation=_breakdown(
            db,
            user_id=str(user.id),
            since=since,
            column=AIUsageEvent.operation,
        ),
        by_skill=_breakdown(
            db,
            user_id=str(user.id),
            since=since,
            column=AIUsageEvent.skill_id,
        ),
    )
