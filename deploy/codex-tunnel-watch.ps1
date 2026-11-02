$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
$stopFile = Join-Path $logs 'codex-relay.stop'
$childPidFile = Join-Path $logs 'codex-tunnel-child.pid'
$keyFile = Join-Path $logs 'eviepoch-codex-tunnel'
$knownHosts = Join-Path $env:TEMP 'eviepoch-com-pinned-known-hosts'
$logFile = Join-Path $logs 'codex-tunnel.log'
$ssh = (Get-Command ssh.exe -ErrorAction SilentlyContinue).Source

function Write-TunnelLog([string]$message) {
    try {
        if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt 1048576) {
            $old = "$logFile.1"
            Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
            Move-Item -LiteralPath $logFile -Destination $old -Force
        }
        Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format o) $message"
    } catch { }
}

if (-not $ssh -or -not (Test-Path $keyFile) -or -not (Test-Path $knownHosts)) {
    Write-TunnelLog 'Tunnel prerequisites missing; watcher stopped.'
    exit 2
}

while (-not (Test-Path $stopFile)) {
    $args = @(
        '-N', '-T', '-i', $keyFile,
        '-o', 'BatchMode=yes',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=20',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'ConnectTimeout=15',
        '-o', 'LogLevel=ERROR',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', "UserKnownHostsFile=$knownHosts",
        '-R', '127.0.0.1:18012:127.0.0.1:18123',
        'ami-tunnel@bubblevan.bbroot.com'
    )
    try {
        $proc = Start-Process -FilePath $ssh -ArgumentList $args -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $childPidFile -Value $proc.Id -NoNewline
        Write-TunnelLog "Reverse tunnel process started (pid=$($proc.Id))."
        $proc.WaitForExit()
        Remove-Item -LiteralPath $childPidFile -Force -ErrorAction SilentlyContinue
        if (Test-Path $stopFile) { break }
        Write-TunnelLog "Reverse tunnel exited (code=$($proc.ExitCode)); reconnecting in 5 seconds."
    } catch {
        Write-TunnelLog 'Reverse tunnel launch failed; reconnecting in 5 seconds.'
    }
    Start-Sleep -Seconds 5
}
Remove-Item -LiteralPath $childPidFile -Force -ErrorAction SilentlyContinue
Write-TunnelLog 'Reverse tunnel watcher stopped.'
