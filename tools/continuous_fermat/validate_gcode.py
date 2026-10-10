#!/usr/bin/env python3
"""Fail-closed validation for integrated Continuous Fermat G-code sections.

The validator scans the whole file and models the modal state needed to audit
the marked paths. It supports explicit ``marlin2`` and ``klipper`` contracts;
their G90/G91 interaction with the independently tracked E mode is modeled
separately.

A passing file has one continuously extruding cyclic stroke per layer. After
the first flat stroke, each section begins exactly where the preceding one
ended and raises Z through a shallow positive-E XYZ scarf at the front of the
next cycle. No motion is accepted between marked strokes. Positive-E XY moves
outside the markers are rejected; a reviewed
purge in start G-code can be opted in explicitly, but the exception never
applies between sections or after the first section.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


BEGIN_MARKER = ";_CONTINUOUS_FERMAT_BEGIN"
END_MARKER = ";_CONTINUOUS_FERMAT_END"
VALIDATION_WARNING_MARKER = ";_CONTINUOUS_FERMAT_VALIDATION_WARNING"
LAYER_MARKERS = {"CHANGE_LAYER", "LAYER_CHANGE"}
MOTION_COMMANDS = {"G0", "G1", "G2", "G3"}
UNMODELED_EXTRUSION_COMMANDS = {"G5", "G6"}
FIRMWARE_RETRACT_COMMANDS = {"G10", "G11", "G22", "G23"}
FLOW_OVERRIDE_COMMANDS = {"M220", "M221"}
VOLUMETRIC_E_COMMAND = "M200"
POWER_LOSS_RECOVERY_COMMAND = "M413"
LINEAR_ADVANCE_COMMAND = "M900"
KLIPPER_PRESSURE_ADVANCE_COMMAND = "SET_PRESSURE_ADVANCE"
UNSAFE_COORDINATE_COMMANDS = {
    "G20",  # inch units; the validator deliberately certifies millimetres only
    "G52",  # local coordinate offset
    "G53",
    "G54",
    "G55",
    "G56",
    "G57",
    "G58",
    "G59",
    "M206",  # home offset
    "M290",  # babystepping
    "M428",  # home-offset application
}
OPAQUE_OR_MATERIAL_COMMANDS = {
    "G65",   # macro invocation
    "M32",   # run a file from SD
    "M98",   # subprogram/macro invocation
    "M600",  # filament change
    "M601",
    "M701",
    "M702",
    "M808",  # repeat marker
    *(f"M{number}" for number in range(810, 820)),  # Marlin G-code macros
}
SAFE_SECTION_M_COMMANDS = {
    "M106",  # fan
    "M107",
    "M204",  # acceleration
    "M205",  # advanced motion limits
    "M73",   # print progress
    "M117",  # display message
}
SAFE_TRANSITION_M_COMMANDS = SAFE_SECTION_M_COMMANDS | {
    "M140",  # non-blocking bed temperature
    "M190",  # blocking bed temperature
    "M400",  # wait for queued moves
}
SAFE_POSTAMBLE_M_COMMANDS = SAFE_TRANSITION_M_COMMANDS | {
    "M104",  # hotend shutdown is safe only after the final stroke
    "M109",
}
STRICT_PARSE_COMMANDS = (
    MOTION_COMMANDS
    | FIRMWARE_RETRACT_COMMANDS
    | FLOW_OVERRIDE_COMMANDS
    | (SAFE_POSTAMBLE_M_COMMANDS - {"M117"})
    | {
        "G90",
        "G91",
        "G92",
        "G28",
        "M104",
        "M109",
        VOLUMETRIC_E_COMMAND,
        POWER_LOSS_RECOVERY_COMMAND,
        LINEAR_ADVANCE_COMMAND,
    }
)
FINITE_WORDS = {"X", "Y", "Z", "E", "F", "I", "J", "K", "R", "S", "D"}

# ``nan`` and ``inf`` are accepted lexically so that they produce an explicit
# non-finite-value violation instead of being silently ignored.
NUMBER_TEXT = r"[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|nan|inf(?:inity)?)"
COMMAND_RE = re.compile(rf"^\s*(?:N\s*[+-]?\d+\s*)?([GMT])\s*({NUMBER_TEXT})", re.IGNORECASE)
WORD_RE = re.compile(rf"([A-Za-z])\s*({NUMBER_TEXT})", re.IGNORECASE)
COMMANDLESS_MOTION_RE = re.compile(
    rf"(?:^|\s)[XYZEFIJKRABCUVW]\s*{NUMBER_TEXT}",
    re.IGNORECASE,
)
NAMED_COMMAND_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b")
NAMED_PARAM_RE = re.compile(rf"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*({NUMBER_TEXT})", re.IGNORECASE)
PAREN_COMMENT_RE = re.compile(r"\([^)]*\)")


@dataclass
class MachineState:
    units_mm: bool | None = None
    absolute_xy: bool | None = None
    absolute_e: bool | None = None
    plane: str = "G17"
    x: float | None = None
    y: float | None = None
    z: float | None = None
    e: float = 0.0
    f: float | None = None
    speed_factor: float | None = None
    flow_factor: float | None = None
    volumetric_e_disabled: bool | None = None
    power_loss_recovery_disabled: bool | None = None
    linear_advance_disabled: bool | None = None
    all_axes_homed: bool = False
    hotend_ready_after_home: bool = False
    bed_ready_after_home: bool = False
    active_tool: int | None = None
    pressure_advance_disabled: bool | None = None


@dataclass
class ParsedCode:
    command: str = ""
    words: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class SectionState:
    index: int
    start_line: int
    layer_index: int | None
    start_xy: tuple[float, float] | None
    start_z: float | None
    previous_extrusion_end: tuple[float, float] | None = None
    first_extrusion_end: tuple[float, float] | None = None
    saw_z_extrusion: bool = False
    ramp_finished: bool = False
    extrusion_moves: int = 0


@dataclass
class SectionRecord:
    index: int
    start_line: int
    end_line: int
    layer_index: int | None
    start_xy: tuple[float, float] | None
    end_xy: tuple[float, float] | None
    start_z: float | None
    end_z: float | None
    extrusion_moves: int
    closure_gap: float | None


@dataclass
class PendingTransition:
    source: SectionRecord
    xy_lines: list[int] = field(default_factory=list)
    e_moves: list[tuple[int, float]] = field(default_factory=list)
    z_words: list[tuple[int, float | None]] = field(default_factory=list)
    coordinate_reset_lines: list[int] = field(default_factory=list)
    firmware_retract_lines: list[int] = field(default_factory=list)
    disruptive_lines: list[tuple[int, str]] = field(default_factory=list)
    postamble_disruptive_lines: list[tuple[int, str]] = field(default_factory=list)
    postamble_m_commands: list[tuple[int, str, dict[str, float]]] = field(default_factory=list)


@dataclass
class TransitionRecord:
    from_section: int
    to_section: int
    start_line: int
    end_line: int
    endpoint_gap: float | None
    z_delta: float | None
    xy_moves: int
    e_moves: int
    z_words: int


@dataclass
class UnmarkedExtrusion:
    line_no: int
    before_first_section: bool


@dataclass
class ValidationResult:
    path: Path
    sections: int = 0
    layers: int = 0
    extrusion_moves: int = 0
    unmarked_xy_moves: int = 0
    unmarked_e_moves: int = 0
    unmarked_retract_moves: int = 0
    unmarked_z_moves: int = 0
    unmarked_positive_xy_moves: int = 0
    section_records: list[SectionRecord] = field(default_factory=list)
    transitions: list[TransitionRecord] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def fail(self, line_no: int, message: str) -> None:
        self.violations.append(f"line {line_no}: {message}")

    def fail_global(self, message: str) -> None:
        self.violations.append(message)


def strip_comment(line: str) -> str:
    """Remove semicolon and parenthesized comments from one G-code line."""

    return PAREN_COMMENT_RE.sub("", line.split(";", 1)[0]).strip()


def comment_text(line: str) -> str | None:
    if ";" not in line:
        return None
    return line.split(";", 1)[1].strip()


def normalize_command(letter: str, number: str) -> str:
    try:
        value = float(number)
    except ValueError:
        return f"{letter.upper()}{number.upper()}"
    if math.isfinite(value) and value.is_integer():
        return f"{letter.upper()}{int(value)}"
    return f"{letter.upper()}{number.upper()}"


def parse_code(code: str, firmware: str = "marlin2") -> ParsedCode:
    """Parse the commands whose parameters affect this validator.

    Unknown/custom commands are intentionally ignored.  For a motion or modal
    command that we do model, however, malformed and duplicate words are an
    error rather than being silently dropped.
    """

    if not code:
        return ParsedCode()

    # A RepRap checksum covers everything before '*'.  It does not affect the
    # state calculation, so discard it after retaining the actual G-code.
    payload = code.split("*", 1)[0].rstrip()
    command_match = COMMAND_RE.match(payload)
    if command_match is None:
        named_match = NAMED_COMMAND_RE.match(payload)
        if named_match is None:
            return ParsedCode()
        command = named_match.group(1).upper()
        if firmware != "klipper" or command != KLIPPER_PRESSURE_ADVANCE_COMMAND:
            return ParsedCode()

        words: dict[str, float] = {}
        errors: list[str] = []
        position = named_match.end()
        while position < len(payload):
            while position < len(payload) and payload[position].isspace():
                position += 1
            if position >= len(payload):
                break
            param_match = NAMED_PARAM_RE.match(payload, position)
            if param_match is None:
                errors.append(f"malformed Klipper parameter near {payload[position:position + 16]!r}")
                break
            name = param_match.group(1).upper()
            if name in words:
                errors.append(f"duplicate {name} parameter")
            words[name] = float(param_match.group(2))
            position = param_match.end()
        return ParsedCode(command=command, words=words, errors=errors)

    command = normalize_command(command_match.group(1), command_match.group(2))
    if command not in STRICT_PARSE_COMMANDS:
        return ParsedCode(command=command)

    words: dict[str, float] = {}
    errors: list[str] = []
    position = command_match.end()
    while position < len(payload):
        while position < len(payload) and payload[position].isspace():
            position += 1
        if position >= len(payload):
            break
        word_match = WORD_RE.match(payload, position)
        if word_match is None:
            snippet = payload[position : position + 16]
            errors.append(f"malformed G-code word near {snippet!r}")
            break
        letter = word_match.group(1).upper()
        if letter in words:
            errors.append(f"duplicate {letter} word")
        try:
            words[letter] = float(word_match.group(2))
        except ValueError:
            errors.append(f"invalid numeric value for {letter}")
        position = word_match.end()

    return ParsedCode(command=command, words=words, errors=errors)


def parse_words(code: str) -> tuple[str, dict[str, float]]:
    """Compatibility wrapper retained for callers of the original helper."""

    parsed = parse_code(code)
    return parsed.command, parsed.words


def xy_position(state: MachineState) -> tuple[float, float] | None:
    if state.x is None or state.y is None:
        return None
    return state.x, state.y


def xy_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def axis_end(current: float | None, value: float, absolute: bool | None) -> float | None:
    if absolute is None:
        return None
    if absolute:
        return value
    if current is None:
        return None
    return current + value


def move_end(state: MachineState, words: dict[str, float]) -> tuple[float | None, float | None, float | None]:
    x, y, z = state.x, state.y, state.z
    if "X" in words:
        x = axis_end(state.x, words["X"], state.absolute_xy)
    if "Y" in words:
        y = axis_end(state.y, words["Y"], state.absolute_xy)
    if "Z" in words:
        z = axis_end(state.z, words["Z"], state.absolute_xy)
    return x, y, z


def extrusion_delta(state: MachineState, words: dict[str, float]) -> float:
    if "E" not in words:
        return 0.0
    if state.absolute_e is None:
        return math.nan
    return words["E"] - state.e if state.absolute_e else words["E"]


def update_state_after_move(
    state: MachineState,
    words: dict[str, float],
    end: tuple[float | None, float | None, float | None],
) -> None:
    state.x, state.y, state.z = end
    if "E" in words:
        if state.absolute_e is not None:
            state.e = words["E"] if state.absolute_e else state.e + words["E"]


def validate_transition(
    pending: PendingTransition,
    next_section: SectionState,
    state: MachineState,
    result: ValidationResult,
    xy_tolerance: float,
    z_tolerance: float,
) -> None:
    """Require adjacent marked strokes to meet with no intervening motion."""

    for line_no in pending.xy_lines:
        result.fail(line_no, "XY motion is not allowed between continuously extruded layer sections")
    for line_no, delta in pending.e_moves:
        kind = "retraction" if delta < 0.0 else "extrusion/unretraction"
        result.fail(line_no, f"unmarked {kind} between Continuous Fermat sections (delta E={delta:.9g})")
    for line_no in pending.firmware_retract_lines:
        result.fail(line_no, "firmware retract/unretract between Continuous Fermat sections")
    for line_no in pending.coordinate_reset_lines:
        result.fail(line_no, "G92 coordinate reset between Continuous Fermat sections")
    for line_no, command in pending.disruptive_lines:
        result.fail(line_no, f"{command} is not allowed between Continuous Fermat sections")

    for line_no, delta in pending.z_words:
        if delta is None:
            result.fail(line_no, "Z motion between sections starts from an unknown or non-finite position")
        elif abs(delta) > z_tolerance:
            result.fail(line_no, f"Z motion ({delta:.9g} mm) is not allowed between Continuous Fermat sections")

    source_z = pending.source.end_z
    target_z = state.z
    z_delta = None if source_z is None or target_z is None else target_z - source_z
    if z_delta is None:
        result.fail(next_section.start_line, "cannot compare section Z endpoints because a Z position is unknown")
    elif not math.isfinite(z_delta) or abs(z_delta) > z_tolerance:
        result.fail(next_section.start_line, f"section start is discontinuous in Z by {z_delta:.9g} mm")

    source_xy = pending.source.end_xy
    target_xy = xy_position(state)
    endpoint_gap = None
    if source_xy is None or target_xy is None:
        result.fail(next_section.start_line, "cannot compare adjacent section endpoints because XY is unknown")
    else:
        endpoint_gap = xy_distance(source_xy, target_xy)
        if endpoint_gap > xy_tolerance:
            result.fail(next_section.start_line, f"adjacent section endpoint gap {endpoint_gap:.6f} mm exceeds tolerance")

    result.transitions.append(
        TransitionRecord(
            from_section=pending.source.index,
            to_section=next_section.index,
            start_line=pending.source.end_line,
            end_line=next_section.start_line,
            endpoint_gap=endpoint_gap,
            z_delta=z_delta,
            xy_moves=len(pending.xy_lines),
            e_moves=len(pending.e_moves) + len(pending.firmware_retract_lines),
            z_words=len(pending.z_words),
        )
    )


def validate_accounting(
    result: ValidationResult,
    section_layer_counts: dict[int, int],
    sections_without_layer: list[int],
    layer_lines: dict[int, int],
    expected_sections: int | None,
    expected_layers: int | None,
) -> None:
    if expected_sections is not None and result.sections != expected_sections:
        result.fail_global(f"expected {expected_sections} Continuous Fermat sections, found {result.sections}")

    if expected_layers is None:
        return

    if result.layers != expected_layers:
        result.fail_global(f"expected {expected_layers} layer markers, found {result.layers}")
    for line_no in sections_without_layer:
        result.fail(line_no, "Continuous Fermat section is not associated with a preceding layer marker")
    for layer_index in range(1, result.layers + 1):
        count = section_layer_counts.get(layer_index, 0)
        if count != 1:
            line_no = layer_lines[layer_index]
            result.fail(line_no, f"layer {layer_index} contains {count} Continuous Fermat sections; expected exactly 1")


def validate_lines(
    lines: Iterable[str],
    path: Path,
    xy_tolerance: float,
    e_tolerance: float,
    *,
    z_tolerance: float = 1e-6,
    expected_sections: int | None = None,
    expected_layers: int | None = None,
    allow_unmarked_before_first_section: bool = False,
    firmware: str = "marlin2",
) -> ValidationResult:
    """Validate an iterable of G-code lines.

    The first four positional parameters retain the original API.  Strict
    accounting and the narrowly scoped start-G-code exception are keyword-only.
    """

    if firmware not in {"marlin2", "klipper"}:
        raise ValueError("firmware must be 'marlin2' or 'klipper'")

    for name, value in (
        ("xy_tolerance", xy_tolerance),
        ("e_tolerance", e_tolerance),
        ("z_tolerance", z_tolerance),
    ):
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{name} must be finite and non-negative")
    for name, value in (("expected_sections", expected_sections), ("expected_layers", expected_layers)):
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")

    state = MachineState(units_mm=True if firmware == "klipper" else None)
    result = ValidationResult(path=path)
    section: SectionState | None = None
    pending: PendingTransition | None = None
    current_layer: int | None = None
    first_begin_line: int | None = None
    unmarked_extrusions: list[UnmarkedExtrusion] = []
    unparsed_code_lines: list[tuple[int, bool]] = []
    unsupported_motion_word_lines: list[tuple[int, str]] = []
    unsafe_presection_commands: list[tuple[int, str]] = []
    section_layer_counts: dict[int, int] = {}
    sections_without_layer: list[int] = []
    layer_lines: dict[int, int] = {}

    for line_no, raw in enumerate(lines, start=1):
        code = strip_comment(raw)
        comment = comment_text(raw)
        if comment in LAYER_MARKERS:
            if section is not None:
                result.fail(line_no, "layer marker appears inside a Continuous Fermat section")
            result.layers += 1
            current_layer = result.layers
            layer_lines[current_layer] = line_no

        marker = f";{comment}" if comment is not None else ""
        if marker.startswith(VALIDATION_WARNING_MARKER):
            result.fail(
                line_no,
                "Continuous Fermat geometric safety certification failed; warning-marked G-code is diagnostic only",
            )
        if marker == BEGIN_MARKER:
            if code:
                result.fail(line_no, "Continuous Fermat begin marker must be on a standalone comment line")
            result.sections += 1
            if first_begin_line is None:
                first_begin_line = line_no
            if section is not None:
                result.fail(line_no, "nested Continuous Fermat begin marker")
                if current_layer is None:
                    sections_without_layer.append(line_no)
                else:
                    section_layer_counts[current_layer] = section_layer_counts.get(current_layer, 0) + 1
                continue

            if firmware == "marlin2":
                if state.volumetric_e_disabled is not True:
                    result.fail(line_no, "M200 D0 was not explicitly established before the protected sequence")
                if state.power_loss_recovery_disabled is not True:
                    result.fail(line_no, "M413 S0 was not explicitly established before the protected sequence")
                if state.linear_advance_disabled is not True:
                    result.fail(line_no, "M900 K0 was not explicitly established before the protected sequence")
            elif state.pressure_advance_disabled is not True:
                result.fail(line_no, "SET_PRESSURE_ADVANCE ADVANCE=0 was not explicitly established before the protected sequence")
            if not state.all_axes_homed:
                result.fail(line_no, "an exact all-axis G28 was not established before the protected sequence")
            if not state.hotend_ready_after_home:
                wait_form = "M109 R" if firmware == "marlin2" else "M109 S"
                result.fail(line_no, f"{wait_form} with a positive target was not established after the final G28")
            if firmware == "klipper" and not state.bed_ready_after_home:
                result.fail(line_no, "Klipper bed state was not established after G28 with positive M190 S or exact M140 S0")
            if state.units_mm is not True:
                result.fail(line_no, "G21 millimetre units were not explicitly established before the protected sequence")
            if state.absolute_xy is not True:
                result.fail(line_no, "G90 absolute XYZ was not explicitly established before the protected sequence")
            if state.absolute_e is not False:
                result.fail(line_no, "M83 relative E was not explicitly established after the final G90/G91")
            if firmware == "marlin2" and state.active_tool != 0:
                result.fail(line_no, "T0 was not explicitly selected before the protected sequence")
            if state.speed_factor is None or not math.isclose(state.speed_factor, 1.0, rel_tol=0.0, abs_tol=1e-9):
                result.fail(line_no, "M220 S100 was not established before the protected sequence")
            if state.flow_factor is None or not math.isclose(state.flow_factor, 1.0, rel_tol=0.0, abs_tol=1e-9):
                result.fail(line_no, "M221 S100 was not established before the protected sequence")

            start_xy = xy_position(state)
            section = SectionState(
                index=result.sections,
                start_line=line_no,
                layer_index=current_layer,
                start_xy=start_xy,
                start_z=state.z,
            )
            if current_layer is None:
                sections_without_layer.append(line_no)
            else:
                section_layer_counts[current_layer] = section_layer_counts.get(current_layer, 0) + 1
            if pending is not None:
                validate_transition(pending, section, state, result, xy_tolerance, z_tolerance)
                pending = None
            continue

        if marker == END_MARKER:
            if code:
                result.fail(line_no, "Continuous Fermat end marker must be on a standalone comment line")
            if section is None:
                result.fail(line_no, "Continuous Fermat end marker without matching begin")
                continue

            if section.extrusion_moves == 0:
                result.fail(section.start_line, "Continuous Fermat section contains no extrusion moves")
            end_xy = xy_position(state)
            closure_gap = None
            if section.start_xy is None or end_xy is None:
                result.fail(line_no, "cannot verify section closure because an XY endpoint is unknown")
            else:
                closure_gap = xy_distance(section.start_xy, end_xy)
                # A changing cross-section may need one short connector from
                # the preceding seam to the newly rotated cycle. In that form
                # the first extrusion endpoint is the cycle seam and must be
                # where the section ends. The C++ emitter independently bounds
                # that connector to 2.5 bead widths.
                if section.first_extrusion_end is not None:
                    closure_gap = min(closure_gap, xy_distance(section.first_extrusion_end, end_xy))
                if closure_gap > xy_tolerance:
                    result.fail(line_no, f"Continuous Fermat section endpoint gap {closure_gap:.6f} mm exceeds tolerance")

            if section.start_z is None or state.z is None:
                result.fail(line_no, "cannot verify section layer height because Z is unknown")
            else:
                if section.start_z <= z_tolerance:
                    result.fail(line_no, f"Continuous Fermat section Z must be positive, got {section.start_z:.9g} mm")
                section_z_delta = state.z - section.start_z
                if section.index == 1:
                    if abs(section_z_delta) > z_tolerance:
                        result.fail(line_no, f"first Continuous Fermat section changed Z by {section_z_delta:.9g} mm")
                elif not section.saw_z_extrusion or section_z_delta <= z_tolerance:
                    result.fail(line_no, f"Continuous Fermat scarf did not raise Z positively ({section_z_delta:.9g} mm)")
                if section.index > 1 and result.transitions:
                    result.transitions[-1].z_delta = section_z_delta

            record = SectionRecord(
                index=section.index,
                start_line=section.start_line,
                end_line=line_no,
                layer_index=section.layer_index,
                start_xy=section.start_xy,
                end_xy=end_xy,
                start_z=section.start_z,
                end_z=state.z,
                extrusion_moves=section.extrusion_moves,
                closure_gap=closure_gap,
            )
            result.section_records.append(record)
            result.extrusion_moves += section.extrusion_moves
            pending = PendingTransition(source=record)
            section = None
            continue

        parsed = parse_code(code, firmware)
        command, words = parsed.command, parsed.words
        if not command:
            if code:
                # Marlin can optionally execute axis words in the last modal
                # motion mode, even when another parameter precedes X/Y/E.
                # Named macros and nonnumeric MMU T commands are similarly
                # opaque, so every unparsed executable line is fail-closed.
                has_axis_word = COMMANDLESS_MOTION_RE.search(code) is not None
                unparsed_code_lines.append((line_no, has_axis_word))
            continue
        if parsed.errors:
            for error in parsed.errors:
                result.fail(line_no, error)
            continue

        non_finite = False
        for letter, value in words.items():
            if not math.isfinite(value):
                result.fail(line_no, f"non-finite {letter} value")
                non_finite = True
        if non_finite:
            continue

        # Marlin G90/G91 clear an M82/M83 override and apply to E as well as
        # XYZ. Klipper tracks the coordinate and extrusion modes independently.
        if command == "G90":
            if words:
                result.fail(line_no, "G90 normalization has unexpected words")
            if section is not None:
                result.fail(line_no, "G90 mode change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            state.absolute_xy = True
            if firmware == "marlin2":
                state.absolute_e = True
            continue
        if command == "G91":
            if words:
                result.fail(line_no, "G91 mode change has unexpected words")
            if section is not None:
                result.fail(line_no, "G91 mode change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            state.absolute_xy = False
            if firmware == "marlin2":
                state.absolute_e = False
            continue
        if command == "M82":
            if words:
                result.fail(line_no, "M82 mode change has unexpected words")
            if section is not None:
                result.fail(line_no, "M82 mode change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            state.absolute_e = True
            continue
        if command == "M83":
            if words:
                result.fail(line_no, "M83 mode change has unexpected words")
            if section is not None:
                result.fail(line_no, "M83 mode change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            state.absolute_e = False
            continue
        if command == "G21":
            # Millimetres are the only coordinate units certified here.
            if words:
                result.fail(line_no, "G21 normalization has unexpected words")
            if section is not None:
                result.fail(line_no, "G21 unit change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            state.units_mm = True
            continue
        if command in {"G17", "G18", "G19"}:
            state.plane = command
            continue

        if command in UNSAFE_COORDINATE_COMMANDS:
            if section is not None:
                result.fail(line_no, f"unsupported coordinate-state command {command} inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            else:
                unsafe_presection_commands.append((line_no, command))
            continue

        if command in OPAQUE_OR_MATERIAL_COMMANDS:
            if section is not None:
                result.fail(line_no, f"opaque/material command {command} inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            else:
                unsafe_presection_commands.append((line_no, command))
            continue

        if command == "G28":
            if section is not None:
                result.fail(line_no, "homing command G28 inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            state.x = state.y = state.z = None
            state.all_axes_homed = not words
            state.hotend_ready_after_home = False
            state.bed_ready_after_home = False
            continue

        if command == "G92":
            reset_axes = FINITE_WORDS.intersection(words).intersection({"X", "Y", "Z", "E"})
            if section is not None and reset_axes:
                result.fail(line_no, "G92 coordinate reset inside Continuous Fermat section")
            elif pending is not None and reset_axes:
                pending.coordinate_reset_lines.append(line_no)
            if "X" in words:
                state.x = words["X"]
            if "Y" in words:
                state.y = words["Y"]
            if "Z" in words:
                state.z = words["Z"]
            if "E" in words:
                state.e = words["E"]
            if section is None and pending is None:
                state.all_axes_homed = False
                state.hotend_ready_after_home = False
            continue

        if command in FIRMWARE_RETRACT_COMMANDS:
            if section is not None:
                result.fail(line_no, f"firmware retract/unretract command {command} inside Continuous Fermat section")
            else:
                result.unmarked_retract_moves += 1
                if pending is not None:
                    pending.firmware_retract_lines.append(line_no)
            continue

        if command in FLOW_OVERRIDE_COMMANDS:
            if set(words) != {"S"}:
                result.fail(line_no, f"{command} normalization must contain only S")
                continue
            if "S" not in words:
                result.fail(line_no, f"{command} flow/speed override has no S value")
                continue
            factor = words["S"] / 100.0
            if command == "M220":
                state.speed_factor = factor
            else:
                state.flow_factor = factor
            if section is not None:
                result.fail(line_no, f"{command} override change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            continue

        if command == VOLUMETRIC_E_COMMAND:
            if set(words) != {"D"}:
                result.fail(line_no, "M200 normalization must contain only D")
                continue
            if "D" not in words:
                result.fail(line_no, "M200 volumetric extrusion command has no D value")
                continue
            state.volumetric_e_disabled = abs(words["D"]) <= e_tolerance
            if section is not None:
                result.fail(line_no, "M200 extrusion-mode change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            continue

        if command == POWER_LOSS_RECOVERY_COMMAND:
            if set(words) != {"S"}:
                result.fail(line_no, "M413 normalization must contain only S")
                continue
            if "S" not in words:
                result.fail(line_no, "M413 power-loss-recovery command has no S value")
                continue
            state.power_loss_recovery_disabled = abs(words["S"]) <= e_tolerance
            if section is not None:
                result.fail(line_no, "M413 power-loss-recovery change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            continue

        if command == LINEAR_ADVANCE_COMMAND:
            if set(words) != {"K"}:
                result.fail(line_no, "M900 normalization must contain only K")
                state.linear_advance_disabled = False
                continue
            state.linear_advance_disabled = abs(words["K"]) <= e_tolerance
            if section is not None:
                result.fail(line_no, "M900 linear-advance change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            continue

        if command == KLIPPER_PRESSURE_ADVANCE_COMMAND:
            if set(words) != {"ADVANCE"}:
                result.fail(line_no, "SET_PRESSURE_ADVANCE normalization must contain only ADVANCE")
                state.pressure_advance_disabled = False
                continue
            state.pressure_advance_disabled = abs(words["ADVANCE"]) <= e_tolerance
            if section is not None:
                result.fail(line_no, "Klipper pressure-advance change inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            continue

        if command in {"M104", "M109"} and section is None and pending is None:
            if command == "M109":
                target_word = "R" if firmware == "marlin2" else "S"
                state.hotend_ready_after_home = (
                    state.all_axes_homed
                    and set(words) == {target_word}
                    and words[target_word] > e_tolerance
                )
            else:
                state.hotend_ready_after_home = False
            continue

        if command in {"M140", "M190"} and section is None and pending is None and firmware == "klipper":
            state.bed_ready_after_home = (
                state.all_axes_homed
                and set(words) == {"S"}
                and ((command == "M190" and words["S"] > e_tolerance) or
                     (command == "M140" and abs(words["S"]) <= e_tolerance))
            )
            continue

        if command.startswith("T"):
            try:
                state.active_tool = int(command[1:])
            except ValueError:
                state.active_tool = None
            if section is not None:
                result.fail(line_no, f"tool selection/change {command} inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
            elif command != "T0":
                unsafe_presection_commands.append((line_no, command))
            continue

        if command.startswith("M"):
            if section is not None:
                if command not in SAFE_SECTION_M_COMMANDS:
                    result.fail(line_no, f"unsupported M-code command {command} inside Continuous Fermat section")
            elif pending is not None:
                pending.postamble_m_commands.append((line_no, command, dict(words)))
                if command not in SAFE_TRANSITION_M_COMMANDS:
                    pending.disruptive_lines.append((line_no, command))
                    safe_hotend_shutdown = (
                        command in {"M104", "M109"}
                        and "S" in words
                        and abs(words["S"]) <= z_tolerance
                    )
                    if command not in SAFE_POSTAMBLE_M_COMMANDS or (
                        command in {"M104", "M109"} and not safe_hotend_shutdown
                    ):
                        pending.postamble_disruptive_lines.append((line_no, command))
            continue

        # A strict section cannot certify movement semantics for other G-code
        # families (probing, splines, canned cycles, dwell, unit changes, and
        # vendor extensions). Treat them as disruptive rather than silently
        # assuming that they do not move an axis.
        if command.startswith("G") and command not in MOTION_COMMANDS:
            if section is not None:
                result.fail(line_no, f"unsupported G-code command {command} inside Continuous Fermat section")
            elif pending is not None:
                pending.disruptive_lines.append((line_no, command))
                pending.postamble_disruptive_lines.append((line_no, command))
                if command in UNMODELED_EXTRUSION_COMMANDS:
                    result.fail(line_no, f"unmodeled extrusion-capable command {command} outside a marked section")
            elif first_begin_line is not None:
                result.fail(line_no, f"unsupported G-code command {command} after the first Continuous Fermat marker")
            elif command in UNMODELED_EXTRUSION_COMMANDS:
                unsafe_presection_commands.append((line_no, command))
            continue

        if command not in MOTION_COMMANDS:
            continue

        allowed_motion_words = {"X", "Y", "Z", "E", "F"}
        if command in {"G2", "G3"}:
            allowed_motion_words |= {"I", "J", "K", "R"}
        unsupported_motion_words = sorted(set(words) - allowed_motion_words)
        if unsupported_motion_words:
            unsupported_motion_word_lines.append((line_no, ",".join(unsupported_motion_words)))
            continue

        if "F" in words:
            if words["F"] <= 0.0:
                result.fail(line_no, f"feed rate F must be positive, got {words['F']:.9g}")
            else:
                state.f = words["F"]

        start_xy = xy_position(state)
        start_z = state.z
        end_x, end_y, end_z = move_end(state, words)
        de = extrusion_delta(state, words)
        new_e = (
            words.get("E")
            if state.absolute_e is True
            else state.e + words.get("E", 0.0)
            if state.absolute_e is False
            else None
        )
        if "E" in words and state.absolute_e is None:
            result.fail(line_no, "E motion occurred before G90/G91 plus M82/M83 established extrusion mode")

        computed_values = {"X": end_x, "Y": end_y, "Z": end_z}
        computed_invalid = False
        for letter, value in computed_values.items():
            if value is not None and not math.isfinite(value):
                result.fail(line_no, f"computed {letter} position is non-finite")
                computed_invalid = True
        if "E" in words and (new_e is None or not math.isfinite(new_e) or not math.isfinite(de)):
            result.fail(line_no, "computed E position or extrusion delta is non-finite")
            computed_invalid = True
        if computed_invalid:
            continue

        end_xy = None if end_x is None or end_y is None else (end_x, end_y)
        has_known_xy = start_xy is not None and end_xy is not None
        is_arc = command in {"G2", "G3"}
        if is_arc and section is None:
            result.fail(line_no, "G2/G3 arcs are not certified outside Continuous Fermat sections")
        has_xy_word = "X" in words or "Y" in words
        has_xy_motion = is_arc or (
            has_xy_word and (not has_known_xy or xy_distance(start_xy, end_xy) > xy_tolerance)
        )
        has_z_word = "Z" in words
        z_delta = None if start_z is None or end_z is None else end_z - start_z
        has_z_motion = has_z_word and (z_delta is None or abs(z_delta) > z_tolerance)

        if section is not None:
            if command == "G0":
                result.fail(line_no, "rapid G0 move inside Continuous Fermat section")
            if is_arc:
                result.fail(line_no, "G2/G3 arcs are not certified inside Continuous Fermat sections")
                if state.plane != "G17":
                    result.fail(line_no, f"arc inside Continuous Fermat section uses unsupported plane {state.plane}")
                if not ({"I", "J", "R"} & words.keys()):
                    result.fail(line_no, "arc inside Continuous Fermat section has no I/J/R geometry")
            if has_z_motion:
                if section.index == 1:
                    result.fail(line_no, "first Continuous Fermat section must remain at constant Z")
                if not has_xy_motion or de <= e_tolerance:
                    result.fail(line_no, "inter-layer Z scarf must be a positive-E XY extrusion move")
                if z_delta is None or z_delta <= z_tolerance:
                    result.fail(line_no, "inter-layer Z scarf must increase Z monotonically")
                elif has_known_xy:
                    xy_length = xy_distance(start_xy, end_xy)
                    if z_delta > 0.051 * xy_length + z_tolerance:
                        result.fail(line_no, "inter-layer Z scarf is steeper than the certified 20:1 slope")
                if section.ramp_finished:
                    result.fail(line_no, "inter-layer Z scarf resumed after the section reached flat-layer extrusion")
                section.saw_z_extrusion = True
            elif has_xy_motion and section.saw_z_extrusion:
                section.ramp_finished = True

            if "E" in words and abs(de) > e_tolerance and not has_xy_motion:
                result.fail(line_no, "E-only extrusion/retract inside Continuous Fermat section")
            if has_xy_motion:
                if de < -e_tolerance:
                    result.fail(line_no, "retracting XY move inside Continuous Fermat section")
                elif de <= e_tolerance:
                    result.fail(line_no, f"non-positive-extrusion XY move inside Continuous Fermat section ({command})")
                else:
                    if state.f is None or not math.isfinite(state.f) or state.f <= 0.0:
                        result.fail(line_no, "extrusion move has no known positive feed rate")
                    if state.speed_factor is None:
                        result.fail(line_no, "extrusion move has no established M220 speed override")
                    elif state.speed_factor <= 0.0:
                        result.fail(line_no, "extrusion move has a non-positive M220 speed override")
                    elif not math.isclose(state.speed_factor, 1.0, rel_tol=0.0, abs_tol=1e-9):
                        result.fail(line_no, "extrusion move has a non-unity M220 speed override")
                    if state.flow_factor is None:
                        result.fail(line_no, "extrusion move has no established M221 flow override")
                    elif state.flow_factor <= 0.0:
                        result.fail(line_no, "extrusion move has a non-positive M221 flow override")
                    elif not math.isclose(state.flow_factor, 1.0, rel_tol=0.0, abs_tol=1e-9):
                        result.fail(line_no, "extrusion move has a non-unity M221 flow override")
                    if not has_known_xy:
                        result.fail(line_no, "extrusion move starts from unknown XY position")
                    else:
                        if section.first_extrusion_end is None:
                            section.first_extrusion_end = end_xy
                        if section.previous_extrusion_end is not None:
                            gap = xy_distance(section.previous_extrusion_end, start_xy)
                            if gap > xy_tolerance:
                                result.fail(line_no, f"extrusion continuity gap {gap:.6f} mm before move")
                        section.previous_extrusion_end = end_xy
                    section.extrusion_moves += 1
        else:
            before_first = first_begin_line is None
            if has_xy_motion:
                result.unmarked_xy_moves += 1
                if pending is not None:
                    pending.xy_lines.append(line_no)
            if "E" in words and abs(de) > e_tolerance:
                result.unmarked_e_moves += 1
                if de < -e_tolerance:
                    result.unmarked_retract_moves += 1
                if pending is not None:
                    pending.e_moves.append((line_no, de))
            if has_z_motion:
                result.unmarked_z_moves += 1
            if pending is not None and has_z_word:
                pending.z_words.append((line_no, z_delta))
            if has_xy_motion and de > e_tolerance:
                result.unmarked_positive_xy_moves += 1
                unmarked_extrusions.append(UnmarkedExtrusion(line_no, before_first))
                if before_first and allow_unmarked_before_first_section:
                    if command != "G1":
                        result.fail(line_no, "reviewed pre-section purge must use linear G1 motion")
                    if not has_known_xy:
                        result.fail(line_no, "reviewed pre-section purge starts from unknown XY")
                    if state.f is None or state.f <= 0.0:
                        result.fail(line_no, "reviewed pre-section purge has no known positive feed rate")
                    if start_z is None or start_z <= z_tolerance or has_z_motion:
                        result.fail(line_no, "reviewed pre-section purge requires a known positive constant Z")

        update_state_after_move(state, words, (end_x, end_y, end_z))

    if section is not None:
        result.fail(section.start_line, "unterminated Continuous Fermat section")
    if result.sections == 0:
        result.fail_global("no Continuous Fermat sections found")
    else:
        for line_no, has_axis_word in unparsed_code_lines:
            if has_axis_word:
                result.fail(line_no, "commandless axis words are unsafe with Marlin modal motion modes")
            else:
                result.fail(line_no, "unparsed executable line or named macro cannot be certified")
        for line_no, letters in unsupported_motion_word_lines:
            result.fail(line_no, f"unsupported motion word(s) {letters} cannot be certified")
        for line_no, command in unsafe_presection_commands:
            result.fail(line_no, f"{command} before the first section changes unmodeled printer state")
        for event in unmarked_extrusions:
            if event.before_first_section and allow_unmarked_before_first_section:
                continue
            location = "before the first section" if event.before_first_section else "outside a marked section"
            result.fail(event.line_no, f"positive-E XY print move {location}")
        if pending is not None:
            for line_no, command in pending.postamble_disruptive_lines:
                result.fail(line_no, f"{command} is not allowed after the final Continuous Fermat section")
            for line_no in pending.xy_lines:
                result.fail(line_no, "XY motion is not allowed after the final Continuous Fermat section")
            for line_no, _delta in pending.e_moves:
                result.fail(line_no, "E motion is not allowed after the final Continuous Fermat section")
            for line_no in pending.coordinate_reset_lines:
                result.fail(line_no, "coordinate reset is not allowed after the final Continuous Fermat section")
            for line_no in pending.firmware_retract_lines:
                result.fail(line_no, "firmware retract is not allowed after the final Continuous Fermat section")
            for line_no, delta in pending.z_words:
                if delta is None or delta <= z_tolerance or delta > 2.0 + z_tolerance:
                    result.fail(line_no, "final clearance move must be a positive Z-only lift of at most 2 mm")
            if len(pending.z_words) > 1:
                result.fail_global("final postamble contains more than one Z move")
            for line_no, command, command_words in pending.postamble_m_commands:
                safe = (
                    (command in {"M104", "M140", "M106"} and set(command_words) == {"S"} and abs(command_words["S"]) <= z_tolerance)
                    or (command in {"M107", "M400"} and not command_words)
                    or (command == "M73" and set(command_words).issubset({"P", "R"}))
                )
                if not safe:
                    result.fail(line_no, f"{command} is not part of the certified final shutdown")

    validate_accounting(
        result,
        section_layer_counts,
        sections_without_layer,
        layer_lines,
        expected_sections,
        expected_layers,
    )
    return result


def validate_file(
    path: Path,
    xy_tolerance: float,
    e_tolerance: float,
    *,
    z_tolerance: float = 1e-6,
    expected_sections: int | None = None,
    expected_layers: int | None = None,
    allow_unmarked_before_first_section: bool = False,
    firmware: str = "marlin2",
) -> ValidationResult:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return validate_lines(
            fh,
            path,
            xy_tolerance,
            e_tolerance,
            z_tolerance=z_tolerance,
            expected_sections=expected_sections,
            expected_layers=expected_layers,
            allow_unmarked_before_first_section=allow_unmarked_before_first_section,
            firmware=firmware,
        )


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def finite_non_negative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed validation of ;_CONTINUOUS_FERMAT_* G-code sections.")
    parser.add_argument("gcode", nargs="+", type=Path, help="G-code file(s) to validate")
    parser.add_argument("--xy-tolerance", type=finite_non_negative_float, default=0.001, help="XY continuity tolerance in mm")
    parser.add_argument("--z-tolerance", type=finite_non_negative_float, default=1e-6, help="Z motion tolerance in mm")
    parser.add_argument("--e-tolerance", type=finite_non_negative_float, default=1e-7, help="extrusion delta tolerance")
    parser.add_argument("--expected-sections", type=non_negative_int, help="require exactly this many marked sections")
    parser.add_argument(
        "--firmware",
        choices=("marlin2", "klipper"),
        default="marlin2",
        help="firmware-specific protected startup and modal contract",
    )
    parser.add_argument(
        "--expected-layers",
        type=non_negative_int,
        help="require this many Orca layer markers and exactly one marked section per layer",
    )
    parser.add_argument(
        "--allow-unmarked-before-first-section",
        action="store_true",
        help="allow reviewed positive-E XY purge moves only before the first marker (never between/after sections)",
    )
    parser.add_argument(
        "--allow-unknown-counts",
        action="store_true",
        help="diagnostic only: run without exact expected layer/section accounting",
    )
    args = parser.parse_args()
    if not args.allow_unknown_counts and (args.expected_sections is None or args.expected_layers is None):
        parser.error(
            "printer-bound validation requires both --expected-sections and --expected-layers; "
            "use --allow-unknown-counts only for diagnostics"
        )

    failed = False
    for path in args.gcode:
        result = validate_file(
            path,
            args.xy_tolerance,
            args.e_tolerance,
            z_tolerance=args.z_tolerance,
            expected_sections=args.expected_sections,
            expected_layers=args.expected_layers,
            allow_unmarked_before_first_section=args.allow_unmarked_before_first_section,
            firmware=args.firmware,
        )
        behavior = (
            f"outside XY/E/R/Z={result.unmarked_xy_moves}/"
            f"{result.unmarked_e_moves}/{result.unmarked_retract_moves}/{result.unmarked_z_moves}"
        )
        if result.violations:
            failed = True
            print(
                f"{path}: FAIL ({result.sections} sections, {result.layers} layers, "
                f"{result.extrusion_moves} extrusion moves; {behavior})"
            )
            for violation in result.violations:
                print(f"  {violation}")
        else:
            print(
                f"{path}: OK ({result.sections} sections, {result.layers} layers, "
                f"{result.extrusion_moves} extrusion moves; {behavior})"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
