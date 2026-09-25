---
name: launch-top-level-claude-session
description: >-
  Launch a new, top-level, interactive Claude Code session in a new Windows
  Terminal tab — native Windows or inside WSL — in a chosen folder, optionally
  remote-controllable and optionally seeded with an initial prompt or a handoff
  file. Use this WHENEVER the user asks to start / open / launch / spawn a new
  (top-level, separate, fresh, detached, background) Claude Code session, hand
  off to a fresh session, or launch Claude in a new terminal, tab or window, on
  Windows or in WSL, even if they don't name every detail. It clears the
  nested-session marker a launch from Claude's own shell tool would inherit (so
  the session is saved and shows in --resume), resolves claude's full path, and
  handles the quirks that silently break naive attempts: ConPTY via Windows
  Terminal, wt.exe's ';' splitting, prompt quoting, session-id vs initial-prompt
  openers, and the workspace-trust gate.
compatibility: >-
  Requires a Windows PC with Windows Terminal (and WSL for the WSL launchers). Works
  whether Claude runs on the Windows host (via PowerShell) or inside the WSL distro
  (via bash + Windows interop). Not applicable on Claude.ai web, the mobile app,
  headless/remote sandboxes, macOS, or plain Linux without Windows underneath.
---

# Launch a new top-level Claude Code session (Windows Terminal tab or WSL)

## Environment requirement (read first)

This skill only applies on a **Windows PC with Windows Terminal** (plus WSL for a WSL
session) — and it works the same whether the Claude you're using right now is running
**on the Windows host** or **inside a WSL distro**. The result is a new interactive,
top-level Claude session in the chosen directory, native or in WSL.

It does **not** apply, and you should not use it, when there's no local Windows to
drive: Claude.ai web, the mobile app, a remote/headless sandbox, a Mac, or a plain
Linux box without Windows underneath. In those environments, stop and tell the user the
skill needs a Windows machine.

**Pick the launcher for where the NEW session should run:**
- **A native Windows session** (a Windows folder like `C:\src\example-repo`), launched
  from Windows PowerShell or pwsh → `scripts\launch-claude-session.ps1`. It opens
  `wt.exe new-tab -d <dir> <pwsh|powershell> ...` and runs claude there.
- **A WSL session, Claude running on Windows** (platform `win32`) →
  `scripts\launch-wsl-claude.ps1`.
- **A WSL session, Claude running inside WSL / Linux** (`/proc/version` mentions
  `microsoft`) → `scripts/launch-wsl-claude.sh`.

The two WSL scripts produce the same window via the same underlying command
(`wt.exe wsl.exe --cd <dir> -- env ... <claude> ...`); they differ only in how the host
shell spawns it.

## Why the environment is set (every launcher does this)

When you launch from your own Bash/PowerShell tool, that shell carries
`CLAUDE_CODE_CHILD_SESSION=1`, and a `claude` started from it inherits it. The
[env-vars reference](https://code.claude.com/docs/en/env-vars) says of
`CLAUDE_CODE_CHILD_SESSION`: *"A nested interactive claude TUI started this way is
automatically excluded from --resume, --continue, up-arrow history, and the claude agents
list… Set CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 to override"*; and of
`CLAUDE_CODE_FORCE_SESSION_PERSISTENCE`: *"Set to 1 to force transcript persistence,
prompt history, and claude agents registration even when this claude was launched from
inside another Claude Code session…"*. Left alone, the "new top-level session" the user
asked for is a nested one whose transcript is not saved.

So each launcher, **in the launched process itself**, removes `CLAUDE_CODE_CHILD_SESSION`
and sets `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` — never relying on the parent's
environment, because Windows Terminal may give a new tab its own environment and WSL
passes only what `WSLENV` names:

- native tab: the tab's shell runs `Remove-Item -Path Env:CLAUDE_CODE_CHILD_SESSION`
  then `$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'` before `& '<full path to claude>'`;
- WSL: `wsl.exe ... -- env -u CLAUDE_CODE_CHILD_SESSION CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 PATH=... <claude>`.

Every launcher also runs claude by its **full path, resolved at launch time**
(`Get-Command claude` on Windows, `command -v claude` in WSL) — a new `wt.exe` tab could
not find bare `claude` (error `0x80070002`). Never hard-code a user's home path.

## What this does

Opens a new terminal window or tab running an **interactive** `claude` session (native or in WSL),
rooted at a directory you choose, and leaves it running for the user to drive (or to
control remotely from the Claude mobile/web app). The session is independent — it
shares no context with the current one.

Two ways to open the session:

- **Default — `--session-id <fresh-uuid>`.** Opens a brand-new session directly. This
  bypasses the "agents view" landing screen (which otherwise does *not* start a chat
  session) and gives the user an empty session to type into.
- **Initial-prompt mode — a positional prompt.** If the user supplies an initial
  prompt (e.g. "stand by for instructions"), seed the session with it instead. Do
  **not** pass `-p`/`--print` — that makes Claude run the prompt once and exit. Without
  it, the prompt becomes the first message and the session stays interactive.

## The fastest path: use the bundled script

**A native Windows session in a new Windows Terminal tab** (PowerShell 7 or Windows
PowerShell 5.1):

```powershell
# Default (session-id opener), in a folder:
& "<skill-dir>\scripts\launch-claude-session.ps1" -Dir C:\src\example-repo

# Hand off a long task: write it to a file, and the session is told to read it.
& "<skill-dir>\scripts\launch-claude-session.ps1" -Dir C:\src\example-repo -PromptFile C:\src\example-repo\handoff.md -RemoteControl

# Dry run: print the wt.exe command line and the tab's script, launch nothing.
& "<skill-dir>\scripts\launch-claude-session.ps1" -Dir C:\src\example-repo -Prompt "Stand by." -PrintArgs
```

Parameters: `-Dir` (Windows path; default the current directory), `-Prompt` or
`-PromptFile` (not both), `-RemoteControl` (bare `--remote-control`) or
`-RemoteControlName <name>`, `-Shell` (default `pwsh`, else `powershell`), `-PrintArgs`.
The tab's script travels as `-EncodedCommand`, so no prompt text passes through
`wt.exe`'s tokenizer.

**A WSL session, from a Windows host** (PowerShell):

```powershell
# Default (session-id opener):
& "<skill-dir>\scripts\launch-wsl-claude.ps1" -Dir /home/<user>/repos/GHA-bench

# With an initial prompt (stays interactive):
& "<skill-dir>\scripts\launch-wsl-claude.ps1" -Dir /home/<user>/repos/GHA-bench -Prompt "Stand by for instructions."
```

Parameters: `-Dir` (required, the **WSL** path), `-Prompt` (optional initial prompt) or
`-PromptFile` (a **WSL** path the session is told to read), `-Distro` (default `Ubuntu`),
`-RemoteControl` (bare `--remote-control`) or `-RemoteControlName <name>`,
`-NoWindowsTerminal` (switch; avoid — see the ConPTY gotcha), `-PrintArgs` (dry run).

**A WSL session, from inside WSL / Linux** (bash) — same behavior, flag-style args:

```bash
# Default (session-id opener):
bash "<skill-dir>/scripts/launch-wsl-claude.sh" --dir /home/<user>/repos/GHA-bench

# With an initial prompt (stays interactive):
bash "<skill-dir>/scripts/launch-wsl-claude.sh" --dir /home/<user>/repos/GHA-bench --prompt "Stand by for instructions."
```

Args: `--dir` (required), `--prompt` or `--prompt-file <path>` (optional, not both),
`--distro` (default `Ubuntu`), `--remote-control [name]` (name optional; or
`--remote-control-name <name>`). `LAUNCH_WSL_CLAUDE_DRY_RUN=1` prints the argv instead.

All three scripts resolve the `claude` binary path, set the session environment (above),
generate the session UUID, pass an initial prompt as a single argument (so the quoting is
always correct), and launch a Windows Terminal window or tab — so you don't have to
reconstruct any of it by hand.

**Prefer `--prompt-file` / `-PromptFile` for anything longer than a sentence.** Write the
handoff to a file; the session's first message becomes *"Read the file <path> and follow
the instructions in it."* A long prompt on the command line is where quoting breaks.

A bare `--remote-control` goes **last** on claude's command line: it takes an optional
name, so placed before the prompt it would swallow the prompt as that name. The scripts
order it for you.

## Verify the session is persisted (do this, then tell the user)

After the launch, confirm the new session is a real top-level one:

1. In the new tab, the session should start normally (no trust dialog left waiting).
2. From a **fresh** terminal (not one of your tool shells, which carry the child marker)
   in the same directory, run `claude --resume`: the new session must be in the list once
   it has had its first message. `claude agents` should also show it while it runs.
3. Tell the user how to check it themselves: "open a new terminal in `<dir>` and run
   `claude --resume` — the session should be listed." If it is missing, the child marker
   leaked: re-check the launched command with `-PrintArgs` / `LAUNCH_WSL_CLAUDE_DRY_RUN=1`.

## Prerequisites (check these — they cause silent failures)

1. **The target directory must be trusted.** If `hasTrustDialogAccepted` is `false`
   for that path in WSL `~/.claude.json`, the session opens but immediately **blocks on
   the workspace-trust dialog** waiting for input. A "launch and ignore" session then
   sits there invisibly and never registers for remote control. Either open it once
   interactively and click **Trust**, or (only with the user's explicit OK) set
   `hasTrustDialogAccepted: true` for that exact path in `~/.claude.json` first. Do not
   silently flip this — it's a security gate and the user's decision.
2. **Claude Code must be installed in the WSL distro** (the script checks via
   `command -v claude` and errors out if missing).

## Remote control

The whole point is usually that the session shows up in the user's **remote Claude
sessions list**. Remote control engages automatically at startup when
`remoteControlAtStartup: true` is set in the WSL `~/.claude/settings.json` (or
`~/.claude.json`). If it is, you don't need any flag — just launch. If it isn't, pass
`-RemoteControlName <name>` so the script adds `--remote-control <name>`.

Note the session only registers once it actually *reaches a live session* — i.e. past
the agents-view landing (handled by the openers above) and past the trust gate
(prerequisite #1). If "nothing shows up," it's almost always one of those two gates.

## Why the launch is done this way (gotchas, learned the hard way)

These are non-obvious and each one silently breaks the launch if ignored — that's why
the script encodes them:

- **On the Windows host, launch from PowerShell, never the Bash/Git-Bash tool.** Git
  Bash rewrites POSIX-looking arguments: `/home/<user>/.local/bin/claude` becomes
  `C:/Program Files/Git/home/<user>/.local/bin/claude`. The session then starts in the
  wrong place (or the binary isn't found). PowerShell `Start-Process` passes the paths
  through untouched. (This mangling is a Git-Bash/MSYS quirk — **real WSL bash does not
  do it**, which is why the WSL-side `.sh` launcher calls `wt.exe` from bash directly.)
- **Use Windows Terminal (`wt.exe`), not bare `wsl.exe`.** `wt` gives the session a
  proper ConPTY. A bare `wsl.exe` spawn gets a malformed TTY (`your 131072x1 screen
  size is bogus`), and in initial-prompt mode Claude treats that as non-interactive and
  **exits immediately**. With `wt`, the prompt session stays open.
- **Quote the entire initial prompt as ONE argument.** If the prompt is split across
  multiple shell arguments, Claude receives only the first word. The script takes
  `-Prompt` as a single string and passes it as a single element, so this is handled —
  but if you ever launch by hand, wrap the whole prompt in quotes:
  `... -- <claude> "Stand by for instructions."` not `... <claude> Stand by for instructions.`
- **Use the full path to the `claude` binary.** A non-login WSL shell may not have
  `~/.local/bin` on `PATH`. The script resolves the absolute path first.
- **Inject the full login PATH via `env` — never via an interactive shell wrapper.**
  Resolving the binary isn't enough: `wsl.exe -- <claude>` runs claude under WSL's reduced
  default PATH, so the *running* agent's own subprocesses can't find tools that only live
  on the login PATH — `pwsh` (`/snap/bin`), `bun` (`~/.bun/bin`), `dotnet`,
  `~/.npm-global/bin`. A long agent job (e.g. a benchmark) then silently breaks with
  `pwsh: command not found`. The scripts capture the login PATH from an **interactive**
  login shell (`bash -lic` — `bun`/`~/.npm-global/bin` are added in `~/.bashrc`, which a
  plain `-lc` skips) and inject it with `... -- env "PATH=<login-path>" <claude> <args>`.
  Do **not** instead wrap claude in `bash -lic 'exec "$@"'`: an interactive bash grabs the
  ConPTY's process group and the claude **TUI exits immediately**. `env` is a transparent
  exec, so claude stays a direct child holding the ConPTY (like the working bare launch),
  just with the right PATH.
- **`--cd <wsl-path>` sets the working directory** for the session; pass a WSL path
  (`/home/...`), not a Windows path.
- **Escape every literal `;` in a prompt or remote-control name as `\;` before handing
  it to `wt.exe`.** `wt` re-parses its own command line and treats an unescaped `;` as
  a subcommand separator (opens a bogus new tab) even when it sits inside an already-
  quoted argument — a prompt like `Work issue #5; it has evidence...` truncates at the
  `;` and the stray tab errors with `0x80070002`. Both scripts do this automatically;
  if you ever build the `wt.exe` command by hand, escape it yourself.

## Manual one-liners (fallback if the script isn't available)

Native Windows tab (PowerShell) — the tab's shell clears the marker itself:

```powershell
$claude = (Get-Command claude -CommandType Application | Select-Object -First 1).Source
$tab = "Remove-Item Env:CLAUDE_CODE_CHILD_SESSION -ErrorAction SilentlyContinue`n" +
       "`$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = '1'`n& '$claude' --session-id $([guid]::NewGuid())"
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($tab))
Start-Process wt.exe -ArgumentList "new-tab -d `"C:\src\example-repo`" pwsh -NoExit -EncodedCommand $enc"
```

WSL, from a Windows host (PowerShell):

```powershell
# Default — new session by id:
$sid = [guid]::NewGuid().ToString()
$claude = (wsl.exe -d Ubuntu -- bash -lc 'command -v claude').Trim()
Start-Process wt.exe -ArgumentList @('wsl.exe','-d','Ubuntu','--cd','/home/<user>/repos/GHA-bench','--','env','-u','CLAUDE_CODE_CHILD_SESSION','CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1',$claude,'--session-id',$sid)

# Initial-prompt — note the prompt is a single quoted argument:
$claude = (wsl.exe -d Ubuntu -- bash -lc 'command -v claude').Trim()
Start-Process wt.exe -ArgumentList @('wsl.exe','-d','Ubuntu','--cd','/home/<user>/repos/GHA-bench','--','env','-u','CLAUDE_CODE_CHILD_SESSION','CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1',$claude,'"Stand by for instructions."')
```

Inside WSL / Linux (bash, via Windows interop):

```bash
# Default — new session by id:
wt.exe wsl.exe -d Ubuntu --cd /home/<user>/repos/GHA-bench -- env -u CLAUDE_CODE_CHILD_SESSION \
  CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 "$(command -v claude)" \
  --session-id "$(cat /proc/sys/kernel/random/uuid)" &

# Initial-prompt — the whole prompt is a single quoted argument:
wt.exe wsl.exe -d Ubuntu --cd /home/<user>/repos/GHA-bench -- env -u CLAUDE_CODE_CHILD_SESSION \
  CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 "$(command -v claude)" \
  "Stand by for instructions." &
```

After launching, the session is the user's to drive — don't try to interact with it
from here. If they asked you to "launch and ignore," confirm it's up and stop.
