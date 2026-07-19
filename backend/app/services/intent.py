"""Lightweight rule-based query intent for lab RAG (no LLM classifier)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, Library


class QueryIntent(str, Enum):
    CATALOG = "catalog"  # list docs in selected libraries
    COMPARE = "compare"  # cross-doc commonalities / differences / overview
    QA = "qa"  # default evidence QA


_CATALOG_RE = re.compile(
    r"(有哪些|哪些|列出|清单|列表|一共|共有|多少).{0,12}(文献|论文|文档|文件|pdf)|"
    r"(文献|论文|文档|文件).{0,8}(有哪些|列表|清单|多少|几篇)|"
    r"(当前|这个|本).{0,4}(库|知识库).{0,8}(有什么|有哪些|内容|文献)|"
    r"上传了(什么|哪些|几)",
    re.I,
)

# Fuzzy / meta compare: "两篇共同点", "对比差异", "这些论文都讲什么"
_COMPARE_RE = re.compile(
    r"(共同|相同|相似|共性|共同点|相通|一致).{0,8}(点|处|性|之处)?|"
    r"(对比|比较|差异|区别|不同|异同)|"
    r"(两|几|多)篇.{0,12}(共同|对比|比较|差异|关系|联系)|"
    r"(这|那|所选|库(里|中)?|知识库).{0,10}(两|几)?篇.{0,16}(共同|对比|比较|异同|关系)|"
    r"(概括|总结|综述).{0,8}(这些|两篇|几篇|库(里|中)?|文献|论文)|"
    r"(文献|论文).{0,6}(之间|彼此).{0,8}(关系|联系|共同|差异)",
    re.I,
)


@dataclass(frozen=True)
class DocInventoryItem:
    document_id: str
    file_name: str
    library_id: str
    library_name: str
    status: str
    page_count: int


def detect_intent(question: str) -> QueryIntent:
    q = (question or "").strip()
    if not q:
        return QueryIntent.QA
    if _CATALOG_RE.search(q):
        return QueryIntent.CATALOG
    if _COMPARE_RE.search(q):
        return QueryIntent.COMPARE
    return QueryIntent.QA


def list_library_documents(
    db: Session,
    *,
    owner_id: str,
    library_ids: list[str],
) -> list[DocInventoryItem]:
    if not library_ids:
        return []
    libs = db.scalars(
        select(Library).where(Library.owner_id == owner_id, Library.id.in_(library_ids))
    ).all()
    lib_name = {lib.id: lib.name for lib in libs}
    docs = db.scalars(
        select(Document)
        .where(
            Document.owner_id == owner_id,
            Document.library_id.in_(library_ids),
        )
        .order_by(Document.created_at.asc())
    ).all()
    return [
        DocInventoryItem(
            document_id=str(d.id),
            file_name=str(d.file_name),
            library_id=str(d.library_id),
            library_name=str(lib_name.get(d.library_id, "未命名库")),
            status=str(d.status or "unknown"),
            page_count=int(d.page_count or 0),
        )
        for d in docs
    ]


def format_inventory_block(items: list[DocInventoryItem]) -> str:
    if not items:
        return "所选知识库文献清单：（空）"
    lines = [f"所选知识库文献清单（共 {len(items)} 篇，按入库顺序）："]
    for i, d in enumerate(items, 1):
        lines.append(
            f"{i}. 《{d.file_name}》（库：{d.library_name}；状态：{d.status}；约 {d.page_count} 页；document_id={d.document_id}）"
        )
    ready = sum(1 for d in items if d.status == "ready")
    if ready < len(items):
        lines.append(f"其中已索引完成可检索：{ready} 篇。")
    return "\n".join(lines)


def format_catalog_answer(items: list[DocInventoryItem]) -> str:
    if not items:
        return "所选知识库中目前还没有文献。请先在右侧上传 PDF 并等待索引完成。"
    lines = [f"所选知识库共有 {len(items)} 篇文献（按入库时间）："]
    for i, d in enumerate(items, 1):
        lines.append(f"{i}. 《{d.file_name}》（库：{d.library_name}；状态：{d.status}；约 {d.page_count} 页）")
    ready = sum(1 for d in items if d.status == "ready")
    if ready < len(items):
        lines.append(f"其中已索引完成可检索：{ready} 篇；其余仍在处理或失败，可在右侧文档列表查看。")
    return "\n".join(lines)


# Back-compat for existing tests importing from query.py
def is_catalog_question(question: str) -> bool:
    return detect_intent(question) == QueryIntent.CATALOG


def is_compare_question(question: str) -> bool:
    return detect_intent(question) == QueryIntent.COMPARE
