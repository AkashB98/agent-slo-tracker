"""End-to-end demo: generate -> report -> budget -> burn -> alerts
-> check -> serve.

SYNTHETIC DATA ONLY (fictional Helios Home support agent). Runs fully
offline; the serve round-trip is loopback-only on an ephemeral port.
Every stage asserts.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alerts import score_alerts  # noqa: E402
from cli import EXIT_OK, main as cli_main, make_server, reset_state  # noqa: E402
from generator import generate_runs  # noqa: E402
from report import build_report  # noqa: E402
from validation import validate_runs  # noqa: E402


def _request(port: int, path: str, payload=None, method: str = "GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def main() -> None:
    print("== 1. generate synthetic Helios Home agent runs (seed=42) ==")
    runs = generate_runs()
    again = generate_runs()
    assert runs == again, "generator must be deterministic"
    assert validate_runs(runs) == [], validate_runs(runs)[:3]
    print(f"   {len(runs)} runs over 28 days, deterministic, clean")

    print("\n== 2. SLO report at dataset now ==")
    report = build_report(runs)
    for name in ("success", "latency"):
        block = report["slos"][name]
        print(f"   {name}: {block['good']}/{block['eligible']} good "
              f"({block['observed']:.2%}), budget consumed "
              f"{block['budget']['consumed_pct']:.1%}, "
              f"compliant={block['compliant']}")
    assert report["eligible_runs"] == report["slos"]["success"]["eligible"]

    print("\n== 3. alert scoring against the planted incidents ==")
    scoring = score_alerts(runs)
    print(f"   fast_burn paged (ttd {scoring['fast_burn_ttd_minutes']} min), "
          f"slow_burn ticketed (ttd {scoring['slow_burn_ttd_minutes']} min), "
          f"blip paged={scoring['blip_paged']}, "
          f"false pages={scoring['false_pages']}")
    assert scoring["fast_burn_detected"], scoring
    assert scoring["slow_burn_ticketed"], scoring
    assert not scoring["blip_paged"], scoring
    assert scoring["false_pages"] == 0, scoring

    print("\n== 4. CLI check gate on the seeded dataset ==")
    code = cli_main(["check"])
    code_err = cli_main(["check", "--input", "/tmp/no-such-slo-xyz.json"])
    print(f"   seeded={code} missing-file={code_err}")
    assert code_err == 2

    print("\n== 5. live localhost serve round-trip ==")
    reset_state(runs)
    server = make_server(0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, health = _request(port, "/health")
        assert status == 200 and health == {"status": "ok"}
        status, served = _request(port, "/report", payload={}, method="POST")
        assert status == 200 and served == report, "empty body -> seeded"
        status, served2 = _request(port, "/report",
                                   payload={"runs": runs}, method="POST")
        assert status == 200 and served2 == report
        print("   POST /report (seeded + explicit runs) matches local report")
    finally:
        server.shutdown()
        server.server_close()
        reset_state()

    print("\nDemo OK: generate, report, scoring, check and serve verified.")


if __name__ == "__main__":
    main()
