#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/backend/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="${PYTHON_BIN:-python3}"
fi

ACTION="${1:-upgrade}"
if [[ "$ACTION" == "current" ]]; then
  "$PYTHON" -m alembic -c "$ROOT/backend/alembic.ini" current
else
  "$PYTHON" -m alembic -c "$ROOT/backend/alembic.ini" upgrade head
fi
