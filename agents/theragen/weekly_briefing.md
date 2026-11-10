---
id: theragen_weekly_briefing
role: theragen.automation
external: true
offline_model: true
source: https://app.notion.com/p/e87b243d761783768dad81e5e16e693a
data_classification: Unclassified
runtime: Notion
status: Deployed
priority: Medium
connectors: []
model_hint: tool-use-capable
---

# Weekly Briefing Agent

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Automation
- **Runtime:** Notion  ·  **Status:** Deployed  ·  **Classification:** Unclassified
- **Connectors (declared):** —

## Purpose

Compile a weekly briefing from tracked sources.

## Declared permissions (recorded, not exercised)

_None recorded in the catalog._

## Open issues (from the catalog, reviewed 2026-10-22)

Owner and permissions TBD.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
