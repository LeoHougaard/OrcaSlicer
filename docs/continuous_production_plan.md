# Continuous mode integration and tuning

Deliver an Orca workflow that uses the selected printer and filament settings,
shows understandable flow controls, applies flow calibration, and produces
verifiable motion without editing exported G-code. Preserve the geometry and
connection improvements and existing profiles. Physical print validation remains
Leo's testing task; software checks must not be presented as that validation.

Leo chose unmodified Klipper. Use coordinated extrusion to preserve deposited
volume during acceleration. Offer steady nominal flow targets and normal process
speeds; neither promises physically constant incoming feed through acceleration.

## Evidence and acceptance

The baseline is preserved in `build-continuous/production-baseline` and the
previous Generalized application. Its default 0.5 mm/s input feed produces
1.20264 mm3/s with 1.75 mm filament. The custom exporter ignores filament flow
ratio and ordinary process speed limits; the extension feeds additional material
when firmware lengthens a move. Nominal-flow checks do not detect that problem.

Use the two real parts, original scales 50/75/100/125%, and independent solids
from `scripts/check_continuous_generalization.py`. Verify no object travel or
retraction, flow ratios, printer/material limits, preview estimates, settings
round trips and legacy profiles, and ordinary slicing with this mode disabled.
Add acceleration/corner checks appropriate to the agreed firmware contract.
Target roughly two minutes for the midsize examples, with useful progress.

## Work

- [x] Resolve the firmware contract: standard Klipper, coordinated extrusion.
- [x] Replace the unexplained low default with explicit automatic/manual flow
  controls, preserving existing saved feed settings through migration.
- [x] Apply filament flow calibration and selected motion/material limits.
- [x] Integrate motion output, cooling, diagnostics, and efficient reslicing.
- [x] Verify full native exports and the real GUI in an isolated updated build.

No printer commands, firmware installation, or physical motion are authorized.
Preserve the open GUI's unsaved project. Changes are local to the source,
verification files, documentation, and build/data directories.

## Verification, 2026-09-08

The final native matrix passed all nine shapes/scales with standard Klipper
G-code and zero object travel or retraction. The original at 100% and 125% took
127.35 and 129.77 seconds, including the bounded search and export. Geometry
coverage remains consistent with the preserved baseline. The current user part
also passed the command audit: 65 layers, 45,152 extrusion moves, 100 rising
moves, and no object travel or retraction. Its saved printer settings produce
an estimated 14m 17s print; this is an Orca estimate, not a measured print.

Ten focused native flow cases passed: 35 mm3/s reached when permitted; exact
0.9 flow calibration; 4 mm3/s material cap; XYZ speed limits; pressure advance;
cooling; normal and automatic speeds; adhesion; and absolute-E profile
compatibility. Flow-only reslicing retained the geometry cache and search time.
The target changed to 2.5 mm3/s without rerunning the planner.

The C++ geometry/configuration checks passed 291,084 assertions in 27 cases.
Normal fill, flow and export checks passed 68 assertions in four cases, repeated
after the scoped cooling-filter change. Six offline cases using official
Klipper Move, LookAheadQueue and PrinterExtruder code preserved requested E
volume through acceleration and corners at targets of 1.2, 5 and 35 mm3/s.
Pressure advance was zero in that motion test; no MCU or physical printer ran.

The test-project writer now constructs enum-vector options through their
configuration definitions. A saved-project round trip preserves the user's
50% and 25% overhang-fan thresholds and the new flow-control selection.

Evidence is in `build-continuous/production`: `final-generalization/results.json`,
`final-flow/results.json`, `current-final-audit.json`, `core-tests.log`,
`normal-final-tests.log`, `klipper-motion.json` and `iteration-notes.json`.
The stock Klipper application is `build-continuous/src/Production`.

Known limits remain: one object/instance/material, no support/raft/prime tower,
100% fill planning, estimated gaps in small corners/tips, and no overhang
slowdown or custom layer/role commands within the route. Geometry coverage is
planar and does not establish physical TPU finish or complete 3D nozzle clearance.

The final desktop application opened `Continuous Extrusion - Stock Klipper.3mf`,
showed the editable continuous controls, and sliced through the GUI. Its exported
G-code passed the same audit as the native export, including matching object
volume and 45,152 moves. The actual OpenGL preview was inspected on screen;
`Orca-Preview.png` records the model, controls, skirt and 14m 17s estimate.
Travel/retraction entries in that preview are startup/adhesion moves outside the
continuous object. The original unsaved Orca window was left open.

`build-continuous/Open Continuous Mode.lnk` now launches the Production build
with its separate `continuous-production-userdata`. Open the supplied Stock
Klipper project after startup. The current project retains the user's saved PLA
profile; choose a tuned TPU profile for TPU testing. No physical print has been
sent or validated by this work.
