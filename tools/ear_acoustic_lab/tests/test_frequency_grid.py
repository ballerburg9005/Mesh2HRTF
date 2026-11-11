import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frequency_grid import log_overview, linear_overview, replace_frequency_vector
from run_analysis import PRESETS, FAST_PRESETS, export_frequency_settings


class FrequencyVectorTest(unittest.TestCase):

    def test_every_preset_can_export_before_frequency_rewrite(self):
        """Regression: Blender rejects 250 Hz / 300 Hz placeholder increments."""
        for preset, (low, high, step) in PRESETS.items():
            with self.subTest(preset=preset):
                mode, value = export_frequency_settings(preset)
                if preset in FAST_PRESETS or preset == 'Log overview 80 tones':
                    self.assertEqual((mode, value), ('Num steps', 80))
                    self.assertEqual(len(linear_overview(80, low, high)), 80)
                else:
                    self.assertEqual(mode, 'Step size')
                    self.assertEqual(value, step)
                    self.assertEqual((high - low) % value, 0)

    def test_fast_presets_have_safe_legacy_export_step_too(self):
        for preset in FAST_PRESETS:
            low, high, placeholder = PRESETS[preset]
            self.assertEqual((high - low) % placeholder, 0)

    def test_endpoint_exact_fast_broadband_grid(self):
        for high in (20000., 24000.):
            f = linear_overview(n=80, low=100, high=high)
            self.assertEqual(len(f), 80)
            self.assertEqual(f[0], 100.)
            self.assertEqual(f[-1], high)
            self.assertTrue(all(a < b for a,b in zip(f, f[1:])))

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
