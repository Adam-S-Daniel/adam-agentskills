# Purpose — launch-wsl-claude-session

Maintenance context only; never loaded at inference.

## What it is for

Opening a detached, interactive Claude Code session inside WSL from either
side of the boundary (Windows PowerShell or WSL bash), optionally seeded with
an initial prompt, in a real Windows Terminal ConPTY. It is how one session
hands a task to a fresh one on the same machine.

## Why it is `adam-coding-local`

It drives `wt.exe` and `wsl.exe` on the local machine; nothing about it works
in a hosted session. It is a machine-bound coding skill;
`adam-coding-anywhere` is the cloud-safe one.

## The failure shapes it was hardened against

- **A malformed TTY.** A bare `wsl.exe` spawn gets no proper ConPTY and an
  initial-prompt session exits immediately; hence `wt.exe`.
- **A missing PATH.** `wsl.exe` does not run the login shell's rc files, so
  the agent's subprocesses could not find `pwsh`, `bun`, etc.; hence the
  captured login PATH injected with `env`, not a wrapping interactive shell.
- **A prompt split by `;` (2026-09-24).** `wt.exe` re-parses its command line
  and treats an unescaped `;` as a new-tab separator even inside a quoted
  argument. Prompts containing `;` opened the real tab with the prompt
  truncated, plus a stray tab trying to execute the remainder
  (`0x80070002`). Both launchers now escape `;` as `\;`, and the `.ps1` quotes
  each argument, because `Start-Process -ArgumentList <array>` does not.
- **A "new top-level session" that was secretly nested (2026-09-25).** Claude
  launches the session from its own Bash/PowerShell tool, which carries
  `CLAUDE_CODE_CHILD_SESSION=1`; a `claude` started from it inherits the
  marker and is excluded from `--resume`, `--continue`, up-arrow history and
  `claude agents` — its transcript is not saved. Measured: a session opened
  with `wt.exe new-tab … claude …` from the Bash tool was nested. Every
  launcher now removes the marker and sets
  `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1` in the launched process itself
  (Windows Terminal and WSL decide what crosses from the parent, so the
  parent's environment is never relied on). Source: the Claude Code
  env-vars reference, https://code.claude.com/docs/en/env-vars.
- **Bare `claude` not found in a new `wt.exe` tab (2026-09-25).** The tab
  failed with `0x80070002`; the full path was needed. Every launcher resolves
  it at launch time — never a hard-coded home path.
- **No native-Windows path.** The skill only launched WSL sessions, so a
  "start a new session" request on Windows was improvised by hand — which is
  how both failures above happened. `scripts/launch-claude-session.ps1` is
  that path; its tab script travels as `-EncodedCommand` so no prompt text
  meets `wt.exe`'s `;` tokenizer. A bare `--remote-control` is placed last
  because it takes an optional name and would otherwise swallow the prompt.
