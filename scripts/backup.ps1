# Consistent PaperPilot backup for Windows/Docker Compose.
# Usage: .\scripts\backup.ps1 [-OutDir .\backups]

param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $OutDir) {
    $OutDir = Join-Path $Root "backups"
}
$BackupRoot = [System.IO.Path]::GetFullPath($OutDir)
$Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$Dest = [System.IO.Path]::GetFullPath((Join-Path $BackupRoot "paperpilot_$Stamp"))
if (-not $Dest.StartsWith(
    $BackupRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Backup destination escaped the requested backup root"
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$DataDir = Join-Path $Root "data"
$PdfDir = Join-Path $DataDir "pdfs"
$Sqlite = Join-Path $DataDir "paperpilot.db"
$PdfCount = 0
$PdfBytes = 0

if (Test-Path -LiteralPath $PdfDir -PathType Container) {
    $PdfDest = Join-Path $Dest "pdfs"
    Copy-Item -LiteralPath $PdfDir -Destination $PdfDest -Recurse
    $PdfFiles = @(Get-ChildItem -LiteralPath $PdfDest -File -Recurse -Filter "*.pdf")
    $PdfCount = $PdfFiles.Count
    $PdfBytes = [long](($PdfFiles | Measure-Object -Property Length -Sum).Sum)
    $PdfFiles |
        ForEach-Object {
            [pscustomobject]@{
                relative_path = $_.FullName.Substring($PdfDest.Length + 1).Replace("\", "/")
                bytes = $_.Length
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
            }
        } |
        Export-Csv -LiteralPath (Join-Path $Dest "pdf-manifest.csv") -NoTypeInformation -Encoding UTF8
}

if (Test-Path -LiteralPath $Sqlite -PathType Leaf) {
    $SqliteExe = Get-Command sqlite3 -ErrorAction SilentlyContinue
    if ($SqliteExe) {
        & $SqliteExe.Source $Sqlite ".backup '$(Join-Path $Dest "paperpilot.db")'"
        if ($LASTEXITCODE -ne 0) {
            throw "SQLite backup failed with exit code $LASTEXITCODE"
        }
    } else {
        Write-Warning "sqlite3 is unavailable; skipped live SQLite backup."
    }
}

$DbDump = $null
$Docker = Get-Command docker -ErrorAction SilentlyContinue
if ($Docker) {
    $Running = docker compose -f (Join-Path $Root "docker-compose.yml") ps --status running --services 2>$null
    if ($Running -match "(?m)^db$") {
        $ContainerId = (
            docker compose -f (Join-Path $Root "docker-compose.yml") ps -q db
        ).Trim()
        if (-not $ContainerId) {
            throw "Compose reported a running database but returned no container id"
        }
        $TempName = "paperpilot-backup-$Stamp.dump"
        $TempPath = "/tmp/$TempName"
        $DbDump = Join-Path $Dest "paperpilot_pg.dump"
        docker exec $ContainerId pg_dump -U paperpilot -d paperpilot -Fc --no-owner --no-acl -f $TempPath
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL backup failed with exit code $LASTEXITCODE"
        }
        docker exec $ContainerId pg_restore -l $TempPath |
            Set-Content -LiteralPath (Join-Path $Dest "pg-archive-list.txt") -Encoding UTF8
        if ($LASTEXITCODE -ne 0) {
            throw "PostgreSQL archive validation failed with exit code $LASTEXITCODE"
        }
        docker cp "${ContainerId}:$TempPath" $DbDump
        if ($LASTEXITCODE -ne 0) {
            throw "Copying the PostgreSQL archive failed with exit code $LASTEXITCODE"
        }
        docker exec $ContainerId rm -f $TempPath
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove the exact temporary dump $TempPath"
        }

        $Counts = @("table,count")
        foreach ($Table in @(
            "auth_codes", "blocks", "chunks", "conversations", "documents",
            "ai_usage_events", "conversation_memories", "index_jobs", "libraries", "messages",
            "model_price_versions", "usage_daily", "users", "worker_heartbeats"
        )) {
            $Exists = docker exec $ContainerId psql -U paperpilot -d paperpilot -At `
                -c "SELECT to_regclass('public.$Table') IS NOT NULL;"
            if ($LASTEXITCODE -ne 0) {
                throw "Checking table $Table failed with exit code $LASTEXITCODE"
            }
            if ($Exists.Trim() -ne "t") {
                $Counts += "$Table,not_present"
                continue
            }
            $Count = docker exec $ContainerId psql -U paperpilot -d paperpilot -At `
                -c "SELECT count(*) FROM public.$Table;"
            if ($LASTEXITCODE -ne 0) {
                throw "Counting table $Table failed with exit code $LASTEXITCODE"
            }
            $Counts += "$Table,$($Count.Trim())"
        }
        $Counts | Set-Content -LiteralPath (Join-Path $Dest "db-counts.csv") -Encoding UTF8
    } else {
        Write-Warning "Compose db is not running; skipped PostgreSQL dump."
    }
}

$Metadata = [ordered]@{
    created_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    source_root = $Root
    postgres_dump = if ($DbDump) { Split-Path -Leaf $DbDump } else { $null }
    postgres_dump_sha256 = if ($DbDump) {
        (Get-FileHash -Algorithm SHA256 -LiteralPath $DbDump).Hash.ToLowerInvariant()
    } else {
        $null
    }
    pdf_count = $PdfCount
    pdf_bytes = $PdfBytes
}
$Metadata |
    ConvertTo-Json -Depth 3 |
    Set-Content -LiteralPath (Join-Path $Dest "backup-metadata.json") -Encoding UTF8

Write-Host "Backup complete: $Dest"
Write-Host "Restore PostgreSQL with pg_restore --no-owner --no-acl."
