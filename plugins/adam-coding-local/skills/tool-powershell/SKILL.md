---
name: tool-powershell
description: >-
  Use when a PowerShell tool call fails with a bare "Exit code 1", fails the
  same way twice, or is refused as blocked or protected (for example a
  Remove-Item on a system or VM-storage path). Covers getting the real error
  out of the first failure, the two-strikes stop rule, treating a policy block
  as a decision to report rather than a bug to route around, and previewing
  deletions before running them. For "Access is denied" from powershell.exe
  inside WSL, use windows-elevation-from-wsl instead.
license: MIT
compatibility: >-
  A session that drives Windows PowerShell or pwsh through a PowerShell tool or
  a shell. Not for plain Linux or macOS sessions.
---

# PowerShell tool failures

A PowerShell call that returns only `Exit code 1` has told you nothing yet.
Repeating it, or trying a sibling command, usually repeats the failure. This
skill is the short procedure for the first failure and the second.

## 1. The first failure: get the real error out

PowerShell often fails without printing anything useful: a non-terminating
error keeps going, a native command sets `$LASTEXITCODE` without raising, and
the tool reports only the final exit code. Re-run the same operation once,
wrapped so the failure speaks:

```powershell
$ErrorActionPreference = 'Stop'
try { <the same operation> }
catch { $_.Exception.Message; $_.InvocationInfo.PositionMessage; exit 1 }
```

- Add `Test-Path -LiteralPath <path>` for each path the operation names. A
  wrong or missing path is the most common cause, and it reads as a silent
  exit 1.
- After a native program (`robocopy`, `git`, `msiexec`), print `$LASTEXITCODE`.
  Some programs use nonzero codes for success, so read the code against the
  program's own table.
- Read the message, then fix the cause (the path, the quoting, the parameter),
  not the symptom.

## 2. The second failure: stop and change approach

Two failures of the same shape (same cmdlet, same target, a reworded variant)
are one failure seen twice. Do not make a third attempt. Say what you know,
what the error text was, and what you would try that differs in kind, such as
reading state instead of writing it, or asking.

## 3. A block is a decision, not a bug

When the tool refuses with wording like "blocked", "protected", or "refused by
policy", a safety layer made the call. It is not a transient fault and it is
not an invitation to find another door.

- Do not route around it: no `cmd /c rmdir`, `robocopy /MIR` onto an empty
  folder, `[IO.Directory]::Delete`, `takeown`/`icacls`, or deleting from the
  Linux side with `wsl rm`. Each reaches the same state by sidestepping the
  check, which is the harm the check exists to stop.
- A user saying "use whatever works" does not change this. It authorizes the
  goal, not bypassing a protection they may not know about. Tell them what was
  blocked and why.
- Hand over the exact command with its full Windows path, say that it was
  blocked here, and let the owner run it from their own prompt after looking
  at what is in the folder.

## 4. Preview before you delete

Before any `Remove-Item` that matches by pattern:

1. List what the pattern matches (`Get-ChildItem -LiteralPath <dir> -Filter
   <pattern>`) in its own call, and read it.
2. Run the delete once with `-WhatIf` if the list is not obviously exact.
3. Delete with the narrowest selector that covers the list, never the parent
   folder with `-Recurse` to catch "the rest".

If the listing contains something the user did not mention, leave it and say so.

## Quick reference

| Symptom | First move |
| --- | --- |
| `Exit code 1`, no text | Re-run once under `Stop` with try/catch and `Test-Path` |
| Same error twice | Stop; report; change kind of approach |
| "blocked" / "protected" | Do not work around; hand over the command |
| Pattern delete | List, `-WhatIf`, then the narrowest delete |
| `Access is denied` from WSL | `windows-elevation-from-wsl` |
