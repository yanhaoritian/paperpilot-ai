from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import (
    Conversation,
    ConversationMemory,
    Document,
    DocumentStatus,
    IndexJob,
    IndexJobStatus,
    Message,
    WorkerHeartbeat,
)
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = False
    worker_alive: bool | None = None
    index_pending = 0
    index_running = 0
    index_failed = 0
    queue_pending = 0
    queue_running = 0
    queue_failed = 0
    queue_oldest_pending_seconds: int | None = None
    jobs_completed_last_hour = 0
    jobs_failed_last_hour = 0
    latest_heartbeat: datetime | None = None
    worker_heartbeat_age_seconds: int | None = None
    memory_enabled_conversations = 0
    memory_episodes = 0
    memory_compaction_pending = 0
    memory_last_updated_at: datetime | None = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    if db_ok:
        db = SessionLocal()
        try:
            status_counts = dict(
                db.execute(
                    select(Document.status, func.count())
                    .group_by(Document.status)
                ).all()
            )
            index_pending = int(status_counts.get(DocumentStatus.pending.value, 0))
            index_running = int(status_counts.get(DocumentStatus.processing.value, 0))
            index_failed = int(status_counts.get(DocumentStatus.failed.value, 0))
            queue_counts = dict(
                db.execute(
                    select(IndexJob.status, func.count()).group_by(IndexJob.status)
                ).all()
            )
            queue_pending = int(
                queue_counts.get(IndexJobStatus.pending.value, 0)
            )
            queue_running = int(
                queue_counts.get(IndexJobStatus.running.value, 0)
            )
            queue_failed = int(
                queue_counts.get(IndexJobStatus.failed.value, 0)
            )
            now = datetime.now(timezone.utc)
            oldest_pending = db.scalar(
                select(func.min(IndexJob.created_at)).where(
                    IndexJob.status == IndexJobStatus.pending.value
                )
            )
            if oldest_pending is not None:
                if oldest_pending.tzinfo is None:
                    oldest_pending = oldest_pending.replace(tzinfo=timezone.utc)
                queue_oldest_pending_seconds = max(
                    0,
                    int((now - oldest_pending).total_seconds()),
                )
            recent_cutoff = now - timedelta(hours=1)
            jobs_completed_last_hour = int(
                db.scalar(
                    select(func.count())
                    .select_from(IndexJob)
                    .where(
                        IndexJob.status == IndexJobStatus.done.value,
                        IndexJob.finished_at >= recent_cutoff,
                    )
                )
                or 0
            )
            jobs_failed_last_hour = int(
                db.scalar(
                    select(func.count())
                    .select_from(IndexJob)
                    .where(
                        IndexJob.status == IndexJobStatus.failed.value,
                        IndexJob.finished_at >= recent_cutoff,
                    )
                )
                or 0
            )
            if settings.conversation_memory_enabled:
                memory_enabled_conversations = int(
                    db.scalar(
                        select(func.count())
                        .select_from(Conversation)
                        .where(Conversation.memory_enabled.is_(True))
                    )
                    or 0
                )
                memory_episodes = int(
                    db.scalar(
                        select(func.count()).select_from(ConversationMemory)
                    )
                    or 0
                )
                memory_last_updated_at = db.scalar(
                    select(func.max(Conversation.memory_updated_at))
                )
                message_counts = (
                    select(
                        Message.conversation_id.label("conversation_id"),
                        func.max(Message.sequence).label("max_sequence"),
                    )
                    .group_by(Message.conversation_id)
                    .subquery()
                )
                backlog_threshold = max(
                    2,
                    int(settings.chat_history_turns) * 2,
                )
                memory_compaction_pending = int(
                    db.scalar(
                        select(func.count())
                        .select_from(Conversation)
                        .join(
                            message_counts,
                            message_counts.c.conversation_id
                            == Conversation.id,
                        )
                        .where(
                            Conversation.memory_enabled.is_(True),
                            (
                                message_counts.c.max_sequence
                                - Conversation.summarized_message_count
                            )
                            > backlog_threshold,
                        )
                    )
                    or 0
                )
            if settings.index_external_worker:
                latest_heartbeat = db.scalar(
                    select(func.max(WorkerHeartbeat.last_seen_at)).where(
                        WorkerHeartbeat.worker_kind == "index"
                    )
                )
                if latest_heartbeat is None:
                    worker_alive = False
                else:
                    if latest_heartbeat.tzinfo is None:
                        latest_heartbeat = latest_heartbeat.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - latest_heartbeat).total_seconds()
                    worker_heartbeat_age_seconds = max(0, int(age))
                    worker_alive = age <= max(5, settings.index_worker_stale_seconds)
        except Exception:  # noqa: BLE001
            worker_alive = False if settings.index_external_worker else None
        finally:
            db.close()

    detail = None
    if settings.health_verbose:
        detail = {
            "base_url": settings.openai_base_url,
            "default_model": settings.default_model,
            "embedding_model": settings.embedding_model,
            "index_external_worker": settings.index_external_worker,
            "latest_worker_heartbeat": (
                latest_heartbeat.isoformat() if latest_heartbeat else None
            ),
        }

    return HealthResponse(
        status="ok" if db_ok and worker_alive is not False else "degraded",
        database=db_ok,
        has_api_key=bool(settings.openai_api_key),
        worker_alive=worker_alive,
        worker_heartbeat_age_seconds=worker_heartbeat_age_seconds,
        index_pending=index_pending,
        index_running=index_running,
        index_failed=index_failed,
        queue_pending=queue_pending,
        queue_running=queue_running,
        queue_failed=queue_failed,
        queue_oldest_pending_seconds=queue_oldest_pending_seconds,
        jobs_completed_last_hour=jobs_completed_last_hour,
        jobs_failed_last_hour=jobs_failed_last_hour,
        conversation_memory_enabled=bool(
            settings.conversation_memory_enabled
        ),
        memory_enabled_conversations=memory_enabled_conversations,
        memory_episodes=memory_episodes,
        memory_compaction_pending=memory_compaction_pending,
        memory_last_updated_at=memory_last_updated_at,
        detail=detail,
    )
