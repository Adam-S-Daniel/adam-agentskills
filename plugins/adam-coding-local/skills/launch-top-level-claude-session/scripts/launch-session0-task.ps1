<# Shared session-0 dispatch. Dot-source from a launcher, or pass a NUL-delimited
   UTF-8 argv file from WSL bash. No task is registered with -PrintArgs. #>
[CmdletBinding()]
param([string] $ArgumentFile, [Alias('PrintArgs')] [switch] $TaskPrintArgs)

function Get-LaunchSessionId {
  param([bool] $DryRun)
  # The override cannot redirect a real launch into Task Scheduler.
  if ($DryRun -and $env:LAUNCH_CLAUDE_FORCE_SESSION0 -eq '1') { return 0 }
  if ($DryRun -and $env:LAUNCH_CLAUDE_FORCE_SESSION0 -eq '0') { return 1 }
  if ($env:OS -eq 'Windows_NT') {
    return [System.Diagnostics.Process]::GetCurrentProcess().SessionId
  }
  return 1 # Linux PowerShell supports hermetic dry-run tests only.
}

function Get-LaunchCurrentUser {
  [System.Security.Principal.WindowsIdentity]::GetCurrent()
}

function Test-LaunchInteractiveSession {
  param([string] $UserSid)
  # WTS includes console and RDP sessions, including logged-on disconnected
  # sessions. Match SIDs, not names, so local/domain accounts cannot collide.
  if (-not ('ClaudeLauncher.Wts' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace ClaudeLauncher {
  public static class Wts {
    [StructLayout(LayoutKind.Sequential)]
    public struct Session { public int Id; public IntPtr Station; public int State; }
    [DllImport("wtsapi32.dll", SetLastError = true)]
    public static extern bool WTSEnumerateSessions(IntPtr server, int reserved, int version, out IntPtr sessions, out int count);
    [DllImport("wtsapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool WTSQuerySessionInformation(IntPtr server, int session, int info, out IntPtr buffer, out int bytes);
    [DllImport("wtsapi32.dll")]
    public static extern void WTSFreeMemory(IntPtr buffer);
    public static string Query(int id, int info) {
      IntPtr buffer; int bytes;
      if (!WTSQuerySessionInformation(IntPtr.Zero, id, info, out buffer, out bytes))
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
      try { return Marshal.PtrToStringUni(buffer); }
      finally { WTSFreeMemory(buffer); }
    }
  }
}
'@
  }
  $sessions = [IntPtr]::Zero
  $count = 0
  if (-not [ClaudeLauncher.Wts]::WTSEnumerateSessions([IntPtr]::Zero, 0, 1, [ref] $sessions, [ref] $count)) {
    throw 'Could not check logged-on Windows sessions.'
  }
  try {
    $size = [Runtime.InteropServices.Marshal]::SizeOf([type] [ClaudeLauncher.Wts+Session])
    for ($i = 0; $i -lt $count; $i++) {
      $entry = [Runtime.InteropServices.Marshal]::PtrToStructure(
        [IntPtr]::Add($sessions, $i * $size), [type] [ClaudeLauncher.Wts+Session])
      if ($entry.Id -le 0 -or $entry.State -notin @(0, 4)) { continue }
      try {
        $name = [ClaudeLauncher.Wts]::Query($entry.Id, 5)
        if (-not $name) { continue }
        $domain = [ClaudeLauncher.Wts]::Query($entry.Id, 7)
        $account = New-Object System.Security.Principal.NTAccount($domain, $name)
        $sid = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
        if ($sid -eq $UserSid) { return $true }
      }
      catch { continue } # An unrelated/inaccessible session must not hide ours.
    }
    return $false
  }
  finally { [ClaudeLauncher.Wts]::WTSFreeMemory($sessions) }
}

function Get-LaunchTaskPath {
  # Empty existing folders resolve too; only an unavailable folder falls back.
  try {
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $null = $scheduler.GetFolder('\ClaudeSessionLauncher')
    return '\ClaudeSessionLauncher\'
  }
  catch { return '\' }
}

function Invoke-LaunchSession0Task {
  param([string] $CommandLine, [switch] $PrintArgs)
  $taskName = 'launch-claude-session-' + [guid]::NewGuid().ToString()
  $taskPath = Get-LaunchTaskPath
  if ($PrintArgs) {
    'TASK ' + (@{
      TaskName = $taskName; TaskPath = $taskPath
      Principal = @{ UserId = '<current-user>'; LogonType = 'Interactive'; RunLevel = 'Limited' }
      Action = @{ Execute = 'wt.exe'; Arguments = $CommandLine }
    } | ConvertTo-Json -Depth 3 -Compress)
    return
  }
  $user = Get-LaunchCurrentUser
  if (-not (Test-LaunchInteractiveSession $user.User.Value)) {
    throw 'No logged-on interactive Windows session exists for the current user. Log on to Windows before launching Claude.'
  }
  $wt = Get-Command wt.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
  $principal = New-ScheduledTaskPrincipal -UserId $user.Name -LogonType Interactive -RunLevel Limited
  $action = New-ScheduledTaskAction -Execute $wt.Source -Argument $CommandLine
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  $registered = $false
  $attempted = $false
  $marker = 'One-off Claude launcher ' + $taskName
  try {
    $attempted = $true
    $null = Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action -Principal $principal -Settings $settings -Description $marker -ErrorAction Stop
    $registered = $true
    $before = (Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop).LastRunTime
    Start-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
    for ($i = 0; $i -lt 50; $i++) {
      $task = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
      $info = Get-ScheduledTaskInfo -TaskName $taskName -TaskPath $taskPath -ErrorAction Stop
      if ($task.State -eq 'Running' -or $info.LastRunTime -ne $before) {
        if ($info.LastTaskResult -notin @(0, 267009)) {
          throw "Interactive Windows Terminal task failed (result $($info.LastTaskResult))."
        }
        return
      }
      Start-Sleep -Milliseconds 100
    }
    throw 'The interactive Windows Terminal task did not start. Check that the current user is logged on.'
  }
  finally {
    if ($attempted -and -not $registered) {
      # If registration created our task before reporting an error, remove it.
      # A collision belonging to someone else must survive the failed register.
      $partial = Get-ScheduledTask -TaskName $taskName -TaskPath $taskPath -ErrorAction SilentlyContinue
      $registered = $partial -and $partial.Description -eq $marker
    }
    if ($registered) {
      Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false -ErrorAction Stop
    }
  }
}

function ConvertTo-TaskCommandLineArg {
  param([string] $Value)
  # CommandLineToArgvW rules; argv arriving from bash is already wt-escaped.
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

if ($ArgumentFile) {
  $content = [IO.File]::ReadAllText($ArgumentFile, (New-Object Text.UTF8Encoding($false, $true)))
  if (-not $content.EndsWith([string] [char] 0)) { throw 'Invalid argv handoff: a trailing NUL is required.' }
  $values = $content.Substring(0, $content.Length - 1).Split([char] 0)
  if ($values.Length -lt 1 -or $values[0] -ne 'wsl.exe') { throw 'Invalid argv handoff: expected a wsl.exe command.' }
  $commandLine = ($values | ForEach-Object { ConvertTo-TaskCommandLineArg $_ }) -join ' '
  Invoke-LaunchSession0Task -CommandLine $commandLine -PrintArgs:$TaskPrintArgs
}
