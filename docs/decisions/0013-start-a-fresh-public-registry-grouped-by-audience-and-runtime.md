# 0013. Start a fresh public registry and group plugins by audience and runtime

- **Status:** Accepted
- **Date:** 2026-09-24
- **Deciders:** Adam Daniel

## Context

The public registry `Adam-S-Daniel/agentskills` carried personal data in its
git history: example usernames, local paths, a hostname and account-specific
details that had been written into docs, tests and commit messages over time.
On a public repo, history is public, and scrubbing the tip does not remove it.

Experiment E6
([`docs/experiments/E6-account-plugin-channel.md`](../experiments/E6-account-plugin-channel.md))
and [ADR 0012](0012-serve-the-account-skills-as-one-repo-synced-plugin.md)
had meanwhile established how the plugin channel behaves:

- claude.ai accepts a public repo as a personal marketplace, and adding it
  enables none of its plugins; each app (claude.ai, the Desktop app) enables
  plugins separately (E6 §3.5–§3.6).
- A plugin from a repo-synced marketplace reaches local Desktop Cowork,
  claude.ai chat and iOS; an uploaded plugin does not reach local Cowork
  (E6 §3.5–§3.7).
- claude.ai serves a plugin only from a folder with its own `plugin.json`, and
  ignores a curated `skills` list on an entry (E6 §3.7). ADR 0012 therefore
  built the account plugin `adam-personal` out of git symlinks into the
  existing bundles — a view over them, with its own carve-out from ADR 0008
  and a second version bump for every linked-skill edit.
- A Claude Code terminal signed in with the account downloads every enabled
  repo-synced plugin as `<name>@synced` (E6 §3.6), so durable machines had to
  opt out of it per plugin in `setup.sh`.

The three bundles (`adam`, `adam-local`, `fastmail`) were grouped by history
rather than by who uses a skill and where it can run. The owner's actual
grouping is two questions: **audience** (coding work, non-coding work, or
anything) and **runtime** (needs this machine, or runs anywhere). Grouped that
way, the plugins themselves line up with the surfaces, and a symlink view
plugin is no longer needed.

## Decision

Start a fresh public repo, **`Adam-S-Daniel/adam-agentskills`**, with no
shared history, and a private sibling, **`Adam-S-Daniel/adam-agentskills-private`**
(formerly `agentskills-private`). The marketplace name is `adam-agentskills`.

Plugins are named `[adam|adam-private]-[coding|non-coding|anything]-[local|anywhere]`.
Each skill lives in exactly one real directory; no plugin contains a symlink.

| Plugin | Default | Skills | Surfaces |
|---|---|---|---|
| `adam-anything-anywhere` | enabled | finding-unknowns, writing-adrs | claude.ai web, iOS, Chrome, Desktop, and Claude Code |
| `adam-coding-anywhere` | enabled | debug-github-workflows, disarm-inherited-reach, github-actions-repo-settings, review-bash-ci-reliability, skills-doctor, workflow-path-audit | Claude Code terminals and cloud sessions |
| `adam-coding-local` | opt-in | sync-skills, sync-cc-settings-between-wsl-and-windows, launch-wsl-claude-session, migrate-claude-memory, windows-elevation-from-wsl | Claude Code on the owner's machines |
| `adam-non-coding-local` | opt-in | ocr-pdfs, pdf-ocr-audit, rename-pdfs, compare-pdfpairs, fastmail, add-from-address, add-received-from-addresses | the Desktop app's local Cowork |

> Later change (2026-09-25): `launch-wsl-claude-session` was renamed
> `launch-top-level-claude-session` before it shipped; the table above is left
> as decided.

`cms-platform` stays a federated, opt-in marketplace entry sourced from
`Adam-S-Daniel/cms-platform`. The private repo holds
`adam-private-anything-anywhere`, which now carries `adam-writing-style`; that
skill is no longer in the public repo.

Skills were re-homed without content changes, apart from privacy scrubs. All
plugins start at version 1.0.0. The marketplace has **no `renames` map**: it
is a new marketplace, and the old names never existed in it. Invocation is
`/<plugin>:<skill>`, e.g. `/adam-anything-anywhere:finding-unknowns`.

Anyone may run `bash setup.sh`; by default it only links skills into the
per-agent homes (`~/.agents/skills` and friends). The owner-machine steps —
the GLOBAL sync-skills pre-push hook and the `~/.claude/settings.json`
convergence below — run only with `bash setup.sh --owner-machine` (or
`AGENTSKILLS_OWNER_MACHINE=1`), because on anyone else's machine they would
register the owner's private marketplace, enable the owner's plugins and turn
off that user's claude.ai account skills.

With `--owner-machine`, `setup.sh` registers `adam-agentskills` →
`Adam-S-Daniel/adam-agentskills` and `adam-agentskills-private` →
`Adam-S-Daniel/adam-agentskills-private`, and converges user `enabledPlugins` to:

- `true`: `adam-anything-anywhere@adam-agentskills`,
  `adam-coding-anywhere@adam-agentskills`,
  `adam-coding-local@adam-agentskills`,
  `adam-private-anything-anywhere@adam-agentskills-private`;
- `false`: `adam-anything-anywhere@synced` and
  `adam-private-anything-anywhere@synced` — terminals take these from the
  marketplace, pinned, not from the account;
- `false`, too: the retired registry's `adam@agentskills`,
  `adam-local@agentskills`, `fastmail@agentskills`,
  `adam-personal@agentskills` and `adam-private@agentskills-private`, so a
  machine converged under the old names does not load every skill twice
  (`false` for a plugin never installed is inert). The old
  `extraKnownMarketplaces` entries are left in place: nothing in the
  convergence deletes a key, and removing a marketplace is a manual
  `claude plugin marketplace remove`.

`syncClaudeAiSkills: false` stays ([ADR 0010](0010-let-pinned-channels-own-the-terminal.md)).

This repo's `skills.lock` pins the cloud-safe pair, `adam-anything-anywhere`
and `adam-coding-anywhere`, from registry `Adam-S-Daniel/adam-agentskills`.
`check_consistency.py` refuses any symlink under `plugins/` (on disk or
committed as mode 120000), requires every local plugin folder to be closed
(`.claude-plugin/`, `plugin.json`, `skills/` only, manifests metadata-only),
and a test pins the four-plugin layout. ADR 0008's refusal of a symlinked
skill root stays.

A privacy check, `scripts/check_privacy_denylist.py`, runs as CI job
`privacy` against a denylist held in the `PRIVACY_DENYLIST` repository
secret. It scans every tracked file and, with `--base`, every commit message
and added line in the PR range, and prints only `file:line` and a pattern
number. The PR template asks for invented data only.

The old repo is made private and archived once consumers have migrated.

## Consequences

- **Every consumer migrates by hand.** Each consumer repo's `skills.lock` is
  re-pinned to `Adam-S-Daniel/adam-agentskills` and the new plugin names.
  The owner's durable machines re-run `bash setup.sh --owner-machine`,
  which disables the old marketplace's plugins by name; the old marketplace
  entries stay registered until removed by hand.
- **One-way doors.** A plugin name, once enabled anywhere, and a skill
  directory basename (keys for `setup.sh` symlinks and the account store) are
  permanent in practice. With no `renames` map, renaming a plugin means every
  app that enabled it loses it.
- **No view plugins, no double bumps.** A skill edit bumps one plugin's
  version (ADR 0009). The ADR 0012 carve-out from ADR 0008, the account-plugin
  checks, the repo-root `conftest.py` and the link recipe are gone.
- **The `privacy` job fails closed** in CI until the `PRIVACY_DENYLIST` secret
  is set; locally, with no denylist, it only warns.
- **A skill now has one obvious home.** Placing a new skill is two questions
  (audience, runtime), plus whether it is sensitive enough for the private
  registry.

## Alternatives considered

- **Rewrite the old repo's history.** Rejected: 646 commits; about ten
  consumer repos pin its SHAs in their `skills.lock`, so every pin would
  break; and GitHub holds 144 pull-request refs (`refs/pull/*`) that a
  force-push cannot delete, so the old objects would stay fetchable anyway.
- **Keep symlink view plugins** (ADR 0012's `adam-personal`). No longer
  needed: grouping plugins by audience and runtime makes each plugin the
  set a surface should get, without a second plugin pointing into the first.

## References

- [ADR 0012](0012-serve-the-account-skills-as-one-repo-synced-plugin.md) —
  superseded by this ADR
- [E6](../experiments/E6-account-plugin-channel.md) — the plugin-channel
  measurements
- [ADR 0001](0001-consolidate-plugins-into-bundles.md) — the three bundles
  this replaces
- [ADR 0008](0008-refuse-symlinks-in-a-skill-directory.md) — no symlinks in a
  skill directory
- [ADR 0009](0009-bump-bundle-versions-on-every-release.md) — version bumps
- [ADR 0010](0010-let-pinned-channels-own-the-terminal.md) — terminals take
  skills from pinned channels
