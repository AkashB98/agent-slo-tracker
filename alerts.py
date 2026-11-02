"""Multi-window burn-rate alerts + hour-by-hour incident scoring.

An alert rule fires only when BOTH of its windows exceed the same
threshold. One hot short window is a blip; one hot long window is
old news. Both together mean the budget is being spent *now* at a
rate that matters - that conjunction is the whole design.

  PAGE (fast)   1h burn AND 5m burn  > thr(2% of budget in 1h)  = 13.44
  PAGE (slow)   6h burn AND 30m burn > thr(5% of budget in 6h)  = 5.6
  TICKET        3d burn AND 6h burn  > thr(10% of budget in 3d) = 0.9333...

Thresholds are computed by slo.alert_threshold from the budget
fraction and the window length - never hard-coded here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from generator import INCIDENTS, parse_ts
from slo import TARGET, WindowIndex, alert_threshold, burn_rate

# (name, severity, long window, short window, budget fraction)
RULES = [
    ("page_fast", "page", "1h", "5m", 0.02),
    ("page_slow", "page", "6h", "30m", 0.05),
    ("ticket", "ticket", "3d", "6h", 0.10),
]

_WINDOW_HOURS = {"5m": 5 / 60, "30m": 0.5, "1h": 1.0, "6h": 6.0,
                 "3d": 72.0, "28d": 672.0}
_WINDOW_DELTA = {"5m": timedelta(minutes=5), "30m": timedelta(minutes=30),
                 "1h": timedelta(hours=1), "6h": timedelta(hours=6),
                 "3d": timedelta(days=3), "28d": timedelta(days=28)}


def rule_thresholds() -> Dict[str, float]:
    out = {}
    for name, _sev, long_w, _short_w, fraction in RULES:
        out[name] = round(alert_threshold(fraction, _WINDOW_HOURS[long_w]), 6)
    return out


def evaluate_alerts(index: WindowIndex, now: datetime, slo: str = "success",
                    target: float = TARGET) -> List[Dict]:
    """Alerts firing at ``now`` for one SLO (default: success)."""
    burns = {}
    for w in _WINDOW_DELTA:
        eligible, bad = index.counts(slo, now - _WINDOW_DELTA[w], now)
        burns[w] = burn_rate(bad, eligible, target)
    thresholds = rule_thresholds()
    active = []
    for name, severity, long_w, short_w, _fraction in RULES:
        thr = thresholds[name]
        if burns[long_w] > thr and burns[short_w] > thr:
            active.append({"rule": name, "severity": severity, "slo": slo,
                           "threshold": thr,
                           "long_window": long_w, "long_burn": burns[long_w],
                           "short_window": short_w,
                           "short_burn": burns[short_w]})
    return active


def score_alerts(runs: List[Dict],
                 incidents: Optional[List[Dict]] = None) -> Dict:
    """Step hour-by-hour through the dataset, evaluate alerts at each
    hour boundary (success SLO), and score against the planted
    incidents. Deterministic: boundaries run from the first whole
    hour after the first run to the dataset's last timestamp."""
    if incidents is None:
        incidents = INCIDENTS
    index = WindowIndex(runs)
    if not index.times:
        return {"fast_burn_detected": False, "fast_burn_ttd_minutes": None,
                "slow_burn_ticketed": False, "slow_burn_ttd_minutes": None,
                "blip_paged": False, "false_pages": 0,
                "evaluated_hours": 0}
    windows = {inc["kind"]: (parse_ts(inc["start"]), parse_ts(inc["end"]))
               for inc in incidents}
    fast_s, fast_e = windows["fast_burn"]
    slow_s, slow_e = windows["slow_burn"]
    blip_s, blip_e = windows["blip"]

    t = index.times[0].replace(minute=0, second=0) + timedelta(hours=1)
    last = index.times[-1]
    fast_ttd = slow_ttd = None
    blip_paged = False
    false_pages = 0
    hours = 0
    while t <= last:
        hours += 1
        active = evaluate_alerts(index, t)
        paged = any(a["severity"] == "page" for a in active)
        ticketed = any(a["severity"] == "ticket" for a in active)
        in_fast = fast_s <= t <= fast_e + timedelta(hours=1)
        if paged and in_fast and fast_ttd is None:
            fast_ttd = int((t - fast_s).total_seconds() // 60)
        if paged and blip_s <= t <= blip_e + timedelta(hours=1):
            blip_paged = True
        if paged and not in_fast:
            false_pages += 1
        if ticketed and slow_s <= t <= slow_e and slow_ttd is None:
            slow_ttd = int((t - slow_s).total_seconds() // 60)
        t += timedelta(hours=1)

    return {"fast_burn_detected": fast_ttd is not None,
            "fast_burn_ttd_minutes": fast_ttd,
            "slow_burn_ticketed": slow_ttd is not None,
            "slow_burn_ttd_minutes": slow_ttd,
            "blip_paged": blip_paged,
            "false_pages": false_pages,
            "evaluated_hours": hours}
