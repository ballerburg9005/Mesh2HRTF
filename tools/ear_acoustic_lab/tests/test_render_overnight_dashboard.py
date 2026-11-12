"""Test offline PNG + HTML overnight dashboard without NumCalc."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from render_overnight_dashboard import collect, render, _comparison


class OfflineOvernightDashboardTest(unittest.TestCase):
    def test_method_switch_and_dense_candidate_are_not_mislabeled(self):
        with tempfile.TemporaryDirectory() as folder:
            project=Path(folder)/"project"
            analysis=project/"analysis"
            batch=analysis/"overnight_bem_validation_steps_1_2"
            batch.mkdir(parents=True)
            (project/"earlab_config.json").write_text(json.dumps({
                "mic":[0,0,0],"source":[0,120,0],"units":"mm"}))
            (project/"parameters.json").write_text(json.dumps({
                "frequencies":[1800,3500],"speedOfSound":343.
            }))
            data={
                "1:sl8":{"status":"converged","gain_db":14.5,"phase_deg":0,
                         "true_relative_residual":8e-9},
                "1:ml8":{"status":"failed","reason":"wrong solver method"},
                "1:dense":{"status":"failed","reason":"CGS residual 5.0"},
                "2:ml8":{"status":"converged","gain_db":19.3,"phase_deg":0,
                         "true_relative_residual":9e-9},
                "2:sl8":{"status":"converged","gain_db":14.8,"phase_deg":0,
                         "true_relative_residual":9e-9},
            }
            (batch/"results.json").write_text(json.dumps(data))
            for step,variant,method in (
                (1,"ml8","Single level fast multipole BEM"),
                (1,"dense","Traditional BEM")):
                source=(batch/f"step{step:02d}_{variant}"/"project"/
                        "NumCalc"/"source_1")
                source.mkdir(parents=True)
                msg=(method+"\nCGS TRUE relative residual = 5.0\n"+
                     ("Iteration method CGS failed, direct method is used!\n"
                      if variant=="dense" else
                      "GMRES fallback: iterations = 120, true relative residual = 8e-9\n")+
                     "End time: done")
                (source/f"NC{step}-{step}.out").write_text(msg)
                grid=source/"be.out"/f"be.{step}"/"pEvalGrid"
                grid.parent.mkdir(parents=True)
                grid.write_text(
                    "Mesh2HRTF 1.3.0\n1\n1 3\n"+
                    "".join(f"{10000000+i} .1 -.1\n" for i in range(3)))
            rows,paths=collect(project)
            by={(x["step"],x["variant"]):x for x in rows}
            self.assertEqual(by[1,"ml8"]["status"],"auto_single")
            self.assertLess(by[1,"ml8"]["true_relative_residual"],1e-8)
            self.assertEqual(by[1,"dense"]["status"],"direct_unverified")
            self.assertIsNone(by[1,"dense"]["true_relative_residual"])
            comparison=_comparison(rows)
            self.assertIsNone(comparison[0]["ml_minus_sl_db"])
            self.assertAlmostEqual(comparison[1]["ml_minus_sl_db"],4.5)
            output=render(project)
            for filename in ("index.html","frequency_response.png",
                "method_disagreement.png","solver_status.png",
                "chart_data.json"):
                self.assertTrue((output/filename).is_file(),filename)
            html=(output/"index.html").read_text()
            self.assertIn("not a validated HRTF",html)
            self.assertIn("UNVERIFIED",html)
            self.assertIn("Auto-switched",html)
            self.assertEqual(len(paths),1)


if __name__=="__main__":
    unittest.main()
