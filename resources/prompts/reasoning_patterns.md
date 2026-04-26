# Reasoning Patterns

Reusable structures every ROBOPORT agent can draw from. Pull a named pattern by reference rather than re-inventing it inside an agent definition.

---

## 1. Structured Chain-of-Thought (not verbose)

Force reasoning into named buckets, not freeform monologue. Verbose CoT drifts; structured CoT stays on contract.

**Template:**
```
GOAL: <one sentence>
KNOWN: <bulleted facts from inputs only — no inference>
INFER: <bulleted inferences, each tagged with the KNOWN items it depends on>
RISK: <what would break this>
PLAN: <ordered steps to produce the output>
```

The model is allowed only to write inside these buckets. Anything outside the buckets is excluded from the output.

**When to use:** any reasoning step where intermediate work matters but you don't want a wall of text.

---

## 2. Tool-First Reasoning

Default behavior: if a tool can produce the answer, use the tool — don't reason your way to a number you could look up.

**Heuristic:**
1. Read the question. Ask: *is there a tool that returns this directly?*
2. If yes — call the tool, then reason only about how to format/use the result.
3. If no — reason, but emit a ticket: "no tool covers this; consider adding one."

**When to use:** every Executor invocation. This is the default, not an option.

---

## 3. Fallback Strategies (typed)

Every step needs a "what if this fails" answer at design time, not run time.

**Template:**
```yaml
primary:    <preferred path>
fallback_1: <if primary returns empty>
fallback_2: <if primary returns malformed>
fallback_3: <if primary times out>
abort_when: <conditions under which we surface to the user, not retry>
```

Fallbacks are *typed* — each one declares the same output schema as the primary, so the next step doesn't care which path produced the answer.

---

## 4. Retry Logic

Three categories, three policies:

| Failure | Retry? | How? |
|---|---|---|
| Transient (timeout, 429, 5xx) | Yes | Exponential backoff, max 2 |
| Semantic (4xx, validation error) | No | Return failure to Orchestrator |
| Criterion failure (output produced, criteria failed) | At most once | With Critic feedback injected as hint |

**Anti-pattern:** retrying semantic failures. If the API said "bad request," retrying with the same payload gets the same answer.

---

## 5. Self-Verification Before Return

Every Executor step ends with this check:

```
For each criterion in step.success_criteria:
    Quote the part of the output that satisfies it.
    If you can't quote it, the criterion failed.
```

Forcing a quote prevents "yes, looks good" hand-waving. Either the evidence is in the output, or the step failed.

---

## 6. Refusal-Reframe-Resolve

When the model wants to refuse, force one of:

- **Refuse** — clearly, with the reason and the policy citation
- **Reframe** — propose a related task that *is* doable, ask if the user wants that instead
- **Resolve** — do the task; the refusal urge was overcautious

This stops both over-refusal (everything becomes "as an AI...") and under-refusal (silent compliance with bad asks). Force a category, then act.

---

## 7. Two-Hat Reasoning (advocate + skeptic)

Used in the Application Strategist. Two passes, two hats:

```
HAT 1 — ADVOCATE: list every reason this is a good fit / right answer
HAT 2 — SKEPTIC:  list every reason this is wrong / a bad fit
SYNTHESIS: which side wins, and on what evidence?
```

Surfaces both `reasoning_for` and `reasoning_against` so the verdict is auditable.

---

## 8. Citation Discipline

Any factual claim in an output must point at one of:

- A tool result (with the tool call id)
- An input field (with the field path)
- A document (with file + line)
- An LLM inference clearly tagged `inferred:` and explained

If a claim doesn't fit any of these, it's a hallucination dressed up as a fact. Drop it.
