# Agent Design Principles

The opinions baked into every agent in this repo. Follow them when adding new ones; push back hard when reviewing PRs that don't.

These are not aspirational. Every existing agent in `agents/` already conforms. New agents that ignore these principles will wash out in the first eval cycle.

---

## 1. Typed contracts at every boundary

Every agent declares its `inputs` and `outputs` in YAML frontmatter, and every output has a JSON Schema in `resources/schemas/output.schema.json`. The Executor validates schema before handoff.

**Why:** Untyped boundaries hide bugs that only surface three steps downstream, where they're expensive to debug. Schema validation at every hop means failures land where they were caused.

**What this rules out:**
- Returning natural-language prose as the primary output of any non-final agent.
- "Optional" fields that some downstream agents require and some don't.
- Free-form `metadata` bags. If a field matters, name it.

---

## 2. Deterministic when possible

Not every agent needs a model. The Synthesizer is a pure Python merge. The Job Scout's dedupe step is deterministic. If an agent's job is "combine these inputs by these rules," it should not be calling an LLM.

**Why:** Determinism is free reliability. A deterministic step has zero variance, costs nothing to re-run, and never hallucinates a field.

**The test:** Can you write the agent's logic as `def f(inputs) -> output` without an LLM? If yes, do that. The frontmatter declares `deterministic: true` and the registry sets `model_hint: none`.

---

## 3. One agent, one job

Each agent does one thing. The Technical Analyst reads a JD and outputs a `TechnicalAnalysis`. It does not also score the candidate, that's the Strategist's job. It does not also tailor a resume, that's the Resume Tailor's job.

**Why:** Composite agents are hard to evaluate. When `analyst_or_strategist_or_tailor` produces a bad output, you don't know which sub-task failed. You can't isolate prompts. Eval coverage collapses to the union of three blurry rubrics.

**The test:** Can you write the agent's success criteria in three sentences? If you need bullet points and "additionally," split it.

---

## 4. Fail loudly

Every failure mode is explicit. No empty arrays returned to mean "couldn't find anything." No generic strings. No partial outputs that look complete.

The forbidden pattern: the **quiet 200** — a structurally-valid response that semantically failed. Returning `{"matches": []}` because the search broke is worse than returning an error, because every downstream agent now operates on bad data.

**What this looks like in practice:**
- Required outputs always populated, or the agent raises.
- Schema includes a `confidence` field where applicable; the Critic uses it to gate downstream steps.
- Every agent spec has an "Anti-patterns" section enumerating known failure modes.

---

## 5. Citation discipline

Any agent making a factual claim about an external artifact (job description, resume, regulation) must cite the verbatim source span. No paraphrasing.

The Compliance & Risk agent's findings each include a quoted excerpt from the JD. The Resume Tailor's edits each map back to a master-resume bullet. If the source isn't quotable, the claim doesn't get made.

**Why:** Paraphrased citations are how hallucinations leak into outputs that look authoritative. The audit trail is the defense.

---

## 6. Two-hat reasoning

Reasoning agents (the Critic, the Grader, the Strategist) write under two hats: the **proposer** drafts the output, the **reviewer** flags problems with the draft. Both hats live in the same prompt. The final output incorporates the reviewer's pushback or explains why it was rejected.

**Why:** Single-pass generation rewards confident-sounding output. Forcing an explicit review step catches the easy bugs — unsupported claims, missed inputs, mixed-up entities — before they ship.

See `resources/prompts/reasoning_patterns.md` for the prompt skeleton.

---

## 7. Tools are whitelisted, not advertised

Each agent declares the tools it can use in `config/agent_config.yaml` under `tools.<agent_id>`. The runner refuses to dispatch any tool not on the list. Agents are never told "you have access to tools X, Y, Z" — they are told "use the tool best suited to the task; if none fit, say so."

**Why:** Telling an LLM about a tool is sometimes enough to make it call the tool whether or not it should. Whitelisting keeps the agent focused on the inputs it was given.

---

## 8. Anti-patterns are documented per-agent

Every agent spec has an `## Anti-patterns` section listing the failure modes that have actually shown up in evals or the wild. New anti-patterns get added when discovered, not retroactively justified.

**Why:** This section is the institutional memory of the agent. It's the first thing a new contributor reads when they're confused about why a prompt is shaped a certain way.

---

## 9. The eval is part of the agent

You cannot land a new agent without at least one entry in `evals/evals.json` targeting it. At least one assertion must be marked `blocker: true`.

**Why:** An agent without an eval is an agent without a definition of success. The eval doesn't have to be exhaustive — it has to exist and have a blocker. The eval set is iterated alongside the agent.

---

## 10. The spec is the prompt

The agent's Markdown body is the literal prompt sent to the model (with the input bound at the top and the output schema appended). There is no separate "prompt template" file. There is no prompt assembly logic that mutates the spec.

**Why:** Prompt drift is silent and brutal. If the spec and the prompt are the same artifact, the spec is always live, and changes to behavior require changes to a tracked file.

The Executor handles input/output binding; everything else is verbatim.

---

## How to add a new agent — the short version

1. Write `agents/<role>/<name>.md` with frontmatter and the seven sections every existing agent has: Role, Inputs, Process (or Capabilities), Output, Success criteria, Anti-patterns, Hand-off.
2. Add an entry to `agents/registry.json` under `agents` (and to a crew's `edges` if it's part of one).
3. If it has a new output type, add the schema to `resources/schemas/output.schema.json`.
4. Add tools to `config/agent_config.yaml` if it needs any.
5. Add at least one eval to `evals/evals.json` with at least one blocker assertion.
6. Run `python scripts/validate.py --all` — registry, schemas, and evals must all be clean.
7. Run `python scripts/benchmark.py --target <agent_name>` — it should pass its own evals before review.

If you find yourself wanting to skip step 5 because "the agent is obvious," the agent is not obvious. Write the eval.
