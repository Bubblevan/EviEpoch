param(
    [string]$ServerHost = "bubblevan.de5.net",
    [string]$KeyPath = "D:\下载\my_server_key.pem",
    [string]$Destination = (Join-Path $env:USERPROFILE "Downloads\EviEpoch-audit")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $KeyPath -PathType Leaf)) {
    throw "SSH key not found: $KeyPath"
}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$remote = "root@${ServerHost}:/var/lib/eviepoch/audit/api-requests-*.jsonl"
& scp -i $KeyPath -o BatchMode=yes -o StrictHostKeyChecking=yes $remote $Destination
if ($LASTEXITCODE -ne 0) {
    throw "SCP failed with exit code $LASTEXITCODE. Confirm that audit records exist and SSH host verification succeeds."
}
Write-Output "Audit files downloaded to: $Destination"
