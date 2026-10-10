"""Focused regression tests for the fail-closed Continuous Fermat validator."""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_gcode import validate_lines  # noqa: E402


BEGIN = ";_CONTINUOUS_FERMAT_BEGIN"
END = ";_CONTINUOUS_FERMAT_END"


def audit(gcode: str, **kwargs):
    normalize_firmware_state = kwargs.pop("normalize_firmware_state", True)
    firmware = kwargs.pop("firmware", "marlin2")
    normalized = textwrap.dedent(gcode).strip()
    if normalize_firmware_state:
        if firmware == "klipper":
            normalized = (
                "G90\nM83\nM220 S100\nM221 S100\n"
                "SET_PRESSURE_ADVANCE ADVANCE=0\nG28\nM190 S60\nM109 S200\n" + normalized
            )
        else:
            normalized = (
                "G21\nG90\nM83\nT0\nM200 D0\nM220 S100\nM221 S100\n"
                "M900 K0\nM413 S0\nG28\nM109 R200\n" + normalized
            )
    return validate_lines(
        normalized.splitlines(),
        Path("fixture.gcode"),
        0.001,
        1e-7,
        firmware=firmware,
        **kwargs,
    )


def messages(result) -> str:
    return "\n".join(result.violations)


def one_layer(body: str, *, preamble: str = "", postamble: str = "") -> str:
    return f"""
        G90
        M83
        G1 X0 Y0 Z0.2 F1200
        {preamble}
        ;LAYER_CHANGE
        {BEGIN}
        {body}
        {END}
        {postamble}
    """


class ContinuousFermatGCodeValidationTests(unittest.TestCase):
    def test_rejects_failed_geometric_certification_marker(self):
        fixture = one_layer("G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2")
        warning = ";_CONTINUOUS_FERMAT_VALIDATION_WARNING layer=0 exact_coverage=0.97<0.98"
        result = audit(fixture.replace(BEGIN, f"{warning}\n{BEGIN}"))

        self.assertIn("geometric safety certification failed", messages(result))

    def test_accepts_klipper_native_state_and_rejects_missing_guards(self):
        fixture = one_layer("G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2")
        accepted = audit(fixture, firmware="klipper")
        missing = audit(
            "G90\nM83\nM220 S100\nM221 S100\nG28\nM109 S200\n" + fixture,
            firmware="klipper",
            normalize_firmware_state=False,
        )
        active_pa = audit(
            "G90\nM83\nM220 S100\nM221 S100\n"
            "SET_PRESSURE_ADVANCE ADVANCE=0.05\nG28\nM190 S60\nM109 S200\n" + fixture,
            firmware="klipper",
            normalize_firmware_state=False,
        )

        self.assertEqual([], accepted.violations)
        self.assertIn("SET_PRESSURE_ADVANCE ADVANCE=0 was not explicitly established", messages(missing))
        self.assertIn("Klipper bed state was not established", messages(missing))
        self.assertIn("SET_PRESSURE_ADVANCE ADVANCE=0 was not explicitly established", messages(active_pa))

    def test_requires_explicit_firmware_extrusion_state_normalization(self):
        fixture = one_layer("G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2")
        missing = audit(fixture, normalize_firmware_state=False)
        unsafe = audit("M200 D1\nM220 S90\nM221 S110\nM413 S1\n" + fixture, normalize_firmware_state=False)

        self.assertIn("M200 D0 was not explicitly established", messages(missing))
        self.assertIn("M200 D0 was not explicitly established", messages(unsafe))
        self.assertIn("M220 S100 was not established", messages(unsafe))
        self.assertIn("M221 S100 was not established", messages(unsafe))
        self.assertIn("M413 S0 was not explicitly established", messages(missing))
        self.assertIn("M413 S0 was not explicitly established", messages(unsafe))

        modal_missing = messages(missing)
        self.assertIn("G21 millimetre units were not explicitly established", modal_missing)
        self.assertIn("T0 was not explicitly selected", modal_missing)

    def test_requires_homing_cooling_aware_wait_and_disabled_linear_advance(self):
        fixture = one_layer("G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2")
        prefix = "G21\nG90\nM83\nT0\nM200 D0\nM220 S100\nM221 S100\nM413 S0\n"

        missing = audit(prefix + fixture, normalize_firmware_state=False)
        heat_only = audit(
            prefix + "M900 K0\nG28\nM109 S200\n" + fixture,
            normalize_firmware_state=False,
        )
        partial_home = audit(
            prefix + "M900 K0\nG28 X0\nM109 R200\n" + fixture,
            normalize_firmware_state=False,
        )
        active_pa = audit(
            prefix + "M900 K0\nG28\nM109 R200\nM900 K0.2\n" + fixture,
            normalize_firmware_state=False,
        )
        pa_inside = audit(one_layer("M900 K0.2\nG1 X1 Y0 E0.2\nG1 X0 Y0 E0.2"))

        self.assertIn("M900 K0 was not explicitly established", messages(missing))
        self.assertIn("all-axis G28 was not established", messages(missing))
        self.assertIn("M109 R with a positive target", messages(missing))
        self.assertIn("M109 R with a positive target", messages(heat_only))
        self.assertIn("all-axis G28 was not established", messages(partial_home))
        self.assertIn("M900 K0 was not explicitly established", messages(active_pa))
        self.assertIn("M900 linear-advance change inside", messages(pa_inside))

    def test_accepts_continuous_sections_with_a_20_to_1_opening_scarf(self):
        result = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X10 Y0 E1
            G1 X10 Y10 E1
            G1 X0 Y10 E1
            G1 X0 Y0 E1
            {END}
            ;LAYER_CHANGE
            {BEGIN}
            G1 X4 Y0 Z0.4 E1
            G1 X8 Y0 E1
            G1 X8 Y8 E1
            G1 X0 Y8 E1
            G1 X0 Y0 E1
            {END}
            """,
            expected_sections=2,
            expected_layers=2,
        )

        self.assertEqual([], result.violations)
        self.assertEqual(2, len(result.section_records))
        self.assertEqual(1, len(result.transitions))
        self.assertAlmostEqual(0.2, result.transitions[0].z_delta)
        self.assertAlmostEqual(0.0, result.transitions[0].endpoint_gap)

    def test_rejects_non_extruding_motion_between_sections(self):
        result = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X10 Y0 E1
            G1 X10 Y10 E1
            G1 X0 Y10 E1
            G1 X0 Y0 E1
            {END}
            G1 X2 Y2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X6 Y2 Z0.4 E1
            G1 X8 Y2 E1
            G1 X8 Y8 E1
            G1 X2 Y8 E1
            G1 X2 Y2 E1
            {END}
            """,
            expected_sections=2,
            expected_layers=2,
        )

        self.assertIn("XY motion is not allowed between", messages(result))
        self.assertIn("adjacent section endpoint gap", messages(result))
        self.assertEqual(1, result.transitions[0].xy_moves)
        self.assertAlmostEqual(2.0 * 2.0**0.5, result.transitions[0].endpoint_gap)

    def test_g90_clears_m83_and_restores_absolute_e(self):
        result = audit(
            one_layer(
                """
                G1 X1 Y0 E0.5
                G90
                G1 X0 Y0 E0.4
                """
            )
        )

        self.assertIn("retracting XY move", messages(result))

    def test_g91_clears_m82_but_is_rejected_inside_a_section(self):
        result = audit(
            f"""
            G90
            M82
            G92 E5
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G91
            G1 X1 Y0 E0.2
            G1 X-1 Y0 E0.2
            {END}
            """
        )

        self.assertIn("M83 relative E was not explicitly established", messages(result))
        self.assertIn("G91 mode change inside Continuous Fermat section", messages(result))

    def test_start_purge_requires_explicit_exception_and_exception_is_narrow(self):
        fixture = one_layer(
            "G1 X0 Y0 E0.5\nG1 X5 Y0 E0.5",
            preamble="G1 X5 Y0 E1",
        )
        strict = audit(fixture)
        reviewed_start = audit(fixture, allow_unmarked_before_first_section=True)

        self.assertIn("positive-E XY print move before the first section", messages(strict))
        self.assertEqual([], reviewed_start.violations)

        after = audit(
            one_layer(
                "G1 X0 Y0 E0.5\nG1 X5 Y0 E0.5",
                preamble="G1 X5 Y0 E1",
                postamble="G1 X2 Y0 E0.2",
            ),
            allow_unmarked_before_first_section=True,
        )
        self.assertIn("positive-E XY print move outside a marked section", messages(after))

        unknown_start = audit(
            f"""
            G90
            M83
            G1 X5 Y0 E1
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """,
            allow_unmarked_before_first_section=True,
        )
        self.assertIn("purge starts from unknown XY", messages(unknown_start))
        self.assertIn("purge has no known positive feed rate", messages(unknown_start))

    def test_transition_rejects_retraction_and_all_axis_motion(self):
        result = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            G1 E-0.1
            G1 X2 Y0
            G1 Z0.1
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X2 Y0 E0.2
            {END}
            """
        )
        output = messages(result)

        self.assertIn("XY motion is not allowed between", output)
        self.assertIn("unmarked retraction between", output)
        self.assertIn("Z motion (", output)

    def test_transition_requires_a_known_positive_z_increase(self):
        result = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """
        )

        self.assertIn("scarf did not raise Z positively", messages(result))

    def test_rejects_a_steep_or_resumed_opening_scarf(self):
        first_section = f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X10 Y0 E1
            G1 X0 Y0 E1
            {END}
            ;LAYER_CHANGE
            {BEGIN}
        """
        steep = audit(first_section + f"""
            G1 X1 Y0 Z0.4 E1
            G1 X0 Y0 E1
            {END}
        """)
        resumed = audit(first_section + f"""
            G1 X4 Y0 Z0.3 E1
            G1 X8 Y0 E1
            G1 X12 Y0 Z0.4 E1
            G1 X0 Y0 E1
            {END}
        """)

        self.assertIn("steeper than the certified 20:1 slope", messages(steep))
        self.assertIn("resumed after the section reached flat-layer extrusion", messages(resumed))

    def test_every_section_requires_a_known_positive_constant_z(self):
        unknown = audit(
            f"""
            G90
            M83
            G1 X0 Y0 F1200
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """
        )
        zero = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0 F1200
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """
        )

        self.assertIn("Z is unknown", messages(unknown))
        self.assertIn("section Z must be positive", messages(zero))

    def test_requires_closed_section_endpoint(self):
        result = audit(one_layer("G1 X1 Y0 E0.2\nG1 X2 Y0 E0.2"))

        self.assertIn("section endpoint gap", messages(result))

    def test_rejects_each_non_positive_or_disconnected_in_section_move(self):
        cases = {
            "travel": ("G1 X1 Y0", "non-positive-extrusion XY move"),
            "e_only": ("G1 E0.2", "E-only extrusion/retract"),
            "z": ("G1 Z0.3", "first Continuous Fermat section must remain at constant Z"),
            "rapid": ("G0 X1 Y0 E0.2", "rapid G0 move"),
        }
        for name, (bad_line, expected) in cases.items():
            with self.subTest(name=name):
                result = audit(one_layer(f"{bad_line}\nG1 X0 Y0 E0.2"))
                self.assertIn(expected, messages(result))

    def test_rejects_non_finite_coordinates_extrusion_and_feed(self):
        cases = {
            "coordinate": ("G1 Xnan Y0 E0.2", "non-finite X value"),
            "extrusion": ("G1 X1 Y0 E1e309", "non-finite E value"),
            "feed": ("G1 X1 Y0 E0.2 F-inf", "non-finite F value"),
        }
        for name, (bad_line, expected) in cases.items():
            with self.subTest(name=name):
                result = audit(one_layer(f"{bad_line}\nG1 X0 Y0 E0.2"))
                self.assertIn(expected, messages(result))

        safe_mcode_with_nan = audit(one_layer("M204 Snan\nG1 X1 Y0 E0.2\nG1 X0 Y0 E0.2"))
        self.assertIn("non-finite S value", messages(safe_mcode_with_nan))

    def test_rejects_malformed_and_duplicate_motion_words(self):
        malformed = audit(one_layer("G1 Xoops Y0 E0.2\nG1 X0 Y0 E0.2"))
        duplicate = audit(one_layer("G1 X1 X2 Y0 E0.2\nG1 X0 Y0 E0.2"))

        self.assertIn("malformed G-code word", messages(malformed))
        self.assertIn("duplicate X word", messages(duplicate))

    def test_rejects_unmodeled_g_command_and_non_standalone_marker(self):
        unmodeled = audit(one_layer("G5 X1 Y0 E0.2\nG1 X0 Y0 E0.2"))
        shared_line = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            G1 F1000 {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """
        )

        self.assertIn("unsupported G-code command G5", messages(unmodeled))
        self.assertIn("begin marker must be on a standalone", messages(shared_line))

    def test_rejects_commandless_modal_motion_and_arcs(self):
        modal = audit(one_layer("X1 Y0 E0.2\nG1 X0 Y0 E0.2"))
        prefixed_modal = audit(one_layer("P0 X1 Y0 E0.2\nG1 X0 Y0 E0.2"))
        named_macro = audit(one_layer("PAUSE\nG1 X1 Y0 E0.2\nG1 X0 Y0 E0.2"))
        arc = audit(one_layer("G2 X1 Y0 I0.5 J0 E0.2\nG1 X0 Y0 E0.2"))

        self.assertIn("commandless axis words", messages(modal))
        self.assertIn("commandless axis words", messages(prefixed_modal))
        self.assertIn("named macro cannot be certified", messages(named_macro))
        self.assertIn("G2/G3 arcs are not certified", messages(arc))

        pre_model_arc = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                preamble="G2 X1 Y0 I0.5 J0 E0.2 F1200",
            ),
            allow_unmarked_before_first_section=True,
        )
        self.assertIn("G2/G3 arcs are not certified outside", messages(pre_model_arc))

    def test_rejects_zero_flow_override_and_unmodeled_coordinate_state(self):
        zero_flow = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                preamble="M221 S0",
            )
        )
        inches = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                preamble="G20",
            )
        )

        self.assertIn("non-positive M221 flow override", messages(zero_flow))
        self.assertIn("G20 before the first section changes unmodeled printer state", messages(inches))

        altered_flow = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                preamble="M221 S150",
            )
        )
        self.assertIn("non-unity M221 flow override", messages(altered_flow))

    def test_rejects_unmodeled_motion_axes(self):
        result = audit(one_layer("G1 X1 Y0 A10 E0.2\nG1 X0 Y0 E0.2"))

        self.assertIn("unsupported motion word(s) A", messages(result))

    def test_rejects_tool_and_filament_change_between_sections(self):
        result = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            G1 Z0.4
            T1
            M600
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """
        )

        self.assertIn("T1 is not allowed between", messages(result))
        self.assertIn("M600 is not allowed between", messages(result))

        mmu_text_tool = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                postamble="T?",
            )
        )
        self.assertIn("unparsed executable line", messages(mmu_text_tool))

    def test_rejects_opaque_commands_after_final_section(self):
        filament_change = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                postamble="M600",
            )
        )
        macro = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                postamble="M810",
            )
        )

        self.assertIn("M600 is not allowed after the final", messages(filament_change))
        self.assertIn("M810 is not allowed after the final", messages(macro))

        before = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                preamble="M98 P1",
            )
        )
        self.assertIn("M98 before the first section changes unmodeled printer state", messages(before))

    def test_allows_explicit_non_printing_shutdown_after_final_section(self):
        result = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                postamble="""
                G1 Z2.2
                M107
                M104 S0
                M140 S0
                M400
                """,
            )
        )

        self.assertEqual([], result.violations)

        unsafe = audit(
            one_layer(
                "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                postamble="G1 E-0.2\nG1 X20 Y20\nG91\nM220 S90\nM84",
            )
        )
        self.assertIn("E motion is not allowed after", messages(unsafe))
        self.assertIn("XY motion is not allowed after", messages(unsafe))
        self.assertIn("M84 is not part of the certified final shutdown", messages(unsafe))
        self.assertIn("G91 is not allowed after the final", messages(unsafe))
        self.assertIn("M220 is not allowed after the final", messages(unsafe))

    def test_rejects_hotend_temperature_changes_in_protected_spans(self):
        inside = audit(one_layer("M104 S0\nG1 X1 Y0 E0.2\nG1 X0 Y0 E0.2"))
        between = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            G1 Z0.4
            M109 R0
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """
        )

        self.assertIn("unsupported M-code command M104", messages(inside))
        self.assertIn("M109 is not allowed between", messages(between))

        for command in ("M104 S210", "M109 S210", "M104"):
            with self.subTest(postamble=command):
                after = audit(
                    one_layer(
                        "G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2",
                        postamble=command,
                    )
                )
                self.assertIn(f"{command.split()[0]} is not allowed after the final", messages(after))

    def test_expected_section_and_layer_accounting_is_exact(self):
        fixture = one_layer("G1 X1 Y0 E0.2\nG1 X0 Y0 E0.2")
        wrong_sections = audit(fixture, expected_sections=2)
        wrong_layers = audit(fixture, expected_layers=2)
        missing_layer_section = audit(
            fixture + "\n;LAYER_CHANGE\nG1 Z0.4",
            expected_layers=2,
        )

        self.assertIn("expected 2 Continuous Fermat sections, found 1", messages(wrong_sections))
        self.assertIn("expected 2 layer markers, found 1", messages(wrong_layers))
        self.assertIn("layer 2 contains 0 Continuous Fermat sections", messages(missing_layer_section))

        marker_inside_section = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            ;LAYER_CHANGE
            {BEGIN}
            G1 X1 Y0 E0.2
            ;LAYER_CHANGE
            G1 X0 Y0 E0.2
            {END}
            G1 Z0.4
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """,
            expected_sections=2,
            expected_layers=2,
        )
        self.assertIn("layer marker appears inside", messages(marker_inside_section))

    def test_expected_layers_rejects_a_section_before_any_layer_marker(self):
        result = audit(
            f"""
            G90
            M83
            G1 X0 Y0 Z0.2 F1200
            {BEGIN}
            G1 X1 Y0 E0.2
            G1 X0 Y0 E0.2
            {END}
            """,
            expected_layers=0,
        )

        self.assertIn("not associated with a preceding layer marker", messages(result))


if __name__ == "__main__":
    unittest.main()
