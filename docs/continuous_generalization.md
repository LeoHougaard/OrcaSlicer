# Continuous mode generalization

The mode must slice ordinary printable shapes and scaled versions of the original
fixture without inserting object travel, retractions, or changing filament feed.
Unreachable material may be omitted, with its missing area reported. Retain the
original verified result and editable native settings.

## Evaluation contract

- Preserve the original executable and source in `build-continuous/generalization`
  and `build-continuous/src/Release/continuous-baseline.exe`.
- Reproduce the user's recovered new part and the original at 50%, 75%, 100%, and
  125%. Use a larger evaluation bed to avoid confusing placement errors with
  planner errors. Use the same 120-second search limit and physical bead settings.
- Add independent rectangular, curved, hollow, narrow, and moving/tapered layer
  cases. Exhaust the same finite candidate set for deterministic geometry checks.
- Require exact XYZ continuity, monotone Z, valid bead widths, contained
  connections, positive extrusion, and constant nominal filament feed. Audit
  native G-code. Keep failures on impossible disconnected transitions explicit.
- Measure missing, outside, and excess material. A successful export alone is
  insufficient. Retain coverage of the original and avoid broad omission as a
  substitute for connecting printable routes.
- Work locally in source, tests, docs, and isolated build outputs. Preserve the
  open GUI's unsaved project. No printer operations.

## Failure ledger

1. The 50% fixture fails to connect layer 43. Reproduced with the old binary.
   Suspected cause: greedy attachment to a fixed reference is not a complete
   connection search. High impact, cause still under investigation.
2. The exact recovered project fails at layer 16 with the old connector and
   succeeds with the corrected connector. The downloaded STL with demonstration
   settings did not reproduce it. The evaluation tool now loads project geometry
   and process configuration so this difference is represented in verification.
3. A translating rectangular section fails at layer 5 in the independent layer
   test, and a leaning solid fails at layer 14 in native slicing. Both connect
   with the corrected attachment search.
4. A synthetic box initially crashed in mesh-bound calculation because the test
   generator wrote zero normals. Correcting the STL normals makes it pass on the
   old build. This was an evaluation defect, not a continuous-planner regression.
   The evaluation tool now rejects meshes emptied by import repair.
5. The shrinking-square experiment exposes 9.1% and 10.6% estimated missing area
   in its final 2.0 mm and 1.7 mm sections. The attempted 8% per-layer fill target
   was not achieved. Connections pass. The connection regression test checks
   that attachment preserves the selected planar coverage exactly; it does not
   certify these small sections as sufficiently filled.

## Iterations

1. Replace the fixed inset vertex reference with a point of high clearance;
   when no shared column exists, use the current/next-layer overlap. Check that
   an attachment can leave for the next layer, and project both the desired
   reference and the actual previous endpoint. Both user failures and the moving
   independent shape connect. Promoted after the matched scale, independent
   geometry, native export, and GUI checks below.
2. Match contour join cut length to adjoining bead widths. Rejected: the small
   square fill failures remain, with slightly worse missing area in one case.
3. Relax whole-path widths on short routes. Rejected: the objective improves by
   reducing excess but missing area gets worse.
4. Relax widths on short pieces near corners. Rejected: it still misses the small
   square fill target and adds too much work. The final region planner is byte
   identical to the preserved baseline. No fill refinement was promoted.

## Final verification

All ten native cases export successfully and pass the continuous command audit.
The five independent solids are a box, 1.2 mm strip, ring, taper, and leaning
prism. The fixture succeeds at 50%, 75%, 100%, and 125%. The tenth case is the
user's exact recovered project, including its saved geometry and process values.

| Case | Whole slice/export seconds | Estimated missing material |
| --- | ---: | ---: |
| Box | 0.63 | 1.81% |
| Narrow strip | 0.10 | 1.85% |
| Ring | 1.03 | 1.06% |
| Taper | 0.23 | 3.32% |
| Leaning solid | 0.23 | 3.26% |
| Original at 50% | 16.56 | 1.87% |
| Original at 75% | 66.39 | 1.26% |
| Original at 100% | 126.95 | 1.06% |
| Original at 125% | 129.71 | 0.97% |
| Recovered new part | approximately 12 | 1.03% |

The time-limited width search may finish different numbers of candidates between
runs. The deterministic layer tests exhaust the finite width search and cover
five shapes, three scales, and two rotations, translated away from the origin.
The combined continuous, constrained-bead, and Arachne checks pass 25 test cases
with 291,047 assertions. These check 30 independent shape/transform combinations,
exact XYZ continuity, monotone Z, unchanged planar coverage, widths, and containment.
The rebuilt ordinary FFF flow, fill, and G-code export checks also pass 68
assertions in four cases with continuous mode disabled.

The rebuilt GUI at `build-continuous/src/Generalized/orca-slicer.exe` loaded the
recovered project after startup and sliced it successfully. Its actual generated
G-code has 65 layers and 63,643 extrusion moves with no object travel or retraction.
The editable settings remain visible. The original open GUI and its unsaved work
were preserved. `build-continuous/Open Continuous Mode.lnk` now starts this build
with a separate local data directory.

The remaining small-section fill issue is unchanged. This correction does not
prove that every geometry has a valid continuous route or verify physical TPU
deposition or Klipper step timing. Impossible transitions still fail explicitly.

## Plan

- [x] Preserve baseline and recover the user's current geometry/settings.
- [x] Classify reproduced failures and establish independent shape checks.
- [x] Make a general connection/planning correction and compare matched results.
- [x] Verify native exports and the updated GUI build; document remaining limits.

Exact commands, logs, and per-layer measurements are kept under
`build-continuous/generalization`.
