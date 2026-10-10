# Continuous Fermat model-corpus evaluation, 2026-08-19

## Scope and fixed contract

This evaluation uses 20 real printing models from
[Thingi10K](https://github.com/Thingi10K/Thingi10K), a 10,000-model dataset
assembled from Thingiverse for geometry-processing and fabrication research.
The accompanying [paper](https://arxiv.org/abs/1605.04797) describes the set as
representative of 3D-printing meshes found in the wild. Each selected model has
a source URL and a redistributable license.

The frozen manifest is `tools/continuous_fermat/corpus/benchmark20.tsv`.
`benchmark20-provenance.tsv` records model names, licenses, face and layer
counts, hole counts, and normalized-STL SHA-256 values. The set has five models
in each of four strata: short solids, tall solids, short models with holes, and
tall models with holes. It spans 4 to 3,504 faces and 5 to 200 nominal layers.

All runs use a 0.20 mm layer height, 0.40 mm nozzle and nominal line width,
0.80 mm maximum adaptive line width, exact layer deduplication, and no elapsed
budget. A model passes only if every eligible layer passes the unchanged full
validator. Discovery runs keep `CONTINUOUS_FERMAT_CORPUS_REQUIRE_ALL=0` so one
failure cannot hide later categories.

## Baseline

Artifact:
`sandboxes/continuous_fermat_corpus/runs/benchmark20-scarf-baseline-20260819.jsonl`

SHA-256:
`BA9D400C6D34210D028F14602445AB5F2C77B262CAC453BA37CBDC1EDDF68708`

| Measure | Baseline |
| --- | ---: |
| Models | 20 |
| Eligible models | 20 |
| Structurally emittable models | 17 |
| Fully passing models | 8 |
| Nominal layers | 2,007 |
| Structurally emittable layers | 2,000 |
| Fully passing layers | 1,512 |
| Failed layers | 495 |
| Unique layer geometries | 1,906 |
| Exact cache hits | 101 |

The passing models were RC printed Buldozer, Pull String Helicopter, FlexMesh,
Duals of Polyhedra, Customizable Chalice Lathe, Marblevator, Page Keeper, and
Automatic Transmission Model. The last model contributed 167 different holed
layers, so a pass was not limited to simple prisms.

## Failure categories

| Primary category | Layers | Interpretation |
| --- | ---: | --- |
| Redeposition | 375 | The swept beads overlap more than the fixed 0.040 global allowance. |
| Exact coverage | 108 | The swept footprint covers less than 0.980 of the layer. |
| Material | 4 | Deposited volume is outside 0.980 through 1.020. |
| Open path | 4 | A tapering tip no longer produces a closed printable cycle. |
| Crossing | 3 | A narrow neck or changing hole topology produces a nonlocal crossing. |
| Outside or containment | 1 | A final tapering section exceeds the allowed footprint. |

These primary labels need geometric context:

- 303 redeposition failures occur in globally thin sections of Wolverine Claws
  and Christmas tree. A doubled or retraced centerline makes this category
  worse because those layers already contain too much repeated footprint.
- 34 failures occur at necks or hole webs narrower than 2.5 line widths.
- 73 failures are taper-tip progression in the two pyramids.
- Noisemaker contributes 46 ordinary-width exact-coverage misses. Its material,
  outside area, topology, and redeposition already pass; exact coverage is only
  about 0.00002 to 0.00017 below the 0.980 floor.
- The remaining failures are isolated model-specific transitions and seven
  underfilled upper layers of 3D Initials Logo.

The categories overlap geometrically even though each layer has one primary
label. Counts above are the stable primary categories emitted by the harness.

## Doubled-line hypothesis

The original
[Connected Fermat Spirals paper](https://www.cs.tau.ac.il/~dcor/articles/2016/Spirals.pdf)
notes that a fill can start and end at approximately the same outer-boundary
location. That supports treating each layer as a cuttable cycle and putting the
opening scarf at the cycle cut, which is the layer-transition design evaluated
here.

The useful part of the doubled-line idea is graph-theoretic: traversing edges
in both directions can make a path Eulerian and remove travel moves. The
[continuous-toolpath Euler transformation](https://arxiv.org/abs/1908.07452)
uses related duplication for sparse infill. It also deliberately adds support
edges, so it does not promise exact dense-layer material without overlap.
[CrossFill](https://arxiv.org/abs/1906.03027) likewise treats continuity and
self-overlap as simultaneous constraints rather than assuming that retracing is
free.

Four controlled attempts were evaluated against the frozen failures:

1. Replanning any redeposition failure at the maximum certified width did not
   remove Skully's 23 failures and made Spring Pins substantially slower.
2. Replacing a thin ring with one boundary-averaged medial loop removed
   redeposition but put about 0.113 of its bead footprint outside the model.
3. Adding local paired widths to that loop preserved material and removed
   redeposition, but outside area remained about 0.117.
4. Restricting the existing Voronoi medial-axis result to one safe closed loop
   did not apply to the branched Wolverine skeleton and changed no result.

All four variants were rejected and removed. They demonstrate why forward/back
coverage is promising for sparse Eulerian structures but is not a general dense
solid-fill replacement: duplicated traversal and constant extrusion create the
same redeposition that dominates this corpus.

## Promoted general change

The one useful generalization is narrower. If a nominal path is structurally
emittable, has correct total material, passes outside-area and redeposition
limits, and has exact coverage from 0.950 up to 0.980, the planner makes one
bounded redistribution attempt. It moves existing extrusion width from
overlapping donor segments toward the exact uncovered region. The centerline,
total material target, and all acceptance limits remain unchanged. The retry is
discarded unless the complete independent validator passes.

On the focused Noisemaker run this changed 46 exact-coverage failures to passes,
kept the other 101 layers passing, and introduced no pass-to-fail regression.
The run finished 147/147 layers passing. Candidate JSONL SHA-256:
`E5CCB64C2674BAC392834B79FF3C5DCA2CE00505BC04435CC1F7C2DDDE84CD6B`.

## Matched post-change result

Artifact:
`sandboxes/continuous_fermat_corpus/runs/benchmark20-coverage-rebalance-20260819.jsonl`

SHA-256:
`C0ED62A0C486EDDB5B9D15F34CD79139D8F9CFF1C47114B97F91A2AA8FEFF5B6`

| Measure | Baseline | Post-change | Delta |
| --- | ---: | ---: | ---: |
| Fully passing models | 8 | 9 | +1 |
| Fully passing layers | 1,512 | 1,564 | +52 |
| Failed layers | 495 | 443 | -52 |
| Exact-coverage failures | 108 | 56 | -52 |
| Redeposition failures | 375 | 375 | 0 |
| Structural failures | 7 | 7 | 0 |
| Summed model runtime | 1,498.44 s | 1,479.56 s | -1.26% |

All 1,512 baseline passes remained passes. The 52 improvements were 46
Noisemaker layers and six Christmas-tree layers. No acceptance threshold or
model-eligibility rule changed. Runtime should be treated as neutral because a
single local run is noisy; importantly, the bounded retry did not cause the
large slowdown seen in the rejected width-replanning experiment.

## Printer-ready G-code evaluation

Three fully certified models were exported through the complete OrcaSlicer
printer-ready path. This exposed two failures that the planar corpus cannot
measure:

- Page Keeper initially failed because positive mesh-intersection segments
  collapsed to the same serialized XY. The emitter now merges only consecutive
  sub-resolution segments, transfers their exact planned volume to the merged
  chord, checks the resulting physical width, and reruns the existing machine
  footprint, material, volumetric-rate, and transition gates.
- Customizable Chalice initially exported ideal 20:1 ramps whose individual
  rounded Z moves were locally steeper than 20:1. Z is now allocated in 0.001 mm
  serialized steps. Each step requires at least 0.020 mm of serialized XY, the
  ramp is one contiguous run, and its start remains within 2.5 maximum bead
  widths of the cycle opening.

Final independent-auditor results:

| Model | Layers/sections | Extrusion moves | G-code SHA-256 |
| --- | ---: | ---: | --- |
| RC printed Buldozer | 6/6 | 485 | `4A629B2BBB6E9E5767C0FA1996AEC1553DD4C370E96858C6B917894ADD69C5AB` |
| Page Keeper | 21/21 | 105,044 | `367BDBB02DC0C7DA8CE21E8600529D5AD2440C8867EFEE0ADBED9CB4CAE9D767` |
| Customizable Chalice Lathe | 200/200 | 408,166 | `991D528D0280689E4E63C923C0423202B927922B195B7D0CEA934009C5FB7BC5` |

The auditor found no unmarked extrusion, retraction, E-only motion, inter-layer
travel, slope violation, resumed ramp, section-count mismatch, or failed-quality
marker in these final artifacts.

## Regression evidence

- Non-hidden geometry suite: 152 assertions in 17 test cases, passed.
- Continuous print/G-code suite: 66,928 assertions in 27 passing test cases;
  the environment-driven internet test was skipped in this aggregate run and
  passed separately for all three models above.
- Exact negative control: the toothed-ring regression fails without the retry
  at 0.979884 coverage, 1.019 material, and 0.039116 redeposition; it passes with
  the retry restored.
- Procedural eligibility corpus: 100/100 structurally emittable and 98/100
  fully certified. The two remaining cases fail both fixed redeposition and
  coverage limits.
- Sharp/hole corpus: 8/8 fully certified; touching-connector regression passed.
- Independent G-code auditor: 27 tests passed; corpus builder: 3 tests passed.

## Remaining boundary

This is a substantial improvement, not release certification. Only 9 of the 20
fixed models pass every planar layer, and 443 layer failures remain: 375
redeposition, 56 exact coverage, four material, four open path, three crossing,
and one outside/containment. Most remaining failures are thin sections, narrow
necks or hole webs, and tapering tips. Printer-ready output remains fail-closed
for them. The three passing G-code artifacts were audited in software; no
physical printer test was performed.
