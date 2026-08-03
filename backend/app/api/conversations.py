from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.models import (
    Conversation,
    ConversationMemory,
    Document,
    Library,
    Message,
    User,
)
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationMessageRequest,
    ConversationOut,
    ConversationUpdate,
    MessageOut,
)
from app.services.pdf_tools import iter_agent_events
from app.services.quotas import consume_query_quota
from app.services.response_cache import (
    corpus_revision,
    make_query_cache_key,
    query_cache,
)
from app.services.intent import QueryIntent, detect_intent
from app.services.conversation_memory import (
    clear_conversation_memory,
    refresh_conversation_memory,
)
from app.services.research_skills import (
    resolve_research_skill,
    validate_skill_answer,
)
from app.services.usage_tracking import record_cache_hit, usage_scope

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)


def _conv_or_404(
    db: Session,
    conversation_id: str,
    owner_id: str,
    *,
    lock: bool = False,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.owner_id == owner_id,
    )
    if lock:
        statement = statement.with_for_update()
    conv = db.scalar(statement)
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return conv


def _validate_libraries(db: Session, owner_id: str, library_ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys([x.strip() for x in library_ids if x and x.strip()]))
    if not ids:
        return []
    owned = db.scalars(select(Library.id).where(Library.owner_id == owner_id, Library.id.in_(ids))).all()
    if len(set(owned)) != len(ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return ids


def _message_count(db: Session, conversation_id: str) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id))
        or 0
    )


def _to_out(db: Session, conv: Conversation) -> ConversationOut:
    return ConversationOut(
        id=conv.id,
        title=conv.title,
        library_ids=list(conv.library_ids or []),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=_message_count(db, conv.id),
        memory_enabled=bool(conv.memory_enabled),
        memory_revision=int(conv.memory_revision or 0),
        summarized_message_count=int(conv.summarized_message_count or 0),
        memory_updated_at=conv.memory_updated_at,
    )


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ConversationOut]:
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.owner_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    ).all()
    return [_to_out(db, c) for c in rows]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    body: ConversationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationOut:
    library_ids = _validate_libraries(db, user.id, body.library_ids or [])
    title = (body.title or "").strip() or "新对话"
    conv = Conversation(
        owner_id=user.id,
        title=title[:200],
        library_ids=library_ids,
        memory_enabled=bool(body.memory_enabled),
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _to_out(db, conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    message_limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    conv = _conv_or_404(db, conversation_id, user.id)
    newest = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.sequence.desc())
            .limit(message_limit)
        ).all()
    )
    msgs = list(reversed(newest))
    base = _to_out(db, conv)
    memory_entry_count = int(
        db.scalar(
            select(func.count())
            .select_from(ConversationMemory)
            .where(
                ConversationMemory.conversation_id == conv.id,
                ConversationMemory.owner_id == user.id,
            )
        )
        or 0
    )
    return ConversationDetail(
        **base.model_dump(),
        messages=[MessageOut.from_orm_msg(m) for m in msgs],
        messages_truncated=base.message_count > len(msgs),
        memory_summary=conv.memory_summary,
        memory_entry_count=memory_entry_count,
    )


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationOut:
    conv = _conv_or_404(db, conversation_id, user.id)
    if body.title is not None:
        conv.title = body.title.strip()[:200]
    if body.library_ids is not None:
        conv.library_ids = _validate_libraries(db, user.id, body.library_ids)
    if body.memory_enabled is not None:
        if not body.memory_enabled:
            clear_conversation_memory(db, conv, disable=True)
        else:
            conv.memory_enabled = True
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    return _to_out(db, conv)


@router.delete(
    "/{conversation_id}/memory",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation_memory(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    conv = _conv_or_404(db, conversation_id, user.id, lock=True)
    clear_conversation_memory(db, conv, disable=True)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    conv = _conv_or_404(db, conversation_id, user.id, lock=True)
    db.delete(conv)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@contextmanager
def _closing_event_iterator(events: Iterator):
    """Always close the nested agent iterator when the SSE consumer goes away."""

    iterator = iter(events)
    disconnecting = False
    try:
        yield iterator
    except GeneratorExit:
        # Never turn a client disconnect into a business error. In particular,
        # the caller must not attempt another SSE yield while close() unwinds.
        disconnecting = True
        raise
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                if not disconnecting:
                    raise
                # A broken nested generator must not replace GeneratorExit and
                # provoke "generator ignored GeneratorExit" in StreamingResponse.
                logger.exception(
                    "agent event iterator close failed during client disconnect"
                )


@router.post("/{conversation_id}/messages")
def post_message_stream(
    conversation_id: str,
    body: ConversationMessageRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    # Serialize sequence allocation for concurrent sends in the same thread.
    conv = _conv_or_404(db, conversation_id, user.id, lock=True)
    question = body.question.strip()
    library_ids = body.library_ids if body.library_ids is not None else list(conv.library_ids or [])
    library_ids = _validate_libraries(db, user.id, library_ids)
    if not library_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个知识库")
    settings = get_settings()
    if not settings.chat_model_is_allowed(body.model):
        raise HTTPException(status_code=400, detail="所选模型不在服务端允许列表中")
    intent = detect_intent(question)
    try:
        skill = resolve_research_skill(
            body.skill_id,
            question=question,
            intent=intent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if intent == QueryIntent.COMPARE or skill.cover_all_documents:
        ready_count = int(
            db.scalar(
                select(func.count())
                .select_from(Document)
                .where(
                    Document.owner_id == user.id,
                    Document.library_id.in_(library_ids),
                    Document.status == "ready",
                )
            )
            or 0
        )
        if ready_count > settings.compare_max_documents:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"当前选择中有 {ready_count} 篇可检索文献，超过单次全量对比上限 "
                    f"{settings.compare_max_documents} 篇；请缩小知识库范围后重试"
                ),
            )

    next_sequence = int(
        db.scalar(
            select(func.max(Message.sequence)).where(
                Message.conversation_id == conv.id
            )
        )
        or 0
    ) + 1

    # Persist user + placeholder assistant quickly so SSE can start immediately
    user_msg = Message(
        conversation_id=conv.id,
        sequence=next_sequence,
        role="user",
        content=question,
        extra={
            "library_ids": library_ids,
            "skill_id": skill.id,
            "skill_version": skill.version,
        },
    )
    db.add(user_msg)
    if conv.title == "新对话":
        conv.title = question[:40] + ("…" if len(question) > 40 else "")
    conv.library_ids = library_ids
    conv.updated_at = datetime.now(timezone.utc)
    db.flush()

    recent_messages = max(2, int(settings.chat_history_turns) * 2)
    history_limit = recent_messages
    history_filters = [
        Message.conversation_id == conv.id,
        Message.id != user_msg.id,
    ]
    if settings.conversation_memory_enabled and conv.memory_enabled:
        history_limit = max(
            recent_messages,
            int(settings.memory_summary_trigger_messages),
            recent_messages + int(settings.memory_summary_batch_messages),
        )
        summarized_through = int(conv.summarized_message_count or 0)
        if summarized_through > 0:
            history_filters.append(Message.sequence > summarized_through)
    history_rows = db.scalars(
        select(Message)
        .where(*history_filters)
        .order_by(Message.sequence.desc())
        .limit(history_limit)
    ).all()
    history = [
        {"role": m.role, "content": m.content}
        for m in reversed(history_rows)
        if m.role in {"user", "assistant"} and (m.content or "").strip()
    ]
    # Cache only the first turn. A compacted old thread can have no raw history
    # in the active window while still carrying different memory state.
    cacheable = next_sequence == 1 and settings.response_cache_ttl_ms > 0
    cache_key = ""
    cached_final: dict | None = None
    if cacheable:
        revision = corpus_revision(
            db,
            owner_id=user.id,
            library_ids=library_ids,
        )
        cache_key = make_query_cache_key(
            owner_id=user.id,
            library_ids=library_ids,
            question=question,
            model=body.model or settings.default_model,
            temperature=body.temperature,
            intent=intent.value,
            history_fingerprint="",
            corpus_revision=revision,
            prompt_version=settings.prompt_version,
            skill_id=skill.id,
            skill_version=skill.version,
        )
        hit = query_cache().get(cache_key)
        if isinstance(hit, dict) and hit.get("answer"):
            cached_final = hit

    if cached_final is None:
        consume_query_quota(db, user.id, commit=False)

    assistant = Message(
        conversation_id=conv.id,
        sequence=next_sequence + 1,
        role="assistant",
        content="",
        citations=None,
        extra={
            "streaming": True,
            "cache_hit": bool(cached_final),
            "library_ids": library_ids,
            "skill_id": skill.id,
            "skill_version": skill.version,
        },
    )
    db.add(assistant)
    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant)

    # Capture primitives — ORM objects expire after request session closes
    conv_id = str(conv.id)
    user_msg_id = str(user_msg.id)
    assistant_id = str(assistant.id)
    owner_id = str(user.id)
    model = body.model
    temperature = body.temperature
    ttl_ms = int(settings.response_cache_ttl_ms or 0)
    memory_active = bool(
        settings.conversation_memory_enabled and conv.memory_enabled
    )
    request_id = getattr(request.state, "request_id", None)
    skill_id = skill.id
    skill_version = skill.version
    skill_title = skill.title
    skill_prompt = skill.system_prompt()
    force_document_coverage = bool(skill.cover_all_documents)

    def event_gen() -> Iterator[str]:
        stream_ok = True
        yield _sse(
            "meta",
            {
                "conversation_id": conv_id,
                "message_id": assistant_id,
                "user_message_id": user_msg_id,
                "cache_hit": bool(cached_final),
                "skill": {
                    "id": skill_id,
                    "version": skill_version,
                    "title": skill_title,
                },
            },
        )
        final_payload: dict = {
            "answer": "",
            "citations": [],
            "retrieval_hit": 0,
            "degraded": True,
            "confidence": "low",
        }
        parts: list[str] = []

        if cached_final is not None:
            with usage_scope(
                request_id=request_id,
                user_id=owner_id,
                conversation_id=conv_id,
                message_id=assistant_id,
                skill_id=skill_id,
                skill_version=skill_version,
            ) as attribution:
                record_cache_hit(
                    model=model or settings.default_model,
                    attribution=attribution,
                )
            answer = str(cached_final.get("answer") or "")
            # Replay as coarse tokens so the UI still streams.
            step = max(24, len(answer) // 40 or 24)
            for i in range(0, len(answer), step):
                piece = answer[i : i + step]
                parts.append(piece)
                yield _sse("token", {"text": piece})
            final_payload = {
                "answer": answer,
                "citations": cached_final.get("citations") or [],
                "retrieval_hit": int(cached_final.get("retrieval_hit") or 0),
                "degraded": bool(cached_final.get("degraded")),
                "confidence": cached_final.get("confidence") or "medium",
                "skill_id": skill_id,
                "skill_version": skill_version,
                "skill_validation": cached_final.get("skill_validation"),
            }
        else:
            work = SessionLocal()
            try:
                agent_events = iter_agent_events(
                    work,
                    owner_id=owner_id,
                    conversation_id=conv_id,
                    library_ids=library_ids,
                    question=question,
                    history=history,
                    model=model,
                    temperature=temperature,
                    skill_prompt=skill_prompt,
                    force_document_coverage=force_document_coverage,
                )
                with _closing_event_iterator(agent_events) as managed_events:
                    while True:
                        try:
                            # A sync StreamingResponse may resume this generator
                            # in a different context. Keep ContextVar tokens within
                            # one next() call instead of spanning the outer yield.
                            with usage_scope(
                                request_id=request_id,
                                user_id=owner_id,
                                conversation_id=conv_id,
                                message_id=assistant_id,
                                skill_id=skill_id,
                                skill_version=skill_version,
                            ):
                                event, data = next(managed_events)
                        except StopIteration:
                            break

                        if event == "token":
                            text = str(data.get("text") or "")
                            parts.append(text)
                            yield _sse("token", {"text": text})
                        elif event == "final":
                            final_payload = data
                        elif event == "error":
                            stream_ok = False
                            yield _sse(event, data)
                        elif event in {"status", "tool", "meta"}:
                            yield _sse(event, data)
                        else:
                            yield _sse(event, data)
            except Exception as exc:  # noqa: BLE001
                stream_ok = False
                logger.exception(
                    "conversation agent stream failed conversation_id=%s message_id=%s",
                    conv_id,
                    assistant_id,
                )
                err = f"生成失败：{exc}"
                if not parts:
                    parts.append(err)
                    yield _sse("token", {"text": err})
                yield _sse("error", {"detail": str(exc)[:300]})
                final_payload = {
                    "answer": "".join(parts),
                    "citations": [],
                    "retrieval_hit": 0,
                    "degraded": True,
                    "confidence": "low",
                }
            finally:
                work.close()

        answer = str(final_payload.get("answer") or "".join(parts)).strip() or "无法从文献中得出可靠结论。"
        citations = final_payload.get("citations") or []
        skill_validation = final_payload.get("skill_validation") or validate_skill_answer(
            skill,
            answer=answer,
            citations=citations,
            degraded=bool(final_payload.get("degraded")),
        )
        final_payload["skill_id"] = skill_id
        final_payload["skill_version"] = skill_version
        final_payload["skill_validation"] = skill_validation
        if (
            cached_final is None
            and cacheable
            and cache_key
            and not final_payload.get("degraded")
        ):
            query_cache().set(
                cache_key,
                {
                    "answer": answer,
                    "citations": citations,
                    "retrieval_hit": int(final_payload.get("retrieval_hit") or 0),
                    "degraded": bool(final_payload.get("degraded")),
                    "confidence": final_payload.get("confidence") or "medium",
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "skill_validation": skill_validation,
                },
                ttl_ms=ttl_ms,
            )
        yield _sse(
            "citations",
            {
                "citations": citations,
                "confidence": final_payload.get("confidence") or "medium",
                "degraded": bool(final_payload.get("degraded")),
                "out_of_scope": False,
                "retrieval_hit": int(final_payload.get("retrieval_hit") or 0),
                "skill": {
                    "id": skill_id,
                    "version": skill_version,
                    "title": skill_title,
                    "validation": skill_validation,
                },
            },
        )
        _finalize_assistant(
            assistant_id,
            answer,
            citations,
            {
                "confidence": final_payload.get("confidence"),
                "degraded": bool(final_payload.get("degraded")),
                "retrieval_hit": int(final_payload.get("retrieval_hit") or 0),
                "cache_hit": bool(cached_final),
                "library_ids": library_ids,
                "skill_id": skill_id,
                "skill_version": skill_version,
                "skill_title": skill_title,
                "skill_validation": skill_validation,
                "memory": final_payload.get("memory") or {
                    "enabled": memory_active
                },
            },
        )
        s = SessionLocal()
        try:
            c = s.get(Conversation, conv_id)
            if c and c.owner_id == owner_id:
                c.updated_at = datetime.now(timezone.utc)
                s.commit()
        finally:
            s.close()
        yield _sse("done", {"ok": stream_ok})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
        background=BackgroundTask(
            refresh_conversation_memory,
            conv_id,
            owner_id,
            model=model,
            request_id=request_id,
            message_id=assistant_id,
            skill_id=skill_id,
            skill_version=skill_version,
        ),
    )


def _finalize_assistant(
    message_id: str,
    content: str,
    citations: list,
    extra: dict,
) -> None:
    db = SessionLocal()
    try:
        msg = db.get(Message, message_id)
        if not msg:
            return
        msg.content = content
        msg.citations = citations
        msg.extra = extra
        db.commit()
    finally:
        db.close()
