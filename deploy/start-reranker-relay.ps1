param([Parameter(Mandatory=$true)][string]$SshKeyPath,[string]$ServerHost='47.107.141.70',[string]$SshUser='root',[int]$LocalPort=8090,[int]$RemotePort=8090)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$runtime=Join-Path $root 'logs\codex-runtime'
$python=Join-Path $runtime 'reranker-runtime\Scripts\python.exe'
$model=Join-Path $root 'models\Qwen3-Reranker-0.6B'
$health="http://127.0.0.1:$LocalPort/health"
$env:EPOCH_RERANK_MODEL_PATH=(Resolve-Path $model).Path
$env:EPOCH_RERANK_BATCH_SIZE='8'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
if(-not(Test-Path $python)){throw 'reranker venv missing'}
if(-not(Test-Path (Join-Path $model 'config.json'))){throw 'model files missing'}
if(-not(Test-Path $SshKeyPath)){throw 'SSH key missing'}
New-Item -ItemType Directory -Force -Path $runtime|Out-Null
Remove-Item (Join-Path $runtime 'reranker-relay.stop') -Force -ErrorAction SilentlyContinue
try{$ready=(Invoke-RestMethod $health -TimeoutSec 2).status -eq 'ready'}catch{$ready=$false}
if(-not $ready -and -not (Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)){
 $svc=Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','eviepoch.providers.reranker_service:app','--host','127.0.0.1','--port',"$LocalPort",'--workers','1','--log-level','warning') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'reranker-uvicorn.stdout.log') -RedirectStandardError (Join-Path $runtime 'reranker-uvicorn.stderr.log') -PassThru
 Set-Content (Join-Path $runtime 'reranker-uvicorn.pid') $svc.Id -NoNewline
 $deadline=(Get-Date).AddSeconds(120)
 do{Start-Sleep 2;try{$ready=(Invoke-RestMethod $health -TimeoutSec 2).status -eq 'ready'}catch{$ready=$false}}while(-not $ready -and (Get-Date)-lt $deadline -and -not $svc.HasExited)
 if(-not $ready -and -not (Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)){throw 'reranker did not become ready; see logs/codex-runtime/reranker-uvicorn.stderr.log'}
}
$pidFile=Join-Path $runtime 'reranker-tunnel-watch.pid'
$watch=$null
if(Test-Path $pidFile){$id=[int](Get-Content $pidFile -Raw);$watch=Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue;if($watch -and $watch.CommandLine -notlike '*reranker-tunnel-watch.ps1*'){$watch=$null}}
if(-not $watch){
 $watchScript=Join-Path $PSScriptRoot 'reranker-tunnel-watch.ps1'
 $args='-NoProfile -ExecutionPolicy Bypass -File "'+$watchScript+'" -SshKeyPath "'+$SshKeyPath+'" -ServerHost "'+$ServerHost+'" -SshUser "'+$SshUser+'" -LocalPort '+$LocalPort+' -RemotePort '+$RemotePort
 $proc=Start-Process powershell.exe -ArgumentList $args -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'reranker-watch.stdout.log') -RedirectStandardError (Join-Path $runtime 'reranker-watch.stderr.log') -PassThru
 Set-Content $pidFile $proc.Id -NoNewline
}
Write-Output "reranker ready at 127.0.0.1:$LocalPort; supervisor will reconnect a loopback-only SSH tunnel."


