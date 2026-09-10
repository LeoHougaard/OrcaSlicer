"""Exercise the constant-feed backend against a supplied Klipper lookahead.

Usage: python check_motion_queue.py /path/to/klipper/klippy/toolhead.py
No printer, MCU, network connection, or extra Python package is needed.
Only the Move and LookAheadQueue definitions are loaded from that file.
"""

import ast
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

from continuous_extrusion import ContinuousExtrusion


def check(source_path):
    source = Path(source_path).read_bytes()
    parsed = ast.parse(source.decode('utf-8'))
    classes = [node for node in parsed.body if isinstance(node, ast.ClassDef)
               and node.name in ('Move', 'LookAheadQueue')]
    if len(classes) != 2:
        raise ValueError('Expected Klipper Move and LookAheadQueue classes')
    namespace = {'math': math, 'LOOKAHEAD_FLUSH_TIME': .150}
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(source_path),
                 'exec'), namespace)
    backend = ContinuousExtrusion.__new__(ContinuousExtrusion)
    backend.rate = .5
    backend.position = 0.
    backend.last_end_time = None
    backend.segments = 0
    calls = []
    failures = []
    backend.printer = SimpleNamespace(invoke_shutdown=failures.append)
    filament_area = math.pi * (1.75 / 2.)**2
    backend.extruder = SimpleNamespace(trapq=object(),
        trapq_append=lambda *args: calls.append(args),
        filament_area=filament_area, max_extrude_ratio=.64/filament_area,
        last_position=0.)
    axis = SimpleNamespace(calc_junction=backend._calc_junction)
    toolhead = SimpleNamespace(max_accel=1000., max_velocity=100.,
        junction_deviation=25.*(math.sqrt(2.)-1.)/1000.,
        mcr_pseudo_accel=500., max_accel_to_decel=500.,
        extra_axes=[axis], extruder=axis)
    queue = namespace['LookAheadQueue']()
    position = [0., 0., .2, 0.]
    for xy, width in (((20., 0.), .42), ((20., 20.), .8),
                      ((0., 20.), .3), ((0., 0.), .63),
                      ((20., 0.), .42)):
        area = .2 * (width - .2 * (1. - math.pi / 4.))
        distance = math.dist(position[:2], xy)
        end = [*xy, .2, position[3] + area * distance / filament_area]
        queue.add_move(namespace['Move'](toolhead, position, end,
                       backend.rate * filament_area / area))
        position = end
    moves = queue.flush()
    print_time = 10.
    ordinary_speeds = []
    for move in moves:
        backend._process_move(print_time, move, 3)
        print_time += move.accel_t + move.cruise_t + move.decel_t
        ordinary_speeds.extend(v * move.axes_r[3]
                               for v in (move.start_v, move.cruise_v, move.end_v))
    if failures or len(calls) != len(moves):
        raise AssertionError(f'Backend rejected motion: {failures}')
    if not all(c[2] == 0. and c[4] == 0. and c[-3:] == (.5, .5, 0.)
               for c in calls):
        raise AssertionError('Extruder rate changed inside planned motion')
    duration = print_time - 10.
    if not math.isclose(backend.position, .5 * duration, abs_tol=1e-12):
        raise AssertionError('Extruder displacement is inconsistent with time')
    return {'klipper_toolhead_sha256': hashlib.sha256(source).hexdigest(),
            'moves': len(moves), 'planned_seconds': duration,
            'constant_feed_min_mm_s': .5, 'constant_feed_max_mm_s': .5,
            'ordinary_feed_min_mm_s': min(ordinary_speeds),
            'ordinary_feed_max_mm_s': max(ordinary_speeds),
            'actual_filament_mm': backend.position,
            'scope': 'Klipper lookahead and extruder queue requests; no MCU or hardware test'}


if __name__ == '__main__':
    print(json.dumps(check(sys.argv[1]), indent=2))
