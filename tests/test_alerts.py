"""Multi-window alert rules: the two-window conjunction is the point."""

import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import evaluate_alerts, rule_thresholds, score_alerts
from generator import generate_runs, parse_ts
from helpers import burst
from slo import WindowIndex, dataset_now


def _rules(active):
    return sorted(a["rule"] for a in active)


class TestThresholds(unittest.TestCase):
    def test_thresholds_pinned(self):
        thr = rule_thresholds()
        self.assertEqual(thr["page_fast"], 13.44)
        self.assertEqual(thr["page_slow"], 5.6)
        self.assertAlmostEqual(thr["ticket"], 0.933333, places=6)


class TestTwoWindowRule(unittest.TestCase):
    def test_short_hot_long_cool_does_not_page(self):
        # 10 solid bad minutes at the end of an otherwise clean 6
        # hours: the 5m window is pure failure (burn 20) but the 1h
        # window dilutes it - exactly the planted-blip shape.
        runs = burst(0, 350, bad=False) + burst(350, 10, bad=True)
        idx = WindowIndex(runs)
        active = evaluate_alerts(idx, dataset_now(runs))
        self.assertNotIn("page_fast", _rules(active))
        self.assertNotIn("page_slow", _rules(active))

    def test_sustained_bad_hour_pages_fast(self):
        # A full hour of nothing but bad runs, evaluated at its end:
        # 1h burn = 20 and 5m burn = 20 - both over 13.44.
        runs = burst(0, 120, bad=False) + burst(120, 60, bad=True)
        idx = WindowIndex(runs)
        active = evaluate_alerts(idx, dataset_now(runs))
        self.assertIn("page_fast", _rules(active))

    def test_clean_data_fires_nothing(self):
        runs = burst(0, 400, bad=False)
        idx = WindowIndex(runs)
        self.assertEqual(evaluate_alerts(idx, dataset_now(runs)), [])

    def test_moderate_sustained_burn_tickets_without_paging(self):
        # 3 days at ~13% bad: 3d burn ~2.6 > 0.933 and 6h burn ~2.6,
        # but 6h burn is far under the 5.6 page line.
        # build directly: 20 runs/hour, 15% bad, for 72 hours
        from datetime import datetime, timezone
        runs = []
        base = datetime(2026, 1, 5, tzinfo=timezone.utc)
        n = 0
        for hour in range(72):
            for k in range(20):
                n += 1
                bad = (k % 8 == 0)  # 3/20 = 15% bad, deterministic
                ts = base + timedelta(hours=hour, minutes=k * 3)
                from helpers import run as mk
                runs.append(mk(
                    timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    status="failure" if bad else "success",
                    tool_error=bad))
        idx = WindowIndex(runs)
        active = evaluate_alerts(idx, dataset_now(runs))
        self.assertIn("ticket", _rules(active))
        self.assertNotIn("page_fast", _rules(active))
        self.assertNotIn("page_slow", _rules(active))


class TestSeededAlerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = generate_runs()
        cls.idx = WindowIndex(cls.runs)

    def test_page_fires_at_fast_burn_end(self):
        now = parse_ts("2026-01-29T12:00:00Z")
        active = evaluate_alerts(self.idx, now)
        self.assertTrue(any(a["severity"] == "page" for a in active),
                        active)

    def test_no_alert_at_dataset_now(self):
        active = evaluate_alerts(self.idx, dataset_now(self.runs))
        self.assertEqual(active, [])

    def test_ticket_fires_during_slow_burn(self):
        now = parse_ts("2026-01-18T00:00:00Z")  # day 3 of slow burn
        active = evaluate_alerts(self.idx, now)
        self.assertIn("ticket", _rules(active))
        self.assertFalse(any(a["severity"] == "page" for a in active))


class TestScoringSeeded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scoring = score_alerts(generate_runs())

    def test_fast_burn_detected_with_ttd(self):
        self.assertTrue(self.scoring["fast_burn_detected"])
        self.assertEqual(self.scoring["fast_burn_ttd_minutes"], 60)

    def test_slow_burn_ticketed_with_ttd(self):
        self.assertTrue(self.scoring["slow_burn_ticketed"])
        self.assertEqual(self.scoring["slow_burn_ttd_minutes"], 1080)

    def test_blip_never_paged(self):
        self.assertFalse(self.scoring["blip_paged"])

    def test_zero_false_pages(self):
        self.assertEqual(self.scoring["false_pages"], 0)

    def test_empty_dataset_scores_clean(self):
        out = score_alerts([])
        self.assertEqual(out["evaluated_hours"], 0)
        self.assertFalse(out["fast_burn_detected"])


if __name__ == "__main__":
    unittest.main()
