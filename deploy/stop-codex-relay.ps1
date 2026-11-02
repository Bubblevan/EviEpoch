$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
$stopFile = Join-Path $logs 'codex-relay.stop'
Set-Content -LiteralPath $stopFile -Value 'stop' -NoNewline

$childPidFile = Join-Path $logs 'codex-tunnel-child.pid'
if (Test-Path $childPidFile) {
    $childPid = [int](Get-Content -Raw $childPidFile)
    Stop-Process -Id $childPid -Force
}
Start-Sleep -Seconds 2

$watchPidFile = Join-Path $logs 'codex-tunnel-watch.pid'
if (Test-Path $watchPidFile) {
    $watchPid = [int](Get-Content -Raw $watchPidFile)
    Stop-Process -Id $watchPid -Force
}

$gatewayPidFile = Join-Path $logs 'codex-gateway.pid'
if (Test-Path $gatewayPidFile) {
    $gatewayPid = [int](Get-Content -Raw $gatewayPidFile)
    Stop-Process -Id $gatewayPid -Force
}
Remove-Item -LiteralPath $childPidFile,$watchPidFile,$gatewayPidFile,$stopFile -Force
Write-Output 'Local Codex gateway and reverse SSH tunnel stopped.'
