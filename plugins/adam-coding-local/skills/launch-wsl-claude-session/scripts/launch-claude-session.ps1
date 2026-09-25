<#
.SYNOPSIS
  Launch a new, top-level, interactive Claude Code session in a native Windows
  Terminal tab.

.DESCRIPTION
  Opens `wt.exe new-tab -d <Dir> <shell> ...` whose shell runs `claude` in <Dir>.
  The tab's shell is handed a small script (via -EncodedCommand, so no prompt
  text ever passes through wt.exe's own ';'-splitting tokenizer) that:

    * removes CLAUDE_CODE_CHILD_SESSION and sets
      CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1 - a claude started from inside
      another Claude Code session's Bash/PowerShell tool inherits the child
      marker, is classified as nested, and is excluded from --resume,
      --continue, up-arrow history and `claude agents`. Both are set in the
      launched process itself, never relied on from this one: Windows Terminal
      may give a new tab its own environment.
    * runs claude by its FULL path, resolved here at launch time - a new wt tab
      could not find bare `claude` (error 0x80070002).

  Works under PowerShell 7 and Windows PowerShell 5.1. See the skill's SKILL.md.

.EXAMPLE
  .\launch-claude-session.ps1 -Dir C:\src\example-repo
.EXAMPLE
  .\launch-claude-session.ps1 -Dir C:\src\example-repo -PromptFile C:\src\example-repo\handoff.md -RemoteControl
#>
[CmdletBinding()]
param(
  [string] $Dir,                    # Windows path; default: the current directory
  [string] $Prompt,                 # optional initial prompt (stays interactive; no -p)
  [string] $PromptFile,             # optional: the session is told to read this file
  [switch] $RemoteControl,          # adds --remote-control (no name)
  [string] $RemoteControlName,      # adds --remote-control <name>
  [string] $Shell,                  # tab shell; default: pwsh.exe, else powershell.exe
  [switch] $PrintArgs               # dry run: print the wt.exe command line and the
                                    # decoded tab script instead of launching anything
)

$ErrorActionPreference = 'Stop'

# Plain stderr plus a chosen exit code: Write-Error under 'Stop' would throw
# instead, and every failure would look the same.
function Stop-Launch {
  param([string] $Message, [int] $Code)
  [Console]::Error.WriteLine($Message)
  exit $Code
}

function Get-FullCommandPath {
  param([string[]] $Names)
  foreach ($n in $Names) {
    $cmd = Get-Command $n -CommandType Application -ErrorAction SilentlyContinue |
      Select-Object -First 1
    if ($cmd) { return $cmd.Source }
  }
  return $null
}

# PowerShell single-quoted literal: only ' needs escaping, by doubling it.
function ConvertTo-PsLiteral {
  param([string] $Value)
  "'" + ($Value -replace "'", "''") + "'"
}

# Win32 argv quoting (CommandLineToArgvW rules), same as launch-wsl-claude.ps1.
function ConvertTo-WindowsCommandLineArg {
  param([string] $Value)
  if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
  $sb = New-Object System.Text.StringBuilder
  [void] $sb.Append('"')
  $i = 0
  while ($i -lt $Value.Length) {
    $bs = 0
    while ($i -lt $Value.Length -and $Value[$i] -eq '\') { $bs++; $i++ }
    if ($i -eq $Value.Length) { [void] $sb.Append('\' * ($bs * 2)) }
    elseif ($Value[$i] -eq '"') { [void] $sb.Append('\' * ($bs * 2 + 1)); [void] $sb.Append('"'); $i++ }
    else { [void] $sb.Append('\' * $bs); [void] $sb.Append($Value[$i]); $i++ }
  }
  [void] $sb.Append('"')
  $sb.ToString()
}

if ($Prompt -and $PromptFile) { Stop-Launch 'Pass -Prompt or -PromptFile, not both.' 2 }
if (-not $Dir) { $Dir = (Get-Location).ProviderPath }
if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { Stop-Launch "Directory not found: $Dir" 2 }
$Dir = (Resolve-Path -LiteralPath $Dir).ProviderPath

# Resolve claude to a full path NOW. A new wt tab may not have the caller's PATH.
$claude = Get-FullCommandPath @('claude')
if (-not $claude -and $env:USERPROFILE) {
  $candidate = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
  if (Test-Path -LiteralPath $candidate) { $claude = $candidate }
}
if (-not $claude) { Stop-Launch 'claude not found on PATH or in %USERPROFILE%\.local\bin - is Claude Code installed?' 1 }

$Shell = if ($Shell) { Get-FullCommandPath @($Shell) } else { Get-FullCommandPath @('pwsh', 'powershell') }
if (-not $Shell) { Stop-Launch 'No shell (pwsh, powershell or -Shell) was found for the new tab.' 1 }

if ($PromptFile) {
  if (-not (Test-Path -LiteralPath $PromptFile -PathType Leaf)) { Stop-Launch "Prompt file not found: $PromptFile" 2 }
  $PromptFile = (Resolve-Path -LiteralPath $PromptFile).ProviderPath
  # A long prompt travels as a path the session reads, not as argv text.
  $Prompt = "Read the file $PromptFile and follow the instructions in it."
}

$claudeArgs = @()
if ($RemoteControlName) { $claudeArgs += @('--remote-control', $RemoteControlName) }
if ($Prompt) {
  # One argument, no -p: the prompt is the first message and the session stays open.
  $claudeArgs += $Prompt
  $mode = 'initial-prompt'
}
else {
  $claudeArgs += @('--session-id', [guid]::NewGuid().ToString())
  $mode = 'session-id'
}
# `--remote-control [name]` takes an OPTIONAL value, so a bare flag goes LAST:
# anywhere earlier it would swallow the prompt as the session's name.
if ($RemoteControl -and -not $RemoteControlName) { $claudeArgs += '--remote-control' }

$tabScript = @(
  'Remove-Item -Path Env:CLAUDE_CODE_CHILD_SESSION -ErrorAction SilentlyContinue'
  '$env:CLAUDE_CODE_FORCE_SESSION_PERSISTENCE = ''1'''
  "Set-Location -LiteralPath $(ConvertTo-PsLiteral $Dir)"
  '& ' + ((@($claude) + $claudeArgs | ForEach-Object { ConvertTo-PsLiteral $_ }) -join ' ')
) -join "`n"
$encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($tabScript))

# wt treats an unescaped ';' as a subcommand separator even inside a quoted
# argument; only -d can carry one here (the script is base64).
$wtArgs = @('new-tab', '-d', ($Dir -replace ';', '\;'), $Shell, '-NoLogo', '-NoExit', '-EncodedCommand', $encoded)
$cmdLine = ($wtArgs | ForEach-Object { ConvertTo-WindowsCommandLineArg $_ }) -join ' '

if ($PrintArgs) {
  Write-Output $cmdLine
  Write-Output $tabScript
  return
}

Start-Process wt.exe -ArgumentList $cmdLine
Write-Host "Launched Claude ($mode) in a new Windows Terminal tab at $Dir"
