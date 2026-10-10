param([string]$Project)

$ErrorActionPreference = 'Stop'
$repo_root = Split-Path -Parent $PSScriptRoot
$data_root = Join-Path (Split-Path -Parent $repo_root) 'OrcaSlicer-data'
$executable = Join-Path $repo_root 'build-continuous\src\Release\orca-slicer.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'Build the canonical Release application in build-continuous first.'
}
if (-not $Project) {
    $Project = Join-Path $data_root 'projects\Continuous Infill.3mf'
}
if (-not (Test-Path -LiteralPath $Project -PathType Leaf)) {
    throw "Project does not exist: $Project"
}
$userdata = Join-Path $data_root 'userdata'
if (-not (Test-Path -LiteralPath $userdata -PathType Container)) {
    throw 'Restore the preserved canonical userdata before launching.'
}
Start-Process -FilePath $executable -WorkingDirectory (Split-Path -Parent $executable) `
    -ArgumentList @('--datadir', ('"{0}"' -f $userdata), ('"{0}"' -f (Resolve-Path -LiteralPath $Project).Path)) `
    -WindowStyle Normal
