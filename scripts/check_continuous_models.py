"""Native release checks over independent printable geometries and settings.

Usage: python scripts/check_continuous_models.py EXE OPTIONS_JSON OUTPUT_DIR [case ...]
All generated meshes use millimetres. Existing results are never treated as passes.
"""

import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time

from check_continuous_crossings import check as check_crossings
from check_continuous_gcode import check
from check_continuous_generalization import circle, rectangle, solid


def heightfield(path, cells):
    """Boundary mesh of unit columns, including concave outlines and multiple holes."""
    triangles = []

    def face(points):
        triangles.extend((points[0], points[i], points[i + 1]) for i in (1, 2))

    for (x, y), height in cells.items():
        face([(x, y, 0), (x, y + 1, 0), (x + 1, y + 1, 0), (x + 1, y, 0)])
        face([(x, y, height), (x + 1, y, height),
              (x + 1, y + 1, height), (x, y + 1, height)])
        for dx, dy, a, b in [
            (0, -1, (x, y), (x + 1, y)),
            (1, 0, (x + 1, y), (x + 1, y + 1)),
            (0, 1, (x + 1, y + 1), (x, y + 1)),
            (-1, 0, (x, y + 1), (x, y)),
        ]:
            low = cells.get((x + dx, y + dy), 0)
            if low < height:
                face([(*a, low), (*b, low), (*b, height), (*a, height)])
    data = bytearray(80) + struct.pack('<I', len(triangles))
    for a, b, c in triangles:
        u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
        normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(value*value for value in normal))
        data += struct.pack('<12fH', *(value/length for value in normal), *a, *b, *c, 0)
    path.write_bytes(data)


def models(output):
    shapes = {
        'box': (rectangle(18, 14), rectangle(18, 14), 4, None),
        'strip-1mm': (rectangle(1, 20), rectangle(1, 20), 4, None),
        'strip-1.3mm': (rectangle(1.3, 20), rectangle(1.3, 20), 4, None),
        'disk': (circle(10), circle(10), 4, None),
        'ring': (circle(12), circle(12), 4, circle(8)),
        'thin-ring': (circle(10), circle(10), 4, circle(9)),
        'oval': ([(x*1.5, y) for x, y in circle(8)],
                 [(x*1.5, y) for x, y in circle(8)], 4, None),
        'taper': (rectangle(18, 14), rectangle(14, 10, 2, 2), 6, None),
        'leaning': (rectangle(12, 10), rectangle(12, 10, 3, 1), 6, None),
        'expanding': (rectangle(14, 10, 2, 2), rectangle(18, 14), 6, None),
    }
    for name, shape in shapes.items():
        solid(output / f'{name}.stl', *shape)
    outlines = {
        'l-bracket': lambda x, y: 4 if x < 4 or y < 4 else 0,
        'u-bracket': lambda x, y: 4 if x < 4 or x >= 16 or y < 4 else 0,
        'cross': lambda x, y: 4 if 8 <= x < 12 or 8 <= y < 12 else 0,
        'two-holes': lambda x, y: 0 if (3 <= x < 7 or 13 <= x < 17) and 7 <= y < 13 else 4,
        'dumbbell': lambda x, y: 4 if x < 7 or x >= 13 or 9 <= y < 11 else 0,
        'shoulder': lambda x, y: 4 if 4 <= x < 16 and 4 <= y < 16 else 1,
        'pocket': lambda x, y: 1 if 4 <= x < 16 and 4 <= y < 16 else 4,
        'stairs': lambda x, y: 1 + x // 5,
    }
    for name, height in outlines.items():
        heightfield(output / f'{name}.stl', {(x, y): height(x, y)
                    for x in range(20) for y in range(20) if height(x, y) > 0})
    rotated = []
    # Rotated concave outlines exercise diagonal joins and G-code rounding.
    sine, cosine = math.sin(math.radians(37)), math.cos(math.radians(37))
    for name in ('u-bracket', 'two-holes', 'shoulder'):
        data = bytearray((output / f'{name}.stl').read_bytes())
        for offset in range(84, len(data), 50):
            for vector in range(4):
                position = offset + vector * 12
                x, y = struct.unpack_from('<2f', data, position)
                struct.pack_into('<2f', data, position, x*cosine - y*sine, x*sine + y*cosine)
        rotated.append(f'rotated-{name}')
        (output / f'rotated-{name}.stl').write_bytes(data)
    return list(shapes) + list(outlines) + rotated


def run(executable, options_path, output, selected):
    output.mkdir(parents=True, exist_ok=True)
    names = models(output)
    options = json.loads(options_path.read_text(encoding='utf-8-sig'))
    options.update(continuous_extrusion='1', slicing_mode='regular',
                   ce_search_time='30', wall_loops='2', top_shell_layers='3',
                   bottom_shell_layers='3', top_shell_thickness='0',
                   bottom_shell_thickness='0', seam_position='aligned')
    cases = {f'{name}-{density}': (name, 1, {'sparse_infill_density': f'{density}%'})
             for name in names for density in (15, 40, 100)}
    for name in ('box', 'ring', 'two-holes', 'pocket'):
        cases[f'{name}-0'] = (name, 1, {'sparse_infill_density': '0%'})
    for name in ('ring', 'u-bracket', 'two-holes', 'shoulder'):
        for scale in (.75, 1.25):
            cases[f'{name}-scale-{scale}'] = (name, scale, {'sparse_infill_density': '15%'})
    for name in ('ring', 'two-holes', 'shoulder'):
        for seam in ('back', 'nearest', 'random'):
            cases[f'{name}-seam-{seam}'] = (name, 1, {'sparse_infill_density': '15%', 'seam_position': seam})
    for name in ('box', 'ring', 'u-bracket'):
        cases[f'{name}-fine'] = (name, 1, {'sparse_infill_density': '15%', 'layer_height': '.12',
                                        'initial_layer_print_height': '.12', 'ce_resolution': '.0125'})
    for name in ('ring', 'u-bracket', 'shoulder'):
        for nozzle, width, minimum, maximum, height in ((.4, .42, .4, .6, .2),
                                                       (.6, .63, .45, 1.2, .3),
                                                       (.8, .84, .6, 1.6, .4)):
            cases[f'{name}-nozzle-{nozzle}'] = (name, 1, {
                'sparse_infill_density': '15%', 'nozzle_diameter': str(nozzle),
                'ce_nominal_width': str(width), 'ce_min_width': str(minimum),
                'ce_max_width': str(maximum), 'layer_height': str(height),
                'initial_layer_print_height': str(height)})
    results = []
    for case in selected or cases:
        name, scale, changes = cases[case]
        config = output / f'{case}.json'
        config.write_text(json.dumps(options | changes, indent=2))
        gcode = output / f'{case}.gcode'
        log = output / f'{case}.log'
        started = time.monotonic()
        result = {'case': case, 'passed': False}
        try:
            with log.open('w') as stream:
                process = subprocess.run([str(executable.resolve()), '--slice-native',
                                          str(output / f'{name}.stl'), str(gcode), str(scale), str(config)],
                                         stdout=stream, stderr=stream, timeout=180)
            result['slice_seconds'] = round(time.monotonic() - started, 2)
            assert process.returncode == 0, 'Native slicing failed'
            result['motion'] = check(gcode)
            result['crossings'] = check_crossings(gcode)['conflicts']
            assert result['crossings'] == 0, 'Same-height crossing or retrace'
            layers = json.loads(Path(str(gcode) + '.json').read_text())['layers']
            area = sum(layer['target_area'] for layer in layers)
            for key in ('missing', 'excess', 'outside'):
                result[key + '_fraction'] = sum(layer[key + '_area'] for layer in layers) / area
            result['worst_missing_fraction'] = max(layer['missing_area'] / layer['target_area'] for layer in layers)
            assert result['missing_fraction'] < .05, 'More than 5% missing material'
            assert result['worst_missing_fraction'] < .08, 'A layer has more than 8% missing material'
            assert result['excess_fraction'] < .05, 'More than 5% excess material'
            assert result['outside_fraction'] < .02, 'More than 2% outside model'
            assert result['slice_seconds'] < 120, 'Small model exceeds two minutes'
            result['passed'] = True
        except (AssertionError, subprocess.TimeoutExpired, ValueError, OSError) as error:
            result['error'] = str(error)
            if log.exists():
                result['log_tail'] = log.read_text(errors='replace')[-1500:]
        results.append(result)
        (output / 'results.json').write_text(json.dumps(results, indent=2))
        print(case, 'PASS' if result['passed'] else 'FAIL', result.get('error', ''), flush=True)
    return all(result['passed'] for result in results)


if __name__ == '__main__':
    sys.exit(0 if run(*(Path(arg) for arg in sys.argv[1:4]), sys.argv[4:]) else 1)
