# Continuous Slicing Mode (Experimental)

Continuous slicing (`spiral_hybrid_non_crossing`) replaces every eligible non-empty layer with one closed, continuously extruded, variable-width path. It is independent of classic Spiral vase mode.

This mode is fail-closed. A structurally unsafe layer aborts processing and never falls back to ordinary slicing. A path that is structurally emittable but misses a geometric quality target remains available for research preview and diagnosis, but blocks printer-ready export.

This is experimental software, not physical-printer certification. The slicer validates the geometry and serialized commands it can model, but cannot prove a machine's homing behavior, collision clearances, leveling state, material response, or hardware condition.

## Eligibility

A model is geometrically eligible when:

- every non-empty layer has exactly one connected island; holes are allowed;
- layer zero begins at the build plate; and
- every later layer has positive-area geometric overlap with the layer immediately below.

Cross-sections may translate, rotate, taper, grow, shrink, or change outline. Identical layers and vertical prisms are not required. The current whole-job contract additionally requires one object, one instance, one region/material/extruder, tool 0, by-layer printing, relative E, and either Marlin 2 or Klipper semantics.

Support, raft, skirt, draft shield, brim, prime tower, ironing, per-layer custom G-code, and calibration modes are disabled or rejected because they would add extrusion strokes or uncontrolled state changes. Cached or imported Continuous Fermat entities are rejected because they cannot be recertified against the current slices.

## Enabling and line width

```ini
spiral_mode = 0
spiral_hybrid_non_crossing = 1
```

`continuous_max_line_width` controls how far the planner may widen individual segments to close residual gaps. It defaults to 150% of nozzle diameter and is hard-clamped to 200%. Width is derived from the rounded-rectangle bead cross-section, not by multiplying nominal width directly.

Wider segments retain the same volumetric deposition by slowing feed rate by the extrusion multiplier. Narrower segments are not accelerated beyond nominal speed. All adjusted candidates are rechecked against the same fixed metrics.

## Mandatory layer contract

Every printer-ready planar layer cycle must have:

- one exactly closed path with matching finite per-segment metadata;
- physical width within the configured and two-nozzle limits;
- a non-empty swept footprint;
- zero model-relative containment violations;
- zero nonlocal crossings;
- zero physically relevant turns sharper than 135 degrees;
- exact swept-bead coverage of at least 0.980;
- outside-model swept area of at most 0.020;
- deposited material from 0.980 through 1.020 of solid-layer material; and
- global redeposition of at most 0.040 of printable area.

Coarse sampled coverage, pairwise spacing, and bead-overlap counts remain diagnostics. Exact unioned footprint, material, and global redeposition are authoritative. A final zero-area apex may be omitted only when every preceding layer emitted one protected stroke.

Failed geometry is categorized at a 2.5-nominal-line-width threshold. Half-threshold erosion identifies globally thin sections, local necks, and hole webs. The category explains a failure; it never relaxes a limit or pre-rejects a thin layer that generated a satisfactory path.

## Generated path and layer transitions

Each nondegenerate layer contains one marked section. The planner certifies a
closed XY cycle, but after the first layer the emitter may rotate that cycle
and prepend one bounded connector from the preceding cycle:

```gcode
;_CONTINUOUS_FERMAT_BEGIN
...
;_CONTINUOUS_FERMAT_END
```

A failed research-preview layer is marked explicitly:

```gcode
;_CONTINUOUS_FERMAT_VALIDATION_WARNING layer=42 ... exact_coverage=0.91 ...
```

The first layer is flat. Every later section starts at the preceding section's
exact XYZ endpoint, rotates its cyclic path to the nearest seam, and raises Z
while extruding along the opening path. The ramp is at least 20 horizontal
units per unit of Z and at least ten nozzle diameters long. If adjacent cycles
do not meet exactly, the connector is limited to 2.5 bead widths, must stay in
the union of the adjacent model cross-sections, and may place at most 2% of its
swept footprint outside that union.

The serialized ramp preserves the same limit after rounding: each 0.001 mm Z
step is assigned to a move with at least 0.020 mm of serialized XY, and all Z
steps form one contiguous opening run. Consecutive vertices separated by at
most serialized XY resolution are merged before output. Their planned material
is carried into the merged chord, which must still pass the physical-width and
complete output checks.

There is no motion, retraction, wipe, Z-hop, or pressure restart between marked
sections. Segment extrusion and the volumetric feed cap use true XYZ distance,
so the slope retains the same bead rate as planar motion. Serialized material,
including a connector and the small extra 3D ramp length, must still remain in
the mandatory 0.980 through 1.020 layer range.

This follows the same geometric principle as Orca's [scarf joint
seam](https://github.com/OrcaSlicer/OrcaSlicer/wiki/quality_settings_seam):
spread the height change along a loop while retaining 100% flow. The stricter
continuous-mode version joins successive layer cycles and does not overlap a
second perimeter at the seam.

Graph-theoretic continuous-path work can make every vertex even by duplicating
or offsetting edges; see [Continuous Toolpath Planning in Additive
Manufacturing](https://arxiv.org/abs/1908.07452). Literal forward/backward
retracing is not used here because it deposits twice on the same centerline and
would directly increase the mode's redeposition failures. The existing paired
Fermat/contour branches are the physically useful version: the return edge is
spatially separated by approximately one bead spacing.

## Printer-ready export

Printer-ready export requires every layer to pass structural, geometric-quality, configuration, machine-envelope, and serialized G-code checks. Warning-marked paths are preview-only. Current export also blocks cached/imported Continuous Fermat G-code and warning-marked artifacts.

The emitter checks transformed and quantized XY, the complete variable-width footprint against the configured bed polygon, positive bounded Z, safely merged sub-resolution segments, quantized material from 0.980 through 1.020, and a downward-rounded volumetric feed cap. A finite serialized material deviation is a hard failure, not an advisory.

Profile start/end and layer-change macros are omitted in favor of deterministic firmware-specific state. Marlin 2 normalization includes tool selection, relative E, unity overrides, disabled volumetric E, linear advance, and power-loss recovery, all-axis homing, and a cooling-aware nozzle wait. Klipper uses native relative-E, unity override, zero-pressure-advance, homing, bed-state, and heater-wait commands. Shutdown does not park in XY.

## Independent G-code audit

Run the standalone auditor with exact expected counts:

```powershell
python tools\continuous_fermat\validate_gcode.py path\to\model.gcode `
    --expected-layers 120 --expected-sections 120 --firmware marlin2
```

The auditor rejects geometric-certification warning markers and checks marker/layer accounting, modal state, finite coordinates, positive feed, exact inter-section XYZ continuity, no motion between sections, a monotonic positive-E opening scarf no steeper than 20:1, cyclic XY closure, and a restricted postamble. It does not recompute the C++ swept-footprint or material calculations.

## Evidence and limitations

The deterministic 100-shape procedural corpus is structurally emittable 100/100 and currently meets the complete quality contract 98/100. The sharp/hole corpus meets the complete contract 8/8, including the two-hole cut-domain regression. A tapering pyramid remains deliberately blocked because serialized material falls outside the mandatory range.

The 2026-08-19 fixed internet benchmark is structurally emittable on 2,000/2,007 layers, but only 1,564/2,007 layers and 9/20 models meet every quality target. Three passing models, including a 200-layer changing section and a one-hole model, also pass printer-ready export and the independent G-code auditor. Continuous slicing is therefore improved but is not promoted as safe for printing. See the [2026-08-19 model-corpus evaluation](continuous-fermat-model-corpus-2026-08-19.md) for exact artifacts, commands, categories, and blockers.

Eligibility alone cannot guarantee physical printability: features may be narrower than a printable bead and adjacent layers may overlap by an arbitrarily small area. The rounded-rectangle material model does not simulate pressure, acceleration, cooling, die swell, adhesion, or physical collision clearance.

Use classic Spiral vase with Continuous slicing disabled when a conventional hollow single-wall vase is required.
