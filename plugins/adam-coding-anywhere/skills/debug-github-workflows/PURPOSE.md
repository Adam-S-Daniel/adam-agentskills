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
- **The `gh` install block pins an exact release.** Cloud images often lack
  `gh`, and the fleet rule is exact pins with a 7-day cooldown, so the block is
  refreshed by hand to the newest release older than 7 days rather than
  resolving `releases/latest` at run time. Last refreshed 2026-10-04 to v2.101.0
  (published 2026-09-15); it was v2.67.0 before.

## Eval status

No eval fixture exists for this skill in skills-evals.
