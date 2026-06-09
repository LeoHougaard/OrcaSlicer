param(
    [Parameter(Mandatory = $true)]
    [string]$ProfileBundle,
    [string]$OutputDir = "",
    [string]$PrinterName = "Voron 0.2 0.4 nozzle",
    [string]$SystemPrinterName = "Voron 0.1 0.4 nozzle",
    [string]$FilamentName = "Voron Bambu PLA",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $sourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $OutputDir = Join-Path $sourceRoot "artifacts\continuous-loop\voron-profile\continuous"
}

if ((Test-Path $OutputDir) -and $Force) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
New-Item -ItemType Directory -Force $OutputDir | Out-Null

$zipPath = Join-Path (Split-Path $OutputDir -Parent) "source-profile.zip"
New-Item -ItemType Directory -Force (Split-Path $zipPath -Parent) | Out-Null
Copy-Item -LiteralPath $ProfileBundle -Destination $zipPath -Force
Expand-Archive -LiteralPath $zipPath -DestinationPath $OutputDir -Force

function Read-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Write-Json([string]$Path, $Object) {
    $text = $Object | ConvertTo-Json -Depth 30
    [System.IO.File]::WriteAllText((Resolve-Path -LiteralPath $Path), $text, [System.Text.UTF8Encoding]::new($false))
}

function Set-JsonProp($Object, [string]$Name, $Value) {
    if ($Object.PSObject.Properties[$Name]) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

$printerPath = Get-ChildItem -LiteralPath (Join-Path $OutputDir "printer") -Filter "$PrinterName.json" | Select-Object -First 1
if (!$printerPath) {
    throw "printer profile not found in bundle: $PrinterName"
}

$processPath = Get-ChildItem -LiteralPath (Join-Path $OutputDir "process") -Filter "*.json" | Select-Object -First 1
if (!$processPath) {
    throw "process profile not found in bundle"
}

$filamentPath = Get-ChildItem -LiteralPath (Join-Path $OutputDir "filament") -Filter "$FilamentName.json" | Select-Object -First 1
if (!$filamentPath) {
    $filamentPath = Get-ChildItem -LiteralPath (Join-Path $OutputDir "filament") -Filter "*.json" | Select-Object -First 1
}
if (!$filamentPath) {
    throw "filament profile not found in bundle"
}

$printer = Read-Json $printerPath.FullName
$process = Read-Json $processPath.FullName
$filament = Read-Json $filamentPath.FullName

Set-JsonProp $printer "type" "machine"
Set-JsonProp $printer "use_relative_e_distances" "1"
Set-JsonProp $printer "z_hop" @("0")
Set-JsonProp $printer "retract_when_changing_layer" @("0")
Set-JsonProp $printer "timelapse_type" "0"
Set-JsonProp $printer "time_lapse_gcode" ""
Set-JsonProp $printer "manual_filament_change" "0"

Set-JsonProp $process "type" "process"
Set-JsonProp $process "continuous_filament_mode" "1"
Set-JsonProp $process "continuous_filament_connector_flow_ratio" "0.25"
Set-JsonProp $process "enable_support" "0"
Set-JsonProp $process "enable_prime_tower" "0"
Set-JsonProp $process "sparse_infill_pattern" "zigzag"
Set-JsonProp $process "top_surface_pattern" "rectilinear"
Set-JsonProp $process "bottom_surface_pattern" "rectilinear"
Set-JsonProp $process "internal_solid_infill_pattern" "rectilinear"
Set-JsonProp $process "compatible_printers" @($SystemPrinterName)

Set-JsonProp $filament "type" "filament"
Set-JsonProp $filament "compatible_printers" @($SystemPrinterName)
Set-JsonProp $filament "manual_filament_change" "0"

Write-Json $printerPath.FullName $printer
Write-Json $processPath.FullName $process
Write-Json $filamentPath.FullName $filament

[ordered]@{
    output_dir = (Resolve-Path $OutputDir).Path
    printer = $printerPath.FullName
    process = $processPath.FullName
    filament = $filamentPath.FullName
} | ConvertTo-Json -Depth 4
