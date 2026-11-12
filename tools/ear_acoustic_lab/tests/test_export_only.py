"""Export-only preflight regression: never starts a NumCalc solve."""
from argparse import Namespace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_analysis


class ExportOnlyTest(unittest.TestCase):
    def test_export_only_without_solver(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            repo = root / "Mesh2HRTF"
            exporter = repo / "mesh2hrtf" / "Mesh2Input" / "mesh2input.py"
            exporter.parent.mkdir(parents=True)
            exporter.write_text("placeholder")
            stl = root / "fixed_model.stl"
            vertices = np.asarray([
                [0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]
            ], float)
            faces = [(0, 2, 1), (0, 1, 3),
                     (0, 3, 2), (1, 2, 3)]
            trimesh.Trimesh(vertices=vertices, faces=faces,
                            process=False).export(str(stl))
            calls = []

            def fake_blender(cmd, cwd=None):
                calls.append(cmd)
                self.assertIn("--config", [str(x) for x in cmd])
                config = Path(cmd[-1])
                project = config.parent
                folder = project / "ObjectMeshes" / "Reference"
                folder.mkdir(parents=True)
                (folder / "Nodes.txt").write_text(
                    "4\n" + "".join(
                        f"{i} {p[0]/1000} {p[1]/1000} {p[2]/1000}\n"
                        for i, p in enumerate(vertices)))
                (folder / "Elements.txt").write_text(
                    "4\n" + "".join(
                        f"{i} {f[0]} {f[1]} {f[2]} 0 0 0\n"
                        for i, f in enumerate(faces)))
                folder = project / "NumCalc" / "source_1"
                folder.mkdir(parents=True)
                (folder / "NC.inp").write_text("stub")
                (project / "parameters.json").write_text(
                    '{"numFrequencies":1, "frequencies":[1000], '
                    '"speedOfSound":343}')

            args = Namespace(
                mesh2hrtf=str(repo), stl=str(stl), units='mm',
                mic=[0, 0, 0], source_mode="Near 12 cm",
                source=[0, 120, 0], axis="+Y",
                preset="Quick ~1kHz", results=str(root / "results"),
                music="", jobs=1, export_only=True, resume="",
                inspect_resume="", inspect_geometry="")
            with patch.object(run_analysis, "call", side_effect=fake_blender), \
                 patch.object(run_analysis.shutil, "which", return_value="/bin/blender"):
                status = run_analysis.run(args)
            self.assertEqual(status, 0)
            self.assertEqual(len(calls), 1, "Blender only, no NumCalc invocation")
            project, = (root / "results").glob("ear_*")
            self.assertTrue((project / "geometry_topology.json").is_file())
            self.assertTrue((project / "analysis" /
                             "stl_vs_numcalc_geometry.json").is_file())
            self.assertFalse((repo / "mesh2hrtf" / "NumCalc" /
                              "bin" / "NumCalc").exists())


if __name__ == "__main__":
    unittest.main()
