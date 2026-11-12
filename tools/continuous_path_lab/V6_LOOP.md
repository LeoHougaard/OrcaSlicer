# Continuous Path Lab Iteration Loop

> **Pre-alpha research only. Do not print lab output.** A lab benchmark pass is neither a C++ production pass nor a serialized-G-code safety result.

## Implemented Lab Acceptance Target

For every built-in shape, the current lab `result["ok"]` conjunction requires:

- a non-empty path and at most one contour-tree root;
- sampled strict coverage at least `0.990`;
- sampled internal-overlap ratio at most `0.020`;
- spacing warnings at most `3`;
- sampled containment violations and non-local crossings equal to zero;
- physical bead crossings, non-local bead overlaps, and 135-degree hairpin turnbacks equal to zero; and
- no contour with a sampled `missFraction` greater than `0.25`.

All repair strokes must remain in the one connected path. Rules must address generic causes, not per-shape symptoms. The current `missFraction` allowance is not exact contour retention: up to 25% sampled miss can pass that individual lab check. Exact retention is a stronger research goal and must not be claimed from the present `ok` value.

## Production-Parity Metrics Missing From the Lab

The following production checks are **not implemented by the lab** and are not part of `result["ok"]`:

- exact swept-area coverage at least `0.970`;
- swept area outside the printable region at most `0.020` of printable area;
- modeled deposited-material ratio within `[0.980, 1.020]`;
- per-segment metadata cardinality and multiplier bounds `[0.60, 1.60]`; and
- physical segment widths within `[0.85, 2.0]` nozzle diameters.

Consequently, neither the benchmark nor any lab UI result is a complete production-contract result. These metrics must be implemented and aligned with the C++ geometry before the lab can be used as production-parity evidence.

## Current Loop

1. Run the v6 benchmark with fixed settings:

   ```powershell
   python tools/continuous_path_lab/benchmark.py --algorithm contour_tree_v6 --grid 80 --coverage-grid 70 --json tools/continuous_path_lab/v6_benchmark.json
   ```

2. Sort failures by benchmark score, but use only `result["ok"]` for pass/fail.

3. Classify each failure:

   - `coverage`: sampled underfill cells/groups remain;
   - `overlap`: sampled internal overlap exceeds the threshold;
   - `spacing`: a true different-contour or connector-corridor overlap remains;
   - `crossing`: a route connector crosses or touches the non-local path;
   - `physical`: bead overlap or turnback remains;
   - `containment`: a sampled connector/path point leaves the polygon; or
   - `validator`: a same-contour arc was incorrectly classified as non-local.

4. Implement one small generic rule.

5. Re-run the identical benchmark.

6. Keep the rule only if it improves the worst failures without suppressing or hiding another metric.

## Current v6 Rules

- Use all printable iso-contours.
- Reserve printed hole-barrier corridors in the planning SDF.
- Use semantic spacing for same-contour arc pairs.
- Add local underfill detours from sampled strict-audit cells.

## Audited Baseline

- A fixed-grid rerun on 2026-07-09 passed `0/10` built-in shapes under the implemented v6 lab conjunction. Even `rectangle` failed with sampled coverage `0.993`, 3 spacing warnings, 77 bead-overlap violations, and 8 turnbacks.
- This was not a run of the complete production contract because the production-parity metrics listed above were absent.
- A prior v7 endpoint-dropping experiment regressed simple-rectangle strict coverage to roughly `0.292` and is not an acceptable fallback. Current v7 preserves the candidate for final rejection instead of deleting the endpoint.
- The benchmark delegates pass/fail to the full set of metrics currently represented in `result["ok"]`; that set remains smaller than the production contract.

## Current Measured Blocker

- Local detours plateau around 95–98% sampled coverage on hard shapes.
- Residual SDF contours still produce `0` residual inserts on those shapes.
- Remaining underfill must be addressed from sampled underfill components rather than another closed iso-contour threshold.
- Existing component stitching is an experimental v7 implementation, not an unimplemented v6 idea and not a demonstrated solution.

## Next Work

- Evaluate or port the existing v7 component-stitch experiment only behind complete physical checks.
- For each candidate stitch, preserve all endpoints and required contours and validate connection, semantic spacing, crossing, containment, bead overlap, turnback, sampled coverage, and internal overlap.
- Add the missing exact outside-area, material, metadata, and nozzle-width audits before claiming production parity.
- Re-run deterministic v6 and v7 benchmarks with recorded settings and keep only candidates that satisfy the entire applicable conjunction.
- Do not broaden the production shape gate from rectangle-only until the matching C++ and final serialized-G-code matrices pass.

## Do Not

- Add per-shape branches.
- Suppress spacing or overlap warnings without semantic proof.
- Use legacy raster coverage or a weighted score as a pass condition.
- Add disconnected repair islands.
- Drop endpoints, contours, or terminal strokes merely to improve a reported metric.
- Describe the current contour-miss threshold as exact contour retention.
- Describe `result["ok"]` as the complete production contract.
- Ship a lab score as proof that a C++ export or final serialized G-code is safe.
