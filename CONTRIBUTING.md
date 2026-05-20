# Contributing to ROBOPORT

## Branch model

- `main` — protected. Always deployable. Only updated via reviewed PR.
- `feat/<short-name>` — new feature work.
- `fix/<short-name>` — bug fixes.
- `chore/<short-name>` — tooling, config, deps, repo hygiene.
- `docs/<short-name>` — documentation-only changes.
- `claude/<task>` / `codex/<task>` — branches authored by automated assistants. Treat the same as a human PR.

**Never** push directly to `main`. **Never** force-push a shared branch.

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
   - All status checks passing
   - At least one approving review
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

## Recommended GitHub branch protection (`main`)

Enable in repo Settings → Branches:

- Require pull request before merging
- Require at least 1 approval
- Require status checks to pass (CI workflow)
- Require branches to be up to date before merging
- Require linear history
- Block force pushes
- Block deletions
