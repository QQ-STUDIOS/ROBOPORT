# Grader verification boundaries

The runtime verifies two blinded model assessments and resolves their citations against the exact artifact context. The model selects source/pointer pairs from a bounded inventory of existing JSON paths. Large artifacts retain container references; omitted step context cannot be cited by an assessment. Empty strings are valid evidence for assertions about emptiness.

A conservative runtime assertion grammar independently checks scalar equality (`is` or `==`), ledger sums and counts, negative transaction counts, booleans, and extraction hash equality. Supported report fields are direct final-output or summary fields. Ledger operands must come from the unique successful data_engineering step identified in the plan. Arithmetic uses bounded Decimal values; duplicate JSON keys, nonfinite numbers, ambiguous operands, and missing evidence fail closed. Artifact instructions never define predicates or expected values.

Each checked result records `deterministic_check` and retains both original model assessments, including any wrong findings. `verification.deterministic_coverage` is the number of assertions checked by runtime rules. A recognized assertion with unavailable evidence is INCONCLUSIVE. Provider, schema, and invalid-reference failures still require review and cannot retain provisional passes.

Unsupported prose continues to use blinded model consensus. Agreement and valid references do not guarantee semantic correctness: these results are model judgments, not independently proven facts. Use supported explicit factual assertions or an independent artifact audit for acceptance-critical quantitative claims. The checks establish consistency with recorded extraction evidence; they do not authenticate an arbitrary external dataset or guarantee that all prose expectations are true.

Planner execution flags are copied from the registered agent metadata after generation. They are not model decisions. DataOps plans additionally require the ordered CleanedDataset and DataReport contracts.
