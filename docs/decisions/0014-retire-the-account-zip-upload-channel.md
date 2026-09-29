# 0014. Retire the account ZIP-upload channel

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** Adam Daniel

## Context

Experiment E6 ([`docs/experiments/E6-account-plugin-channel.md`](../experiments/E6-account-plugin-channel.md))
and [ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md)
established that a plugin from a repo-synced personal marketplace reaches
every surface the ZIP uploads reach — including local Desktop Cowork, the one
surface the uploader existed for — and, unlike an upload, can be deleted.
[Issue #23](https://github.com/Adam-S-Daniel/adam-agentskills/issues/23) step
1 asked to confirm that on every surface before retiring the uploader.

On 2026-09-27/28 the owner enabled `adam-anything-anywhere`,
`adam-non-coding-local` and `adam-private-anything-anywhere` on claude.ai (a
Claude Code terminal's synced manifest lists exactly those three from these
marketplaces), enabled them in the Desktop app as well, and reported the
plugin skills loading on local Cowork. The skills-evals account-store audit
and the claude.ai Routine that fed
[ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md)'s
drift loop were retired first, as #23 requires: the owner deleted the Routine,
and [skills-evals#211](https://github.com/Adam-S-Daniel/skills-evals/pull/211)
removed the audit.

## Decision

Delete the ZIP-upload channel: `sync-skills`
(`plugins/adam-coding-local/skills/sync-skills/`, including
`account-skills.txt`), `scripts/account_zip_selection.py`, the
`account-skill-zips.yml` and
`record-account-upload.yml`
workflows, `account-state.json`, and the ADR 0006 drift loop they implemented.
`setup.sh --owner-machine` no longer registers the global sync-skills
pre-push hook; it now removes the `hook.sync-skills-reminder` and
`hook.sync-skills-private-reminder` global git-config sections a prior run
left behind, so a machine that pulls this change keeps pushing instead of
failing on a hook command pointing at a script that no longer exists.

## Consequences

- **Updates arrive by hand, not by push.** The account's skills now come from
  this repo as a repo-synced personal marketplace: press "Check for updates"
  on claude.ai and restart the Desktop app after a release (E6 §5).
- **ADR 0002's rule still applies, to a different mechanism.** The account
  store still carries only repo-independent skills useful everywhere it
  touches — that constraint now governs which bundles the owner enables, not
  which ZIPs get uploaded. Nothing here reads skills-evals' published account
  audit any more either.
- **The uploaded ZIP skills are not deleted by this change.** They still sit
  in the account store until the owner removes them by hand in the claude.ai
  UI ([issue #23](https://github.com/Adam-S-Daniel/adam-agentskills/issues/23)
  step 3) — the upload path never had a delete, and nothing here gained one.
- **Every owner machine needs a one-time cleanup.** A machine that ran an
  earlier `setup.sh --owner-machine` still has the two global hook sections
  registered, and the next push there fails until `setup.sh --owner-machine`
  runs again (or the two `git config --global --remove-section` commands run
  by hand) — do this before, or immediately after, pulling this change.

## Alternatives considered

None beyond what E6 and ADR 0013 already weighed — this change only executes
the retirement those already decided, once step 1's confirmation landed.

## References

- [Issue #23](https://github.com/Adam-S-Daniel/adam-agentskills/issues/23) —
  the retirement this ADR closes
- [ADR 0013](0013-start-a-fresh-public-registry-grouped-by-audience-and-runtime.md) —
  the plugin channel this switches to
- [ADR 0006](0006-drive-the-account-store-drift-loop-from-one-published-artifact.md) —
  superseded by this ADR
- [ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) — the
  rule that still governs account-store content
- [E6](../experiments/E6-account-plugin-channel.md) — the plugin-channel
  measurements

## Addendum 2026-09-28: skills-doctor `--account-drift`

`skills-doctor --account-drift` (and `account_drift`, `DriftReport`,
`registry_copy`) is removed too, in [PR #35](https://github.com/Adam-S-Daniel/adam-agentskills/pull/35):
it read skills-evals' `propagation/account/latest.json`, which
[skills-evals#211](https://github.com/Adam-S-Daniel/skills-evals/pull/211) stopped
publishing. `--account-channel` and the shadow checks stay.
[ADR 0002](0002-limit-account-store-to-repo-independent-skills.md) is marked
superseded and [ADR 0010](0010-let-pinned-channels-own-the-terminal.md)
partially superseded by this ADR. Owner machines still need
`bash setup.sh --owner-machine` re-run once (see the last consequence above).
