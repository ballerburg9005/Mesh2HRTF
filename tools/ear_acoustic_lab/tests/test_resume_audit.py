"""NumCalc step integrity tests for interrupted and rejected sweeps."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resume_audit import audit_project, save_audit


class ResumeAuditTest(unittest.TestCase):
    def test_logs_are_not_completion_and_failed_solve_stays_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            src = project / "NumCalc" / "source_1"
            src.mkdir(parents=True)
            (project / "parameters.json").write_text(json.dumps({
                "numFrequencies": 6,
                "frequencies": [100, 200, 300, 400, 500, 600]
            }))
            contents = {
                1: "CGS solver: number of iterations = 12, relative error = 1e-9\nEnd time: finished",
                2: "CGS solver: number of iterations = 250, relative error = 1e3\nGMRES fallback failed after 360 iterations",
                3: "CGS solver: number of iterations = 80, relative error = 1e-9",
                4: "End time: finished",
                5: "CGS and GMRES failed convergence. Frequency rejected\nEnd time: finished",
            }
            for step, content in contents.items():
                (src / f"NC{step}-{step}.out").write_text(content)
            for step in [1, 3, 5]:
                p = src / "be.out" / f"be.{step}" / "pEvalGrid"
                p.parent.mkdir(parents=True)
                p.write_text("valid format placeholder")
            report, path = save_audit(project)
            self.assertEqual(report["total"], 6)
            self.assertEqual(report["completed"], 1)
            self.assertEqual(report["unfinished"], 5)
            self.assertEqual(report["counts"]["solver_rejected"], 2)
            self.assertEqual(report["counts"]["partial_pressure"], 1)
            self.assertEqual(report["counts"]["postprocessing_missing"], 1)
            self.assertEqual(report["counts"]["not_started"], 1)
            self.assertTrue(path.is_file())

    def test_empty_pressure_file_is_not_complete(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            src = project / "NumCalc" / "source_1"
            p = src / "be.out" / "be.1" / "pEvalGrid"
            p.parent.mkdir(parents=True)
            p.touch()
            (src / "NC1-1.out").write_text("End time: finished")
            (project / "parameters.json").write_text(json.dumps({
                "numFrequencies": 1, "frequencies": [500]
            }))
            result = audit_project(project)
            self.assertEqual(result["completed"], 0)
            self.assertEqual(result["counts"]["postprocessing_missing"], 1)


if __name__ == "__main__":
    unittest.main()
