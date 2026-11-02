param(
    [string]$SshKeyPath = 'D:\下载\hongkong_server_key.pem',
    [string]$ServerHost = '47.82.102.145',
    [string]$SshUser = 'root',
    [int]$LocalPort = 8090,
    [int]$RemotePort = 8090
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'logs\codex-runtime'
$python = Join-Path $runtime 'reranker-runtime\Scripts\python.exe'
$model = Join-Path $root 'models\Qwen3-Reranker-0.6B'
$health = "http://127.0.0.1:$LocalPort/health"
$stop = Join-Path $runtime 'reranker-hk-relay.stop'
$servicePidFile = Join-Path $runtime 'reranker-hk-uvicorn.pid'
$tunnelPidFile = Join-Path $runtime 'reranker-hk-ssh.pid'
$sshExe = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$sshLog = Join-Path $runtime 'reranker-hk-ssh.log'
$watchLog = Join-Path $runtime 'reranker-hk-watch.log'

$env:EPOCH_RERANK_MODEL_PATH = (Resolve-Path $model).Path
$env:EPOCH_RERANK_BATCH_SIZE = '8'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'

function Ready {
    try {
        $response = Invoke-RestMethod $health -TimeoutSec 2
        if (-not $script:healthProbeLogged) {
            Write-WatchLog ("local health response status=" + $response.status)
            $script:healthProbeLogged = $true
        }
        return $response.status -eq 'ready'
    }
    catch {
        if (-not $script:healthProbeLogged) {
            Write-WatchLog ("local health probe failed: " + $_.Exception.GetType().Name)
            $script:healthProbeLogged = $true
        }
        return $false
    }
}

function Stop-ProcessQuietly($Process) {
    if ($Process) {
        try {
            $Process.Refresh()
            if (-not $Process.HasExited) {
                $Process.Kill()
                $Process.WaitForExit(3000) | Out-Null
            }
        } catch { }
    }
}

function Write-WatchLog([string]$Message) {
    if ((Test-Path -LiteralPath $watchLog) -and (Get-Item -LiteralPath $watchLog).Length -gt 1048576) {
        Move-Item -LiteralPath $watchLog -Destination ($watchLog + '.1') -Force
    }
    Add-Content -LiteralPath $watchLog -Value ((Get-Date).ToUniversalTime().ToString('o') + ' ' + $Message)
}

function Start-LocalReranker {
    $process = Start-Process $python -ArgumentList @(
        '-m', 'uvicorn', 'eviepoch.providers.reranker_service:app', '--host', '127.0.0.1',
        '--port', "$LocalPort", '--workers', '1', '--log-level', 'warning'
    ) -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtime 'reranker-hk-uvicorn.stdout.log') `
        -RedirectStandardError (Join-Path $runtime 'reranker-hk-uvicorn.stderr.log') -PassThru
    Set-Content -LiteralPath $servicePidFile -Value $process.Id -NoNewline
}

$tunnel = $null
Write-WatchLog "watcher started for ${SshUser}@${ServerHost}; remote 127.0.0.1:$RemotePort to local 127.0.0.1:$LocalPort"
while (-not (Test-Path -LiteralPath $stop)) {
    if (-not (Ready)) {
        Write-WatchLog 'local reranker health check failed; waiting for local service'
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)
        Write-WatchLog ("local listener count=" + $listeners.Count)
        if ($listeners.Count -eq 0) {
            Write-WatchLog 'local port is not listening; starting the model service'
            Start-LocalReranker
        }
        $deadline = (Get-Date).AddSeconds(120)
        while (-not (Ready) -and (Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $stop)) {
            Start-Sleep -Seconds 2
        }
    }

    if ((Ready) -and (-not $tunnel -or $tunnel.HasExited)) {
        Write-WatchLog 'starting reverse SSH tunnel'
        $forward = '127.0.0.1:' + $RemotePort + ':127.0.0.1:' + $LocalPort
        $sshArgs = @(
            '-i', $SshKeyPath, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR',
            '-E', $sshLog, '-N', '-R', $forward, "$SshUser@$ServerHost"
        )
        $tunnel = Start-Process $sshExe -ArgumentList $sshArgs -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $runtime 'reranker-hk-ssh.stdout.log') `
            -RedirectStandardError (Join-Path $runtime 'reranker-hk-ssh.stderr.log') -PassThru
        Set-Content -LiteralPath $tunnelPidFile -Value $tunnel.Id -NoNewline
        Start-Sleep -Milliseconds 500
        $tunnel.Refresh()
        if ($tunnel.HasExited) { Write-WatchLog ("SSH tunnel exited immediately with code " + $tunnel.ExitCode) }
    }

    if ($tunnel) {
        $tunnel.Refresh()
        if ($tunnel.HasExited) {
            Write-WatchLog ("SSH tunnel exited with code " + $tunnel.ExitCode + '; retrying')
            $tunnel = $null
            Remove-Item -LiteralPath $tunnelPidFile -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 3
}

Stop-ProcessQuietly $tunnel
