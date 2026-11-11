"""Fast exported-mesh topology checks and NumCalc launch safety."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from topology_audit import audit_exported_topology, enforce_topology_gate


def fixture(tmp, nodes, faces, max_hz=1000.):
    p = Path(tmp)
    mesh = p / "ObjectMeshes" / "Reference"
    mesh.mkdir(parents=True)
    (mesh / "Nodes.txt").write_text(
        f"{len(nodes)}\n" +
        "".join(f"{i} {x} {y} {z}\n" for i, (x,y,z) in enumerate(nodes)))
    (mesh / "Elements.txt").write_text(
        f"{len(faces)}\n" +
        "".join(f"{i} {a} {b} {c} 0 0 0\n"
                for i, (a,b,c) in enumerate(faces)))
    (p / "parameters.json").write_text(json.dumps({
        "numFrequencies": 1, "frequencies": [max_hz]
    }))
    (p / "earlab_config.json").write_text(json.dumps({"units": "mm"}))
    return p


class MeshTopologyTest(unittest.TestCase):
    def test_closed_tetrahedron_passes_topology_and_low_frequency(self):
        with tempfile.TemporaryDirectory() as d:
            p = fixture(d, [
                (0,0,0), (.001,0,0), (0,.001,0), (0,0,.001)],
                [(0,2,1), (0,1,3), (0,3,2), (1,2,3)])
            q = audit_exported_topology(p)
            self.assertEqual(q["open_boundary_edges"], 0)
            self.assertEqual(q["nonmanifold_edges"], 0)
            self.assertEqual(q["winding_conflicts"], 0)
            self.assertEqual(q["edge_connected_components"], 1)
            self.assertTrue(q["ready_for_unattended_solver"])
            self.assertIsNone(q["overlay_obj"])
            self.assertTrue((p / "geometry_topology.json").is_file())
            enforce_topology_gate(p)

    def test_flaps_produce_four_open_and_one_four_face_nonmanifold_edge(self):
        with tempfile.TemporaryDirectory() as d:
            p = fixture(d, [
                (0,0,0), (.001,0,0), (0,.001,0), (0,0,.001),
                (0,.001,.001), (.001,.001,.001)],
                [(0,2,1), (0,1,3), (0,3,2), (1,2,3),
                 (1,2,4), (2,1,5)])
            q = audit_exported_topology(p)
            self.assertEqual(q["open_boundary_edges"], 4)
            self.assertEqual(q["nonmanifold_edges"], 1)
            self.assertEqual(q["edge_connected_components"], 1)
            self.assertFalse(q["ready_for_unattended_solver"])
            errors = [x for x in q["issue_edges"]
                      if x["kind"] == "nonmanifold"]
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["triangle_ids"], [0, 3, 4, 5])
            self.assertEqual(errors[0]["vertex_ids"], [1,2])
            self.assertTrue((p / "topology_defects.obj").is_file())
            self.assertIn("usemtl nonmanifold",
                          (p / "topology_defects.obj").read_text())
            with self.assertRaisesRegex(RuntimeError,
                                        "Refusing to launch"):
                enforce_topology_gate(p)
            enforce_topology_gate(p, allow=True)

    def test_high_frequency_underresolution_blocks_closed_mesh(self):
        with tempfile.TemporaryDirectory() as d:
            p = fixture(d, [
                (0,0,0), (.010,0,0), (0,.010,0), (0,0,.010)],
                [(0,2,1), (0,1,3), (0,3,2), (1,2,3)],
                max_hz=20000.)
            q = audit_exported_topology(p)
            self.assertFalse(q["ready_for_unattended_solver"])
            self.assertEqual(q["open_boundary_edges"], 0)
            self.assertGreater(
                q["resolution"]["fraction_of_triangles_over"], .05)
            self.assertIn("wavelength/6", q["blockers"][0])

    def test_winding_conflict_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = fixture(d, [
                (0,0,0), (.001,0,0), (0,.001,0), (0,0,.001)],
                [(0,1,2), (0,1,3)])
            q = audit_exported_topology(p)
            self.assertEqual(q["winding_conflicts"], 1)
            self.assertTrue(
                any(x["kind"] == "winding_conflict"
                    for x in q["issue_edges"]))


if __name__ == "__main__":
    unittest.main()
