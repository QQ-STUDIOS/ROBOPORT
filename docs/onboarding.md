# Onboarding

You just cloned ROBOPORT. This is the 30-minute path from clone to "I can add an agent and watch it run."

---

## Prereqs

- Python 3.11+
- A local model server. We default to [Ollama](https://ollama.com) running `qwen3:14b` (matches the model in the Crew Builder UI). Any OpenAI-compatible endpoint works — just edit `config/agent_config.yaml`.
- `pip install jsonschema pyyaml` — that's it for the validation/benchmark scripts. The agents themselves bring no Python dependencies; they're Markdown.

```bash
# Pull the model (one-time, ~9GB)
ollama pull qwen3:14b
ollama serve  # if not already running
```

---

## Layout, in plain English

```
ROBOPORT/
├── agents/                    # all agent specs (Markdown w/ YAML frontmatter)
│   ├── core/                  # planner, executor, orchestrator, critic
│   ├── evaluation/            # grader, comparator, analyzer
│   ├── domain/                # generic domain agents
│   │   └── crew_builder/      # the 8 JD-Crew agents from the image
│   └── registry.json          # single source of truth: agents + crews
├── resources/
│   ├── prompts/               # cross-cutting prompt patterns
│   ├── schemas/               # JSON Schemas for all typed outputs + evals
│   ├── templates/             # report + pipeline scaffolds
│   ├── examples/              # canonical example outputs
│   └── datasets/              # input prompts for benchmarking
├── workflows/                 # executable specs: how a crew runs end-to-end
├── evals/
│   └── evals.json             # the live eval set
├── scripts/
│   ├── validate.py            # JSON-Schema-validates registry, schemas, evals
│   ├── benchmark.py           # runs evals, writes runs/<id>/
│   └── aggregate.py           # rolls up run results
├── runs/                      # one dir per run; produced artifacts
├── docs/
│   ├── architecture.md        # read this second
│   ├── agent_design_principles.md  # read this third
│   └── onboarding.md          # you are here
└── config/
    └── agent_config.yaml      # model bindings, temps, budgets, tools
```

If you only read three files: `docs/architecture.md`, then `agents/core/orchestrator.md`, then `workflows/jd_crew_flow.md`. That's the whole system.

---

## First five minutes: validate

Make sure the repo's internal references are sound:

```bash
cd ROBOPORT
python scripts/validate.py --all
```

Expected output:
```
✓ agents/registry.json valid
✓ resources/schemas/output.schema.json valid
✓ resources/schemas/eval.schema.json valid
✓ resources/schemas/grading.schema.json valid
✓ evals/evals.json valid (4 evals, 4 with blockers)
✓ all 19 registered agents have spec files
```

If any of those fail, fix before doing anything else.

---

## Second five minutes: trace the JD-Crew

Open three files side by side:

1. `workflows/jd_crew_flow.md` — narrative + the wave structure
2. `agents/registry.json` — the `crews.jd_crew.edges` block
3. `agents/domain/crew_builder/job_scout.md` — the entry agent

Walk the data: a goal comes in → Planner produces a plan with five waves → Orchestrator dispatches `job_scout` first → its output is `list[Job]`, validated against `output.schema.json#/$defs/Job` → that list fans out to `technical_analyst` and `compliance_risk` in parallel → both feed `application_strategist` → `synthesizer` (deterministic) merges into `FinalReport`.

This is the whole pattern. Every other crew you build is a variation on it.

---

## Third five minutes: dry-run the benchmark

```bash
python scripts/benchmark.py --target jd_crew --dry-run
```

`--dry-run` skips actual LLM calls — it stubs every model invocation with a synthetic, schema-valid response. You should see the orchestrator walk all 5 waves, write a run dir under `runs/`, and produce a final.json. Look at the run dir:

```bash
ls runs/<latest_run_id>/
# plan.json  run_log.jsonl  steps/  prompts/  final.json
```

Open `run_log.jsonl` — one line per step, with timing and status. Open `steps/04_synthesizer.json` — the deterministic merge output. This is what every real run looks like, just with synthetic content.

---

## Fourth five minutes: run for real

Drop `--dry-run` and provide a real prompt:

```bash
python scripts/benchmark.py \
  --target jd_crew \
  --input "Senior backend engineer roles at health-tech companies, remote, US"
```

This will hit your local Ollama. Wall time is dominated by the four LLM calls; on a workstation with a recent GPU, expect 30–90 seconds end-to-end. The final report lands in `runs/<run_id>/final.json`.

---

## Adding your first agent

Pick something small — say, a `keyword_extractor` that pulls the top 10 technical keywords from a JD.

1. **Spec it.** Create `agents/domain/keyword_extractor.md`. Copy the frontmatter from any existing agent and adapt. Keep the body to: Role, Inputs, Process, Output, Success criteria, Anti-patterns, Hand-off.

2. **Schema it.** If it returns a new type, add it to `resources/schemas/output.schema.json` under `$defs`.

3. **Register it.** Add an entry to `agents/registry.json` under `agents`. If it slots into JD-Crew, add an edge.

4. **Allow its tools.** If it uses tools, add them to `config/agent_config.yaml` under `tools.keyword_extractor`.

5. **Eval it.** Add at least one entry to `evals/evals.json` with `target: keyword_extractor` and at least one assertion marked `blocker: true`.

6. **Validate.** `python scripts/validate.py --all` — clean.

7. **Run.** `python scripts/benchmark.py --target keyword_extractor`.

If steps 1–7 take longer than 30 minutes for a simple agent, the friction is a bug — file an issue.

---

## Reading run logs

Every line in `run_log.jsonl` looks like:

```json
{"ts":"2026-04-26T01:30:12Z","step":2,"agent":"technical_analyst","status":"ok","wall_ms":4123,"llm_calls":1,"tokens_in":1840,"tokens_out":612,"tool_calls":0,"schema_valid":true}
```

Failures look like:

```json
{"ts":"...","step":2,"agent":"technical_analyst","status":"failed","layer":"call","reason":"schema_invalid","attempt":2,"detail":"missing required field 'seniority'"}
```

The `layer` field tells you which layer of the three-layer error stack handled the failure (call/step/user). See `docs/architecture.md` for the layer model.

---

## Common pitfalls

- **Skipping the eval.** Tempting because the agent feels obvious. Don't. The eval is what makes the agent's job legible to the next contributor.
- **Adding "context" fields to outputs.** If an agent needs an upstream value, add it to its declared `inputs` and have the Planner thread it. Stuffing it into a sibling agent's output as a side-channel is how typed contracts rot.
- **Editing prompts in code.** Prompts live in agent specs. Period. If you find yourself building a string in Python, you're working around the design.
- **Treating eval failures as eval bugs.** Sometimes they are. Most of the time they're agent bugs masquerading. Read the Grader's `meta_critique` field — it specifically tries to distinguish the two.

---

## Where to ask questions

- Architecture: `docs/architecture.md`
- "Why is this agent shaped this way?": that agent's `## Anti-patterns` section
- "How should I write this prompt?": `resources/prompts/reasoning_patterns.md`
- "What's the typed contract?": `resources/schemas/output.schema.json`

If the answer isn't in one of those four places, that's a documentation bug. Open a PR.
