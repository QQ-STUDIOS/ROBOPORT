# Error Handling Patterns

How ROBOPORT agents surface, route, and recover from failure. The principle: **fail loudly, recover where it's safe, surface where it isn't**.

---

## The Three-Layer Error Stack

```
┌──────────────────────────────────────────────┐
│  USER-FACING       (Orchestrator)            │  what the user sees
├──────────────────────────────────────────────┤
│  STEP-LEVEL        (Executor + Critic)       │  retry, repair, or fail step
├──────────────────────────────────────────────┤
│  CALL-LEVEL        (tool / LLM call)         │  raw failures
└──────────────────────────────────────────────┘
```

Each layer has its own retry/recovery policy. Layers do not skip — a call-level failure becomes a step-level decision becomes (sometimes) a user-facing surface.

---

## Typed Failure Objects

All failures carry a typed payload:

```json
{
  "type": "transient | semantic | criterion_failed | budget_exceeded | unsafe | plan_invalid",
  "message": "<human-readable>",
  "retryable": true,
  "context": {
    "step_id": "...",
    "tool": "...",
    "args_hash": "...",
    "attempt": 2
  }
}
```

`type` is what the Orchestrator routes on. `retryable` is a hint, not a contract — the Orchestrator's policy table is authoritative.

---

## Recovery Patterns

### Pattern A — Retry with backoff
For `transient` failures only. Cap at 2 retries per step. Exponential backoff (1s, 4s).

### Pattern B — Critic-feedback loop
For `criterion_failed` where the output is non-empty. Run Critic; if Critic returns `fix`, re-run the Executor with the Critic's `suggested_repair` injected as a hint. **Maximum one loop.**

### Pattern C — Re-plan
For `plan_invalid`. Return to the Planner with the failure attached. The Planner may revise the step or restructure the plan.

### Pattern D — Graceful degrade
For optional steps (e.g., `salary_estimator` in the JD-Crew). Failure is logged, the step is marked `skipped`, and the FinalReport surfaces the absence in `warnings[]`.

### Pattern E — Hard abort
For `unsafe`, `budget_exceeded`, or repeated failures. Stop the run, write the final state, surface to the user. **Never silently truncate.**

---

## What Counts as "Loud Enough"

A failure is loud enough when:

1. It appears in the run log with its typed payload
2. Its step's `status` is `failed` (not `ok`, not `partial`)
3. The downstream `FinalReport.warnings[]` mentions it if the step was optional
4. The user sees it if any blocker step failed

A failure is **not** loud enough when:

- The step returns `status: ok` with an empty payload
- An exception was caught and logged but the run claims success
- The output schema validates because a default value silently filled the gap

The last one is the most dangerous. Default values that hide failures are bugs disguised as features.

---

## Error Messages: Three Audiences

Every typed failure produces three views:

| View | Audience | Style |
|---|---|---|
| `message` | end user | plain language; what happened, what to try |
| `developer_note` | engineer reading the log | exception class, stack hint, args |
| `repair_hint` | the Critic / next attempt | what would change to make this succeed |

Don't use the same string for all three. The user doesn't want a stack trace; the engineer doesn't want "something went wrong."

---

## The "Quiet 200" Anti-Pattern

The single most common failure mode: a tool returns `200 OK` with a payload that's *technically* valid but missing the important field. Examples:

- Job board API returns `[]` because the search query was malformed (not because there are no jobs)
- LLM returns valid JSON with all fields filled in, but every field is the placeholder example from the prompt
- A scraper returns the page title as the job description

**Defense:** add a `degraded` check after every tool call: spot-check that the values are non-empty, in plausible ranges, and not equal to known placeholders. The Critic does this systematically; the Executor does a fast version of it inline.
