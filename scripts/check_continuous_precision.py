"""Check bead bounds and volume using emitted native continuous XYZ/E motion.

Usage: python scripts/check_continuous_precision.py FILE.gcode [FILE.gcode ...]
Widths use the rounded-rectangle model, not a physical deposition measurement.
Missing configuration or width metadata makes the result inconclusive.
"""
import json
import math
from pathlib import Path
import re
import sys

from check_continuous_gcode import check as check_motion


ROLE_RATIOS = {
    'Outer wall': 'outer_wall_flow_ratio',
    'Inner wall': 'inner_wall_flow_ratio',
    'Sparse infill': 'sparse_infill_flow_ratio',
    'Internal solid infill': 'internal_solid_infill_flow_ratio',
}


def object_moves(text):
    """Track startup position and modal state before inspecting object moves."""
    xyz = [None, None, None]
    extrusion = feed = 0.
    relative_xyz = relative_e = active = False
    width = height = None
    role, layer = '', 0
    for number, line in enumerate(text.splitlines(), 1):
        if line == '; CONTINUOUS_OBJECT_BEGIN': active = True
        elif line == '; CONTINUOUS_OBJECT_END': active = False
        if active and line in (';LAYER_CHANGE', ';CHANGE_LAYER', '; CHANGE_LAYER'): layer += 1
        if line.startswith(';TYPE:'): role = line[6:]
        if line.startswith(';WIDTH:'): width = float(line[7:])
        if line.startswith(';HEIGHT:'): height = float(line[8:])
        code = line.split(';', 1)[0].split()
        if not code:
            continue
        command = code[0]
        if command == 'G90': relative_xyz = False
        elif command == 'G91': relative_xyz = True
        elif command == 'M82': relative_e = False
        elif command == 'M83': relative_e = True
        elif command in ('G0', 'G1', 'G92'):
            values = {word[0]: float(word[1:]) for word in code[1:] if word[0] in 'XYZEF'}
            assert all(math.isfinite(value) for value in values.values()), f'Nonfinite motion at line {number}'
            if command == 'G92':
                xyz = [values.get(key, xyz[i]) for i, key in enumerate('XYZ')]
                extrusion = values.get('E', extrusion)
                continue
            new = [xyz[i] + values.get(key, 0.) if relative_xyz and xyz[i] is not None
                   else values.get(key, xyz[i]) for i, key in enumerate('XYZ')]
            de = values.get('E', 0.) if relative_e else values.get('E', extrusion) - extrusion
            extrusion += de
            feed = values.get('F', feed)
            if active and any(key in values for key in 'XYZE'):
                assert None not in xyz + new, f'Unknown initial position at line {number}'
                distance = math.dist(xyz, new)
                assert command == 'G1' and distance > 0. and de > 0. and feed > 0., f'Invalid object motion at line {number}'
                assert new[2] >= xyz[2] - 1e-8, f'Downward motion at line {number}'
                yield number, distance, de, width, height, role, layer
            xyz = new


def check(path, tolerance=.005):
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    config = dict(re.findall(r'^; (\w+) = (.+)$', text, re.MULTILINE))
    report = dict(file=str(path), passed=False, status='fail', checked_moves=0, skipped_moves=0,
                  skip_reasons={}, width_errors=0, worst_bound_error_mm=0.,
                  worst_declared_width_error_mm=0., volume_checked=False)

    def scalar(key):
        value = float(config[key].split(',')[0])
        assert math.isfinite(value), f'Nonfinite {key}'
        return value

    geometric_volume = 0.
    volume_complete = True
    try:
        report['motion'] = check_motion(path)
        assert report['motion'].get('backend') == 'standard_klipper', 'Native object markers required'
        for number, distance, de, width, height, role, layer in object_moves(text):
            reason = None
            try:
                assert role in ROLE_RATIOS, 'Unsupported role metadata'
                diameter = scalar('filament_diameter')
                factor = scalar('filament_flow_ratio') * scalar('print_flow_ratio')
                if scalar('set_other_flow_ratios'):
                    assert layer > 0, 'Missing layer metadata for first-layer calibration'
                    factor *= scalar(ROLE_RATIOS[role])
                    if layer == 1: factor *= scalar('first_layer_flow_ratio')
                assert diameter > 0. and factor > 0., 'Nonpositive filament diameter or calibration'
            except KeyError as error:
                reason = f'Missing {error.args[0]}'
            except AssertionError as error:
                if str(error) not in ('Unsupported role metadata', 'Missing layer metadata for first-layer calibration'):
                    raise
                reason = str(error)
            if reason is None:
                volume = de * math.pi * (diameter / 2.) ** 2 / factor
                geometric_volume += volume
                try:
                    minimum, maximum = scalar('ce_min_width'), scalar('ce_max_width')
                except KeyError as error:
                    reason = f'Missing {error.args[0]}'
                if width is None or height is None:
                    reason = 'Missing WIDTH or HEIGHT'
            else:
                volume_complete = False
            if reason:
                report['skipped_moves'] += 1
                report['skip_reasons'][reason] = report['skip_reasons'].get(reason, 0) + 1
                continue
            assert all(math.isfinite(value) and value > 0. for value in (width, height, minimum, maximum)), f'Invalid width metadata at line {number}'
            assert maximum >= minimum >= height, f'Invalid bead limits at line {number}'
            inferred = volume / distance / height + height * (1. - math.pi / 4.)
            bound_error = max(minimum - inferred, inferred - maximum, 0.)
            declared_error = abs(inferred - width)
            if max(bound_error, declared_error) > max(report['worst_bound_error_mm'], report['worst_declared_width_error_mm']):
                report['worst_move'] = dict(line=number, length_mm=distance, inferred_width_mm=inferred, declared_width_mm=width)
            report['worst_bound_error_mm'] = max(report['worst_bound_error_mm'], bound_error)
            report['worst_declared_width_error_mm'] = max(report['worst_declared_width_error_mm'], declared_error)
            report['width_errors'] += max(bound_error, declared_error) > tolerance
            report['checked_moves'] += 1
        assert report['checked_moves'] + report['skipped_moves'] == report['motion']['extrusion_moves'], 'Not every object move was audited'
        planned = re.search(r'; continuous planned volume = (\S+) mm3', text)
        if planned and volume_complete:
            planned_volume = float(planned.group(1))
            assert math.isfinite(planned_volume) and planned_volume > 0., 'Invalid planned volume'
            report.update(volume_checked=True, geometric_volume_mm3=geometric_volume,
                          planned_volume_mm3=planned_volume,
                          volume_relative_error=geometric_volume / planned_volume - 1.)
        else:
            report['volume_skip_reason'] = 'Missing planned volume or calibration metadata'
        if report['width_errors'] or report['volume_checked'] and abs(report['volume_relative_error']) > 1e-7:
            report['status'] = 'fail'
        elif report['skipped_moves'] or not report['volume_checked']:
            report['status'] = 'inconclusive'
        else:
            report['status'] = 'pass'
            report['passed'] = True
    except (AssertionError, ValueError, OverflowError) as error:
        report['error'] = str(error)
    return report


if __name__ == '__main__':
    results = [check(path) for path in sys.argv[1:]]
    print(json.dumps(results, indent=2, allow_nan=False))
    sys.exit(0 if results and all(result['passed'] for result in results) else
             1 if not results or any(result['status'] == 'fail' for result in results) else 2)
