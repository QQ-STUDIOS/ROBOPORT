# Theragen agent catalog (offline models)

ROBOPORT mirrors the real Theragen AI agents — catalogued in the Notion
**🧠 1) Agents** database — as **offline, typed-contract records** so the framework
can reason about them: govern them, diff their runs, and wire them into *modeled*
crews. These are records, not runnable agents.

> **Safety contract.** ROBOPORT never connects to, authenticates against, or invokes
> any real Theragen system (Brightree, Snowflake, Microsoft 365 / Graph, Azure, the
> studio Ollama host). No production data, credential, or PHI flows through this
> repository. Every artifact here is an offline model; anything PHI- or
> Confidential-classified below is a *label carried from the catalog*, not data held
> here. The real agents and their owners live in Notion (linked per row).

These entries live under the registry's `theragen_agents` key — deliberately separate
from ROBOPORT's own runnable `agents`, so an external production agent is never
confused for something this repo can execute. Imported from the catalog reviewed
**2026-10-22**; all rows were **Proposed** for ROBOPORT governance at import.

## The agents

| Agent | Type | Runtime | Status | Classification | Priority | Purpose |
|---|---|---|---|---|---|---|
| [STUDIO.BRIGHTREE_AI PDF extraction](agents/theragen/brightree_pdf_extraction.md) | Automation | Snowflake | Deployed | PHI | — | Extracts fields from Brightree PDFs into Snowflake; feeds the Rounds report. |
| [LegacyRAG retrieval](agents/theragen/legacyrag_retrieval.md) | Research | Local | Paused | PHI | — | Search and Q&A over the legacy document catalog. |
| [Tenant Scan (Graph app)](agents/theragen/tenant_scan.md) | Analysis | Azure | Deployed | Confidential | — | Read-only inventory of Microsoft 365 sites for legacy site disposition. |
| [Claude Code on THERAGEN-STUDIO](agents/theragen/claude_code_studio.md) | Coding | Claude | Deployed | Confidential | — | Coding, analysis and evaluation work on the studio machine. |
| [Roboport (agent framework)](agents/theragen/roboport_framework.md) | Automation | Local | Testing | Confidential | — | Local multi-agent framework running Theragen crews on THERAGEN-STUDIO. |
| [EOM Catalogue Steward Agent](agents/theragen/eom_catalogue_steward.md) | Automation | Notion | Deployed | Unclassified | High | Maintain the EOM catalogue — keep entries current and consistent. |
| [EOM Decision & Backlog Agent](agents/theragen/eom_decision_backlog.md) | Automation | Notion | Deployed | Unclassified | Medium | Track EOM decisions and manage the backlog. |
| [EOM Governance Auditor Agent](agents/theragen/eom_governance_auditor.md) | Analysis | Notion | Deployed | Unclassified | High | Audit EOM artifacts against governance rules. |
| [EOM Traceability & Impact Agent](agents/theragen/eom_traceability_impact.md) | Research | Notion | Deployed | Unclassified | Medium | Trace EOM items to their impact and dependencies. |
| [EOM AI Readiness Agent](agents/theragen/eom_ai_readiness.md) | Analysis | Notion | Deployed | Unclassified | Medium | Assess EOM items for AI-readiness. |
| [Weekly Briefing Agent](agents/theragen/weekly_briefing.md) | Automation | Notion | Deployed | Unclassified | Medium | Compile a weekly briefing from tracked sources. |
| [Support Ticket Triage Agent](agents/theragen/support_ticket_triage.md) | Support | Notion | Design | Unclassified | High | Triage inbound support tickets and route them. |
| [Code Generation Agent](agents/theragen/code_generation.md) | Coding | Notion | Testing | Unclassified | Medium | Generate code from structured specifications. |
| [Research Summarizer Agent](agents/theragen/research_summarizer.md) | Research | Notion | Testing | Unclassified | High | Summarize research sources into briefs. |

## How these are used

- **Governance & diffing.** Each record is a registry citizen, so the governance gate
  and `diff_runs` can treat a modeled Theragen crew the same way they treat JD-Crew or
  DataOps.
- **Modeled crews (next).** A subset of these agents is wired into offline, governance-
  gated crews with ROBOPORT typed contracts — e.g. a Brightree→Rounds reporting flow, a
  Tenant-Scan inventory flow, LegacyRAG retrieval, and the EOM governance loop. Their
  side-effects are represented through the Phase-3 unsafe-action escalation, never
  performed.
- **Provenance.** Every record carries its Notion `source` URL and the catalog fields
  (classification, runtime, status, open issues) verbatim, so the model stays honest to
  the system of record.

## Open issues worth flagging

The catalog records real risks on several agents (recorded in each spec, not acted on
here): the Brightree extractor's undocumented runtime model; LegacyRAG's unverified
restore and a 1.18M-vs-335k catalogue/document gap; Tenant-Scan's client-secret
rotation deadline (2027-08-19) and possible PHI in site names; and Claude Code on
THERAGEN-STUDIO running Snowflake as `ACCOUNTADMIN` rather than a least-privilege
read-only role. These belong to the real agents' owners; ROBOPORT surfaces them.
