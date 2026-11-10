# 0024. Leave a skill to the account plugin that already delivers it

- **Status:** Accepted
- **Date:** 2026-11-10
- **Deciders:** Adam Daniel (the fix asked for in issue #116)

## Context

In a claude.ai cloud session the account's enabled plugins are synced into
`~/.claude/plugins/synced/<bucket>/<plugin>/` before SessionStart hooks run.
`adam-anything-anywhere` is enabled on the account because it carries the
fleet-guidance hook ([ADR 0023](0023-deliver-the-fleet-guidance-from-a-plugin-hook-that-fetches-it.md)),
so its two skills arrive that way. Every consumer `skills.lock` also names the
`adam-anything-anywhere` bundle, so `.claude/hooks/skills-bootstrap.sh`
installed a second copy of each. A cloud session listed both
`finding-unknowns` and `adam-anything-anywhere:finding-unknowns`, and both
`writing-adrs` and `adam-anything-anywhere:writing-adrs`
([issue #116](https://github.com/Adam-S-Daniel/adam-agentskills/issues/116),
seen again on 2026-11-10). Two copies cost their descriptions twice, can
differ in version, and leave it unclear which one a session used, which skews
the usage counts eval scope depends on.

[ADR 0010](0010-let-pinned-channels-own-the-terminal.md) kept cloud sessions
"unchanged, because they cannot opt out". That is still true of the account
channel: a cloud session cannot turn the plugin off without losing the
fleet-guidance hook it carries.

## Decision

The bootstrap hook does not install a locked skill `<bundle>/<name>` when a
synced plugin delivers it: the bucket's `manifest.json` lists a plugin named
`<bundle>` whose `marketplaceName` is the lock source's repository name (the
URL's last segment, less `.git`), with `installationPreference: available`
(the only value seen on a loaded plugin), not turned off by
`enabledPlugins["<bundle>@synced"]` or `["<bundle>@<marketplace>"]` in the
user, project or local settings, and that plugin's `skills/<name>/` is a real
directory holding a `SKILL.md` file. The check runs after the repo-owned
collision check, so a repo's own copy still wins and is still reported. The
hook removes its own earlier copy through the collision arm's existing
`rm -rf`, counts the skill as available, and names it in an `— OK` note:
`left N skills to the account's synced plugins, whose copies already load and
are not digest-checked against the lock (...)`. `skills-doctor` reports such
a skill as the note `delivered-by-an-account-plugin`, not as missing.

It also requires exactly one account bucket under `plugins/synced/` (several
cannot say which account the session loads), and every settings file that
exists to parse: one that does not might be the one turning the plugin off, so
the hook leaves nothing to a plugin and installs.

It applies in Claude mode only. Codex never loads Claude's synced plugins, and
on a durable machine the hook has already exited. Anything it cannot read
matches nothing, so a failure falls back to the old behavior: the skill
installs, twice.

## Consequences

- One copy per skill in cloud sessions, under the plugin's namespaced name
  (`adam-anything-anywhere:finding-unknowns`), not the short name, so a bare
  `/finding-unknowns` no longer exists there. The usage census already strips
  the plugin prefix, so counts are unaffected, but it does not yet attribute a
  load from `~/.claude/plugins/synced/` to a copy and version
  ([skills-evals#953](https://github.com/Adam-S-Daniel/skills-evals/issues/953)).
- The marketplace match uses the repository name only, not its owner, because
  the manifest carries no owner: a lock on a fork named `adam-agentskills`
  matches the account's `adam-agentskills` marketplace.
- That copy is the plugin's, not the lock's: it is whatever claude.ai last
  synced from the marketplace, not digest-verified against the lock's pin.
  How soon claude.ai re-syncs after a release is not measured here (ADR 0012
  saw updates arrive only by hand); a lag shows as an older plugin copy, not
  as a hook failure.
- Sessions with no synced plugin (Codex Cloud, an account that has not enabled
  the plugin, a sync that has not landed yet) install exactly as before.
- No lock or hook schema change: an older copy of the hook reads the same
  locks and keeps installing the duplicate until the copy is updated.
- The account-store `anthropic-skills:self-alert-emails` upload that issue
  #116 also names is not something a repository can remove; the owner deletes
  it on claude.ai.

## Alternatives considered

**Drop the bundle from the consumer locks.** Rejected: Codex Cloud and any
session without the account plugin would lose the two skills.

**Skip only when the plugin's copy has the lock's digest.** Rejected on a
measurement: claude.ai re-serializes `SKILL.md` frontmatter when it syncs
(the synced `finding-unknowns` has its description on one quoted line), so the
digests never match and the check would skip nothing.

## Known limits, accepted

- A skill whose registry fetch fails, or whose lock rows conflict, is still
  reported unavailable or conflicted even when the plugin loads it: those arms
  run before the plugin check. The direction is conservative.
- When the sync lands after the hook, or an older consumer copy of the hook
  runs, the skill loads twice until the next run. `skills-doctor` can call the
  hook's copy `bytes-are-the-locked-ones` in that state.
- When `CLAUDE_PROJECT_DIR` is unset the hook cannot tell which project settings
  apply, so it leaves nothing to a plugin.
- `skills-doctor` matches a plugin's skill by basename within the claimed
  (marketplace, bundle) pairs, not per lock row, and a contest (ADR 0022) won
  by a skill left to a plugin gets no "newer pin" note.
- A plugin present on disk is taken as loaded. If it fails to load, or a
  `permissions.deny` rule blocks its skills, the skill has no copy. No such
  rule exists in the fleet today.
- A manifest entry counts only with `enabled` absent, null or true (every
  entry seen so far is null).
- `skills-doctor`'s SKILL.md does not yet describe the new note or the
  `plugins/synced` channel; its script does.
- `enabledPlugins` is read from the user, project and local settings files, not
  from managed settings or `--settings`, and keyed by the manifest's plugin
  name, which is the bundle name whenever the match applies.
