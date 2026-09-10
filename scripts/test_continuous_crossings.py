import tempfile
from pathlib import Path
import unittest

from check_continuous_crossings import check


class CrossingAudit(unittest.TestCase):
    def audit(self, moves):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paths.gcode"
            path.write_text("G90\nG1 X0 Y0 Z0.2\n; CONTINUOUS_OBJECT_BEGIN\n;LAYER_CHANGE\n"
                            + moves + "\n; CONTINUOUS_OBJECT_END\n", encoding="utf-8")
            return check(path)["conflicts"]

    def test_transverse_crossing(self):
        self.assertEqual(self.audit("G1 X2 Y2 E1\nG1 X0 Y2 E1\nG1 X2 Y0 E1"), 1)

    def test_repeated_pass(self):
        self.assertEqual(self.audit("G1 X2 Y0 E1\nG1 X2 Y1 E1\nG1 X0 Y1 E1\n"
                                    "G1 X0 Y0 E1\nG1 X2 Y0 E1"), 1)

    def test_immediate_backtrack(self):
        self.assertEqual(self.audit("G1 X2 Y0 E1\nG1 X0 Y0 E1"), 1)

    def test_higher_layer_is_not_a_crossing(self):
        self.assertEqual(self.audit("G1 X2 Y2 E1\nG1 X0 Y2 Z0.4 E1\nG1 X2 Y0 E1"), 0)

    def test_rising_layer_is_not_a_flat_retrace(self):
        self.assertEqual(self.audit("G1 X2 Y0 E1\nG1 X2 Y1 E1\nG1 X0 Y1 E1\n"
                                    "G1 X0 Y0 E1\nG1 X2 Y0 Z0.201 E1"), 0)


if __name__ == "__main__":
    unittest.main()
