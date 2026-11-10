import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from share_results import prepare_snapshot
from run_analysis import PRESETS


class ShareResultsTest(unittest.TestCase):
    def test_reviewable_snapshot_excludes_raw_solver_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = root/"ear_test_2026"
            (project/"analysis").mkdir(parents=True)
            (project/"NumCalc"/"source_1"/"be.out").mkdir(parents=True)
            (project/"earlab_config.json").write_text(json.dumps({
                "mic": [0,0,0], "source": [0,120,0], "stl": str(root/"my_model.stl"),
                "units": "mm"
            }))
            (root/"my_model.stl").write_bytes(b"fake STL content")
            (project/"analysis"/"response.csv").write_text("frequency_hz,gain\n500,1.0\n")
            (project/"analysis"/"response.png").write_bytes(b"PNG")
            (project/"NumCalc"/"source_1"/"be.out"/"be.1").write_bytes(b"huge solver workspace")
            dest = prepare_snapshot(project, share_root=root/"shared")
            self.assertTrue((dest/"analysis"/"response.csv").is_file())
            self.assertFalse((dest/"model.stl").exists())
            self.assertFalse((dest/"NumCalc").exists())
            meta = json.loads((dest/"snapshot_manifest.json").read_text())
            self.assertIn("analysis/response.csv", meta["included"])
            self.assertTrue(meta["solver_results_reused"])
            dest = prepare_snapshot(project, include_stl=True, share_root=root/"shared")
            self.assertTrue((dest/"model.stl").exists())

    def test_10khz_presets(self):
        self.assertEqual(PRESETS["10 kHz overview (100 Hz)"], (100,10000,100))
        self.assertEqual(PRESETS["10 kHz detailed (25 Hz)"], (100,10000,25))
        self.assertEqual(PRESETS["10 kHz deep (10 Hz)"], (100,10000,10))


if __name__ == "__main__":
    unittest.main()
