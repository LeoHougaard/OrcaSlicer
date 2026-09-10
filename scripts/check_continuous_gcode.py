"""Audit native continuous G-code without a printer or firmware connection."""

import json
import math
from pathlib import Path
import re
import sys


def check_legacy(path):
    active = False
    starts = ends = moves = layers = ramps = 0
    rate = None
    min_rate, max_rate = math.inf, 0.
    guard = False
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        code = line.split(';', 1)[0].strip()
        guard |= code == 'SET_GCODE_VARIABLE MACRO=CONTINUOUS_EXTRUSION_CHECK VARIABLE=version VALUE=1'
        if code.startswith('SET_CONTINUOUS_EXTRUSION '):
            new_rate = float(code.split('RATE=')[1])
            if new_rate:
                assert guard and not active and starts == 0, f'Invalid start at {number}'
                active, rate = True, new_rate
                starts += 1
            else:
                assert active, f'Unexpected stop at {number}'
                active = False
                ends += 1
            continue
        if not active:
            continue
        if line.strip() in (';LAYER_CHANGE', '; CHANGE_LAYER', ';CHANGE_LAYER'):
            layers += 1
        # Orca's time-estimation postprocessor inserts display-only progress.
        if not code or code == 'G91' or re.fullmatch(r'M73(?: [PR]\d+)+', code):
            continue
        assert code.startswith('G1 '), f'Unexpected command in continuous path at {number}: {code}'
        values = {key: float(value) for key, value in re.findall(r'([XYZEF])([-+0-9.e]+)', code)}
        assert set(values) == set('XYZEF'), f'Missing motion fields at {number}'
        assert all(math.isfinite(v) for v in values.values())
        distance = math.sqrt(sum(values[k] ** 2 for k in 'XYZ'))
        assert distance > 0 and values['E'] > 0 and values['F'] > 0, f'Empty move or retraction at {number}'
        assert values['Z'] >= 0, f'Downward layer motion at {number}'
        nominal_rate = values['E'] * values['F'] / (60 * distance)
        assert math.isclose(nominal_rate, rate, rel_tol=2e-5), f'Feed changed at {number}: {nominal_rate}'
        min_rate, max_rate = min(min_rate, nominal_rate), max(max_rate, nominal_rate)
        moves += 1
        ramps += values['Z'] > 0
    assert starts == ends == 1 and not active and moves > 0
    return {'extrusion_moves': moves, 'layers': layers, 'rising_moves': ramps,
            'travel_or_retraction_during_object': 0,
            'nominal_filament_feed_mm_s': [min_rate, max_rate],
            'physical_feed_verified': False}


def check(path):
    text = Path(path).read_text(encoding='utf-8')
    if '; CONTINUOUS_OBJECT_BEGIN' not in text:
        return check_legacy(path)
    assert 'SET_CONTINUOUS_EXTRUSION ' not in text
    assert 'CONTINUOUS_EXTRUSION_CHECK' not in text
    active = False
    starts = ends = moves = layers = ramps = 0
    xyz = dict.fromkeys('XYZ', 0.)
    relative_xyz = relative_e = False
    extrusion = feed = total_e = total_length = duration = 0.
    min_rate, max_rate = math.inf, 0.
    max_axis_speed = dict.fromkeys('XYZ', 0.)
    max_speed = 0.
    for number, line in enumerate(text.splitlines(), 1):
        if line == '; CONTINUOUS_OBJECT_BEGIN':
            assert not active and starts == 0
            starts += 1
            active = True
        elif line == '; CONTINUOUS_OBJECT_END':
            assert active
            ends += 1
            active = False
        if active and line.strip() in (';LAYER_CHANGE', '; CHANGE_LAYER', ';CHANGE_LAYER'):
            layers += 1
        code = line.split(';', 1)[0].strip()
        if not code:
            continue
        command = code.split()[0]
        values = {k: float(v) for k, v in re.findall(r'([XYZEF])([-+0-9.]+)', code)}
        if command == 'G90': relative_xyz = False
        elif command == 'G91': relative_xyz = True
        elif command == 'M82': relative_e = False
        elif command == 'M83': relative_e = True
        elif command == 'G92':
            extrusion = values.get('E', extrusion)
            for k in xyz: xyz[k] = values.get(k, xyz[k])
        elif command in ('G0', 'G1'):
            new_xyz = {k: (xyz[k] + values.get(k, 0.) if relative_xyz else values.get(k, xyz[k])) for k in xyz}
            delta = {k: new_xyz[k] - xyz[k] for k in xyz}
            distance = math.sqrt(sum(v*v for v in delta.values()))
            de = values.get('E', 0.) if relative_e else values.get('E', extrusion) - extrusion
            extrusion = extrusion + de
            xyz = new_xyz
            feed = values.get('F', feed)
            if active and (distance or 'E' in values):
                assert command == 'G1' and distance > 0 and de > 0 and feed > 0, f'Travel, empty move, or retraction at {number}: {code}'
                assert delta['Z'] >= -1e-8, f'Downward move at {number}'
                nominal_rate = de * feed / (60 * distance)
                assert math.isfinite(nominal_rate)
                min_rate, max_rate = min(min_rate, nominal_rate), max(max_rate, nominal_rate)
                max_speed = max(max_speed, feed / 60)
                for k in xyz: max_axis_speed[k] = max(max_axis_speed[k], abs(delta[k]) * feed / (60 * distance))
                total_e += de
                total_length += distance
                duration += 60 * distance / feed
                moves += 1
                ramps += delta['Z'] > 1e-8
        elif active:
            assert command in ('M73', 'M104', 'M140', 'M106', 'M107', 'SET_VELOCITY_LIMIT', 'SET_PRESSURE_ADVANCE'), f'Unexpected command at {number}: {code}'
    assert starts == ends == 1 and not active and moves > 0
    return {'backend': 'standard_klipper', 'extrusion_moves': moves, 'layers': layers,
            'rising_moves': ramps, 'travel_or_retraction_during_object': 0,
            'nominal_filament_feed_mm_s': [min_rate, max_rate],
            'filament_mm': total_e, 'path_mm': total_length,
            'nominal_seconds': duration, 'max_speed_mm_s': max_speed,
            'max_axis_speed_mm_s': max_axis_speed, 'physical_feed_verified': False}


if __name__ == '__main__':
    print(json.dumps(check(sys.argv[1]), indent=2))
