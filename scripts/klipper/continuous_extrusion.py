# Constant filament feed for continuous extrusion experiments.
# Copyright (c) 2026 OrcaSlicer contributors
# This file may be distributed under the terms of the GNU GPLv3 license.

import math


def constant_feed_segment(start_position, filament_speed, accel_t, cruise_t,
                          decel_t):
    """Use the *actual planned time*, including toolhead acceleration."""
    values = (start_position, filament_speed, accel_t, cruise_t, decel_t)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite constant-feed motion")
    if filament_speed <= 0. or min(accel_t, cruise_t, decel_t) < 0.:
        raise ValueError("Invalid constant-feed motion")
    duration = accel_t + cruise_t + decel_t
    if duration <= 0.:
        raise ValueError("Empty constant-feed motion")
    return duration, start_position + filament_speed * duration


class ContinuousExtrusion:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.extruder_name = config.get('extruder', 'extruder')
        self.max_filament_speed = config.getfloat(
            'max_filament_speed', 2., above=0.)
        self.rate = 0.
        self.position = 0.
        self.last_end_time = None
        self.segments = 0
        self.extruder = None
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'SET_CONTINUOUS_EXTRUSION', self.cmd_SET_CONTINUOUS_EXTRUSION,
            desc='Start or finish a constant filament feed path')
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)

    def _ready(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.gcode_move = self.printer.lookup_object('gcode_move')
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.original_check = self.extruder.check_move
        self.original_junction = self.extruder.calc_junction
        # Klipper added an extra-axis index to these methods. Support both
        # interfaces explicitly; fail during setup if neither exists.
        self.process_name = ('process_move' if hasattr(self.extruder,
                                                      'process_move')
                             else 'move')
        self.original_process = getattr(self.extruder, self.process_name)
        for name in ('trapq', 'trapq_append', 'last_position',
                     'filament_area', 'max_extrude_ratio'):
            if not hasattr(self.extruder, name):
                raise self.printer.config_error(
                    'continuous_extrusion: unsupported Klipper extruder API')
        self.extruder.check_move = self._check_move
        self.extruder.calc_junction = self._calc_junction
        setattr(self.extruder, self.process_name, self._process_move)
        self.next_transform = self.gcode_move.set_move_transform(self, force=True)

    def _shutdown(self):
        self.rate = 0.
        self.last_end_time = None

    def get_status(self, eventtime):
        return {'api_version': 1, 'active': bool(self.rate), 'filament_speed': self.rate,
                'segments': self.segments}

    def get_position(self):
        return self.next_transform.get_position()

    def move(self, newpos, speed):
        if self.rate:
            oldpos = self.get_position()
            if self.toolhead.get_extruder() is not self.extruder:
                raise self.printer.command_error(
                    'Continuous extrusion cannot change extruders')
            if newpos[3] <= oldpos[3]:
                raise self.printer.command_error(
                    'Continuous extrusion rejects travel and retraction moves')
            if newpos[:3] == oldpos[:3]:
                raise self.printer.command_error(
                    'Continuous extrusion rejects stationary extrusion')
        self.next_transform.move(newpos, speed)

    def _check_move(self, move, *args):
        if not self.rate:
            return self.original_check(move, *args)
        index = args[0] if args else 3
        if move.axes_d[index] <= 0. or not move.is_kinematic_move:
            raise self.printer.command_error(
                'Continuous extrusion requires positive extrusion with motion')
        if not self.extruder.heater.can_extrude:
            raise self.printer.command_error('Extrude below minimum temp')
        stepper = self.extruder.extruder_stepper
        if stepper is None or stepper.pressure_advance != 0.:
            raise self.printer.command_error(
                'Continuous extrusion requires an extruder stepper and zero pressure advance')
        # Preserve stock checks on the requested deposition, then check the
        # actual time-based deposition when the lookahead result is available.
        return self.original_check(move, *args)

    def _calc_junction(self, previous, move, *args):
        if self.rate:
            # The E/distance ratio changes as width changes. It must not impose
            # an extruder velocity change: the extruder stays at self.rate.
            return move.max_cruise_v2
        return self.original_junction(previous, move, *args)

    def _process_move(self, print_time, move, *args):
        if not self.rate:
            return self.original_process(print_time, move, *args)
        duration, end_position = constant_feed_segment(
            self.position, self.rate, move.accel_t, move.cruise_t, move.decel_t)
        if self.last_end_time is not None and abs(print_time - self.last_end_time) > 1e-6:
            self.printer.invoke_shutdown(
                'Continuous extrusion interrupted by a motion queue gap')
            return
        distance = move.move_d
        area = (end_position - self.position) * self.extruder.filament_area / distance
        max_area = self.extruder.max_extrude_ratio * self.extruder.filament_area
        if not math.isfinite(area) or area > max_area:
            self.printer.invoke_shutdown(
                'Constant-feed deposition exceeds max_extrude_cross_section')
            return
        # Extrusion has a flat velocity profile for the complete XYZ move,
        # including its acceleration and deceleration phases. No PA flag.
        self.extruder.trapq_append(
            self.extruder.trapq, print_time, 0., duration, 0.,
            self.position, 0., 0., 1., 0., 0., self.rate, self.rate, 0.)
        self.position = end_position
        self.extruder.last_position = end_position
        self.last_end_time = print_time + duration
        self.segments += 1

    def cmd_SET_CONTINUOUS_EXTRUSION(self, gcmd):
        if self.extruder is None:
            raise gcmd.error('Printer is not ready')
        rate = gcmd.get_float('RATE', minval=0.,
                             maxval=self.max_filament_speed)
        if not math.isfinite(rate):
            raise gcmd.error('RATE must be finite')
        if self.rate and rate:
            raise gcmd.error('Filament feed cannot change during a continuous path')
        if rate:
            if self.toolhead.get_extruder() is not self.extruder:
                raise gcmd.error('Configured extruder must be active')
            if not self.extruder.heater.can_extrude:
                raise gcmd.error('Extrude below minimum temp')
            stepper = self.extruder.extruder_stepper
            if stepper is None or stepper.pressure_advance != 0.:
                raise gcmd.error('Set pressure advance to zero before starting')
            if rate > self.extruder.max_e_velocity:
                raise gcmd.error('RATE exceeds configured extruder velocity')
            self.toolhead.flush_step_generation()
            self.position = self.extruder.last_position
            self.last_end_time = None
            self.segments = 0
            self.rate = rate
        elif self.rate:
            self.toolhead.flush_step_generation()
            # Constant-feed motion changes physical E by actual move duration.
            # Rebase its software coordinate to the G-code E coordinate without
            # moving the motor, before returning to ordinary extrusion.
            import chelper
            _, ffi_lib = chelper.get_ffi()
            position = self.toolhead.get_position()[3]
            print_time = self.toolhead.get_last_move_time()
            ffi_lib.trapq_set_position(self.extruder.trapq, print_time,
                                      position, 0., 0.)
            self.extruder.extruder_stepper.stepper.set_position(
                [position, 0., 0.])
            self.extruder.last_position = position
            self.rate = 0.
            self.last_end_time = None
        gcmd.respond_info('Continuous extrusion filament speed: %.6f mm/s' % self.rate)


def load_config(config):
    return ContinuousExtrusion(config)
