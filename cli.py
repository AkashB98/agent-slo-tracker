"""CLI: generate | report | budget | burn | alerts | check | demo | serve.

SYNTHETIC DATA ONLY - Helios Home is a fictional company and every
dataset handled here is fabricated sample data.

Exit codes:
  check : 0 = within budget and no page firing at dataset-now,
          1 = budget exhausted (either SLO) or a page firing,
          2 = usage / IO / data-quality error
  others: 0 = success, 2 = usage / IO error

Commands without --input run on the seeded dataset (seed 42).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alerts import score_alerts  # noqa: E402
from generator import DEFAULT_SEED, generate_runs, load_runs, write_runs  # noqa: E402
from report import build_report  # noqa: E402
from slo import SLO_NAMES  # noqa: E402
from validation import validate_runs  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True)


def _runs_or_error(args):
    """(runs, exit_code): --input file, or the seeded dataset."""
    if not getattr(args, "input", None):
        return generate_runs(), EXIT_OK
    try:
        runs = load_runs(args.input)
    except FileNotFoundError:
        print(f"error: file not found: {args.input}", file=sys.stderr)
        return None, EXIT_ERROR
    except (ValueError, OSError) as exc:
        print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
        return None, EXIT_ERROR
    issues = validate_runs(runs)
    if issues:
        for issue in issues[:20]:
            print(f"data-quality: {issue}", file=sys.stderr)
        return None, EXIT_ERROR
    return runs, EXIT_OK


def _write_out(args, text: str, summary: str) -> int:
    if getattr(args, "out", None):
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(summary)
    else:
        print(text, end="")
    return EXIT_OK


def cmd_generate(args) -> int:
    runs = generate_runs(seed=args.seed)
    if args.out:
        try:
            write_runs(runs, args.out)
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return EXIT_ERROR
        print(f"generated {len(runs)} synthetic runs (seed={args.seed}) "
              f"-> {args.out}")
        return EXIT_OK
    from generator import runs_to_jsonl
    print(runs_to_jsonl(runs), end="")
    return EXIT_OK


def cmd_report(args) -> int:
    runs, code = _runs_or_error(args)
    if runs is None:
        return code
    return _write_out(args, canonical(build_report(runs)) + "\n",
                      f"report -> {args.out}")


def cmd_budget(args) -> int:
    runs, code = _runs_or_error(args)
    if runs is None:
        return code
    report = build_report(runs)
    payload = {name: report["slos"][name] for name in SLO_NAMES}
    return _write_out(args, canonical(payload) + "\n",
                      f"budget -> {args.out}")


def cmd_burn(args) -> int:
    runs, code = _runs_or_error(args)
    if runs is None:
        return code
    report = build_report(runs)
    payload = {"burn_rates": report["burn_rates"],
               "alert_thresholds": report["alert_thresholds"],
               "active_alerts": report["active_alerts"]}
    return _write_out(args, canonical(payload) + "\n", f"burn -> {args.out}")


def cmd_alerts(args) -> int:
    runs, code = _runs_or_error(args)
    if runs is None:
        return code
    report = build_report(runs)
    payload = {"active_alerts": report["active_alerts"],
               "scoring": score_alerts(runs)}
    return _write_out(args, canonical(payload) + "\n",
                      f"alerts -> {args.out}")


def cmd_check(args) -> int:
    runs, code = _runs_or_error(args)
    if runs is None:
        return code
    report = build_report(runs)
    exhausted = [name for name in SLO_NAMES
                 if report["slos"][name]["budget"]["remaining"] < 0]
    pages = [a for a in report["active_alerts"]
             if a["severity"] == "page"]
    for name in SLO_NAMES:
        block = report["slos"][name]
        print(f"{name}: observed={block['observed']:.4f} "
              f"target={block['target']} "
              f"budget_consumed_pct={block['budget']['consumed_pct']:.4f} "
              f"compliant={block['compliant']}")
    print(f"active_alerts={[a['rule'] for a in report['active_alerts']]}")
    if exhausted:
        print(f"check FAIL: error budget exhausted for {exhausted}")
        return EXIT_FAIL
    if pages:
        print(f"check FAIL: page firing at dataset now: "
              f"{[a['rule'] for a in pages]}")
        return EXIT_FAIL
    print("check PASS")
    return EXIT_OK


def cmd_demo(args) -> int:
    import demo as demo_mod
    try:
        demo_mod.main()
    except AssertionError as exc:
        print(f"demo FAILED: {exc}", file=sys.stderr)
        return EXIT_FAIL
    return EXIT_OK


# ------------------------------------------------------------------- serve

class _State:
    def __init__(self):
        self.runs: List[Dict] = generate_runs()


_STATE = _State()


def get_state() -> _State:
    return _STATE


def reset_state(runs: Optional[List[Dict]] = None) -> None:
    _STATE.runs = list(runs) if runs is not None else generate_runs()


class _Handler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: Any) -> None:
        body = (canonical(payload) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self._send_json(200, {"status": "ok"})
        return self._send_json(404, {"error": "unknown endpoint"})

    def do_POST(self):  # noqa: N802
        if self.path != "/report":
            return self._send_json(404, {"error": "unknown endpoint"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw.strip() else {}
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            runs = body.get("runs", _STATE.runs)
            issues = validate_runs(runs)
            if issues:
                raise ValueError(f"invalid runs: {issues[0]}")
            return self._send_json(200, build_report(runs))
        except (ValueError, TypeError, KeyError) as exc:
            return self._send_json(400, {"error": f"bad request: {exc}"})

    def log_message(self, *a: Any) -> None:
        pass


def make_server(port: int) -> HTTPServer:
    return HTTPServer(("127.0.0.1", port), _Handler)


def cmd_serve(args) -> int:
    if args.input:
        runs, code = _runs_or_error(args)
        if runs is None:
            return code
        reset_state(runs)
    server = make_server(args.port)
    print(f"serving on http://127.0.0.1:{args.port}  "
          f"(POST /report, GET /health)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-slo-tracker",
        description="SLO / error-budget tracker for agent runs "
                    "(Helios Home sample data).")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate the synthetic run dataset")
    g.add_argument("--seed", type=int, default=DEFAULT_SEED)
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_generate)

    for name, fn, helptext in (
            ("report", cmd_report, "full SLO report (JSON)"),
            ("budget", cmd_budget, "per-SLO error-budget blocks"),
            ("burn", cmd_burn, "burn rates per window + active alerts"),
            ("alerts", cmd_alerts, "active alerts + incident scoring"),
            ("check", cmd_check, "CI gate: exit 0 pass / 1 breach / 2 error")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--input", default=None,
                       help="runs file (.json array or .jsonl); "
                            "default: seeded dataset")
        s.add_argument("--out", default=None)
        s.set_defaults(func=fn)

    sub.add_parser("demo", help="run the end-to-end demo").set_defaults(
        func=cmd_demo)

    s = sub.add_parser("serve", help="localhost JSON API")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--input", default=None)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
