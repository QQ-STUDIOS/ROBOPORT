## Summary
<!-- What changed and why? Keep the why out of the code, in here. -->

## Type of change
- [ ] `feat` — new feature
- [ ] `fix` — bug fix
- [ ] `chore` / `docs` — tooling, config, or docs only
- [ ] `refactor` — no behavior change
- [ ] **Breaking change** — callers / configs must update (explain below)

## Test plan
Check the boxes that apply; explain any you skipped.
- [ ] `python scripts/validate.py --all` — clean
- [ ] `python scripts/benchmark.py --target <agent> --dry-run` for touched agents — passing
- [ ] Live run with `--grade` if behavior could change for end users
- [ ] <!-- anything else: unit tests, manual repro, etc. -->

## Repo-specific checklist
<!-- Per CONTRIBUTING.md / docs/agent_design_principles.md -->
- [ ] If you added/changed an agent: at least one **blocker** eval in `evals/evals.json` covers it
- [ ] If you added/changed a typed boundary: JSON Schema added to `resources/schemas/`
- [ ] If you added a new env var: `.env.example` updated
- [ ] If you changed dependencies: `requirements.txt` updated; CI still installs cleanly
- [ ] No secrets, API keys, `.env` files, or model weights committed
- [ ] No "quiet 200" failure modes (empty arrays returned where the search broke, etc.)

## Linked issues
<!-- Closes #N -->
