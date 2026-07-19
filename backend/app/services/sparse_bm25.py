"""In-process BM25 sparse recall (lab stand-in for Elasticsearch BM25)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import TypeVar

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

T = TypeVar("T")


def tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    toks: list[str] = []
    toks.extend(re.findall(r"[a-z0-9_]+", text))
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for span in cjk:
        if len(span) == 1:
            toks.append(span)
        else:
            toks.extend(span[i : i + 2] for i in range(len(span) - 1))
    return toks


def bm25_rank(
    question: str,
    corpus_rows: Sequence[tuple[object, str]],
    *,
    top_n: int,
    to_retrieved: Callable[[object, str, float], T],
) -> list[T]:
    """corpus_rows: sequence of (chunk, file_name)."""
    if not corpus_rows or top_n <= 0:
        return []
    tokenized_corpus = [
        tokenize(f"{getattr(c, 'section_path', None) or ''} {getattr(c, 'text', '') or ''}")
        for c, _ in corpus_rows
    ]
    if not any(tokenized_corpus):
        return []
    q_toks = tokenize(question)
    if not q_toks:
        return []
    try:
        bm25 = BM25Okapi(tokenized_corpus)
        scores = [float(s) for s in bm25.get_scores(q_toks)]
    except Exception:  # noqa: BLE001
        logger.exception("BM25Okapi failed")
        scores = []

    # Tiny corpora: classic BM25 IDF can collapse to 0 (log((N-n+0.5)/(n+0.5))=0).
    if not scores or max(scores) <= 0:
        qset = set(q_toks)
        scores = []
        for toks in tokenized_corpus:
            overlap = len(qset.intersection(toks))
            tf_bonus = sum(1 for t in toks if t in qset)
            scores.append(float(overlap) + 0.1 * tf_bonus)

    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    out: list[T] = []
    for i in ranked:
        s = scores[i]
        if s <= 0:
            break
        chunk, file_name = corpus_rows[i]
        out.append(to_retrieved(chunk, file_name, s))
        if len(out) >= top_n:
            break
    return out
