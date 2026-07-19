# Backup PaperPilot data (SQLite pdfs + optional Postgres dump via Compose).
# Usage: .\scripts\backup.ps1 [-OutDir .\backups]

param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $OutDir) {
    $OutDir = Join-Path $Root "backups"
}
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Dest = Join-Path $OutDir "paperpilot_$Stamp"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$DataDir = Join-Path $Root "data"
$PdfDir = Join-Path $DataDir "pdfs"
$Sqlite = Join-Path $DataDir "paperpilot.db"

if (Test-Path $Sqlite) {
    Copy-Item $Sqlite (Join-Path $Dest "paperpilot.db")
    Write-Host "Copied SQLite -> $Dest\paperpilot.db"
}

if (Test-Path $PdfDir) {
    Copy-Item $PdfDir (Join-Path $Dest "pdfs") -Recurse
    Write-Host "Copied PDFs -> $Dest\pdfs"
}

# If Compose db is running, also dump Postgres
$compose = Get-Command docker -ErrorAction SilentlyContinue
if ($compose) {
    $running = docker compose -f (Join-Path $Root "docker-compose.yml") ps --status running --services 2>$null
    if ($running -match "(?m)^db$") {
        $sqlOut = Join-Path $Dest "paperpilot_pg.sql"
        docker compose -f (Join-Path $Root "docker-compose.yml") exec -T db `
            pg_dump -U paperpilot -d paperpilot --no-owner --no-acl | Set-Content -Path $sqlOut -Encoding utf8
        Write-Host "Postgres dump -> $sqlOut"
    } else {
        Write-Host "Compose db not running; skipped pg_dump."
    }
}

Write-Host "Backup complete: $Dest"
Write-Host "Restore SQLite: copy paperpilot.db + pdfs back to data\"
Write-Host "Restore Postgres: docker compose exec -T db psql -U paperpilot -d paperpilot < paperpilot_pg.sql"
