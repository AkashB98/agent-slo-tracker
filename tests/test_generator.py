"""Generator: determinism, shape, planted incidents."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import (AGENTS, INCIDENTS, ROW_FIELDS, generate_runs,
                       incident_at, parse_ts, runs_to_jsonl)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = generate_runs()

    def test_deterministic(self):
        self.assertEqual(generate_runs(), self.runs)
        self.assertEqual(runs_to_jsonl(generate_runs()),
                         runs_to_jsonl(self.runs))

    def test_different_seed_differs(self):
        self.assertNotEqual(generate_runs(seed=7), self.runs)

    def test_volume_in_range(self):
        # ~15-25 runs/hour x 672 hours, plus fast-burn density + blip
        self.assertTrue(12000 < len(self.runs) < 16000, len(self.runs))

    def test_fields_and_agents(self):
        for r in self.runs[:200]:
            self.assertEqual(sorted(r.keys()), sorted(ROW_FIELDS))
            self.assertIn(r["agent"], AGENTS)

    def test_timestamps_sorted_and_start_monday(self):
        stamps = [r["timestamp"] for r in self.runs]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(parse_ts(stamps[0]).weekday(), 0)  # Monday
        self.assertTrue(stamps[0].startswith("2026-01-05"))

    def test_run_ids_unique(self):
        ids = [r["run_id"] for r in self.runs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ineligible_share_small(self):
        inelig = sum(1 for r in self.runs if r["eligibility"] != "eligible")
        share = inelig / len(self.runs)
        self.assertTrue(0.005 < share < 0.03, share)

    def test_incidents_labelled_and_fixture_matches(self):
        self.assertEqual([i["kind"] for i in INCIDENTS],
                         ["slow_burn", "blip", "fast_burn"])
        with open(os.path.join(ROOT, "fixtures", "incidents.json"),
                  encoding="utf-8") as fh:
            fixture = json.load(fh)
        self.assertEqual(fixture["incidents"], INCIDENTS)

    def test_fast_burn_failure_rate_spikes(self):
        fast = [r for r in self.runs
                if incident_at(parse_ts(r["timestamp"])) == "fast_burn"
                and r["eligibility"] == "eligible"]
        fails = sum(1 for r in fast if r["status"] == "failure")
        self.assertGreater(fails / len(fast), 0.6)

    def test_slow_burn_moderately_elevated(self):
        slow = [r for r in self.runs
                if incident_at(parse_ts(r["timestamp"])) == "slow_burn"
                and r["eligibility"] == "eligible"]
        fails = sum(1 for r in slow if r["status"] == "failure")
        rate = fails / len(slow)
        self.assertTrue(0.08 < rate < 0.20, rate)

    def test_fast_burn_latency_degraded(self):
        fast = [r for r in self.runs
                if incident_at(parse_ts(r["timestamp"])) == "fast_burn"]
        mean_lat = sum(r["latency_ms"] for r in fast) / len(fast)
        self.assertGreater(mean_lat, 800)


if __name__ == "__main__":
    unittest.main()
