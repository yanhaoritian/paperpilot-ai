from __future__ import annotations

from app.config import get_settings
from app.services.chunking import clamp
from app.services.structure import StructuredBlock


def chunks_from_blocks(
    blocks: list[StructuredBlock],
    *,
    target_chars: int | None = None,
    min_chars: int | None = None,
    overlap_ratio: float | None = None,
    max_chunks: int | None = None,
) -> list[dict]:
    """L3: semantic chunks from structured blocks with real page + section metadata."""
    settings = get_settings()
    target = int(clamp(target_chars or settings.chunk_target_chars, 500, 8000))
    min_c = int(clamp(min_chars or settings.chunk_min_chars, 200, target - 1))
    overlap_ratio = float(clamp(overlap_ratio if overlap_ratio is not None else settings.chunk_overlap_ratio, 0, 0.45))
    overlap = int(target * overlap_ratio)
    limit = max_chunks or settings.max_chunks

    out: list[dict] = []
    idx = 0
    group: list[StructuredBlock] = []

    def flush(force: bool = False) -> None:
        nonlocal group, idx
        if not group:
            return
        text = "\n\n".join(b.text for b in group).strip()
        if not text:
            group = []
            return
        if len(text) <= target or force:
            pieces = [text]
        else:
            pieces = _split_text(text, target, overlap)
        for piece in pieces:
            idx += 1
            out.append(
                {
                    "paragraph_index": idx,
                    "text": piece,
                    "page_start": group[0].page_start,
                    "page_end": group[-1].page_end,
                    "section_path": group[-1].section_path or group[0].section_path,
                    "block_ids": [b.block_id for b in group],
                    "role": group[0].role if len(group) == 1 else "paragraph",
                    "extra": {
                        "clause_id": group[0].extra.get("clause_id"),
                        "metric_key": group[0].extra.get("metric_key"),
                    },
                }
            )
            if len(out) >= limit:
                group = []
                return
        group = []

    for b in blocks:
        if b.role in {"title", "section_heading", "caption", "table", "footnote"}:
            flush()
            group = [b]
            flush(force=True)
            if len(out) >= limit:
                return out[:limit]
            continue
        projected = ("\n\n".join(x.text for x in group) + ("\n\n" if group else "") + b.text)
        if group and len(projected) > target:
            flush()
        group.append(b)
        if len("\n\n".join(x.text for x in group)) >= min_c:
            flush()
        if len(out) >= limit:
            return out[:limit]
    flush()
    return out[:limit]


def _split_text(text: str, target: int, overlap: int) -> list[str]:
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target)
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return parts


def rule_context_prefix(*, file_name: str, section_path: str, page_start: int | None, role: str) -> str:
    page = f"p.{page_start}" if page_start else "p.?"
    sec = section_path or "正文"
    return f"《{file_name}》§{sec} {page}（{role}）："


def build_contextual_prefixes(
    chunks: list[dict],
    *,
    file_name: str,
    use_llm: bool = True,
) -> list[str]:
    """Return context prefixes aligned with chunks (for embedding only)."""
    settings = get_settings()
    prefixes: list[str] = []
    for c in chunks:
        prefixes.append(
            rule_context_prefix(
                file_name=file_name,
                section_path=str(c.get("section_path") or ""),
                page_start=c.get("page_start"),
                role=str(c.get("role") or "paragraph"),
            )
        )
    if not use_llm or not settings.contextual_chunk_enabled:
        return prefixes
    # Optional LLM enrichment for a sample; keep rule prefix as base to bound cost
    try:
        from app.services.openai_client import chat_json

        # Only enrich first N to control cost
        n = min(8, len(chunks))
        for i in range(n):
            c = chunks[i]
            prompt = [
                {
                    "role": "system",
                    "content": "为检索片段写一句中文上下文前缀（文档+章节+本段作用），不超过40字。只输出JSON: {\"prefix\":\"...\"}",
                },
                {
                    "role": "user",
                    "content": (
                        f"文件:{file_name}\n章节:{c.get('section_path')}\n页:{c.get('page_start')}\n"
                        f"角色:{c.get('role')}\n片段:{c.get('text','')[:400]}"
                    ),
                },
            ]
            raw = chat_json(prompt, temperature=0.1)
            pref = str(raw.get("prefix") or "").strip()
            if pref:
                prefixes[i] = pref if pref.endswith("：") or pref.endswith(":") else pref + "："
    except Exception:
        pass
    return prefixes
