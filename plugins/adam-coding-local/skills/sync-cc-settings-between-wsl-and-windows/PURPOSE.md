# Purpose — sync-cc-settings-between-wsl-and-windows

Maintenance context only; never loaded at inference.

## Why it exists

A Windows host with WSL runs Claude Code from two homes, each with its own
`~/.claude/settings.json`. Settings changed on one side (permission rules,
`env`, theme, model) drift from the other, and copying one file over the
other clobbers what has to differ per OS: the status line command, the
default shell, a status-line width only Windows needs pinned. The skill
packages a merge that unions the lists, resolves the scalars by newest file
or by prompt, and leaves the per-OS keys alone.

## What shaped it

- **Review old-registry issue 170
  (2026-09).** Measured against fixtures and copies of real files, it found
  that the merge corrupted what it touched: one-element arrays became strings
  and empty arrays `null` (PowerShell unrolls an array returned by an `if`
  expression), `[s]kip` deleted the key from both files, and every key other
  than `statusLine`/`defaultShell` crossed OSes, so WSL `hooks` running Linux
  commands landed in the Windows file. `permissions.ask` lost one side's
  rules, and `-DryRun` printed `env` values into agent transcripts.
  The fix built each side's output separately, turned the per-file list into
  one derived from the settings reference (command-, path- and OS-bound keys,
  plus the machine-local `syncClaudeAiSkills`/`syncClaudeAiPlugins`
  opt-outs), and added the skill's first tests. They drive the real script
  through `pwsh` against fixture files, because the merge engine is pure
  file-in/file-out even though the skill as a whole is machine-bound.
- **Left open, documented instead:** deletions do not propagate (no deletion
  record), a prompt key present on one side only is copied without asking,
  and unlisted object keys such as `enabledPlugins` merge as a unit.

## Eval

None. skills-evals' `DESIGN.md` "Deliberate non-coverage" table lists this
skill as `defer` (machine-bound). The pytest module is the regression gate
for the merge engine.

- **Windows PowerShell 5.1 encoding compatibility (2026-10-05).** Registry-wide
  normalization replaced typographic dashes in the script with ASCII equivalents
  after another BOM-less launcher failed to parse under the ANSI codepage.
  This changes comments and status punctuation, preserving merge behavior.

## Changes

- Prompt-audit fix ([issue #56](https://github.com/Adam-S-Daniel/adam-agentskills/issues/56)):
  the frontmatter description was trimmed to what the skill does and when to
  use it; merge rules and launch quirks it repeated are stated in the body.
- **Drift on a real machine (2026-11).** Nothing ran the sync, and the two
  files had drifted. Reading the script against them showed why a scheduled
  run could not be built on it: a deleted key came back from the other file
  (no record of the last result), the whole-file newer side decided keys such
  as `theme` that it never changed, whole-object keys such as `enabledPlugins`
  were copied as a unit, and a copied `enabledPlugins` would have switched
  `adam-non-coding-local` off in Windows, because `setup.sh` turns it off on
  Linux only (ADR 0015). The merge is now three-way against a snapshot of the
  last result, recurses into objects, keeps those two plugin entries per file,
  writes (and backs up) only a file that changes, refuses to write over an
  edit made mid-run, and has an `-Unattended` mode that prints one value-free
  line, so a scheduled task can run it in `-DryRun` first.
