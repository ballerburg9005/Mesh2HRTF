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
            (project / "parameters.json").write_text(json.dumps({
                "numFrequencies": 1, "frequencies": [500]
            }))
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
            with patch.object(run_analysis, "call"), \
                 patch.object(m2h, "manage_numcalc") as manager, \
                 patch.object(m2h, "output2hrtf", side_effect=fake_output), \
                 patch.object(run_analysis, "get_sofa_response",
                              return_value=(frequencies, pressure)), \
                 patch("reanalyze.render_project", return_value=project/"analysis"/"report.html"), \
                 patch.object(run_analysis, "prepare_snapshot",
                              return_value=root/"some_snapshot"):
                self.assertEqual(run_analysis.resume_project(args), 0)
            manager.assert_called_once()
            self.assertTrue(manager.call_args.kwargs["stop_on_failed_step"])
            self.assertEqual(manager.call_args.kwargs["max_instances"], 2)
            self.assertTrue((project/"analysis"/"response.csv").is_file())


if __name__ == "__main__":
    unittest.main()
