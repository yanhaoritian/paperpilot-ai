# Quick public URL via Cloudflare (trycloudflare.com) — no paid hosting.
#
# Terminal 1: configure .env then  npm start
# Terminal 2:  powershell -NoProfile -ExecutionPolicy Bypass -File .\tunnel-quick.ps1
#
# Install cloudflared:  winget install Cloudflare.cloudflared
# Then open a NEW terminal (PATH updates) or use full path below.

$port = if ($env:PORT) { $env:PORT } else { "8787" }
$localUrl = "http://127.0.0.1:$port"

$exe = $null
$cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($cmd) {
  $exe = $cmd.Source
}
if (-not $exe) {
  $candidate = Join-Path ${env:ProgramFiles} "Cloudflare\cloudflared\cloudflared.exe"
  if (Test-Path $candidate) {
    $exe = $candidate
  }
}
if (-not $exe) {
  Write-Host "cloudflared not found. Install:"
  Write-Host "  winget install Cloudflare.cloudflared"
  Write-Host "Close and reopen this terminal, then run this script again."
  exit 1
}

Write-Host ""
Write-Host "Local backend: $localUrl"
Write-Host "Keep another window running: npm start"
Write-Host "Your HTTPS URL (trycloudflare.com) will appear below. Ctrl+C to stop tunnel."
Write-Host ""

& $exe tunnel --url $localUrl
