# Push to GitHub for Render (run from repo root)
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy-to-github.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$gh = Join-Path ${env:ProgramFiles} "GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) {
  Write-Host "GitHub CLI not found. Install from https://cli.github.com/"
  exit 1
}

# gh writes "not logged in" to stderr; with $ErrorActionPreference = Stop that becomes a terminating error.
function Invoke-GhStatus {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "SilentlyContinue"
  & $gh auth status 1>$null 2>$null
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  return $code
}

# git prints "No such remote" to stderr when origin is missing; same Stop-mode issue.
function Get-GitRemoteOriginUrl {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "SilentlyContinue"
  $out = & git remote get-url origin 2>$null
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($code -ne 0) {
    return $null
  }
  return $out
}

if ((Invoke-GhStatus) -ne 0) {
  Write-Host "Not logged in. Starting GitHub browser login (complete the steps in the browser)..."
  & $gh auth login -h github.com -p https -w
  if ((Invoke-GhStatus) -ne 0) {
    Write-Host "Still not logged in. Run manually: `"$gh`" auth login"
    exit 1
  }
}

$remote = Get-GitRemoteOriginUrl
if (-not $remote) {
  $name = if ($env:GITHUB_REPO_NAME) { $env:GITHUB_REPO_NAME } else { "paperpilot-ai" }
  Write-Host "Creating repo and pushing: $name (set GITHUB_REPO_NAME to override)"
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "SilentlyContinue"
  & $gh repo create $name --public --source=. --remote=origin --push
  $createCode = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($createCode -ne 0) { exit $createCode }
} else {
  Write-Host "Remote origin exists, running git push"
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "SilentlyContinue"
  git push -u origin master
  $pushCode = $LASTEXITCODE
  $ErrorActionPreference = $prev
  if ($pushCode -ne 0) { exit $pushCode }
}

Write-Host "Done. Open https://dashboard.render.com -> New -> Blueprint -> connect this repo."
