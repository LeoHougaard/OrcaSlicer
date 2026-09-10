# Constant-feed Klipper experiment

This is a development module for the strict constant filament-feed requirement.
It has not been installed on a printer. It is not a validated printing backend.
The current native Orca continuous mode uses standard Klipper G-code and does
not activate this module. These files preserve the earlier experiment; its
physical verification is still outstanding.

Ordinary Klipper extrusion scales E velocity with toolhead velocity during
acceleration. Matching nominal E/time across G-code segments therefore does not
keep filament feed constant. This module instead queues a flat extruder velocity
over each move's actual acceleration, cruise, and deceleration time. Toolhead
cruise speed controls the requested bead width. Pressure advance must be zero.

The proposed command is `SET_CONTINUOUS_EXTRUSION RATE=<input filament mm/s>`.
The rate stays fixed until `RATE=0` finishes the path. The module rejects changes
to that rate, travel, retraction, stationary extrusion, and tool changes while
active. A detected gap between queued moves stops the experiment with a printer
shutdown. It also checks actual average deposition against Klipper's extrusion
cross-section limit after lookahead. This average limit does not establish safe
or accurate local bead widths at corners.

The proposed configuration section is `[continuous_extrusion]`, with optional
`extruder` and `max_filament_speed` keys. This describes the development interface;
installation and live printing have not been verified. All disabled hooks delegate
to Klipper's original methods. Ending a path flushes motion and rebases the
software E coordinate without requesting compensating extrusion.

The companion `continuous_extrusion.cfg` supplies the configuration section and a
`CONTINUOUS_EXTRUSION_CHECK` macro. The native export first uses stock
`SET_GCODE_VARIABLE` to require that macro, then calls it to check the module's
API version. A missing macro raises an error and stops a virtual-SD print before
the continuous object starts. An unknown custom command alone would not provide
that check. This has not been installed on Leo's printer.

## Verification

Run the Python unit tests without a printer or additional packages:

```powershell
python scripts/klipper/test_continuous_extrusion.py
```

Check the backend's queue requests against the `Move` and `LookAheadQueue`
classes from a local Klipper checkout:

```powershell
python scripts/klipper/check_motion_queue.py C:/path/to/klipper/klippy/toolhead.py
```

The check loads those two classes and reports the source SHA-256. It exercises
width changes and corners, including acceleration and deceleration. It does not
run Klipper's step generation, MCU communication, or motors.

On 2026-09-07, the official `toolhead.py` with SHA-256
`911d675604003f9cffc1356562de17c85c9bead3d744e53922a0d77d5dee1e3b`
passed five moves spanning 7.875780185 seconds. Every experimental extruder queue
request had a flat 0.5 mm/s velocity. Ordinary coordinated extrusion for the
same moves ranged from 0 to 0.5 mm/s.

## Work remaining

- Verify initialization and E rebasing in a full Klipper instance, including
  step generation and the exact printer version. Both legacy `move` and current
  `process_move` hooks are handled, but this is not a compatibility guarantee.
- Check interaction with transforms, pauses, cancellation, shutdown, pressure
  advance changes, and queue starvation. A queue interruption already breaks
  physical continuity; detecting it cannot repair the print.
- Include actual toolhead acceleration in the slicer's deposition assessment.
  Constant feed deposits more material per millimetre when the toolhead slows.
  Current planar coverage diagnostics do not model this effect.
- Validate start and finish behavior, motor step timing, and filament motion on
  Leo's printer, then calibrate the TPU width response to toolhead speed.

References: [Klipper extrusion kinematics](https://www.klipper3d.org/Kinematics.html#extruder-kinematics),
[extruder implementation](https://github.com/Klipper3d/klipper/blob/master/klippy/kinematics/extruder.py),
[toolhead implementation](https://github.com/Klipper3d/klipper/blob/master/klippy/toolhead.py).
