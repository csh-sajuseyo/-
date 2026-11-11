# Start the collector in the background with no console window (pythonw).
# Without ANTHROPIC_API_KEY in .env it only collects (no AI classification).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot "data\collector.pid"
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Host "already running (PID $old)"; exit 0
    }
}
$pyw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = "pythonw.exe" }
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "data") | Out-Null
$p = Start-Process -FilePath $pyw -ArgumentList @("run.py", "--log-file", "data\collector.log") `
     -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
Write-Host "started hidden collector PID $($p.Id); log: data\collector.log"
