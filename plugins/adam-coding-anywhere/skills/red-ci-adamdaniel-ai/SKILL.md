---
name: red-ci-adamdaniel-ai
description: Diagnose and fix a red or blocked CI result after pushing to a cms-platform-style Jekyll site such as adamdaniel.ai. Use when a pushed branch or PR shows a failed, cancelled or never-reported required check, a PR stuck BLOCKED, or a job that fails at "Set up job". Covers reading every check conclusion on the head commit, telling a cancelled required check from a real failure, and the fixes for concurrency cancels, timeout walls, path-filtered required checks and unpinned actions.
---

# Red CI after a push

Never call a push done because the push succeeded or a watch command exited.
Read the conclusion of every check on the head commit, then classify each red
or missing one before touching anything.

## 1. Read the whole check list for the head sha

Query check runs AND legacy statuses for the head commit. A check run has
`conclusion`, a status has `state`; filtering on one hides the other.
List every entry that is not `success` or `neutral`, including `cancelled`,
`timed_out`, `skipped` on a required context, and required contexts that
never appeared at all.

## 2. Classify each one

| Signal | Cause | Fix |
|---|---|---|
| `cancelled` on a required check, no failing step | A `concurrency:` group on a job that fires twice per sha (`opened` + `synchronize`, label events), or a `timeout-minutes` wall (GitHub reports a timeout as `cancelled`) | Delete the group, or make triggers disjoint. Move the wall onto a work job no ruleset names and publish the required context from a `needs:` + `if: always()` gate job |
| Required context never reports, PR BLOCKED, everything else green | The workflow has `on.pull_request.paths` | Remove `paths:`; add an early step that detects salient changes and gate each later step on its output, so the job still ends `success` |
| Fails at "Set up job" with no step output | An action ref the repo policy rejects (tag or branch instead of a full 40-character SHA) | Pin to the full SHA taken from the repo's own pin list; no trailing version comment |
| One step fails with a real error | A genuine test or build failure | Read that job's log and fix the cause; never skip or quarantine the test |
| Release or version bump PR BLOCKED | A file that names the current release (an AGENTS.md line, a manifest) was not bumped with the rest | Make the bump atomic in one PR; grep for the old version |

## 3. Fix the smallest thing, then verify on the new head

- Change only the workflow or file the classification names.
- Never push an empty commit or re-run just to turn it green.
- After pushing, re-read the check list for the NEW head sha and report the
  parsed conclusions, naming any check that is still pending.
- A mixed PR (code plus ignored paths) can show a green stub for a heavy
  check; wait for the real run before calling it passing.
