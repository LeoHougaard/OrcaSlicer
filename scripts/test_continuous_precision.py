import math
from pathlib import Path
import tempfile
import unittest

from check_continuous_precision import check


class EmittedPrecision(unittest.TestCase):
    def audit(self, start='10.000300', end='10.010300', width=.3, height=.2,
              length=.01, filament_ratio=1., print_ratio=1., role_ratio=1., first_ratio=1.,
              metadata=True, extrusion=None, planned_ratio=1., relative_e=True, missing=()):
        volume = length * height * (width - height * (1. - math.pi / 4.))
        de = volume / (math.pi * (1.75 / 2.) ** 2) * filament_ratio * print_ratio * role_ratio * first_ratio
        initial_e = 0. if relative_e else 12.
        e_word = de + initial_e if extrusion is None else extrusion
        text = ('G90\n' + ('M83\n' if relative_e else 'M82\n') +
                f'G92 E{initial_e}\nG1 X{start} Y20.000300 Z0.2 F600\n'
                '; CONTINUOUS_OBJECT_BEGIN\n;LAYER_CHANGE\n;TYPE:Outer wall\n' +
                (f';WIDTH:{width}\n;HEIGHT:{height}\n' if metadata else '') +
                f'G1 X{end} Y20.000300 Z0.2 E{e_word:.12f}\n'
                '; CONTINUOUS_OBJECT_END\n' +
                f'; continuous planned volume = {volume * planned_ratio:.12f} mm3\n'
                f'; filament_diameter = 1.75\n; filament_flow_ratio = {filament_ratio}\n'
                f'; print_flow_ratio = {print_ratio}\n; set_other_flow_ratios = 1\n'
                f'; outer_wall_flow_ratio = {role_ratio}\n; first_layer_flow_ratio = {first_ratio}\n'
                '; ce_min_width = 0.3\n; ce_max_width = 0.8\n')
        text = '\n'.join(line for line in text.splitlines()
                         if not any(line.startswith(f'; {key} =') for key in missing)) + '\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'precision.gcode'
            path.write_text(text, encoding='utf-8')
            return check(path)

    def test_short_segment_rounding_counterexample(self):
        result = self.audit(start='10.000', end='10.004', length=.0033)
        self.assertFalse(result['passed'])
        self.assertGreater(result['worst_bound_error_mm'], .04)
        self.assertEqual(result['width_errors'], 1)
        self.assertAlmostEqual(result['volume_relative_error'], 0., places=6)

    def test_high_precision_and_calibration_preserve_width_and_volume(self):
        result = self.audit(filament_ratio=.9, print_ratio=.95, role_ratio=.85, first_ratio=1.1)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['checked_moves'], 1)
        self.assertAlmostEqual(result['worst_declared_width_error_mm'], 0., places=6)
        self.assertTrue(result['volume_checked'])

    def test_precise_initial_position_is_used(self):
        self.assertTrue(self.audit()['passed'])
        result = self.audit(start='10.000')
        self.assertFalse(result['passed'])
        self.assertGreater(result['worst_bound_error_mm'], .005)

    def test_absolute_extrusion_uses_initial_reset(self):
        result = self.audit(relative_e=False, filament_ratio=.9)
        self.assertTrue(result['passed'], result)

    def test_missing_width_metadata_is_inconclusive(self):
        result = self.audit(metadata=False)
        self.assertFalse(result['passed'], result)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['checked_moves'], 0)
        self.assertEqual(result['skipped_moves'], 1)
        self.assertTrue(result['volume_checked'])

    def test_missing_calibration_skips_width_and_volume(self):
        result = self.audit(missing=('filament_flow_ratio',))
        self.assertFalse(result['passed'], result)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['checked_moves'], 0)
        self.assertEqual(result['skipped_moves'], 1)
        self.assertFalse(result['volume_checked'])

    def test_missing_planned_volume_is_inconclusive(self):
        result = self.audit(missing=('continuous planned volume',))
        self.assertFalse(result['passed'], result)
        self.assertEqual(result['status'], 'inconclusive')
        self.assertEqual(result['checked_moves'], 1)
        self.assertEqual(result['skipped_moves'], 0)
        self.assertFalse(result['volume_checked'])

    def test_nonfinite_or_nonpositive_extrusion_fails(self):
        for value in (math.nan, math.inf, 0., -.001):
            with self.subTest(value=value):
                self.assertFalse(self.audit(extrusion=value)['passed'])

    def test_incorrect_planned_volume_fails(self):
        result = self.audit(planned_ratio=2.)
        self.assertFalse(result['passed'])
        self.assertEqual(result['width_errors'], 0)
        self.assertAlmostEqual(result['volume_relative_error'], -.5, places=6)

    def test_declared_width_is_checked_within_bead_bounds(self):
        result = self.audit(width=.42, length=.009)
        self.assertFalse(result['passed'])
        self.assertEqual(result['worst_bound_error_mm'], 0.)
        self.assertGreater(result['worst_declared_width_error_mm'], .005)


if __name__ == '__main__':
    unittest.main()
