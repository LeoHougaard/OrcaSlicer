# Exercise the real launcher against disposable paths without starting a slicer.
$ErrorActionPreference = 'Stop'
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('orca-launcher-' + [guid]::NewGuid())
$repo = Join-Path $fixture 'Canonical source'
$data = Join-Path $fixture 'OrcaSlicer-data'
$launcher = Join-Path $repo 'scripts\open_canonical_orca.ps1'
$executable = Join-Path $repo 'build-continuous\src\Release\orca-slicer.exe'
$project = Join-Path $data 'projects\Continuous Infill.3mf'
$userdata = Join-Path $data 'userdata'

$capture = @{ Launch = $null }

function Start-Process {
    param($FilePath, $WorkingDirectory, $ArgumentList, $WindowStyle)
    # A local mock ensures the fixture can never launch an application.
    $capture.Launch = @{
        FilePath = $FilePath
        WorkingDirectory = $WorkingDirectory
        ArgumentList = $ArgumentList
        WindowStyle = $WindowStyle
    }
}

function Expect-Failure([string]$Message) {
    $capture.Launch = $null
    try { & $launcher }
    catch {
        if ($_.Exception.Message -notlike $Message) { throw }
        if ($capture.Launch) { throw 'Invalid inputs started a process.' }
        return
    }
    throw "Expected launcher failure: $Message"
}

try {
    New-Item -ItemType Directory -Path (Split-Path $launcher) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'open_canonical_orca.ps1') -Destination $launcher
    Expect-Failure 'Build the canonical Release application*'
    New-Item -ItemType Directory -Path (Split-Path $executable) -Force | Out-Null
    New-Item -ItemType File -Path $executable | Out-Null
    Expect-Failure 'Project does not exist:*'
    New-Item -ItemType Directory -Path (Split-Path $project) -Force | Out-Null
    New-Item -ItemType File -Path $project | Out-Null
    Expect-Failure 'Restore the preserved canonical userdata*'
    New-Item -ItemType Directory -Path $userdata | Out-Null

    & $launcher
    $launch = $capture.Launch
    if ($launch.FilePath -ne $executable -or
        $launch.WorkingDirectory -ne (Split-Path $executable) -or
        $launch.WindowStyle -ne 'Normal' -or
        ($launch.ArgumentList -join '|') -ne ('--datadir|"{0}"|"{1}"' -f $userdata, $project)) {
        throw ('Default project launch paths or argument quoting changed: ' + ($launch | ConvertTo-Json -Compress))
    }
    $custom = Join-Path $fixture 'Other project.3mf'
    New-Item -ItemType File -Path $custom | Out-Null
    & $launcher -Project $custom
    $launch = $capture.Launch
    if ($launch.ArgumentList[2] -ne ('"{0}"' -f $custom)) {
        throw 'Explicit project path was not preserved.'
    }
    Write-Output 'Launcher checks passed: missing inputs, default project, explicit project, quoted paths.'
}
finally {
    # Only the uniquely named, absolute temporary fixture created above is removed.
    $resolved = [IO.Path]::GetFullPath($fixture)
    $temp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolved.StartsWith($temp, [StringComparison]::OrdinalIgnoreCase) -or
        (Split-Path $resolved -Leaf) -notlike 'orca-launcher-*') {
        throw 'Unexpected launcher test fixture path.'
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

