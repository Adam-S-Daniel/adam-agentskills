#!/usr/bin/env pwsh
#Requires -Version 7
<#
.SYNOPSIS
Registers (or replaces) the current user's `adam-clone-sync` scheduled task.

.DESCRIPTION
The task runs every 30 minutes and once at logon, as the current user, not
elevated (RunLevel Limited). Its action is

    conhost.exe --headless "<Git Bash>" "<launcher>"

conhost --headless keeps a console window from flashing on every run. Git
Bash is $env:CLAUDE_CODE_GIT_BASH_PATH, else
C:\Program Files\Git\bin\bash.exe; the script refuses to register a task
whose shell does not exist.

The launcher is this folder's launcher.sh, copied to
%LOCALAPPDATA%\adam-agentskills\clone-sync-launcher.sh. It resolves the
installed adam-coding-local plugin at every run, so the task always runs the
released, gated clone-sync.sh (ADR 0017), never this checkout.

setup.sh --owner-machine runs this from Windows Git Bash.

.PARAMETER DryRun
Print the task definition as JSON and change nothing. -WhatIf does the same.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$DryRun
)
$ErrorActionPreference = 'Stop'

$taskName = 'adam-clone-sync'
$everyMinutes = 30

$gitBash = if ($env:CLAUDE_CODE_GIT_BASH_PATH) { $env:CLAUDE_CODE_GIT_BASH_PATH } else { 'C:\Program Files\Git\bin\bash.exe' }
if (-not (Test-Path -LiteralPath $gitBash -PathType Leaf)) {
    throw "Git Bash not found at '$gitBash'. Install Git for Windows or set CLAUDE_CODE_GIT_BASH_PATH."
}
if (-not $env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is not set, so there is nowhere to put the launcher.'
}
$launcherSource = Join-Path $PSScriptRoot 'launcher.sh'
if (-not (Test-Path -LiteralPath $launcherSource -PathType Leaf)) {
    throw "launcher.sh not found next to this script ($launcherSource)."
}
$launcherDir = Join-Path $env:LOCALAPPDATA 'adam-agentskills'
$launcher = Join-Path $launcherDir 'clone-sync-launcher.sh'
$user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { "$env:USERNAME" }
$argument = "--headless `"$gitBash`" `"$launcher`""

$definition = [ordered]@{
    TaskName          = $taskName
    Execute           = 'conhost.exe'
    Argument          = $argument
    Triggers          = @('AtLogOn', "Every${everyMinutes}Minutes")
    RepetitionMinutes = $everyMinutes
    User              = $user
    LogonType         = 'Interactive'
    RunLevel          = 'Limited'
    MultipleInstances = 'IgnoreNew'
    GitBash           = $gitBash
    LauncherSource    = $launcherSource
    Launcher          = $launcher
}

if ($DryRun -or $WhatIfPreference) {
    $definition | ConvertTo-Json
    return
}
if (-not $IsWindows) {
    throw 'Registering the task needs Windows; use -DryRun elsewhere.'
}

New-Item -ItemType Directory -Force -Path $launcherDir | Out-Null
# A byte copy, so the launcher keeps its LF line endings for bash.
Copy-Item -LiteralPath $launcherSource -Destination $launcher -Force

$action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument $argument
$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $user
    New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $everyMinutes)
)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Principal $principal `
    -Settings $settings -Force | Out-Null

"Registered scheduled task '$taskName': every $everyMinutes minutes and at logon, running $launcher"
