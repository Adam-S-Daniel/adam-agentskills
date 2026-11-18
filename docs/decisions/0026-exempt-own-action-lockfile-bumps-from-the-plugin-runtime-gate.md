# 0026. Exempt own-action lockfile bumps from the plugin runtime gate

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Adam Daniel
- **Amends:** [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md), the clause gating `.github/workflows/actions.lock`

## Context

`scripts/plugin_runtime_gate.py` lists `.github/workflows/actions.lock` among
its gate files, because the lockfile chooses the commit every tag-pinned action
runs at, the gate's own checkout included. So every action bump waits for the
owner's approval on the `plugin-runtime-review` environment, including a bump
of an action the owner wrote and released.

The lockfile is pull-request data. Its `owner_id`, `repo_id` and `commit` are
whatever the pull request says, and the runner runs the commit it names even
when that disagrees with the tag. The check that would catch a wrong pin,
`Adam-S-Daniel/verify-actions-lock` in `ci.yml`, is itself an own action pinned
by this file, so a bump can replace the verifier that would judge it.

The gate runs on `pull_request_target` from the base branch and reads the pull
request through the API. The runner reads the lockfile with a different YAML
parser from the gate's PyYAML.

## Decision

**A lockfile change that touches only the owner's own actions does not gate.**
Every other lockfile change still does, and so does anything else in the same
pull request that gates for another reason. The change is exempt only when all
of this holds:

- No other gate file changed, and the lockfile is a regular file modified in
  place (not added, removed, renamed or turned into a symlink).
- Both sides parse strictly: no repeated key, alias, anchor, explicit tag,
  document marker, merge key or scalar outside what `gh actions-lock` writes.
  Both have exactly `version`, `workflows` and `dependencies`, the expected
  entry fields and types, and the same `version`.
- Every dependency that differs is `adam-s-daniel/<repo>@<tag>`, with the owner
  matched without regard to ASCII case. All others are identical.
- Each added or changed entry has `owner_id: 4205216`, the numeric id of
  `Adam-S-Daniel`, and a `ref` equal to its key's tag.
- `workflows` keeps the same keys, and each list changes only in own items.
  Every listed item has a dependency entry.
- At least one dependency or list really changed.
- The gate workflow neither uses an own action nor has one in its list, and its
  list is unchanged. Its third-party actions are covered by the identity rule.
- **GitHub confirms each added or changed entry**: the repository has that
  `repo_id`, that name and owner id `4205216`, and the tag resolves, through
  any annotated tags, to the locked commit.

A refusal keeps the `gate-itself` reason and adds `lock-not-exempt` with a fixed
message. Anything unexpected, an API failure included, is a refusal. The
workflow, its required check and the environment do not change.

## Consequences

- A Dependabot bump of an own action can merge without the owner, since
  `dependabot-auto-merge.yml` already allows `actions.lock` and `uses:`-only
  workflow edits. A compromise of an own action's repository or release now
  reaches `ci.yml` without a human decision. That is the accepted cost.
- The API check proves the tag was at the commit when the gate ran. The lockfile
  pins that commit, so moving the tag later does not change what runs.
- A pull request behind `main` compares against `main`'s tip, so a third-party
  bump that landed meanwhile gates it until it is rebased. That errs toward
  asking.
- A change in `gh actions-lock`'s output makes own bumps gate again.
  `test_the_checked_in_lockfile_is_the_shape_the_exemption_accepts` reports it.
- An own action in a private repository gates, because the workflow's token
  cannot read it.
- The exemption first runs on the pull request after this one merges, because
  `pull_request_target` uses the base branch's script.

## Alternatives considered

- **Trust the lockfile's `owner_id` alone.** It is pull-request data, so it
  proves nothing about a renamed owner or an imposter commit.
- **Rely on `verify-actions-lock` for the pin.** A bump of that action would
  choose its own judge.
- **Exempt every entry the gate workflow does not use.** The owner's decision
  is narrower: third-party pins stay reviewed.
- **Drop the lockfile from `GATE_FILES`.** The gate's own checkout would then
  move without review.

## References

- `scripts/plugin_runtime_gate.py`, `scripts/test_plugin_runtime_gate.py`
- [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md)
- [Fleet ADR 0024](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0024-actions-pin-through-lockfiles.md)
