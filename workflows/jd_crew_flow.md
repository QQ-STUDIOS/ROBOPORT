# Workflow: JD-Crew Flow

The flagship crew shipped with ROBOPORT, matching the Crew Builder UI. Sequential JD analysis: **scout → (technical, compliance) → strategist → synth**, with optional fan-out to salary, resume, and cover-letter agents.

This file is the executable spec. The Planner can read this directly and turn it into a typed plan.

---

## Crew shape

```mermaid
flowchart LR
    SC[job_scout<br/>list[Job]] --> TA[technical_analyst<br/>TechnicalAnalysis]
    SC --> CR[compliance_risk<br/>ComplianceAnalysis]
    TA --> AS[application_strategist<br/>CandidateMatch]
    CR --> AS
    AS --> SY[synthesizer<br/>FinalReport]
    SY -.optional.-> SE[salary_estimator]
    SY -.optional.-> RT[resume_tailor]
    SY -.optional.-> CW[cover_letter_writer]
```

Flow stats this is designed to hit (matching the Crew Builder UI for the canonical run):

| Stat | Target | Notes |
|---|---:|---|
| `llm_calls`     | 4    | scout, technical, compliance, strategist |
| `deterministic` | 2    | dedupe inside scout, full synthesizer  |
| `triggers`      | 2    | user prompt + optional re-trigger on warnings |
| `tools_attached`| 10   | search APIs ×3, fetch ×1, llm ×1, schemas ×3, profile loader ×1, geo lookup ×1 |

---

## Step contract

| # | Wave | Step | Owner | Deterministic | Inputs | Output |
|---|---|---|---|---|---|---|
| 1 | 0 | `scout` | `job_scout` | partial (dedupe) | `search_query`, `profile?` | `list[Job]` |
| 2 | 1 | `technical` | `technical_analyst` | no | `list[Job]`, `profile` | `list[TechnicalAnalysis]` |
| 3 | 1 | `compliance` | `compliance_risk` | no | `list[Job]` | `list[ComplianceAnalysis]` |
| 4 | 2 | `strategy` | `application_strategist` | no | `list[Job]`, technical, compliance, `profile` | `list[CandidateMatch]` |
| 5 | 3 | `synth` | `synthesizer` | yes | all upstream | `FinalReport` |
| 6 | 4 | `salary` (opt) | `salary_estimator` | no | `Job`, `TechnicalAnalysis` | `SalaryBand` |
| 7 | 4 | `tailor` (opt) | `resume_tailor` | no | `Job`, `TechnicalAnalysis`, `CandidateMatch`, `profile` | `TailoredResume` |
| 8 | 4 | `letter` (opt) | `cover_letter_writer` | no | same as tailor + voice | `CoverLetter` |

Steps 6–8 are gated on `match.priority <= 2` — only run for the high-priority matches. This keeps the LLM budget bounded.

---

## Run-level success criteria

- `FinalReport.summary.total_jobs >= 1` (else surface "no matches" cleanly)
- Every `ranked_match` carries `technical`, `compliance`, and `match`
- Sort order: `priority` ASC, `fit_score` DESC, `posted_at` DESC
- All `findings[].evidence` quote the JD, not paraphrase it
- `warnings[]` lists any optional-step failures (graceful degrade pattern)

## Failure policy

| Step | Failure | Action |
|---|---|---|
| `scout` | 0 jobs returned | finalize cleanly with "no matches"; don't run downstream |
| `technical` | individual job fails | continue with other jobs; log a `warning` for the failed one |
| `compliance` | individual job fails | same as technical |
| `strategy` | fails for a job | drop the match; surface in `warnings[]` |
| `synth` | fails | hard abort — synth is deterministic; failure means a contract bug, not data flakiness |
| `salary` / `tailor` / `letter` | any | graceful degrade — log to `warnings[]`, continue |

---

## Eval coverage

The shipping eval set (`resources/examples/eval_example.json`) covers:

- happy-path end-to-end (eval 1)
- empty results edge case (eval 2)
- citation discipline in compliance (eval 3)
- anti-hallucination in resume tailor (eval 4)

For new evals, add to `evals/evals.json` with `target: "jd_crew"`.

---

## Running it

```bash
# One-shot
python scripts/benchmark.py --target jd_crew --runs 1 --query '{"titles":["Senior Data Engineer"], "locations":["Remote-US"], "posted_within_days": 14}'

# With profile
python scripts/benchmark.py --target jd_crew --runs 1 \
  --query '...' \
  --profile resources/datasets/profile_example.json

# Replay a previous run
python scripts/benchmark.py --replay runs/<run_id>
```

---

## Where to extend

- **New source:** add to `job_scout`'s tool whitelist, update its anti-pattern list to include the new source's quirks
- **New finding type:** extend `resources/datasets/compliance_vocab.json` and add an eval that exercises it
- **New optional output (e.g., interview prep):** add an agent under `agents/domain/crew_builder/` and wire it into wave 4 with the `match.priority <= 2` gate
