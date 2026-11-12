# Governed environment investigation

The `environment_investigation` crew follows a fixed two-step plan:
`environment_investigator → improvement_proposer`. The investigator has one
static REA tool; the proposer has none. Their typed outputs preserve the exact
evidence identity through handoff. Runtime semantic checks reject a reference
that differs from an actual successful tool result.

Run the genuine model-backed workflow with:

```bash
python scripts/benchmark.py --target environment_investigation --live --grade --runs 1
```

The dedicated eval uses `tests/fixtures/rea_project`; it requires no external
job data. Offline benchmark mode remains synthetic and is labeled accordingly.
Configure approved roots locally for additional projects. Findings are
observations; expected improvements remain hypotheses until matched benchmarks
demonstrate their effect. This workflow never applies the proposed change.
