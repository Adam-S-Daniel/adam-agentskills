<#
.SYNOPSIS
    Sync Claude Code settings.json between Windows and WSL with a three-way merge.

.DESCRIPTION
    Compares each file with a snapshot of the last result this script wrote (the
    "base"), so it can tell which side CHANGED a value instead of guessing from
    file times:

        only one side changed a value      : that side's value wins (a change, an add or a delete)
        both sides changed it, differently : a CONFLICT (see below); the base is kept
        neither changed it                 : nothing happens
        no snapshot yet (first run)        : two-way; a value on one side only is copied,
                                             a value on both sides that differs is a conflict

    The merge recurses into objects, so `enabledPlugins`, `extraKnownMarketplaces`,
    `env`, `permissions` and any other object-valued key merge entry by entry. An
    array is one value, except these, merged as sets (an item one side removed
    since the base is removed from both):

        permissions.allow, permissions.ask, permissions.deny, spinnerVerbs.verbs

    Kept per file (each file keeps its own value; never copied across, never in
    the snapshot):

        hooks, statusLine, subagentStatusLine, fileSuggestion, apiKeyHelper,
        awsAuthRefresh, awsCredentialExport, gcpAuthRefresh, otelHeadersHelper,
        processWrapper, autoMemoryDirectory, claudeMdExcludes, sandbox, defaultShell,
        syncClaudeAiSkills, syncClaudeAiPlugins, permissions.additionalDirectories,
        env.CCSTATUSLINE_WIDTH, env.MAKEFLAGS, env.CLAUDE_CODE_USE_POWERSHELL_TOOL,
        and the two enabledPlugins entries for
        adam-non-coding-local (setup.sh turns them off on Linux and leaves them on
        for Windows, per ADR 0015, so the OS decides, not a sync).

    A conflict is resolved by the run mode:

        interactive (default) : prompt [w]indows / [l]inux / [n]ewer / [s]kip
        -AssumeYes            : the more-recently-modified file wins (showMessageTimestamps
                                always resolves this way)
        -Unattended           : skip; each file keeps its own value, the snapshot keeps the
                                base, and the run reports the conflict count. Never prompts.

    The script writes a file only when its content changes, backs up (timestamp
    prefix, Eastern time) only a file it is about to change, and keeps the newest
    -KeepBackups of its own backups per side. It refuses to write if either file
    changed while it ran (Claude Code rewrites settings.json on its own).

    Preserves each file's existing newline style (CRLF or LF), UTF-8 BOM presence
    and trailing newline.

.PARAMETER WindowsSettingsPath
    Path to the Windows settings.json.
    Default: "$env:USERPROFILE\.claude\settings.json"
    Can also be set via env var CLAUDE_SETTINGS_WINDOWS.

.PARAMETER WslSettingsPath
    Path to the WSL settings.json (Windows-accessible UNC form).
    Default: auto-detected via `wsl.exe` (uses the default distro's $HOME).
    Can also be set via env var CLAUDE_SETTINGS_WSL.

.PARAMETER WslDistro
    Optional WSL distro name to target when auto-detecting the WSL path.
    Default: empty (uses WSL's default distro).
    Can also be set via env var CLAUDE_SETTINGS_WSL_DISTRO.

.PARAMETER SnapshotPath
    Where the last-synced snapshot lives. It holds the shared (non-per-file) values,
    including env values, so keep it as private as the settings files.
    Default: ".settings-sync-snapshot.json" next to the Windows settings.json.
    Can also be set via env var CLAUDE_SETTINGS_SNAPSHOT.

.PARAMETER AssumeYes
    Resolve every conflict without asking: the more-recently-modified file wins.

.PARAMETER Unattended
    For a scheduled task. Never prompts, skips conflicts (see above), prints exactly one
    value-free summary line and exits 0 unless something is wrong (then it throws).

.PARAMETER ReportPath
    Also write the key-level report (key names and same/changed/added/removed markers,
    never values) to this file, replacing it.

.PARAMETER KeepBackups
    How many of its own backups per side to keep. Default 20.

.PARAMETER DryRun
    Compute the merge and show the plan, but write nothing: no settings, no backups, no
    snapshot. The plan lists key names and whether each file's value would change; it never
    prints settings values (env values can be credentials).

.EXAMPLE
    .\Sync-ClaudeSettings.ps1

.EXAMPLE
    .\Sync-ClaudeSettings.ps1 -Unattended -DryRun

.EXAMPLE
    .\Sync-ClaudeSettings.ps1 -WslDistro Ubuntu-22.04
#>

[CmdletBinding()]
param(
    [string]$WindowsSettingsPath,
    [string]$WslSettingsPath,
    [string]$WslDistro,
    [string]$SnapshotPath,
    [string]$ReportPath,
    [int]$KeepBackups = 20,
    [switch]$AssumeYes,
    [switch]$Unattended,
    [switch]$DryRun
)

# Resolve parameter defaults from env vars, then conventional locations.
if (-not $WindowsSettingsPath) {
    $WindowsSettingsPath = if ($env:CLAUDE_SETTINGS_WINDOWS) {
        $env:CLAUDE_SETTINGS_WINDOWS
    } else {
        Join-Path $env:USERPROFILE '.claude\settings.json'
    }
}
if (-not $WslDistro -and $env:CLAUDE_SETTINGS_WSL_DISTRO) {
    $WslDistro = $env:CLAUDE_SETTINGS_WSL_DISTRO
}
if (-not $WslSettingsPath -and $env:CLAUDE_SETTINGS_WSL) {
    $WslSettingsPath = $env:CLAUDE_SETTINGS_WSL
}
if (-not $SnapshotPath) {
    $SnapshotPath = if ($env:CLAUDE_SETTINGS_SNAPSHOT) {
        $env:CLAUDE_SETTINGS_SNAPSHOT
    } else {
        Join-Path (Split-Path -Parent $WindowsSettingsPath) '.settings-sync-snapshot.json'
    }
}

function Resolve-WslSettingsPath {
    param([string]$Distro)
    # Invoke via `bash -lc` so $HOME expands inside Linux. Use wslpath -w to
    # return a Windows-accessible path (UNC or drive-letter form).
    # Avoid the automatic variable `$args`; use `$wslArgs` instead.
    $cmd = 'wslpath -w "$HOME/.claude/settings.json"'
    $wslArgs = @()
    if ($Distro) { $wslArgs += @('-d', $Distro) }
    $wslArgs += @('--', 'bash', '-lc', $cmd)
    try {
        $out = & wsl.exe @wslArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $p = ($out | Out-String).Trim()
            if ($p) { return $p }
        }
    } catch { }
    return $null
}

if (-not $WslSettingsPath) {
    $WslSettingsPath = Resolve-WslSettingsPath -Distro $WslDistro
    if (-not $WslSettingsPath) {
        throw "Could not auto-detect WSL settings.json path. Pass -WslSettingsPath explicitly, set `$env:CLAUDE_SETTINGS_WSL, or specify -WslDistro. (Is WSL installed and a distro available?)"
    }
}

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw "This script requires PowerShell 7+ (needs ConvertFrom-Json -AsHashtable). Installed: $($PSVersionTable.PSVersion)"
}

# ---------- Helpers ----------

# Everything the run prints goes through here so -Unattended can stay silent and
# -ReportPath can capture the same lines. Key names and markers only, never values.
$script:Report = [System.Collections.Generic.List[string]]::new()
function Write-Info {
    param([string]$Text = '', [string]$Color)
    $script:Report.Add($Text) | Out-Null
    if ($Unattended) { return }
    if ($Color) { Write-Host $Text -ForegroundColor $Color } else { Write-Host $Text }
}

function Get-EasternTimeStamp {
    # Prefer IANA name (works on PS7 cross-platform); fall back to Windows ID.
    $tz = $null
    foreach ($id in @('America/New_York', 'Eastern Standard Time')) {
        try { $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById($id); break } catch { }
    }
    if (-not $tz) { throw "Could not resolve Eastern time zone." }
    $now = [System.TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $tz)
    return $now.ToString('yyyyMMdd-HHmmss')
}

function Get-FileNewline {
    param([Parameter(Mandatory)][byte[]]$Bytes, [Parameter(Mandatory)][string]$Path)
    for ($i = 0; $i -lt $Bytes.Length - 1; $i++) {
        if ($Bytes[$i] -eq 13 -and $Bytes[$i + 1] -eq 10) { return "`r`n" }
        if ($Bytes[$i] -eq 10) { return "`n" }
        if ($Bytes[$i] -eq 13) { return "`r" }
    }
    # No newline found - fall back based on path heuristic.
    if ($Path -match '^(\\\\wsl|/)') { return "`n" }
    return "`r`n"
}

function Get-BytesEncoding {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        return [System.Text.UTF8Encoding]::new($true)
    }
    return [System.Text.UTF8Encoding]::new($false)
}

function Get-BytesText {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    $start = 0
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) { $start = 3 }
    return [System.Text.Encoding]::UTF8.GetString($Bytes, $start, $Bytes.Length - $start)
}

function ConvertFrom-JsonText {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return [ordered]@{} }
    $obj = $Raw | ConvertFrom-Json -AsHashtable -Depth 100
    if ($null -eq $obj) { return [ordered]@{} }
    if ($obj -isnot [System.Collections.IDictionary]) { throw "Settings JSON is not an object." }
    # Convert to [ordered] to stabilize key order.
    $ordered = [ordered]@{}
    foreach ($k in $obj.Keys) { $ordered[$k] = $obj[$k] }
    return $ordered
}

function Get-JsonFinalText {
    param(
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$Newline,
        [bool]$EndsWithNewline
    )
    $json = ConvertTo-Json -InputObject $Object -Depth 100
    $normalized = $json -replace "`r`n", "`n" -replace "`r", "`n"
    $final = if ($Newline -eq "`n") { $normalized } else { $normalized -replace "`n", $Newline }
    if ($EndsWithNewline -and -not $final.EndsWith($Newline)) { $final += $Newline }
    return $final
}

function Test-EndsWithNewline {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    if ($Bytes.Length -eq 0) { return $false }
    return $Bytes[-1] -eq 10 -or $Bytes[-1] -eq 13
}

function Get-BytesHash {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    return [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::HashData($Bytes))
}

function Backup-SettingsFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Stamp,
        [Parameter(Mandatory)][int]$Keep
    )
    $dir = Split-Path -Parent $Path
    $name = Split-Path -Leaf $Path
    $backupPath = Join-Path $dir "$Stamp-ET-$name.bak"
    Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    # Prune only this script's own backups (fixed name shape), oldest first.
    $mine = @(Get-ChildItem -LiteralPath $dir -File -Filter "*-ET-$name.bak" |
        Where-Object { $_.Name -match '^\d{8}-\d{6}-ET-' } |
        Sort-Object Name -Descending)
    if ($Keep -gt 0 -and $mine.Count -gt $Keep) {
        foreach ($old in $mine[$Keep..($mine.Count - 1)]) { Remove-Item -LiteralPath $old.FullName -Force }
    }
    return $backupPath
}

# Key order never decides equality: dictionary keys are sorted before comparing.
function ConvertTo-CanonicalValue {
    param($Value)
    if ($Value -is [System.Collections.IDictionary]) {
        $out = [ordered]@{}
        foreach ($k in @($Value.Keys | Sort-Object { [string]$_ } -CaseSensitive)) {
            $out[$k] = ConvertTo-CanonicalValue $Value[$k]
        }
        return $out
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        $items = [System.Collections.Generic.List[object]]::new()
        foreach ($x in $Value) { $items.Add((ConvertTo-CanonicalValue $x)) | Out-Null }
        return , $items.ToArray()
    }
    return $Value
}

function ConvertTo-ComparableJson {
    param($Value)
    if ($null -eq $Value) { return 'null' }
    # -InputObject, not the pipeline: piping unrolls an array, so [] would
    # compare as nothing and ["x"] as "x".
    return (ConvertTo-Json -InputObject (ConvertTo-CanonicalValue $Value) -Depth 100 -Compress)
}

function Get-UnionArray {
    param($A, $B)
    $seen = @{}
    $out = [System.Collections.Generic.List[object]]::new()
    foreach ($src in @($A, $B)) {
        if ($null -eq $src) { continue }
        # Ensure we iterate even if a single scalar was passed in.
        $items = if ($src -is [System.Collections.IEnumerable] -and $src -isnot [string]) { $src } else { @($src) }
        foreach ($item in $items) {
            if ($null -eq $item) { continue }
            $key = ConvertTo-ComparableJson $item
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $out.Add($item) | Out-Null
            }
        }
    }
    $arr = $out.ToArray()
    # If every item is a string, sort ordinally so the two files get the same
    # array order regardless of which side contributed which items.
    if ($arr.Count -gt 1) {
        $allStrings = $true
        foreach ($x in $arr) { if ($x -isnot [string]) { $allStrings = $false; break } }
        if ($allStrings) {
            $typed = [string[]]$arr
            [Array]::Sort($typed, [System.StringComparer]::Ordinal)
            return ,@($typed)
        }
    }
    return ,@($arr)
}

# Canonical key orders. Keys listed here are emitted first, in this order;
# any unlisted keys fall through and are emitted alphabetically after.
$script:CanonicalTopOrder = @(
    'permissions',
    'env',
    'model',
    'theme',
    'tui',
    'effortLevel',
    'verbose',
    'autoDreamEnabled',
    'showMessageTimestamps',
    'skipDangerousModePermissionPrompt',
    'remoteControlAtStartup',
    'agentPushNotifEnabled',
    'statusLine',
    'defaultShell',
    'spinnerVerbs',
    'extraKnownMarketplaces'
)
$script:CanonicalPermissionsOrder = @('allow','ask','deny','defaultMode')
$script:CanonicalSpinnerVerbsOrder = @('mode','verbs')

function Sort-DictByCanonicalOrder {
    param(
        [Parameter(Mandatory)]$Dict,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$CanonicalOrder
    )
    $out = [ordered]@{}
    foreach ($k in $CanonicalOrder) {
        if ($Dict.Contains($k)) { $out[$k] = $Dict[$k] }
    }
    $remaining = @($Dict.Keys | Where-Object { $CanonicalOrder -notcontains $_ } | Sort-Object -Culture ([cultureinfo]::InvariantCulture))
    foreach ($k in $remaining) { $out[$k] = $Dict[$k] }
    return $out
}

# ---------- Rule tables ----------

# Paths kept per file: each file keeps its own value and the value never enters
# the snapshot. Each top-level one carries a shell command, an absolute path or
# an OS-only feature (checked against
# https://code.claude.com/docs/en/settings-reference on 2026-09-22), or is a
# machine-local choice: syncClaudeAiSkills / syncClaudeAiPlugins set to false in
# user settings move that home's synced skills / plugins into .trash/, so a
# copied `false` would trash the other home's content.
$script:PerFilePaths = @(
    'hooks', 'statusLine', 'subagentStatusLine', 'fileSuggestion',
    'apiKeyHelper', 'awsAuthRefresh', 'awsCredentialExport', 'gcpAuthRefresh',
    'otelHeadersHelper', 'processWrapper', 'autoMemoryDirectory',
    'claudeMdExcludes', 'sandbox', 'defaultShell',
    'syncClaudeAiSkills', 'syncClaudeAiPlugins',
    # permissions: directory paths are OS-bound.
    'permissions.additionalDirectories',
    # WSL auto-detects terminal width and must NOT be pinned, while Windows
    # (win32) cannot auto-detect and needs a fixed value.
    'env.CCSTATUSLINE_WIDTH',
    # Set on WSL only: MAKEFLAGS=-jN sizes a build to that machine and Windows has no make
    # on PATH; the PowerShell tool is native on Windows and an opt-in on Linux.
    'env.MAKEFLAGS',
    'env.CLAUDE_CODE_USE_POWERSHELL_TOOL',
    # setup.sh (ADR 0015): the Windows/browser/document bundle is ON in Windows
    # and `false` on Linux, under both keys it can arrive by.
    'enabledPlugins.adam-non-coding-local@synced',
    'enabledPlugins.adam-non-coding-local@adam-agentskills'
)
# Arrays merged as sets (a removal since the base propagates).
$script:UnionPaths = @('permissions.allow', 'permissions.ask', 'permissions.deny', 'spinnerVerbs.verbs')
# Conflicts here resolve to the newer file even in interactive mode.
$script:AutoConflictPaths = @('showMessageTimestamps')

$script:Missing = [pscustomobject]@{ Missing = $true }
function Test-Missing { param($V) return [object]::ReferenceEquals($V, $script:Missing) }
function Get-ValueKey {
    param($V)
    if (Test-Missing $V) { return '<missing>' }
    return (ConvertTo-ComparableJson $V)
}

$script:ConflictMode = 'prompt'
if ($Unattended)     { $script:ConflictMode = 'skip' }
elseif ($AssumeYes)  { $script:ConflictMode = 'newer' }
$script:NewerSide  = 'windows'
$script:Conflicts  = [System.Collections.Generic.List[string]]::new()
$script:Decisions  = [System.Collections.Generic.List[string]]::new()

function Resolve-Conflict {
    # Returns 'w', 'l' or 'skip'.
    param([string]$Path, $W, $L)
    $mode = $script:ConflictMode
    if ($script:AutoConflictPaths -contains $Path -and $mode -ne 'skip') { $mode = 'newer' }
    if ($mode -eq 'skip')  { return 'skip' }
    if ($mode -eq 'newer') { if ($script:NewerSide -eq 'windows') { return 'w' } else { return 'l' } }
    Write-Host ""
    Write-Host "Conflict on: $Path" -ForegroundColor Yellow
    Write-Host ("  windows : " + (Get-ValueKey $W))
    Write-Host ("  wsl     : " + (Get-ValueKey $L))
    Write-Host ("  newer   : $($script:NewerSide)")
    while ($true) {
        $choice = Read-Host "  [w]indows / [l]inux / [n]ewer / [s]kip (keep each file's current value)"
        switch -Regex ($choice) {
            '^[wW]' { return 'w' }
            '^[lL]' { return 'l' }
            '^[nN]' { if ($script:NewerSide -eq 'windows') { return 'w' } else { return 'l' } }
            '^[sS]' { return 'skip' }
        }
    }
}

# Three-way merge of one value. W, L and B (windows, wsl, base) may each be
# $script:Missing. Returns W, L and S (the value to record in the snapshot),
# each possibly Missing.
function Merge-Node {
    param([string]$Path, $W, $L, $B)
    $wM = Test-Missing $W
    $lM = Test-Missing $L
    $bM = Test-Missing $B

    if ($script:PerFilePaths -contains $Path) {
        $script:Decisions.Add("[$Path] per-file (each file keeps its own value)") | Out-Null
        return [pscustomobject]@{ W = $W; L = $L; S = $script:Missing }
    }

    # --- arrays merged as sets, with removals since the base propagated ---
    if ($script:UnionPaths -contains $Path -and -not ($wM -and $lM)) {
        $wIn = $null; if (-not $wM) { $wIn = $W }
        $lIn = $null; if (-not $lM) { $lIn = $L }
        $removed = @{}
        if (-not $bM -and $B -is [System.Collections.IEnumerable] -and $B -isnot [string]) {
            $wk = @{}; $lk = @{}
            foreach ($x in @($wIn)) { $wk[(ConvertTo-ComparableJson $x)] = $true }
            foreach ($x in @($lIn)) { $lk[(ConvertTo-ComparableJson $x)] = $true }
            foreach ($x in $B) {
                $k = ConvertTo-ComparableJson $x
                if (-not $wk.ContainsKey($k) -or -not $lk.ContainsKey($k)) { $removed[$k] = $true }
            }
        }
        $u = Get-UnionArray -A $wIn -B $lIn
        $keep = [System.Collections.Generic.List[object]]::new()
        foreach ($x in $u) {
            if (-not $removed.ContainsKey((ConvertTo-ComparableJson $x))) { $keep.Add($x) | Out-Null }
        }
        $arr = [object[]]$keep.ToArray()
        $script:Decisions.Add("[$Path] set merge (count=$($arr.Count), removed=$($removed.Count))") | Out-Null
        return [pscustomobject]@{ W = $arr; L = $arr; S = $arr }
    }

    # --- objects merge entry by entry (one side may be absent) ---
    $wD = $W -is [System.Collections.IDictionary]
    $lD = $L -is [System.Collections.IDictionary]
    if (($wD -or $lD) -and ($wD -or $wM) -and ($lD -or $lM)) {
        $bD = $B -is [System.Collections.IDictionary]
        $names = [System.Collections.Generic.List[string]]::new()
        foreach ($src in @($W, $L, $B)) {
            if ($src -is [System.Collections.IDictionary]) {
                foreach ($k in $src.Keys) { if (-not $names.Contains([string]$k)) { $names.Add([string]$k) } }
            }
        }
        $outW = [ordered]@{}; $outL = [ordered]@{}; $outS = [ordered]@{}
        foreach ($k in $names) {
            $cw = $script:Missing; if ($wD -and $W.Contains($k)) { $cw = $W[$k] }
            $cl = $script:Missing; if ($lD -and $L.Contains($k)) { $cl = $L[$k] }
            $cb = $script:Missing; if ($bD -and $B.Contains($k)) { $cb = $B[$k] }
            $childPath = $k
            if ($Path) { $childPath = "$Path.$k" }
            $r = Merge-Node -Path $childPath -W $cw -L $cl -B $cb
            if (-not (Test-Missing $r.W)) { $outW[$k] = $r.W }
            if (-not (Test-Missing $r.L)) { $outL[$k] = $r.L }
            if (-not (Test-Missing $r.S)) { $outS[$k] = $r.S }
        }
        $order = @()
        if ($Path -eq '' )             { $order = $script:CanonicalTopOrder }
        elseif ($Path -eq 'permissions')  { $order = $script:CanonicalPermissionsOrder }
        elseif ($Path -eq 'spinnerVerbs') { $order = $script:CanonicalSpinnerVerbsOrder }
        $resW = Sort-DictByCanonicalOrder -Dict $outW -CanonicalOrder $order
        $resL = Sort-DictByCanonicalOrder -Dict $outL -CanonicalOrder $order
        $resS = Sort-DictByCanonicalOrder -Dict $outS -CanonicalOrder $order
        # An object emptied by a delete (or never there) is absent, not `{}`.
        if ($resW.Count -eq 0 -and ($wM -or ($lM -and -not $bM))) { $resW = $script:Missing }
        if ($resL.Count -eq 0 -and ($lM -or ($wM -and -not $bM))) { $resL = $script:Missing }
        if ($resS.Count -eq 0) { $resS = $script:Missing }
        return [pscustomobject]@{ W = $resW; L = $resL; S = $resS }
    }

    # --- scalars, arrays and mixed types: one value ---
    $wk = Get-ValueKey $W; $lk = Get-ValueKey $L; $bk = Get-ValueKey $B
    if ($wk -eq $lk) {
        return [pscustomobject]@{ W = $W; L = $L; S = $W }
    }
    if ($wk -eq $bk) {
        $script:Decisions.Add("[$Path] changed in wsl -> windows") | Out-Null
        return [pscustomobject]@{ W = $L; L = $L; S = $L }
    }
    if ($lk -eq $bk) {
        $script:Decisions.Add("[$Path] changed in windows -> wsl") | Out-Null
        return [pscustomobject]@{ W = $W; L = $W; S = $W }
    }
    $pick = Resolve-Conflict -Path $Path -W $W -L $L
    if ($pick -eq 'w') {
        $script:Decisions.Add("[$Path] conflict -> windows") | Out-Null
        return [pscustomobject]@{ W = $W; L = $W; S = $W }
    }
    if ($pick -eq 'l') {
        $script:Decisions.Add("[$Path] conflict -> wsl") | Out-Null
        return [pscustomobject]@{ W = $L; L = $L; S = $L }
    }
    $script:Conflicts.Add($Path) | Out-Null
    $script:Decisions.Add("[$Path] conflict skipped (each file keeps its own value)") | Out-Null
    return [pscustomobject]@{ W = $W; L = $L; S = $B }
}

# Key names and a same/changed/added/removed marker per file, one level deep.
# Never values: env values can be credentials, and a dry run is what an agent
# is told to run first, so its output lands in a transcript.
function Write-PlanForSide {
    param([Parameter(Mandatory)]$Before, [Parameter(Mandatory)]$After, [string]$Indent = '  ')
    $names = [System.Collections.Generic.SortedSet[string]]::new([StringComparer]::Ordinal)
    foreach ($k in $Before.Keys) { [void]$names.Add($k) }
    foreach ($k in $After.Keys)  { [void]$names.Add($k) }
    foreach ($k in $names) {
        $bHas = $Before.Contains($k)
        $aHas = $After.Contains($k)
        $b = $null; if ($bHas) { $b = $Before[$k] }
        $a = $null; if ($aHas) { $a = $After[$k] }
        $mark = 'same'
        if (-not $bHas) { $mark = 'added' }
        elseif (-not $aHas) { $mark = 'removed' }
        elseif ((ConvertTo-ComparableJson $b) -ne (ConvertTo-ComparableJson $a)) { $mark = 'changed' }
        Write-Info "$Indent$k : $mark"
        if ($Indent -eq '  ' -and ($b -is [System.Collections.IDictionary] -or $a -is [System.Collections.IDictionary])) {
            $bd = @{}; if ($b -is [System.Collections.IDictionary]) { $bd = $b }
            $ad = @{}; if ($a -is [System.Collections.IDictionary]) { $ad = $a }
            Write-PlanForSide -Before $bd -After $ad -Indent '      '
        }
    }
}

function Get-ChangedKeyCount {
    param($Before, $After)
    $n = 0
    $names = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($k in $Before.Keys) { [void]$names.Add($k) }
    foreach ($k in $After.Keys)  { [void]$names.Add($k) }
    foreach ($k in $names) {
        $b = $script:Missing; if ($Before.Contains($k)) { $b = $Before[$k] }
        $a = $script:Missing; if ($After.Contains($k))  { $a = $After[$k] }
        if ((Get-ValueKey $b) -ne (Get-ValueKey $a)) { $n++ }
    }
    return $n
}

# ---------- Main ----------

foreach ($p in @($WindowsSettingsPath, $WslSettingsPath)) {
    if (-not (Test-Path -LiteralPath $p)) {
        throw "Settings file not found: $p"
    }
}

$stamp = Get-EasternTimeStamp
Write-Info "=== Claude Code settings sync ===" 'Cyan'
Write-Info "Timestamp (Eastern): $stamp"
Write-Info "Windows file:        $WindowsSettingsPath"
Write-Info "WSL file:            $WslSettingsPath"
Write-Info "Snapshot file:       $SnapshotPath"
if ($DryRun)     { Write-Info "Mode:                DRY RUN (nothing will be written)" 'Magenta' }
if ($AssumeYes)  { Write-Info "Mode:                -AssumeYes (a conflict goes to the newer file)" 'Magenta' }
if ($Unattended) { Write-Info "Mode:                UNATTENDED (a conflict is skipped, never prompted)" 'Magenta' }

# Read each file once; its bytes give the format, the content and the
# fingerprint the pre-write check compares against.
$winBytes = [System.IO.File]::ReadAllBytes($WindowsSettingsPath)
$wslBytes = [System.IO.File]::ReadAllBytes($WslSettingsPath)
$winNewline  = Get-FileNewline -Bytes $winBytes -Path $WindowsSettingsPath
$wslNewline  = Get-FileNewline -Bytes $wslBytes -Path $WslSettingsPath
$winEncoding = Get-BytesEncoding -Bytes $winBytes
$wslEncoding = Get-BytesEncoding -Bytes $wslBytes
$winEndsNL   = Test-EndsWithNewline -Bytes $winBytes
$wslEndsNL   = Test-EndsWithNewline -Bytes $wslBytes
$winHash = Get-BytesHash -Bytes $winBytes
$wslHash = Get-BytesHash -Bytes $wslBytes

function Format-Newline { param($nl) switch ($nl) { "`r`n" {'CRLF'} "`n" {'LF'} "`r" {'CR'} default {'?'} } }
Write-Info ("Windows: newline={0}, bom={1}, trailing-nl={2}" -f (Format-Newline $winNewline), ($winEncoding.GetPreamble().Length -gt 0), $winEndsNL)
Write-Info ("WSL:     newline={0}, bom={1}, trailing-nl={2}" -f (Format-Newline $wslNewline), ($wslEncoding.GetPreamble().Length -gt 0), $wslEndsNL)

# The newer file decides only an -AssumeYes / [n]ewer conflict.
$winMTime = (Get-Item -LiteralPath $WindowsSettingsPath).LastWriteTimeUtc
$wslMTime = (Get-Item -LiteralPath $WslSettingsPath).LastWriteTimeUtc
if ($winMTime -lt $wslMTime) { $script:NewerSide = 'wsl' }
Write-Info "Newer file:          $($script:NewerSide)"

$win = ConvertFrom-JsonText -Raw (Get-BytesText -Bytes $winBytes)
$wsl = ConvertFrom-JsonText -Raw (Get-BytesText -Bytes $wslBytes)

# The base: what the last live run wrote. Absent or unreadable means first run.
$base = $script:Missing
$snapshotState = 'absent'
if (Test-Path -LiteralPath $SnapshotPath) {
    try {
        $base = ConvertFrom-JsonText -Raw ([System.IO.File]::ReadAllText($SnapshotPath))
        $snapshotState = 'present'
    } catch {
        $base = $script:Missing
        $snapshotState = 'unreadable'
        Write-Info "Snapshot could not be read; treating this as a first run."
    }
}
Write-Info "Snapshot:            $snapshotState"

$merged = Merge-Node -Path '' -W $win -L $wsl -B $base
$winOut = [ordered]@{}; if (-not (Test-Missing $merged.W)) { $winOut = $merged.W }
$wslOut = [ordered]@{}; if (-not (Test-Missing $merged.L)) { $wslOut = $merged.L }
$snapOut = [ordered]@{}; if (-not (Test-Missing $merged.S)) { $snapOut = $merged.S }

Write-Info ""
Write-Info "Merge decisions (only what changed or conflicted, plus per-file keys):" 'Cyan'
foreach ($line in $script:Decisions) { Write-Info "  $line" }

$winText = Get-JsonFinalText -Object $winOut -Newline $winNewline -EndsWithNewline $winEndsNL
$wslText = Get-JsonFinalText -Object $wslOut -Newline $wslNewline -EndsWithNewline $wslEndsNL
$winWrite = $winText -ne (Get-BytesText -Bytes $winBytes)
$wslWrite = $wslText -ne (Get-BytesText -Bytes $wslBytes)
$winKeys = Get-ChangedKeyCount -Before $win -After $winOut
$wslKeys = Get-ChangedKeyCount -Before $wsl -After $wslOut

Write-Info ""
Write-Info "Windows would become:"; Write-PlanForSide -Before $win -After $winOut
Write-Info "WSL would become:";     Write-PlanForSide -Before $wsl -After $wslOut

function Write-SummaryAndReport {
    param([string]$Outcome)
    $mode = 'live'; if ($DryRun) { $mode = 'dry-run' }
    $w = 'nochange'; if ($winWrite) { $w = "write($winKeys keys)" }
    $l = 'nochange'; if ($wslWrite) { $l = "write($wslKeys keys)" }
    $line = "{0} sync-claude-settings mode={1} outcome={2} snapshot={3} windows={4} wsl={5} conflicts={6}" -f `
        [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), $mode, $Outcome, $snapshotState, $w, $l, $script:Conflicts.Count
    if ($ReportPath) {
        $dir = Split-Path -Parent $ReportPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        [System.IO.File]::WriteAllText($ReportPath, (($script:Report -join "`n") + "`n" + $line + "`n"), [System.Text.UTF8Encoding]::new($false))
    }
    if ($Unattended) { Write-Host $line } else { Write-Host ""; Write-Host $line -ForegroundColor Green }
    if ($script:Conflicts.Count -gt 0 -and -not $Unattended) {
        Write-Host "Conflicts left alone: $($script:Conflicts -join ', ')" -ForegroundColor Yellow
    }
}

if ($DryRun) {
    Write-Info ""
    Write-Info "DRY RUN - no files written. Values are not shown." 'Magenta'
    Write-SummaryAndReport -Outcome 'planned'
    return
}

# Refuse to write over an edit made while this ran (Claude Code rewrites the
# file itself); the next run starts clean.
$winNow = Get-BytesHash -Bytes ([System.IO.File]::ReadAllBytes($WindowsSettingsPath))
$wslNow = Get-BytesHash -Bytes ([System.IO.File]::ReadAllBytes($WslSettingsPath))
if ($winNow -ne $winHash -or $wslNow -ne $wslHash) {
    Write-SummaryAndReport -Outcome 'skipped-file-changed-during-run'
    return
}

if ($winWrite) {
    Backup-SettingsFile -Path $WindowsSettingsPath -Stamp $stamp -Keep $KeepBackups | Out-Null
    [System.IO.File]::WriteAllText($WindowsSettingsPath, $winText, $winEncoding)
}
if ($wslWrite) {
    Backup-SettingsFile -Path $WslSettingsPath -Stamp $stamp -Keep $KeepBackups | Out-Null
    [System.IO.File]::WriteAllText($WslSettingsPath, $wslText, $wslEncoding)
}

# Record what was written as the next base (LF, no BOM, whatever the files use).
$snapText = Get-JsonFinalText -Object $snapOut -Newline "`n" -EndsWithNewline $true
$snapDir = Split-Path -Parent $SnapshotPath
if ($snapDir -and -not (Test-Path -LiteralPath $snapDir)) { New-Item -ItemType Directory -Path $snapDir -Force | Out-Null }
$oldSnap = ''; if (Test-Path -LiteralPath $SnapshotPath) { $oldSnap = [System.IO.File]::ReadAllText($SnapshotPath) }
if ($snapText -ne $oldSnap) {
    [System.IO.File]::WriteAllText($SnapshotPath, $snapText, [System.Text.UTF8Encoding]::new($false))
}

Write-SummaryAndReport -Outcome 'synced'
