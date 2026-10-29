param(
    [Parameter(Mandatory=$true)][string]$HostName,
    [Parameter(Mandatory=$true)][string]$UserName,
    [Parameter(Mandatory=$true)][string]$RemotePath
)
$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ($HostName -notmatch '^[A-Za-z0-9._:-]+$' -or $UserName -notmatch '^[A-Za-z0-9_-]+$') {
    throw 'Invalid SSH host/user'
}
if ($RemotePath -notmatch '^/home/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+$' -or $RemotePath.Contains('..')) {
    throw 'RemotePath must be an explicit path below /home/USER; no traversal'
}
$Private = Join-Path $Root '.local'
New-Item -ItemType Directory -Force -Path $Private | Out-Null
$Archive = Join-Path $Private 'deploy-source.tar'
$Destination = "${UserName}@${HostName}"
Push-Location $Root
try {
    git diff --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Commit tracked changes before deployment' }
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Commit staged changes before deployment' }
    git archive --format=tar --output=$Archive HEAD
    if ($LASTEXITCODE -ne 0) { throw 'git archive failed; create a commit first' }
    ssh $Destination "test ! -L '$RemotePath' && mkdir -p -- '$RemotePath'"
    if ($LASTEXITCODE -ne 0) { throw 'Remote directory validation failed' }
    scp $Archive "${Destination}:${RemotePath}/.source.tar"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed' }
    ssh $Destination "cd '$RemotePath' && tar -xf .source.tar && chmod +x bt-dos scripts/install.sh && ./scripts/install.sh --dev && ./bt-dos doctor --json"
    if ($LASTEXITCODE -ne 0) { throw 'Remote installation or validation failed' }
    Write-Output "Ready: ssh -t $Destination 'cd $RemotePath && ./bt-dos'"
} finally {
    Pop-Location
}
