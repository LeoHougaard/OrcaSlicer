"""Find non-adjacent crossing/retraced centerlines in native continuous G-code."""
import json
import math
from pathlib import Path
import re
import sys


def segments(path):
    xyz = [0., 0., 0.]
    active = False
    layer = 0
    role = ""
    result = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line == "; CONTINUOUS_OBJECT_BEGIN": active = True
        if line == "; CONTINUOUS_OBJECT_END": active = False
        if active and line in (";LAYER_CHANGE", ";CHANGE_LAYER"): layer += 1
        if line.startswith(";TYPE:"): role = line[6:]
        code = line.split(";", 1)[0]
        if code.split(" ", 1)[0] not in ("G0", "G1"): continue
        values = {k: float(v) for k, v in re.findall(r"([XYZE])([-+0-9.]+)", code)}
        new = [values.get(k, xyz[i]) for i, k in enumerate("XYZ")]
        if active and "E" in values and new[:2] != xyz[:2]:
            result.append({"a": xyz, "b": new, "line": number, "layer": layer, "role": role})
        xyz = new
    return result


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def intersection(a, b, c, d):
    v = [b[i] - a[i] for i in range(2)]
    w = [d[i] - c[i] for i in range(2)]
    q = [c[i] - a[i] for i in range(2)]
    det = cross(v, w)
    if abs(det) > 1e-10:
        t, u = cross(q, w) / det, cross(q, v) / det
        if 1e-6 < t < 1. - 1e-6 and 1e-6 < u < 1. - 1e-6:
            return t, u, "crossing"
    elif abs(cross(q, v)) < 1e-8:
        axis = int(abs(v[1]) > abs(v[0]))
        lo, hi = sorted(((c[axis] - a[axis]) / v[axis], (d[axis] - a[axis]) / v[axis]))
        lo, hi = max(0., lo), min(1., hi)
        if (hi - lo) * math.hypot(*v) > .01:
            t = .5 * (lo + hi)
            u = (a[axis] + t * v[axis] - c[axis]) / w[axis]
            return t, u, "retrace"
    return None


def check(path):
    lines = segments(path)
    grid = {}
    conflicts = []
    for i, segment in enumerate(lines):
        a, b = segment["a"], segment["b"]
        assert b[2] >= a[2], "Crossing audit requires nondecreasing native layer heights"
        cells = [(x, y) for x in range(math.floor(min(a[0], b[0]) / 2), math.floor(max(a[0], b[0]) / 2) + 1)
                 for y in range(math.floor(min(a[1], b[1]) / 2), math.floor(max(a[1], b[1]) / 2) + 1)]
        # Finished lower layers cannot intersect a later, higher segment. Evict
        # them from visited cells so a tall print does not make this quadratic
        # in layer count.
        nearby = set()
        for cell in cells:
            grid[cell] = [j for j in grid.get(cell, ()) if max(lines[j]["a"][2], lines[j]["b"][2]) >= a[2] - 1e-6]
            nearby.update(grid[cell])
        for j in nearby:
            other = lines[j]
            c, d = other["a"], other["b"]
            if min(a[2], b[2]) - max(c[2], d[2]) > .005: continue
            hit = intersection(a, b, c, d)
            if hit:
                t, u, kind = hit
                # A rising layer ramp may intentionally stack above a previous
                # contour. Count intersections at the same height, not a 5 um
                # neighborhood that would label that new layer as a retrace.
                if abs(a[2] + t * (b[2] - a[2]) - c[2] - u * (d[2] - c[2])) < 1e-6:
                    conflicts.append({"kind": kind, "line": segment["line"], "other_line": other["line"],
                                      "layer": segment["layer"], "other_layer": other["layer"],
                                      "point": [a[k] + t * (b[k] - a[k]) for k in range(3)]})
        for cell in cells: grid.setdefault(cell, []).append(i)
    return {"conflicts": len(conflicts), "examples": conflicts}


if __name__ == "__main__":
    report = check(sys.argv[1])
    print(json.dumps(report, indent=2))
    if report["conflicts"]: sys.exit(1)
