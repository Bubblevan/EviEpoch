$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'logs\codex-runtime'
$stopFile = Join-Path $runtime 'reranker-relay.stop'
Set-Content -LiteralPath $stopFile -Value 'stop' -NoNewline

$watchPidFile = Join-Path $runtime 'reranker-tunnel-watch.pid'
if (Test-Path $watchPidFile) {
    $watchId = [int](Get-Content -LiteralPath $watchPidFile -Raw)
    $watch = Get-CimInstance Win32_Process -Filter "ProcessId = $watchId" -ErrorAction SilentlyContinue
    if ($watch -and $watch.CommandLine -like '*reranker-tunnel-watch.ps1*') {
        Stop-Process -Id $watchId -Force -ErrorAction SilentlyContinue
    }
}

Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'ssh.exe' -and $_.CommandLine -like '*-R 127.0.0.1:8090:127.0.0.1:8090*' -and $_.CommandLine -like '*@47.107.141.70*'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$servicePidFile = Join-Path $runtime 'reranker-uvicorn.pid'
if (Test-Path $servicePidFile) {
    $serviceId = [int](Get-Content -LiteralPath $servicePidFile -Raw)
    $service = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceId" -ErrorAction SilentlyContinue
    if ($service -and $service.CommandLine -like '*eviepoch.providers.reranker_service*') {
        Stop-Process -Id $serviceId -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item -LiteralPath $watchPidFile,$servicePidFile,$stopFile -Force -ErrorAction SilentlyContinue
Write-Output 'Reranker service and its SSH reverse tunnel were stopped.'
