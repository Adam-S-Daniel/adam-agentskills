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
stamp 0 and lose every contest. The companion `_agent-guidance` change
([#392](https://github.com/Adam-S-Daniel/_agent-guidance/pull/392)) adds
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
  for the same reason. **Measured:** the claude.ai-synced copy of this plugin
  (version 1.0.1, which predates the hook) holds every file at mode 0644 with
  1980 dates, so a synced copy has no execute bits. That supports `/usr/bin/env bash`, at the
  cost of one extra process.
- **UNVERIFIED:** whether claude.ai's sync carries `hooks/` and the manifest's
  `hooks` key into cloud sessions, and runs them. The synced copy that was
  measured has no hook, so the question stays open until a hooked version has
  synced.

**What the hook does, in order.** It never reads stdin, never exits non-zero,
and prints the verdict line, plus one source line when it falls back.

1. A nonempty `FLEET_GUIDANCE_PAYLOAD` in the environment means another
   delivery owns the session (an eval harness, for example): print nothing and
   exit.
2. `FLEET_GUIDANCE_SKIP` set to anything `fleet-memory.sh` treats as on
   (it treats `""`, `0`, `false`, `no` and `off`, in any case, as off): run the
   bundled script with no payload override, which removes any installed block
   and prints the skipped verdict. No network.
3. `FLEET_GUIDANCE_FETCH` set to `0`, `false`, `no` or `off`, a missing `curl`,
   a missing `git`, or no temp directory: fall back.
4. Ask git for the commit at the HEAD of `main`: a commit-only shallow fetch
   into a bare repository that exists only inside the run's private temp
   directory, then read the commit's sha and committer time.

   ```
   git init -q --bare --template= "$tmp/g"
   git --git-dir="$tmp/g" -c credential.helper= -c core.hooksPath=/dev/null \
     -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=5 \
     fetch -q --no-tags --depth=1 --filter=tree:0 \
     https://github.com/Adam-S-Daniel/_agent-guidance refs/heads/main
   git --git-dir="$tmp/g" log -1 --format='%H %ct' FETCH_HEAD
   ```

   The fetch runs with `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`, and
   under `timeout 15` where `timeout` exists (macOS has none; the hook's own 30
   second limit still caps it there). It uses `--git-dir` and never `-C`, so an
   inherited `GIT_DIR` cannot redirect it. The user's other git config is kept,
   because a cloud session's proxy authentication may live there. Measured, the
   fetch transfers one object, the commit, in under a second. The hook strips
   carriage returns from the `log` output, because a Windows git can end the
   line with `\r\n`, and checks it in bash: the sha is 40 lowercase hex digits
   and the epoch is one to ten digits with no leading zero, which is the range
   the script accepts. A failed fetch or `log`, or output that fails the check,
   falls back with the reason `the commit lookup failed`.

   **Why git and not the REST API.** In claude.ai cloud sessions the egress
   proxy answers `api.github.com` with HTTP 403 ("GitHub access to this
   repository is not enabled for this session") for any repo not attached to the
   session, public ones included. `raw.githubusercontent.com` and git
   smart-HTTP pass.
5. Download `agents-md/base.md` **at that commit's sha**, so the bytes are the
   ones the stamp describes and not whatever `main` has moved to.
6. Accept the download only if it is nonempty, at most 1 MiB, and starts with
   the exact bytes `# AGENTS.md`, a blank line, and
   ``> **Managed by [`_agent-guidance`].**``.
7. Run the bundled `fleet-memory.sh` with `FLEET_GUIDANCE_PAYLOAD` set to the
   download and `FLEET_GUIDANCE_PAYLOAD_STAMP` set to the commit epoch. Its
   verdict line passes through unchanged.

**The hook never reads anything from the session's project directory.** It
consults neither `CLAUDE_PROJECT_DIR` nor any file under it. A repo the user
opens can carry any data file, and this hook writes the user's GLOBAL
`~/.claude/CLAUDE.md`. A repo's own SessionStart hook needs a settings entry
that workspace trust gates; a plain data file would need nothing, and could be a
symlink to a secrets file or carry arbitrary instructions with a far-future git
commit date that pins the stamp.

**A fallback** runs the bundled script on the bundled `fleet-guidance.md`, then
prints one line: `fleet-guidance-source: the adam-anything-anywhere plugin's
bundled copy (<reason>), which can be older than _agent-guidance main`. The
reason is one of: fetching is off, curl is not installed, git is not installed,
cannot create a temporary directory, the commit lookup failed, the base.md
download failed, or the downloaded base.md failed validation.

**The stamp is the committer date of `main`'s HEAD, not of the last commit that
touched `base.md`.** The HEAD commit's date is never earlier than the merge that
delivered the current `base.md`. A history filtered to the file returns the
commit that authored the change, and for a merged branch that commit predates
the merge, so a delivery stamped from it could lose to an older copy.

**Transport.** The download uses `curl -q -fsS --proto '=https' --proto-redir
'=https' --connect-timeout 3 --max-time 5 --max-filesize 1048576`. `-q` is the
first argument so curl ignores `~/.curlrc`: a `write-out` there would print into
the session's context and `location` would turn redirects back on. There is no
`-L`, so no redirect is followed, and no token or auth header of any kind is
sent, by curl or by git.

`adam-anything-anywhere` moves from 1.0.1 to 1.1.0 (ADR 0009). The hook and the
three files above are runtime changes, so the pull request waits on the
`plugin-runtime-review` environment, which only the owner approves.

## Consequences

- Every session with the plugin enabled makes a commit-only git fetch and one
  raw download at start, about 25 kB of `base.md` and one git object. The worst
  case is bounded at about 20 seconds (15 for the fetch where `timeout` exists,
  `--max-time 5` for the download), inside the hook's 30-second `timeout`. Where
  `timeout` does not exist, git's low-speed limits end a stalled transfer after
  5 seconds and the hook's own limit caps the rest. A failure is bounded the
  same way. No GitHub API call is made.
- In a fleet repo session both this hook and the repo's own `fleet-memory`
  hook run, and the higher stamp wins. The repo's hook stamps its checkout's
  copy with that checkout's git time; this hook stamps `main`'s copy with
  `main`'s HEAD time, which is usually the higher. So a branch checkout of
  `_agent-guidance` can load `main`'s guidance once `main` has moved past the
  branch's last commit. **Read the branch's `base.md` directly when that
  matters.** An equal version only refreshes an older stamp, and when two writes
  race the last atomic rename wins, which `_agent-guidance`'s
  [ADR 0016](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/decisions/0016-the-freshest-delivery-wins-the-shared-global-block.md)
  accepts.
- The plugin never reads the project directory, so no repo a user opens can
  steer what reaches the global `~/.claude/CLAUDE.md` through this hook.
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
- Hosts that block `raw.githubusercontent.com`, or git smart-HTTP to
  `github.com`, get the bundled copy every time.

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
- **Prefer the session's own checkout.** If the project directory carries
  `.claude/hooks/fleet-guidance.md`, install that file and skip the network, so
  a fleet repo session gets its branch's guidance. Rejected: a data file any
  repo can carry would reach the global memory with a stamp nothing bounds (the
  script bounds a caller-supplied stamp, not one read from git history, so a
  far-future commit date pins it), and it could be a symlink to a secrets file. It also mixes two stamp
  sources, a checkout's git time and `main`'s HEAD time, which misorders
  deliveries across sessions.
- **The REST commits API for the sha and date.** One request
  (`commits?sha=main&per_page=1`) and a JSON parse. Rejected: the cloud proxy
  answers `api.github.com` with HTTP 403 for any repo not attached to the
  session, so it fails in the sessions this hook exists for. It also needs a
  JSON parser on the host.
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
