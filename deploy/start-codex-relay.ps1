param(
    [int]$MaxCalls = 256,
    [int]$CallTimeoutSeconds = 180
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
$python = Join-Path $root '.venv\Scripts\python.exe'
$tokenFile = Join-Path $logs 'codex-gateway-token.txt'
$gatewayPidFile = Join-Path $logs 'codex-gateway.pid'
$watchPidFile = Join-Path $logs 'codex-tunnel-watch.pid'
$stopFile = Join-Path $logs 'codex-relay.stop'
$healthUrl = 'http://127.0.0.1:18123/health'

if (-not (Test-Path $python)) { throw 'Project virtualenv not found at .venv\Scripts\python.exe' }
if (-not (Test-Path $tokenFile)) { throw 'Local gateway token file is missing from ignored logs\' }
if (-not (Test-Path (Join-Path $logs 'eviepoch-codex-tunnel'))) { throw 'Dedicated tunnel key is missing from ignored logs\' }
if (-not (Test-Path (Join-Path $env:TEMP 'eviepoch-com-pinned-known-hosts'))) { throw 'Pinned .com host key file is missing' }
if ($MaxCalls -le 0 -or $CallTimeoutSeconds -le 0) { throw 'Call limits must be positive' }
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue

$gatewayIsReady = $false
try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    $gatewayIsReady = $health.status -eq 'ok'
} catch { }

if (-not $gatewayIsReady) {
    $gateway = Start-Process -FilePath $python `
        -ArgumentList @('deploy\codex_cli_gateway.py', '--host', '127.0.0.1', '--port', '18123',
                        '--max-calls', "$MaxCalls", '--call-timeout', "$CallTimeoutSeconds", '--max-pending', '32') `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $gatewayPidFile -Value $gateway.Id -NoNewline
    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            $gatewayIsReady = $health.status -eq 'ok'
        } catch { $gatewayIsReady = $false }
        if (-not $gatewayIsReady -and $gateway.HasExited) { throw 'Local Codex gateway exited during startup; inspect logs\codex-gateway.log' }
    } while (-not $gatewayIsReady -and (Get-Date) -lt $deadline)
    if (-not $gatewayIsReady) { throw 'Local Codex gateway did not become healthy within 45 seconds' }
}

$watcher = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('powershell.exe', 'pwsh.exe') -and $_.CommandLine -like '*codex-tunnel-watch.ps1*'
} | Select-Object -First 1
if (-not $watcher) {
    $watchScript = Join-Path $PSScriptRoot 'codex-tunnel-watch.ps1'
    $watch = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $watchScript) `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru
    Set-Content -LiteralPath $watchPidFile -Value $watch.Id -NoNewline
}

Write-Output 'Local GPT-6 Luna gateway is healthy on 127.0.0.1:18123.'
Write-Output 'Reverse SSH tunnel supervisor is running; its remote listener is restricted to ECS loopback.'
Write-Output "Per-process Codex CLI launch cap: $MaxCalls."
