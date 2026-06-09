param(
    [string]$SourceRoot = "",
    [string]$OutputDir = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $SourceRoot "artifacts\continuous-loop\lean-datadir"
}

$resources = Join-Path $SourceRoot "resources"
if (!(Test-Path $resources)) {
    throw "resources directory not found: $resources"
}

if ((Test-Path $OutputDir) -and $Force) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
New-Item -ItemType Directory -Force $OutputDir | Out-Null

function Copy-IfExists([string]$RelativePath) {
    $src = Join-Path $resources $RelativePath
    if (!(Test-Path $src)) {
        return
    }
    $dst = Join-Path $OutputDir $RelativePath
    $parent = Split-Path $dst -Parent
    New-Item -ItemType Directory -Force $parent | Out-Null
    if ((Get-Item $src).PSIsContainer) {
        Copy-Item -LiteralPath $src -Destination $parent -Recurse -Force
    }
    else {
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
}

# Keep runtime essentials broad enough that CLI startup and project loading do not fail.
$essentialDirs = @(
    "icons",
    "images",
    "i18n",
    "shapes",
    "custom_gcodes",
    "printers",
    "web",
    "profiles_template"
)
foreach ($dir in $essentialDirs) {
    Copy-IfExists $dir
}

New-Item -ItemType Directory -Force (Join-Path $OutputDir "profiles") | Out-Null
Copy-IfExists "profiles\Voron"
Copy-IfExists "profiles\Voron.json"

# Filaments are not bundled under Voron, so keep generic/custom filament sources available for 3MF/config loading.
Copy-IfExists "profiles\Custom"
Copy-IfExists "profiles\Generic"
Copy-IfExists "profiles\BBL\filament\Generic"
Copy-IfExists "profiles\Creality\filament\Generic"

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("s")
    source_root = $SourceRoot
    output_dir = $OutputDir
    purpose = "Lean runtime datadir for continuous filament loop testing with Voron profiles."
    preferred_printer = "Voron 0.1 0.4 nozzle"
    preferred_process = "0.20mm Standard @Voron"
    note = "Use with run_continuous_loop.ps1 -DataDir <this folder> and a .3mf carrying the actual model/config."
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $OutputDir "continuous-loop-manifest.json") -Encoding UTF8

Write-Output "Lean datadir created: $OutputDir"
