# 0014. Retire the claude.ai account-store channel

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** Adam Daniel

## Context

The registry delivered skills to claude.ai chat, Cowork, Claude in
Chrome and mobile through the account skill store: the one channel that
reached surfaces with no `SessionStart` hook and no marketplace plugin. The
store has no scoping and no delete
([ADR 0002](0002-limit-account-store-to-repo-independent-skills.md)), so keeping it
faithful to the repo took a stack of machinery around it:

- **`sync-skills`**, a ~3,000-line uploader with a committed
  `account-skills.txt` declaration, a pre-push hook and a `setup.sh` step that
  registered two GLOBAL git hooks (`hook.sync-skills-reminder`,
  `hook.sync-skills-private-reminder`) firing on every push in every repo on
  the machine.
- **`account-state.json`**, a committed record of what was last uploaded, and
  `scripts/account_zip_selection.py`, which decided what needed re-uploading.
- **Two workflows**: `account-skill-zips.yml` built a downloadable ZIP per
  stale skill so an upload could be done from a phone, and
  `record-account-upload.yml` recorded the result. Together with the drift
  loop of [ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)
  they made this repo depend on a published artifact in skills-evals whose
  verdict could change with no commit here.
- **`skills-doctor --account-drift`**, plus a Tier-3 audit in skills-evals.

Every upload was a manual step by the owner, and the store could silently
diverge from the repo in between.

Meanwhile the plugin channel had caught up. A plugin from a repo-synced
marketplace reaches local Cowork, claude.ai chat and iOS (E6, cited in
[ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)),
and terminals and cloud sessions already take pinned skills from the
marketplace and `skills.lock` ([ADR 0010](0010-let-pinned-channels-own-the-terminal.md)).
The plugins ADR 0013 defined line up with the surfaces, so the account store
was no longer the only way in.

## Decision

Retire the claude.ai account-store channel, on the owner's decision of
2026-09-28. Every surface takes skills from the repo marketplace plugins; the
registry no longer uploads anything to the account store. The owner deleted
the uploaded skills from the account and checked they were gone.

## Consequences

- **Removed:** the `sync-skills` skill (in `adam-coding-local`), both account
  workflows, `account-state.json`, `scripts/account_zip_selection.py`, their
  tests, and `skills-doctor --account-drift`. `adam-coding-local` and
  `adam-coding-anywhere` go from 1.1.0 to 1.2.0 (ADR 0009). The skills-evals
  Tier-3 audit and drift workflow are removed in a companion PR.
- **Owner machines must re-run `bash setup.sh --owner-machine`.** It now
  removes the global `hook.sync-skills-reminder` and
  `hook.sync-skills-private-reminder` git config sections. Until it is re-run,
  every push on that machine calls a deleted script and is blocked.
- **Skills reach claude.ai sessions only through plugins** enabled from the
  repo-synced marketplace, and Claude Code sessions through the plugins or the
  `skills.lock` bootstrap. Nothing here can put a skill in the account store,
  so a surface that supports neither cannot get the registry's skills.
- **`syncClaudeAiSkills: false` stays** ([ADR 0010](0010-let-pinned-channels-own-the-terminal.md)):
  Anthropic's own skills still arrive through the account channel, and a
  terminal should keep taking the registry's skills pinned, from the
  marketplace. `skills-doctor` keeps its shadowing checks and
  `--account-channel`.
- **ADRs 0002 and 0006 are superseded** and ADR 0010 is superseded in part;
  their text is left as the record of what was decided then.
- **No more upload step**, no ZIP artifacts, and no drift between a store and
  the repo to detect.

## Alternatives considered

- **Keep the channel for the surfaces that "have nothing else"** (ADR 0010's
  position). Not pursued: the plugin channel now reaches those surfaces, so
  the upload machinery was a cost paid for no remaining reach.
- **Keep the uploader and drop only the drift loop.** Not pursued: the
  uploader was the bulk of the machinery, and its hooks were the part that
  broke pushes.

## References

- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md),
  [ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md),
  [ADR 0010](0010-let-pinned-channels-own-the-terminal.md) — the decisions
  this supersedes in whole or in part
- [ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md) — the plugin layout that replaces it
- [PR #35](https://github.com/Adam-S-Daniel/adam-agentskills/pull/35) — this change
- [skills-evals#213](https://github.com/Adam-S-Daniel/skills-evals/issues/213) — the handoff issue that tracks the three-repo rollout
