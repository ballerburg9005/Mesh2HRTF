"""One-step repaired-mesh FMM comparisons: real subprocess interface, fake solver."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fmm_clean_mesh_one_point as focused


FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def make_project(base, edge_m=.001, closed=True):
    root = Path(base)
    repo = root / "repo"
    solver = repo / "mesh2hrtf" / "NumCalc" / "bin" / "NumCalc"
    solver.parent.mkdir(parents=True)
    solver.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        "assert sys.argv[1:] == ['-istart', '13', '-iend', '13']\n"
        "assert Path.cwd().name == 'source_1'\n"
        "assert Path('../../ObjectMeshes/Reference/Nodes.txt').is_file()\n"
        "data = Path('NC.inp').read_text()\n"
        "main = data.split('## 1. Main Parameters I')[1].splitlines()[1]\n"
        "method = main.split()[7]\n"
        "assert method in ('1', '4')\n"
        "type_name = ('Single level fast multipole BEM' if method == '1' "
        "else 'Direct multilevel fast multipole BEM')\n"
        "value = 0.1 if method == '1' else 0.2\n"
        "p = Path('be.out/be.13/pEvalGrid')\n"
        "p.parent.mkdir(parents=True)\n"
        "p.write_text('Mesh2HRTF 1.3.0\\n1\\n1 3\\n' + "
        "''.join(f'{10000000+i} {value} 0\\n' for i in range(3)))\n"
        "Path('NC13-13.out').write_text(type_name + '\\n' + "
        "'GMRES fallback: iterations = 3, true relative residual = 1e-9"
        "\\nEnd time: completed\\n')\n")
    solver.chmod(0o755)
    tool = repo / "tools" / "ear_acoustic_lab"
    tool.mkdir(parents=True)
    project = root / "new_clean_export"
    source = project / "NumCalc" / "source_1"
    source.mkdir(parents=True)
    (source / "NC.inp").write_text(
        "## 1. Main Parameters I\n2 4 4 0 0 2 1 4 0\n")
    for name in ("ObjectMeshes", "EvaluationGrids"):
        (project / name).mkdir()
    ref = project / "ObjectMeshes" / "Reference"
    ref.mkdir()
    vertices = [(0, 0, 0), (edge_m, 0, 0),
                (0, edge_m, 0), (0, 0, edge_m)]
    (ref / "Nodes.txt").write_text(
        "4\n" + "".join(f"{i} {x} {y} {z}\n"
                        for i, (x,y,z) in enumerate(vertices)))
    f = FACES if closed else FACES[:-1]
    (ref / "Elements.txt").write_text(
        f"{len(f)}\n" + "".join(
            f"{i} {a} {b} {c} 0 0 0\n"
            for i, (a,b,c) in enumerate(f)))
    (project / "parameters.json").write_text(json.dumps({
        "frequencies": [100 + (20000-100) * i/79 for i in range(80)],
        "speedOfSound": 343.0}))
    (project / "earlab_config.json").write_text(json.dumps({
        "mic": [0,0,0], "source": [0,120,0], "units": "mm"}))
    return project, repo, tool


class CleanMeshOnePointTest(unittest.TestCase):
    def test_runs_only_one_frequency_two_independent_operators(self):
        with tempfile.TemporaryDirectory() as d:
            project, repo, tool = make_project(d)
            original = (project / "NumCalc" / "source_1" / "NC.inp").read_bytes()
            with patch.object(focused, "__file__",
                              str(tool / "fmm_clean_mesh_one_point.py")), \
                 patch.object(focused, "_check_processes_and_ram",
                              return_value=9.0):
                report = focused.run(project, step=13, timeout=10)
            self.assertEqual(
                (project / "NumCalc" / "source_1" / "NC.inp").read_bytes(),
                original)
            self.assertAlmostEqual(
                report["multilevel_minus_single_db"],
                20*np.log10(2), places=7)
            self.assertEqual(report["single_level"]["true_relative_residual"],
                             1e-9)
            self.assertEqual(report["multilevel"]["true_relative_residual"],
                             1e-9)
            folder, = (project / "analysis").glob("fmm_new_mesh_step13_*")
            self.assertTrue((folder / "single_level" /
                             "NumCalc" / "source_1" / "NC13-13.out").is_file())
            self.assertTrue((folder / "multilevel" /
                             "NumCalc" / "source_1" / "NC13-13.out").is_file())
            self.assertTrue((folder / "comparison.json").is_file())
            self.assertFalse(
                (project / "NumCalc" / "source_1" / "be.out").exists())

    def test_refuses_bad_topology_without_solver(self):
        with tempfile.TemporaryDirectory() as d:
            project, _, tool = make_project(d, closed=False)
            with patch.object(focused, "__file__",
                              str(tool / "fmm_clean_mesh_one_point.py")):
                with self.assertRaisesRegex(RuntimeError, "open"):
                    focused.run(project, step=13, timeout=10)
            self.assertFalse((project / "analysis").exists())

    def test_refuses_underresolved_frequency(self):
        with tempfile.TemporaryDirectory() as d:
            project, _, tool = make_project(d, edge_m=.100)
            with patch.object(focused, "__file__",
                              str(tool / "fmm_clean_mesh_one_point.py")):
                with self.assertRaisesRegex(RuntimeError, "wavelength/6"):
                    focused.run(project, step=13, timeout=10)
            self.assertFalse((project / "analysis").exists())


if __name__ == "__main__":
    unittest.main()
