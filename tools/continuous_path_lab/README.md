# Continuous Path Lab

> **Pre-alpha research tool only. Do not use a lab result or displayed path as printer G-code or as evidence that a production export is safe.**

This is a standalone local tester for experimental one-path-per-layer geometry.

Run:

```powershell
python tools/continuous_path_lab/server.py --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

The lab accepts built-in polygons, simple DXF outlines, polygon JSON, and STL cross-sections. It generates SDF iso-contours, builds a contour containment tree, routes that tree, and reports sampled coverage, internal overlap, crossing, spacing, containment, non-local bead-overlap, and 135-degree turnback metrics.

This code is intentionally outside OrcaSlicer integration. The backend is pure Python and dependency-free so failures are easy to reproduce. Its API shape is intended to remain useful for later experiments:

```text
polygon layer + planner options -> contours + contour tree + path + lab metrics
```

The lab does **not** implement the complete production acceptance contract. In particular, it does not calculate production exact swept-area coverage, swept area outside the printable region, deposited-material ratio, or nozzle-relative physical-width limits. Its sampled contour-miss criterion also tolerates a `missFraction` of up to `0.25`; a lab `ok` result therefore does not prove that every required contour was retained exactly.

The current lab `result["ok"]` conjunction requires a non-empty path, at most one contour-tree root, zero sampled containment violations, zero crossings, zero physical extrusion violations, spacing warnings within the selected limit, no contour whose sampled `missFraction` exceeds `0.25`, sampled coverage at or above the selected threshold, and internal-overlap ratio at or below the selected threshold. These are lab criteria only.

## Planner Variants

The `legacy_cfs` option calls the older `tools/continuous_fermat` SDF prototype for comparison.

`contour_tree_v4` keeps v3's hole barriers, then tries a small deterministic set of start/winding variants and returns the best lab-validated candidate. It is a recovery experiment; v2 and v3 remain fixed baselines.

`contour_tree_v5` is the coverage-audit branch. It keeps all printable iso-contours, attempts residual-gap spirals after the first route, and reports a strict sampled coverage audit that separates underfill from internal bead overlap.

`contour_tree_v6` starts from v5 and adds verifier-guided repairs: printed hole barriers reserve a full bead corridor, same-contour arc pairs are treated semantically by the spacing audit, and existing segments may be locally bent through sampled underfill cells. Repairs are inserted into one path, but only the final lab `result["ok"]` conjunction determines a lab pass.

`contour_tree_v7` is an endpoint and component-stitch research branch. It contains component-based underfill stitching and deliberately disables the earlier `physicalDrawingGuard` so a candidate is preserved for the final audit instead of repairing a collision by deleting an endpoint. A prior endpoint-dropping experiment reduced simple-rectangle coverage to roughly `0.292`; that behavior is not an acceptable fallback. A rendered v7 candidate may be invalid, and neither a displayed candidate nor a v7 lab pass is production evidence.

See [V6_LOOP.md](V6_LOOP.md) for the benchmark loop, the exact distinction between implemented lab criteria and missing production-parity metrics, and the current research blockers.
