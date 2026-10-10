# Internet model corpus

This pipeline builds a license-tracked continuous-slicing corpus from the
[Thingi10K project](https://github.com/Thingi10K/Thingi10K) and its
[official Hugging Face mirror](https://huggingface.co/datasets/Thingi10K/Thingi10K).
Thingi10K contains real 3D-printing models and records the license of every
source Thing. The script excludes unknown and No-Derivatives licenses, then
prefilters to closed, solid, single-component, non-self-intersecting meshes.

Large geometry files are deliberately stored under
`sandboxes/continuous_fermat_corpus` and ignored by git. The generated JSONL
manifests record source and mirror URLs, original Thingiverse URL, author,
license, local path, SHA-256, and verifier results.

## Reproduce

```powershell
python -m venv sandboxes\continuous_fermat_corpus\.venv
& sandboxes\continuous_fermat_corpus\.venv\Scripts\python.exe -m pip install `
    -r tools\continuous_fermat\corpus\requirements.txt
& sandboxes\continuous_fermat_corpus\.venv\Scripts\python.exe `
    tools\continuous_fermat\corpus\build_corpus.py all --target 1000
```

The eligibility verifier uses 0.2 mm cross-sections after uniformly scaling
the mesh's longest axis to 40 mm. At every nonempty plane it reconstructs the
solid with even/odd contour nesting, so holes are preserved. It accepts only
if every plane is exactly one connected polygon and every adjacent pair has
positive-area overlap. The transform and all numeric settings are written to
each manifest entry; no mesh variants are counted as separate models.

To materialize the accepted NPZ geometry as normalized binary STL for an
OrcaSlicer batch run:

```powershell
& sandboxes\continuous_fermat_corpus\.venv\Scripts\python.exe `
    tools\continuous_fermat\corpus\build_corpus.py export-stl
```

`eligible.jsonl` contains the selected 1,000 models. `rejected.jsonl` retains
only geometric rejection reasons, `eligible_extra.jsonl` retains passing models
outside the fixed-size corpus, and `unevaluated.jsonl` lists candidates skipped
after the target was reached. `summary.json` records aggregate counts and source
archive identity.

## Fixed regression benchmark

`benchmark20.tsv` is the stable mixed-topology benchmark used for matched
before/after planner evaluations. It contains five models in each of four
strata: short solids, tall solids, short models with holes, and tall models
with holes. The selection spans 4 to 3,504 faces and 5 to 200 nominal layers.

`benchmark20-provenance.tsv` records each model's name, source license,
complexity, topology, and normalized-STL SHA-256. The source Thingiverse URL
is stored beside each path in `benchmark20.tsv`. `benchmark-phase-coverage.tsv`
and `benchmark-thin-ring.tsv` are focused subsets for the two named failure
families; neither replaces the fixed 20-model promotion gate.

Run the geometry verifier unit tests with:

```powershell
$env:PYTHONPATH = "tools\continuous_fermat\corpus"
& sandboxes\continuous_fermat_corpus\.venv\Scripts\python.exe -m unittest `
    tools.continuous_fermat.corpus.test_build_corpus
```
