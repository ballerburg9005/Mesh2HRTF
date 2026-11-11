"""NumCalc manager must not reuse pressure unless the process finished."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "mesh2hrtf" / "NumCalc" / "manage_numcalc.py"
spec = importlib.util.spec_from_file_location("numcalc_completion_manager", MODULE)
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ManagerCompletionTest(unittest.TestCase):
    def test_requires_pressure_and_matching_end_marker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "NumCalc" / "source_1"
            src.mkdir(parents=True)
            (src / "Memory.txt").write_text("precomputed")
            for step in (1, 2, 3, 4, 5):
                p = src / "be.out" / f"be.{step}" / "pEvalGrid"
                p.parent.mkdir(parents=True)
                p.write_text("pressure")
            (src / "NC1-1.out").write_text("End time: finished")
            (src / "NC2-2.out").write_text("CGS solver: completed iteration")
            # Step 3 has pressure but no NumCalc log.
            (src / "be.out" / "be.4" / "pEvalGrid").write_text("")
            (src / "NC4-4.out").write_text("End time: finished")
            (src / "NC5-5.out").write_text(
                "GMRES fallback failed after 360 iterations\nEnd time: finished")
            estimates = np.array([[1, 100, .2], [2, 200, .3],
                                  [3, 300, .4], [4, 400, .5],
                                  [5, 500, .6]])
            with patch.object(manager.m2h, "read_ram_estimates",
                              return_value=estimates):
                all_jobs, unfinished, nsrc = manager._check_project(
                    str(root), "/bin/NumCalc", str(root / "manager.log"))
            self.assertEqual(len(all_jobs), 5)
            self.assertEqual(nsrc, 1)
            self.assertEqual(sorted(map(int, unfinished[:, 1])), [2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
