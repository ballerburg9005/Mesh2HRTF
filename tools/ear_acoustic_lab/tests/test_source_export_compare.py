"""Compare source STL and NumCalc exported triangles; verify OBJ axes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_export_compare import compare_source_export
from blender_obj_axes import mesh2input_to_obj, obj_y_forward_z_up_to_blender


class SourceVsExportGeometryTest(unittest.TestCase):
    def make_project(self, folder, drop_last=False):
        project = Path(folder)
        face_vertices = [(0,2,1), (0,1,3), (0,3,2), (1,2,3)]
        verts = np.asarray([(0,0,0),(1,0,0),(0,1,0),(0,0,1)], float)
        stl = project / "model.stl"
        mesh = trimesh.Trimesh(vertices=verts, faces=face_vertices, process=False)
        mesh.export(str(stl))
        folder = project / "ObjectMeshes" / "Reference"
        folder.mkdir(parents=True)
        (folder / "Nodes.txt").write_text(
            "4\n" + "".join(
                f"{i} {v[0]*.001:.12f} {v[1]*.001:.12f} {v[2]*.001:.12f}\n"
                for i, v in enumerate(verts)))
        used = face_vertices[:-1] if drop_last else face_vertices
        (folder / "Elements.txt").write_text(
            f"{len(used)}\n" +
            "".join(f"{i} {a} {b} {c} 0 0 0\n"
                    for i, (a,b,c) in enumerate(used)))
        (project / "earlab_config.json").write_text(json.dumps({
            "stl": str(stl), "units": "mm"
        }))
        return project

    def test_exact_stl_and_export_have_same_welded_topology(self):
        with tempfile.TemporaryDirectory() as d:
            report, outfile = compare_source_export(self.make_project(d))
            self.assertEqual(report["original_triangles"], 4)
            self.assertEqual(report["exported_triangles"], 4)
            self.assertEqual(report["exact_face_matches_at_tolerance"], 4)
            self.assertEqual(report["triangles_only_in_stl"], 0)
            self.assertEqual(report["triangles_only_in_export"], 0)
            self.assertEqual(
                report["stl_welded_edge_topology"]["open_boundary_edges"], 0)
            self.assertEqual(
                report["numcalc_export_welded_edge_topology"]["open_boundary_edges"], 0)
            self.assertTrue(outfile.is_file())

    def test_export_omitting_face_is_detected_independently(self):
        with tempfile.TemporaryDirectory() as d:
            report, _ = compare_source_export(
                self.make_project(d, drop_last=True))
            self.assertEqual(report["original_triangles"], 4)
            self.assertEqual(report["exported_triangles"], 3)
            self.assertEqual(report["exact_face_matches_at_tolerance"], 3)
            self.assertEqual(report["triangles_only_in_stl"], 1)
            self.assertEqual(report["triangles_only_in_export"], 0)
            self.assertEqual(report["bad_edges_shared_by_source_and_export"], 0)
            self.assertEqual(report["bad_edges_present_only_in_export"], 3)

    def test_obj_native_axes_roundtrip(self):
        p = (.002, -.019, .003)
        obj = mesh2input_to_obj(p, "mm")
        self.assertEqual(obj, (2., -19., 3.))
        self.assertEqual(
            obj_y_forward_z_up_to_blender(obj),
            (2., -19., 3.))
        self.assertEqual(mesh2input_to_obj(p, "m"), p)
        with self.assertRaises(ValueError):
            mesh2input_to_obj(p, "invalid")


if __name__ == "__main__":
    unittest.main()
