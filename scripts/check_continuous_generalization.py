"""Run matched native slices and command audits on scales and independent solids.

Usage: python scripts/check_continuous_generalization.py EXE OPTIONS_JSON OUTPUT_DIR
       [case ...]
STL coordinates generated here are millimetres. The historical fixture is metres.
"""

import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

from check_continuous_gcode import check


def solid(path, bottom, top, height, inner=None):
    """Loft corresponding convex rings, optionally with a straight through hole."""
    n = len(bottom)
    vertices = [(x, y, z) for z, ring in [(0, bottom), (height, top)] for x, y in ring]
    triangles = []
    for i in range(n):
        j = (i + 1) % n
        triangles.extend([(i, j, n + j), (i, n + j, n + i)])
    if inner is None:
        for i in range(1, n - 1):
            triangles.extend([(0, i + 1, i), (n, n + i, n + i + 1)])
    else:
        assert len(inner) == n
        vertices.extend((x, y, z) for z in [0, height] for x, y in inner)
        for i in range(n):
            j = (i + 1) % n
            triangles.extend([(2*n+i, 3*n+j, 2*n+j), (2*n+i, 3*n+i, 3*n+j),
                              (i, 2*n+j, j), (i, 2*n+i, 2*n+j),
                              (n+i, n+j, 3*n+j), (n+i, 3*n+j, 3*n+i)])
    data = bytearray(80) + struct.pack('<I', len(triangles))
    for triangle in triangles:
        a, b, c = (vertices[i] for i in triangle)
        u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(x*x for x in normal))
        data += struct.pack('<12fH', *(x / length for x in normal), *a, *b, *c, 0)
    path.write_bytes(data)


def rectangle(w, h, x=0, y=0):
    return [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]


def circle(radius):
    return [(radius * math.cos(i * math.tau / 64), radius * math.sin(i * math.tau / 64)) for i in range(64)]


def run(executable, options, output, selected):
    output.mkdir(parents=True, exist_ok=True)
    shapes = {
        'box': (rectangle(12, 8), rectangle(12, 8), 2, None),
        'thin-strip': (rectangle(1.2, 12), rectangle(1.2, 12), 2, None),
        'ring': (circle(10), circle(10), 2, circle(7)),
        'taper': (rectangle(8, 8), rectangle(4, 4, 2, 2), 6, None),
        'leaning': (rectangle(6, 5), rectangle(6, 5, 7, 2), 6, None),
    }
    cases = {}
    for name, shape in shapes.items():
        path = output / (name + '.stl')
        solid(path, *shape)
        cases[name] = (path, 1)
    fixture = Path(__file__).resolve().parents[1] / 'tests/data/continuous_extrusion/hardest_part_metres.stl'
    for scale in [500, 750, 1000, 1250]:
        cases[f'hardest-{scale}'] = (fixture, scale)
    results = []
    for name in selected or cases:
        model, scale = cases[name]
        gcode = output / (name + '.gcode')
        command = [str(executable.resolve()), '--slice-native', str(model), str(gcode), str(scale), str(options)]
        start = time.monotonic()
        with (output / (name + '.log')).open('w') as log:
            process = subprocess.run(command, stdout=log, stderr=log, timeout=240)
        result = dict(case=name, command=command, returncode=process.returncode, seconds=time.monotonic()-start)
        if process.returncode == 0:
            metadata = json.loads(Path(str(gcode) + '.json').read_text())
            layers = metadata['layers']
            area = sum(layer['target_area'] for layer in layers)
            result.update({key + '_percent': 100 * sum(layer[key + '_area'] for layer in layers) / area
                           for key in ['missing', 'outside', 'excess']})
            result['worst_layer_missing_percent'] = max(100 * layer['missing_area'] / layer['target_area'] for layer in layers)
            result['audit'] = check(gcode)
        else:
            result['error'] = (output / (name + '.log')).read_text()[-2000:]
        results.append(result)
        (output / 'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(result), flush=True)
    return all(r['returncode'] == 0 for r in results)


if __name__ == '__main__':
    sys.exit(0 if run(*(Path(arg) for arg in sys.argv[1:4]), sys.argv[4:]) else 1)
