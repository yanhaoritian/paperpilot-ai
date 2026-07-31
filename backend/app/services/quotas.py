"""Per-user daily quotas for public-beta cost control."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import UsageDaily


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_or_create_row(db: Session, user_id: str) -> UsageDaily:
    day = _utc_day()
    row = db.scalar(select(UsageDaily).where(UsageDaily.user_id == user_id, UsageDaily.day == day))
    if row:
        return row
    row = UsageDaily(user_id=user_id, day=day, query_count=0, upload_count=0)
    try:
        # A savepoint preserves a caller's surrounding transaction (for
        # example message sequence allocation under a conversation row lock).
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        # A concurrent request may have created the unique (user, day) row.
        existing = db.scalar(
            select(UsageDaily).where(UsageDaily.user_id == user_id, UsageDaily.day == day)
        )
        if existing is None:
            raise
        return existing


def quota_status(db: Session, user_id: str) -> dict:
    settings = get_settings()
    row = _get_or_create_row(db, user_id)
    db.commit()
    q_lim = int(settings.quota_daily_queries or 0)
    u_lim = int(settings.quota_daily_uploads or 0)
    return {
        "day": row.day,
        "queries_used": int(row.query_count or 0),
        "queries_limit": q_lim,
        "uploads_used": int(row.upload_count or 0),
        "uploads_limit": u_lim,
    }


def consume_query_quota(
    db: Session,
    user_id: str,
    *,
    commit: bool = True,
) -> None:
    settings = get_settings()
    limit = int(settings.quota_daily_queries or 0)
    if limit <= 0:
        return
    day = _utc_day()
    _get_or_create_row(db, user_id)
    result = db.execute(
        update(UsageDaily)
        .where(
            UsageDaily.user_id == user_id,
            UsageDaily.day == day,
            UsageDaily.query_count < limit,
        )
        .values(query_count=UsageDaily.query_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"今日问答次数已达上限（{limit} 次），请明天再试",
        )
    if commit:
        db.commit()
    else:
        db.flush()


def consume_upload_quota(db: Session, user_id: str) -> None:
    settings = get_settings()
    limit = int(settings.quota_daily_uploads or 0)
    if limit <= 0:
        return
    day = _utc_day()
    _get_or_create_row(db, user_id)
    result = db.execute(
        update(UsageDaily)
        .where(
            UsageDaily.user_id == user_id,
            UsageDaily.day == day,
            UsageDaily.upload_count < limit,
        )
        .values(upload_count=UsageDaily.upload_count + 1)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"今日上传次数已达上限（{limit} 次），请明天再试",
        )
    db.commit()
