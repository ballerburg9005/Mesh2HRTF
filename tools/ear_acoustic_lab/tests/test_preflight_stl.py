"""STL watertightness must be checked after triangle-soup vertex welding."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_analysis import _preflight_stl


class OriginalSTLPreflightTest(unittest.TestCase):
    def test_closed_stl_with_unindexed_triangle_soup_is_watertight(self):
        vertices = np.asarray([
            [0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]],
            dtype=float)
        faces = [(0, 2, 1), (0, 1, 3),
                 (0, 3, 2), (1, 2, 3)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tetra.stl"
            trimesh.Trimesh(
                vertices=vertices, faces=faces, process=False).export(str(path))
            raw = trimesh.load_mesh(str(path), process=False)
            # Deliberately demonstrate the raw STL indexing trap.
            self.assertFalse(raw.is_watertight)
            result = _preflight_stl(
                path, "mm", [2,2,2], [0,120,0])
            self.assertTrue(result["watertight"])
            self.assertTrue(result["winding_consistent"])
            self.assertEqual(result["triangles"], 4)
            self.assertLess(result["welded_vertex_count"],
                            result["raw_vertex_count"])


if __name__ == "__main__":
    unittest.main()
