#Requires -Version 7.0
<#
.SYNOPSIS
    Sign a workload identity federation assertion for the API-credit lane
    (ADR 0018) with the TPM-held key named in config.json's wif.key_name.

.DESCRIPTION
    Builds an RS256 JWT:
      header  {alg: RS256, typ: JWT, kid}   kid = RFC 7638 JWK thumbprint
      claims  {iss, sub, aud, iat: now-10, exp: now+LifetimeSeconds, jti}
    from config.json's wif.issuer, wif.subject and wif.audience, and writes it
    to -OutFile atomically (a temp file in the same directory, then a rename),
    readable by the current user only where that is simple. Prints nothing on
    success; on failure writes one line to stderr and exits 1.

    The key is opened with CngKey.Open in the "Microsoft Platform Crypto
    Provider" (the TPM), so signing works only on Windows. -KeyPem signs with
    a software RSA key from a PEM file instead; the tests use it, on any OS.

    The JWT logic mirrors the owner's working New-WifAssertion,
    ConvertTo-Jwk and ConvertTo-Base64Url, so the lane signs exactly as the
    usage collector does. Each call gets a new jti: the assertion is
    single-use.

.EXAMPLE
    pwsh.exe -NoProfile -NonInteractive -File Sign-CreditJwt.ps1 `
        -ConfigPath C:\Users\<user>\.config\claude-credit\config.json `
        -OutFile C:\Users\<user>\.config\claude-credit\run\<id>\assertion.jwt
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ConfigPath,
    [Parameter(Mandatory)] [string] $OutFile,
    [ValidateRange(60, 3600)] [int] $LifetimeSeconds = 3000,
    [string] $KeyPem
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-Prop($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $p = $Object.PSObject.Properties[$Name]
    if ($null -eq $p) { return $null }
    $p.Value
}

function ConvertTo-Base64Url([byte[]]$Bytes) {
    [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function ConvertTo-Jwk {
    param([System.Security.Cryptography.RSA]$Rsa)
    $p = $Rsa.ExportParameters($false)   # public half only
    $n = ConvertTo-Base64Url $p.Modulus
    $e = ConvertTo-Base64Url $p.Exponent
    # kid = RFC 7638 thumbprint (members in lexical order, no whitespace)
    $canon = '{"e":"' + $e + '","kty":"RSA","n":"' + $n + '"}'
    $kid = ConvertTo-Base64Url ([System.Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($canon)))
    [ordered]@{ kty = 'RSA'; n = $n; e = $e; alg = 'RS256'; use = 'sig'; kid = $kid }
}

function New-WifAssertion {
    param($Wif, [System.Security.Cryptography.RSA]$Rsa, [datetimeoffset]$Now, [int]$LifetimeSeconds = 120)
    $t = $Now.ToUnixTimeSeconds()
    $header = [ordered]@{ alg = 'RS256'; typ = 'JWT'; kid = (ConvertTo-Jwk -Rsa $Rsa).kid }
    $claims = [ordered]@{
        iss = Get-Prop $Wif 'issuer'; sub = Get-Prop $Wif 'subject'; aud = Get-Prop $Wif 'audience'
        iat = $t - 10; exp = $t + $LifetimeSeconds; jti = [guid]::NewGuid().ToString('N')   # jti makes each assertion single-use
    }
    $enc = { param($o) ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes(($o | ConvertTo-Json -Compress))) }
    $signing = (& $enc $header) + '.' + (& $enc $claims)
    $sig = $Rsa.SignData([Text.Encoding]::ASCII.GetBytes($signing), [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $signing + '.' + (ConvertTo-Base64Url $sig)
}

function Open-SigningKey([string]$Name) {
    if (-not $IsWindows) { throw 'The TPM key is Windows-only; pass -KeyPem to sign with a software key.' }
    $provider = [System.Security.Cryptography.CngProvider]::new('Microsoft Platform Crypto Provider')
    if (-not [System.Security.Cryptography.CngKey]::Exists($Name, $provider)) {
        throw "Signing key '$Name' not found in the TPM."
    }
    [System.Security.Cryptography.RSACng]::new([System.Security.Cryptography.CngKey]::Open($Name, $provider))
}

function Protect-File([string]$Path) {
    if ($IsWindows) {
        $acl = [System.Security.AccessControl.FileSecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $me, 'FullControl', 'Allow'))
        [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.FileInfo]::new($Path), $acl)
    }
    else {
        [System.IO.File]::SetUnixFileMode($Path, [System.IO.UnixFileMode]'UserRead, UserWrite')
    }
}

$tmp = $null
try {
    $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding utf8 | ConvertFrom-Json
    $wif = Get-Prop $config 'wif'
    foreach ($key in 'issuer', 'subject', 'audience', 'key_name') {
        if ([string]::IsNullOrEmpty((Get-Prop $wif $key))) { throw "config.json has no wif.$key" }
    }
    if ($KeyPem) {
        $rsa = [System.Security.Cryptography.RSA]::Create()
        $rsa.ImportFromPem((Get-Content -LiteralPath $KeyPem -Raw))
    }
    else {
        $rsa = Open-SigningKey (Get-Prop $wif 'key_name')
    }
    $jwt = New-WifAssertion -Wif $wif -Rsa $rsa -Now ([datetimeoffset]::UtcNow) -LifetimeSeconds $LifetimeSeconds

    $full = [System.IO.Path]::GetFullPath($OutFile)
    $dir = [System.IO.Path]::GetDirectoryName($full)
    [void][System.IO.Directory]::CreateDirectory($dir)
    $tmp = [System.IO.Path]::Combine($dir, '.assertion-' + [guid]::NewGuid().ToString('N') + '.tmp')
    [System.IO.File]::WriteAllText($tmp, '')
    try { Protect-File $tmp } catch { Write-Verbose "ACL not applied: $($_.Exception.Message)" }
    [System.IO.File]::WriteAllText($tmp, $jwt, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::Move($tmp, $full, $true)
    $tmp = $null
    exit 0
}
catch {
    [Console]::Error.WriteLine("Sign-CreditJwt: $($_.Exception.Message)")
    exit 1
}
finally {
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Force }
}
