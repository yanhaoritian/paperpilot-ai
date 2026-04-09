# Push to GitHub for Render (run from repo root)
# First time: gh auth login
# Then: powershell -ExecutionPolicy Bypass -File .\deploy-to-github.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$gh = Join-Path ${env:ProgramFiles} "GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) {
  Write-Host "GitHub CLI not found. Install from https://cli.github.com/"
  exit 1
}

& $gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Starting GitHub browser login..."
  & $gh auth login -h github.com -p https -w
}

$remote = ""
try { $remote = git remote get-url origin 2>$null } catch { }

if (-not $remote) {
  $name = if ($env:GITHUB_REPO_NAME) { $env:GITHUB_REPO_NAME } else { "paperpilot-ai" }
  Write-Host "Creating repo and pushing: $name (set GITHUB_REPO_NAME to override)"
  & $gh repo create $name --public --source=. --remote=origin --push
} else {
  Write-Host "Remote origin exists, running git push"
  git push -u origin master
}

Write-Host "Done. Open https://dashboard.render.com -> New -> Blueprint -> connect this repo."
