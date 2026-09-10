# Continuous extrusion implementation

## Agreed requirements

- One uninterrupted extrusion path through a solid part, including layer transitions.
- **Constant physical filament feed. No feed-rate modulation or retractions.** Toolhead speed controls bead width. Calibration may adjust motion, never filament feed.
- Klipper is the first firmware target. A constant nominal E/time in G-code is not proof of constant physical feed during acceleration.
- Generate the interior for continuity; conventional wall loops and infill patterns need not be preserved. Start with 100% fill, without supports.
- Gradual Z ramps are allowed. Minimize geometric error from missing and excess material while preserving dimensions and smooth outer-wall passes. Local wall-to-interior junctions are acceptable.
- Configurable asymmetric bead-width limits; prefer the profile's nominal width and widening over extreme thinning. Initial reference: 0.4 mm nozzle, 0.42 mm nominal width, model walls at least 1 mm. No final minimum/maximum width was agreed.
- Adjustable computation budget, approximately two minutes for a midsized part. At the budget limit, retain progress and show completed paths and unresolved areas. Continuing must preserve search progress.
- Acceptance model: supplied Hardest Part For Slicer Mode, original orientation, approximately 93.652 x 61.938 x 40 mm. STL coordinates are metres; the matching STEP declares metres. Never silently interpret this fixture as millimetres.

## Work and verification

Latest task: fix failures on scaled and unrelated parts without adding
model-specific rules. The layer attachment correction passes the recovered new
part, four fixture scales, five independent solids, and 30 shape/scale/rotation
combinations. Native and GUI exports pass the command audit. See
`docs/continuous_generalization.md` for reproduced failures, matched evidence,
and the unchanged small-section fill limitation.

Previous task: integrate the region planner into normal Orca slicing with editable
settings. Continuity takes priority over unreachable material: omit it and report
the missing area, never insert travel to fill it. Verify layer 111, all layer
transitions, saved settings, and the actual exported command stream in the GUI.
The Klipper module remains experimental until physical verification.

Previous task: inspect the actual region routes in Orca's G-code viewer, including
width, speed, flow, layer selection, and visible unfinished transitions. Verify
the exported inspection file through Orca's own G-code processor and visually in
the desktop application. Printing remains dependent on whole-print routing and
the constant-feed firmware work below.

- [x] Export checkpoints with Orca layer, role, height, width, and configuration metadata.
- [x] Preserve short-segment precision and verify nominal flow through Orca's parser.
- [x] Build the project's Windows GUI and inspect the exported part there.
- [ ] Complete whole-print routing and the physical-feed backend before printing.

- [x] Inspect existing planner and acceptance geometry.
- [x] Establish an isolated native build and run the existing planner tests.
- [x] Generate and evaluate whole-region, variable-width route candidates; include missing material, excess deposition, and exterior errors in diagnostics.
- [x] Preserve search state across time checkpoints and expose incomplete results in the development inspector.
- [x] Omit the narrow cross-section when required, preserving a closed route and reporting missing material.
- [x] Join layers into one continuous three-dimensional path; report ramp/connection volume separately from planar coverage.
- [x] Integrate the planner, settings, progress, and retained-candidate continuation into Orca's GUI.
- [x] Export through the experimental Klipper module with a module/version guard.
- [ ] Implement and verify constant physical feed output for Klipper, including acceleration, junctions, and layer transitions. Reject unsupported execution instead of silently relaxing constant feed.
- [x] Verify the actual exported program and geometry on the acceptance part; record runtime and omitted areas.
- [ ] Physically calibrate and print a constant-feed, variable-width coupon, followed by the acceptance part. Hardware verification requires Leo's printer.

## Findings and open risks

The legacy planner joins generated entities with straight extruding connectors and plans perimeter and infill collections separately. It neither establishes whole-print continuity nor controls physical filament feed. Its containment check defaults off. Legacy configuration and project loading must remain compatible, and ordinary slicing must remain unchanged when the mode is disabled.

The existing build cache refers to an earlier OneDrive workspace. Verification uses an isolated `build-continuous` directory with GUI disabled, tests enabled, and `ORCA_TOOLS=ON`. The prebuilt dependency exports also contain old absolute OpenCV/OCCT dependency paths. Generated `.vcxproj` references in the isolated build were corrected to this workspace; no dependency exports or global configuration were changed. A clean dependency build is required to remove that environment-specific workaround.

The physical width response of soft TPU to toolhead-speed changes has not been measured. Firmware acceleration and queue behavior must be included in the feed-rate contract. Final geometric tolerances and calibration defaults require experiments, not an assumed universal TPU profile.

## Evidence from the current implementation

The 120-second acceptance-model search evaluated all 200 layers in 120.6 seconds. All layers have connected routes, and 199 pass both the width and boundary checks. At Z = 22.1 mm, the horizontal neck narrows to about 0.13 mm. The provisional minimum bead width of 0.30 mm cannot fit within the 0.05 mm boundary tolerance there. This remains a reported defect, not a successful whole-part slice.

The preview includes layer and route-progress sliders, zoom and pan, missing/outside/repeated-deposition maps, candidate counts, and explicit incomplete status. An interactive checkpoint test retained earlier layer candidates while advancing the search. State currently survives only in the running process, and the candidate search is finite.

The Klipper experiment passes Python checks and a test using the official Klipper lookahead classes. Its extruder queue requests stay at 0.5 mm/s throughout acceleration, corners, and width changes. This does not verify step generation, actual motor or filament speed, a complete exported print, or printer compatibility. See `scripts/klipper/README.md` for the source hash and test scope.

The GUI toggle now uses the new region planner. The native acceptance run evaluated
200 layers in 120.13 seconds and joined all of them. Its command audit found
602,648 extrusion moves and no travel or retraction within the object. The planar
estimate reports 1.021% missing material, 0.0088% outside material, and 1.084%
repeated deposition. Layer 111 omits its narrow neck and passes the configured
bead limits. The layer connection search prefers a shared interior material
column to avoid drifting onto sloping outer walls.

All twelve settings are registered in Orca's process preset filter and embedded
in the editable demonstration project. Tests check that nondefault feed and bead
limits survive that filter. No printer configuration was changed, and no G-code
was sent to a printer. Physical step timing, actual acceleration deposition, and
TPU calibration remain unverified. The GUI cannot yet preview incomplete failed
routes; it reports candidate progress and preserves search state in memory.

The final GUI Slice plate test also passed the command audit: 200 layers,
602,729 extrusion moves, and no object travel or retraction. The inspected
settings panel retained the continuous process after slicing. The development
launcher starts Orca without a project argument because this checkout reloads
presets after its optional network plug-in startup prompt; open the project
after dismissing that prompt.
