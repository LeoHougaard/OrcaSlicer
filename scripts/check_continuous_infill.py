"""Exercise native density, shell, wall and seam controls without a printer.

Usage: python scripts/check_continuous_infill.py EXE OPTIONS_JSON OUTPUT_DIR
"""

import json
import math
from pathlib import Path
import subprocess
import sys
import time

from check_continuous_gcode import check
from check_continuous_crossings import check as check_crossings
from check_continuous_generalization import rectangle, solid, circle


def run(executable, options_path, output):
    output.mkdir(parents=True, exist_ok=True)
    options = json.loads(options_path.read_text(encoding="utf-8"))
    options.update(ce_search_time="30", wall_loops="2", top_shell_layers="3",
                   bottom_shell_layers="3", top_shell_thickness="0",
                   bottom_shell_thickness="0", seam_position="back")
    solid(output / "box.stl", rectangle(24, 20), rectangle(24, 20), 4)
    solid(output / "ring.stl", circle(12), circle(12), 4, circle(5))
    solid(output / "taper.stl", rectangle(20, 16), rectangle(16, 12, 2, 2), 4)
    cases = [(f"box-{density}", "box", {"sparse_infill_density": f"{density}%"})
             for density in (0, 15, 40, 100)]
    cases += [("ring-15", "ring", {"sparse_infill_density": "15%"}),
              ("taper-15", "taper", {"sparse_infill_density": "15%"}),
              ("box-walls", "box", {"sparse_infill_density": "15%", "wall_loops": "4"})]
    cases += [(f"box-seam-{seam}", "box", {"sparse_infill_density": "15%", "seam_position": seam})
              for seam in ("aligned", "nearest", "random")]
    results = []
    for name, shape, overrides in cases:
        config = output / (name + ".json")
        config.write_text(json.dumps(options | overrides, indent=2), encoding="utf-8")
        gcode = output / (name + ".gcode")
        started = time.monotonic()
        with (output / (name + ".log")).open("w") as log:
            process = subprocess.run([str(executable.resolve()), "--slice-native",
                                      str(output / (shape + ".stl")), str(gcode), "1", str(config)],
                                     stdout=log, stderr=log, timeout=180)
        result = {"name": name, "seconds": round(time.monotonic() - started, 2), "exit": process.returncode}
        try:
            assert process.returncode == 0, "Native slicing failed"
            result.update(check(gcode))
            result["same_height_crossings"] = check_crossings(gcode)["conflicts"]
            assert result["same_height_crossings"] == 0, "Extrusion crosses or retraces a deposited path"
            layers = json.loads(Path(str(gcode) + ".json").read_text())["layers"]
            voids = [layer["intentional_void_area"] for layer in layers]
            assert all(value < 1e-5 for value in voids[:3] + voids[-3:]), "Shells contain sparse voids"
            if name.startswith("box") and name != "box-100":
                assert max(voids[3:-3]) > 20, "Density control did not produce a sparse interior"
            result["max_intentional_void_mm2"] = max(voids)
            result["max_missing_fraction"] = max(layer["missing_area"] / layer["target_area"] for layer in layers)
            assert result["max_missing_fraction"] < .08, "Unexpectedly large missing target area"
            result["passed"] = True
        except (AssertionError, KeyError, FileNotFoundError) as error:
            result.update(passed=False, error=str(error))
        results.append(result)
        (output / "results.json").write_text(json.dumps(results, indent=2))
        print(name, "PASS" if result["passed"] else "FAIL", result.get("error", ""), flush=True)
    by_name = {result["name"]: result for result in results}
    if all(result["passed"] for result in results):
        volumes = [by_name[f"box-{density}"]["filament_mm"] for density in (0, 15, 40, 100)]
        assert all(a < b for a, b in zip(volumes, volumes[1:])), "Density does not increase deposited volume"
        assert by_name["box-walls"]["filament_mm"] > by_name["box-15"]["filament_mm"], "Wall count ignored"
    else:
        raise SystemExit("Native infill checks failed; see results.json")

    # Embedded presets need importer-owned metadata storage. Reopening a saved
    # project must also retain the evaluated process values over system presets.
    roundtrip = output / "box-roundtrip.gcode"
    no_overrides = output / "no-overrides.json"
    no_overrides.write_text("{}", encoding="utf-8")
    with (output / "box-roundtrip.log").open("w") as log:
        subprocess.run([str(executable.resolve()), "--slice-native",
                        str(output / "box-15.gcode.3mf"), str(roundtrip), "1",
                        str(no_overrides)], stdout=log, stderr=log,
                       timeout=180, check=True)
    audit = check(roundtrip)
    assert math.isclose(audit["filament_mm"], by_name["box-15"]["filament_mm"], rel_tol=1e-8), "Project round trip changed deposition"
    print("project-roundtrip PASS", flush=True)


if __name__ == "__main__":
    run(*(Path(argument) for argument in sys.argv[1:]))
