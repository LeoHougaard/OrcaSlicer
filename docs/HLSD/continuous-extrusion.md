# Continuous extrusion

## Purpose and scope

An opt-in FFF mode joins walls and interior extrusion into a single object route.
The object contains no travel moves or retractions, including between layers.
The initial version supports one object, instance, material and region with stock
Klipper output. Unsupported combinations fail validation before export.

The normal slicing pipeline and default profiles retain their existing behavior
when `continuous_extrusion` is disabled. No firmware module or new dependency is
required. User settings and operational limits are in
[the mode guide](../continuous_extrusion.md).

## Geometry

`ContinuousRegionPlanner` evaluates closed variable-width Arachne contour paths.
Its opt-in beading preference leaves the ordinary wall generator unchanged.
The configured wall count retains the outer rings. Required solid surfaces from
Orca's prepared fill surfaces retain complete rings, conservatively expanded to
cover differences between ordinary wall widths and continuous bead widths.

For sparse interiors, clipped closed rectilinear strips provide paired passes.
Pass spacing follows bead spacing divided by requested density. Joining cycles
removes bead-spaced intervals measured along the contour and replaces them with
two contained extruding links. Links avoid retained and pending contours, and
nearby contours join first to avoid spending the budget on occluded hole walls.
This avoids traversing a closed loop twice just to enter and leave it. Unreachable
material is omitted only when the explicit omission setting permits it.

Orca's `SeamPlacer` provides preferred outer and inner wall attachments, including
painted preferences. Join selection preserves long outer-wall runs and avoids
bypassing inner-seam preferences by attaching to an existing connector. Geometric
containment can override a requested location.

Layer routes rotate to visible attachment points and rise during incoming
connections. Departures stay within reach of actual next-layer deposition.
A ramp follows moving walls within the combined adjacent-section envelope and
reaches full new-layer height before crossing completed strokes. When an incoming
connection runs along its entry edge, that interval supplies part of the layer:
the remaining cycle opens there and traverses away from it without a second pass.
The reused edge retains its bead width and flow. The planner never inserts travel
as a fallback.

## Evaluation and lifetime

Coverage uses planar deposited footprints to estimate missing, outside and repeated
material. Intentional sparse voids are measured separately and excluded from the
missing-material objective. Connection volume is reported separately. This is not
a molten-filament or complete 3D nozzle-clearance simulation.

`ContinuousPrintJob` belongs to a `PrintObject`. It retains candidate searches for
reslicing with a larger time budget. Geometry, walls, density, shells and seam
changes invalidate it. Seam-paint changes explicitly invalidate it as well.
Flow-only tuning retains the geometry. Cancellation uses the ordinary slicing
callback; parallel work owns distinct per-layer planners. Progress reports include
evaluated layers and elapsed search time. A candidate may finish after its budget.

## Export and compatibility

The exporter uses normal extrusion roles and the existing G-code writer, cooling
processor and pressure-advance output. Nominal flow targets obey material, motion
and cooling limits. Ordinary process speeds are also selectable. Standard firmware
still varies physical extrusion speed during acceleration. Object markers allow
automated auditing of continuity without including startup, adhesion or end code.

The hidden `ce_settings_version` distinguishes older solid-only projects from
projects that intentionally select sparse density. Legacy continuous slicing-mode
values migrate to the separate toggle and regular mesh slicing. Retired prototype
`cbp_` options are ignored on loading. Other profiles keep their defaults.

## Verification

Core tests cover geometry, widths, coverage, holes, scales, seams, shoulders and
settings migration. FFF tests exercise native export and cache invalidation.
Native inspection scripts save editable projects and audit emitted G-code across
density, shape and flow settings. GUI verification must include editing, reslicing
and saving a project; imported G-code alone does not verify the feature.

Physical TPU finish, supported spans at low density and practical flow limits
remain printer/material calibration work.
