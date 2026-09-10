import math
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from continuous_extrusion import ContinuousExtrusion, constant_feed_segment


class ConstantFeedTests(unittest.TestCase):
    def test_acceleration_time_does_not_reduce_extruder_velocity(self):
        position = 0.
        for times in ((.1, .8, .1), (.4, .2, .4), (0., 1., 0.)):
            duration, end = constant_feed_segment(position, .5, *times)
            self.assertAlmostEqual((end - position) / duration, .5)
            position = end
        self.assertAlmostEqual(position, 1.5)

    def test_invalid_motion_is_rejected(self):
        for args in ((0., 0., .1, .8, .1), (0., .5, -.1, 1., .1),
                     (0., math.nan, .1, .8, .1), (0., .5, 0., 0., 0.)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                constant_feed_segment(*args)

    def make_backend(self):
        backend = ContinuousExtrusion.__new__(ContinuousExtrusion)
        calls = []
        shutdowns = []
        backend.rate = .5
        backend.position = 0.
        backend.last_end_time = None
        backend.segments = 0
        backend.printer = SimpleNamespace(invoke_shutdown=shutdowns.append)
        backend.extruder = SimpleNamespace(trapq=object(),
            trapq_append=lambda *args: calls.append(args),
            filament_area=2.4, max_extrude_ratio=.4, last_position=0.)
        return backend, calls, shutdowns

    def test_queued_segments_use_flat_feed_instead_of_xyz_acceleration(self):
        backend, calls, shutdowns = self.make_backend()
        move = SimpleNamespace(accel_t=.2, cruise_t=.4, decel_t=.4,
                               move_d=20.)
        backend._process_move(10., move, 3)
        backend._process_move(11., move, 3)
        self.assertFalse(shutdowns)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call[2:5], (0., 1., 0.))
            self.assertEqual(call[-3:], (.5, .5, 0.))
            self.assertEqual(call[9], 0.)  # No pressure-advance flag
        self.assertAlmostEqual(backend.extruder.last_position, 1.)
        self.assertEqual(calls[1][5], .5)

    def test_queue_gap_cannot_silently_restart_extrusion(self):
        backend, calls, shutdowns = self.make_backend()
        move = SimpleNamespace(accel_t=.2, cruise_t=.4, decel_t=.4,
                               move_d=20.)
        backend._process_move(10., move)
        backend._process_move(12., move)
        self.assertEqual(len(calls), 1)
        self.assertIn('queue gap', shutdowns[0])

    def test_actual_deposition_limit_is_checked_after_lookahead(self):
        backend, calls, shutdowns = self.make_backend()
        move = SimpleNamespace(accel_t=10., cruise_t=0., decel_t=10.,
                               move_d=1.)
        backend._process_move(10., move)
        self.assertFalse(calls)
        self.assertIn('max_extrude_cross_section', shutdowns[0])

    def test_inactive_backend_delegates_without_modifying_motion(self):
        backend, calls, shutdowns = self.make_backend()
        backend.rate = 0.
        ordinary = []
        backend.original_process = lambda *args: ordinary.append(args)
        marker = object()
        backend._process_move(7., marker, 3)
        self.assertEqual(ordinary, [(7., marker, 3)])
        self.assertFalse(calls)

    def test_active_transform_rejects_retraction_travel_and_pure_extrusion(self):
        backend, _, _ = self.make_backend()
        backend.printer.command_error = ValueError
        backend.toolhead = SimpleNamespace(get_extruder=lambda: backend.extruder)
        accepted = []
        backend.next_transform = SimpleNamespace(
            get_position=lambda: [0., 0., .2, 1.],
            move=lambda *args: accepted.append(args))
        for end in ([1., 0., .2, 1.], [1., 0., .2, .9], [0., 0., .2, 2.]):
            with self.subTest(end=end), self.assertRaises(ValueError):
                backend.move(end, 10.)
        backend.move([1., 0., .2, 1.1], 10.)
        self.assertEqual(len(accepted), 1)

    def test_rate_cannot_be_changed_mid_path(self):
        backend, _, _ = self.make_backend()
        backend.max_filament_speed = 2.
        command = SimpleNamespace(get_float=lambda *args, **kw: .6,
                                  error=ValueError)
        with self.assertRaisesRegex(ValueError, 'cannot change'):
            backend.cmd_SET_CONTINUOUS_EXTRUSION(command)
        self.assertEqual(backend.rate, .5)

    def test_finishing_rebases_e_without_queuing_a_compensating_extrusion(self):
        backend, calls, _ = self.make_backend()
        backend.max_filament_speed = 2.
        backend.position = 7.
        backend.extruder.last_position = 7.
        events = []
        backend.toolhead = SimpleNamespace(
            flush_step_generation=lambda: events.append('flush'),
            get_position=lambda: [20., 20., .2, 5.],
            get_last_move_time=lambda: 14.)
        backend.extruder.extruder_stepper = SimpleNamespace(stepper=SimpleNamespace(
            set_position=lambda p: events.append(('position', p))))
        ffi = SimpleNamespace(trapq_set_position=lambda *args: events.append(('queue', args[1:])))
        command = SimpleNamespace(get_float=lambda *args, **kw: 0.,
            respond_info=lambda msg: None, error=ValueError)
        with patch.dict('sys.modules', {'chelper': SimpleNamespace(get_ffi=lambda: (None, ffi))}):
            backend.cmd_SET_CONTINUOUS_EXTRUSION(command)
        self.assertEqual(events, ['flush', ('queue', (14., 5., 0., 0.)),
                                  ('position', [5., 0., 0.])])
        self.assertFalse(calls)
        self.assertEqual(backend.rate, 0.)
        self.assertEqual(backend.extruder.last_position, 5.)


if __name__ == '__main__':
    unittest.main()
