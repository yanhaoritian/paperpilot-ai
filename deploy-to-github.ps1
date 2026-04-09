# 推送到 GitHub 供 Render 连接（在仓库根目录执行）
# 1) 首次使用请先登录：gh auth login
# 2) 再运行：powershell -ExecutionPolicy Bypass -File .\deploy-to-github.ps1

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
Set-Location $repoRoot

$gh = Join-Path ${env:ProgramFiles} "GitHub CLI\gh.exe"
if (-not (Test-Path $gh)) {
  Write-Host "未找到 GitHub CLI，请从 https://cli.github.com/ 安装，或使用 Git 手动推送。"
  exit 1
}

& $gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "尚未登录 GitHub。正在启动浏览器登录..."
  & $gh auth login -h github.com -p https -w
}

$remote = ""
try { $remote = git remote get-url origin 2>$null } catch { }

if (-not $remote) {
  $name = if ($env:GITHUB_REPO_NAME) { $env:GITHUB_REPO_NAME } else { "paperpilot-ai" }
  Write-Host "创建远程仓库并推送: $name （可设置环境变量 GITHUB_REPO_NAME 改名）"
  & $gh repo create $name --public --source=. --remote=origin --push
} else {
  Write-Host "已存在 origin，执行 git push"
  git push -u origin master
}

Write-Host "完成。请到 https://dashboard.render.com 新建 Blueprint 并连接该仓库。"
