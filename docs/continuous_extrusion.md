# Continuous extrusion

The continuous region planner now runs through Orca's normal Slice and G-code
export pipeline. It fills one solid object with a variable-width extrusion route,
including gradual layer ramps. Material that cannot join that route may be left
unfilled. The mode never substitutes travel to reach it.

This is experimental printing software. Physical filament motion and TPU bead
response still need calibration on the intended printer.

## Use the native mode

Open `build-continuous/Open Continuous Mode.lnk` on the development Windows
machine. This launches the separate build in `build-continuous/src/Production`.
After startup, use **File > Open Project** to open
`build-continuous/Continuous Extrusion - Stock Klipper.3mf`. Its settings
are stored separately in `build-continuous/continuous-production-userdata`.

If this build asks about the optional Bambu network plug-in, choose **Skip for
Now**, then open the project. This checkout reloads presets after that startup
dialog, which can discard a project preset opened too early. The launcher opens
the application first to avoid that ordering problem.

In **Process > Others > Special mode**, enable **Continuous extrusion** with
Advanced settings visible. Change the continuous settings and press **Slice
plate**. The model remains editable; this is not an imported preview file.
Select a Klipper printer profile matching your hardware before preparing a print.
The Stock Klipper project retains the saved printer and filament settings.
Select a tuned TPU filament profile before testing TPU.

The first version requires one object, one instance, one material, and no
supports, raft, or prime tower. It generates its own solid interior and wall
routes, regardless of conventional infill percentage, wall count, or infill
pattern. A first-layer skirt and brim print before the continuous object route. Cooling,
flow ratios, feature speeds, and acceleration use Orca's normal exporter.
Scarf, overhang slowdown, and custom layer/role code are not applied to this
route. Supports, multiple-layer skirts, and draft shields are incompatible.
Printer start/end code and initial temperature handling still use Orca's normal
export pipeline. Check any custom startup priming separately.

| Setting | Effect |
| --- | --- |
| Preferred bead width | Preferred width when comparing fill candidates. |
| Minimum / maximum bead width | Permitted asymmetric width limits. Minimum must be at least layer height. |
| Continuous flow control | Steady automatic flow, volumetric target, filament target, or normal process speeds. |
| Target volumetric flow | Requested mm3/s, subject to material, motion, and cooling limits. |
| Target filament feed | Nominal incoming mm/s converted using filament diameter; retained for older projects. |
| Planning resolution | Geometric tolerance; smaller values retain more detail and cost more time. |
| Boundary tolerance | Allowed bead excursion beyond the model boundary. |
| Planning time budget | Total search time. Increase and slice again to continue retained candidates. |
| Layer ramp length | Distance along the layer start over which Z rises. |
| Maximum layer connection | Limit on an extruding connector between adjacent layers. |
| Missing / excess material penalty | Relative scoring weights for gaps and repeated deposition. |
| Omit unreachable material | Leave out unconnected strokes, islands, or narrow features; disabling it makes these a slicing error. |

Layer height, nozzle diameter, and filament diameter come from the ordinary
process, printer, and filament settings. Defaults are 0.42 mm preferred width,
0.30 to 0.80 mm limits, and automatic flow selection. Older saved projects keep
their previous nominal feed target; select automatic or enter a new target to
change it. Flow tuning retains the cached geometry and candidate search.

The search reports candidate progress. It retains candidates while the same
object remains loaded. Increasing its time budget continues the search; changing
geometry or planning settings restarts it. A candidate already running can finish
after the deadline. If a layer has no valid route, export stops with its layer
number. The current GUI does not display partial failed routes as a printable
preview. Search state does not survive closing the application.

## Omission and layer connections

The planner first tries the complete region. When a bead cannot fit and omission
is enabled, it removes features narrower than the minimum bead and plans the
remaining region. Unconnected strokes are omitted. If the region contains
separate islands, it currently chooses the largest one. All missing material is
measured against the original region and remains in the report.

The supplied part's layer 111 has a neck around 0.13 mm wide. The new test requires
that layer to produce a closed, contained route with 0.30 to 0.80 mm beads while
reporting the omitted material. It no longer accepts a boundary violation there.

Closed layer routes prefer an attachment with high boundary clearance in material
shared by all layers. When no shared column exists, they use the overlap with
the next layer. Each attachment must also leave room to reach the following
layer. The search considers projections of both the interior target and the
actual previous endpoint. An added connector must fit inside both adjacent
layers' boundary tolerances. If none
fits, slicing stops. The exporter checks full XYZ endpoints and emits positive
extrusion through all object moves. There is no layer travel or retraction
fallback.

The scale and independent-shape verification is recorded in
[continuous_generalization.md](continuous_generalization.md). The corrected
connection search preserves the selected planar fill paths. Small square tips
still have appreciable estimated gaps; that fill limitation remains open.

The planar coverage report measures missing material, material outside the part,
and repeated deposition. Ramp and connection volume is reported separately.
The footprint checks do not prove nozzle clearance against every triangle of a
sloped surface, nor do they model molten TPU or acceleration deposition.

## Klipper output

The mode emits standard coordinated Klipper G-code. No extension or guard macro
is required. The former extension is retained only as a historical experiment in
`scripts/klipper`; new exports do not activate it. Pressure advance follows the
selected filament settings.

Steady-flow automatic selection finds a common nominal flow from the layer's
bead sizes and feature speeds. A volumetric or filament target lets you request
a different rate. Normal process speeds uses the usual wall and infill speeds.
All choices respect first-layer, filament volumetric, printer-axis, and cooling
limits. A 35 mm3/s hotend rating is a ceiling, not a guaranteed printing rate:
a 0.42 by 0.20 mm bead needs about 464 mm/s to use that capacity.

Klipper accelerates the extruder with the toolhead. Constant physical feed
through arbitrary corners cannot be enabled by a slicer-only toggle. Coordinated
motion preserves E volume when acceleration lengthens a move, avoiding the
additional deposition produced by the former independent-feed experiment.
See [Klipper kinematics](https://www.klipper3d.org/Kinematics.html).

Orca's Width, Speed, and Flow views show nominal geometry and commanded flow.
Actual Speed, Actual Flow, and print time estimate coordinated acceleration.
These are estimates, not physical TPU measurements. Software verification and
physical print tuning are separate; no printer configuration is changed here.

## Development verification

```powershell
& build-continuous/tests/libslic3r/Release/libslic3r_tests.exe '[ContinuousGeneralization],[ContinuousExtrusion],[ConstrainedBeadPlanner],[Arachne]' --order rand --rng-seed 12345 --warn NoAssertions
python scripts/check_continuous_flow.py build-continuous/src/Release/continuous-extrusion-inspect.exe build-continuous/production/options.json build-continuous/production/flow-checks
& build-continuous/src/Release/continuous-extrusion-inspect.exe --slice-native tests/data/continuous_extrusion/hardest_part_metres.stl build-continuous/Hardest-Part-Continuous-Native.gcode 1000 build-continuous/native-options.json
python scripts/check_continuous_gcode.py build-continuous/Hardest-Part-Continuous-Native.gcode
```

`--slice-native` exercises Print application, validation, slicing, saved 3MF
settings, and normal G-code export. Its arguments are STL, output G-code, explicit
coordinate scale, and a JSON object of serialized Orca configuration overrides.
The fixture is in metres and needs scale 1000. The command also saves an editable
3MF and a per-layer JSON report.

Geometry baseline results are recorded in
[continuous_generalization.md](continuous_generalization.md). They used the
former experimental exporter. Stock Klipper integration and its acceptance
checks are tracked in [continuous_production_plan.md](continuous_production_plan.md).
Linux, macOS, and physical printing have not been verified for this update.

The older HTML checkpoint inspector and `scripts/continuous_extrusion_preview.py`
remain available for geometry debugging. Their G-code files are inspection-only
and do not use the native layer ramps or firmware commands.

## Compatibility

The persisted toggle remains `continuous_extrusion`. Older
`slicing_mode = constrained_bead_planner` projects still enable the mode. The new
`ce_` settings have additive defaults and persist in projects and profiles. Old
`cbp_` settings remain readable for compatibility, but no longer control the new
mode and are hidden from its settings panel. Disabling continuous extrusion
leaves ordinary slicing and export behavior unchanged.
