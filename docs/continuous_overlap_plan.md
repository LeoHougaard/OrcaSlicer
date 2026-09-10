# Continuous extrusion overlap correction

Outcome: route infill and solid surfaces without crossing or retracing deposited
paths. Preserve continuous extrusion, target coverage, seam preferences and
general geometry support. A rejected slice or omitted printable fill is not a fix.

1. Measure crossings in current native exports, including solid skins and sparse
   interiors. Add an independent geometric regression check.
2. Correct connection planning to choose clear adjacent routes and preserve
   material coverage. Check layer ramps separately from planar paths.
3. Run matched geometry, native density/seam/flow and supplied-model checks.
   Inspect before/after paths. Build the updated GUI and upstream branch, and
   push verified changes to the fork.

Baseline: working branch 5556fb2a, upstream branch 3dccb8ce. Existing coverage
checks penalize overlap area but do not prohibit crossing paths. Loop connectors
currently check only model containment, with no intervening extrusion check.
User-specific model/layer details have been requested; existing fixtures allow
independent investigation.

## Iteration evidence

- Native baseline box15 has 17 crossings/retraces; ring15 has 308.
- Visibility checks during loop joining remove all same-layer crossings across
  the native matrix. Coverage checks and all density cases still pass.
- A spatial midpoint index did not improve the ring's search time, about 31
  seconds versus 4 at baseline. Do not promote this index on performance claims.
- User recovery from PID15756 contains Part Studio 2 - Part 1, 15% infill, two
  walls, aligned seam, bead range0.4-0.6. Copied recovery and preview G-code into
  build-continuous/overlap; the user's live session remains untouched.
- User baseline has 893 events, including 95 on layer 15. After contour visibility
  checks there are 22 layer-transition events and no same-layer crossings.
- Layer transitions add a flat old-height connector and prefer a global reference
  over the nearest point. Correct the entry and include the connector in the rise,
  reaching full new-layer height before a transverse old-path crossing.

- Nearest-only layer entry initially failed six moving-section test variants.
  The old overlap-of-sections containment also rejected a sparse taper even
  though an ascending wall connection fits the union of adjacent sections.
  Restrict entries to visible next-layer edges; rise across the incoming link
  within the adjacent-section envelope and finish the rise by its end.
- Cut intervals now follow contour arc length across small tessellation edges.
  Previously a requested bead-wide cut shrank to 90% of a single tiny edge.
- Stable-entry/arc-cut core run passes 289269 assertions in five cases, but the
  sparse taper still failed under the old intersection envelope. Recheck after
  the wall-ramp change.
- Same-height audit now uses 1e-6 mm equality, with a regression showing that a
  rising new layer is not a flat retrace. The original 5 um neighborhood included
  intentional stacking at a different Z. Baseline and candidate are remeasured
  with the same corrected audit.
- Sort pending contours by proximity to the current route. This avoids retrying
  an enclosed hole wall before joining the intervening rings. Measure it before
  making any performance claim.

## Candidate for delivery

- Adjacent-first joining restores ring slicing to 4.04 seconds, all nine
  candidates on all layers. The user's copied project slices in 8.4 seconds.
- Full native density/seam matrix and user model have zero same-height crossing
  or retracing events. The user model also retains zero object travel/retraction.
- Core moving-section, seam and explicit crossing checks pass 289158 assertions
  in five cases. The sparse taper succeeds with ascending wall connections.
- Final checks add an analytic moving-wall ramp test, the native crossing audit
  to every infill case, and full reference prints at100/125 percent scale.
- Matched geometry source is being built in both working/upstream branches.

## Final verification

- Working Windows app builds; core passes 290807 assertions in 26 cases.
- Current-upstream Windows app builds; core passes296909 assertions in 30 cases.
- Both FFF suites pass57160 assertions in 4 cases, including ordinary export
  after disabling continuous mode. Both native flow matrices pass10 cases.
- Native density/seam matrix passes10 cases and generated-project round trip.
  Every case has zero same-height crossings/retraces.
- Matched user baseline has868 same-height conflicts,95 on layer 15. Corrected
  native and actual GUI output both have zero across65 layers, with no object
  travel/retraction. Estimated missing area0.94%, versus0.92% in the old GUI.
- Reference model at100/125 percent solid:200/250 layers,132/137 seconds, zero
  crossings/retraces and no object travel. Concurrent builds/tests ran during
  these slices, so timings are not isolated benchmarks. Full sparse reference
  also slices successfully with zero same-height conflicts.
- Actual GUI slicing used a separate build/profile directory and the copied
  recovery project. The old session remains available with its unsaved edits.
  Audited GUI G-code and inspected a plotted before/after layer 15 comparison.
  Background PrintWindow does not capture the new OpenGL viewport, so that
  screenshot is not used as evidence of the rendered paths.
- Updated shortcut: build-continuous/Open Continuous Infill.lnk. Review window
  title: Continuous Infill - Corrected paths. No print was sent.
- Physical TPU finish remains untested here. Centerline crossing checks do not
  claim zero bead-footprint overlap at ordinary adjacent joins/corners.

Final entry-edge correction is still under verification before committing.

## Final audit correction

The independent audit skipped consecutive segments, which hid one immediate
backtrack on user layer 28. Added an immediate-backtrack regression and removed
that skip. Existing reference100/125 percent and all native matrix cases still
have zero events; the user model has one.

Entry edges must participate in collinear-overlap checks, and exact endpoints
provide alternatives when projecting into an edge would retrace it. Ranking
all endpoint alternatives alongside projections changed earlier entries and
failed user layer 19. Keep normal projected entries first and use endpoint
alternatives when needed; verify this before rebuilding the delivery app.

The entry-edge check needs constructive reuse, not just rejection. When the
incoming ramp lies along the selected edge, traverse the rest of the cycle
away from that interval and end at its other end. Preserve the original edge's
width/flow for the reused portion. This removes the duplicate pass while
retaining the material path. The user model again slices all 65 layers and now
has zero events with immediate backtracking included. All10 native infill
cases and the project round trip pass with the stricter audit.

## Promotion

Final constructive entry reuse passes the complete working/upstream core suites
with 290807/296909 assertions, plus both FFF suites with 57160 assertions. Both
native infill and flow matrices pass; all infill cases include the stricter
crossing/retrace check. The current-source 200-layer reference rerun completes
in 131 seconds and has zero events.

The final GUI build was replaced and restarted in the separate review profile.
Its actual newly sliced 65-layer output has zero crossings/retraces, including
immediate backtracking, and zero object travel/retraction. The comparison image
now uses this final GUI output. The launcher targets this exact rebuilt DLL.

Promote the fix and push both branches to the fork. Remaining physical-print
and bead-footprint limitations are as stated above.
