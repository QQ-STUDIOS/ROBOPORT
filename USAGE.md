# ROBOPORT — How to use

End-to-end runs of the agent crew, locally or against the Anthropic API.

## 1. Install

```powershell
cd C:\Users\Allen\Downloads\ROBOPORT
pip install -r requirements.txt
```

## 2. Pick a provider

ROBOPORT runs against either a local Ollama server or the Anthropic API.

### Local (Ollama)

Default. Models are pulled by `ollama pull`.

```powershell
ollama pull qwen2.5:14b   # recommended for the 4070 SUPER (~9GB Q4)
# Reasoning agents (planner, grader, etc.) use $env:OLLAMA_MODEL_REASONING
# All other agents use $env:OLLAMA_MODEL_DEFAULT
$env:OLLAMA_MODEL_REASONING = "qwen2.5:14b"
$env:OLLAMA_MODEL_DEFAULT   = "qwen2.5:14b"
```

Other env knobs:

| Var | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama base URL |
| `OLLAMA_MODEL_REASONING` | `qwen3.5:latest` | Used when registry `model_hint = reasoning-strong` |
| `OLLAMA_MODEL_DEFAULT` | `gemma4:latest` | Everything else |

### Remote (Anthropic)

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# Optional model overrides; defaults below
$env:ANTHROPIC_MODEL_REASONING = "claude-opus-4-7"
$env:ANTHROPIC_MODEL_DEFAULT   = "claude-sonnet-4-6"
```

## 3. Run

`benchmark.py` runs an eval set against a target agent or crew. Without `--live` it uses stubs (CI-safe). With `--live` it runs the real model loop.

```powershell
# Stubs only (fast, no model)
python scripts/benchmark.py --target jd_crew --runs 1

# Local run (default provider = ollama)
python scripts/benchmark.py --target jd_crew --runs 1 --live

# Remote run
python scripts/benchmark.py --target jd_crew --runs 1 --live --provider anthropic

# Full A/B (3 runs each, with grading)
python scripts/benchmark.py --target jd_crew --runs 3 --live --provider ollama   --label ollama-3x   --grade
python scripts/benchmark.py --target jd_crew --runs 3 --live --provider anthropic --label sonnet-3x   --grade
```

Output lives under `evals/benchmarks/<label>/`:

```
evals/benchmarks/<label>/
  summary.json
  eval_<id>/
    run_<n>/
      plan.json          # planner output
      final_output.json  # last step's typed output
      run.log            # JSONL event log
      grading.json       # only when --grade
```

## 4. Aggregate / compare

```powershell
python scripts/aggregate.py --benchmark evals/benchmarks/sonnet-3x
python scripts/aggregate.py --compare --baseline evals/benchmarks/ollama-3x --candidate evals/benchmarks/sonnet-3x
```

## 5. Validate without running

```powershell
python scripts/validate.py --all
```

Schema-checks `evals/evals.json` and `agents/registry.json`. CI runs the same.

## 6. Tools

Per-agent tool whitelists live in `config/agent_config.yaml`. Real implementations are in `scripts/roboport_runtime/tools.py`:

| Tool | Backend |
|---|---|
| `load_profile` | Local file read |
| `fetch_url` | `requests.get` + HTML strip |
| `dedupe_jobs` | Pure function |
| `parse_jd_skills` | Keyword vocab match |
| `ats_score` | Keyword overlap (0.0–1.0) |
| `lookup_jurisdiction` | Static table (HIPAA/GDPR/etc.) |
| `lookup_comp_band` | Static salary table |
| `search_linkedin` | Greenhouse + Lever aggregator (35 boards) |
| `search_indeed` | Same backend as `search_linkedin` |
| `search_company_careers` | Greenhouse → Lever fallback by slug |

LinkedIn / Indeed proper require paid APIs; the names are kept for spec compatibility.

## 7. Output schemas

`step.output_type` (e.g. `TechnicalAnalysis`, `list[Job]`) is resolved against `resources/schemas/output.schema.json` and:
1. Inlined into the executor prompt so the agent sees the required shape.
2. Validated post-hoc with `jsonschema`. Validation failures appear as a non-blocking entry in `criteria_results` with `passed: false` — they don't fail the step.

To make schema violations fail the step, change the executor accordingly in `scripts/roboport_runtime/executor.py`.
