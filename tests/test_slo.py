"""SLI definitions, budget math, burn derivation, exhaustion, guards."""

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import parse_ts
from helpers import burst, run
from slo import (WindowIndex, alert_threshold, burn_rate, burn_rates,
                 dataset_now, is_eligible, is_latency_good,
                 is_success_good, per_agent, projected_exhaustion,
                 slo_block)


class TestEligibility(unittest.TestCase):
    def test_eligible_only(self):
        self.assertTrue(is_eligible(run(eligibility="eligible")))
        self.assertFalse(is_eligible(run(eligibility="invalid_input")))
        self.assertFalse(is_eligible(run(eligibility="client_cancelled")))

    def test_ineligible_excluded_from_index(self):
        runs = [run(), run(eligibility="invalid_input"),
                run(eligibility="client_cancelled", status="failure")]
        idx = WindowIndex(runs)
        self.assertEqual(len(idx.times), 1)
        self.assertEqual(idx.total_bad("success"), 0)


class TestGoodDefinitions(unittest.TestCase):
    def test_clean_success_is_good(self):
        self.assertTrue(is_success_good(run()))

    def test_success_with_tool_error_is_not_good(self):
        self.assertFalse(is_success_good(run(tool_error=True)))

    def test_success_with_timeout_is_not_good(self):
        self.assertFalse(is_success_good(run(timeout=True)))

    def test_failure_is_not_good(self):
        self.assertFalse(is_success_good(run(status="failure")))

    def test_latency_good_is_independent(self):
        slow_but_clean = run(latency_ms=900)
        self.assertTrue(is_success_good(slow_but_clean))
        self.assertFalse(is_latency_good(slow_but_clean))
        fast_failure = run(status="failure", latency_ms=100)
        self.assertFalse(is_success_good(fast_failure))
        self.assertTrue(is_latency_good(fast_failure))

    def test_latency_boundary_800_is_good(self):
        self.assertTrue(is_latency_good(run(latency_ms=800)))
        self.assertFalse(is_latency_good(run(latency_ms=801)))


class TestBudgetMath(unittest.TestCase):
    def test_hand_computed_block(self):
        block = slo_block(eligible=200, good=186)  # 14 bad, allowed 10
        self.assertEqual(block["bad"], 14)
        self.assertEqual(block["budget"]["allowed"], 10.0)
        self.assertEqual(block["budget"]["consumed_pct"], 1.4)
        self.assertEqual(block["budget"]["remaining"], -4.0)
        self.assertFalse(block["compliant"])

    def test_compliant_block(self):
        block = slo_block(eligible=1000, good=980)
        self.assertEqual(block["observed"], 0.98)
        self.assertTrue(block["compliant"])
        self.assertEqual(block["budget"]["consumed_pct"], 0.4)

    def test_zero_eligible_block(self):
        block = slo_block(eligible=0, good=0)
        self.assertEqual(block["observed"], 0.0)
        self.assertEqual(block["budget"]["consumed_pct"], 0.0)


class TestBurnRate(unittest.TestCase):
    def test_hand_computed(self):
        # bad rate 10% against a 5% budget = burn 2.0
        self.assertEqual(burn_rate(bad=10, eligible=100), 2.0)

    def test_zero_eligible_is_zero(self):
        self.assertEqual(burn_rate(bad=0, eligible=0), 0.0)

    def test_burn_at_exact_budget_rate_is_one(self):
        self.assertEqual(burn_rate(bad=5, eligible=100), 1.0)

    def test_window_rates_keys(self):
        runs = burst(0, 30, bad=False)
        idx = WindowIndex(runs)
        now = dataset_now(runs)
        rates = burn_rates(idx, "success", now)
        self.assertEqual(sorted(rates), ["1h", "28d", "30m", "3d", "5m", "6h"])
        self.assertTrue(all(v == 0.0 for v in rates.values()))


class TestThresholdDerivation(unittest.TestCase):
    def test_page_fast_threshold_13_44(self):
        self.assertAlmostEqual(alert_threshold(0.02, 1.0), 13.44, places=9)

    def test_page_slow_threshold_5_6(self):
        self.assertAlmostEqual(alert_threshold(0.05, 6.0), 5.6, places=9)

    def test_ticket_threshold(self):
        self.assertAlmostEqual(alert_threshold(0.10, 72.0), 0.9333333,
                               places=6)

    def test_threshold_scales_with_budget_fraction(self):
        self.assertAlmostEqual(alert_threshold(0.04, 1.0),
                               2 * alert_threshold(0.02, 1.0))


class TestDatasetNow(unittest.TestCase):
    def test_now_is_last_timestamp_not_clock(self):
        runs = [run(timestamp="2026-01-05T00:00:00Z"),
                run(timestamp="2026-01-07T12:30:00Z")]
        self.assertEqual(dataset_now(runs), parse_ts("2026-01-07T12:30:00Z"))

    def test_now_empty_is_none(self):
        self.assertIsNone(dataset_now([]))

    def test_window_excludes_run_at_start_boundary(self):
        # window is (start, end]: a run exactly at now-1h is outside 1h
        runs = [run(timestamp="2026-01-05T00:00:00Z", status="failure",
                    tool_error=True),
                run(timestamp="2026-01-05T01:00:00Z")]
        idx = WindowIndex(runs)
        eligible, bad = idx.counts("success", parse_ts("2026-01-05T00:00:00Z"),
                                   parse_ts("2026-01-05T01:00:00Z"))
        self.assertEqual((eligible, bad), (1, 0))


class TestProjectedExhaustion(unittest.TestCase):
    def test_zero_burn_no_exhaustion(self):
        runs = burst(0, 60, bad=False)
        idx = WindowIndex(runs)
        self.assertIsNone(projected_exhaustion(50.0, idx, "success",
                                               dataset_now(runs)))

    def test_already_exhausted_is_zero(self):
        runs = burst(0, 60, bad=True)
        idx = WindowIndex(runs)
        out = projected_exhaustion(-1.0, idx, "success", dataset_now(runs))
        self.assertEqual(out, {"hours": 0.0, "days": 0.0})

    def test_hand_computed_exhaustion(self):
        # 10 bad runs inside the last hour, 100 budget left -> 10 hours
        runs = burst(0, 10, bad=True) + burst(120, 5, bad=False)
        idx = WindowIndex(runs)
        now = dataset_now(runs)
        out = projected_exhaustion(100.0, idx, "success",
                                   parse_ts("2026-01-05T00:10:00Z"))
        self.assertEqual(out["hours"], 10.0)
        self.assertAlmostEqual(out["days"], 10 / 24, places=3)
        self.assertIsNotNone(now)


class TestPerAgentGuard(unittest.TestCase):
    def test_below_minimum_is_insufficient_data(self):
        runs = [run(agent="billing-agent") for _ in range(10)]
        now = dataset_now(runs)
        table = per_agent(runs, now, min_runs=500)
        self.assertEqual(table[0]["status"], "insufficient_data")
        self.assertNotIn("success", table[0])

    def test_above_minimum_reports_burn_block(self):
        runs = ([run(agent="billing-agent") for _ in range(30)]
                + [run(agent="billing-agent", status="failure",
                       tool_error=True) for _ in range(5)])
        table = per_agent(runs, dataset_now(runs), min_runs=10)
        self.assertEqual(table[0]["status"], "ok")
        self.assertIn("budget_consumed_pct", table[0]["success"])

    def test_agents_sorted(self):
        runs = [run(agent="support-agent"), run(agent="billing-agent")]
        table = per_agent(runs, dataset_now(runs), min_runs=500)
        self.assertEqual([t["agent"] for t in table],
                         ["billing-agent", "support-agent"])


if __name__ == "__main__":
    unittest.main()
