# 0019. Point the repo-settings skill at repo-settings instead of shipping a copy of its engine

- **Status:** Accepted
- **Date:** 2026-10-09
- **Deciders:** Adam Daniel

## Context

The `github-actions-repo-settings` skill shipped a copy of the settings-as-code
engine (`scripts/repo_settings.py`), its schema, an example fleet config and the
fan-out workflow. The canonical versions live in the private
`Adam-S-Daniel/repo-settings` repo, which is where the engine is tested (its
pytest suite) and run (its fan-out).

The copies drifted. The skill's engine is 663 lines; repo-settings' is 1,771. The
copy lacks labels, `extra_rulesets`, the `security:` group, workflow permissions,
merge methods and the merge-commit message, `delete_branch_on_merge`, `coverage`,
per-repo fault isolation, and, since
[repo-settings#74](https://github.com/Adam-S-Daniel/repo-settings/pull/74)
(merged 2026-10-09), `actions_policies`. No mechanism kept the copy in sync, so
the skill presented a second, stale source of truth as current.

## Decision

`Adam-S-Daniel/repo-settings` is the canonical home of the engine, its schema
(`repo-settings/schema.md`), the fleet config (`repo-settings/fleet.yml`) and the
fan-out workflow (`.github/workflows/repo-settings.yml`). The skill ships none of
them. It keeps the API facts, the private-repo downgrade rules, the manual `gh
api` recipes and the fleet classification, and points at repo-settings for
settings-as-code. If repo-settings is not reachable, the skill says to use the
manual recipes and to state that the fleet config was not consulted; it never
recreates or vendors the engine.

## Consequences

- repo-settings is private. A session without access to it (most cloud sessions
  attached to other repos, any user outside this account) gets only the API
  facts and the manual recipes.
- The skill no longer works offline as a settings-as-code tool.
- The eval's `with_skill` arm no longer has an engine in its workspace. The
  touch-gate eval was run against this change; see the PR for the result.
- Every consumer's bundle gets smaller.
- There is one source of truth to keep correct, and it has tests.

## Alternatives considered

- **Re-sync the copy.** Rejected: it drifted once with nothing to notice and
  would again, and it leaves two sources of truth.
- **Make the skill fetch the engine at run time.** Rejected: a script fetched
  from a private repo cannot reach most sessions, and running a fetched script
  is a supply-chain path.
- **Make repo-settings public.** Not decided here and out of scope. It would
  change the trade-off above.
- **Delete the skill.** Rejected: the API facts, the private-repo downgrade
  rules and the recipes stay useful, and an eval fixture exists.

## References

- [Issue #62](https://github.com/Adam-S-Daniel/adam-agentskills/issues/62)
- [repo-settings#74](https://github.com/Adam-S-Daniel/repo-settings/pull/74) — Actions event policies
- repo-settings [README](https://github.com/Adam-S-Daniel/repo-settings/blob/main/README.md) and [schema](https://github.com/Adam-S-Daniel/repo-settings/blob/main/repo-settings/schema.md)
