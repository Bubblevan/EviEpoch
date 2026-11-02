param(
    [string]$SshKeyPath = 'D:\下载\hongkong_server_key.pem',
    [string]$ServerHost = '47.82.102.145',
    [string]$SshUser = 'root',
    [int]$LocalPort = 8090,
    [int]$RemotePort = 8090
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'logs\codex-runtime'
$pidFile = Join-Path $runtime 'reranker-hk-watch.pid'
$stopFile = Join-Path $runtime 'reranker-hk-relay.stop'
$watchScript = Join-Path $PSScriptRoot 'hk-reranker-tunnel-watch.ps1'
$health = "http://127.0.0.1:$LocalPort/health"

if (-not (Test-Path -LiteralPath $SshKeyPath)) { throw "SSH key not found: $SshKeyPath" }
if (-not (Test-Path -LiteralPath (Join-Path $root 'models\Qwen3-Reranker-0.6B'))) {
    throw 'Local Qwen3-Reranker-0.6B model directory is missing.'
}
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue

$watcher = $null
if (Test-Path -LiteralPath $pidFile) {
    $id = [int](Get-Content -LiteralPath $pidFile -Raw)
    $watcher = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
    if ($watcher -and $watcher.CommandLine -notlike '*hk-reranker-tunnel-watch.ps1*') { $watcher = $null }
}
if (-not $watcher) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $watchScript + '"' +
        ' -SshKeyPath "' + $SshKeyPath + '" -ServerHost "' + $ServerHost + '"' +
        ' -SshUser "' + $SshUser + '" -LocalPort ' + $LocalPort + ' -RemotePort ' + $RemotePort
    $process = Start-Process powershell.exe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtime 'reranker-hk-watch.stdout.log') `
        -RedirectStandardError (Join-Path $runtime 'reranker-hk-watch.stderr.log') -PassThru
    Set-Content -LiteralPath $pidFile -Value $process.Id -NoNewline
}

$deadline = (Get-Date).AddSeconds(150)
$ready = $false
do {
    Start-Sleep -Seconds 3
    try { $ready = (Invoke-RestMethod $health -TimeoutSec 3).status -eq 'ready' } catch { $ready = $false }
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) { throw 'Local reranker did not become ready; see logs/codex-runtime/reranker-hk-watch.stderr.log' }
Write-Output "Reranker is ready on 127.0.0.1:$LocalPort; the HK-only reverse SSH tunnel supervisor is running."
