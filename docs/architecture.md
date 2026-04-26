# ROBOPORT Architecture

How the pieces fit. Read this before adding agents or changing the run loop.

---

## One-paragraph mental model

ROBOPORT is a **plan → dispatch → grade** loop. The Planner turns a user goal into a typed plan. The Orchestrator walks the plan, dispatching each step to the Executor with the right agent spec loaded. Outputs are typed (JSON Schema), logged per-step, and graded against an eval set. Crews — like JD-Crew — are just named, edge-defined arrangements of domain agents that the Planner can target wholesale.

If a step fails, three layers handle it: the call (retries), the step (alternate path), the user (clear escalation). Nothing is allowed to silently swallow an error.

---

## The four core agents

```
┌───────────┐   plan    ┌──────────────┐   step    ┌──────────┐
│  Planner  │──────────▶│ Orchestrator │──────────▶│ Executor │
└───────────┘           └──────┬───────┘           └────┬─────┘
                               │                        │
                               │   typed output         │
                               ▼                        ▼
                        ┌──────────┐            ┌─────────────┐
                        │  Critic  │            │ domain agent│
                        └──────────┘            │   (e.g.     │
                                                │ job_scout)  │
                                                └─────────────┘
```

- **Planner** — decomposes the goal into a typed plan (waves of steps, each with inputs/outputs/agent).
- **Orchestrator** — dispatches each step, threads outputs forward, owns the run log, decides retry/abort/escalate.
- **Executor** — actually invokes the agent: loads its spec, builds the prompt, calls the model or the deterministic function, validates the output against schema.
- **Critic** — pass/fix/fail verdict on any output. Used by the Orchestrator before passing data to the next step. Can recommend a re-run with edits.

These four are deliberately small. They do not know about JD analysis or any other domain. They just route typed values around.

---

## Domain agents

Domain agents are the real workhorses. Each one is a Markdown file with YAML frontmatter under `agents/domain/`. Crew Builder agents — the eight that show up in the image — live under `agents/domain/crew_builder/`. The frontmatter declares `inputs`, `outputs`, `model_hint`, and whether the agent is `deterministic`. The Markdown body is the prompt and the contract.

Adding a domain agent is intentionally cheap: write the spec, register it in `agents/registry.json`, add at least one eval. No code changes required for most agents.

---

## Crews

A **crew** is a named DAG of agents declared in `agents/registry.json` under `crews`. JD-Crew is the flagship example. The registry's `edges` list is the canonical structure; the Planner reads it, the Orchestrator walks it.

For JD-Crew specifically:

```
job_scout ──┬──▶ technical_analyst ──┐
            │                         ├──▶ application_strategist ──▶ synthesizer ──┬──▶ resume_tailor (optional)
            └──▶ compliance_risk ────┘                                               ├──▶ cover_letter_writer (optional)
                                                                                     └──▶ salary_estimator (optional)
```

Wave structure (what the Orchestrator actually executes):

| Wave | Agents | Notes |
|---:|---|---|
| 0 | `job_scout` | Aggregation + dedupe. Last step is deterministic. |
| 1 | `technical_analyst`, `compliance_risk` | Run in parallel. No shared state. |
| 2 | `application_strategist` | Joins both wave-1 outputs. |
| 3 | `synthesizer` | Pure Python merge. No LLM call. |
| 4 | `resume_tailor`, `cover_letter_writer`, `salary_estimator` | Optional; gated on `priority<=2` from synth. |

This wave structure is what gives the canonical flow stats `llm_calls=4, deterministic=2`.

---

## Data contracts

Every value crossing an agent boundary is typed. Schemas live in `resources/schemas/output.schema.json` and define:

- `Job` — a single posting from the scout
- `TechnicalAnalysis` — per-job tech read (skills, seniority, stack fit)
- `ComplianceAnalysis` — legal/regulatory findings, citations required
- `CandidateMatch` — strategist's ranked match w/ rationale
- `SalaryBand` — comp-band reasoning + range
- `FinalReport` — synth output; the human-shippable artifact

The Executor validates every agent's output against its declared schema before handing it off. A schema-invalid output is a step failure, period — never coerced, never partially accepted.

Eval format and grading-rubric format are also schema-defined (`eval.schema.json`, `grading.schema.json`).

---

## The three-layer error stack

This is non-negotiable. Every failure goes through these three layers, in order, with a clear handoff between each.

```
┌────────────────────────────────────────────────────────────────┐
│ LAYER 3 — User-facing                                          │
│   Clear message. What we tried. What blocked us. What's next.  │
│   Owned by: Orchestrator (escalation) + final report writer.   │
└────────────────────────────────────────────────────────────────┘
                              ▲
┌────────────────────────────────────────────────────────────────┐
│ LAYER 2 — Step-level                                           │
│   Retry with backoff. Try alternate agent. Skip-with-warning.  │
│   Mark step failed if budget blown.                            │
│   Owned by: Orchestrator policy table.                         │
└────────────────────────────────────────────────────────────────┘
                              ▲
┌────────────────────────────────────────────────────────────────┐
│ LAYER 1 — Call-level                                           │
│   Provider 5xx? Retry up to N. Schema-invalid? One repair pass.│
│   Tool 4xx? Don't retry — surface immediately.                 │
│   Owned by: Executor.                                          │
└────────────────────────────────────────────────────────────────┘
```

The forbidden pattern is a "quiet 200" — returning a structurally-valid but semantically-empty output to make a failure look like a success. See `resources/prompts/error_handling.md` for the full taxonomy.

---

## Run artifacts

Every run writes to `runs/<run_id>/`:

```
runs/<run_id>/
  plan.json              # the plan the Planner emitted
  run_log.jsonl          # one line per step: status, timing, tokens, tool calls
  steps/
    01_job_scout.json    # raw output of each step, schema-validated
    02_technical_analyst.json
    ...
  prompts/
    01_job_scout.txt     # literal prompt sent to the model
    ...
  final.json             # FinalReport (or whatever the crew's terminal type is)
  errors.jsonl           # any failures, w/ layer (call/step/user)
```

This is intentionally noisy. Disk is cheap; reproducing a one-shot run from memory is not.

---

## Evaluation pipeline

```
evals/evals.json ──▶ benchmark.py ──▶ runs/<id>/*  ──▶ aggregate.py ──▶ summary
                          │                                 ▲
                          └─▶ grader.py per-run ────────────┘
```

- **`evals/evals.json`** — the live eval set. Each eval names a target (agent or crew), an input, expected schema, and assertions (some marked `blocker`).
- **`scripts/benchmark.py`** — runs the targets against the eval set; produces a fresh run dir.
- **`grader`** agent — pass/fix/fail per assertion + meta-critique of the eval itself.
- **`scripts/aggregate.py`** — rolls runs up into pass-rate by agent, by assertion category, baseline-vs-candidate diff.

The eval set is treated as a living artifact. The Grader's `meta_critique` field surfaces weaknesses in the evals themselves — when an eval keeps producing ambiguous verdicts, that's a signal to rewrite the eval, not to keep grinding the agent against it.

---

## What ROBOPORT deliberately does not do

- **No global state.** Agents are pure functions of their declared inputs. If you find yourself wanting "context" or "memory" across agents, encode it as a typed field on an upstream output.
- **No prompt chaining inside an agent.** One agent = one model call (or one deterministic function). If you need three calls, you need three agents.
- **No silent fallbacks.** If a step degrades to a backup path, that fact is in the run log and bubbles up to the user.
- **No untyped boundaries.** If you can't write a schema for an output, you don't yet understand the agent's job well enough.

---

## Where to go next

- New to the codebase? → `docs/onboarding.md`
- Adding an agent? → `docs/agent_design_principles.md`
- Tuning models or budgets? → `config/agent_config.yaml`
- Running the JD crew? → `workflows/jd_crew_flow.md`
