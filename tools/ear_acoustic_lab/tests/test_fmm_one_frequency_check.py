"""One-frequency FMM crosscheck: isolated inputs, real solver output validation."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fmm_one_frequency_check as fmm


class OneFrequencyFMMTest(unittest.TestCase):
    def test_changes_only_main_parameters_i_methodbem(self):
        original = (
            "##\n## 1. Main Parameters I\n"
            "2 9075 4542 0 0 2 1 4 0\n##\n")
        edited, old = fmm.force_single_level(original)
        self.assertEqual(old, "4")
        self.assertIn("2 9075 4542 0 0 2 1 1 0", edited)
        self.assertTrue(edited.endswith("##\n"))
        with self.assertRaises(ValueError):
            fmm.force_single_level(
                "## 1. Main Parameters I\n2 9075 4542 0 0 2 1 0 0\n")

    def test_requires_converged_complete_pressure(self):
        self.assertEqual(
            fmm.completed_true_residual(
                "CGS TRUE relative residual = 1e-9\nEnd time:"),
            ("CGS", 1e-9))
        self.assertEqual(
            fmm.completed_true_residual(
                "CGS TRUE relative residual = 1\n"
                "GMRES fallback: iterations = 3, "
                "true relative residual = 5e-9\nEnd time:"),
            ("GMRES", 5e-9))
        for log in [
            "GMRES fallback failed after 360 iterations\nEnd time:",
            "CGS TRUE relative residual = 2e-8\nEnd time:",
            "CGS TRUE relative residual = 1e-9",
        ]:
            with self.assertRaises(ValueError):
                fmm.completed_true_residual(log)

    def test_only_step_13_in_independent_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "repo"
            tool_dir = repo / "tools" / "ear_acoustic_lab"
            tool_dir.mkdir(parents=True)
            binary = repo / "mesh2hrtf" / "NumCalc" / "bin" / "NumCalc"
            binary.parent.mkdir(parents=True)
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                "import sys\n"
                "assert sys.argv[1:] == "
                "['-istart', '13', '-iend', '13']\n"
                "assert '2 9075 4542 0 0 2 1 1 0' "
                "in Path('NC.inp').read_text()\n"
                "p = Path('be.out/be.13/pEvalGrid')\n"
                "p.parent.mkdir(parents=True)\n"
                "p.write_text('Mesh2HRTF 1.3.0\\n1\\n1 3\\n'"
                " + ''.join(f'{10000000+i} 0.1 0\\n' for i in range(3)))\n"
                "Path('NC13-13.out').write_text("
                "'GMRES fallback: iterations = 3, "
                "true relative residual = 1e-9\\nEnd time: finished')\n")
            binary.chmod(0o755)
            project = root / "original_project"
            source = project / "NumCalc" / "source_1"
            source.mkdir(parents=True)
            for name in ["ObjectMeshes", "EvaluationGrids"]:
                (project / name).mkdir()
            (project / "parameters.json").write_text(json.dumps({
                "frequencies": [float(100 * i) for i in range(1, 81)],
                "speedOfSound": 343.
            }))
            (project / "earlab_config.json").write_text(json.dumps({
                "units": "mm", "mic": [0, 0, 0],
                "source": [0, 120, 0]
            }))
            original = (
                "##\n## 1. Main Parameters I\n"
                "2 9075 4542 0 0 2 1 4 0\n##\n"
                "NODES\n../../ObjectMeshes/Reference/Nodes.txt\n")
            (source / "NC.inp").write_text(original)
            pressure = source / "be.out" / "be.13" / "pEvalGrid"
            pressure.parent.mkdir(parents=True)
            pressure.write_text(
                "Mesh2HRTF 1.3.0\n1\n1 3\n"
                + "".join(f"{10000000+i} 0.2 0\n" for i in range(3)))
            (source / "NC13-13.out").write_text(
                "GMRES fallback: iterations = 100, "
                "true relative residual = 8e-9\nEnd time: finished")

            fake = types.ModuleType("analysis_tools")

            def transfer(freq, p, mic, src, units, speed=343.):
                distance = .12
                return p / (
                    .1 * np.exp(1j * 2 * np.pi * freq * distance / speed) /
                    (4 * np.pi * distance))

            fake.reference_transfer = transfer
            fake_path = tool_dir / "fmm_one_frequency_check.py"
            with patch.object(fmm, "__file__", str(fake_path)), \
                    patch.dict(sys.modules, {"analysis_tools": fake}):
                result = fmm.run_crosscheck(
                    project, step=13, timeout=5, min_free_gib=.1)
            self.assertAlmostEqual(
                result["single_minus_multilevel_db"],
                -20 * np.log10(2), places=9)
            self.assertEqual((source / "NC.inp").read_text(), original)
            self.assertEqual(pressure.read_text().count("0.2 0"), 3)
            reports = list((project / "analysis").glob("*/comparison.json"))
            self.assertEqual(len(reports), 1)
            self.assertLess(
                json.loads(reports[0].read_text())["single_level"]
                ["true_relative_residual"], 1e-8)


if __name__ == "__main__":
    unittest.main()
