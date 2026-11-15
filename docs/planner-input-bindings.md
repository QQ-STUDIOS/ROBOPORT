# Planner input contracts

Generic planning uses a structured internal proposal. Context bindings select an existing JSON pointer and copy the exact value. An empty destination binds an object as the whole step input; named object-field destinations preserve requested nesting. Whole arrays are copied as values rather than rebuilt item by item. Missing sources, conflicting destinations, malformed pointers and non-object root inputs are rejected.

Step bindings identify an earlier zero-based step slot. Runtime generates stable IDs and concrete ID.output references, derives waves from data and control dependencies, and copies execution flags from the registry. Self/future references are rejected. Literal inputs cannot contain free-text dependency tokens. Quoted untrusted text bound from context remains data.

Small contexts constrain source pointers to an inventory. Larger contexts permit valid pointer syntax and validate existence at compilation so late records are not silently excluded by the inventory ceiling. Context selection still depends on planner intent; materialization proves exact copying and valid references, not universal understanding of arbitrary prose.

An unavailable trusted required_owner is rejected before inference. Model refusals use a dedicated rejection branch and become caller-visible ValueError responses, rather than executable plans with invented contracts. Output contracts include the core and installed station packs. The fixed DataOps format and deterministic workflow fast paths remain supported.
