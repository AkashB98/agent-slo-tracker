"""Report assembly: seeded headline numbers + structure."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import generate_runs
from helpers import run
from report import build_report


class TestSeededReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = build_report(generate_runs())

    def test_headline_counts(self):
        self.assertEqual(self.report["total_runs"], 13548)
        self.assertEqual(self.report["eligible_runs"], 13317)

    def test_success_block_pinned(self):
        block = self.report["slos"]["success"]
        self.assertEqual((block["good"], block["bad"]), (12709, 608))
        self.assertEqual(block["observed"], 0.954344)
        self.assertEqual(block["budget"]["consumed_pct"], 0.913119)
        self.assertTrue(block["compliant"])

    def test_latency_block_pinned(self):
        block = self.report["slos"]["latency"]
        self.assertEqual((block["good"], block["bad"]), (12966, 351))
        self.assertEqual(block["observed"], 0.973643)
        self.assertEqual(block["budget"]["consumed_pct"], 0.527146)

    def test_burn_at_now_pinned(self):
        burns = self.report["burn_rates"]["success"]
        self.assertEqual(burns["1h"], 0.0)
        self.assertEqual(burns["6h"], 0.183486)
        self.assertEqual(burns["3d"], 0.382916)
        self.assertEqual(burns["28d"], 0.913119)

    def test_no_active_alerts_or_exhaustion_at_now(self):
        self.assertEqual(self.report["active_alerts"], [])
        # trailing 1h is clean (burn 0) -> nothing projected
        self.assertIsNone(self.report["projected_exhaustion"]["success"])

    def test_per_agent_all_sufficient(self):
        table = self.report["per_agent"]
        self.assertEqual(len(table), 3)
        self.assertTrue(all(t["status"] == "ok" for t in table))
        self.assertTrue(all(t["eligible"] > 4000 for t in table))

    def test_report_deterministic(self):
        self.assertEqual(build_report(generate_runs()), self.report)

    def test_empty_dataset_raises(self):
        with self.assertRaises(ValueError):
            build_report([])

    def test_tiny_dataset_report(self):
        runs = [run() for _ in range(5)]
        rep = build_report(runs)
        self.assertEqual(rep["slos"]["success"]["observed"], 1.0)
        self.assertEqual(rep["per_agent"][0]["status"], "insufficient_data")


if __name__ == "__main__":
    unittest.main()
