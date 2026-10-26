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

- **A runs listing can lie about absence.** In 2026-09 the per-workflow
  `/actions/workflows/<id>/runs` endpoint intermittently answered with a
  ~3-week-old snapshot while the repo-wide `/actions/runs` listing, queried
  seconds later, already had the recent runs (not reproducible afterward:
  15/15 calls came back current). It produced false "no recent success"
  alerts
  ([jodidaniel.com#264](https://github.com/jodidaniel/jodidaniel.com/issues/264),
  fixed in
  [cms-platform#459](https://github.com/Adam-S-Daniel/cms-platform/pull/459)).
  It is its own section rather than folded into another, because the failure
  is in the listing itself, not in a script run inside a job. First written
  in the retired registry's draft
  old-registry PR 180.

## Eval status

No eval fixture exists for this skill in skills-evals.
