# Copy news-feed/ out of the csh-sajuseyo/- repo branch into a standalone git repo.
# Does not merge or modify any branch; coin-auto-trader is not involved.
param(
    [string]$Target = "C:\CodexProjects\news-feed",
    [string]$Repo   = "https://github.com/csh-sajuseyo/-.git",
    [string]$Branch = "claude/wonderful-hawking-86v0bn"
)
$ErrorActionPreference = "Stop"
if (Test-Path (Join-Path $Target ".git")) { throw "$Target is already a git repo; refusing to overwrite" }
$tmp = Join-Path $env:TEMP ("newsfeed-src-" + [guid]::NewGuid().ToString("N"))
git clone --depth 1 --branch $Branch --filter=blob:none --sparse $Repo $tmp
git -C $tmp sparse-checkout set news-feed
New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Path (Join-Path $tmp "news-feed\*") -Destination $Target -Recurse -Force
Copy-Item -Path (Join-Path $tmp "news-feed\.gitignore"), (Join-Path $tmp "news-feed\.env.example") -Destination $Target -Force
Remove-Item -Recurse -Force $tmp
Set-Location $Target
git init -b main
git add -A
git commit -m "Import news-feed collector"
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; (Get-Content .env) -replace '^ANTHROPIC_API_KEY=.*','ANTHROPIC_API_KEY=' | Set-Content .env }
Write-Host "done: $Target  (add ANTHROPIC_API_KEY to .env later to enable AI classification)"
