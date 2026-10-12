// Launch Pad demo fixtures. Every record here is labelled as fixture data in the UI;
// nothing is fetched or mutated. Replace this module with gateway reads when one exists.

export const ENVIRONMENT = {
  name: "THERAGEN-STUDIO",
  reach: "Unreachable",
  observedAt: "2026-10-12 09:41:12 ET",
  observedShort: "09:41:12 ET",
  source: "Desktop Commander device inventory (fixture)",
  profile: [
    ["OS", "Windows Pro x64"],
    ["CPU", "Threadripper 9960X"],
    ["GPU", "RTX 5090 · 32 GB"],
    ["RAM", "128 GB"],
    ["Storage", "2× M.2 ≈ 4 TB total", "volume map unverified"],
    ["Runtime", "Python operator service · Ollama · REA static adapter"],
  ],
};

export const NAV = [
  ["launch", "Launch Pad", "◎"],
  ["missions", "Missions", "≡"],
  ["fleet", "Fleet", "⬡"],
  ["evidence", "Evidence", "▤"],
  ["approvals", "Approvals", "✓"],
  ["settings", "Settings", "⚙"],
];

export const DEFAULT_DRAFT = {
  objective: "Investigate JD run 41 regression against baseline run 40 (REA static inspection, proposal only — no environment mutation).",
  root: "C:\\roboport\\projects\\jd-crew",
  crew: "JD-Crew v0.9.3",
  dataClass: "Internal",
  calls: 120,
  minutes: 30,
  context: 32768,
  criteria: "All typed step outputs validate against their contracts; blocker suite passes; the proposal cites exact baseline/candidate artifacts and hashes.",
};

export const READINESS = [
  { label: "Connection", state: "Unreachable", icon: "⚠", tone: "amber", detail: "No configured VPN/TCP route. Device not listed in Desktop Commander.", fresh: "observed 09:41:12 ET · stale" },
  { label: "Compliance", state: "No evidence", icon: "?", tone: "steel", detail: "Intune · BitLocker · Defender · standard-user: links to evidence required, no checks received.", fresh: "never observed" },
  { label: "GPU · model", state: "Unknown", icon: "?", tone: "steel", detail: "RTX 5090 placement, driver and qwen3:14b residency unverified on target.", fresh: "never observed" },
  { label: "Services", state: "Unknown", icon: "?", tone: "steel", detail: "Operator service · bridge · Ollama. Restart must reconcile owned processes first.", fresh: "never observed" },
  { label: "Permissions", state: "Not enrolled", icon: "⊘", tone: "red", detail: "No local operator enrollment or authenticated session. Policy applies to local HTTP callers too.", fresh: "checked now · gateway absent" },
];

export const BASE_BLOCKERS = [
  "THERAGEN-STUDIO is unreachable — no supported connection route (LAN relay / operator session)",
  "Operator session not enrolled — commands require an authenticated local session",
  "Launch profile “bounded local static-inspection / proposal” not enabled — approval evidence missing",
  "Project root is not within an approved root (none configured)",
];

export const MANIFEST_COMPONENTS = [
  "AGT-local-job_scout@0.9.3",
  "AGT-local-technical_analyst@0.9.3",
  "AGT-local-ranker@0.9.3",
  "AGT-local-compliance_review@0.9.3",
  "PRM-local-jd-system@3",
  "SKL-local-static-inspect@0.2.0",
];

export const LAUNCH_INCIDENTS = [
  { icon: "✕", tone: "red", title: "Grading failed · M-0004 · compliance_review", note: "schema drift: missing field risk_tier · run 41 vs 40 · diff_runs.py" },
  { icon: "◐", tone: "amber", title: "Awaiting reviewer · APR-0002 · REA proposal M-0005", note: "reviewer roster unconfigured · independent review required" },
  { icon: "?", tone: "steel", title: "State unknown · M-0001", note: "owned processes not reconciled after service restart · duplicate dispatch blocked" },
  { icon: "▤", tone: "green", title: "7 artifacts imported · run.log fixture", note: "hashes recorded · provenance validated · ", link: "evidence" },
];

// [tone, icon, label]
export const MISSION_STATES = {
  draft: ["steel", "◌", "draft"],
  waiting: ["amber", "◐", "waiting review"],
  queued: ["blue", "●", "queued"],
  running: ["cyan", "▶", "running"],
  cancelling: ["amber", "⏹", "cancelling"],
  failed: ["red", "✕", "failed"],
  completed: ["green", "✓", "completed"],
  rejected: ["red", "⊘", "rejected"],
  unknown: ["steel", "?", "unknown"],
};

export const MISSIONS = [
  { id: "M-0006", title: "JD run 41 regression investigation (draft)", state: "draft", approval: "not requested", runtime: "—", updated: "now", owner: "unassigned", baseline: "run 40 (completed · M-0003)", inputHash: "— (assigned at dispatch)", configHash: "— (assigned at dispatch)", reviewer: "operator checkpoint · roster unconfigured", source: "this console · local draft store",
    timeline: [{ t: "2026-10-12 09:44 ET", text: "Draft created (offline)" }] },
  { id: "M-0005", title: "REA proposal review — compliance_review contract fix", state: "waiting", approval: "waiting · APR-0002", runtime: "not started", updated: "09:31 ET", owner: "unassigned", baseline: "M-0004 evidence", inputHash: "b61e…9f04 (fixture)", configHash: "42aa…c7d1 (fixture)", reviewer: "independent process-owner review", source: "REA static adapter · typed proposal",
    timeline: [{ t: "2026-10-12 09:29 ET", text: "Typed proposal emitted · 1 patch · expected effect recorded" }, { t: "2026-10-12 09:31 ET", text: "Approval APR-0002 opened · reviewer unconfigured" }] },
  { id: "M-0004", title: "JD run 41 — REA static inspection", state: "completed", approval: "n/a · replay import", runtime: "failed grading (compliance_review)", updated: "09:12 ET", owner: "unassigned", baseline: "run 40", inputHash: "3f9c…a71e (fixture)", configHash: "8d02…13b7 (fixture)", reviewer: "none (import)", source: "run.log fixture import",
    timeline: [{ t: "2026-10-12 08:52 ET", text: "run.start · plan.json recorded" }, { t: "2026-10-12 08:58 ET", text: "step.complete job_scout · list[Job] valid" }, { t: "2026-10-12 09:03 ET", text: "step.complete technical_analyst · valid" }, { t: "2026-10-12 09:06 ET", text: "step.complete compliance_review · grading failed · schema drift" }, { t: "2026-10-12 09:06 ET", text: "run.complete · 1 blocker failed" }] },
  { id: "M-0003", title: "JD run 40 — baseline", state: "completed", approval: "n/a · replay import", runtime: "success · all blockers pass", updated: "yesterday", owner: "unassigned", baseline: "—", inputHash: "c0de…55e2 (fixture)", configHash: "8d02…13b7 (fixture)", reviewer: "none (import)", source: "run.log fixture import",
    timeline: [{ t: "2026-10-11 16:20 ET", text: "run.start" }, { t: "2026-10-11 16:34 ET", text: "run.complete · 4/4 blockers pass" }] },
  { id: "M-0002", title: "Connector drilldown pilot (HIPAA zone)", state: "rejected", approval: "rejected · data-egress approval absent", runtime: "not dispatched", updated: "2026-10-09", owner: "unassigned", baseline: "—", inputHash: "—", configHash: "—", reviewer: "independent process-owner review", source: "this console",
    timeline: [{ t: "2026-10-09 11:02 ET", text: "Rejected: connector label is not approval evidence" }] },
  { id: "M-0001", title: "Service restart reconciliation", state: "unknown", approval: "accepted (expired)", runtime: "unknown", updated: "2026-10-08", owner: "unassigned", baseline: "—", inputHash: "9a1f…0c33 (fixture)", configHash: "7e7e…b0a9 (fixture)", reviewer: "operator checkpoint", source: "gateway restart log",
    timeline: [{ t: "2026-10-08 07:40 ET", text: "Gateway restarted · owned processes not reconciled" }, { t: "2026-10-08 07:40 ET", text: "State set to unknown · duplicate dispatch blocked" }] },
];

// [tone, icon, label]
export const AGENT_STATES = {
  docked: ["blue", "●", "docked"],
  dispatched: ["cyan", "→", "dispatched"],
  working: ["amber", "⚙", "working"],
  returning: ["green", "↩", "returning"],
};

export const FLEET = [
  { id: "executor-01", state: "working", station: "technical_analyst · step 2/8", task: "T-0412", pct: 62, ctx: "41,200 / 60,000", rel: "consumes list[Job] from job_scout · emits list[JobAnalysis] → ranker" },
  { id: "executor-02", state: "working", station: "job_scout · step 1/8", task: "T-0415", pct: 18, ctx: "12,804 / 60,000", rel: "entry step · tools: job_board_api, load_profile" },
  { id: "executor-03", state: "dispatched", station: "ranker · step 3/8", task: "T-0409", pct: 0, ctx: "53,900 / 60,000", rel: "awaits list[JobAnalysis] · context pressure 90%" },
  { id: "executor-04", state: "returning", station: "— (carrying result)", task: "T-0402", pct: 100, ctx: "8,310 / 60,000", rel: "returning compliance_review result → hub" },
  { id: "executor-05", state: "docked", station: "—", task: "—", pct: 0, ctx: "0 / 60,000", rel: "idle · specialty packager" },
];

export const ARTIFACTS = [
  { name: "run.log", type: "event log · JSONL", size: "412 KB", hash: "sha256 3f9c…a71e" },
  { name: "plan.json", type: "mission plan", size: "6 KB", hash: "sha256 8d02…13b7" },
  { name: "provenance.json", type: "provenance manifest", size: "3 KB", hash: "sha256 11be…e0f2" },
  { name: "outputs/job_scout.json", type: "typed · list[Job]", size: "48 KB", hash: "sha256 90aa…7c1d" },
  { name: "outputs/technical_analyst.json", type: "typed · list[JobAnalysis]", size: "131 KB", hash: "sha256 5c5c…2e8a" },
  { name: "criteria.json", type: "success criteria", size: "2 KB", hash: "sha256 e7d1…4409" },
  { name: "blockers.json", type: "blocker results", size: "4 KB", hash: "sha256 2f61…b8c0" },
];

export const STEPS = [
  { step: "job_scout", contract: "→ list[Job]", status: "valid · 12 items", tone: "green", icon: "✓" },
  { step: "technical_analyst", contract: "list[Job] → list[JobAnalysis]", status: "valid", tone: "green", icon: "✓" },
  { step: "ranker", contract: "list[JobAnalysis] → RankedList", status: "valid", tone: "green", icon: "✓" },
  { step: "compliance_review", contract: "RankedList → ComplianceReport", status: "failed grading", tone: "red", icon: "✕" },
  { step: "packager", contract: "ComplianceReport → Bundle", status: "skipped · upstream failure", tone: "steel", icon: "◌" },
];

export const BLOCKER_RESULTS = [
  { name: "validate.py --all", html: '<span class="t-green">✓ 3 pass</span> · <span class="t-red">✕ 1 fail</span>' },
  { name: "diff_runs.py 41 ↔ 40", html: '<span class="t-amber">◐ regression at compliance_review</span>' },
  { name: "fault-injection evals", html: '<span class="t-steel">? not run · capability proposed</span>' },
];

export const LEDGER = [
  { k: "LLM calls", v: "42", note: "measured · run.log", tone: "bright" },
  { k: "Prompt tokens", v: "118,420", note: "measured", tone: "bright" },
  { k: "Completion tokens", v: "24,118", note: "measured", tone: "bright" },
  { k: "Context limit", v: "32,768", note: "completion limit 2,048", tone: "bright" },
  { k: "API spend", v: "$0.00", note: "local route · not total cost", tone: "green" },
  { k: "Electricity · host", v: "unknown", note: "not measured", tone: "steel" },
  { k: "GPU placement", v: "unknown", note: "target not observed", tone: "steel" },
  { k: "Elapsed", v: "14m 08s", note: "run.start → run.complete", tone: "bright" },
];

export const APPROVALS = [
  { id: "APR-0001", title: "Enable launch profile", trigger: "first launch requires an explicitly enabled profile", reviewer: "unconfigured · process owner not identified", versions: "profile bounded-static-inspection v1", evidence: "policy configuration · none attached",
    why: "No reviewer identity is configured; decisions require an authenticated reviewer and cannot be invented." },
  { id: "APR-0002", title: "Mission M-0005 · REA proposal", trigger: "typed proposal with 1 patch · expected effect recorded", reviewer: "independent process-owner review required", versions: "AGT-local-compliance_review 0.9.3 → 0.9.4 (proposed)", evidence: "M-0004 artifacts · 7 hashes · diff_runs verdict",
    why: "Independent review profile — requester self-approval is rejected; reviewer roster unconfigured." },
];

export const APPROVAL_EMPTIES = [
  { k: "Decisions", v: "No decisions recorded", note: "Each decision stores identity, manifest/evidence hashes, rationale, timestamp and expiry." },
  { k: "Exceptions", v: "None", note: "Exception records carry an expiry and the accountable governance owner." },
  { k: "Escalations", v: "None", note: "Escalation is a separate state with its own trigger and reviewer." },
  { k: "Revocations", v: "None", note: "Revoking an approval blocks further dispatch under it immediately." },
];

// [tone, icon]
export const CAPABILITY_STATES = {
  implemented: ["green", "✓"],
  verified: ["cyan", "◆"],
  proposed: ["blue", "◇"],
  "not configured": ["amber", "◌"],
  unavailable: ["steel", "⊘"],
  "observation only": ["steel", "◔"],
};

const C = (name, state, detail, blocker, action, disabled, why) => ({ name, state, detail, blocker: blocker || "", action, disabled: !!disabled, why: why || "" });

export const SETTINGS_GROUPS = [
  { title: "Environments · connection providers", items: [
    C("THERAGEN-STUDIO", "proposed", "Expected profile recorded (Windows Pro · 9960X · RTX 5090 32 GB · 128 GB · 2× M.2 ≈ 4 TB).", "Not enrolled — no connection route; power state unknown.", "Enroll environment", true, "Requires a supported connection route"),
    C("LAN relay / wake provider", "not configured", "Wake and remote connection need a configured relay with capability discovery and a connection acceptance test.", "No relay configured. Wake is not an Entra action.", "Configure relay", true, "Later slice LP-19"),
    C("Entra · Intune Remote Desktop policy", "observation only", "Prior policy success observed; it is not an active connection or remote-wake capability.", "", "View observation", false, ""),
  ] },
  { title: "Identity · roles · modes", items: [
    C("Local operator enrollment", "implemented", "Gateway owns authenticated sessions, origin/CSRF checks, rate limits and authorization — applies to local HTTP callers.", "No operator enrolled on this console.", "Enroll operator", true, "Gateway not running (demo)"),
    C("Reviewer roster", "not configured", "Reviewer identities, independent process-owner assignment and approval-record authority.", "Roster owner not supplied — approvals stay waiting.", "Assign reviewers", true, "Business input missing"),
    C("Operating modes", "implemented", "Demo (fixtures) · Replay (imported run) · Live (gateway). Permanently labelled; demo never appears as live.", "Live unavailable: no gateway. Replay unavailable: no imported run in session.", "Switch mode", true, "Only Demo is available"),
  ] },
  { title: "Registry · catalogues · model routes", items: [
    C("Enterprise register (workbook)", "not configured", "Stated system of record: permanent AGT/SKL/PRM/INS/MCP IDs, owners, approvals, review and pilot dates.", "Accountable owner and canonical workbook not identified — no synchronization.", "Preview import", true, "Owner not identified"),
    C("Catalogue CSV import", "proposed", "4 agents · 5 skills · 5 prompts · 3 instructions · 6 connectors captured 2026-09-26.", "Ownership, approval, permission, version and ID fields absent; unreconciled to runtime.", "Review mappings", false, ""),
    C("Local runtime registry", "implemented", "21 local agent entries incl. investigation/proposal pair.", "Local IDs not reconciled to enterprise register IDs.", "Open registry", false, ""),
    C("Ollama · qwen3:14b", "implemented", "Local model route with tool routing; REA static adapter.", "Placement on target unverified.", "Verify on target", true, "Environment unreachable"),
    C("OpenAI provider / sessions", "not configured", "Optional adapter — data-egress approval and priced budgets are route gates.", "Not authorized in this slice (LP-20).", "Configure provider", true, "Separately authorized capability"),
  ] },
  { title: "Data · retention · credentials", items: [
    C("Classification · retention", "not configured", "Classification governs access, safe exports, logs and retention.", "No policy — safe export disabled.", "Define policy", true, "Policy authority not identified"),
    C("Credentials", "implemented", "Referenced by name only; never bundled or exposed through a URL.", "None stored.", "Add reference", true, "Gateway not running (demo)"),
    C("Tenant corpus (8 TB)", "not configured", "Retrieval/training require owner, classification, approved purpose/population/date range, exclusions, retention, evaluations and disposal state.", "Approval evidence missing. Hardware capacity is not approval to ingest.", "Request approval record", true, "Later slices LP-21/22"),
  ] },
];
