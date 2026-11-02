param([Parameter(Mandatory=$true)][string]$SshKeyPath,[Parameter(Mandatory=$true)][string]$ServerHost,[Parameter(Mandatory=$true)][string]$SshUser,[Parameter(Mandatory=$true)][int]$LocalPort,[Parameter(Mandatory=$true)][int]$RemotePort)
$ErrorActionPreference='Continue'
$root=Split-Path -Parent $PSScriptRoot
$runtime=Join-Path $root 'logs\codex-runtime'
$python=Join-Path $runtime 'reranker-runtime\Scripts\python.exe'
$model=Join-Path $root 'models\Qwen3-Reranker-0.6B'
$health="http://127.0.0.1:$LocalPort/health"
$stop=Join-Path $runtime 'reranker-relay.stop'
$pidFile=Join-Path $runtime 'reranker-uvicorn.pid'
$sshExe=Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$sshLog=Join-Path $runtime 'reranker-ssh.log'
$env:EPOCH_RERANK_MODEL_PATH=(Resolve-Path $model).Path
$env:EPOCH_RERANK_BATCH_SIZE='8'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
function Ready{try{return (Invoke-RestMethod $health -TimeoutSec 2).status -eq 'ready'}catch{return $false}}
function StopP($p){if($p){try{$p.Refresh();if(-not $p.HasExited){$p.Kill();$p.WaitForExit(3000)|Out-Null}}catch{}}}
function StartReranker{$p=Start-Process $python -ArgumentList @('-m','uvicorn','eviepoch.providers.reranker_service:app','--host','127.0.0.1','--port',"$LocalPort",'--workers','1','--log-level','warning') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'reranker-uvicorn.stdout.log') -RedirectStandardError (Join-Path $runtime 'reranker-uvicorn.stderr.log') -PassThru;Set-Content $pidFile $p.Id -NoNewline}
$tunnel=$null
while(-not(Test-Path $stop)){
 if(-not(Ready)){
  $svc=$null
  if(Test-Path $pidFile){$id=[int](Get-Content $pidFile -Raw);$row=Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue;if($row -and $row.CommandLine -like '*eviepoch.providers.reranker_service*'){$svc=Get-Process -Id $id -ErrorAction SilentlyContinue}}
  if(-not $svc -and -not (Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)){StartReranker}
  $until=(Get-Date).AddSeconds(120);while(-not(Ready) -and (Get-Date)-lt $until -and -not(Test-Path $stop)){Start-Sleep 2}
 }
 if((Ready) -and (-not $tunnel -or $tunnel.HasExited)){
  $forward='127.0.0.1:'+$RemotePort+':127.0.0.1:'+$LocalPort
  $sshArgs=@('-i',$SshKeyPath,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-E',$sshLog,'-N','-R',$forward,"$SshUser@$ServerHost")
  $tunnel=Start-Process $sshExe -ArgumentList $sshArgs -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'reranker-ssh.stdout.log') -RedirectStandardError (Join-Path $runtime 'reranker-ssh.stderr.log') -PassThru
 }
 if($tunnel -and -not(Ready)){StopP $tunnel;$tunnel=$null}
 if($tunnel){$tunnel.Refresh();if($tunnel.HasExited){$tunnel=$null}}
 Start-Sleep 3
}
StopP $tunnel

