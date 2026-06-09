param(
    [string]$SourceRoot = "",
    [string]$BuildDir = "",
    [string]$DepBuildDir = "",
    [string]$OutputRoot = "",
    [string]$Config = "RelWithDebInfo",
    [string]$Generator = "Visual Studio 17 2022",
    [string]$Platform = "x64",
    [string]$OrcaExe = "",
    [string]$Python = "python",
    [string[]]$Inputs = @(
        "tests\data\20mm_cube.obj",
        "tests\data\cube_with_hole.obj",
        "tests\data\two_hollow_squares.obj"
    ),
    [switch]$Loop,
    [int]$MaxIterations = 1,
    [int]$SleepSeconds = 20,
    [switch]$SkipConfigure,
    [switch]$SkipDepsBuild,
    [switch]$SkipBuild,
    [switch]$SkipTests,
    [switch]$SkipSlice,
    [string]$TestTarget = "fff_print_tests",
    [string]$AppTarget = "OrcaSlicer_app_gui"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if ([string]::IsNullOrWhiteSpace($BuildDir)) {
    $BuildDir = Join-Path $SourceRoot "build-continuous"
}
if ([string]::IsNullOrWhiteSpace($DepBuildDir)) {
    $DepBuildDir = Join-Path (Join-Path $SourceRoot "deps") "build-continuous"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $SourceRoot "artifacts\continuous-loop"
}

$StatusPath = Join-Path $OutputRoot "status.md"
$FailuresPath = Join-Path $OutputRoot "failures.md"
$AnalyzerPath = Join-Path $PSScriptRoot "analyze_gcode.py"

function Ensure-LoopFiles {
    New-Item -ItemType Directory -Force $OutputRoot | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $OutputRoot "runs") | Out-Null
    if (!(Test-Path $StatusPath)) {
        "# Continuous Loop Status`n`n" | Set-Content -Path $StatusPath -Encoding UTF8
    }
    if (!(Test-Path $FailuresPath)) {
        "# Continuous Loop Failures`n`n" | Set-Content -Path $FailuresPath -Encoding UTF8
    }
}

function Add-Status([string]$Message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $StatusPath -Encoding UTF8 -Value "- [$timestamp] $Message"
}

function Add-Failure([string]$Message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $FailuresPath -Encoding UTF8 -Value "- [$timestamp] $Message"
}

function Invoke-Step([string]$Name, [scriptblock]$Block) {
    Add-Status "START $Name"
    try {
        & $Block
        Add-Status "PASS $Name"
    }
    catch {
        Add-Status "FAIL $Name`: $($_.Exception.Message)"
        Add-Failure "$Name`: $($_.Exception.Message)"
        throw
    }
}

function Find-OrcaExe {
    if (![string]::IsNullOrWhiteSpace($OrcaExe) -and (Test-Path $OrcaExe)) {
        return (Resolve-Path $OrcaExe).Path
    }
    $candidates = @(
        (Join-Path $BuildDir "src\$Config\orca-slicer.exe"),
        (Join-Path $BuildDir "src\$Config\OrcaSlicer.exe"),
        (Join-Path $BuildDir "src\orca-slicer.exe"),
        (Join-Path $BuildDir "src\OrcaSlicer.exe"),
        (Join-Path $BuildDir "$Config\orca-slicer.exe"),
        (Join-Path $BuildDir "$Config\OrcaSlicer.exe"),
        (Join-Path $BuildDir "orca-slicer.exe"),
        (Join-Path $BuildDir "OrcaSlicer.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }
    return ""
}

function Run-ConfigureDeps {
    if ($SkipDepsBuild) {
        Add-Status "SKIP deps configure/build"
        return
    }
    if (!(Test-Path (Join-Path $DepBuildDir "CMakeCache.txt"))) {
        New-Item -ItemType Directory -Force $DepBuildDir | Out-Null
        & cmake -S (Join-Path $SourceRoot "deps") -B $DepBuildDir -G $Generator -A $Platform -DCMAKE_BUILD_TYPE=$Config
        if ($LASTEXITCODE -ne 0) {
            throw "deps cmake configure failed with exit code $LASTEXITCODE"
        }
    }
    & cmake --build $DepBuildDir --config $Config --target deps -- /m
    if ($LASTEXITCODE -ne 0) {
        throw "deps build failed with exit code $LASTEXITCODE"
    }
}

function Run-Configure {
    if ($SkipConfigure) {
        Add-Status "SKIP configure"
        return
    }
    $cachePath = Join-Path $BuildDir "CMakeCache.txt"
    if (Test-Path $cachePath) {
        $cacheText = Get-Content $cachePath -Raw
        if ($cacheText -match '\$DepBuildDir') {
            Add-Status "REMOVE stale configure cache with literal `$DepBuildDir"
            Remove-Item -LiteralPath $cachePath -Force
        }
    }
    if (Test-Path $cachePath) {
        Add-Status "SKIP configure: CMakeCache.txt already exists"
        return
    }
    New-Item -ItemType Directory -Force $BuildDir | Out-Null
    $depPrefix = Join-Path $DepBuildDir "OrcaSlicer_dep\usr\local"
    & cmake -S $SourceRoot -B $BuildDir -G $Generator -A $Platform `
        "-DORCA_TOOLS=ON" `
        "-DBUILD_TESTS=ON" `
        "-DDEP_BUILD_DIR=$DepBuildDir" `
        "-DCMAKE_PREFIX_PATH=$depPrefix" `
        "-DCMAKE_BUILD_TYPE=$Config"
    if ($LASTEXITCODE -ne 0) {
        throw "cmake configure failed with exit code $LASTEXITCODE"
    }
}

function Run-Build {
    if ($SkipBuild) {
        Add-Status "SKIP build"
        return
    }
    & cmake --build $BuildDir --config $Config --target $TestTarget -- /m
    if ($LASTEXITCODE -ne 0) {
        throw "build target $TestTarget failed with exit code $LASTEXITCODE"
    }
    & cmake --build $BuildDir --config $Config --target $AppTarget -- /m
    if ($LASTEXITCODE -ne 0) {
        throw "build target $AppTarget failed with exit code $LASTEXITCODE"
    }
}

function Run-Tests {
    if ($SkipTests) {
        Add-Status "SKIP tests"
        return
    }
    & ctest --test-dir $BuildDir -C $Config -R "^fff_print" --output-on-failure
    if ($LASTEXITCODE -ne 0) {
        throw "fff_print ctest failed with exit code $LASTEXITCODE"
    }
}

function Run-SliceAndAnalyze([string]$RunDir) {
    if ($SkipSlice) {
        Add-Status "SKIP slice"
        return
    }
    $exe = Find-OrcaExe
    if ([string]::IsNullOrWhiteSpace($exe)) {
        throw "OrcaSlicer.exe not found. Pass -OrcaExe or build target $AppTarget first."
    }

    foreach ($inputFile in $Inputs) {
        $inputPath = Join-Path $SourceRoot $inputFile
        if (!(Test-Path $inputPath)) {
            throw "input file not found: $inputPath"
        }

        $inputName = [IO.Path]::GetFileNameWithoutExtension($inputPath)
        $inputRunDir = Join-Path $RunDir $inputName
        New-Item -ItemType Directory -Force $inputRunDir | Out-Null

        Add-Status "Slice $inputFile"
        & $exe --slice 1 --outputdir $inputRunDir $inputPath
        if ($LASTEXITCODE -ne 0) {
            throw "slice failed for $inputFile with exit code $LASTEXITCODE"
        }

        $gcode = Join-Path $inputRunDir "plate_1.gcode"
        if (!(Test-Path $gcode)) {
            $found = Get-ChildItem -Path $inputRunDir -Recurse -Filter "*.gcode" | Select-Object -First 1
            if ($found) {
                $gcode = $found.FullName
            }
        }
        if (!(Test-Path $gcode)) {
            throw "slice completed but no G-code was found in $inputRunDir"
        }

        $analysisDir = Join-Path $inputRunDir "analysis"
        & $Python $AnalyzerPath --gcode $gcode --out-dir $analysisDir
        if ($LASTEXITCODE -ne 0) {
            throw "G-code analysis failed for $gcode with exit code $LASTEXITCODE"
        }

        Copy-Item -Force (Join-Path $analysisDir "metrics.json") (Join-Path $OutputRoot "latest-metrics.json")
    }
}

function Run-Iteration([int]$Iteration) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runDir = Join-Path (Join-Path $OutputRoot "runs") "$stamp-iteration-$Iteration"
    New-Item -ItemType Directory -Force $runDir | Out-Null
    Add-Status "ITERATION $Iteration run_dir=$runDir"

    Invoke-Step "deps" { Run-ConfigureDeps }
    Invoke-Step "configure" { Run-Configure }
    Invoke-Step "build" { Run-Build }
    Invoke-Step "tests" { Run-Tests }
    Invoke-Step "slice-and-analyze" { Run-SliceAndAnalyze $runDir }
}

Ensure-LoopFiles
Add-Status "Controller policy: main Codex gpt-5.5 high; sub-agents gpt-5.5 low; native Windows first."

$iteration = 1
while ($true) {
    try {
        Run-Iteration $iteration
        Add-Status "GREEN iteration $iteration"
    }
    catch {
        Add-Status "RED iteration $iteration`: $($_.Exception.Message)"
        if (!$Loop) {
            throw
        }
    }

    if (!$Loop) {
        break
    }
    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) {
        break
    }
    $iteration += 1
    Start-Sleep -Seconds $SleepSeconds
}
