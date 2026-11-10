import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reanalyze import _surface_maps


class BoundaryPressureMapsTest(unittest.TestCase):
    def test_can_plot_computed_boundary_field_from_mesh(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            surface = project/"ObjectMeshes"/"Reference"
            surface.mkdir(parents=True)
            (surface/"Nodes.txt").write_text(
                "3\n0 0 0 0\n1 0.003 0 0\n2 0 0.003 0\n")
            (surface/"Elements.txt").write_text(
                "1\n0 0 1 2 0 0 0\n")
            output = project/"analysis"
            output.mkdir()
            freq = np.array([500., 1000., 1500.])
            pressure = np.array([[[1+0j, 1.5+0j, 0.5+0j]]])
            with patch("mesh2hrtf.Output2HRTF.output2hrtf._read_numcalc_data",
                       return_value=(pressure, np.array([0]))), \
                 patch("mesh2hrtf.export_vtk"):
                image, vtk, warning = _surface_maps(project, output, freq, np.array([0.,4.,0.]))
            self.assertIsNone(warning)
            self.assertTrue(image.is_file())
            self.assertGreater(image.stat().st_size, 1000)
            self.assertEqual(len(vtk), 3)


if __name__ == "__main__":
    unittest.main()
