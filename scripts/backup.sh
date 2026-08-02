#!/usr/bin/env bash
# Consistent PaperPilot backup for Ubuntu/Docker Compose.
# Usage: ./scripts/backup.sh [outdir]

set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTDIR="${1:-$ROOT/backups}"
DEST="$OUTDIR/paperpilot_$STAMP"
mkdir -p "$DEST"

PDF_COUNT=0
PDF_BYTES=0
if [[ -d "$ROOT/data/pdfs" ]]; then
  cp -a "$ROOT/data/pdfs" "$DEST/pdfs"
  while IFS= read -r -d '' file; do
    PDF_COUNT=$((PDF_COUNT + 1))
    PDF_BYTES=$((PDF_BYTES + $(stat -c %s "$file")))
  done < <(find "$DEST/pdfs" -type f -name '*.pdf' -print0)
  (
    cd "$DEST/pdfs"
    find . -type f -name '*.pdf' -print0 \
      | sort -z \
      | xargs -0 -r sha256sum
  ) > "$DEST/pdf-manifest.sha256"
fi

if [[ -f "$ROOT/data/paperpilot.db" ]]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$ROOT/data/paperpilot.db" ".backup '$DEST/paperpilot.db'"
  else
    echo "sqlite3 is unavailable; skipped live SQLite backup." >&2
  fi
fi

DB_DUMP=""
if command -v docker >/dev/null 2>&1 \
  && docker compose -f "$ROOT/docker-compose.yml" ps --status running --services 2>/dev/null \
    | grep -qx db; then
  DB_DUMP="$DEST/paperpilot_pg.dump"
  docker compose -f "$ROOT/docker-compose.yml" exec -T db \
    pg_dump -U paperpilot -d paperpilot -Fc --no-owner --no-acl > "$DB_DUMP"
  docker compose -f "$ROOT/docker-compose.yml" exec -T db \
    pg_restore -l < "$DB_DUMP" > "$DEST/pg-archive-list.txt"
  sha256sum "$DB_DUMP" > "$DEST/database-manifest.sha256"
  {
    echo "table,count"
    for table in ai_usage_events auth_codes blocks chunks conversation_memories conversations documents index_jobs libraries messages model_price_versions usage_daily users worker_heartbeats; do
      exists="$(
        docker compose -f "$ROOT/docker-compose.yml" exec -T db \
          psql -U paperpilot -d paperpilot -At \
          -c "SELECT to_regclass('public.$table') IS NOT NULL;"
      )"
      if [[ "$exists" != "t" ]]; then
        echo "$table,not_present"
        continue
      fi
      count="$(
        docker compose -f "$ROOT/docker-compose.yml" exec -T db \
          psql -U paperpilot -d paperpilot -At \
          -c "SELECT count(*) FROM public.$table;"
      )"
      echo "$table,$count"
    done
  } > "$DEST/db-counts.csv"
else
  echo "Compose db is not running; skipped PostgreSQL dump." >&2
fi

cat > "$DEST/backup-metadata.txt" <<EOF
created_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
source_root=$ROOT
postgres_dump=$(basename "${DB_DUMP:-none}")
pdf_count=$PDF_COUNT
pdf_bytes=$PDF_BYTES
EOF

echo "Backup complete: $DEST"
echo "Validate PDFs: cd '$DEST/pdfs' && sha256sum -c ../pdf-manifest.sha256"
echo "Restore PostgreSQL: pg_restore --no-owner --no-acl -d paperpilot '$DB_DUMP'"
