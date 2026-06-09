# Continuous Mode Loop

This directory contains the local controller tooling for continuous-filament mode development.

The main Codex controller should be launched as `gpt-5.5` with high reasoning. Any sub-agent it spawns should be `gpt-5.5` with low reasoning and a bounded, short-lived task.

## Native Loop

Run from the OrcaSlicer source root:

```powershell
.\tools\continuous_loop\run_continuous_loop.ps1
```

Useful options:

```powershell
.\tools\continuous_loop\run_continuous_loop.ps1 -SkipConfigure -SkipDepsBuild -SkipBuild -SkipTests -OrcaExe C:\path\to\orca-slicer.exe
.\tools\continuous_loop\run_continuous_loop.ps1 -Loop -MaxIterations 0 -SleepSeconds 30
```

The runner writes durable state to `artifacts/continuous-loop/` and per-run output to `artifacts/continuous-loop/runs/`.

The default native build path is:

```powershell
cmake -S .\deps -B .\deps\build-continuous -G "Visual Studio 17 2022" -A x64 -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build .\deps\build-continuous --config RelWithDebInfo --target deps -- /m
cmake -S . -B .\build-continuous -G "Visual Studio 17 2022" -A x64 -DORCA_TOOLS=ON -DBUILD_TESTS=ON -DDEP_BUILD_DIR="$PWD\deps\build-continuous" -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build .\build-continuous --config RelWithDebInfo --target fff_print_tests -- /m
cmake --build .\build-continuous --config RelWithDebInfo --target OrcaSlicer_app_gui -- /m
```

For end-to-end slicing, prefer passing a `.3mf` project with continuous filament mode already enabled:

```powershell
.\tools\continuous_loop\run_continuous_loop.ps1 -Inputs C:\path\to\continuous-mode-project.3mf
```

STL/OBJ fixtures are useful for parser and fallback checks, but 3MF is the reliable CLI path because it carries printer/process/filament configuration.

## Analyzer

Analyze an existing G-code file directly:

```powershell
python .\tools\continuous_loop\analyze_gcode.py --gcode .\artifacts\continuous-loop\runs\...\plate_1.gcode --out-dir .\artifacts\continuous-loop\manual-analysis
```

The analyzer writes:

- `metrics.json`
- `layer_*.svg` previews for first, middle, last, and worst layers

## Design vs Verification

Use the Fermat spiral paper for slicer-mode design direction: continuity, fewer starts/stops, low curvature, and systematic connection of sub-regions.

Use the MDPI G-code accuracy paper only for verification: G-code-derived geometry and quantitative output checks.

## Lean Voron Runtime Datadir

The source tree should stay complete for builds, but loop runs can use a smaller runtime datadir focused on Voron profiles:

```powershell
.\tools\continuous_loop\create_lean_datadir.ps1 -Force
.\tools\continuous_loop\run_continuous_loop.ps1 -SkipDepsBuild -DataDir .\artifacts\continuous-loop\lean-datadir -Inputs C:\path\to\continuous-mode-project.3mf
```

This copies shared runtime essentials plus `resources/profiles/Voron` and generic/custom filament sources into `artifacts/continuous-loop/lean-datadir`. It is meant to reduce CLI startup/profile loading noise, not replace the full source tree or dependency build.

## Voron Continuous Fixture

Prepare a user-exported `.orca_printer` bundle for continuous-mode CLI slicing:

```powershell
.\tools\continuous_loop\prepare_continuous_voron_profile.ps1 -ProfileBundle "C:\Users\Leo\OneDrive\Projects\Voron 0.2 0.4 nozzle.orca_printer" -Force
.\tools\continuous_loop\run_continuous_loop.ps1 -SkipDepsBuild -SkipConfigure -SkipBuild `
  -LoadSettings ".\artifacts\continuous-loop\voron-profile\continuous\printer\Voron 0.2 0.4 nozzle.json;.\artifacts\continuous-loop\voron-profile\continuous\process\0.20mm Standard 0.2.json" `
  -LoadFilaments ".\artifacts\continuous-loop\voron-profile\continuous\filament\Voron Bambu PLA.json" `
  -Inputs ".\tests\data\20mm_cube.obj"
```

The helper keeps the user printer/process/filament baseline, then injects `continuous_filament_mode` and required V1 safety settings into the ignored test copy.
