"""Paragraph-aware chunking ported from rag.js."""

from __future__ import annotations


def clamp(n: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, n))


def page_for_char_offset(char_index: int, text_length: int, num_pages: int) -> int | None:
    pages = int(num_pages or 0)
    if pages <= 0 or text_length <= 0:
        return None
    ratio = clamp(char_index / text_length, 0, 1)
    page = int(ratio * pages) + 1
    return int(clamp(page, 1, pages))


def normalize_whitespace(text: str) -> str:
    t = str(text or "").replace("\r", "")
    while " \n" in t or "\t\n" in t:
        t = t.replace(" \n", "\n").replace("\t\n", "\n")
    while "\n\n\n" in t:
        t = t.replace("\n\n\n", "\n\n")
    return t.strip()


def extract_paragraphs(normalized: str) -> list[dict]:
    if not normalized:
        return []
    out: list[dict] = []
    i = 0
    while i < len(normalized):
        sep = normalized.find("\n\n", i)
        end = len(normalized) if sep == -1 else sep
        raw = normalized[i:end]
        text = " ".join(raw.replace("\n", " ").split())
        if text:
            lead = len(raw) - len(raw.lstrip())
            start = i + lead
            trailing = len(raw) - len(raw.rstrip())
            end_trim = end - trailing
            out.append({"text": text, "start": start, "end": max(end_trim, start)})
        i = len(normalized) if sep == -1 else sep + 2
    return out


def slice_with_overlap(text: str, start: int, target_len: int, overlap_chars: int) -> tuple[str, int]:
    end = min(len(text), start + target_len)
    piece = text[start:end].strip()
    next_start = end - overlap_chars
    return piece, max(start + 1, next_start)


def split_long_paragraph(
    para: str,
    para_start: int,
    target_len: int,
    overlap_chars: int,
    full_len: int,
    num_pages: int,
    chunk_index_ref: list[int],
) -> list[dict]:
    chunks: list[dict] = []
    start = 0
    body = para
    while start < len(body):
        piece, next_start = slice_with_overlap(body, start, target_len, overlap_chars)
        if not piece:
            break
        char_start = para_start + start
        char_end = para_start + start + len(piece)
        page_start = page_for_char_offset(char_start, full_len, num_pages)
        page_end = page_for_char_offset(min(char_end - 1, full_len - 1), full_len, num_pages)
        chunk_index_ref[0] += 1
        chunks.append(
            {
                "paragraph_index": chunk_index_ref[0],
                "char_start": char_start,
                "char_end": char_end,
                "text": piece,
                "page_start": page_start,
                "page_end": page_end if page_end is not None else page_start,
            }
        )
        if next_start <= start:
            break
        start = next_start
    return chunks


def chunk_document_text(
    full_text: str,
    num_pages: int,
    *,
    target_chars: int = 2000,
    min_chars: int = 450,
    overlap_ratio: float = 0.15,
) -> list[dict]:
    normalized = normalize_whitespace(full_text)
    if not normalized:
        return []
    target = int(clamp(target_chars or 2000, 500, 8000))
    min_c = int(clamp(min_chars or 450, 200, target - 1))
    overlap_ratio = float(clamp(overlap_ratio or 0.15, 0, 0.45))
    overlap_chars = int(target * overlap_ratio)

    paragraphs = extract_paragraphs(normalized)
    full_len = len(normalized)
    chunk_index_ref = [0]
    out: list[dict] = []

    if not paragraphs:
        return split_long_paragraph(
            normalized, 0, target, overlap_chars, full_len, num_pages, chunk_index_ref
        )

    group: list[dict] = []
    group_start = -1
    group_end = -1

    def flush_group() -> None:
        nonlocal group, group_start, group_end
        if not group:
            return
        merged = "\n\n".join(p["text"] for p in group)
        start = group_start
        end = group_end
        if len(merged) <= target:
            chunk_index_ref[0] += 1
            page_start = page_for_char_offset(start, full_len, num_pages)
            page_end = page_for_char_offset(min(end - 1, full_len - 1), full_len, num_pages)
            out.append(
                {
                    "paragraph_index": chunk_index_ref[0],
                    "char_start": start,
                    "char_end": end,
                    "text": merged,
                    "page_start": page_start,
                    "page_end": page_end if page_end is not None else page_start,
                }
            )
        else:
            out.extend(
                split_long_paragraph(
                    merged, start, target, overlap_chars, full_len, num_pages, chunk_index_ref
                )
            )
        group = []
        group_start = -1
        group_end = -1

    for p in paragraphs:
        projected = (
            "\n\n".join(x["text"] for x in group) + ("\n\n" if group else "") + p["text"]
            if group
            else p["text"]
        )
        if len(projected) > target and group:
            flush_group()
            projected = p["text"]
        if len(p["text"]) > target:
            flush_group()
            out.extend(
                split_long_paragraph(
                    p["text"], p["start"], target, overlap_chars, full_len, num_pages, chunk_index_ref
                )
            )
            continue
        if not group:
            group_start = p["start"]
        group.append(p)
        group_end = p["end"]
        if len(projected) >= min_c:
            flush_group()
    flush_group()
    return out
