# 0017. Write harness-aware plugin hooks, because Codex loads them too

- **Status:** Accepted
- **Date:** 2026-10-08
- **Deciders:** Adam Daniel

Builds on [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md),
which opened plugin folders to hooks and gated runtime changes on the owner's
approval. ADR 0016 considered only Claude Code as a host for those hooks.

## Context

**OpenAI Codex installs this marketplace's plugins too.** Verified against
Codex CLI 0.162 on 2026-10-08, and against the
[Codex hooks](https://learn.chatgpt.com/docs/hooks) and
[Codex plugins](https://learn.chatgpt.com/docs/plugins) documentation:

- It reads the root Agent Plugins `plugin.json`, never
  `.claude-plugin/plugin.json`.
- It loads `hooks/hooks.json` from the plugin root by default — the same file
  Claude Code loads by default.
- It runs a hook only after the user has reviewed and trusted that exact
  definition. A new or changed definition prompts again.
- It exports `PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` (its
  own data directory) to a hook, but not `CLAUDE_PROJECT_DIR`.
- It does no `${...}` text substitution in a command. On POSIX it runs
  `$SHELL -lc "<command>"`, so the shell expands the exported variable. On
  Windows it runs `%COMSPEC% /C "<command>"`, where `${CLAUDE_PLUGIN_ROOT}`
  stays literal; a handler's `commandWindows` field replaces `command` there.
- It drops the Claude-only handler fields `if`, `args` and `shell`, and
  always reads a matcher as a regular expression.
- A hook's stdin carries `session_id`, `cwd`, `hook_event_name`, `model` and a
  `transcript_path` under `~/.codex`. PostToolUse adds `tool_name` (`Bash`
  for its shell tool), `tool_input` and `tool_response`. Stdout it cannot
  parse is an error.

**Claude Code** loads `hooks/hooks.json` plus any file the
`.claude-plugin/plugin.json` `hooks` key names, merged. It substitutes
`${CLAUDE_PLUGIN_ROOT}` inline and also exports it, sets
`CLAUDE_PROJECT_DIR`, and keeps transcripts under `~/.claude/projects/`. On
Windows it runs a shell-form hook in Git Bash. A matcher made only of
letters, digits, `_` and `|` is an exact name list; any other character makes
it an unanchored JavaScript regular expression (read from the 2.1.294
binary). `claude plugin validate --strict` (2.1.294) accepts a
`commandWindows` key on a handler: it passes unknown handler keys through,
while still failing a wrong type or an unknown event.

**Cursor** may import Claude hooks, and sets `CLAUDE_PROJECT_DIR`.

So a hook in `hooks/hooks.json` runs in at least two harnesses, with
different environments, different command handling on Windows, and different
rules for stdout.

Two other facts shaped this change. `setup.sh` linked every skill into
`~/.agents/skills`, `~/.agent/skills` and `~/.cursor/skills`. Codex reads
`~/.agents/skills`, so with the plugins installed from the marketplace every
skill would load there twice. Nothing on the owner's machines reads
`~/.agent/skills`, Cursor is not installed on them, and Gemini was retired as
a target on 2026-08-14. Separately, the owner keeps clones in two homes per
machine (Windows `D:\repos\<owner>\<repo>`, WSL `~/repos/<repo>`), and a
merged pull request leaves every other clone of that repo behind until
someone pulls it by hand.

## Decision

**Layout.** `hooks/hooks.json` holds only harness-agnostic hooks, because both
harnesses load it. Every handler there has:

- a `command` that works in Claude Code and in POSIX Codex: shell form,
  `"${CLAUDE_PLUGIN_ROOT}"/hooks/...`, never a bare `bash`;
- a `commandWindows` for Windows Codex:
  `"C:\Program Files\Git\bin\bash.exe" "%PLUGIN_ROOT%/hooks/..."`;
- a `timeout`, and none of the fields Codex drops.

Hooks that only make sense in Claude Code go in a separate file,
`hooks/claude-code.json`, named by `.claude-plugin/plugin.json`'s `hooks`
key. Claude Code merges that file with `hooks/hooks.json`; Codex never reads
the Claude manifest, so it never sees them. No such file exists yet; the
first will come with the API-credit lane.

**Harness detection.** `hooks/lib/harness.sh` takes a hook's stdin JSON (read
once by the caller) and the environment, and prints one of:

- `claude-code` when `CLAUDE_PROJECT_DIR` is set AND `transcript_path`, with
  backslashes turned into slashes, contains `/.claude/projects/`;
- `codex` when `transcript_path` contains `/.codex/`, or `PLUGIN_ROOT` is set
  without `CLAUDE_PROJECT_DIR`;
- `other` otherwise, including Cursor, an empty or unparseable input, and a
  machine with no working Python (JSON is parsed with Python's `json`, never
  matched as text).

Every hook script uses it and decides per hook what each harness gets.

**Clone sync is the first harness-agnostic hook.** `hooks/clone-sync/`:

- `clone-sync.sh --repo <owner>/<repo> | --all` fast-forwards this machine's
  clones of a GitHub repo. It looks under `~/repos/*` and, when present,
  `/mnt/d/repos/*/*` (WSL) or `/d/repos/*/*` (Git Bash), skips
  `.claude/worktrees`, and touches a matching clone only when it is on the
  remote default branch, has no staged or unstaged tracked changes, has no
  merge, rebase, cherry-pick, revert or bisect in progress, and is not a
  linked worktree. It runs `git fetch origin <branch>` and
  `git merge --ff-only`, and never stashes, resets, checks out, cleans or
  forces. In WSL it uses `git.exe` for a clone under `/mnt/`. A global lock
  keeps runs from overlapping, and each fetch is bounded by `timeout`.
- `on-merge.sh` (PostToolUse, matcher `Bash|.*merge_pull_request`) acts after
  a successful `gh pr merge` or a `*merge_pull_request` tool call.
  `on-session-start.sh` (SessionStart, matcher `startup|resume`) acts for the
  session's directory. Both start the sync detached, print nothing, and exit
  0 in well under a second, in every harness.

**The Codex trust prompt.** The owner sees Codex's review-and-trust prompt
once for each of the two definitions, and again whenever a definition in
`hooks/hooks.json` changes. That prompt is Codex's own owner gate, on top of
this repo's plugin-runtime review.

**A scheduled task covers the time between sessions on Windows.**
`Register-CloneSyncTask.ps1` registers the current user's `adam-clone-sync`
task, not elevated, every 30 minutes and at logon, running
`conhost.exe --headless "<Git Bash>" "<launcher>"`. The launcher is a small,
stable copy of `hooks/clone-sync/launcher.sh` in
`%LOCALAPPDATA%\adam-agentskills\`. At each run it resolves the CURRENTLY
installed `adam-coding-local@adam-agentskills` from
`~/.claude/plugins/installed_plugins.json` and runs that install's
`clone-sync.sh --all`, so the task only ever runs released code that passed
the plugin-runtime gate, and fixes arrive with plugin updates. It exits 0
silently when the plugin is not installed. If WSL is already running, it does
the same inside each running distro for that distro's `~/repos`; it never
boots WSL. `setup.sh --owner-machine` registers the task from Windows Git
Bash, through `pwsh.exe` found by full path.

**`setup.sh` stops linking skills.** All five per-agent homes, the three it
linked until now and the two Gemini ones, are retired. A run removes only
links whose target lies under this checkout's `plugins/` or under a retired
`agentskills` checkout beside it, then removes a home left empty. It never
deletes a real directory or a link it did not make.

**`setup.sh --owner-machine` in WSL** writes `[core] autocrlf = true` to
`~/.gitconfig-windows-clones` and includes it with
`includeIf.gitdir:/mnt/.path`, adding the include only when absent. Windows
git checks files out with CRLF because Git for Windows sets
`core.autocrlf=true` in its system config, which WSL git never reads, so
without this WSL git reports every file in a Windows clone as modified, and
clone-sync would skip the clone as dirty.

**`bin/` is gated.** `scripts/plugin_runtime_gate.py` now also gates a change
under `<plugin-root>/bin/`: Claude Code puts that directory on the session's
PATH, so what is in it runs without a model choosing it, like a hook. The
gate's script-reference extraction reads `commandWindows` as well as
`command`, and every spelling of the root: `${CLAUDE_PLUGIN_ROOT}`,
`$CLAUDE_PLUGIN_ROOT`, `${PLUGIN_ROOT}`, `$PLUGIN_ROOT`, `%PLUGIN_ROOT%` and
`%CLAUDE_PLUGIN_ROOT%`. `hooks/clone-sync/*` is gated twice over: it sits
under `hooks/`, and `hooks.json` names its scripts. `check_consistency.py`'s
bare-`bash` rule covers `commandWindows` too, since cmd.exe resolves a bare
`bash` to WSL's launcher as well.

`adam-coding-local` moves from 2.0.5 to 2.1.0 ([ADR 0009](0009-bump-bundle-versions-on-every-release.md)).

## Consequences

- One hook definition serves Claude Code and Codex on POSIX and Windows. Each
  new harness-agnostic hook pays for that with a `commandWindows` and a
  harness check; a hook that cannot pay goes in `hooks/claude-code.json`.
- The owner answers a Codex trust prompt per changed definition, which makes
  editing `hooks/hooks.json` cost a prompt on every Codex machine.
- Clean clones on the default branch follow merges within seconds of a
  merge in any session, and within 30 minutes on Windows otherwise. A dirty,
  diverged or busy clone is reported in the log and left alone, so nothing a
  person is working on moves.
- The scheduled task depends on Git Bash, Python (to read
  `installed_plugins.json`) and the plugin being installed; without any of
  them it logs and does nothing.
- Anyone who relied on `setup.sh` to put these skills in Codex, Cursor or
  `~/.agent/skills` loses them on the next run; Codex users install the
  plugins from the marketplace instead.
- Harness detection is heuristic. A future harness that copies Claude Code's
  environment and transcript layout would be treated as Claude Code.

## Alternatives considered

- **Keep every hook Claude-only, in a manifest-named file.** Codex would never
  load them, and clone sync would be missing from Codex sessions. Clone sync
  is safe in any harness, so it belongs in the shared file.
- **Detect the harness from one signal.** `CLAUDE_PROJECT_DIR` alone matches
  Cursor; `CLAUDE_PLUGIN_ROOT` is exported by both Claude Code and Codex. The
  transcript location combined with the environment separates all three.
- **Have the scheduled task run a checkout's `clone-sync.sh`.** That runs
  whatever the working tree holds, ungated. Resolving the installed plugin at
  run time runs only released code.
- **Keep linking skills for Codex.** Codex installs the plugins from the
  marketplace now; the links would load every skill twice.

## References

- [Codex hooks](https://learn.chatgpt.com/docs/hooks),
  [Codex plugins](https://learn.chatgpt.com/docs/plugins)
- [ADR 0016](0016-open-plugin-folders-and-gate-runtime-changes-on-owner-approval.md) —
  the gate and the bare-`bash` rule this extends
- [ADR 0009](0009-bump-bundle-versions-on-every-release.md) — the version bump
- `plugins/adam-coding-local/hooks/`, `scripts/test_clone_sync_hooks.py`,
  `scripts/plugin_runtime_gate.py`, `scripts/test_plugin_runtime_gate.py`,
  `setup.sh`, `scripts/test_setup_owner_machine.py`
