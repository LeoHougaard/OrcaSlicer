"""Check stock Klipper lookahead and extruder requests without a printer.

Usage: python scripts/check_continuous_klipper.py TOOLHEAD_PY EXTRUDER_PY
The two files must be official Klipper source; their hashes are reported.
"""
import ast
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace


def load(path, names):
    source = Path(path).read_bytes()
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name in names]
    assert len(nodes) == len(names)
    scope = {'math': math, 'LOOKAHEAD_FLUSH_TIME': .150}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope, hashlib.sha256(source).hexdigest()


def check(toolhead_path, extruder_path):
    motion, mh = load(toolhead_path, ['Move', 'LookAheadQueue'])
    extrusion, eh = load(extruder_path, ['PrinterExtruder'])
    results = []
    area = math.pi * (1.75 / 2)**2
    for accel in (500., 5000.):
        for flow in (1.2, 5., 35.):
            axis = extrusion['PrinterExtruder'].__new__(extrusion['PrinterExtruder'])
            calls = []
            axis.instant_corner_v = 1.
            axis.trapq = object()
            axis.trapq_append = lambda *args: calls.append(args)
            toolhead = SimpleNamespace(max_accel=accel, max_velocity=600.,
                junction_deviation=25*(math.sqrt(2)-1)/accel,
                mcr_pseudo_accel=accel*.5, extra_axes=[axis])
            queue = motion['LookAheadQueue']()
            position = [0., 0., .2, 0.]
            for xyz, width in (((20.,0.,.2),.42),((20.,20.,.2),.8),
                               ((19.998,20.,.2),.3),((0.,20.,.25),.63),((0.,0.,.4),.42)):
                section = .2 * (width - .2 * (1-math.pi/4))
                end = [*xyz, position[3] + math.dist(position[:3],xyz)*section/area]
                queue.add_move(motion['Move'](toolhead,position,end,flow/section))
                position = end
            moves = queue.flush()
            elapsed = actual_e = 0.
            for move in moves:
                axis.process_move(elapsed,move,3)
                request = calls[-1]
                ta,tc,td = request[2:5]
                start,cruise,a = request[-3:]
                # Integrate the actual trapezoid sent to the extruder queue.
                distance = start*ta + .5*a*ta*ta + cruise*tc + cruise*td - .5*a*td*td
                assert math.isclose(distance,move.axes_d[3],rel_tol=1e-9,abs_tol=1e-12)
                actual_e += distance
                elapsed += ta+tc+td
            assert len(calls)==5 and any(c[-1]>0 for c in calls)
            assert math.isclose(actual_e,position[3],rel_tol=1e-10)
            results.append(dict(acceleration=accel,target_mm3_s=flow,seconds=elapsed,
                                requested_filament_mm=position[3],queued_filament_mm=actual_e))
    return dict(toolhead_sha256=mh,extruder_sha256=eh,cases=results,
                scope='Official Klipper lookahead and base extruder queue; pressure advance zero; no MCU or hardware')


if __name__ == '__main__':
    print(json.dumps(check(*sys.argv[1:]),indent=2))
