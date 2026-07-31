param(
    [ValidateSet("upgrade", "current")]
    [string]$Action = "upgrade"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $Root "backend\.venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$Config = Join-Path $Root "backend\alembic.ini"

if ($Action -eq "current") {
    & $Python -m alembic -c $Config current
} else {
    & $Python -m alembic -c $Config upgrade head
}

if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed with exit code $LASTEXITCODE"
}
