import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis_tools import mic_grid, reference_transfer, find_resonances, save_analysis, audition_music


class AnalysisToolsTest(unittest.TestCase):
    def test_probe_grid_has_all_nodes_in_element(self):
        with tempfile.TemporaryDirectory() as d:
            p = mic_grid(Path(d) / 'MicProbe', [0, 0, 0], 'mm')
            self.assertEqual(p.joinpath('Nodes.txt').read_text().splitlines()[0], '3')
            self.assertEqual(p.joinpath('Elements.txt').read_text().splitlines()[0], '1')
            xyz = []
            for line in p.joinpath('Nodes.txt').read_text().splitlines()[1:]:
                xyz.append([float(x) for x in line.split()[1:4]])
            np.testing.assert_allclose(np.mean(xyz, axis=0), [0, 0, 0], atol=1e-12)

    def test_freefield_normalization_is_unity(self):
        f = np.linspace(500, 2500, 41)
        r = 0.120
        p = .1 * np.exp(1j * 2 * np.pi * f * r / 343) / (4 * np.pi * r)
        np.testing.assert_allclose(abs(reference_transfer(
            f, p, [0, 0, 0], [0, -120, 0], 'mm')), 1, atol=1e-12)

    def test_extreme_gain_peak_marked_unverified(self):
        f = np.arange(100., 10100., 100.)
        h = np.ones(len(f), dtype=complex)
        h[f == 6000.] = 1e5
        peaks = find_resonances(f, h)
        self.assertTrue(peaks[0]["unverified_spectrum"])

    def test_detect_synthetic_peak_graph_and_music(self):
        f = np.linspace(500, 3500, 301)
        h = 1 + 8 / (1 + 1j * (f - 1050) / 15)
        peaks = find_resonances(f, h)
        self.assertLess(abs(peaks[0]['frequency_hz'] - 1050), 20)
        with tempfile.TemporaryDirectory() as d:
            data = save_analysis(d, f, h, h, [0, 0, 0], [0, -120, 0], 'mm')
            self.assertTrue(Path(d, 'response.png').stat().st_size > 10000)
            self.assertTrue(Path(d, 'response.csv').stat().st_size > 10000)
            self.assertTrue(Path(d, 'pressure.npz').exists())
            sample_rate = 48000
            t = np.arange(sample_rate) / sample_rate
            infile = Path(d, 'music.wav')
            sf.write(infile, .04 * np.sin(2 * np.pi * 1050 * t), sample_rate)
            outfile = Path(d, 'music_audition.wav')
            audition_music(infile, f, h, outfile)
            y, rate = sf.read(outfile)
            self.assertEqual(rate, sample_rate)
            self.assertEqual(len(y), sample_rate)
            self.assertGreater(float(np.max(abs(y))), .1)
            self.assertLessEqual(float(np.max(abs(y))), .91)


if __name__ == '__main__':
    unittest.main()
