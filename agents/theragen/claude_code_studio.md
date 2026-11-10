---
id: theragen_claude_code_studio
role: theragen.coding
external: true
offline_model: true
source: https://app.notion.com/p/402b243d761781beb1aecb30be28e548
data_classification: Confidential
runtime: Claude
status: Deployed
connectors: ["Claude", "Snowflake", "Notion", "Microsoft 365"]
model_hint: tool-use-capable
---

# Claude Code on THERAGEN-STUDIO

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Coding
- **Runtime:** Claude  ·  **Status:** Deployed  ·  **Classification:** Confidential
- **Connectors (declared):** Claude, Snowflake, Notion, Microsoft 365

## Purpose

Coding, analysis and evaluation work on the studio machine.

## Declared permissions (recorded, not exercised)

Snowflake (ACCOUNTADMIN connection), Notion, Microsoft 365 and other connectors.

## Open issues (from the catalog, reviewed 2026-10-22)

Owner TBD. Snowflake role is not least-privilege (ACCOUNTADMIN); move to a dedicated read-only role. Anthropic BAA reported signed; confirm it covers this product and connectors before PHI use.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
