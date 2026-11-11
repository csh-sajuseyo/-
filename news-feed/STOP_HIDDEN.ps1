Set-Location $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot "data\collector.pid"
if (-not (Test-Path $pidFile)) { Write-Host "not running"; exit 0 }
$id = Get-Content $pidFile
Stop-Process -Id $id -ErrorAction SilentlyContinue
Remove-Item $pidFile -ErrorAction SilentlyContinue
Write-Host "stopped PID $id"
