# Purpose - fan-out-triage

Maintenance context only; never loaded at inference.

## What it is for

Triaging thousands of vendor release bullets (or any large item list) against
several repos with parallel subagents. Motivated by a changelog session in
which a triage agent cited item IDs off by two while its reasons were right,
background agents showed no progress until they finished, an orchestration
tool was launched without the owner's opt-in, and quality depended on how
well the agents knew the repos' dependency surface.

## Triage (hook vs skill)

Not mechanical: whether an item truly affects a repo needs reading the item
against the code, and how work is chunked is judgment. The part that is
mechanical, the ID check, ships as `scripts/check_ids.py` inside the skill.

## Decisions made without a person (skill-creator ran non-interactively)

- Working directory `triage/` and fixed file names so a reviewer and the
  check script find results without being told.
- The contract's "first 8 words" column is the ID guard; the script compares
  it to the source text.
- Staleness threshold is stated as a number to set in advance; ten minutes is
  an example, not a rule.
- Placed in `adam-coding-anywhere` as the proposal asked; the name still needs
  the registry's adversarial round before merge.
