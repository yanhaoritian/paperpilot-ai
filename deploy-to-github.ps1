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

if ((Invoke-GhStatus) -ne 0) {
  Write-Host "Not logged in. Starting GitHub browser login (complete the steps in the browser)..."
  & $gh auth login -h github.com -p https -w
  if ((Invoke-GhStatus) -ne 0) {
    Write-Host "Still not logged in. Run manually: `"$gh`" auth login"
    exit 1
  }
}

$remote = git remote get-url origin 2>$null
if (-not $remote) {
  $name = if ($env:GITHUB_REPO_NAME) { $env:GITHUB_REPO_NAME } else { "paperpilot-ai" }
  Write-Host "Creating repo and pushing: $name (set GITHUB_REPO_NAME to override)"
  & $gh repo create $name --public --source=. --remote=origin --push
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
  Write-Host "Remote origin exists, running git push"
  git push -u origin master
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Done. Open https://dashboard.render.com -> New -> Blueprint -> connect this repo."
