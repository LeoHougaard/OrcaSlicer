# Continuous Slicing Development Audit

## Current status

Continuous slicing is fail-closed but is not yet certified safe for printing. Structurally unsafe layers abort processing; they do not retain OrcaSlicer's ordinary paths. Structurally emittable paths that miss coverage, outside-area, material, or redeposition limits are retained only for research preview and diagnosis. Printer-ready export, cached-artifact export, upload paths, and the independent G-code auditor reject failed-certification markers.

The 2026-08-19 fixed 20-model evaluation covers 2,007 layers from real Thingi10K models. It improved full-quality results from 1,512 to 1,564 layers and fully passing models from 8 to 9 without regressing a baseline pass or changing a quality threshold. Three passing models also completed printer-ready export and independent scarf-G-code auditing. The mandatory gate still fails because 443 layers miss fixed targets. See [Continuous Fermat model-corpus evaluation, 2026-08-19](continuous-fermat-model-corpus-2026-08-19.md).

## Eligibility and acceptance

For every non-empty model layer:

1. there is exactly one connected island; holes do not create additional islands;
2. layer zero starts at the build plate; and
3. each later layer has positive-area overlap with the immediately lower layer.

Eligibility is broader than certification. An eligible model is printer-ready only when every layer meets the complete fixed structural, footprint, material, machine-envelope, and serialized-output contract.

Mandatory geometry targets are:

- exact closure and matching finite segment metadata;
- physical width within the continuous-flow floor and configured/two-nozzle ceiling;
- zero containment violations, crossings, and unsafe turnbacks;
- exact footprint coverage at least 0.980;
- outside swept area at most 0.020;
- deposited material from 0.980 through 1.020; and
- global redeposition at most 0.040.

Sampled coverage, pairwise spacing, and pairwise bead-overlap counts remain diagnostic. None relax an authoritative exact metric.

## Safety architecture

- Normal toolpaths are cleared only after a final Continuous Fermat path is structurally emittable.
- Hole-removal or connector failure throws an explicit slicing error; there is no ordinary-slicing fallback.
- A cut-open multiply-connected planning domain uses the topology-preserving contour-tree router. This removed the observed multi-hole legacy-chain crossings without a model-specific exception.
- Failed-quality paths retain their reason on the protected entity so preview can explain the failure.
- Printer-ready export scans every protected entity and rejects any failed-quality marker before opening an output file.
- Serialized material outside 0.980–1.020 is a hard exception, including in research preview.
- Cached/imported Continuous Fermat sections and all warning-marked artifacts are blocked unless the explicit internal research-preview purpose is used; warning-marked artifacts remain blocked even when the mode is later disabled.
- Protected paths are excluded from later generic simplification, clipping, arc fitting, flow boosts, small-area compensation, overhang resampling, and resonance-speed changes.

No model index, filename, shape name, or fixture-specific exception exists in the planner.

## Serialized-output contract

The strict emitter:

- establishes deterministic Marlin 2 or Klipper absolute-XYZ/relative-E state, unity overrides, disabled pressure advance, homing, and heater-ready state;
- emits exactly one marked cyclic extrusion section per nondegenerate layer;
- rotates each later cycle to the previous endpoint and raises Z through a continuously extruding opening scarf, with no motion between sections;
- merges only consecutive points at or below serialized XY resolution, preserves their planned material in the resulting chord, and rechecks the physical-width limit;
- assigns each serialized 0.001 mm Z step to at least 0.020 mm of XY in one contiguous opening ramp, so every emitted scarf move is at least 20:1;
- requires at least a 20:1 scarf run, bounds a changing-cross-section connector to 2.5 bead widths, and contains it in the union of the adjacent model cross-sections;
- suppresses retraction, wipe, Z-hop, object cancellation, power-loss recovery, pressure equalization, firmware-limit output, and auxiliary extrusion features;
- checks transformed/quantized XY, complete bead-footprint containment in the bed polygon, bounded positive Z, quantized-E material, and a downward-rounded volumetric feed cap; and
- uses a no-XY shutdown with at most one bounded final Z lift.

The independent Python auditor rejects failed-certification markers and adversarial modal tricks, malformed markers, arcs, non-finite values, unmarked extrusion, retraction, invalid transitions, and unsafe postambles. It does not duplicate the C++ swept-footprint calculation.

## Current measured evidence

- Non-hidden production geometry suite: 152 assertions in 17 test cases, passed.
- Hidden procedural eligibility corpus: 100/100 structurally emittable and 98/100 meet the complete quality contract.
- Hidden sharp/hole corpus: 8/8 complete-quality pass; the prior two-hole 58-crossing/6-turnback result is now 0/0.
- Continuous print/G-code suite: 66,928 assertions in 27 passing test cases; one environment-driven external-model test skipped in the aggregate run and passed separately on three models.
- Standalone G-code auditor: 27 tests, passed; corpus construction geometry: 3 tests, passed.
- Current safe-prism artifact: 3 sections, 3 layers, 3,513 extrusion moves, passed the independent auditor and visual inspection.
- Opening-scarf regressions: the safe prism and a ten-layer translating/shrinking cross-section pass with positive-E XYZ ramps and no inter-section motion.
- Fixed internet benchmark: 2,000/2,007 emittable, 1,564/2,007 full-quality, and 9/20 models fully certified; mandatory gate failed.
- Printer-ready internet artifacts: 6-layer solid, 21-layer one-hole, and 200-layer changing-section models passed independent section, material, extrusion-continuity, and serialized 20:1 scarf checks.

## Remaining limitations and blockers

- The fixed 20-model benchmark still has 375 redeposition, 56 exact-coverage, four material, four open-path, three crossing, and one outside/containment failure. Fixed limits were not changed.
- Only three fully passing internet models have completed printer-ready G-code auditing. The other six planar passes and future planner improvements still need matched full-output checks.
- The procedural 100-shape corpus still has two combined redeposition/coverage failures, despite 100/100 structural emittability.
- Corpus elapsed budgets are checked before and after each in-process generation/validation call. They fail closed, but cannot preempt one pathological C++ call; suspected deadlocks still require an outer process watchdog.
- The full 1,000-model corpus was not run after the fixed 20-model promotion set still failed. A larger sample cannot make the current mandatory benchmark pass.
- The configured printable polygon is an extrusion envelope, not a complete toolhead collision model. Homing, clips, probes, leveling, and hardware condition remain machine-dependent.
- The rounded-rectangle bead model does not simulate pressure advance, acceleration corner effects, die swell, cooling, adhesion, or extrusion force.

No commit or push is permitted while these mandatory gates fail.
