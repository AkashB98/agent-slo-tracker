"""CLI: commands, exit codes 0/1/2, JSON round-trips, serve round-trips."""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK, main as cli_main, make_server, reset_state  # noqa: E402
from generator import generate_runs, write_runs  # noqa: E402
from helpers import run as mk_run  # noqa: E402
from report import build_report  # noqa: E402


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slo-test-")
        self.seeded = os.path.join(self.tmp, "seeded.json")
        write_runs(generate_runs(), self.seeded)


class TestCommands(CliBase):
    def test_generate_to_file(self):
        out_path = os.path.join(self.tmp, "gen.jsonl")
        code, out, _ = run_cli(["generate", "--out", out_path])
        self.assertEqual(code, EXIT_OK)
        with open(out_path, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().strip().splitlines()), 13548)

    def test_report_seeded_default(self):
        code, out, _ = run_cli(["report"])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["eligible_runs"], 13317)

    def test_report_from_file_matches_default(self):
        code, out, _ = run_cli(["report", "--input", self.seeded])
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(json.loads(out), build_report(generate_runs()))

    def test_budget_command(self):
        code, out, _ = run_cli(["budget", "--input", self.seeded])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["success"]["budget"]["consumed_pct"],
                         0.913119)

    def test_burn_command(self):
        code, out, _ = run_cli(["burn"])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["alert_thresholds"]["page_fast"], 13.44)

    def test_alerts_command_includes_scoring(self):
        code, out, _ = run_cli(["alerts"])
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertTrue(payload["scoring"]["fast_burn_detected"])
        self.assertEqual(payload["scoring"]["false_pages"], 0)

    def test_report_out_file(self):
        out_path = os.path.join(self.tmp, "rep.json")
        code, _, _ = run_cli(["report", "--out", out_path])
        self.assertEqual(code, EXIT_OK)
        with open(out_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["total_runs"], 13548)


class TestCheckExitCodes(CliBase):
    def test_check_seeded_passes(self):
        code, out, _ = run_cli(["check"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("check PASS", out)

    def test_check_missing_file_is_error(self):
        code, _, _ = run_cli(["check", "--input",
                              "/tmp/no-such-slo-xyz.json"])
        self.assertEqual(code, EXIT_ERROR)

    def test_check_exhausted_budget_fails(self):
        # 100 runs, 20 bad -> consumed 400% of the 5-run budget
        runs = [mk_run(status="failure", tool_error=True)
                for _ in range(20)] + [mk_run() for _ in range(80)]
        path = os.path.join(self.tmp, "bad.json")
        write_runs(runs, path)
        code, out, _ = run_cli(["check", "--input", path])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("exhausted", out)

    def test_check_invalid_data_is_error(self):
        path = os.path.join(self.tmp, "junk.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('[{"run_id": 1}]')
        code, _, _ = run_cli(["check", "--input", path])
        self.assertEqual(code, EXIT_ERROR)

    def test_report_missing_file_is_error(self):
        code, _, _ = run_cli(["report", "--input",
                              "/tmp/no-such-slo-xyz.json"])
        self.assertEqual(code, EXIT_ERROR)


class TestServe(CliBase):
    def setUp(self):
        super().setUp()
        self.runs = generate_runs()
        reset_state(self.runs)
        self.server = make_server(0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        reset_state()

    def _req(self, path, payload=None, method="GET"):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())

    def test_health(self):
        status, body = self._req("/health")
        self.assertEqual((status, body), (200, {"status": "ok"}))

    def test_report_empty_body_is_seeded(self):
        status, body = self._req("/report", payload={}, method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(body, build_report(self.runs))

    def test_report_explicit_runs(self):
        status, body = self._req("/report", payload={"runs": self.runs},
                                 method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(body["eligible_runs"], 13317)

    def test_report_bad_runs_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._req("/report", payload={"runs": [{"run_id": 1}]},
                      method="POST")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._req("/nope")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
