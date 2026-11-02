"""Deterministic synthetic agent-run generator - Helios Home (fictional).

SYNTHETIC DATA ONLY. No real company, customer, or agent data is ever
used. Every run is fabricated from a seeded PRNG so the same seed always
yields the same dataset, byte-for-byte.

One row per agent run, minute-stamped, over a rolling 28-day window
starting 2026-01-05T00:00:00Z (a Monday):

  run_id, timestamp (ISO Z), agent, status (success/failure),
  tool_error (bool), timeout (bool), latency_ms,
  eligibility (eligible / invalid_input / client_cancelled)

Three incidents are planted and labelled (see ``INCIDENTS`` and
``fixtures/incidents.json``):

  slow_burn  days 10-13 (~4 days) of moderately elevated failures
             (~13%): hot enough for the 3-day ticket rule, far too
             cool to page.
  blip       a single 10-minute burst of failures late in one hour
             on day 18: the short (5m) window runs hot, the long (1h)
             window does not - it must never page. That asymmetry is
             the entire point of the two-window alert rule.
  fast_burn  ~2 hours on day 24 at ~75% failures with degraded
             latency: must page, fast.

Baseline otherwise: ~1.5% status failures, latency mostly 200-600 ms
with a small slow tail, and rare tool errors / timeouts on otherwise
successful runs (a run can carry status=success and still not be
"good" - see slo.py).
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List

DEFAULT_SEED = 42
START = datetime(2026, 1, 5, 0, 0, 0, tzinfo=timezone.utc)  # a Monday
DAYS = 28
AGENTS = ["support-agent", "billing-agent", "diagnostics-agent"]

BASE_FAILURE_RATE = 0.015
SLOW_FAILURE_RATE = 0.13
FAST_FAILURE_RATE = 0.75

ROW_FIELDS = ["run_id", "timestamp", "agent", "status", "tool_error",
              "timeout", "latency_ms", "eligibility"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def _incident_windows():
    """(kind, start, end) datetimes for the three planted incidents."""
    slow_start = START + timedelta(days=10)
    slow_end = START + timedelta(days=14)          # days 10,11,12,13
    blip_start = START + timedelta(days=18, hours=14, minutes=50)
    blip_end = blip_start + timedelta(minutes=10)  # 14:50-15:00
    fast_start = START + timedelta(days=24, hours=10)
    fast_end = fast_start + timedelta(hours=2)     # 10:00-12:00
    return [("slow_burn", slow_start, slow_end),
            ("blip", blip_start, blip_end),
            ("fast_burn", fast_start, fast_end)]


INCIDENTS = [{"kind": kind, "start": _iso(s), "end": _iso(e)}
             for kind, s, e in _incident_windows()]

_INCIDENT_DT = {kind: (s, e) for kind, s, e in _incident_windows()}


def incident_at(dt: datetime):
    for kind, (s, e) in _INCIDENT_DT.items():
        if s <= dt < e:
            return kind
    return None


def _make_run(rng: random.Random, run_id: str, dt: datetime,
              failure_rate: float, degraded_latency: bool) -> Dict:
    # Eligibility first: a small share of runs never count toward
    # any SLI (bad caller input, caller hung up).
    roll = rng.random()
    if roll < 0.010:
        eligibility = "invalid_input"
    elif roll < 0.015:
        eligibility = "client_cancelled"
    else:
        eligibility = "eligible"

    failed = rng.random() < failure_rate
    status = "failure" if failed else "success"
    if failed:
        tool_error = rng.random() < 0.50
        timeout = (not tool_error) and rng.random() < 0.30
    else:
        # The trap the SLI definition exists for: a run can report
        # success yet have hit a tool error or a timeout on the way.
        tool_error = rng.random() < 0.005
        timeout = (not tool_error) and rng.random() < 0.002

    if degraded_latency:
        latency_ms = rng.randint(900, 1600)
    elif failed:
        latency_ms = rng.randint(300, 750)
    elif rng.random() < 0.02:
        latency_ms = rng.randint(850, 1200)  # slow tail
    else:
        latency_ms = rng.randint(200, 600)

    return {"run_id": run_id, "timestamp": _iso(dt),
            "agent": rng.choice(AGENTS), "status": status,
            "tool_error": tool_error, "timeout": timeout,
            "latency_ms": latency_ms, "eligibility": eligibility}


def generate_runs(seed: int = DEFAULT_SEED) -> List[Dict]:
    """The full seeded 28-day dataset (deterministic)."""
    rng = random.Random(seed)
    runs: List[Dict] = []
    counter = 0

    def emit(dt: datetime, failure_rate: float, degraded: bool):
        nonlocal counter
        counter += 1
        runs.append(_make_run(rng, f"run_{counter:05d}", dt,
                              failure_rate, degraded))

    for hour in range(DAYS * 24):
        hour_start = START + timedelta(hours=hour)
        kind = incident_at(hour_start + timedelta(minutes=30))
        if kind == "fast_burn":
            n, rate, degraded = rng.randint(45, 55), FAST_FAILURE_RATE, True
        elif kind == "slow_burn":
            n, rate, degraded = rng.randint(15, 25), SLOW_FAILURE_RATE, False
        else:
            n, rate, degraded = rng.randint(15, 25), BASE_FAILURE_RATE, False
        for minute in sorted(rng.randrange(60) for _ in range(n)):
            emit(hour_start + timedelta(minutes=minute), rate, degraded)

        # The blip is planted inside the 14:00 hour of day 18, in its
        # last ten minutes, as extra runs on top of that hour's normal
        # traffic - dense enough that the trailing 5-minute window at
        # the 15:00 boundary is genuinely hot.
        if hour_start == _INCIDENT_DT["blip"][0].replace(minute=0):
            for minute in sorted(rng.randrange(50, 60) for _ in range(12)):
                emit(hour_start + timedelta(minutes=minute), 1.0, False)

    runs.sort(key=lambda r: r["timestamp"])
    return runs


def runs_to_json(runs: List[Dict]) -> str:
    return json.dumps(runs, indent=None, sort_keys=True) + "\n"


def runs_to_jsonl(runs: List[Dict]) -> str:
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in runs)


def load_runs(path: str) -> List[Dict]:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError("expected a JSON array of runs")
        return data
    return [json.loads(line) for line in stripped.splitlines() if line.strip()]


def write_runs(runs: List[Dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(runs_to_jsonl(runs) if path.endswith(".jsonl")
                 else runs_to_json(runs))


def incidents_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "incidents.json")
