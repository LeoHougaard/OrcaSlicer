import io
import math
import unittest

from continuous_extrusion_preview import write_preview


class PreviewTests(unittest.TestCase):
    def fixture(self):
        settings = {'layer_height': .2, 'filament_diameter': 1.75, 'filament_speed': .5}
        def layer(z):
            return {'z': z, 'connected': True, 'widths_valid': True, 'contained': True,
                'paths': [{'points': [[0., 0.], [10., 0.]], 'width': .42, 'role': 'Outer wall'},
                          {'points': [[10., 0.], [10., 10.]], 'width': .8, 'role': 'Internal solid infill'}]}
        return {'layers': [layer(.1), layer(.3)]}, settings

    def test_paths_widths_and_nominal_feed_survive_export(self):
        data, settings = self.fixture()
        stream = io.StringIO()
        stats = write_preview(data, settings, stream)
        text = stream.getvalue()
        self.assertEqual(stats['extrusion_moves'], 4)
        self.assertIn('; LINE_WIDTH: 0.420000', text)
        self.assertIn('; LINE_WIDTH: 0.800000', text)
        self.assertIn('; FEATURE: Outer wall', text)
        speeds = []
        for line in text.splitlines():
            if not line.startswith('G1 '):
                continue
            axes = {s[0]: float(s[1:]) for s in line.split()[1:]}
            speed = axes['F'] / 60.
            self.assertAlmostEqual(axes['E'] / (10. / speed), .5, places=7)
            speeds.append(speed)
        self.assertGreater(speeds[0], speeds[1])
        self.assertNotIn('G28', text)
        self.assertIn('Not a printable program', text)

    def test_unfinished_transitions_are_visible_travels(self):
        data, settings = self.fixture()
        stream = io.StringIO()
        stats = write_preview(data, settings, stream)
        text = stream.getvalue()
        self.assertEqual(stats['travel_moves'], 4)
        self.assertIn('G0 Z0.200000', text)
        self.assertIn('G0 Z0.400000', text)
        self.assertEqual(text.count('; CHANGE_LAYER'), 2)

    def test_relative_moves_preserve_the_original_route(self):
        data, settings = self.fixture()
        stream = io.StringIO()
        write_preview(data, settings, stream)
        position = [0., 0., 0.]
        relative = False
        extruded_endpoints = []
        for line in stream.getvalue().splitlines():
            if line in ('G90', 'G91'):
                relative = line == 'G91'
            if not line.startswith(('G0 ', 'G1 ')):
                continue
            axes = {s[0]: float(s[1:]) for s in line.split()[1:]}
            for i, axis in enumerate('XYZ'):
                if axis in axes:
                    position[i] = position[i] + axes[axis] if relative else axes[axis]
            if 'E' in axes:
                extruded_endpoints.append(tuple(position))
        self.assertEqual(extruded_endpoints, [(110., 100., .2), (110., 110., .2),
                                            (110., 100., .4), (110., 110., .4)])

    def test_invalid_layers_and_fragments_are_preserved(self):
        data, settings = self.fixture()
        data['layers'][0]['contained'] = False
        data['layers'][0]['unresolved'] = [{'points': [[20., 0.], [25., 0.]], 'width': .3}]
        stream = io.StringIO()
        stats = write_preview(data, settings, stream)
        self.assertEqual(stats['invalid_layers'], [1])
        self.assertEqual(stats['unresolved_paths'], 1)
        self.assertEqual(stats['extrusion_moves'], 5)
        self.assertIn('; FEATURE: Gap infill', stream.getvalue())

    def test_invalid_geometry_and_settings_are_rejected(self):
        for field in ('layer_height', 'filament_diameter', 'filament_speed'):
            data, settings = self.fixture()
            settings[field] = math.nan
            with self.subTest(field=field), self.assertRaises(ValueError):
                write_preview(data, settings, io.StringIO())
        data, settings = self.fixture()
        data['layers'][0]['paths'][0]['points'][1][0] = math.inf
        with self.assertRaises(ValueError):
            write_preview(data, settings, io.StringIO())


if __name__ == '__main__':
    unittest.main()
