"""Shared run builders for the test suite (not a test module)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_counter = [0]


def run(timestamp="2026-01-05T00:00:00Z", agent="support-agent",
        status="success", tool_error=False, timeout=False,
        latency_ms=300, eligibility="eligible"):
    _counter[0] += 1
    return {"run_id": f"test_{_counter[0]:04d}", "timestamp": timestamp,
            "agent": agent, "status": status, "tool_error": tool_error,
            "timeout": timeout, "latency_ms": latency_ms,
            "eligibility": eligibility}


def burst(start_minute, count, bad=True, step_minutes=1):
    """``count`` runs on day 1 from ``start_minute``, 1/minute apart."""
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)
    out = []
    for i in range(count):
        ts = (base + timedelta(minutes=start_minute + i * step_minutes))
        out.append(run(timestamp=ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       status="failure" if bad else "success",
                       tool_error=bad))
    return out
