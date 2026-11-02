"""Full SLO report assembly: per-SLO blocks, burn rates per window,
active alerts at dataset-now, projected exhaustion, per-agent table.

The report is a pure function of the runs - no clock, no randomness -
so local computation and the HTTP serve path agree byte-for-byte.
"""

from __future__ import annotations

from typing import Dict, List

from alerts import evaluate_alerts, rule_thresholds
from slo import (SLO_NAMES, TARGET, WindowIndex, burn_rates, dataset_now,
                 per_agent, projected_exhaustion, slo_block)


def build_report(runs: List[Dict]) -> Dict:
    now = dataset_now(runs)
    if now is None:
        raise ValueError("cannot report on an empty dataset")
    index = WindowIndex(runs)

    # The dataset spans exactly the 28-day window, so the SLO blocks
    # are totals over the whole index (eligible runs only, by
    # construction of WindowIndex).
    total_eligible = len(index.times)
    slos: Dict[str, Dict] = {}
    for name in SLO_NAMES:
        total_bad = index.total_bad(name)
        slos[name] = slo_block(total_eligible, total_eligible - total_bad)

    return {
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": 28,
        "total_runs": len(runs),
        "eligible_runs": total_eligible,
        "target": TARGET,
        "slos": slos,
        "burn_rates": {name: burn_rates(index, name, now)
                       for name in SLO_NAMES},
        "alert_thresholds": rule_thresholds(),
        "active_alerts": evaluate_alerts(index, now, slo="success"),
        "projected_exhaustion": {
            name: projected_exhaustion(
                slos[name]["budget"]["remaining"], index, name, now)
            for name in SLO_NAMES},
        "per_agent": per_agent(runs, now),
    }
