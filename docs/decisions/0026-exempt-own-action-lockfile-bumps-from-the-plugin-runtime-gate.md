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
  place (not added, removed, renamed or turned into a symlink) at the base, the
  merge base and the head.
- All three copies are ASCII, parse with no repeated key, have the expected
  shape and lowercase `owner/repo` keys, and are byte for byte what
  `gh actions-lock` writes for their values, apart from a leading block of
  comment lines. An entry may carry the `uses` list the tool writes for a
  composite action's nested actions. That comparison is what refuses CRLF, flow style, complex keys, aliases,
  anchors, tags, other quoting and stray comments, so the runner's parser has
  nothing to read differently.
- From base to head **and** from merge base to head: `version` is the same,
  every dependency that differs is `adam-s-daniel/<repo>@<tag>`, and every other
  entry is identical. The second comparison matters because a verdict outlives
  its base: an older third-party pin the branch carries can match `main` today
  and undo a later fix when merged.
- Each added or changed entry has `owner_id: 4205216`, the numeric id of
  `Adam-S-Daniel`, a `ref` equal to its key's tag, and no `uses` list: GitHub
  confirms the entry's own commit, not the nested pins beside it.
- `workflows` keeps the same keys, and each list changes only in own items.
  Every listed item has a dependency entry.
- At least one dependency or list changed from base to head.
- The gate workflow neither uses an own action nor has one in its list, and its
  list is unchanged. Its third-party actions are covered by the identity rule.
- **GitHub confirms each added or changed entry**: the repository has that
  `repo_id`, that name and owner id `4205216`, and is not a fork; the tag
  resolves, through any annotated tags, to the locked commit; that commit is on
  the repository's default branch; and the tag has a published release, not a
  prerelease, marked `immutable: true`.

Separately, for **every** pull request, the gate re-reads the pull request after
listing its files and fails closed unless the head sha, base sha and base branch
still match the event and the base branch is the default branch. The file list
carries no sha of its own, so this is what ties it to the commits being judged.

A refusal keeps the `gate-itself` reason and adds `lock-not-exempt` with a fixed
message. Anything unexpected, an API failure included, is a refusal. The
workflow, its required check and the environment do not change.

## Consequences

- **A compromised own release runs in CI with no person involved.** The exempted
  action, `Adam-S-Daniel/verify-actions-lock`, is the `Verify action lockfile`
  step of `ci.yml`'s required `pytest` job. It runs before that job's later
  guards: "Loop pull requests change nothing that runs before their check",
  "Shipped names and loop pull requests pass the one-way-door check", the test
  suites and the one-way-door re-check near the end of the job. A step can change what later
  steps run through `GITHUB_PATH` and `GITHUB_ENV`, so a malicious release could
  affect those checks, on the bump's own pull request and on later ones. The
  job also runs on every push to `main`. Its token is read-only (`contents`,
  `pull-requests`), it references no repository secrets, and it uses no cache.
- **It can arrive quickly.** `dependabot-auto-merge.yml` sweeps daily and already
  allows `actions.lock` and `uses:`-only workflow edits. Once
  `.github/dependabot.yml` checks own actions daily with no cooldown for them, as
  [#180](https://github.com/Adam-S-Daniel/adam-agentskills/pull/180) proposes, a
  new own release can reach `main` within about a day or two. Until then that
  file checks weekly and applies a 7-day cooldown.
- The provenance checks reduce that risk and do not remove it. They require a
  commit on the default branch and an immutable release, so a stray tag, a
  fork-network commit or a tag moved later does not qualify. An attacker who can
  merge to the action's default branch and publish a release still passes.
- **Removals and moves to an older own tag are exempt too.** A removal needs no
  provenance check. A move back passes when the older tag meets the same checks,
  so a bump can return to an older release with a known flaw.
- An own action without immutable releases, or in a private repository the
  workflow's token cannot read, gates.
- A pull request is compared with the base commit of its latest event. A
  third-party bump that reached `main` before that event gates it until it is
  rebased, which errs toward asking. One that lands afterward starts no new run,
  so the verdict stands; the merge still combines the two lockfiles as text.
- Changing a pull request's base starts no new gate run, because the workflow
  does not listen for `edited`. A pull request aimed at another branch gates, so
  this matters only if the owner approves it there and it is then retargeted to
  the default branch.
- Any pull request aimed at a branch other than the default now gates, and so
  does one whose head or base moves while the gate reads it, until a new run.
- A change in `gh actions-lock`'s output makes own bumps gate again.
  `test_the_checked_in_lockfile_is_the_shape_the_exemption_accepts` reports it.
- The exemption first runs on the pull request after this one merges, because
  `pull_request_target` uses the base branch's script.

## Alternatives considered

- **Trust the lockfile's `owner_id` alone.** It is pull-request data, so it
  proves nothing about a renamed owner or an imposter commit.
- **Screen YAML features one by one.** Each screen is another place for two
  parsers to differ; comparing against the tool's exact output closes them
  together. It accepts all 20 fleet lockfiles read on 2026-11-18.
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
