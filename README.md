# agent-slo-tracker

**An SLO / error-budget tracker for AI agent runs over a rolling 28-day window — with multi-window burn-rate alerts whose thresholds are derived from the budget, not guessed, and an hour-by-hour scoring sweep that proves the alerts catch real incidents without crying wolf on blips.**

Pre-deploy evals tell you whether an agent *was* good in the lab. This project answers the question that starts the morning after go-live: *is it still good in production, and how fast are we spending the reliability we promised?* That promise is made precise. An **SLO** — service level objective, the target you commit to (here: 95% of runs good over 28 days). An **SLI** — service level indicator, the measured ratio the SLO judges (good runs ÷ eligible runs). An **error budget** — the failure you are allowed: at a 95% target over 13,317 eligible runs, exactly 665.85 bad runs for the whole window — a spendable allowance, not a vibe. A **burn rate** — how fast you are spending it: observed bad rate ÷ budgeted bad rate, so burn 1.0 spends the budget in exactly 28 days and burn 14 spends it in 2. This is the "operate it after go-live" capstone of the agent-reliability track: `eval-cost-budget-gate` is the pre-deploy gate, `trace-anomaly-detector` reads a single session, `agent-eval-dashboard` is the fleet view — this one owns the promise-keeping layer none of them cover.

> **Synthetic data only.** Every run is fabricated by a seeded generator for a fictional smart-home company (**Helios Home**) and its support / billing / diagnostics agents. No real company, customer, or agent data is used anywhere in this project.

Stdlib-only Python — deliberately. An SLO report is an audit artifact: anyone should be able to read every line of the math, re-run it offline, and get byte-identical outputs, with no dependency tree between them and the numbers. Zero network, zero API keys, zero external dependencies.

## Quickstart

```bash
# 1. Generate the synthetic Helios Home agent-run dataset
#    (28 days from 2026-01-05, seed 42)
python3 cli.py generate --out dataset.json

# 2. Full report: SLO blocks, burn rates, alerts, exhaustion, agents
python3 cli.py report            # seeded dataset (no --input needed)
python3 cli.py report --input dataset.json

# 3. Just the error budgets / just the burn rates + active alerts
python3 cli.py budget
python3 cli.py burn

# 4. Active alerts at dataset-now + hour-by-hour incident scoring
python3 cli.py alerts

# 5. CI gate: exit 0 = within budget and no page firing,
#    1 = budget exhausted or a page firing, 2 = data/IO error
python3 cli.py check; echo $?    # 0 on the seeded dataset

# 6. One-command end-to-end demo (generate -> report -> scoring -> serve)
python3 demo.py

# 7. Tiny localhost API: POST /report, GET /health on 127.0.0.1
python3 cli.py serve --port 8765
curl -s -X POST localhost:8765/report -d '{}'   # seeded dataset report
```

## The seeded window at a glance (seed 42, "now" = 2026-02-01T23:58Z)

The dataset holds **13,548 runs**, of which **13,317 are eligible** (the rest are `invalid_input` / `client_cancelled` and excluded from every SLI). "Now" is fixed at the dataset's last timestamp — never the system clock — so every number below reproduces exactly.

| SLO (target 95%) | Good / eligible | Observed | Budget consumed | Verdict |
|---|---|---|---|---|
| Success | 12,709 / 13,317 | 95.43% | **91.31%** (608 of 665.85 allowed bad) | ✅ compliant, 57.85 bad runs left |
| Latency (≤800 ms) | 12,966 / 13,317 | 97.36% | 52.71% (351 bad) | ✅ compliant |

Two honest observations the tracker exists to surface: the success budget is 91% spent with three incident scars in the window (below), so one more bad week breaches it — and the trailing 1-hour burn at "now" is 0.0, so no exhaustion is projected *at the current rate*. Both facts are in the report; neither requires a dashboard to interpret.

**Alert scoring (the headline).** The generator plants three labelled incidents (`fixtures/incidents.json`); `alerts.score_alerts` steps hour-by-hour through all **671 hour boundaries**, evaluates the alert rules at each, and scores:

| Planted incident | Expected | Result |
|---|---|---|
| `fast_burn` — Jan 29, 10:00–12:00, ~75% failures, latency degraded | PAGE | ✅ paged, time-to-detect **60 min** |
| `slow_burn` — Jan 15–18 (4 days), ~13% failures | TICKET, never page | ✅ ticketed, time-to-detect **1,080 min (18 h)**, never paged |
| `blip` — Jan 23, 14:50–15:00, a 10-minute failure burst | silence | ✅ never paged |
| Incident-free hours | no pages | ✅ **0 false pages** in 671 evaluated hours |

## How it works

**The SLIs.** A run is *eligible* unless it is marked `invalid_input` or `client_cancelled` — runs the agent never really attempted don't count for or against it. A run is *success-good* iff it ended `success` with **no tool error and no timeout** — the generator deliberately plants successful-looking runs carrying tool errors, because "the run completed" and "the run worked" are different claims. *Latency-good* (≤800 ms) is defined separately: a run can be success-good but latency-bad, and the evals pin that independence.

**Error budget.** Per SLO: `allowed = (1 − target) × eligible`; consumed is the observed bad count; consumed %, remaining, and remaining % are arithmetic on those two numbers. Nothing else is estimated.

**Burn rates** are reported for six windows ending at "now" (5m, 30m, 1h, 6h, 3d, 28d). On the seeded data the success burn at now reads 0.0 / 0.0 / 0.0 / 0.183 / 0.383 / 0.913 across those windows — quiet now, scarred over the full window.

**Multi-window alerts, derived thresholds.** A rule fires only when **both** its windows exceed the *same* threshold, and the threshold is computed in code (`slo.alert_threshold`) from first principles: to consume a fraction *f* of the whole budget inside one window, the bad rate in that window must run at *f ÷ (window ÷ 28 days)* times the budgeted rate.

| Rule | Windows | Budget fraction | Derivation | Threshold |
|---|---|---|---|---|
| PAGE fast | 1h **and** 5m | 2% in 1h | 0.02 ÷ (1/672) | **13.44** |
| PAGE slow | 6h **and** 30m | 5% in 6h | 0.05 ÷ (6/672) | **5.6** |
| TICKET | 3d **and** 6h | 10% in 3d | 0.10 ÷ (72/672) | **0.9333…** |

The conjunction is the design: the blip makes the 5-minute window run at burn 20 while the 1-hour window sits near 8 — hot short, cool long, no page. A slow leak never spikes any short window but holds the 3-day window over its line for days — ticket, not page. Only a genuine fast burn is hot on both scales at once.

**Projected exhaustion.** Remaining budget ÷ bad runs per hour in the trailing 1-hour window, reported in hours and days. Burn 0 → no projection (the honest answer is "not at this rate", not a made-up infinity date); an already-exhausted budget reports 0.

**Per-agent breakdown, with a volume guard.** Agents below **500 eligible runs** in the window are reported as `insufficient_data` with no burn computed — a burn rate over a handful of runs is noise wearing a costume. All three seeded agents clear the guard (~4,300–4,500 eligible runs each); billing-agent is the tightest at 99.6% of its success budget consumed.

## Architecture

| Module | Job |
|---|---|
| `generator.py` | Deterministic synthetic Helios Home run dataset (28 days, minute-stamped, ~15–25 runs/hour, denser during the fast burn): eligibility mix, failure/tool-error/timeout/latency process, three planted labelled incidents; JSON / JSONL serialisers and loaders |
| `slo.py` | The definitions: eligibility, success-good, latency-good; `slo_block` budget math; burn rates; `alert_threshold` derivation; projected exhaustion; per-agent table behind the 500-run guard; `WindowIndex` (time-sorted eligible runs + bad-count prefix sums, so window queries are binary searches) |
| `alerts.py` | The three two-window rules with derived thresholds; `evaluate_alerts` at any instant; `score_alerts` — the hour-by-hour sweep scored against the planted incidents (detection, time-to-detect, blip silence, false pages) |
| `report.py` | Full report assembly: per-SLO blocks, burn per window, thresholds, active alerts at dataset-now, projected exhaustion, per-agent table — a pure function of the runs |
| `validation.py` | Data-quality gate: required fields, enums, the int/bool trap on `latency_ms` and the flags, duplicate run IDs — reports issues, never raises on bad data |
| `cli.py` | `generate` / `report` / `budget` / `burn` / `alerts` / `check` (CI exit 0/1/2) / `demo` / `serve` (stdlib `http.server` on 127.0.0.1: `POST /report` with `{"runs": [...]}` or an empty body for the seeded dataset, `GET /health`) |
| `demo.py` | End-to-end narrative with asserts: determinism, headline report, incident scoring, check exit codes, live localhost serve round-trip |
| `fixtures/` | `incidents.json` — the three planted incidents, labelled with exact windows |

## Evals

`python3 evals/run_evals.py` → **15/15 golden evals**, report committed at `evals/eval_report.json` (md5 `bc85186ebbb21ef7b8e33a30988ebca5`, byte-identical across 3 verified runs):

- **E1** generator determinism — seed 42 JSONL bytes pinned by md5
- **E2** headline counts pinned (13,548 runs, 13,317 eligible, dataset validates clean)
- **E3** success SLO pinned (12,709 good, observed 0.954344, budget 91.3119% consumed, 57.85 remaining)
- **E4** latency SLO pinned (12,966 good, observed 0.973643, budget 52.7146% consumed)
- **E5** budget math hand-computed (200 eligible → 10.0 allowed; burn of a 10% bad rate = 2.0)
- **E6** good-run traps (success+tool_error and success+timeout are not good; latency-good is independent)
- **E7** thresholds derived, not hard-coded (13.44 / 5.6 / 0.933333 from the budget fractions)
- **E8** burn at dataset-now pinned (1h 0.0, 6h 0.183486, 3d 0.382916, 28d 0.913119; no alerts firing)
- **E9** alert scoring headline (fast page, time-to-detect 60 min; slow ticket, 1,080 min)
- **E10** two-window proof (blip never paged; 0 false pages across 671 evaluated hours)
- **E11** projected exhaustion (clean trailing hour → no projection; exhausted budget → 0)
- **E12** per-agent volume guard (seeded agents all report; a 10-run slice is `insufficient_data`)
- **E13** CLI `check` exit codes 0 / 1 / 2
- **E14** serve round-trip matches local computation (seeded and explicit runs)
- **E15** incidents fixture matches the generator's labels; a page fires at the fast-burn boundary

Unit tests: `python3 -m unittest discover -s tests` → **94 hermetic tests**, including a no-network enforcement test (sockets stubbed), hand-computed budget/burn/exhaustion cases, window boundary semantics (windows are (start, end]), the two-window rule on synthetic blip/sustained shapes, threshold-derivation values, CLI exit codes, validation defects (including the `latency_ms=True` bool trap), and serve round-trips.

## Development loop — what the checks actually caught

1. **The design was cross-checked before a line was written, and it changed the build.** The alert thresholds were specced as derived values (budget fraction ÷ window fraction) rather than the industry-copied constants 14.4 / 6 / 1 — the derivation is now a tested function, and the evals pin its outputs (13.44 / 5.6 / 0.9333…) so a future "just hard-code it" edit fails loudly.
2. **The blip only proves something if a scoring window actually sees it.** A 10-minute burst placed mid-hour is invisible to every hour-boundary evaluation of the 5-minute window — the sweep would report "never paged" vacuously. The blip is therefore planted in the *last* ten minutes of its hour (14:50–15:00), so the 15:00 evaluation's trailing 5-minute window is genuinely hot (burn 20 on pure failures) while the 1-hour window stays under the page line. The unit suite additionally proves the conjunction on synthetic data shaped the same way.
3. **The fast burn needed density, not just severity.** At the normal ~20 runs/hour, the trailing 5-minute window at an hour boundary holds ~2 runs, so the short-window half of the page rule would hinge on one or two coin flips. During the fast burn the generator emits ~50 runs/hour, giving the 5-minute window ~4–5 runs at ~75% failure — the page decision rests on a rate, not on luck. Verified in scoring: detection at the first boundary after the incident starts (60 minutes), with zero false pages anywhere else.
4. **First full run passed end-to-end** — 94 tests, 15/15 evals, demo green — with the incident parameters as designed (baseline ~1.5%, slow burn ~13%, fast burn ~75%). The validated design decisions worth keeping: "now" pinned to the dataset's last timestamp everywhere (report, alerts, scoring, exhaustion) so no result depends on when the code runs; window membership is (start, end] so a run exactly on a boundary is counted exactly once across adjacent windows; and the scoring sweep runs on prefix sums (`WindowIndex`), evaluating 671 hours × 6 windows in ~0.1 s instead of rescanning 13.5k runs per window.

## Scope and honest limits

- **Hour-granularity scoring.** The incident sweep evaluates at hour boundaries, so time-to-detect is measured in whole hours (the fast burn's 60 minutes is the sweep's resolution, not proof the page would take that long in a streaming system). Sub-hour detection latency is a property of the alerting pipeline, not of these thresholds.
- **Windows are trailing and calendar-free.** The 28-day window is the dataset itself; there is no notion of business hours, weekends, or traffic-weighted budgets. A production tracker would also age incidents out of the window — here that falls out of the trailing windows naturally as "now" advances past the dataset.
- **Two SLOs, one alerted.** Alerts are evaluated on the success SLO. The latency SLO is reported with the same burn machinery and could carry its own rules; wiring per-SLO alert policies is a config question this repo deliberately leaves visible rather than burying.
- **Per-agent is a table, not an alert source.** With the volume guard, per-agent burns are diagnostic. Alerting per agent would need per-agent budgets and a multiple-comparisons answer.
- **The natural next step is the dashboard tie-in:** this report dict is exactly the shape an SLO panel in `agent-eval-dashboard` would consume (per-SLO blocks, burn table, active alerts, exhaustion) — the serve endpoint is the seam, and joining them is named future work rather than a claim made here.
- **The API is a demo surface.** It binds 127.0.0.1 only, holds its dataset in memory, and resets on restart.

## Why this project

It takes the reliability discipline of site-reliability engineering — SLIs defined tightly enough to argue about, budgets treated as spendable allowances, alerts derived from the budget instead of folklore constants — and rebuilds it for the systems the author actually wants to ship and operate: AI agents. The same discipline a forward-deployed engineer brings to a customer problem: make the promise inspectable, make the alerting provable against planted failures, and make the failure modes exit codes.

## License

MIT — see LICENSE.
