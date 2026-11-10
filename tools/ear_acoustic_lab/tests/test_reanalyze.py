import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reanalyze


class TestRetrospectiveReport(unittest.TestCase):
    def test_select_spatial_points_stays_on_solved_frequencies(self):
        freq = np.array([500., 600., 700., 800.])
        db = np.array([0., 0., 5., 0.])
        self.assertEqual(reanalyze._select_spatial_frequencies(freq, db), [0, 2, 3])
        self.assertEqual(reanalyze._select_spatial_frequencies(freq[:1], db[:1]), [0])

    def test_build_report_without_running_solver(self):
        with tempfile.TemporaryDirectory() as t:
            project = Path(t) / "ear_old_result"
            (project / "Output2HRTF").mkdir(parents=True)
            (project / "Output2HRTF" / "HRTF_MicProbe.sofa").touch()
            cfg = {
                "mic": [0, 0, 0], "source": [0, 120, 0],
                "units": "mm", "preset": "Quick ~1kHz",
            }
            (project / "earlab_config.json").write_text(json.dumps(cfg))
            freq = np.arange(500., 3500.1, 100.)
            r = .12
            free = .1 * np.exp(1j * 2*np.pi*freq*r/343.)/(4*np.pi*r)
            coloration = 18. + .4*np.cos(freq/500)
            simulated_pressure = free * 10**(coloration/20.)

            with patch.object(reanalyze, "get_sofa_response",
                              return_value=(freq, simulated_pressure)), \
                 patch.object(reanalyze, "_surface_maps",
                              return_value=(None, [], "No test boundary field")):
                report = reanalyze.render_project(project)
            self.assertTrue(report.is_file())
            data = json.loads((project/"analysis"/"diagnostics.json").read_text())
            self.assertEqual(data["samples"], 31)
            self.assertEqual(data["frequency_step_median_hz"], 100.)
            self.assertGreater(data["gain_median_db_relative_to_freefield"], 17.)
            self.assertTrue(any("COARSE SWEEP" in w for w in data["warnings"]))
            self.assertTrue(any("PARTIAL SPECTRUM" in w for w in data["warnings"]))
            html = report.read_text()
            self.assertIn("spectral coloration", html.lower())
            self.assertIn("COARSE SWEEP", html)
            self.assertIn("frequency table", html.lower())
            for name in ["response.png", "diagnostic_samples.csv", "diagnostics.json"]:
                self.assertTrue((project/"analysis"/name).is_file())
            self.assertGreater((project/"analysis"/"response.png").stat().st_size, 20000)


if __name__ == "__main__":
    unittest.main()
