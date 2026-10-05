# Purpose — debug-github-workflows

Maintenance context only; never loaded at inference.

## What it is for

A recipe for diagnosing a failing GitHub Actions run from a cloud session that
has little installed: how to get `gh`, where the logs and annotations live, and
the patterns that make a run read green (or fail with no useful message) while
the work did not happen — process substitution that swallows a failure under
`set -e`, scripts that exit 0 on "nothing found", `|| true`, workflows that
exist only on another branch.

## Why it is written the way it is

- **A run marked success is not evidence.** The skill tells the reader to read
  the actual step logs, because the patterns above are all ways a success badge
  hides an unperformed task. This is also why the process-substitution case is
  shared with `review-bash-ci-reliability`.
- **The `gh` install step defers to the official instructions.** Cloud images
  often lack `gh`; the skill checks `command -v gh` first and otherwise installs
  per https://github.com/cli/cli#installation, taking the newest release at
  least 7 days old (the fleet's cooling-off rule) rather than a hard-coded
  pin. It was a hand-refreshed pin (v2.101.0, published 2026-09-15, refreshed
  2026-10-04; v2.67.0 before) until 2026-10-05.

## Eval status

No eval fixture exists for this skill in skills-evals.
