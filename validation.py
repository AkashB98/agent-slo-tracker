"""Data-quality gate for agent-run records.

Reports issues, never raises on bad data, so the CLI ``check`` gate
can map data problems to exit code 2 - distinct from a budget breach
(exit 1). Also enforces the int/bool trap: in Python ``True`` is an
``int``, so latency must be checked as a real int and the flags as
real bools, in the right order.
"""

from __future__ import annotations

from typing import Dict, List

from generator import AGENTS, parse_ts

STATUSES = ("success", "failure")
ELIGIBILITIES = ("eligible", "invalid_input", "client_cancelled")


def validate_run(run) -> List[str]:
    if not isinstance(run, dict):
        return ["run is not an object"]
    issues = []
    if not isinstance(run.get("run_id"), str) or not run.get("run_id"):
        issues.append("run_id must be a non-empty string")
    try:
        parse_ts(run.get("timestamp", ""))
    except (ValueError, TypeError, AttributeError):
        issues.append("timestamp must be ISO Z (YYYY-MM-DDTHH:MM:SSZ)")
    if run.get("agent") not in AGENTS:
        issues.append(f"agent must be one of {AGENTS}")
    if run.get("status") not in STATUSES:
        issues.append(f"status must be one of {STATUSES}")
    for flag in ("tool_error", "timeout"):
        if not isinstance(run.get(flag), bool):
            issues.append(f"{flag} must be a bool")
    latency = run.get("latency_ms")
    if isinstance(latency, bool) or not isinstance(latency, int):
        issues.append("latency_ms must be an int")
    elif latency < 0:
        issues.append("latency_ms must be >= 0")
    if run.get("eligibility") not in ELIGIBILITIES:
        issues.append(f"eligibility must be one of {ELIGIBILITIES}")
    return issues


def validate_runs(runs) -> List[str]:
    if not isinstance(runs, list):
        return ["dataset is not a list"]
    issues: List[str] = []
    seen = set()
    for i, run in enumerate(runs):
        for issue in validate_run(run):
            issues.append(f"run[{i}]: {issue}")
        if isinstance(run, dict):
            rid = run.get("run_id")
            if rid in seen:
                issues.append(f"run[{i}]: duplicate run_id {rid}")
            seen.add(rid)
        if len(issues) > 50:
            break
    if not runs:
        issues.append("dataset is empty")
    return issues
