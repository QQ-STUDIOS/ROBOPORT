# Pipeline: {{pipeline_name}}

**Owner:** {{owner}}
**Trigger:** {{trigger}}  (cron / webhook / manual / event)
**SLA:** {{sla}}

---

## Purpose

{{one_sentence_purpose}}

## Inputs

| Source | Shape | Refresh | Owner |
|---|---|---|---|
| {{src_1}} | `{{shape_1}}` | {{refresh_1}} | {{owner_1}} |
| {{src_2}} | `{{shape_2}}` | {{refresh_2}} | {{owner_2}} |

## Outputs

| Artifact | Schema | Consumers |
|---|---|---|
| `{{output_1}}` | `resources/schemas/{{schema_1}}` | {{consumer_1}} |
| `{{output_2}}` | `resources/schemas/{{schema_2}}` | {{consumer_2}} |

---

## Steps

```mermaid
flowchart LR
    A[{{step_1}}] --> B[{{step_2}}]
    B --> C[{{step_3}}]
    B --> D[{{step_4}}]
    C --> E[{{step_5}}]
    D --> E
```

| # | Step | Agent | Deterministic? | Inputs | Outputs |
|---|---|---|---|---|---|
| 1 | {{step_1}} | `{{agent_1}}` | {{det_1}} | {{in_1}} | {{out_1}} |
| 2 | {{step_2}} | `{{agent_2}}` | {{det_2}} | {{in_2}} | {{out_2}} |
| 3 | {{step_3}} | `{{agent_3}}` | {{det_3}} | {{in_3}} | {{out_3}} |

---

## Success criteria (run-level)

- {{criterion_1}}
- {{criterion_2}}
- {{criterion_3}}

## Failure policy

| Step fails | Action |
|---|---|
| {{step_a}} | {{action_a}} |
| {{step_b}} | {{action_b}} |

---

## Observability

- **Run log:** `runs/<run_id>/run.log` (JSONL)
- **Per-step transcripts:** `runs/<run_id>/<step_id>.transcript.md`
- **Aggregation:** `python scripts/aggregate.py --pipeline {{pipeline_name}}`

## Evals

Eval set: `evals/evals.json` filtered to `target == "{{pipeline_name}}"`.
Run with: `python scripts/benchmark.py --target {{pipeline_name}}`.

---

## Versioning

This pipeline lives at version `{{version}}`. Behavior changes go in a new version under `versions/`. Old runs replay against their original version, not HEAD.
