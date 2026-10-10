# Continuous Fermat internet corpus harness

The hidden Catch2 test `[internet-corpus]` slices external STL, OBJ, and 3MF files and records reproducible eligibility, correctness, and timing data. Source models are deliberately not checked into the repository. Keep their license and source URL in the manifest.

## Manifest

Use UTF-8 tab-separated lines. Blank lines and lines beginning with `#` are ignored:

```text
# id<TAB>path<TAB>source URL<TAB>optional scale
cone-001<TAB>models/cone.stl<TAB>https://example.test/cone<TAB>1.0
ring-002<TAB>models/ring.obj<TAB>https://example.test/ring
models/local-smoke-test.stl
```

Paths are resolved relative to the manifest. A one-field line uses the file stem plus line number as its ID. The optional scale is useful only when a source explicitly declares non-millimetre units; otherwise leave it at `1.0`.

STL import uses OrcaSlicer's normal STL repair path. OBJ and 3MF use the normal model importer. The harness uniformly moves the mesh's lowest Z to the build plate. It does not otherwise modify geometry by default. `CONTINUOUS_FERMAT_CORPUS_MAX_DIMENSION_MM` can opt into scale-down-only fitting, but certification runs should leave it unset and record explicit source-unit scaling in the manifest.

## Run

Build the existing libslic3r test executable, then run only the hidden corpus test. PowerShell example:

```powershell
cmake --build build-tests --target libslic3r_tests --config Release --parallel 2
$env:CONTINUOUS_FERMAT_CORPUS_MANIFEST = 'C:\corpora\fermat\manifest.tsv'
$env:CONTINUOUS_FERMAT_CORPUS_OUTPUT = 'C:\corpora\fermat\runs\baseline'
& .\build-tests\tests\libslic3r\Release\libslic3r_tests.exe '[internet-corpus]'
```

The output prefix produces `.jsonl` and `.csv`. Both contain one record per nominal layer and one summary per model. JSONL also ends with a run summary. Output is flushed after every model so a crash or manual stop preserves completed results.

Important variables:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `CONTINUOUS_FERMAT_CORPUS_LAYER_HEIGHT` | `0.20` | Nominal layer height, mm |
| `CONTINUOUS_FERMAT_CORPUS_LINE_WIDTH` | `0.40` | Base bead width, mm |
| `CONTINUOUS_FERMAT_CORPUS_NOZZLE` | `0.40` | Nozzle diameter, mm |
| `CONTINUOUS_FERMAT_CORPUS_MAX_LINE_WIDTH` | `0.80` | Allowed adaptive width, mm |
| `CONTINUOUS_FERMAT_CORPUS_MAX_LAYERS` | `2000` | Fail-closed per-model layer cap |
| `CONTINUOUS_FERMAT_CORPUS_MAX_MODELS` | unlimited | Models processed by this run |
| `CONTINUOUS_FERMAT_CORPUS_SHARD_INDEX` | `0` | Zero-based deterministic shard |
| `CONTINUOUS_FERMAT_CORPUS_SHARD_COUNT` | `1` | Number of deterministic shards |
| `CONTINUOUS_FERMAT_CORPUS_DEDUPLICATE_LAYERS` | `true` | Evaluate identical exact-coordinate slices once per model |
| `CONTINUOUS_FERMAT_CORPUS_PROGRESS_EVERY` | `1` | Print progress every N layers; `0` disables console progress |
| `CONTINUOUS_FERMAT_CORPUS_LAYER_BUDGET_MS` | `0` | Optional per-layer elapsed budget; `0` disables it |
| `CONTINUOUS_FERMAT_CORPUS_MODEL_BUDGET_MS` | `0` | Optional per-model elapsed budget; `0` disables it |
| `CONTINUOUS_FERMAT_CORPUS_REQUIRE_ALL` | `false` | Fail on any uncertified eligible layer, elapsed-budget failure, or harness error |

For discovery, leave `REQUIRE_ALL` false so every model is measured even when failures are expected. For a certification run, set it to `true`. This fails when an eligible layer misses any fixed quality target, when an elapsed budget is exceeded, or on harness errors such as import failures or the layer cap. `emittable` records the narrower structural result; `passed` and `quality_ok` both record complete certification. Topology-ineligible models are reported separately.

## Eligibility and results

All nominal layers are sliced before Fermat generation. A model is eligible only when every layer has exactly one `ExPolygon` (holes are allowed) and each layer after the first has positive-area intersection with its predecessor. No layer is sent to Continuous Fermat unless the complete model passes that check.

Exact duplicate slices within a model are generated and validated once by default. The signature canonicalizes only the cyclic starting point, winding direction, and hole order; it does not round, simplify, translate, or otherwise fuzz coordinates. A 64-bit hash selects a cache bucket and full canonical coordinate equality, including every hole, confirms the hit, so hash collisions cannot reuse a result. Every nominal layer still receives its own output record. Cache-hit records have `cache_hit=true`, name `reused_from_layer`, copy all validation metrics and outcome, and report zero generation/validation time plus their actual lookup `layer_elapsed_ms`. Set `DEDUPLICATE_LAYERS=false` for timing comparisons.

The JSONL stream writes and flushes `model_start` immediately and a complete layer checkpoint after every layer; CSV is also flushed per layer. Console progress defaults to every layer. This makes long discovery runs observable and preserves all finished work if the wrapper is interrupted.

Elapsed budgets are discovery controls rather than printer-safety thresholds. When a layer exceeds its budget it is recorded as `layer_budget_exceeded`; when the model budget is exhausted, the current and remaining layers are recorded as `model_budget_exceeded` without beginning more Fermat work. The core generator has no cancellation callback, so a budget is checked immediately before and after each in-process generation/validation call rather than forcibly terminating C++ in the middle of a geometry operation. Use an outer process watchdog as well when investigating a suspected single-call deadlock.

Eligible layers record generation, independent post-validation, and complete layer elapsed time, path size, structural emittability, `quality_ok`, exact and physical coverage, material and redeposition ratios, containment, crossing, spacing, bead-overlap, turnback counts, and the 2.5-line-width thin-feature category. Stable failure categories make results groupable without parsing English diagnostics. Model summaries additionally record import, mesh slicing, total elapsed time, unique geometries, cache hits, and whether all layers met the quality targets.

For a 1,000-model corpus, run separate shard processes with distinct output prefixes. Do not point simultaneous processes at the same output prefix.

## Repository benchmark

`tools/continuous_fermat/corpus/benchmark20.tsv` freezes a 20-model promotion
set selected from the eligible Thingi10K pool. It is balanced across short and
tall models, with and without holes. The adjacent
`benchmark20-provenance.tsv` records license, face count, layer count, hole
count, and the normalized mesh hash. The model files themselves remain under
the ignored `sandboxes/continuous_fermat_corpus` directory.

Run it with the standard 0.4 mm profile and no elapsed budget:

```powershell
$env:CONTINUOUS_FERMAT_CORPUS_MANIFEST = `
    (Resolve-Path 'tools/continuous_fermat/corpus/benchmark20.tsv').Path
$env:CONTINUOUS_FERMAT_CORPUS_OUTPUT = `
    (Join-Path (Resolve-Path 'sandboxes/continuous_fermat_corpus/runs').Path 'benchmark20-run')
$env:CONTINUOUS_FERMAT_CORPUS_LINE_WIDTH = '0.40'
$env:CONTINUOUS_FERMAT_CORPUS_NOZZLE = '0.40'
$env:CONTINUOUS_FERMAT_CORPUS_MAX_LINE_WIDTH = '0.80'
$env:CONTINUOUS_FERMAT_CORPUS_DEDUPLICATE_LAYERS = '1'
$env:CONTINUOUS_FERMAT_CORPUS_LAYER_BUDGET_MS = '0'
$env:CONTINUOUS_FERMAT_CORPUS_MODEL_BUDGET_MS = '0'
$env:CONTINUOUS_FERMAT_CORPUS_REQUIRE_ALL = '0'
& .\build-cert\tests\libslic3r\Release\libslic3r_tests.exe '[internet-corpus]'
```

Keep `REQUIRE_ALL` disabled during failure discovery so all categories are
measured. Enable it only when every eligible layer is expected to pass.
