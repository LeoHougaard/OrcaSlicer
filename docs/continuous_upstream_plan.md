# Continuous extrusion: infill and upstream preparation

Preserve the working continuous route and stock Klipper export while adding
adjustable infill, wall/shell controls and deliberate wall attachment placement.
The no-travel rule applies throughout the object. Unsupported geometry must be
reported; it must not silently enable travel. Physical TPU validation remains
separate from software verification.

Baseline: commit `4b3fc03e`, based on upstream v2.4.0. Existing native geometry,
flow and configuration checks are documented in continuous_production_plan.md.

## Work and acceptance

- [x] Preserve the current version in a GitHub branch with upstream history.
- [x] Add density-controlled connected rectilinear interiors and ordinary wall/shell controls.
      Verify 0, 15, 40 and 100 percent, holes, narrow sections, changing outlines,
      solid surfaces and transitions between solid and sparse layers.
- [x] Add wall attachment placement controls; verify alignment and layer offsets.
- [x] Integrate preset persistence, cache invalidation, enabled controls and
      actionable errors. Show intentional voids separately from omitted material.
- [x] Remove obsolete experimental implementation from the submission diff.
- [x] Run matched geometry and native export checks, ordinary-mode regressions,
      and inspect the actual GUI preview. Retain the current working app.
- [x] Compare with current upstream and prepare the PR description.
      Record platform and physical-print evidence honestly.
- Commit and push the verified source to both fork branches after the final checks.

## Initial findings

- Solid-fill planning ignores standard density, wall and shell settings.
- The existing coverage score treats intentional sparse voids as defects.
- Layer attachments favor the interior of the solid model. Sparse layers need
  attachments on deposited material, with support across layer transitions.
- The earlier constrained planner and firmware extension are historical work,
  not dependencies of stock Klipper continuous export.
- Large generic changes to GUI option fallback deserve separate review.

## Iterations

1. Sparse concentric ring selection passed the first three planar tests, but
   concentric solid roofs could span long unsupported arcs between sparse rings.
   Rejected as the sparse interior design. Retained the ordinary seam planner
   integration and the density/coverage plumbing.
2. Generate clipped closed rectilinear passes for sparse material, retaining
   complete solid rings where Orca requests skins. Start sparse layer ramps on
   walls. Native shape, density, shell and seam checks are pending.
3. Removed the generic GUI option fallback patch from the submission. Continuous
   settings now belong to the normal preset schema, so missing registration must
   be fixed there. GUI verification remains required before promotion.

Do not claim readiness from compilation alone or remove unmet acceptance items.

4. Native shell checks exposed width-dependent sparse rings around ordinary fill
   surfaces. Expanded solid masks by the configured wall envelope. A taper then
   exposed an outer-wall-only ramp restriction; allowing inner-wall attachments
   restored the contained route. Ten native density/shell/seam cases passed.
5. Core regression passed 291216 assertions in 22 cases before the final inner
   seam and legacy-enum additions. Those additions need a fresh test run.
6. Current upstream requires nozzle-indexed speed/cooling configuration and new
   build dependencies. A separate worktree keeps the working 2.4 app available.
   Retired planner code, settings and firmware extension are excluded there.

7. The inner-seam test caught connector preference bypassing the requested wall
   departure. Track connector edges and prefer actual wall/infill attachments.
   Core checks passed 291223 assertions in 23 cases after the correction.
8. The sparse reference model exposed an abrupt-outline attachment failure at
   layer 99. Departures now consider actual next-layer deposition; solid shoulders
   may connect through their interior. The independent shoulder test and all ten
   native density cases pass. Sparse user part: 65 layers and zero object travel.
   Sparse reference: 200 layers, 254095 extruding moves and zero object travel.
   Core delivery checks pass 291232 assertions in 24 cases.
9. Real GUI slicing, density edit from 15 to 40 percent and 3MF persistence were
   verified. Saved settings include regular mesh slicing plus the separate mode
   toggle. The old Production application remains open and untouched.
10. Current-upstream application builds. Final native/export tests remain pending
    after fixing the diagnostic default-option construction for enum vectors.
    Completed in iteration 11.

11. Final current-upstream core checks passed 298050 assertions in 28 cases.
    Both branches passed 57100 FFF assertions in four cases, including ordinary
    export with the mode disabled. Both final native infill and flow matrices
    passed all ten cases each. At a 35 mm3/s target the unrestricted case reaches
    35; filament, motion and cooling limits reduce it as configured.
12. A generated project inherited a stale process dirty-key list. The diagnostic
    exporter now marks its evaluated process settings explicitly so the GUI does
    not substitute system values. The regenerated project opens with 15 percent infill, two walls and Back seam.

## Evidence and limits

- Windows Release builds of the working app and current-upstream app pass.
- Core and FFF logs: `build-continuous/infill/*-delivery.log` and
  `upstream-core-final.log`. Native matrices: `native-delivery`,
  `upstream-native-delivery`, `flow-delivery`, `upstream-flow-delivery`.
- Solid generalization: nine cases in `generalization-final/results.json`,
  including reference scales 50, 75, 100 and 125 percent.
- Sparse supplied models: `models-sparse/results.json`, 65 and 200 layers,
  zero object travel/retraction. Reference 100/125 percent solid runs took
  about 130/146 seconds with concurrent build activity.
- GUI density editing, slicing and 3MF persistence were exercised in the working
  2.4 app. Current upstream was built and checked through native export/tests.
- Linux/macOS builds and physical TPU finish have not been verified here.

13. Reloading generated projects exposed missing output arguments to Orca's
    3MF importer in the diagnostic tool. Supply plate, preset and version storage
    and release imported metadata. Generated-project reload and native export pass
    on both branches. The native infill script now checks round-trip deposition
    without reapplying overrides; the full upstream matrix passes. This does not
    change the GUI importer or slicing implementation.
