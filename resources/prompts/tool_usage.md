# Tool Usage Patterns

How ROBOPORT agents reach for, combine, and recover from tools. Borrowed-and-deconstructed from LangChain tool-calling conventions, with ROBOPORT-specific guardrails.

---

## Tool Selection Order

When multiple tools could answer a question, prefer in this order:

1. **Internal / domain-specific tool** (HRIS, internal search, the job DB) — closest to ground truth
2. **Deterministic API** (a documented vendor API with stable schema)
3. **Web search + fetch** — broad but noisy
4. **LLM-only inference** — last resort, must be tagged `inferred:`

The Executor logs which tier was used per call. The Analyzer reads this log to spot tier-creep (cheap tools being skipped for expensive ones).

---

## Tool Whitelisting per Step

Every step in a plan declares its `tools_allowed`. The Executor refuses to call any tool not on the list. New tool needs go back to the Planner to update the plan — they do not get added at runtime.

```yaml
step:
  id: s2
  owner: technical_analyst
  tools_allowed: [job_db_get, web_fetch, llm_call]
  tools_forbidden: [send_email, create_calendar_event]   # explicit deny-list for side-effects
```

Forbid side-effecting tools by default in non-Automation agents.

---

## Argument Validation

Validate tool arguments **before** calling. Cheaper to fail fast than to round-trip a malformed payload through a vendor API.

```python
def call_tool(name, args):
    spec = TOOL_REGISTRY[name].args_schema
    jsonschema.validate(args, spec)   # raises before network call
    return TOOL_REGISTRY[name].fn(**args)
```

If validation fails, the Executor returns a `semantic_error` — don't retry, don't reason around it, fail the step.

---

## Result Handling

Every tool call result goes through this filter:

1. **Schema check** — does the response shape match what the tool advertised?
2. **Sanity check** — are the values in plausible ranges? (e.g., a job posted in 1972 is suspicious)
3. **Provenance stamp** — attach `{tool, args_hash, ts}` to every record before passing it forward

Records without a provenance stamp are not allowed to flow downstream. This is how the Critic catches hallucinated tool results.

---

## Combining Tool Results

When two tools answer the same question:

| Pattern | When to use |
|---|---|
| **Prefer-and-cite** | One source is authoritative; cite the other for corroboration |
| **Triangulate** | Three independent sources; flag if they disagree by >X% |
| **Vote** | Many noisy sources (e.g., scrapers); pick the mode |
| **Concatenate-and-dedupe** | Sources are complementary, not competing (multiple job boards) |

The Salary Estimator uses **triangulate**. The Job Scout uses **concatenate-and-dedupe**. Pick the right pattern at design time and document it in the agent.

---

## Long-Running Tools

If a tool's expected runtime exceeds the step budget:

1. Return a `pending` marker with a `poll_token`
2. The Orchestrator schedules a poll step instead of blocking
3. The poll step runs in a later wave; it can be retried independently

Never block a synchronous chain on an async tool. That's the #1 source of mysterious timeouts.

---

## Tool Failure Taxonomy

| Class | Example | Retry? | Action |
|---|---|---|---|
| `transient` | timeout, 502, 429 | yes (×2, backoff) | log and retry |
| `auth`      | 401, 403, expired token | no | surface to user/operator immediately |
| `quota`     | hit a rate cap or daily limit | no | abort run; resume after window |
| `semantic`  | 400, schema mismatch | no | fail step, return `plan_invalid` to Orchestrator |
| `degraded`  | 200 OK but missing fields | conditional | retry once, then fail |

The Orchestrator's failure-policy table (`agents/core/orchestrator.md`) keys off this taxonomy.
