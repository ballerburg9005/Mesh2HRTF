import json
from argparse import Namespace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import mesh2hrtf as m2h

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_analysis


class ResumeTest(unittest.TestCase):
    def test_reuses_project_and_skips_geometry_export(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = root / "ear_partially_done"
            solver = project / "NumCalc" / "source_1"
            solver.mkdir(parents=True)
            (solver / "NC.inp").write_text("NumCalc input stub")
            (project / "earlab_config.json").write_text(json.dumps({
                "mic": [0, 0, 0], "source": [0, 120, 0],
                "units": "mm", "stl": "~/model.stl"
            }))
            binary = root / "mesh2hrtf" / "NumCalc" / "bin" / "NumCalc"
            binary.parent.mkdir(parents=True)
            binary.touch()
            output2hrtf = project / "Output2HRTF"
            frequencies = np.arange(500, 1001, 100, dtype=float)
            dist = 0.12
            pressure = 0.1*np.exp(1j*2*np.pi*frequencies*dist/343.) / (
                4*np.pi*dist)
            def fake_output(*args):
                output2hrtf.mkdir(exist_ok=True)
                (output2hrtf/"HRTF_MicProbe.sofa").touch()
            args = Namespace(resume=str(project), mesh2hrtf=str(root), jobs=4)
            with patch.object(m2h, "manage_numcalc") as manager, \
                 patch.object(m2h, "output2hrtf", side_effect=fake_output), \
                 patch.object(run_analysis, "get_sofa_response",
                              return_value=(frequencies, pressure)), \
                 patch("reanalyze.render_project", return_value=project/"analysis"/"report.html"), \
                 patch.object(run_analysis, "prepare_snapshot",
                              return_value=root/"some_snapshot"):
                self.assertEqual(run_analysis.resume_project(args), 0)
            manager.assert_called_once()
            self.assertEqual(manager.call_args.kwargs["max_instances"], 4)
            self.assertTrue((project/"analysis"/"response.csv").is_file())

    def test_fails_closed_on_cached_nonconverged_pressure(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            source = project / "NumCalc" / "source_1"
            directory = source / "be.out" / "be.2"
            directory.mkdir(parents=True)
            (directory / "pEvalGrid").write_text("stale pressure values")
            (source / "NC2-2.out").write_text(
                "Step 2, Frequency = 6850 Hz\n"
                "CGS solver: number of iterations = 250, relative error = 11157.9\n")
            with self.assertRaisesRegex(RuntimeError, "cached NumCalc"):
                run_analysis.check_cached_solver_convergence(project)

    def test_allows_unfinished_or_converged_output(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            source = project / "NumCalc" / "source_1"
            directory = source / "be.out" / "be.2"
            directory.mkdir(parents=True)
            (directory / "pEvalGrid").write_text("converged")
            (source / "NC2-2.out").write_text(
                "Step 2, Frequency = 5000 Hz\n"
                "CGS solver: number of iterations = 124, relative error = 5.8e-10\n")
            run_analysis.check_cached_solver_convergence(project)


if __name__ == "__main__":
    unittest.main()
