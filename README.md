# ROBOPORT

An agent orchestration framework with typed contracts, deterministic-when-possible execution, and a non-negotiable error stack.

The flagship example shipped with the repo is **JD-Crew** — the eight-agent sequential job-description analyzer pictured in the Crew Builder UI: `scout → (technical, compliance) → strategist → synth (+ optional resume_tailor, cover_letter_writer, salary_estimator)`.

```
┌──────────┐   ┌─────────────────┐   ┌──────────────────┐   ┌─────────────┐
│ job      │──▶│ technical_      │──┐                                       
│ scout    │   │ analyst         │  │                                       
│          │   └─────────────────┘  ├─▶ application_  ─▶ synthesizer ──┬──▶ resume_tailor
│ list[Job]│   ┌─────────────────┐  │   strategist        (FinalReport)│
│          │──▶│ compliance_     │──┘                                  ├──▶ cover_letter_writer
└──────────┘   │ risk            │                                     │
               └─────────────────┘                                     └──▶ salary_estimator
```

Canonical run hits the flow stats from the Crew Builder UI: `llm_calls=4, deterministic=2, triggers=2, tools_attached=10`.

---

## Why this exists

Most "agent frameworks" optimize for the demo. ROBOPORT optimizes for the second week — when the agent that worked on Tuesday is producing nonsense on Friday and you need to figure out which step regressed.

The opinions baked in:

- **Typed contracts at every boundary.** Every output is JSON-Schema-validated before handoff. No untyped blobs.
- **Deterministic when possible.** The Synthesizer is pure Python. The Job Scout's dedupe is pure Python. If you can write it as `def f(x) -> y`, you don't get to call an LLM.
- **One agent, one job.** No agent both "analyzes and recommends" — those are two agents.
- **Fail loudly.** The forbidden pattern is the *quiet 200* — a structurally-valid response that semantically failed. Empty arrays don't mean "no results"; they mean the search broke.
- **The spec is the prompt.** Agent Markdown bodies are the literal prompts. No drift, no template assembly logic.
- **Evals are part of the agent.** No agent lands without at least one blocker assertion in `evals/evals.json`.

The full philosophy is in [`docs/agent_design_principles.md`](docs/agent_design_principles.md).

---

## Quick start

```bash
# Prereqs: Python 3.11+, Ollama running qwen3:14b (or edit config/agent_config.yaml)
pip install jsonschema pyyaml

# Sanity-check the repo
python scripts/validate.py --all

# Dry-run the JD crew (no LLM calls — synthetic schema-valid stubs)
python scripts/benchmark.py --target jd_crew --dry-run

# Real run
python scripts/benchmark.py \
  --target jd_crew \
  --input "Senior backend engineer roles at health-tech companies, remote, US"

# See the artifacts
ls runs/<run_id>/
```

Full walkthrough: [`docs/onboarding.md`](docs/onboarding.md).

---

## Layout

```
agents/
  core/               planner, executor, orchestrator, critic
  evaluation/         grader, comparator, analyzer
  domain/             generic domain agents
  domain/crew_builder/  the 8 JD-Crew agents from the image
  registry.json       single source of truth for agents + crews
resources/
  prompts/            cross-cutting reasoning, tool-use, error-handling patterns
  schemas/            JSON Schema for every typed boundary
  templates/          report + pipeline scaffolds
  examples/           canonical example outputs
  datasets/           input prompts for benchmarking
workflows/            executable specs: how a crew runs end-to-end
evals/evals.json      live eval set (4 evals, all with blockers)
scripts/              validate, benchmark, aggregate
runs/                 produced artifacts; one dir per run
docs/                 architecture, design principles, onboarding
config/agent_config.yaml   model bindings, temps, budgets, tool whitelist
```

The 19 registered agents — 4 core, 3 evaluation, 4 generic domain, 8 crew_builder — are listed in `agents/registry.json`.

---

## The four core agents

| Agent | Role | Deterministic? |
|---|---|:-:|
| `planner` | Decomposes goal → typed plan (waves of steps) | no |
| `executor` | Loads agent spec, builds prompt, invokes model/fn, validates output | no |
| `orchestrator` | Dispatches plan, threads outputs, owns failure policy | mostly |
| `critic` | Pass/fix/fail on outputs before handoff | no |

These four don't know about JD analysis or any other domain. They route typed values around.

---

## The JD-Crew agents (from the image)

| Agent | Output type | Deterministic? |
|---|---|:-:|
| `job_scout` | `list[Job]` | mixed (search → LLM, dedupe → Python) |
| `technical_analyst` | `TechnicalAnalysis` | no |
| `compliance_risk` | `ComplianceAnalysis` | no |
| `application_strategist` | `CandidateMatch` | no |
| `synthesizer` | `FinalReport` | **yes** (pure merge) |
| `salary_estimator` | `SalaryBand` | no |
| `resume_tailor` | `TailoredResume` | no |
| `cover_letter_writer` | `CoverLetter` | no |

Every one of these has a Markdown spec under `agents/domain/crew_builder/`, an entry in `agents/registry.json`, and at least one eval covering it.

---

## The three-layer error stack

Non-negotiable. Every failure goes through these three layers in order, with explicit handoffs:

```
LAYER 3 — User-facing       (Orchestrator escalation; clear msg to caller)
   ▲
LAYER 2 — Step-level        (retry/alternate/skip-with-warning)
   ▲
LAYER 1 — Call-level        (provider 5xx → retry; schema-invalid → repair pass)
```

Full taxonomy: [`resources/prompts/error_handling.md`](resources/prompts/error_handling.md).

---

## Adding an agent

Short version (full version in [`docs/agent_design_principles.md`](docs/agent_design_principles.md)):

1. Write `agents/<role>/<n>.md` with frontmatter + the seven required sections.
2. Add an entry to `agents/registry.json`.
3. Add the output schema to `resources/schemas/output.schema.json`.
4. Whitelist tools in `config/agent_config.yaml`.
5. Add at least one eval with at least one blocker.
6. `python scripts/validate.py --all` — clean.
7. `python scripts/benchmark.py --target <agent>` — passing.

---

## Documentation map

| File | When to read it |
|---|---|
| [`docs/onboarding.md`](docs/onboarding.md) | First — clone-to-running in 30 minutes. |
| [`docs/architecture.md`](docs/architecture.md) | Second — how the pieces fit. |
| [`docs/agent_design_principles.md`](docs/agent_design_principles.md) | Third — the opinions, before you add an agent. |
| [`workflows/jd_crew_flow.md`](workflows/jd_crew_flow.md) | The flagship crew, end-to-end. |
| [`resources/prompts/reasoning_patterns.md`](resources/prompts/reasoning_patterns.md) | Reference for prompt design. |
| [`resources/prompts/error_handling.md`](resources/prompts/error_handling.md) | Reference for failure modes. |

---

## License

MIT. See `LICENSE`.
