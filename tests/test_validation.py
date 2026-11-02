"""Validation gate: defects flagged, clean data passes, bool trap."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generator import generate_runs
from helpers import run
from validation import validate_run, validate_runs


class TestValidateRun(unittest.TestCase):
    def test_clean_run_passes(self):
        self.assertEqual(validate_run(run()), [])

    def test_bad_status(self):
        self.assertTrue(validate_run(run(status="ok-ish")))

    def test_bad_agent(self):
        self.assertTrue(validate_run(run(agent="rogue-agent")))

    def test_bad_eligibility(self):
        self.assertTrue(validate_run(run(eligibility="maybe")))

    def test_bad_timestamp(self):
        self.assertTrue(validate_run(run(timestamp="yesterday")))

    def test_negative_latency(self):
        self.assertTrue(validate_run(run(latency_ms=-1)))

    def test_bool_latency_is_rejected(self):
        # True is an int in Python - the trap must be caught explicitly
        self.assertTrue(validate_run(run(latency_ms=True)))

    def test_non_bool_flag_rejected(self):
        bad = run()
        bad["tool_error"] = 1
        self.assertTrue(validate_run(bad))

    def test_missing_run_id(self):
        bad = run()
        bad["run_id"] = ""
        self.assertTrue(validate_run(bad))

    def test_non_dict_run(self):
        self.assertEqual(validate_run("nope"), ["run is not an object"])


class TestValidateRuns(unittest.TestCase):
    def test_generated_dataset_is_clean(self):
        self.assertEqual(validate_runs(generate_runs()), [])

    def test_duplicate_ids_flagged(self):
        a, b = run(), run()
        b["run_id"] = a["run_id"]
        self.assertTrue(any("duplicate" in i for i in validate_runs([a, b])))

    def test_empty_dataset_flagged(self):
        self.assertTrue(validate_runs([]))

    def test_non_list_flagged(self):
        self.assertEqual(validate_runs({"runs": []}),
                         ["dataset is not a list"])


if __name__ == "__main__":
    unittest.main()
