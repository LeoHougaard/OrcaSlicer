"""Bounded offline CLI regression checks.

Usage: python scripts/check_native_input_validation.py EXE NEW_OUTPUT_DIR [--numeric-only]
Uses generated millimetre meshes and the repository's offline test profile.
On Windows, put the existing Release runtime directory on PATH for DLL loading.
The output directory must be writable, including its orcaslicer_model backups.
"""
import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from check_continuous_generalization import rectangle, solid
from check_continuous_gcode import check


def run(executable, output, numeric_only=False):
    # Never overwrite a previous check's fixtures or evidence.
    output.mkdir(parents=True, exist_ok=False)
    options_path = Path(__file__).resolve().parents[1] / "tests/data/continuous_extrusion/native_options.json"
    options = json.loads(options_path.read_text()) | {"ce_search_time": "5"}
    solid(output / "box.stl", rectangle(8, 6), rectangle(8, 6), 1)
    (output / "empty.stl").write_bytes(b"")
    (output / "truncated.stl").write_bytes(bytes(80) + struct.pack("<I", 10))
    (output / "malformed.3mf").write_bytes(b"not a zip")
    (output / "malformed.json").write_text("{invalid json}")
    (output / "array.json").write_text("[]")
    if os.name == "nt":
        # Keep regression crashes from opening Windows error dialogs.
        ctypes.windll.kernel32.SetErrorMode(0x8003)
    cases = [
        ("missing-file", "missing.stl", "1", {}, None, False),
        ("empty-mesh", "empty.stl", "1", {}, None, False),
        ("truncated-binary", "truncated.stl", "1", {}, None, False),
        ("malformed-3mf", "malformed.3mf", "1", {}, None, False),
        ("malformed-config", "box.stl", "1", {}, "malformed.json", False),
        ("array-config", "box.stl", "1", {}, "array.json", False),
    ]
    for name, scale in (("zero-scale", "0"), ("negative-scale", "-1"), ("nan-scale", "nan"),
                        ("infinite-scale", "inf"), ("trailing-scale", "1mm"),
                        ("float-overflow-scale", "1e39"), ("float-underflow-scale", "1e-50")):
        cases.append((name, "box.stl", scale, {}, None, False))
    for name, changes in (("negative-layer", {"layer_height": "-0.2"}),
                          ("zero-layer", {"layer_height": "0"}),
                          ("numeric-json-setting", {"layer_height": .2}),
                          ("unknown-setting", {"not_an_orca_setting": "1"}),
                          ("mode-disabled", {"continuous_extrusion": "0"})):
        cases.append((name, "box.stl", "1", changes, None, False))
    cases += [("valid-mm", "box.stl", "1", {}, None, True),
              ("valid-scaled", "box.stl", "1.25", {}, None, True)]
    numeric_cases = []
    for name, key, value in (
        ("float-junk", "layer_height", "0.2junk"),
        ("float-unit", "layer_height", "0.2mm"),
        ("float-extra-number", "layer_height", "0.2 0.3"),
        ("float-whitespace-only", "layer_height", " \t\n"),
        ("float-nan", "layer_height", "nan"),
        ("float-inf", "layer_height", "inf"),
        ("float-overflow", "layer_height", "1e400"),
        ("int-junk", "wall_loops", "2junk"),
        ("int-decimal", "wall_loops", "2.0"),
        ("int-scientific", "wall_loops", "2e0"),
        ("int-overflow", "wall_loops", "999999999999999999999"),
        ("float-vector-junk", "nozzle_diameter", "0.4junk"),
        ("float-vector-second-junk", "nozzle_diameter", "0.4,0.6junk"),
        ("float-vector-inf", "nozzle_diameter", "inf"),
        ("float-vector-empty-item", "nozzle_diameter", "0.4,"),
        ("int-vector-junk", "nozzle_temperature", "220junk"),
        ("percent-junk", "sparse_infill_density", "15%junk"),
        ("percent-duplicate-sign", "sparse_infill_density", "15%%"),
        ("percent-unit", "sparse_infill_density", "15mm"),
        ("percent-inf", "sparse_infill_density", "inf%"),
        ("float-or-percent-junk", "outer_wall_line_width", "100%junk"),
        ("float-or-percent-unit", "outer_wall_line_width", "0.42mm"),
        ("percent-vector-junk", "filament_ironing_flow", "10%junk"),
        ("percent-vector-second-junk", "filament_ironing_flow", "10%,niljunk"),
        ("percent-vector-nan", "filament_ironing_flow", "nan"),
        ("nonnull-nil", "nozzle_diameter", "nil"),
    ):
        numeric_cases.append((name, "box.stl", "1", {key: value}, None, False))
    for name, changes in (
        ("valid-float-scientific", {"layer_height": "2e-1"}),
        ("valid-float-leading-point", {"layer_height": ".2"}),
        ("valid-float-whitespace", {"layer_height": " \t+0.2 \n"}),
        ("valid-int-whitespace", {"wall_loops": " +2 \t"}),
        ("valid-float-vector-scientific", {"nozzle_diameter": "4e-1"}),
        ("valid-float-vector-list", {"filament_flow_ratio": "1.0, 1.0"}),
        ("valid-int-vector-whitespace", {"nozzle_temperature": " 220 \t"}),
        ("valid-percent", {"sparse_infill_density": "15%"}),
        ("valid-percent-no-sign", {"sparse_infill_density": "15"}),
        ("valid-percent-whitespace", {"sparse_infill_density": " 15 % \t"}),
        ("valid-float-or-percent", {"outer_wall_line_width": "100%"}),
        ("valid-float-or-percent-absolute", {"outer_wall_line_width": "0.42"}),
        ("valid-percent-vector", {"filament_ironing_flow": "10%"}),
        ("valid-nullable-vector", {"filament_ironing_flow": "nil"}),
        ("valid-nullable-vector-list", {"filament_ironing_flow": "10%, nil"}),
        ("valid-empty-vector", {"filament_ironing_flow": ""}),
    ):
        numeric_cases.append((name, "box.stl", "1", changes, None, True))
    cases = numeric_cases if numeric_only else cases + numeric_cases
    results = []
    for name, model, scale, changes, config, accept in cases:
        settings = output / (name + ".json")
        settings.write_text(json.dumps(options | changes))
        gcode = output / (name + ".gcode")
        command = [str(executable.resolve()), "--slice-native", str(output / model), str(gcode),
                   scale, str(output / config if config else settings)]
        started = time.monotonic()
        result = {"case": name, "expected_accept": accept, "command": command, "passed": False}
        with (output / (name + ".log")).open("w") as log:
            try:
                process = subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                    timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                result.update(exit=process.returncode, gcode_exists=gcode.exists())
                result["passed"] = (process.returncode == 0 and gcode.exists()) if accept else (process.returncode == 1 and not gcode.exists())
                if accept and result["passed"]:
                    result["motion"] = check(gcode)
            except (subprocess.TimeoutExpired, AssertionError, ValueError) as error:
                result.update(passed=False, error=str(error))
        result["seconds"] = round(time.monotonic() - started, 3)
        result["log_tail"] = (output / (name + ".log")).read_text(errors="replace")[-900:]
        results.append(result)
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(name, "PASS" if result["passed"] else "FAIL", result.get("exit"), flush=True)
    return all(result["passed"] for result in results)


if __name__ == "__main__":
    sys.exit(0 if run(Path(sys.argv[1]), Path(sys.argv[2]).resolve(), "--numeric-only" in sys.argv[3:]) else 1)
