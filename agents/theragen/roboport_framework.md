---
id: theragen_roboport_framework
role: theragen.automation
external: true
offline_model: true
source: https://app.notion.com/p/402b243d761781dfb2d7efe546c03898
data_classification: Confidential
runtime: Local
status: Testing
connectors: ["Ollama"]
model_hint: tool-use-capable
---

# Roboport (agent framework)

> **Offline model — not a runnable ROBOPORT agent.** This spec is a typed-contract
> *record* of a real Theragen agent, imported from the Notion agent catalog so
> ROBOPORT can reason about it (govern, diff, wire into modeled crews). ROBOPORT
> does **not** connect to, authenticate against, or invoke the real system. No
> production data, credential, or PHI flows through this repository.

- **Type:** Automation
- **Runtime:** Local  ·  **Status:** Testing  ·  **Classification:** Confidential
- **Connectors (declared):** Ollama

## Purpose

Local multi-agent framework running Theragen crews on THERAGEN-STUDIO.

## Declared permissions (recorded, not exercised)

qwen3:14b via local Ollama; operator surfaces on 127.0.0.1:4242 and :8000 (localhost only).

## Open issues (from the catalog, reviewed 2026-10-22)

Owner TBD. Reboot-recovery check pending; script signing. Model change (qwen3:14b to qwen3.6/qwen3.8) goes through Roboport's own trial and rollback process; 2026-10-22 benchmark report is the evidence.

## ROBOPORT modeling note

When this agent is modeled as a crew station (see `docs/THERAGEN_AGENTS.md` and
the Theragen crews), its contract is represented with ROBOPORT's typed outputs and
its side-effects are gated by the Phase-3 unsafe-action policy and the Phase-5
governance rules — exercised entirely offline.
