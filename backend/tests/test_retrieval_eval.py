from __future__ import annotations

import os
from pathlib import Path

import pytest

# Offline eval must not depend on real API keys / Postgres
os.environ.setdefault("JWT_REQUIRE_STRONG", "0")
os.environ.setdefault("AUTH_EXPOSE_CODE", "1")
os.environ.setdefault("INDEX_RECOVER_ON_STARTUP", "0")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("OCR_ENABLED", "0")

from app.config import get_settings

get_settings.cache_clear()


def test_retrieval_gold_regression():
    from evals.run_retrieval_eval import MIN_MRR, MIN_RECALL_AT_K, run_retrieval_eval

    get_settings.cache_clear()
    settings = get_settings()
    settings.embedding_provider = "local"
    settings.ocr_enabled = False
    settings.jwt_require_strong = False

    summary = run_retrieval_eval()
    assert summary.recall_at_k >= MIN_RECALL_AT_K, (
        f"Recall@{6}={summary.recall_at_k:.3f} < {MIN_RECALL_AT_K}; "
        f"details={[r.__dict__ for r in summary.results]}"
    )
    assert summary.mrr >= MIN_MRR, f"MRR={summary.mrr:.3f} < {MIN_MRR}"


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_query_smoke_optional():
    """Optional e2e smoke when chat API key is present."""
    # Keep lightweight: skip heavy corpus; just ensure endpoint imports.
    from app.api import query as query_mod

    assert hasattr(query_mod, "query_libraries")
