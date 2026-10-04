# Purpose — migrate-claude-memory

Maintenance context only; never loaded at inference.

This local skill inventories machine-local auto-memory stores, helps a human
review cleanup candidates, and copies a selected store into portable
repository memory without deleting its source.

[Issue #14](https://github.com/Adam-S-Daniel/adam-agentskills/issues/14)
tracks naming changes that prompted this scoped decoder follow-up. A failed
decode created a data-loss hazard: failed or lossy path decoding labeled stores
`ORPHANED`, while the cleanup instructions suggested deleting those stores.
The inventory now keeps unsupported or ambiguous paths `UNRESOLVED`, including
collisions with existing dotted names and mixed existing/missing candidates.
Only one supported missing final component beneath an accessible existing
parent can be `ORPHANED`; a human must independently verify that result.

The focused tests run the inventory with invented temporary homes and cover
classification, JSON and text output, aggregate counts, and preserved memory
contents. This skill remains local because its inputs live on the machine;
none of these tests inspect the operator's real memory stores.
