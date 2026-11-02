"""Golden evals for agent-slo-tracker.

SYNTHETIC DATA ONLY (fictional Helios Home support agent). Every eval
is deterministic: run_evals.py writes eval_report.json and the file
must be byte-identical across runs. No timestamps, ports, or temp
paths enter the report - only names, booleans, pinned counts and
hashes. ("now" values below are the dataset's own last timestamp, a
property of the seeded data, never the system clock.)

E1-E8 pin the seeded dataset and the SLO/burn math. E9-E10 pin the
headline: alert scoring against the three planted incidents. E11-E15
pin exhaustion, the per-agent guard, CLI exit codes, the serve
round-trip, and the incidents fixture.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import evaluate_alerts, rule_thresholds, score_alerts  # noqa: E402
from cli import EXIT_ERROR, EXIT_FAIL, EXIT_OK, main as cli_main, make_server, reset_state  # noqa: E402
from generator import INCIDENTS, generate_runs, parse_ts, runs_to_jsonl, write_runs  # noqa: E402
from report import build_report  # noqa: E402
from slo import (WindowIndex, alert_threshold, burn_rate, dataset_now,  # noqa: E402
                 is_latency_good, is_success_good, per_agent,
                 projected_exhaustion, slo_block)
from validation import validate_runs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append({"name": name, "passed": bool(passed), "detail": detail})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return passed


def _run_cli(argv):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return cli_main(argv)


def _mk(**kw):
    base = {"run_id": "eval_x", "timestamp": "2026-01-05T00:00:00Z",
            "agent": "support-agent", "status": "success",
            "tool_error": False, "timeout": False, "latency_ms": 300,
            "eligibility": "eligible"}
    base.update(kw)
    return base


def main():
    print("== golden evals: agent-slo-tracker ==")
    runs = generate_runs()
    report = build_report(runs)

    # E1: generator determinism (bytes pinned)
    text1 = runs_to_jsonl(generate_runs())
    md5 = hashlib.md5(text1.encode()).hexdigest()
    check("E1 generator determinism (seed=42, jsonl md5 pinned)",
          text1 == runs_to_jsonl(runs)
          and md5 == "2bfd2bbd36a18cab5ae0102c6bf19430",
          f"jsonl_md5={md5}")

    # E2: headline counts pinned
    check("E2 headline counts (13548 runs, 13317 eligible)",
          report["total_runs"] == 13548
          and report["eligible_runs"] == 13317
          and validate_runs(runs) == [])

    # E3: success SLO block pinned
    s = report["slos"]["success"]
    check("E3 success SLO pinned (12709/13317 good, 91.31% budget)",
          (s["good"], s["bad"]) == (12709, 608)
          and s["observed"] == 0.954344
          and s["budget"]["allowed"] == 665.85
          and s["budget"]["consumed_pct"] == 0.913119
          and s["budget"]["remaining"] == 57.85
          and s["compliant"],
          f"consumed_pct={s['budget']['consumed_pct']}")

    # E4: latency SLO block pinned (independent of success)
    lat = report["slos"]["latency"]
    check("E4 latency SLO pinned (12966 good, 52.71% budget)",
          (lat["good"], lat["bad"]) == (12966, 351)
          and lat["observed"] == 0.973643
          and lat["budget"]["consumed_pct"] == 0.527146)

    # E5: budget math hand-computed
    block = slo_block(eligible=200, good=186)
    check("E5 budget math hand-computed (allowed 10, consumed 140%)",
          block["budget"]["allowed"] == 10.0
          and block["budget"]["consumed_pct"] == 1.4
          and burn_rate(bad=10, eligible=100) == 2.0)

    # E6: good-run definition traps
    check("E6 good-run traps (tool_error / timeout / latency split)",
          is_success_good(_mk())
          and not is_success_good(_mk(tool_error=True))
          and not is_success_good(_mk(timeout=True))
          and not is_success_good(_mk(status="failure"))
          and is_success_good(_mk(latency_ms=900))
          and not is_latency_good(_mk(latency_ms=900))
          and is_latency_good(_mk(status="failure", latency_ms=100)))

    # E7: burn threshold derivation pinned (computed, not hard-coded)
    thr = rule_thresholds()
    check("E7 thresholds derived (page 13.44 / 5.6, ticket 0.933333)",
          abs(alert_threshold(0.02, 1.0) - 13.44) < 1e-9
          and abs(alert_threshold(0.05, 6.0) - 5.6) < 1e-9
          and thr == {"page_fast": 13.44, "page_slow": 5.6,
                      "ticket": 0.933333},
          f"thresholds={thr}")

    # E8: burn rates at dataset-now pinned; no alerts firing
    burns = report["burn_rates"]["success"]
    check("E8 burn at now pinned (1h 0.0, 6h 0.183486, 28d 0.913119)",
          burns["5m"] == 0.0 and burns["1h"] == 0.0
          and burns["6h"] == 0.183486 and burns["3d"] == 0.382916
          and burns["28d"] == 0.913119
          and report["active_alerts"] == []
          and report["now"] == "2026-02-01T23:58:00Z",
          f"burn_28d={burns['28d']}")

    # E9: alert scoring - the headline (fast page, slow ticket,
    # blip silent, zero false pages)
    scoring = score_alerts(runs)
    check("E9 alert scoring (fast page ttd 60m, slow ticket ttd 1080m)",
          scoring["fast_burn_detected"]
          and scoring["fast_burn_ttd_minutes"] == 60
          and scoring["slow_burn_ticketed"]
          and scoring["slow_burn_ttd_minutes"] == 1080,
          f"scoring={scoring}")

    # E10: blip + false pages pinned separately (two-window proof)
    check("E10 blip never paged, zero false pages in 671 hours",
          scoring["blip_paged"] is False
          and scoring["false_pages"] == 0
          and scoring["evaluated_hours"] == 671)

    # E11: projected exhaustion (zero-burn None, exhausted 0, math)
    idx = WindowIndex(runs)
    now = dataset_now(runs)
    check("E11 projected exhaustion (clean 1h -> None at now)",
          projected_exhaustion(57.85, idx, "success", now) is None
          and projected_exhaustion(-1.0, idx, "success", now)
          == {"hours": 0.0, "days": 0.0}
          and report["projected_exhaustion"]["latency"] is None)

    # E12: per-agent guard (seeded agents all sufficient; tiny = not)
    tiny = per_agent(runs[:10], dataset_now(runs[:10]))
    check("E12 per-agent volume guard (seeded ok, 10 runs insufficient)",
          all(t["status"] == "ok" for t in report["per_agent"])
          and all(t["status"] == "insufficient_data" for t in tiny)
          and len(report["per_agent"]) == 3)

    # E13: CLI check exit codes 0 / 1 / 2
    import tempfile
    tmp = tempfile.mkdtemp(prefix="slo-evals-")
    bad_runs = ([dict(_mk(run_id=f"b{i}", status="failure",
                          tool_error=True)) for i in range(20)]
                + [dict(_mk(run_id=f"g{i}")) for i in range(80)])
    bad_path = os.path.join(tmp, "bad.json")
    write_runs(bad_runs, bad_path)
    codes = (_run_cli(["check"]),
             _run_cli(["check", "--input", bad_path]),
             _run_cli(["check", "--input", "/tmp/no-such-slo-xyz.json"]))
    check("E13 CLI check exit codes 0/1/2",
          codes == (EXIT_OK, EXIT_FAIL, EXIT_ERROR), f"codes={codes}")

    # E14: serve round-trip matches local report (seeded + explicit)
    reset_state(runs)
    server = make_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def _post(payload):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/report",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        serve_ok = _post({}) == report and _post({"runs": runs}) == report
    finally:
        server.shutdown()
        server.server_close()
        reset_state()
    check("E14 serve round-trip matches local report", serve_ok)

    # E15: incidents fixture matches the generator's labels, and a
    # page really fires at the fast-burn boundary on the seeded data
    with open(os.path.join(ROOT, "fixtures", "incidents.json"),
              encoding="utf-8") as fh:
        fixture = json.load(fh)
    at_fast_end = evaluate_alerts(idx, parse_ts("2026-01-29T12:00:00Z"))
    check("E15 incidents fixture pinned + page at fast-burn end",
          fixture["incidents"] == INCIDENTS
          and any(a["severity"] == "page" for a in at_fast_end),
          f"kinds={[i['kind'] for i in INCIDENTS]}")

    passed = sum(1 for r in RESULTS if r["passed"])
    total_n = len(RESULTS)
    summary_doc = {"passed": passed, "total": total_n,
                   "all_passed": passed == total_n, "evals": RESULTS}
    with open(os.path.join(HERE, "eval_report.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary_doc, sort_keys=True) + "\n")
    print(f"\n{passed}/{total_n} evals passed -> evals/eval_report.json")
    return 0 if passed == total_n else 1


if __name__ == "__main__":
    sys.exit(main())
