import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from surface_viewer import mesh_views, write_interactive_surface, write_vtk_surfaces


class SurfaceViewerTest(unittest.TestCase):
    def test_rotation_cutaway_and_offline_html(self):
        verts = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]], float)
        faces = np.array([[0,1,2],[0,2,3]], int)
        hz = np.array([500., 1000., 2500.])
        pressure = np.array([[.5+0j,2+0j,1+0j], [1+0j,.7+0j,1.2+0j]])
        with tempfile.TemporaryDirectory() as d:
            output = Path(d)
            html = write_interactive_surface(
                output, verts, faces, hz, pressure, mic_mm=[0,0,0])
            self.assertTrue(html.is_file())
            self.assertGreater(html.stat().st_size, 200_000)
            content = html.read_text()
            self.assertIn("pinna opening", content)
            self.assertIn("Cut Y: show back half", content)
            self.assertIn("Hide large faces", content)
            self.assertIn("Hide distorted faces", content)
            self.assertIn("Side/back (+X,-Y)", content)
            # Mode selection also updates scene.camera; users need not
            # manually switch away from the front view.
            self.assertIn("scene.camera", content)
            vtk = write_vtk_surfaces(output, verts, faces, hz, pressure)
            self.assertEqual(len(vtk), 3)
            self.assertTrue(Path(vtk[0]).is_file())
            self.assertIn("pressure_relative_surface_median_db",
                          Path(vtk[0]).read_text())

    def test_face_masks_do_not_mutate_solver_geometry(self):
        verts = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]], float)
        faces = np.array([[0,1,2],[0,2,3]], int)
        before = faces.copy()
        modes = mesh_views(verts, faces)
        self.assertEqual(len(modes), 5)
        self.assertTrue(modes["Full mesh"].all())
        for mask in modes.values():
            self.assertEqual(mask.shape, (len(faces),))
        np.testing.assert_array_equal(faces, before)


if __name__ == "__main__":
    unittest.main()
