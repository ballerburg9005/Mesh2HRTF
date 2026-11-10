import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frequency_grid import log_overview, replace_frequency_vector


class FrequencyVectorTest(unittest.TestCase):
    def test_rewrite_numcalc_curve_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            path = project/"NumCalc"/"source_1"
            path.mkdir(parents=True)
            (path/"NC.inp").write_text(
                "Mesh2HRTF 1.3\n## Controlparameter II\n"
                "1 3 0.000001 0.00e+00 1 0 0\n##\n"
                "## Load Frequency Curve \n0 4\n0.000000 0.000000e+00 0.0\n"
                "0.000001 0.010000e+04 0.0\n"
                "0.000002 0.020000e+04 0.0\n"
                "0.000003 0.030000e+04 0.0\n##\n"
                "## 1. Main Parameters I\n")
            (project/"parameters.json").write_text(json.dumps({
                "frequencies": [100, 200, 300],
                "numFrequencies": 3, "frequencyStepSize": 100,
            }))
            f = log_overview(n=7, low=100, high=20000)
            self.assertEqual(replace_frequency_vector(project, f), 7)
            updated = (path/"NC.inp").read_text()
            self.assertIn("1 7 0.000001 0.00e+00 1 0 0", updated)
            self.assertIn("0 8\n0.000000 0.000000e+00 0.0", updated)
            self.assertIn("2.000000000000e+04", updated)
            self.assertIn("## 1. Main Parameters I", updated)
            p = json.loads((project/"parameters.json").read_text())
            self.assertEqual(len(p["frequencies"]), 7)
            self.assertEqual(p["frequencyStepSize"], 0.0)
            self.assertEqual(p["maxFrequency"], 20000.)
            self.assertGreater(p["frequencies"][2], p["frequencies"][1])

    def test_non_monotonic_frequencies_rejected(self):
        with self.assertRaises(ValueError):
            replace_frequency_vector("/tmp/unused", [100, 100, 200])


if __name__ == "__main__":
    unittest.main()
