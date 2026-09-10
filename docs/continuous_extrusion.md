# Continuous extrusion

Continuous extrusion connects walls and infill into one variable-width route,
including extruding layer ramps. There are no travel moves or retractions within
the object. First-layer adhesion and printer startup/end code run outside that
route. The mode emits ordinary coordinated Klipper G-code.

## Settings

Enable **Continuous extrusion** in **Process > Others > Special mode**, with
Advanced settings visible. Slice normally and inspect the native preview.

Use the ordinary **Strength** controls for **Wall loops**, **Sparse infill
density**, and top/bottom shell layer counts and thickness. At 100 percent the
planner fills the cross-section with connected variable-width concentric paths.
Sparse interiors use clipped, closed rectilinear passes. Required solid skins
retain complete concentric rings, so small or irregular regions may become
solid. The ordinary pattern selectors do not apply to this route.

Density controls the interior spacing. It is not a promise about the total
material fraction: walls, solid skins, thin features and extruding connections
also consume material. At zero percent, shells still print. Connections between
otherwise separate wall loops can still cross the interior. Holes in the model
remain holes; connectors must stay within the model's boundary tolerance.

Use Orca's **Seam position**, **Staggered inner seams**, and seam painting to set
preferred wall attachment positions. Here an attachment is where the route
leaves a wall to print the interior. The planner uses Orca's seam placement
priorities, subject to a contained connection being possible. Layer ramps prefer
wall material on sparse layers. Scarf seams and intentional seam gaps do not
apply to an uninterrupted route.

| Continuous setting | Effect |
| --- | --- |
| Preferred bead width | Width preferred by the search. |
| Minimum / maximum bead width | Permitted asymmetric limits. The minimum must be at least the layer height. |
| Continuous flow control | Automatic steady nominal flow, a volumetric or filament-feed target, or ordinary feature speeds. |
| Target volumetric flow | Requested mm3/s, capped by material, motion and cooling limits. |
| Target filament feed | Nominal incoming mm/s, converted using filament diameter. |
| Planning resolution | Geometric approximation tolerance. Smaller values cost more slicing time. |
| Boundary tolerance | Permitted bead excursion beyond the model boundary. |
| Planning time budget | Total search seconds. Increase and reslice to continue cached candidates. |
| Layer ramp length | Distance over which a layer transition rises in Z. |
| Maximum layer connection | Maximum length of an extruding connection between adjacent layers. |
| Missing / excess material penalty | Relative weights for missing target material and repeated deposition. |
| Omit unreachable material | Permit omission of disconnected or unprintably narrow material. Otherwise it is a slicing error. |

Flow, speed and cooling tuning retain the geometry cache where possible.
Density, walls, shells, seam settings and seam painting invalidate it. Search
state survives reslicing while the same object is loaded, but not application
restart. Progress reports show evaluated layers and elapsed search time. An
individual candidate can finish after the time budget.

## Compatibility and limits

The current implementation requires one object, one instance, one material and
one print region, with at least one wall. Supports, raft, spiral vase, prime tower,
draft shield and skirts above the first layer are incompatible. Some geometries
cannot form the required route under the selected bead and connection limits.
They produce a slicing error; there is no travel fallback.

The preview uses Orca's normal wall/infill roles, widths, speeds, flow and time
estimates. A slicing warning reports estimated missing material. Intentional
sparse voids are excluded from that missing-material estimate. Coverage is a
planar deposited-footprint estimate, not a simulation of molten filament or
complete 3D nozzle clearance. Check unsupported spans before printing, especially
with flexible filament or very low density.

Pressure advance, extrusion calibration, feature speeds, first-layer settings
and cooling use the ordinary exporter. Overhang slowdown and custom layer/role
commands are not applied inside the continuous route. Printer start/end and
filament startup code remain the user's responsibility.

Steady flow is a nominal target. Standard Klipper accelerates the extruder with
the toolhead, so physical filament feed varies during acceleration. No firmware
extension is installed or required. See [Klipper kinematics](https://www.klipper3d.org/Kinematics.html).
Physical TPU finish and practical flow limits require print calibration.

Older continuous-mode projects preserve their solid-fill behavior on loading.
Projects using the old `constrained_bead_planner` slicing-mode value migrate to
the separate toggle. Saved nominal filament-feed targets remain readable.
Disabling continuous extrusion restores ordinary slicing and export.

## Development checks

Core tests cover connected routes, containment, width limits, coverage accounting,
sparse interiors, solid surfaces, wall attachments, ramps and settings migration.
The FFF suite exercises native G-code, density changes and cache invalidation.

```text
libslic3r_tests "[ContinuousExtrusion],[ContinuousInfill],[ContinuousSeam],[ContinuousIntegration],[ContinuousGeneralization]"
fff_print_tests "[ContinuousExtrusion]"
python scripts/check_continuous_infill.py EXE OPTIONS_JSON OUTPUT_DIR
python scripts/check_continuous_generalization.py EXE OPTIONS_JSON OUTPUT_DIR
python scripts/check_continuous_flow.py EXE OPTIONS_JSON OUTPUT_DIR
```

`EXE` is `continuous-extrusion-inspect`, built with `ORCA_TOOLS=ON`.
`OPTIONS_JSON` contains serialized Orca configuration overrides. The native tool
writes editable 3MF projects, G-code and per-layer coverage reports. Its
`--slice-native` arguments are input model, output G-code, explicit STL coordinate
scale and options JSON. The supplied historical STL fixture uses metres; use
scale 1000 for millimetres. Generated test shapes use millimetres.
