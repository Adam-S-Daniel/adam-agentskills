# Purpose — github-actions-repo-settings

Maintenance context only; never loaded at inference.

## What it is for

Repository security settings as code: SHA pinning required, fork-PR approval for
outside collaborators, and a default-branch ruleset, applied from one manifest
across a fleet (generate, diff, apply) with manual `gh api` recipes as the
fallback. The fleet rule it implements is that default branches are PR-only by
ruleset and that settings are never hand-granted in the UI.

## The boundary it has to keep

`cms-platform` and the sites that consume it manage their own settings from the
platform's `repo-settings.yml`, and are excluded from this skill's fan-out
(`manage: false`): GitHub enforces the union of all rulesets, so a second
writer would create drift the platform's audit cannot see. That section of the
skill described the platform's Actions-permissions support as a gap while its
first PR (cms-platform#168) was open. It merged 2026-07-13 and the platform now
manages `sha_pinning_required` and the fork-PR `approval_policy`; the skill was
corrected 2026-10-04.

## Eval status

Fixtures exist in skills-evals: `evals/github-actions-repo-settings/drift-diagnosis`
and `evals/github-actions-repo-settings/onboarding`, the latter marked `draft`.

## History moved from SKILL.md (2026-10-05)

- 2026-10-09: the engine copy (`scripts/repo_settings.py`) and the assets
  (schema, example fleet config, fan-out workflow) were removed and the skill
  points at `Adam-S-Daniel/repo-settings` (issue #62, ADR 0019). The copy was
  663 lines against repo-settings' 1,771 and nothing kept it in sync.

- In this fleet, `_agent-guidance`'s nightly `drift-report.yml` triggered the
  "hold the ruleset while a workflow pushes to its own default branch" case.
- The cms-platform settings-as-code work (`repo-settings.yml` manifest +
  `scripts/audit-repo-settings.js`, rulesets) merged 2026-07-13 as cms-platform
  PR #168, `feat/109-repo-settings-as-code`.
- The first version of that work (PR #168) did not manage the Actions-permissions
  settings, and SKILL.md once described that as a gap to close.
