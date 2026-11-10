"""Conservative NumCalc scheduling on memory-constrained desktops."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_analysis import safe_numcalc_options


class RAMGuardTest(unittest.TestCase):
    def test_32_gib_machine_limits_memory_and_workers(self):
        with patch("psutil.virtual_memory",
                   return_value=SimpleNamespace(total=32 * 1024 ** 3)):
            options = safe_numcalc_options(SimpleNamespace(jobs=4))
        self.assertAlmostEqual(options["max_ram_load"], 20.8)
        self.assertEqual(options["max_instances"], 2)
        self.assertEqual(options["ram_safety_factor"], 2.0)
        self.assertEqual(options["wait_time"], 30)

    def test_one_worker_remains_one(self):
        with patch("psutil.virtual_memory",
                   return_value=SimpleNamespace(total=32 * 1024 ** 3)):
            self.assertEqual(
                safe_numcalc_options(SimpleNamespace(jobs=1))["max_instances"], 1)

    def test_eight_gib_minimum_reserve(self):
        with patch("psutil.virtual_memory",
                   return_value=SimpleNamespace(total=16 * 1024 ** 3)):
            self.assertAlmostEqual(
                safe_numcalc_options(SimpleNamespace(jobs=2))["max_ram_load"], 8)

    def test_too_small_machine_rejected(self):
        with patch("psutil.virtual_memory",
                   return_value=SimpleNamespace(total=8 * 1024 ** 3)):
            with self.assertRaises(RuntimeError):
                safe_numcalc_options(SimpleNamespace(jobs=1))


if __name__ == "__main__":
    unittest.main()
