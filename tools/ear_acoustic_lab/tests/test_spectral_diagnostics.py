import sys
import tempfile
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spectral_diagnostics import inspect_spectrum, inspect_numcalc_logs


class SpectralIntegrityTest(unittest.TestCase):
    def test_locates_start_of_suspicious_high_frequency_region(self):
        freq = np.arange(100., 10100., 100.)
        gain = 12 + .001 * freq
        gain[freq == 6000.] = 95.
        gain[freq == 6100.] = 25.
        diagnostic = inspect_spectrum(freq, gain)
        self.assertEqual(diagnostic["first_suspected_frequency_hz"], 5900.)
        self.assertGreaterEqual(diagnostic["large_jump_count"], 2)
        self.assertGreater(diagnostic["suspect_point_count"], 0)
        suspect_bands = [b for b in diagnostic["bands_500hz"]
                         if b["suspect_points"]]
        self.assertTrue(any(b["from_hz"] == 6000 for b in suspect_bands))
        self.assertEqual(diagnostic["bands_500hz"][-1]["to_hz"], 10500.)

    def test_smooth_spectrum_is_not_flagged(self):
        freq = np.linspace(100., 20000., 80)
        gain = 5. + 2.*np.cos(freq/2000.)
        diagnostic = inspect_spectrum(freq, gain)
        self.assertIsNone(diagnostic["first_suspected_frequency_hz"])
        self.assertEqual(diagnostic["large_jump_count"], 0)

    def test_reads_saved_numcalc_solver_residuals(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d)/"NumCalc"/"source_1"
            source.mkdir(parents=True)
            (source/"NC1-2.out").write_text(
                ">> S T E P   N U M B E R   A N D   F R E Q U E N C Y <<\n"
                "Step 1, Frequency = 5900 Hz\n"
                "CGS solver: number of iterations = 20, relative error = 1e-10\n"
                "Step 2, Frequency = 6000 Hz\n"
                "CGS solver: number of iterations = 500, relative error = 0.03\n"
            )
            results = inspect_numcalc_logs(d)
            self.assertEqual(results["steps_with_reported_residual"], 2)
            self.assertEqual(results["steps_over_threshold"], 1)
            self.assertEqual(results["steps_over_threshold_details"][0]["frequency_hz"], 6000.)
            self.assertAlmostEqual(results["max_reported_residual"], 0.03)


if __name__ == "__main__":
    unittest.main()
