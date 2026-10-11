"""Bounded offline CLI regression checks.

Usage: python scripts/check_native_input_validation.py EXE NEW_OUTPUT_DIR
Uses generated millimetre meshes and the repository's offline test profile.
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


def run(executable, output):
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
    sys.exit(0 if run(Path(sys.argv[1]), Path(sys.argv[2]).resolve()) else 1)
