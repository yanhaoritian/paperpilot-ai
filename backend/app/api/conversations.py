from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.models import Conversation, Library, Message, User
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

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conv_or_404(db: Session, conversation_id: str, owner_id: str) -> Conversation:
    conv = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == owner_id)
    )
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
    conv = Conversation(owner_id=user.id, title=title[:200], library_ids=library_ids)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return _to_out(db, conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    conv = _conv_or_404(db, conversation_id, user.id)
    msgs = db.scalars(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.asc())
    ).all()
    base = _to_out(db, conv)
    return ConversationDetail(
        **base.model_dump(),
        messages=[MessageOut.from_orm_msg(m) for m in msgs],
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
    conv.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conv)
    return _to_out(db, conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    conv = _conv_or_404(db, conversation_id, user.id)
    db.delete(conv)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{conversation_id}/messages")
def post_message_stream(
    conversation_id: str,
    body: ConversationMessageRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    conv = _conv_or_404(db, conversation_id, user.id)
    question = body.question.strip()
    library_ids = body.library_ids if body.library_ids is not None else list(conv.library_ids or [])
    library_ids = _validate_libraries(db, user.id, library_ids)
    if not library_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个知识库")

    consume_query_quota(db, user.id)

    # Persist user + placeholder assistant quickly so SSE can start immediately
    user_msg = Message(conversation_id=conv.id, role="user", content=question)
    db.add(user_msg)
    if conv.title == "新对话":
        conv.title = question[:40] + ("…" if len(question) > 40 else "")
    conv.library_ids = library_ids
    conv.updated_at = datetime.now(timezone.utc)
    db.flush()

    settings = get_settings()
    history_rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conv.id, Message.id != user_msg.id)
        .order_by(Message.created_at.desc())
        .limit(settings.chat_history_turns * 2)
    ).all()
    history = [
        {"role": m.role, "content": m.content}
        for m in reversed(history_rows)
        if m.role in {"user", "assistant"} and (m.content or "").strip()
    ]

    assistant = Message(
        conversation_id=conv.id,
        role="assistant",
        content="",
        citations=None,
        extra={"streaming": True},
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

    def event_gen() -> Iterator[str]:
        yield _sse(
            "meta",
            {
                "conversation_id": conv_id,
                "message_id": assistant_id,
                "user_message_id": user_msg_id,
            },
        )
        work = SessionLocal()
        final_payload: dict = {
            "answer": "",
            "citations": [],
            "retrieval_hit": 0,
            "degraded": True,
            "confidence": "low",
        }
        parts: list[str] = []
        try:
            for event, data in iter_agent_events(
                work,
                owner_id=owner_id,
                library_ids=library_ids,
                question=question,
                history=history,
                model=model,
                temperature=temperature,
            ):
                if event == "token":
                    text = str(data.get("text") or "")
                    parts.append(text)
                    yield _sse("token", {"text": text})
                elif event == "final":
                    final_payload = data
                elif event in {"status", "tool", "meta", "error"}:
                    yield _sse(event, data)
                else:
                    yield _sse(event, data)
        except Exception as exc:  # noqa: BLE001
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
        yield _sse(
            "citations",
            {
                "citations": citations,
                "confidence": final_payload.get("confidence") or "medium",
                "degraded": bool(final_payload.get("degraded")),
                "out_of_scope": False,
                "retrieval_hit": int(final_payload.get("retrieval_hit") or 0),
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
        yield _sse("done", {"ok": True})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
