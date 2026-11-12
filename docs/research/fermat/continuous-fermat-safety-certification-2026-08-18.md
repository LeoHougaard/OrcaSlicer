# Continuous Fermat Safety Certification — 2026-08-18

> Scope note: this run predates the continuously extruding opening-scarf layer
> transition. Its planner results remain useful, but its G-code results do not
> certify the revised inter-layer emitter; that corpus must be rerun.

## Conclusion

**Not safe for printing and not approved for promotion.**

The branch is strictly safer than commit `00bc1100cbfab47297af00a2ce08099a5cd1a813`: structural hole routing is fixed for the observed cases, unsafe ordinary-slicing fallback is removed, warning-marked output is blocked from printer-ready export and by the independent auditor, and serialized material deviation is a hard failure. The mandatory high-risk corpus still has 139/730 failed-quality layers, so no commit or push is permitted.

No physical printer was connected to, uploaded to, or operated.

## Repository and evidence baseline

- Branch: `codex/continuous-slicing-safety-hardening`
- Upstream: `leo/codex/continuous-slicing-safety-hardening`
- Starting commit: `00bc1100cbfab47297af00a2ce08099a5cd1a813`
- Starting divergence: 0 ahead, 0 behind
- Fresh build directory: `build-cert`
- Fresh configuration:

```powershell
cmake -S . -B build-cert -G "Visual Studio 17 2022" -A x64 `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_PREFIX_PATH="C:/Users/Leo/Code Projects/orcaslicer-src/deps/build/OrcaSlicer_dep/usr/local" `
    -DSLIC3R_GUI=OFF -DBUILD_TESTS=ON
```

The existing `build`, `build-tests`, and `build-asan` caches point at the repository's former OneDrive location and were not modified or treated as fresh evidence. Installed dependency CMake files also embed that old path, so a temporary directory junction to the current checkout was used only while linking the fresh tests and was removed after verification.

## Untracked artifact audit

The 4.95 GB `sandboxes/continuous_fermat_corpus` tree is reproducible but valuable raw evidence and remains untracked:

| Part | Files | Bytes | Disposition |
| --- | ---: | ---: | --- |
| `downloads` | 2 | 4,083,478,834 | Keep archive for reproducibility; `download.pid` is stale generated state |
| `models` | 5,366 | 778,533,644 | Keep extracted/normalized corpus inputs |
| `runs` | 134 | 17,821,764 | Keep raw historical and 2026-08-18 reports |
| `.venv` | 2,502 | 72,939,147 | Reproducible local environment; never commit |

`summary.json` identifies the Thingi10K official mirror, archive SHA-256 `0A9E3E7F0DF0393C9F12959B5C3691EC01A4032A9C5C13EE9CC9A6E3F3D11E0C`, 4,080 candidates, 2,624 verified candidates, 1,006 eligible models, and 1,000 selected models. Its own SHA-256 is `AE243C9D04E8333EF3E7A5AB6349D86A0F9126DF3EC7394C93C76CE457D5173E`.

The root `sandboxes/continuous-fermat-*.csv/.jsonl` reports are nine pre-existing historical pairs plus one current strict smoke pair (359,871 bytes total). They are generated but useful for reconstructing discovery behavior, so they were preserved and not proposed for commit. `tools/continuous_fermat/__pycache__` and the corpus virtual environment are reproducible caches, not source or commit candidates.

The three opaque root files are Boost `unique_path()` G-code artifacts left by the research-preview unit test while its input stream was still open on Windows. All have three failed-certification markers and are retained as small raw auditor fixtures:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `0683-785c-963f-1447` | 30,696 | `9EA89C604F2605F317E6E29D554F092C1122F83CBEEF3095FA1ED48CE619B4F4` |
| `67ff-29d4-5746-8b02` | 31,309 | `A1E545C1AA31588817758C46A237895BCDA2C28553C95DF3FDB07B55DF56E2DD` |
| `9a23-34fc-e4ec-c78c` | 31,201 | `4EBA06EFDC9A2879789EC1CF3C9C49694291E86E19B3AEE88D7142CA6C8AD5A7` |

The test now closes the stream and requires successful cleanup. Six equivalent artifacts created during this certification session were removed after provenance was established; no pre-existing artifact was removed.

## Safety changes

1. A hole-layer generation or structural-validation failure now throws an explicit slicing error. It can no longer return `false` and retain ordinary toolpaths.
2. A safely cut-open planning domain now uses the existing topology-preserving contour-tree route. The prior legacy flattened chain could reconnect opposite slit sides.
3. Printer-ready export recursively scans protected paths and rejects every retained geometric-certification failure before opening output.
4. Cached/imported warning-marked artifacts remain blocked even after the mode is disabled; only the explicit internal research-preview purpose can inspect non-warning Continuous Fermat artifacts.
5. Serialized material outside 0.980–1.020 is a hard failure instead of an emitted warning.
6. Corpus `passed` now means complete `validation.ok`, not merely structural `emittable`; `REQUIRE_ALL=1` fails on quality misses and budget failures.
7. The standalone Python G-code auditor rejects `_CONTINUOUS_FERMAT_VALIDATION_WARNING` artifacts.
8. Tests cover certified two-hole end-to-end slicing, printer-ready/preview separation, material rejection, cached artifacts, and Windows cleanup of diagnostic output.

No filename, model ID, fixture name, relaxed limit, hidden advisory, or ordinary-slicing fallback was added.

## Before and after high-risk metrics

Manifest: `sandboxes/continuous_fermat_corpus/targeted_failures.tsv`, SHA-256 `FC531CDDB0AD7A61FC454CEEE2D756958EC2DC0B243453EB772F637DF3DEDB4F`.

Both runs used 0.20 mm layers, 0.40 mm nominal width/nozzle, 0.80 mm maximum width, exact-coordinate layer deduplication, and no elapsed budgets.

| Metric | Fresh starting commit | Working-tree result |
| --- | ---: | ---: |
| Models | 5 | 5 |
| Structurally emittable models | 2 | 5 |
| Fully certified models | 1 | 1 |
| Layers | 730 | 730 |
| Structurally emittable layers | 493 | 730 |
| Fully certified layers | 356 | 591 |
| Failed-quality layers | 374 | 139 |
| Crossing failures | 237 | 0 |
| Maximum crossings / turnbacks | 5 / 1 | 0 / 0 |
| Containment maximum | 0 | 0 |
| Exact-coverage failures | 15 | 17 |
| Redeposition failures | 122 | 119 |
| Material failures | 0 | 3 |
| Maximum layer time | 4,872.2 ms | 2,282.4 ms |
| Total test time | 1,017.4 s | 468.3 s |

Per-model full-quality layers after the fix: `thingi10k-36082` 25/80, `36086` 172/200, `36090` 172/200, `36373` 172/200, and `37283` 50/50. All 730 layers are now structurally emittable. The remaining categories are 119 redeposition, 17 exact coverage, and 3 material failures. Fixed thresholds were not changed.

Baseline artifacts:

- `runs/cert-baseline-head-target5-20260818.jsonl` — SHA-256 `CFE6F9A36231CBE0C14C45159B3B7631E6CC318AD738D54E86254349A97D079B`
- `runs/cert-baseline-head-target5-20260818.csv` — SHA-256 `A903D6BAAE11A8979473F5A3623253FF96A856EC03BC3888CEB70AB19981D408`

Final artifacts:

- `runs/cert-final-target5-20260818.jsonl` — SHA-256 `14C9B89E94E42CB9229E4A4880442B4B0F0AD6DAC087B5A35CD19C186C19E3E6`
- `runs/cert-final-target5-20260818.csv` — SHA-256 `3CA10C1CBF6D2C15720A90C250445CA69C58BC4F7E349DC4EA722C9F662008D7`

The actual final run used unsupported `..._JSONL`/`..._CSV` environment names, so the harness wrote its documented default `targeted_failures-results.*`; those files were moved, without modification, to the final artifact names above. The corrected reproducible command is:

```powershell
$env:CONTINUOUS_FERMAT_CORPUS_MANIFEST = `
    (Resolve-Path '.\sandboxes\continuous_fermat_corpus\targeted_failures.tsv').Path
$env:CONTINUOUS_FERMAT_CORPUS_OUTPUT = `
    (Join-Path (Resolve-Path '.\sandboxes\continuous_fermat_corpus\runs').Path `
        'cert-final-target5-20260818')
$env:CONTINUOUS_FERMAT_CORPUS_REQUIRE_ALL = '1'
$env:CONTINUOUS_FERMAT_CORPUS_DEDUPLICATE_LAYERS = '1'
$env:CONTINUOUS_FERMAT_CORPUS_PROGRESS_EVERY = '10'
$env:CONTINUOUS_FERMAT_CORPUS_LAYER_BUDGET_MS = '0'
$env:CONTINUOUS_FERMAT_CORPUS_MODEL_BUDGET_MS = '0'
& .\build-cert\tests\libslic3r\Release\libslic3r_tests.exe '[internet-corpus]' --durations yes
```

Expected result: Catch exit 42, `failed_layers=139`, `passed_models=1`, `eligible_models=5`. This failure is the promotion decision.

## Builds and validators

```powershell
cmake --build build-cert --target libslic3r_tests --config Release --parallel 1
& .\build-cert\tests\libslic3r\Release\libslic3r_tests.exe '[ContinuousFermat]~[.]' --durations yes
& .\build-cert\tests\libslic3r\Release\libslic3r_tests.exe `
    '[corpus-100],[corpus-touch],[sharp-hole-corpus]' --durations yes

cmake --build build-cert --target fff_print_tests --config Release --parallel 1
& .\build-cert\tests\fff_print\Release\fff_print_tests.exe '[continuous]' --durations yes

python -m unittest tools.continuous_fermat.test_validate_gcode
Push-Location .\tools\continuous_fermat\corpus
& ..\..\..\sandboxes\continuous_fermat_corpus\.venv\Scripts\python.exe `
    -m unittest test_build_corpus.py
Pop-Location
```

Results:

- production geometry: 148 assertions / 16 cases, passed;
- procedural eligibility and sharp/hole corpora: 5 assertions / 3 cases, passed; 100/100 structurally emittable, 97/100 full quality, sharp/hole 8/8 full quality;
- Continuous print/G-code: 70,467 assertions / 25 cases, passed;
- G-code auditor: 26 tests, passed;
- corpus-construction geometry: 3 tests, passed.

The linker emitted the repository's existing `LNK4098` runtime-library warning. A separate test emits an unrelated missing/empty `info/nozzle_info.json` parse log; assertions still pass.

## Determinism and budgets

`thingi10k-36069` passed 200/200 layers with deduplication enabled and disabled. Projecting away elapsed times and cache bookkeeping produced byte-identical geometry and validation records for all 200 layers. Raw reports are `cert-final-determinism-{dedup,no-dedup}-20260818.{jsonl,csv}`.

A 1 ms layer budget marked all 200 otherwise-quality-safe layers `layer_budget_exceeded` and returned exit 42. A 1 ms model budget skipped/failed all 200 layers as `model_budget_exceeded` and returned exit 42. Reports are `cert-final-{layer,model}-budget-20260818.{jsonl,csv}`.

Budgets are fail-closed accounting controls, not preemptive timeouts. They are checked before and after an in-process geometry call; one pathological call can still overrun and requires an outer process watchdog.

## Emitted G-code and visual inspection

The test `Continuous slicing export preserves the serialized section contract` retained a current-build printer-ready artifact at `runs/cert-safe-prism-20260818.gcode`, SHA-256 `5130E1D401D2F48794668041383B7A57E5C630CA84AEF2E44A538CDF0F6C5C79`.

```powershell
python .\tools\continuous_fermat\validate_gcode.py `
    .\sandboxes\continuous_fermat_corpus\runs\cert-safe-prism-20260818.gcode `
    --expected-layers 3 --expected-sections 3 --firmware marlin2
```

Result: 3 sections, 3 layers, 3,513 extrusion moves, passed. The auditor verified exact section/layer accounting, closure, extrusion/travel continuity, modal state, positive/bounded Z transitions, and restricted startup/shutdown. C++ integration tests additionally cover the transformed bed polygon, printable height, first-approach Z-hop suppression, serialized material, volumetric limits, and printer-ready warning rejection.

`runs/cert-safe-prism-20260818.png` (SHA-256 `B111BFA69F72763F111E104E7AACB4D950A999B7C48EC766DCB7F2F361F4D8F1`) renders the exact positive-E XY strokes from all three protected sections. Visual inspection showed three congruent closed square-spiral layers, co-located start/end markers, no visible crossing, and no stray interlayer extrusion. This is supporting evidence, not a substitute for the numeric validators.

The three preserved historical warning-marked G-code files now fail the standalone auditor explicitly at each warning marker.

## Unfinished promotion gates

1. Resolve the 139 high-risk quality failures without relaxing coverage, material, or redeposition thresholds.
2. Resolve the three quality misses in the procedural 100-shape corpus.
3. Add a preemptive outer watchdog or cancellable core operation for pathological geometry calls.
4. Rerun the complete 1,000-model manifest only after the mandatory high-risk subset passes. The remaining 995 models were not rerun because they cannot reverse this gate failure.
5. Perform machine-specific physical qualification separately; this certification did not and may not operate a printer.

Because gates 1–3 remain open, the working tree is intentionally left uncommitted and unpushed.
