"""Regression tests for salvage of verified NumCalc outputs only."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from partial_response import export_partial_response, load_mic_probe


def write_probe(path, value="0.20"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Mesh2HRTF 1.3.0\n1\n1 3\n"
        + "".join(f"{10000000+i} {value} 0.0\n" for i in range(3)))


class PartialResponseTest(unittest.TestCase):
    def test_saves_only_completed_verified_samples_not_missing_frequencies(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "parameters.json").write_text(json.dumps({
                "numFrequencies": 4, "frequencies": [100, 200, 300, 400],
                "speedOfSound": 343.0
            }))
            (p / "earlab_config.json").write_text(json.dumps({
                "units": "mm", "mic": [0, 0, 0],
                "source": [0, 120, 0]
            }))
            source = p / "NumCalc" / "source_1"
            source.mkdir(parents=True)
            write_probe(source / "be.out" / "be.1" / "pEvalGrid")
            (source / "NC1-1.out").write_text(
                "CGS TRUE relative residual = 4e-9\nEnd time: finished")
            write_probe(source / "be.out" / "be.2" / "pEvalGrid")
            (source / "NC2-2.out").write_text(
                "GMRES fallback failed after 360 iterations\n"
                "CGS and GMRES failed convergence")
            write_probe(source / "be.out" / "be.3" / "pEvalGrid")
            (source / "NC3-3.out").write_text(
                "CGS solver: number of iterations = 140, relative error = 2e-10\n"
                "End time: finished")
            write_probe(source / "be.out" / "be.4" / "pEvalGrid")
            (source / "NC4-4.out").write_text(
                "CGS TRUE relative residual = 1.0\n"
                "GMRES fallback: iterations = 100, "
                "true relative residual = 9e-9\nEnd time: finished")
            report, path = export_partial_response(p)
            self.assertEqual(report["verified_points"], 2)
            self.assertEqual(report["total_project_steps"], 4)
            self.assertEqual(report["missing_or_failed_steps"], 1)
            self.assertEqual(report["completed_but_unverified"][0]["step"], 3)
            self.assertTrue(path.is_file())
            with path.open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([int(x["step"]) for x in rows], [1, 4])
            self.assertEqual(rows[1]["solver_method"], "GMRES")
            self.assertTrue((p / "analysis" / "partial_response.json").is_file())
            self.assertTrue((p / "analysis" / "partial_response.png").is_file())
            self.assertFalse((p / "analysis" / "response.csv").is_file())

    def test_rejects_malformed_microphone_grid(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pEvalGrid"
            path.write_text("Mesh2HRTF 1.3.0\n1\n1 3\n10000000 nan 0\n")
            with self.assertRaises(ValueError):
                load_mic_probe(path)


if __name__ == "__main__":
    unittest.main()
