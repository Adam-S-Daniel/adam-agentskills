---
name: sync-cc-settings-between-wsl-and-windows
description: >-
  Sync Claude Code settings.json between the Windows and WSL homes, merging per property
  and keeping OS-bound keys per file. Use for "sync Claude Code settings", "merge my
  settings.json", or keeping WSL and Windows settings in sync, not for editing one file.
  Windows-only (PowerShell 7+ and the WSL UNC share).
license: MIT
compatibility: Requires Windows with PowerShell 7+ and WSL2 (UNC access to \\wsl.localhost); local execution only
---

# sync-cc-settings-between-wsl-and-windows

Keep a user's Claude Code `settings.json` in sync across their Windows host and a WSL distro, without clobbering per-environment preferences.

## When to use

Trigger on any of:

- "Sync my Claude Code settings between Windows and WSL"
- "Merge settings.json from both sides"
- "My WSL settings.json drifted from the Windows one, reconcile them"
- Mentions of `%USERPROFILE%\.claude\settings.json` AND `~/.claude/settings.json`

Do **not** trigger for single-file edits, or for syncing unrelated config (e.g., `.vscode/settings.json`, `git config`).

## How it works

Given two files:

- Windows: `%USERPROFILE%\.claude\settings.json` (or `$env:CLAUDE_SETTINGS_WINDOWS`)
- WSL:     `~/.claude/settings.json` in the default distro (or `$env:CLAUDE_SETTINGS_WSL`, or auto-detected via `wsl.exe -- bash -lc 'wslpath -w "$HOME/.claude/settings.json"'`)

The script:

1. Loads both files and the **snapshot**, the last result a live run wrote (`.settings-sync-snapshot.json` next to the Windows file, or `-SnapshotPath` / `$env:CLAUDE_SETTINGS_SNAPSHOT`). The snapshot is the three-way base: it lets the script tell which side *changed* a value, instead of guessing from file times.
2. Merges every value three ways (below), building each file's output separately. Object-valued keys (`enabledPlugins`, `extraKnownMarketplaces`, `env`, `permissions`, any other) merge entry by entry.
3. Emits keys in a canonical order (known keys first, the rest alphabetical) and sorts the set-merged arrays ordinally, so a rerun is a no-op.
4. Writes a file **only when its content changes**, after a timestamped backup (`YYYYMMDD-HHMMSS-ET-settings.json.bak`, US/Eastern; the newest `-KeepBackups`, default 20, are kept per side), **preserving that file's newline style, UTF-8 BOM presence and trailing newline**.
5. Refuses to write if either file changed while it ran (Claude Code rewrites the file itself) and reports `skipped-file-changed-during-run`; the next run starts clean.
6. Saves what it wrote as the next snapshot. A `-DryRun` writes nothing, snapshot included.

## Merge rules

For one value, with *base* = the snapshot's value (absent on a first run):

| Windows / WSL vs base | Result |
| --- | --- |
| Equal to each other | Nothing to do. |
| Only one side differs from base (a change, an add or a **delete**) | That side wins on both files. |
| Both differ from base, and from each other | **Conflict** (below). Each file keeps its own value and the snapshot keeps the base, so it stays a conflict until someone resolves it. |
| No snapshot, on one side only | Copied to the other file. |
| No snapshot, on both sides, different | Conflict. |

A **conflict** goes by the mode: interactive prompts `[w]indows` / `[l]inux` / `[n]ewer` / `[s]kip` (skip keeps each file's own value); `-AssumeYes` gives it to the more recently modified file (`showMessageTimestamps` always resolves this way); `-Unattended` skips it and counts it. "Newer" is the file's `LastWriteTimeUtc`, and it decides only conflicts, never a value one side changed alone.

Special cases:

| Key | Rule |
| --- | --- |
| `permissions.allow`, `.ask`, `.deny`, `spinnerVerbs.verbs` | **Set merge**: the union, minus any item either side removed since the base. Sorted ordinally. |
| Any other array | One value (the table above). |
| Per-file paths (below) | **Per-file**: each file keeps its own value; never copied across, never in the snapshot. |

**Per-file paths.** These carry a shell command, an absolute path, an OS-only feature or an OS-specific choice, so a value from one OS is wrong on the other (checked against the [settings reference](https://code.claude.com/docs/en/settings-reference) on 2026-09-22):
`hooks`, `statusLine`, `subagentStatusLine`, `fileSuggestion`, `apiKeyHelper`, `awsAuthRefresh`, `awsCredentialExport`, `gcpAuthRefresh`, `otelHeadersHelper`, `processWrapper`, `autoMemoryDirectory`, `claudeMdExcludes`, `sandbox`, `defaultShell`, `permissions.additionalDirectories`, `env.CCSTATUSLINE_WIDTH`.
`syncClaudeAiSkills` and `syncClaudeAiPlugins` are per-file as a machine-local choice: `false` in user settings moves that home's synced skills or plugins into `.trash/`, so a copied `false` would trash the other home's content.
`enabledPlugins."adam-non-coding-local@synced"` and `"adam-non-coding-local@adam-agentskills"` are per-file because `setup.sh --owner-machine` (ADR 0015) turns them off on Linux and leaves them on for Windows; a sync must not undo either. Every other plugin entry merges per entry, and `setup.sh`'s other writes (the `@synced` and retired-plugin `false` entries, marketplaces) are the same on both sides, so they do not fight the sync.
The list is `$script:PerFilePaths` in the script; add a path there when the reference gains another command- or path-valued setting, or when `setup.sh` starts writing a per-OS value.

### Known limitations

- **No snapshot, no deletions.** On the first live run (or after the snapshot is lost) a key removed from one file comes back from the other. Run `-DryRun` first and review the keys each file would gain.
- **A value changed on both sides stays a conflict** until you resolve it interactively, or edit one side to match.
- Arrays outside the four set-merged lists are one value, so two sides that each added an item conflict.
- The snapshot holds the shared values, `env` included. Keep it as private as the settings files.

## Invocation

The logic lives in `scripts/Sync-ClaudeSettings.ps1`. Run it from Windows PowerShell 7+:

```powershell
# Interactive (prompts for each conflict)
pwsh -File .\scripts\Sync-ClaudeSettings.ps1

# Conflicts go to the more recently modified file, no prompts
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -AssumeYes

# Scheduled task: never prompts, skips conflicts, prints ONE value-free line
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -Unattended -ReportPath "$env:LOCALAPPDATA\ClaudeSettingsSync\last-report.txt"

# Dry run — list what would change, no writes
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -DryRun

# Point at a specific WSL distro, override paths
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 -WslDistro Ubuntu-22.04
pwsh -File .\scripts\Sync-ClaudeSettings.ps1 `
    -WindowsSettingsPath "$env:USERPROFILE\.claude\settings.json" `
    -WslSettingsPath "\\wsl.localhost\Ubuntu\home\<wsl-user>\.claude\settings.json"
```

In the last example, replace `<wsl-user>` with your **WSL** username, which can differ from the Windows one (`$env:USER` is empty in Windows pwsh). `wsl.exe -- whoami` prints it.

### Parameters

| Parameter | Env var | Default |
| --- | --- | --- |
| `-WindowsSettingsPath` | `CLAUDE_SETTINGS_WINDOWS` | `$env:USERPROFILE\.claude\settings.json` |
| `-WslSettingsPath`     | `CLAUDE_SETTINGS_WSL`     | Auto-detected via `wsl.exe -- bash -lc 'wslpath -w "$HOME/.claude/settings.json"'` |
| `-WslDistro`           | `CLAUDE_SETTINGS_WSL_DISTRO` | empty (uses WSL's default distro) |
| `-SnapshotPath`        | `CLAUDE_SETTINGS_SNAPSHOT` | `.settings-sync-snapshot.json` next to the Windows settings.json |
| `-AssumeYes`           | — | off (interactive). A conflict goes to the newer file. |
| `-Unattended`          | — | off. Never prompts; a conflict is skipped; stdout is one line: `<UTC time> sync-claude-settings mode= outcome= snapshot= windows= wsl= conflicts=`. Counts only, no key names or values. |
| `-ReportPath`          | — | none. Writes the key-level report (key names and markers, never values) to this file, replacing it. |
| `-KeepBackups`         | — | 20 |
| `-DryRun`              | — | off. Prints key names with a `same` / `changed` / `added` / `removed` marker per file, never values: `env` values can be credentials. |

No username is hardcoded anywhere in the script; paths come from `$env:USERPROFILE`, `$env:CLAUDE_SETTINGS_*`, or `wsl.exe` auto-detection. Auto-detection runs `bash -lc`, so anything your login dotfiles print on stdout ends up in the detected path; pass `-WslSettingsPath` if that happens.

## Requirements

- Windows host (the script uses `\\wsl.localhost\...` UNC paths via the WSL adapter, and invokes `wsl.exe`).
- **PowerShell 7+** — required for `ConvertFrom-Json -AsHashtable`.
- WSL installed with at least one distro, if `-WslSettingsPath` isn't supplied.
- Both `settings.json` files already exist. The script does not create missing files.

## Tests

`tests/test_sync_claude_settings.py` drives the script through `pwsh` against two fixture files (and their snapshot) in a temp directory (`python3 -m pytest plugins/adam-coding-local/skills/sync-cc-settings-between-wsl-and-windows/tests/ -q`). The module skips when `pwsh` is not on `PATH`. `SYNC_CC_PWSH` names another pwsh (a Windows `pwsh.exe` from inside WSL works), and `SYNC_CC_SETTINGS_SCRIPT` runs the tests against another copy of the script.

## Operational notes

- The script rewrites the JSON via PowerShell's serializer; exotic formatting (custom indent, trailing commas) is **not** preserved. Newlines, BOM, and trailing newline **are** preserved.
- Comments in settings.json are not supported (Claude Code uses plain JSON, not JSONC).
- Backups live next to the originals. Clean them up periodically if you run this often.
- If you need to roll back: the backup filenames are `YYYYMMDD-HHMMSS-ET-settings.json.bak`; overwrite the live file with the backup.

## Troubleshooting

- **"Could not auto-detect WSL settings.json path"** — pass `-WslSettingsPath` explicitly, or set `$env:CLAUDE_SETTINGS_WSL`, or install WSL.
- **"This script requires PowerShell 7+"** — install PowerShell 7 (`winget install Microsoft.PowerShell`), then invoke with `pwsh` rather than `powershell`.
- **UNC access denied to `\\wsl.localhost\...`** — the WSL distro has to be running, or at least startable, for the UNC adapter to serve files. `wsl.exe -l -v` should show the distro.
- **Invoked from a CLI running inside WSL** — pass `-WindowsSettingsPath` and `-WslSettingsPath` explicitly to skip the auto-detect, which shells out to `wsl.exe` and is re-entrant (and unreliable) when the caller is itself already inside WSL. Also pass `-Unattended` (or `-AssumeYes`): the conflict prompt's `while ($true)` loop around `Read-Host` has no EOF branch, so it can loop with no output when stdin is redirected or closed. This is not the same as always hanging — under `pwsh -NonInteractive`, `Read-Host` instead raises a terminating error (the script sets `$ErrorActionPreference = 'Stop'`) and the run dies rather than looping; the silent loop is specifically a redirected/closed-stdin failure mode. Do a `-DryRun -Unattended` pass first and review which keys each file would gain or change before running for real.
