---
id: theragen_support_ticket_triage
role: theragen.support
external: true
offline_model: true
source: https://app.notion.com/p/758b243d76178244910c01b559cafbe0
data_classification: Unclassified
runtime: Notion
status: Design
priority: High
connectors: []
model_hint: reasoning-strong
---

# Support Ticket Triage Agent

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Support
- **Runtime:** Notion  ·  **Status:** Design  ·  **Classification:** Unclassified
- **Connectors (declared):** —

## Purpose

Triage inbound support tickets and route them.

## Declared permissions (recorded, not exercised)

_None recorded in the catalog._

## Open issues (from the catalog, reviewed 2026-10-22)

Owner and permissions TBD.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
