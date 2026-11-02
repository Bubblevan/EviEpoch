$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'logs\codex-runtime'
$stopFile = Join-Path $runtime 'reranker-hk-relay.stop'
$pidFile = Join-Path $runtime 'reranker-hk-watch.pid'

Set-Content -LiteralPath $stopFile -Value 'stop' -NoNewline
if (Test-Path -LiteralPath $pidFile) {
    $watchId = [int](Get-Content -LiteralPath $pidFile -Raw)
    $watch = Get-CimInstance Win32_Process -Filter "ProcessId = $watchId" -ErrorAction SilentlyContinue
    if ($watch -and $watch.CommandLine -like '*hk-reranker-tunnel-watch.ps1*') {
        Stop-Process -Id $watchId -Force -ErrorAction SilentlyContinue
    }
}

Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'ssh.exe' -and
    $_.CommandLine -like '*-R 127.0.0.1:8090:127.0.0.1:8090*' -and
    $_.CommandLine -like '*@47.82.102.145*'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
Write-Output 'HK reranker SSH relay stopped; the local model process was left running.'
