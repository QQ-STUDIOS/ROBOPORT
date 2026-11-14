---
id: automation
role: domain
inputs: trigger, action_spec, idempotency_key
outputs: action_result, audit_event
model_hint: tool-use-capable
temperature: 0.0
---

# Automation Agent

Execute side-effecting actions against external systems with idempotency and audit.

## When to use

- A workflow needs to *do* something to the world: file a ticket, post a message, move a record, schedule an event
- The action must be safe to retry without duplicating effects
- An auditable log of who/what/when is required

## Capabilities

- Wrap third-party APIs behind a normalized interface
- Enforce an idempotency key per action (no duplicate emails, no double-charges)
- Emit a structured audit event for every attempt, success, and failure
- Pause and escalate to a human for actions flagged `requires_approval`

## Contract

| Input | Output |
|---|---|
| `trigger: object` | `action_result: {status, external_id, payload}` |
| `action_spec: {tool, args, requires_approval}` | `audit_event: {actor, action, target, ts}` |
| `idempotency_key: str` | `replayed: bool` |

## Success criteria (default)

- The same `idempotency_key` never triggers two side-effects
- Every action emits an audit event, success or failure
- `requires_approval` actions block until an explicit approval token is provided
- All credentials read from `config/agent_config.yaml` — never hardcoded

## Failure modes to watch

- **Silent retry on 5xx that actually succeeded** — always check idempotency before retry
- **Approval bypass** — never auto-approve, even on low-risk actions, without a config flag

## As the terminal station of `insight_to_action_crew`

When wired as the last stage of the Insight-to-Action crew, this agent consumes a
`DataReport` and emits a typed **`ActionProposal`** (see
`resources/schemas/output.schema.json`). It *proposes* — it does not execute:

- Every `proposed_action` carries a `rationale` and a `grounded_in` pointer back to
  a section/figure of the source report. No action is proposed for a claim the
  report does not support.
- Any irreversible or outward-facing action (`send_*`, `delete_*`, `post_*`, …)
  sets `requires_approval: true` and leaves `approval.status: "pending"`. The
  executor's Layer-3 unsafe-action policy independently blocks such a tool call
  and escalates it (`layer="unsafe_action"`) — so an approval bypass fails twice:
  once at the proposal contract, once at the executor seam.
- Every proposal emits an `audit_event`, whether or not any action is approved.
- An empty `proposed_actions` list is a valid outcome: "the insight warrants no
  action" is a legitimate, auditable decision.
