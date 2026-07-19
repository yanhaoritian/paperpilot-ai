#!/usr/bin/env bash
# Backup PaperPilot data (SQLite/pdfs + optional Postgres dump via Compose).
# Usage: ./scripts/backup.sh [outdir]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUTDIR="${1:-$ROOT/backups}"
DEST="$OUTDIR/paperpilot_$STAMP"
mkdir -p "$DEST"

if [[ -f "$ROOT/data/paperpilot.db" ]]; then
  cp "$ROOT/data/paperpilot.db" "$DEST/paperpilot.db"
  echo "Copied SQLite -> $DEST/paperpilot.db"
fi

if [[ -d "$ROOT/data/pdfs" ]]; then
  cp -R "$ROOT/data/pdfs" "$DEST/pdfs"
  echo "Copied PDFs -> $DEST/pdfs"
fi

if command -v docker >/dev/null 2>&1; then
  if docker compose -f "$ROOT/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx db; then
    docker compose -f "$ROOT/docker-compose.yml" exec -T db \
      pg_dump -U paperpilot -d paperpilot --no-owner --no-acl > "$DEST/paperpilot_pg.sql"
    echo "Postgres dump -> $DEST/paperpilot_pg.sql"
  else
    echo "Compose db not running; skipped pg_dump."
  fi
fi

echo "Backup complete: $DEST"
echo "Restore SQLite: copy paperpilot.db + pdfs back to data/"
echo "Restore Postgres: docker compose exec -T db psql -U paperpilot -d paperpilot < paperpilot_pg.sql"
