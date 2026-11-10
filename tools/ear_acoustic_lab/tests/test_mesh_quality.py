import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mesh_quality import analyze_exported_mesh


class MeshQualityTest(unittest.TestCase):
    def test_matches_numcalc_warning_and_fatal_thresholds(self):
        # Reference: NC_Input.cpp, NC_WriteParameters:
        # dis_ref = sqrt(area)*1.51967 (triangles)
        # Warn at max_edge/dis_ref > 10, exit at > 100.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            folder = project / "ObjectMeshes" / "Reference"
            folder.mkdir(parents=True)
            pts = [
                (0, 0, 0), (1, 0, 0), (0, 1, 0),  # healthy
                (0, 2, 0), (1, 2, 0), (0, 2.001, 0),  # warn
                (0, 4, 0), (1, 4, 0), (0, 4.000001, 0),  # fatal
            ]
            (folder / "Nodes.txt").write_text(
                f"{len(pts)}\n" + "".join(
                    f"{i} {p[0]:.12f} {p[1]:.12f} {p[2]:.12f}\n"
                    for i, p in enumerate(pts)))
            (folder / "Elements.txt").write_text(
                "3\n0 0 1 2 0 0 0\n1 3 4 5 0 0 0\n2 6 7 8 0 0 0\n")
            q = analyze_exported_mesh(project)
            self.assertEqual(q["triangle_count"], 3)
            self.assertEqual(q["fatal_ids"], [2])
            self.assertEqual(q["fatal_count"], 1)
            self.assertEqual(q["warning_count"], 1)
            self.assertTrue(Path(q["overlay_obj"]).exists())
            with open(q["report_csv"], newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([r["element_id"] for r in rows], ["2", "1"])
            self.assertGreater(float(rows[0]["ratio"]), 100.)
            self.assertGreater(float(rows[1]["ratio"]), 10.)
            overlay = Path(q["overlay_obj"]).read_text()
            self.assertIn("NumCalc_element_2", overlay)
            self.assertIn("usemtl fatal", overlay)
            self.assertIn("usemtl warning", overlay)

    def test_good_mesh_has_no_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "ObjectMeshes" / "Reference"
            folder.mkdir(parents=True)
            (folder / "Nodes.txt").write_text(
                "3\n0 0 0 0\n1 0.001 0 0\n2 0 0.001 0\n")
            (folder / "Elements.txt").write_text(
                "1\n7 0 1 2 0 0 0\n")
            q = analyze_exported_mesh(tmp)
            self.assertEqual(q["warning_count"], 0)
            self.assertEqual(q["fatal_count"], 0)
            self.assertIsNone(q["overlay_obj"])
            self.assertTrue(Path(q["report_csv"]).is_file())


if __name__ == "__main__":
    unittest.main()
