# Purpose — review-bash-ci-reliability

Maintenance context only; never loaded at inference.

## What it is for

A checklist for shell that runs in CI, where a failure that is swallowed reads as
a green check. Each item is a pattern that makes a script succeed, or die without
saying why, when something underneath it failed. The eval fixture that measures
it is skills-evals' `evals/review-bash-ci-reliability/`.

## The incident behind item 7

- **An expected non-zero inside `$(...)`**, from
  [adam-agentskills#20](https://github.com/Adam-S-Daniel/adam-agentskills/issues/20).
  `_agent-guidance`'s `test/run-tests.sh` (lines 3300-3302) ran
  `grep -n ... | head -1 | cut ...` under `set -euo pipefail`. A file without the
  marker made `grep` exit 1, `pipefail` failed the pipeline, and `set -e` killed
  the test runner mid-file with no `Results:` line, instead of recording one
  FAIL. It surfaced on an image whose `yq` differed from CI's. A test harness is
  the worst place for this, because one failing assertion hides every test after
  it.

Items 1 to 6 predate this file; their motivating incidents are in git history
and the fixture above, not restated here.
