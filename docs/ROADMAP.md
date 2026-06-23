# ROBOPORT Roadmap

## Product Thesis

ROBOPORT should commit to one primary identity:

> **The agent framework you can operate after the demo breaks.**

The existing repo already points here. The README positions ROBOPORT around typed
contracts, deterministic execution where possible, fail-loud behavior, and eval
blockers. The runtime writes run artifacts. The evaluation workflow already thinks
in baseline-vs-candidate terms. The Ops Console bridge already maps `run.log`
events into a live operational surface.

The roadmap therefore prioritizes **operability** over broader agent count, more
providers, or more polished demo views. The one question ROBOPORT should answer
better than any other framework:

> **Which agent, contract, model, tool, or routing decision caused this run to regress?**

## Current Baseline

Repo artifacts reviewed:

- `README.md`: positions ROBOPORT as "debuggable past the demo" — typed
  contracts, deterministic steps, fail-loud behavior, eval blockers.
- `agents/registry.json`: 19 registered agents, one concrete crew (`jd_crew`).
- `evals/evals.json`: 6 JD-Crew evals; 5 of 6 carry blocker assertions.
- `scripts/benchmark.py`: writes run dirs with `plan.json`, `final_output.json`,
  `run.log` (JSONL), optional `grading.json`, and a rolled-up `summary.json`.
- `scripts/aggregate.py`: rolled-up benchmark reports + baseline-vs-candidate at
  the eval-summary level.
- `scripts/roboport_runtime/executor.py`: injects output schemas into prompts and
  records schema issues as failed criteria.
- `workflows/evaluation_pipeline.md`: benchmark → grade → compare → decide →
  analyzer loop.
- Dashboard handoff: `dashboard/bridge.py`, `feed_adapter.js`, and the Ops Console
  translate live `run.log` events into a dashboard feed.

**The gap this roadmap closes:** the evaluation workflow compares benchmark
*summaries*, but nothing diffs two concrete run directories by typed contract and
attributes drift to a specific agent boundary.

> **Status:** Phase 1 v1 has landed — `scripts/diff_runs.py` + `tests/test_diff_runs.py`
> implement the criteria-anchored diff and all five acceptance gates, offline. The
> remaining phases build on it.

## Strategic Fork

Make the next 90 days **framework-first, product-shaped.**

ROBOPORT stays a framework at the core: typed agent specs, schema contracts, run
artifacts, evals, provider routing, reproducible benchmark comparisons. The Crew
Builder and Ops Console are **proof surfaces** for the framework, not the primary
product yet.

- **Primary audience now:** engineering teams building agent crews who need
  traceability, regression proof, and live operations.
- **Product surface now:** the Ops Console as the operational view of framework runs.
- **Deferred:** the full Crew Builder authoring product, until regression tracking
  and failure proof are real.

---

## Design Principle: benign drift vs. real regression

This is the crux of the whole roadmap, because it decides whether `diff_runs` is
signal or noise. Two *passing* runs of the same agent produce different free text;
a naive content diff would flag every run as regressed.

**Regression is anchored to criteria, not to prose.** The unit of regression is
*"a criterion / blocker that passed in the baseline now fails in the candidate,"*
plus schema validity, plus typed structured fields. Dimensions are tiered by
determinism:

| Tier | Signals | Verdict weight |
|---|---|---|
| **Hard** | step status, success criteria, grading verdicts (esp. blockers), schema validity | regression |
| **Soft** | llm/tool-call cost, latency, tool-call sequence | warning |
| **Informational** | free-text content changes | info (never a regression alone) |

Two refinements:

- **Schema-declared field stability** — annotate fields that *should* be
  reproducible (IDs, enums, counts, booleans) in `output.schema.json` (e.g.
  `x-roboport: { stable: true }`) so content diff focuses there and ignores
  volatile narrative.
- The virtuous consequence: **the quality of `diff_runs` is bounded by the quality
  of your criteria** — exactly the pressure the "evals are part of the agent"
  principle already wants.

## Baseline lifecycle

A baseline is a **blessed** run, not merely "the previous one."

- **Promotion rule:** a run becomes the baseline only when it passes all blockers
  (ideally on `main`). `evals/benchmarks/current_baseline.json` points at that run
  dir **plus the config fingerprint that produced it**.
- **The loop:** `diff_runs candidate vs current_baseline`; on green, optionally
  auto-promote the candidate (gated). That is what turns "diff two runs" into
  **regression-over-time** — the actual moat.

## Comparability precondition

Before diffing *outputs*, diff the *inputs*. If baseline and candidate came from a
different prompt, registry/crew version, or routing policy, the output diff is
meaningless.

- Record a **config fingerprint** per step in `run.log` (hash of registry entry +
  `agent_config` slice + model_hint + provider/model).
- `diff_runs` compares fingerprints (and the goal) first; a mismatch yields
  `verdict: inconclusive` with the reason — making `inconclusive` a real, useful
  state rather than an error. (v1 already gates on differing goals.)

---

## Roadmap Overview

| Phase | Theme | Outcome | Ship Gate |
|---|---|---|---|
| 0 | Baseline hardening | Current repo truth is documented and validated | `validate.py --all` passes; eval blocker gaps listed |
| 1 | Cross-run regression tracking | `diff_runs.py` compares two run dirs and attributes contract drift | Same two runs produce stable JSON + Markdown diff |
| 2 | Dashboard regression view | Ops Console can load a run comparison | Diff events render by agent/station with pass/fail severity |
| 3 | Provable error stack | Fault-injection evals prove call, step, and escalation paths | CI fails when a layer is only documented, not exercised |
| 4 | Observability-aware routing | Routing decisions include cost, latency, model, provider evidence | Route changes visible in run logs and aggregate reports |
| 5 | Crew expansion discipline | New crews land only with blocker evals and run-comparison coverage | No new agent/crew lands without blocker evals |

### Dependency graph & resequencing

The phase table hides three ordering facts that will bite if ignored:

1. **Per-step metadata for diffing is a Phase 1 blocker, not P0 backlog.** The
   benchmark run-dir `run.log` carries `status`, `criteria_results`, `tool_calls`,
   `llm_calls` per `step_done` — but **no per-step latency** (that lives only in
   the separate Ops Console log). Add `duration_ms` (and the config fingerprint) to
   `step_done` so latency/comparability dimensions become real. `diff_runs` already
   reads `duration_ms` if present.
2. **Phase 2 depends on a frozen, versioned Phase 1 JSON contract.** The dashboard
   parses the diff JSON; don't touch `feed_adapter.js` until the diff schema has a
   version field.
3. **Emit Phase 4's telemetry early, act on it late.** Log provider/model/cost/
   latency in Phase 0/1 even though routing logic lands in Phase 4 — Phase 1's
   cost/latency deltas need those fields anyway. (Mirrors the observability
   philosophy already in the repo: surfaces consume opt-in emitters.)

---

## Phase 0: Stabilize the baseline

**Goal:** establish what is already real and what is still aspirational.

Tasks:

1. Add `docs/OPERABILITY_BASELINE.md`.
2. Record the current operational surfaces: `runs/<run_id>/run.log`,
   `evals/benchmarks/<label>/summary.json`, the dashboard SSE bridge, file-drop
   replay mode.
3. Audit `evals/evals.json` for missing blockers (currently the `empty_results`
   edge case has none — decide whether that is intended).
4. Confirm current validation behavior: schema-invalid executor output is recorded
   as a failed criterion, not necessarily a hard failure; benchmark stubs remain
   unless `--live` is passed.
5. Add a short "known proof gaps" section: no per-step latency in the benchmark
   artifact; error stack not fully fault-injected; routing not cost/latency-aware;
   only JD-Crew is genuinely exercised end-to-end.

Acceptance gates:

```bash
python scripts/validate.py --all
python scripts/benchmark.py --target jd_crew --runs 1 --label phase0-smoke
python scripts/aggregate.py --benchmark evals/benchmarks/phase0-smoke
```

## Phase 1: Cross-run regression tracking — **v1 shipped**

**Goal:** compare a candidate run against a baseline and identify which agent
boundary drifted.

Primary artifact: **`scripts/diff_runs.py`** (criteria-anchored, offline, stdlib +
optional `jsonschema`).

```bash
python scripts/diff_runs.py \
  --baseline runs/<baseline_run_id> \
  --candidate runs/<candidate_run_id> \
  --out diff_against_baseline.json \
  --markdown diff_against_baseline.md
```

Diff dimensions:

| Dimension | Compared | Regression signal | Tier |
|---|---|---|---|
| Step status | `step_done.status` | `ok → failed` | hard |
| Criteria | `criteria_results` | `PASS → FAIL` | hard |
| Grading | `grading.json` verdicts | blocker / expectation `PASS → FAIL` | hard |
| Contract shape | schema validation by `output_type` | new schema invalidity | hard |
| Cost | llm/tool calls | material increase without quality gain | soft (warning) |
| Latency | per-step `duration_ms` (when logged) | p95 increase | soft (warning) |
| Content | canonical JSON of `final_output` | stable-field change | info |

Output envelope (deterministic, `sort_keys`, no timestamps):

```json
{
  "baseline": "runs/a", "candidate": "runs/b",
  "verdict": "pass|warning|regression|inconclusive",
  "summary": {
    "changed_agents": ["synthesizer"],
    "new_blocker_failures": 1, "schema_regressions": 0,
    "cost_delta_llm_calls": 1, "cost_delta_tool_calls": 0, "latency_delta_ms": null
  },
  "agent_diffs": [{
    "agent": "compliance_risk", "step_id": "compliance", "contract": "ComplianceAnalysis",
    "severity": "regression",
    "signals": [{"kind": "criterion_failed", "message": "...", "baseline": "PASS", "candidate": "FAIL"}],
    "recommended_next_action": "run analyzer on compliance_risk with baseline/candidate context"
  }]
}
```

Acceptance gates (all covered by `tests/test_diff_runs.py`):

1. Self-compare → `pass`.
2. Removing a required field from candidate `final_output.json` → `regression` (schema).
3. A new blocker failure in `grading.json` → `regression`.
4. Added llm/tool calls without blocker loss → `warning`, not regression.
5. Same inputs → byte-stable JSON.

Implementation notes: JSON parsing (not string diff); canonical key order; absent
optional artifacts → `inconclusive`, not pass; offline (no live provider); reuses
`resources/schemas/output.schema.json`.

Still open for Phase 1.x: stable-field annotations; config-fingerprint
comparability; per-step latency once `benchmark.py` logs `duration_ms`.

## Phase 2: Make the dashboard load-bearing

**Goal:** move the Ops Console from live-demo visualization to a regression
investigation surface.

1. Extend `dashboard/bridge.py` with a comparison mode (`--baseline`/`--candidate`).
2. Add diff event types to the feed:

   ```jsonl
   {"event":"diff.start","baseline":"...","candidate":"..."}
   {"event":"diff.agent","agent":"compliance_risk","severity":"regression","contract":"ComplianceAnalysis"}
   {"event":"diff.signal","agent":"compliance_risk","kind":"blocker_failed","message":"..."}
   {"event":"diff.complete","verdict":"regression"}
   ```
3. Map diff severity to station visuals in `feed_adapter.js`: pass=normal,
   warning=amber ring, regression=red ring, inconclusive=muted.
4. File-drop support for `diff_against_baseline.json` (works without the bridge).
5. A "Regression" panel: changed agents, signals, recommended next action.

## Phase 3: Make the error stack provable

**Goal:** turn the three-layer error stack from documented doctrine into enforced
behavior.

Fault-injection evals:

| Eval | Injected fault | Required behavior | Layer |
|---|---|---|---|
| `provider_5xx_retry` | transient 5xx | retry at call layer, then continue or fail loudly | 1 |
| `schema_invalid_repair` | schema-invalid JSON | repair pass or hard failure with schema evidence | 1 |
| `criterion_failed_retry` | valid JSON fails criterion | step-level retry or critic review | 2 |
| `budget_exceeded_abort` | call/tool budget exceeded | abort with budget failure record | 2 |
| `unsafe_escalation` | disallowed action requested | user-facing escalation, no tool side effect | 3 |
| `quiet_200_empty_results` | structurally valid but empty payload | fail loudly, no downstream execution | 1/2 |

**Shared seam with Phase 4 — build it once, early.** Phase 3 needs a fake/fault
provider; Phase 4 needs per-provider cost/latency/routing. Both want the *same*
abstraction: a `Provider` protocol the executor calls —

```
generate(prompt, schema, hints) -> Response{ text, tokens, cost_usd, latency_ms, provider, model, retry_count }
```

That one seam yields `FaultProvider` (Phase 3), a routing provider that picks among
candidates (Phase 4), and the telemetry Phase 4 logs (every `Response` carries the
cost/latency fields). Pull a thin protocol forward into Phase 1/0 enabling work;
`executor.py` already injects schemas and records issues, so it is the natural home.

CI must run without paid or local model dependencies, and must fail if any fault
eval loses a blocker.

## Phase 4: Observability-aware routing

**Goal:** make provider/model routing operationally visible and eventually
self-tuning.

1. Expand run-log events with provider, model, model_hint, prompt/completion
   tokens, `cost_usd` (when available), `latency_ms`, `retry_count`.
2. Add a per-agent routing summary to `summary.json`.
3. Teach `aggregate.py` to report cost/latency per *passing* run and blocker pass
   rate by provider/model, and to flag cost/latency regressions by agent.
4. Add an optional routing policy file:

   ```yaml
   routing:
     objective: blocker_pass_then_cost
     constraints: { max_latency_ms_per_step: 10000, max_usd_per_run: 5.00 }
     candidates:
       reasoning-strong:
         - { provider: ollama,    model: qwen3:14b }
         - { provider: anthropic, model: claude-sonnet-4-6 }
   ```

## Phase 5: Crew expansion with proof discipline

**Goal:** prevent breadth from outrunning proof.

Rules:

1. No new agent lands without blocker evals.
2. No new crew lands without: one happy-path, one empty/no-result, one
   schema/failure, and one anti-hallucination / evidence-grounding eval.
3. Every new crew must produce run artifacts compatible with `diff_runs.py`.
4. Every new dashboard station must map to a registry agent and output contract.
5. Generic agent count grows only when an eval proves an existing agent cannot
   cover the role.

First expansion candidates: **JD-Crew hardening** (give all six evals blockers)
before any second flagship crew. Do not add a second crew until Phases 1 and 3 are
complete.

---

## The end-state workflow: localize → explain

The highest-leverage experience the framework should deliver, and the thing no
other framework has:

> **`diff_runs` (deterministic, offline, free) localizes *where* a regression is.
> The `analyzer` agent (LLM, expensive) explains *why* — invoked only on the agents
> `diff_runs` flags.**

That cost-tiered debugging loop is already latent in the repo (`analyzer` agent +
the analyzer loop in `evaluation_pipeline.md`); `diff_runs` supplies the
`recommended_next_action` hook that triggers it.

## CI gating contract

What lets Phases 3 and 5 actually enforce ("CI fails when…") is `diff_runs.py`'s
exit-code contract:

- `0` pass / warning (default); `1` regression; `2` inconclusive.
- `--fail-on {regression,warning,inconclusive}` sets the gating threshold.
- The verdict thresholds (what is hard vs. soft; cost/latency deltas) belong in a
  small policy file, defaulting conservative: only blocker/schema/criteria losses
  are hard regressions; cost/latency are warnings. **That policy block is the
  governance layer for Phase 5.**

---

## Backlog

### P0
- ~~Add `scripts/diff_runs.py`.~~ **Done (v1).**
- ~~Unit fixtures for pass, schema drift, blocker drift, cost drift.~~ **Done.**
- Add `docs/OPERABILITY_BASELINE.md`.
- Add blockers to every eval in `evals/evals.json`.
- Add `duration_ms` + config fingerprint to `step_done` in `benchmark.py`.

### P1
- Add `evals/fault_injection.json` + a fake provider / `Provider` protocol seam.
- Dashboard diff event support + `diff_against_baseline.json` file-drop.
- Provider/model/cost/latency fields on run events.
- Stable-field (`x-roboport`) annotations in `output.schema.json`.

### P2
- Routing policy config; aggregate cost-per-passing-run reports.
- Analyzer handoff from `diff_runs.py`.
- `evals/benchmarks/current_baseline.json` pointer + promotion rule.
- CI job: benchmark smoke + fault-injection evals + `diff_runs --fail-on regression`.

## First Two-Week Execution Plan

**Week 1** — Day 1–3 (run loader, schema validation, canonical diff, criteria/
blocker/cost deltas, Markdown, self-compare test) are **substantially shipped** in
`diff_runs.py` v1. Day 4–5: wire `diff_runs` alongside `aggregate.py`, add the
README snippet, write `docs/OPERABILITY_BASELINE.md`, and patch eval blockers.

**Week 2** — Day 6–8: dashboard bridge diff mode, `diff.*` events in
`feed_adapter.js`, the Regression panel. Day 9–10: first fault-injection fixtures +
fake provider, smoke gates, and the promotion rules (candidate wins blockers and
introduces no schema regression; cost/latency regressions need explicit acceptance;
inconclusive → rerun, not promotion).

## Definition of Done

ROBOPORT can answer these from artifacts alone:

1. Did this run regress against the baseline?
2. Which agent or contract boundary changed?
3. Was the change quality, schema, cost, latency, routing, or tool-use related?
4. Did a blocker fail?
5. Can an operator see the regression in the dashboard without reading raw JSON?
6. Can CI prevent the same class of regression from landing again?

**Leading indicators** (so you can tell it is working before it is done): median
time-to-localize a regression (should fall), % of PRs to `main` carrying a
`diff_against_baseline.json`, baseline freshness (age of `current_baseline`), and
fault-eval coverage of all three error layers (target 100%).

## Near-Term Recommendation

`scripts/diff_runs.py` is the smallest slice that validates the thesis — and it now
exists. The next highest-leverage move is **Phase 2**: render its output in the Ops
Console, so the dashboard stops being a demo renderer and becomes the visual
debugger for ROBOPORT's actual moat — cross-run operability.
