# One-click update of C:\CodexProjects\news-feed from the GitHub branch (double-click UPDATE_NEWS_FEED.cmd).
#   1. If this folder's git repo has no commit yet, commits the current state first (".env" and "data\" stay out).
#   2. Stops the hidden collector, copies the new code in (never touches .env, data\, .venv\).
#   3. Installs requirements, sets NEWS_FEED_DAILY_USD_CAP=2.00 in .env (the key is not read or printed).
#   4. Commits the update, checks every source once, starts the hidden collector again.
# It does not push anywhere and does not create a remote.
param(
    [string]$Repo    = "https://github.com/csh-sajuseyo/-.git",
    [string]$Branch  = "claude/wonderful-hawking-86v0bn",
    [string]$Reviews = "C:\CodexProjects\_reviews"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$here = $PSScriptRoot

function Commit-All([string]$msg) {
    git add -A
    $staged = git diff --cached --name-only
    if ($staged -match '(^|/)\.env$') { throw ".env is staged - refusing to commit. Check .gitignore." }
    if (-not $staged) { Write-Host "nothing to commit"; return }
    $name = git config user.name
    if ($name) { git commit -q -m $msg }
    else { git -c user.name="news-feed" -c user.email="news-feed@localhost" commit -q -m $msg }
    Write-Host "committed: $msg"
}

# 1. first commit
if (-not (Test-Path ".git")) { git init -b main | Out-Null }
cmd /c "git rev-parse --verify HEAD >nul 2>&1"
if ($LASTEXITCODE -ne 0) { Commit-All "Import news-feed collector (state before world-economy update)" }

# 2. stop + copy
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "STOP_HIDDEN.ps1")
$tmp = Join-Path $env:TEMP ("newsfeed-src-" + [guid]::NewGuid().ToString("N"))
git clone -q --depth 1 --branch $Branch --filter=blob:none --sparse $Repo $tmp
git -C $tmp sparse-checkout set news-feed _reviews
$src = Join-Path $tmp "news-feed"
Get-ChildItem -Force $src | Where-Object { $_.Name -notin @(".env", "data", ".venv", ".git") } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination $here -Recurse -Force
}
if (Test-Path (Join-Path $tmp "_reviews")) {
    New-Item -ItemType Directory -Force -Path $Reviews | Out-Null
    Copy-Item -Path (Join-Path $tmp "_reviews\*") -Destination $Reviews -Recurse -Force
    Write-Host "reviews copied to $Reviews"
}
Remove-Item -Recurse -Force $tmp

# 3. deps + cap
if (-not (Test-Path ".venv\Scripts\python.exe")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pytest
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }
$lines = [IO.File]::ReadAllLines((Join-Path $here ".env"))
$found = $false
$lines = $lines | ForEach-Object { if ($_ -match '^\s*NEWS_FEED_DAILY_USD_CAP\s*=') { $found = $true; "NEWS_FEED_DAILY_USD_CAP=2.00" } else { $_ } }
if (-not $found) { $lines = @($lines) + "NEWS_FEED_DAILY_USD_CAP=2.00" }
[IO.File]::WriteAllLines((Join-Path $here ".env"), [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
Write-Host "daily cap set to `$2.00 in .env"

# BLS (US CPI / jobs schedule) refuses anonymous scripts: it wants a contact e-mail in the request.
# Asked once; stored only in .env (git-ignored). Press Enter to skip.
$lines = [IO.File]::ReadAllLines((Join-Path $here ".env"))
$hasContact = @($lines | Where-Object { $_ -match '^\s*NEWS_FEED_CONTACT\s*=\s*\S' }).Count -gt 0
if (-not $hasContact) {
    $c = Read-Host "Contact e-mail for BLS (saved only in .env; press Enter to skip)"
    if ($c -and $c -match '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
        $lines = @($lines | Where-Object { $_ -notmatch '^\s*#?\s*NEWS_FEED_CONTACT\s*=' }) + "NEWS_FEED_CONTACT=$c"
        [IO.File]::WriteAllLines((Join-Path $here ".env"), [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
        Write-Host "NEWS_FEED_CONTACT saved in .env"
    } else { Write-Host "skipped (BLS schedule stays blocked)" }
}

# 4. commit, check, start
Commit-All "World-economy sources, macro classification, briefings, feed.json"
& .\.venv\Scripts\python.exe -m pytest -q tests
& .\.venv\Scripts\python.exe run.py --check-sources
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $here "START_HIDDEN.ps1")
Write-Host ""
Write-Host "git remote:"; git remote -v
Write-Host "done."
