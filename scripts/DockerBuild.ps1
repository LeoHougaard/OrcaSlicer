param(
    [string] $ImageName = "orcaslicer",
    [string] $Docker = "docker",
    [string] $User = $env:USERNAME,
    [int] $Ncores = [Environment]::ProcessorCount
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

& $Docker build `
    -t $ImageName `
    --build-arg "USER=$User" `
    --build-arg "UID=0" `
    --build-arg "GID=0" `
    --build-arg "NCORES=$Ncores" `
    -f (Join-Path $ScriptDir "Dockerfile") `
    $ProjectRoot
