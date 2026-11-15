# Planner input contracts

Generic planning uses a structured internal proposal. Context bindings select an existing JSON pointer and copy the exact value. An empty destination binds an object as the whole step input; named object-field destinations preserve requested nesting. Whole arrays are copied as values rather than rebuilt item by item. Missing sources, conflicting destinations, malformed pointers and non-object root inputs are rejected.

Arrays and scalars require a named destination field. The model schema constrains known context pointers by their actual source type. Step bindings identify an earlier zero-based step slot without a model-selected destination; runtime materializes them under `input.prior_outputs`, keyed by actual step ID. This prevents two join references from overwriting one target. Runtime generates stable IDs and concrete ID.output references, derives waves from data and control dependencies, and copies execution flags from the registry. Self/future references are rejected. Literal inputs cannot contain free-text dependency tokens. Quoted untrusted text bound from context remains data.

Small contexts constrain source pointers to an inventory. Larger contexts permit valid pointer syntax and validate existence at compilation so late records are not silently excluded by the inventory ceiling. Context selection still depends on planner intent; materialization proves exact copying and valid references, not universal understanding of arbitrary prose.

An unavailable trusted required_owner is rejected before inference. Model refusals use a dedicated rejection branch and become caller-visible ValueError responses, rather than executable plans with invented contracts. Output contracts include the core and installed station packs. The fixed DataOps format and deterministic workflow fast paths remain supported.

The existing improvement-proposer handoff also needs a bare `investigation_step` control ID. Runtime derives it from the unique earlier environment-investigator dependency; ordinary prior-output bindings still use `.output`. Missing or conflicting investigation dependencies are rejected before execution.
