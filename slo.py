"""SLI / SLO / error-budget / burn-rate math for agent runs.

Definitions (also in the README - the code and the prose must agree):

  Eligible runs: every run except those marked ``invalid_input`` or
  ``client_cancelled``. Ineligible runs are excluded from every SLI.

  Success-good: status == "success" AND no tool error AND no timeout.
  Latency-good: latency_ms <= 800. Defined separately on purpose: a
  run can be success-good but latency-bad, and vice versa.

  Each SLO: good / eligible over the rolling 28-day window, target
  0.95 for both the success SLO and the latency SLO.

  Error budget: allowed bad = (1 - target) * eligible. Consumed is
  the observed bad count; the rest is arithmetic.

  Burn rate: observed bad rate / (1 - target). Burn 1.0 = failing at
  exactly the rate that would consume the whole 28-day budget in
  exactly 28 days; burn 14 = eating it 14x too fast.

All windows end at a fixed "now" - the dataset's last timestamp,
never the system clock - so every computation is reproducible.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from generator import parse_ts

TARGET = 0.95
LATENCY_THRESHOLD_MS = 800
WINDOW_DAYS = 28
MIN_AGENT_RUNS = 500

# window name -> duration, in report order
WINDOWS = [("5m", timedelta(minutes=5)), ("30m", timedelta(minutes=30)),
           ("1h", timedelta(hours=1)), ("6h", timedelta(hours=6)),
           ("3d", timedelta(days=3)), ("28d", timedelta(days=28))]

SLO_NAMES = ("success", "latency")


def is_eligible(run: Dict) -> bool:
    return run.get("eligibility", "eligible") == "eligible"


def is_success_good(run: Dict) -> bool:
    return (run.get("status") == "success"
            and not run.get("tool_error")
            and not run.get("timeout"))


def is_latency_good(run: Dict) -> bool:
    return run.get("latency_ms", 0) <= LATENCY_THRESHOLD_MS


_GOOD_FN = {"success": is_success_good, "latency": is_latency_good}


def dataset_now(runs: List[Dict]) -> Optional[datetime]:
    """Fixed 'now': the dataset's last timestamp (never the clock)."""
    if not runs:
        return None
    return max(parse_ts(r["timestamp"]) for r in runs)


class WindowIndex:
    """Eligible runs sorted by time with prefix counts per SLO, so a
    window query is two binary searches instead of a full scan. The
    alert-scoring sweep evaluates ~672 hour boundaries x 6 windows;
    this keeps that sweep fast and exact."""

    def __init__(self, runs: List[Dict]):
        eligible = sorted((r for r in runs if is_eligible(r)),
                          key=lambda r: r["timestamp"])
        self.times: List[datetime] = [parse_ts(r["timestamp"])
                                      for r in eligible]
        self._prefix: Dict[str, List[int]] = {}
        for name in SLO_NAMES:
            fn = _GOOD_FN[name]
            prefix = [0]
            for r in eligible:
                prefix.append(prefix[-1] + (0 if fn(r) else 1))
            self._prefix[name] = prefix  # prefix[i] = bad among first i

    def total_bad(self, slo: str) -> int:
        return self._prefix[slo][-1]

    def counts(self, slo: str, start: datetime, end: datetime):
        """(eligible, bad) for start < t <= end."""
        lo = bisect_right(self.times, start)
        hi = bisect_right(self.times, end)
        return hi - lo, self._prefix[slo][hi] - self._prefix[slo][lo]


def slo_block(eligible: int, good: int, target: float = TARGET) -> Dict:
    bad = eligible - good
    allowed = (1.0 - target) * eligible
    observed = (good / eligible) if eligible else 0.0
    consumed_pct = (bad / allowed) if allowed else 0.0
    remaining = allowed - bad
    return {
        "target": target,
        "eligible": eligible,
        "good": good,
        "bad": bad,
        "observed": round(observed, 6),
        "compliant": observed >= target,
        "budget": {
            "allowed": round(allowed, 4),
            "consumed": bad,
            "consumed_pct": round(consumed_pct, 6),
            "remaining": round(remaining, 4),
            "remaining_pct": round(remaining / allowed, 6) if allowed else 0.0,
        },
    }


def burn_rate(bad: int, eligible: int, target: float = TARGET) -> float:
    """Observed bad rate / budgeted bad rate. 0.0 when no runs."""
    if eligible == 0:
        return 0.0
    return round((bad / eligible) / (1.0 - target), 6)


def burn_rates(index: WindowIndex, slo: str, now: datetime,
               target: float = TARGET) -> Dict[str, float]:
    out = {}
    for name, delta in WINDOWS:
        eligible, bad = index.counts(slo, now - delta, now)
        out[name] = burn_rate(bad, eligible, target)
    return out


def alert_threshold(budget_fraction: float, window_hours: float,
                    window_days: int = WINDOW_DAYS) -> float:
    """Multi-window alert threshold, derived - not a magic number.

    To consume ``budget_fraction`` of the whole budget inside one
    window, the bad rate during that window must run at
    budget_fraction / (window / total window) times the budgeted
    rate. Threshold = budget_fraction / (window_hours / (28d in h)).
    """
    return budget_fraction / (window_hours / (window_days * 24.0))


def projected_exhaustion(remaining_budget: float, index: WindowIndex,
                         slo: str, now: datetime,
                         target: float = TARGET) -> Optional[Dict]:
    """Hours/days until the budget runs out at the 1h burn speed.

    Consumption speed = bad runs per hour observed in the trailing
    1h window. Burn 0 (or no runs) -> no exhaustion projected (None).
    An already-exhausted budget reports 0 hours.
    """
    if remaining_budget <= 0:
        return {"hours": 0.0, "days": 0.0}
    _, bad_1h = index.counts(slo, now - timedelta(hours=1), now)
    if bad_1h == 0:
        return None
    hours = remaining_budget / bad_1h
    return {"hours": round(hours, 4), "days": round(hours / 24.0, 4)}


def per_agent(runs: List[Dict], now: datetime,
              min_runs: int = MIN_AGENT_RUNS) -> List[Dict]:
    """Per-agent success/latency blocks over the trailing 28 days,
    behind a minimum-volume guard: below ``min_runs`` eligible runs an
    agent is reported as insufficient_data with no burn computed -
    a burn rate over a handful of runs is noise wearing a costume."""
    start = now - timedelta(days=WINDOW_DAYS)
    by_agent: Dict[str, List[Dict]] = {}
    for r in runs:
        if is_eligible(r) and start < parse_ts(r["timestamp"]) <= now:
            by_agent.setdefault(r["agent"], []).append(r)
    out = []
    for agent in sorted(by_agent):
        agent_runs = by_agent[agent]
        eligible = len(agent_runs)
        if eligible < min_runs:
            out.append({"agent": agent, "eligible": eligible,
                        "status": "insufficient_data"})
            continue
        entry = {"agent": agent, "eligible": eligible, "status": "ok"}
        for slo in SLO_NAMES:
            good = sum(1 for r in agent_runs if _GOOD_FN[slo](r))
            block = slo_block(eligible, good)
            entry[slo] = {"observed": block["observed"],
                          "compliant": block["compliant"],
                          "budget_consumed_pct":
                              block["budget"]["consumed_pct"]}
        out.append(entry)
    return out
