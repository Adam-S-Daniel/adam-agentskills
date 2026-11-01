# 0023. Deliver the fleet guidance from a plugin hook that fetches it

- **Status:** Accepted
- **Date:** 2026-11-01
- **Deciders:** Adam Daniel

Builds on [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md),
which gates a plugin's runtime changes on the owner's approval, and
[ADR 0017](0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md),
which keeps Claude-only hooks out of `hooks/hooks.json`.

## Context

The fleet's managed guidance is `agents-md/base.md` in the public repo
Adam-S-Daniel/_agent-guidance. It reaches a Claude Code session through one
path: a `fleet-memory.sh` SessionStart hook that each fleet repo carries in its
own `.claude/hooks/`. The hook writes a marked block into `~/.claude/CLAUDE.md`
(user memory), and the same session then loads it.

**A session that starts with no fleet repo attached runs no such hook, so it
gets no guidance.** Three shapes follow from how the hook is found:

- a cloud session started from claude.ai chat, which has no repo at start;
- a project thread whose environment has no user-scope wiring;
- a local session in a folder that is not a fleet repo.

`adam-anything-anywhere` is default-enabled, and claude.ai syncs it into cloud
sessions, so it is a delivery path that reaches those sessions without a repo.

**Bundling a copy of `base.md` and syncing it would cost an owner approval and
a version bump on every change.** A hook change waits on the
`plugin-runtime-review` environment (ADR 0016), and a release needs a version
bump so `plugin update` can fire ([ADR 0009](0009-bump-bundle-versions-on-every-release.md)).
`base.md` had 47 commits in October 2026.

**`fleet-memory.sh` orders writes to the shared block by a stamp**: the
payload's git commit time, or 0 outside git. A block with a higher stamp is
kept. A payload downloaded to a temp file sits in no checkout, so it would
stamp 0 and lose every contest. The companion `_agent-guidance` change adds
`FLEET_GUIDANCE_PAYLOAD_STAMP`: set to decimal epoch seconds together with
`FLEET_GUIDANCE_PAYLOAD`, it is the payload's stamp. The script ignores a
stamp with more than ten digits or more than a day in the future, because an
installed stamp never decreases and one implausible value (a millisecond epoch,
a date written `YYYYMMDDhhmmss`) would keep every later delivery out. A rejected
stamp behaves as an unset one.

## Decision

**The plugin's own SessionStart hook fetches the current `base.md` from GitHub
and installs it through a bundled `fleet-memory.sh`.** The hook code stays
bundled and gated; only the guidance text is fetched. A bundled copy of
`base.md` is the offline fallback.

**Files** under `plugins/adam-anything-anywhere/hooks/`:

- `claude-code.json`, named by `.claude-plugin/plugin.json`'s `hooks` key. One
  SessionStart group, matcher `startup|resume|fork`, one command hook,
  `timeout` 30. `clear` and `compact` are left out because the block was
  installed when the session began. There is **no `hooks/hooks.json`**: both
  Claude Code and Codex load that file, and this hook is Claude-only by design
  (ADR 0017). Codex reads neither the Claude manifest nor this file, and Codex
  already gets the guidance through its own registration in `_agent-guidance`.
  The root Agent Plugins `plugin.json` gets no `hooks` key.
- `fleet-guidance/on-session-start.sh`, the plugin's own hook.
- `fleet-guidance/fleet-memory.sh` and `fleet-guidance/fleet-guidance.md`:
  verbatim copies from `_agent-guidance` (the script from the companion
  change's commit, the payload from `main`'s `agents-md/base.md`). They are
  refreshed only by a reviewed pull request and never edited here.

**The command line** is `/usr/bin/env bash "${CLAUDE_PLUGIN_ROOT}/hooks/fleet-guidance/on-session-start.sh"`.

- The first word is `/usr/bin/env`, not a bare `bash`, so the rule in
  `scripts/check_consistency.py` that rejects a bare `bash` (ADR 0016) accepts
  it. On Windows, Claude Code runs a shell-form hook in Git Bash, where
  `/usr/bin/env` resolves Git Bash's own `bash` and not WSL's launcher.
- Naming the interpreter means the script's execute bit is not needed. The hook
  also runs the bundled `fleet-memory.sh` as `"$BASH" "$HERE/fleet-memory.sh"`
  for the same reason. **Whether claude.ai's sync of a plugin copy drops file
  modes is UNVERIFIED.** It was not measured; this spelling makes the question
  moot at the cost of one extra process.

**What the hook does, in order.** It never reads stdin, never exits non-zero,
and prints one verdict line.

1. A nonempty `FLEET_GUIDANCE_PAYLOAD` in the environment means another
   delivery owns the session (an eval harness, for example): print nothing and
   exit.
2. `FLEET_GUIDANCE_SKIP` set to anything `fleet-memory.sh` treats as on
   (it treats `""`, `0`, `false`, `no` and `off`, in any case, as off): run the
   bundled script with no payload override, which removes any installed block
   and prints the skipped verdict. No network.
3. **The session's own checkout wins.** If `CLAUDE_PROJECT_DIR` is nonempty and
   `$CLAUDE_PROJECT_DIR/.claude/hooks/fleet-guidance.md` is a regular, readable,
   nonempty file, install that file through the bundled script with no network
   and no stamp override, so `fleet-memory.sh` stamps it from the checkout's
   git history. In a fleet repo session the repo's own hook delivers that same
   payload with its git stamp. Fetching `main` instead would race it with a
   different version, and in a branch checkout of `_agent-guidance` would
   replace the branch's guidance with `main`'s. With this step both hooks
   deliver identical bytes and an identical stamp.
4. `FLEET_GUIDANCE_FETCH` set to `0`, `false`, `no` or `off`, a missing `curl`,
   no working Python 3, or no temp directory: fall back.
5. Ask the GitHub API for the commit at the HEAD of `main`
   (`commits?sha=main&per_page=1`). Parse it with Python's `json`: the sha must
   be 40 lowercase hex digits, the committer date must have the exact shape
   `YYYY-MM-DDTHH:MM:SSZ`, and the epoch must be positive. Python on Windows
   ends a line with `\r\n`, so the hook strips carriage returns from the
   parser's output and checks it again in bash: the sha as above and the epoch
   as one to ten digits with no leading zero, which is the range the script
   accepts.
6. Download `agents-md/base.md` **at that commit's sha**, so the bytes are the
   ones the stamp describes and not whatever `main` has moved to.
7. Accept the download only if it is nonempty, at most 1 MiB, and starts with
   the exact bytes `# AGENTS.md`, a blank line, and
   ``> **Managed by [`_agent-guidance`].**``.
8. Run the bundled `fleet-memory.sh` with `FLEET_GUIDANCE_PAYLOAD` set to the
   download and `FLEET_GUIDANCE_PAYLOAD_STAMP` set to the commit epoch. Its
   verdict line passes through unchanged.

**A fallback** runs the bundled script on the bundled `fleet-guidance.md`, then
prints one line: `fleet-guidance-source: the adam-anything-anywhere plugin's
bundled copy (<reason>), which can be older than _agent-guidance main`. The
reason is one of: fetching is off, curl is not installed, no working Python,
cannot create a temporary directory, the GitHub API request failed, the GitHub
API answer was not a commit, the base.md download failed, or the downloaded
base.md failed validation.

**The stamp is the committer date of `main`'s HEAD, not of the last commit that
touched `base.md`.** The HEAD commit's date is never earlier than the merge that
delivered the current `base.md`. A history filtered to the file returns the
commit that authored the change, and for a merged branch that commit predates
the merge, so a delivery stamped from it could lose to an older copy.

**Transport.** Both requests use `curl -fsS --proto '=https' --proto-redir
'=https' --connect-timeout 3 --max-time 5 --max-filesize 1048576`. There is no
`-L`, so no redirect is followed, and no token or auth header of any kind is
sent.

`adam-anything-anywhere` moves from 1.0.1 to 1.1.0 (ADR 0009). The hook and the
three files above are runtime changes, so the pull request waits on the
`plugin-runtime-review` environment, which only the owner approves.

## Consequences

- Every session with the plugin enabled makes one or two HTTPS requests, about
  25 kB in all, at start. The worst case is bounded at about 10 seconds
  (`--max-time 5` on each), inside the hook's 30-second `timeout`, and a
  failure is bounded by the same limit.
- The unauthenticated GitHub API allows 60 requests per hour per IP address. A
  machine without an authenticating proxy, or several machines behind one
  address, can reach that limit; the hook then falls back to the bundled copy
  and says so. The raw download is not on that budget.
- In a fleet repo session both this hook and the repo's own `fleet-memory`
  hook run. That is harmless: this hook installs the checkout's own copy, so
  both deliver identical bytes and an identical stamp, an equal version only
  refreshes an older stamp, and when two writes race the last atomic rename
  wins, which `_agent-guidance`'s
  [ADR 0016](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0016-the-freshest-delivery-wins-the-shared-global-block.md)
  accepts.
- The bundled `fleet-memory.sh` and `fleet-guidance.md` are refreshed only by a
  reviewed pull request, so the fallback can lag `main`. The extra
  `fleet-guidance-source:` line is how a session tells.
- **The text a session loads is whatever `_agent-guidance`'s `main` holds when
  it starts.** It arrives outside this repo's runtime-review gate, so the trust
  rests on `_agent-guidance`'s branch protection and on GitHub. The validation
  checks the header and the size, not the meaning.
- This marketplace is public. Anyone who installs the plugin gets the fleet
  guidance in their user memory, unless they set `FLEET_GUIDANCE_SKIP` or
  disable the plugin. Content outside the managed markers is left untouched.
- Codex is not covered by this file. It keeps its own registration in
  `_agent-guidance`.
- Hosts that block `api.github.com` or `raw.githubusercontent.com` get the
  bundled copy every time.

## Alternatives considered

- **Bundle `base.md` and sync it by opening a pull request for each change.**
  It keeps the guidance text inside the gate, but each change then waits on the
  owner's approval and a version bump, 47 times in October 2026, and a local
  install gets it only after `plugin update`.
- **Sync straight to `main`.** It skips the gate and the version bump, so local
  installs stay stale until someone bumps the version; fleet repos are also
  pull-request-only on their default branch.
- **Wire the cloud environment's setup script to the user scope.** It reaches
  only environments that have that wiring, and only repos present at session
  start. A session started from chat has no repo to read the wiring from.
- **Put the hook in `hooks/hooks.json`.** Codex would load it too, which means a
  `commandWindows`, a harness check and a trust prompt for each definition
  (ADR 0017), to deliver something Codex already gets.
- **Always fetch, even in a fleet repo session.** It is simpler, but it races
  the repo's own hook with a different version and can replace a branch's
  guidance with `main`'s.
- **Fetch `main`'s `base.md` in one request.** It saves a request but carries no
  commit time, so the stamp would be 0 and lose every ordering contest, and the
  bytes could change between the stamp and the download.

## References

- [ADR 0009](0009-bump-bundle-versions-on-every-release.md),
  [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md),
  [ADR 0017](0017-harness-aware-plugin-hooks-because-codex-loads-them-too.md)
- `_agent-guidance`'s
  [ADR 0016, the freshest delivery wins the shared global block](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0016-the-freshest-delivery-wins-the-shared-global-block.md)
- The `fleet-memory.sh` copied here:
  [`_agent-guidance` commit 9181ba0](https://github.com/Adam-S-Daniel/_agent-guidance/commit/9181ba072726e6f25f85aea01aa670778a376cb4)
  (adds `FLEET_GUIDANCE_PAYLOAD_STAMP` and bounds it)
- `plugins/adam-anything-anywhere/hooks/`,
  `scripts/test_fleet_guidance_hook.py`, `docs/multi-repo-delivery.md`
