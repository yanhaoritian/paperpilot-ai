from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Library, User
from app.schemas import QueryRequest, QueryResponse
from app.services.doc_context import ensure_document_snapshots, format_document_context_cards
from app.services.generate import generate_answer
from app.services.hybrid_retrieve import hybrid_retrieve
from app.services.intent import (
    QueryIntent,
    detect_intent,
    format_catalog_answer,
    format_inventory_block,
    list_library_documents,
)
from app.services.quotas import consume_query_quota
from app.services.response_cache import make_query_cache_key, query_cache

router = APIRouter(prefix="/api", tags=["query"])


# Back-compat for tests
def _is_catalog_question(question: str) -> bool:
    return detect_intent(question) == QueryIntent.CATALOG


def _catalog_answer(db: Session, *, owner_id: str, library_ids: list[str]) -> QueryResponse:
    items = list_library_documents(db, owner_id=owner_id, library_ids=library_ids)
    return QueryResponse(
        answer=format_catalog_answer(items),
        citations=[],
        confidence="high",
        retrieval_hit=len(items),
        degraded=False,
        out_of_scope=False,
    )


@router.post("/query", response_model=QueryResponse)
def query_libraries(
    body: QueryRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QueryResponse:
    library_ids = list(dict.fromkeys([x.strip() for x in body.library_ids if x and x.strip()]))
    if not library_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请至少选择一个知识库")

    owned = db.scalars(
        select(Library.id).where(Library.owner_id == user.id, Library.id.in_(library_ids))
    ).all()
    owned_set = set(owned)
    if len(owned_set) != len(library_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")

    question = body.question.strip()
    intent = detect_intent(question)
    settings = get_settings()
    cache_key = make_query_cache_key(
        owner_id=user.id,
        library_ids=library_ids,
        question=question,
        model=body.model,
        temperature=body.temperature,
        intent=intent.value,
    )
    if settings.response_cache_ttl_ms > 0:
        cached = query_cache().get(cache_key)
        if isinstance(cached, dict):
            return QueryResponse(**cached)

    consume_query_quota(db, user.id)

    if intent == QueryIntent.CATALOG:
        result = _catalog_answer(db, owner_id=user.id, library_ids=library_ids)
        if settings.response_cache_ttl_ms > 0:
            query_cache().set(
                cache_key,
                result.model_dump(),
                ttl_ms=settings.response_cache_ttl_ms,
            )
        return result

    inventory = list_library_documents(db, owner_id=user.id, library_ids=library_ids)
    inventory_text = format_inventory_block(inventory)
    cards = ensure_document_snapshots(db, owner_id=user.id, library_ids=library_ids)
    cards_text = format_document_context_cards(cards)
    compare = intent == QueryIntent.COMPARE
    retrieved = hybrid_retrieve(
        db,
        owner_id=user.id,
        library_ids=library_ids,
        question=question,
        cover_all_docs=compare or len(inventory) <= 3,
    )
    result = generate_answer(
        question,
        retrieved,
        model=body.model,
        temperature=body.temperature,
        inventory_text=inventory_text,
        document_cards_text=cards_text,
        intent_hint=(
            "这是跨文献对比/共同点问题：必须覆盖文献清单与 Context 卡片中的各篇，禁止声称只检索到一篇。"
            "排版：先自然段总述，异同处可用 Markdown 表格，最后一段小结；不要用 --- 装饰线。"
            if compare
            else None
        ),
    )
    if settings.response_cache_ttl_ms > 0:
        query_cache().set(cache_key, result, ttl_ms=settings.response_cache_ttl_ms)
    return QueryResponse(**result)
