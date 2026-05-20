# Security Policy

## Supported versions

ROBOPORT is pre-1.0. Only `main` is supported — fixes are not backported to older tags.

## Reporting a vulnerability

**Do not open a public issue or PR for security vulnerabilities.**

Report privately via one of:

- **GitHub Security Advisory** (preferred): https://github.com/RustyRich020/ROBOPORT/security/advisories/new
- **Email**: security contact in the repo profile, or open a minimal public issue asking for a private channel without disclosing the vulnerability

Please include:

1. A description of the issue and its potential impact.
2. Reproduction steps or a proof-of-concept (the simpler, the better).
3. The commit SHA (`main` tip is fine) and any relevant config.
4. Your suggested fix or mitigation, if you have one.

## Response

- Acknowledgement within **3 business days**.
- Initial assessment + tentative remediation timeline within **7 business days**.
- Coordinated disclosure: we agree on a disclosure date together, typically 30–90 days from the initial report depending on severity and complexity. Critical issues are fast-tracked.

## Scope

**In scope:**

- Code execution, sandbox escape, or unsafe deserialization in `scripts/`
- Schema / validator bypasses that allow type-confused or malicious agent output to flow through `validate.py` or `roboport_runtime/`
- Credential or secret exposure (env var leaks in logs, run artifacts, transcripts)
- Server-side request forgery, command injection, or path traversal in tool integrations (job_scout, aggregator, providers)
- Supply-chain concerns in dependencies pinned in `requirements.txt`
- Auth/authz issues in any deployment helpers (`deploy.sh`, etc.)

**Out of scope:**

- Vulnerabilities requiring physical access to the host running ROBOPORT
- Vulnerabilities in upstream model providers (Anthropic API, Ollama) — report those to the respective vendors
- Prompt-injection issues against agent prompts that don't escalate beyond the agent's existing capabilities (please file as a regular issue with the `prompt-injection` label instead)
- Issues in third-party job-board APIs we proxy

## Hardening expectations for contributors

These live in `CONTRIBUTING.md` but are repeated here because they're security-relevant:

- **Never commit secrets.** `.env` and `*.key` / `*.pem` are gitignored — don't bypass it.
- **Add a redacted `.env.example`** for every new env var.
- **Don't quiet failures.** A schema-invalid response should propagate, not be silently coerced.
- **Validate at boundaries.** Every typed handoff goes through `jsonschema.validate` (see `resources/prompts/error_handling.md`).
- **Prefer deterministic code over LLM calls** for anything safety- or correctness-critical (see `docs/agent_design_principles.md`).

## If a secret leaks

1. **Rotate the credential first.** Treat it as compromised regardless of who saw it.
2. Scrub history with `git filter-repo` or BFG; force-push with team coordination.
3. Audit logs / run artifacts (`runs/*`) for any other places the secret may have been written.
4. Open a private advisory describing what leaked and what was rotated.
