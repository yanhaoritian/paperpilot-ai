from __future__ import annotations

import os

import pytest

from app.config import get_settings


def test_default_test_suite_never_targets_postgres():
    if os.getenv("POSTGRES_GATE_TEST") == "1":
        pytest.skip("the explicit PostgreSQL gate intentionally targets Postgres")
    assert get_settings().is_sqlite
