# Continuous extrusion acceptance geometry

`hardest_part_metres.stl` is Leo's supplied "Hardest Part For Slicer Mode.stl".
Its coordinates are in metres, consistent with the accompanying STEP file.
Scale by 1000 when loading into a millimetre-based test. Keep the supplied
orientation: the Z extent is 40 mm after scaling.

The shape is approximately 93.652 x 61.938 x 40 mm. Its middle cross-sections
contain an enclosed hole; the lower and upper cross-sections open that hole to
the exterior. The full model is the development acceptance case. The selected
cross-section tests protect geometry and continuity regressions and do not
establish whole-print validity or physical TPU print quality.
