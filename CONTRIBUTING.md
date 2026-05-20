# Contributing to ROBOPORT

## Branch model

- `main` — protected. Always deployable. Only updated via reviewed PR.
- `feat/<short-name>` — new feature work.
- `fix/<short-name>` — bug fixes.
- `chore/<short-name>` — tooling, config, deps, repo hygiene.
- `docs/<short-name>` — documentation-only changes.
- `claude/<task>` / `codex/<task>` — branches authored by automated assistants. Treat the same as a human PR.

**Never** push directly to `main`. **Never** force-push a shared branch.

## Stale branches

GitHub auto-deletes head branches on merge (Settings → General → "Automatically delete head branches" is enabled). Branches outlive their PR only when the PR is **closed without merging** — typically an assistant scaffold attempt that was superseded or a feature spike that didn't pan out.

**Rule of thumb:** a `claude/` or `codex/` branch with no open PR is an abandoned scaffold attempt. Safe to delete on sight.

Known survivors as of `9d74d1a` (delete on next pass):

- `codex/scaffold-core-agent-system-with-initial-files` (PR #1, closed)
- `codex/scaffold-core-agent-system-with-initial-files-eve0rd` (PR #3, closed)
- `codex/scaffold-core-agent-system-with-initial-files-m57be1` (PR #2, closed)
- `codex/scaffold-core-agent-system-with-initial-files-r1tkwo` (PR #4, closed)

These are four sibling attempts at the same task; only the last one's content went forward (via a different path), and the branches were left behind when the PRs were closed unmerged.

Delete via https://github.com/QQ-STUDIOS/ROBOPORT/branches — trash icon next to each. Some automated environments can't push ref deletions; if you're driving from one, surface the list to a human maintainer rather than letting it accumulate.

## Commit messages

Conventional Commits, lowercase type:

```
<type>(<optional scope>): <imperative summary>

<optional body — what & why, not how>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `build`, `ci`, `perf`.

Examples (drawn from this repo's history):
- `feat: local Ollama runtime (raw HTTP)`
- `chore: repo hygiene (CODEOWNERS, templates, CI)`
- `fix(executor): stop schema-repair pass from looping on empty arrays`

## Pull requests

1. Branch from the latest `main`.
2. Keep PRs small and focused — one concern per PR.
3. Open as **draft** until CI is green and you've self-reviewed the diff.
4. Required before merge:
   - All status checks passing (`Validate schemas & scripts`, `Supply-chain audit (pip-audit)`)
   - Approvals per the active ruleset — see "GitHub branch protection" below. Solo mode runs with 0 required approvals; team mode should require at least 1.
   - Branch up to date with target
5. Prefer **squash merge** for feature branches.

## Local sync workflow

```bash
git fetch --all --prune
git checkout main
git pull --ff-only origin main
git checkout -b feat/my-thing
# ...work...
git push -u origin feat/my-thing
```

If `--ff-only` refuses, you have diverged — rebase consciously:

```bash
git pull --rebase origin main
# resolve conflicts, then:
git push --force-with-lease
```

Never `git push --force` (without `-with-lease`) on any branch someone else might be on.

## Repo-specific rules

These are non-negotiable for this codebase (see `docs/agent_design_principles.md`):

- **No agent lands without at least one blocker eval** in `evals/evals.json`.
- **Every typed boundary needs a JSON Schema** in `resources/schemas/`.
- **Run `python scripts/validate.py --all`** before pushing — must be clean.
- **Run `python scripts/benchmark.py --target <agent> --dry-run`** for any agent you touched.
- **Don't quiet a failure** to make a test pass. The forbidden pattern is the *quiet 200* — see `resources/prompts/error_handling.md`.

## Secrets

- Never commit `.env`, API keys, model credentials, or `*.pem` / `*.key` files. The `.gitignore` blocks the common cases; that is not a substitute for thinking.
- Add a redacted `.env.example` for every new env var.
- If a secret leaks: rotate first, then scrub history with `git filter-repo` / BFG, then force-push with team coordination.

## Large files / model weights

`.ckpt`, `.safetensors`, `.bin`, `.onnx`, `.gguf`, `.pt`, `.pth` are gitignored. Use Git LFS or external storage (S3, HF Hub, Ollama registry) instead.

## GitHub branch protection (`main`)

Configured via **Settings → Rules → Rulesets** (preferred over the legacy Branch protection rules screen).

**Target branches:** `main` only. Do **not** target `**` or `*` — that gates feature-branch deletion too, which then prevents auto-cleanup of merged branches and stale codex branches.

**Bypass list:** empty in normal operation. Add yourself temporarily for emergency hotfixes, then remove.

**Rules:**

| Rule | Solo mode | Team mode | Notes |
|---|:-:|:-:|---|
| Restrict deletions | ✅ | ✅ | Keep `main` undeletable. |
| Block force pushes | ✅ | ✅ | History stays linear and reproducible. |
| Require linear history | ✅ | ✅ | Matches the squash-merge convention. |
| Require a pull request before merging | ✅ | ✅ | No direct pushes to `main`. |
| → Required approvals | **0** | **1+** | Solo: self-PR-and-merge with CI as the only gate. Team: at least one human approval. |
| → Dismiss stale approvals on push | ✅ | ✅ | Cheap correctness. |
| → Require review from Code Owners | ❌ | optional | Only useful once a CODEOWNERS file with real teams exists. |
| Require status checks to pass | ✅ | ✅ | See below. |
| → Require branches up to date before merging | ✅ | ✅ | Catches integration-time regressions. |
| Require signed commits | ❌ | optional | Overkill for solo; revisit on team onboarding. |
| Require deployments to succeed | ❌ | ❌ | No CD pipeline yet. |
| Require code scanning results | ❌ | ❌ | No code-scanning workflow wired up. |

**Required status checks (search by name when adding):**

- `Validate schemas & scripts`
- `Supply-chain audit (pip-audit)`

If a check doesn't appear in the search box, run any PR's CI once and it'll register with GitHub.

**Verification after saving:** open a throwaway typo PR. Both required checks should appear under "Some checks haven't completed yet"; the Merge button should stay gray until they go green. In solo mode the button enables immediately on green; in team mode it waits for the approval too.
