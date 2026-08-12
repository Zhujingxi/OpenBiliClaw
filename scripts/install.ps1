#requires -Version 5.1
<#
.SYNOPSIS
    OpenBiliClaw one-command installer for native Windows (PowerShell).

.DESCRIPTION
    Mirrors scripts/install.sh for users on native Windows who do NOT
    want to use Docker or WSL2. Clones the repo, installs Python deps
    via pip, runs the composition readiness check, and
    prints the same status block install.sh emits so an AI coding agent
    (Claude Code, Codex, Cursor, OpenClaw, etc.) can drive the rest of
    the install with no shell-style ambiguity.

.PARAMETER InstallDir
    Target directory. Default: $env:USERPROFILE\OpenBiliClaw

.PARAMETER ReuseFrom
    Path to an existing OpenBiliClaw checkout whose API keys + Bilibili
    cookie should be reused. When unset, the script auto-detects under
    common locations. Pass an empty string to disable auto-detect.

.PARAMETER Branch
    Git branch to clone. Default: main

.PARAMETER Port
    Backend API port. Default: 8420

.PARAMETER ApiHost
    Backend bind address. Default: 0.0.0.0

.PARAMETER Mode
    Bootstrap mode. Default: "local" (no Docker on Windows by design;
    pass --mode docker only if Docker Desktop is configured).

.PARAMETER SkipStart
    Retained for invocation compatibility. The installer validates the graph
    but never starts a background backend.

.EXAMPLE
    # PowerShell 5.1 (Win10/Win11 default) needs the TLS 1.2 prefix —
    # GitHub no longer accepts TLS 1.0/1.1, which is what PS 5.1 picks.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex

.EXAMPLE
    # PowerShell 7+ — TLS 1.2 is already the default, no prefix needed.
    iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex

.EXAMPLE
    $env:INSTALL_DIR = "$env:USERPROFILE\obc"
    iwr <url> -UseBasicParsing | iex
#>

[CmdletBinding()]
param(
    [string] $InstallDir = $env:INSTALL_DIR,
    [string] $ReuseFrom  = $env:REUSE_FROM,
    [string] $Branch     = $env:OPENBILICLAW_BRANCH,
    [int]    $Port       = 0,
    [string] $ApiHost    = $env:HOST,
    [string] $Mode       = $env:MODE,
    [switch] $SkipStart
)

$ErrorActionPreference = 'Stop'

# Force TLS 1.2 for any HTTP calls. PowerShell 5.1 (the default on
# Windows 10/11 without manual upgrade) defaults to TLS 1.0/1.1 + SSL3,
# but GitHub.com / pypi.org / raw.githubusercontent.com require TLS 1.2+.
# Without this, Invoke-WebRequest / git-https handshakes fail with
# misleading messages like "underlying connection was closed".
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Older .NET (pre-4.5) won't have Tls12 — nothing we can do, but most
    # PS 5.1 installs ship .NET 4.6+ so this almost never triggers.
}

function Add-LocalNoProxy {
    $parts = @()
    foreach ($name in @('NO_PROXY', 'no_proxy')) {
        $raw = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($raw) {
            foreach ($part in ($raw -split ',')) {
                $value = $part.Trim()
                if ($value -and -not $parts.Contains($value)) {
                    $parts += $value
                }
            }
        }
    }
    foreach ($hostName in @('localhost', '127.0.0.1', '::1')) {
        if (-not $parts.Contains($hostName)) {
            $parts += $hostName
        }
    }
    $value = ($parts -join ',')
    $env:NO_PROXY = $value
    $env:no_proxy = $value
}

Add-LocalNoProxy

# -----------------------------------------------------------------------------
# Defaults

$DefaultRepoUrl    = 'https://github.com/whiteguo233/OpenBiliClaw.git'
$DefaultBranch     = 'main'
$DefaultInstallDir = Join-Path $env:USERPROFILE 'OpenBiliClaw'
$CandidateSources  = @(
    Join-Path $env:USERPROFILE 'workspace\OpenBiliClaw'
    Join-Path $env:USERPROFILE 'OpenBiliClaw'
    Join-Path $env:USERPROFILE 'projects\OpenBiliClaw'
    Join-Path $env:USERPROFILE 'code\OpenBiliClaw'
)

if (-not $InstallDir) { $InstallDir = $DefaultInstallDir }
if (-not $Branch)     { $Branch     = if ($env:OPENBILICLAW_BRANCH) { $env:OPENBILICLAW_BRANCH } else { $DefaultBranch } }
if ($Port -le 0)      { $Port       = if ($env:PORT) { [int]$env:PORT } else { 8420 } }
if (-not $ApiHost)    { $ApiHost    = '0.0.0.0' }
if (-not $Mode)       { $Mode       = 'local' }   # native Windows defaults to local, not docker
$RepoUrl = if ($env:OPENBILICLAW_REPO_URL) { $env:OPENBILICLAW_REPO_URL } else { $DefaultRepoUrl }

# Distinguish "user explicitly set ReuseFrom='' to disable" vs "not passed".
$ReuseExplicit = $PSBoundParameters.ContainsKey('ReuseFrom') -or ($null -ne $env:REUSE_FROM)

# -----------------------------------------------------------------------------
# Logging helpers

function Write-LogLine([string]$Color, [string]$Message) {
    Write-Host -NoNewline -ForegroundColor $Color '[openbiliclaw] '
    Write-Host $Message
}
function Log-Info  { param($m) Write-LogLine 'Cyan'   $m }
function Log-OK    { param($m) Write-LogLine 'Green'  $m }
function Log-Warn  { param($m) Write-LogLine 'Yellow' $m }
function Log-Err   { param($m) Write-LogLine 'Red'    $m }

# -----------------------------------------------------------------------------
# Prerequisites

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Log-Err "Missing required command: $Name"
        if ($Hint) { Log-Err "  $Hint" }
        exit 1
    }
}

function Get-PythonExe {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            try {
                if ($candidate -eq 'py') {
                    $version = & $cmd -3.11 -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
                    if (-not $version) {
                        $version = & $cmd -3 -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
                    }
                } else {
                    $version = & $cmd -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
                }
            } catch {
                continue
            }
            if (-not $version) { continue }
            # Python prints 'major minor' (whitespace-separated) — using
            # print(major, minor) instead of an f-string avoids a PS 5.1
            # quoting bug where inner double-quotes / { } get stripped
            # before reaching python.exe, which would yield SyntaxError
            # on the Python side and falsely trigger "Python 3.11+ is
            # required."
            $parts = $version.Trim() -split '\s+'
            if ($parts.Count -lt 2) { continue }
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if (($major -gt 3) -or ($major -eq 3 -and $minor -ge 11)) {
                return $cmd.Path
            }
        }
    }
    Log-Err 'Python 3.11+ is required.'
    Log-Err '  Install from https://www.python.org/downloads/  (check "Add python.exe to PATH" during install)'
    exit 1
}

# -----------------------------------------------------------------------------
# Source discovery (auto-reuse existing install)

function Detect-ReuseSource {
    if ($ReuseExplicit) {
        if ($ReuseFrom) { Log-Info "REUSE_FROM explicitly set to $ReuseFrom" }
        else            { Log-Info 'REUSE_FROM explicitly set to empty — skipping auto-detection.' }
        return
    }
    foreach ($cand in $CandidateSources) {
        if ($cand -ieq $InstallDir) { continue }
        if (-not (Test-Path $cand -PathType Container)) { continue }
        $hasConfig = Test-Path (Join-Path $cand 'config.toml')
        $hasCookie = Test-Path (Join-Path $cand 'data\bilibili_cookie.json')
        if ($hasConfig -or $hasCookie) {
            $script:ReuseFrom = $cand
            Log-Info "Found existing OpenBiliClaw at $ReuseFrom — will reuse API keys and cookie."
            return
        }
    }
}

# -----------------------------------------------------------------------------
# Checkout: clone or update existing

function Test-UserDataOnlyRoot([string]$Path) {
    if (-not (Test-Path $Path -PathType Container)) { return $false }
    $allowed = @('config.toml', 'config.local.toml', 'data', 'logs', 'openbiliclaw.lock')
    $entries = @(Get-ChildItem -LiteralPath $Path -Force | Where-Object { $_.Name -ne '.DS_Store' })
    if ($entries.Count -eq 0) { return $false }
    foreach ($entry in $entries) {
        if (-not $allowed.Contains($entry.Name)) { return $false }
    }
    return $true
}

function Clone-IntoUserDataRoot {
    $parent = Split-Path $InstallDir -Parent
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $tmp = Join-Path $parent ("openbiliclaw-clone." + [Guid]::NewGuid().ToString('N'))
    Log-Info "Target contains existing user data only; cloning source into $InstallDir without touching config/data/logs."
    git clone --branch $Branch --depth 1 $RepoUrl $tmp
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
        Log-Err 'git clone failed.'
        exit 1
    }
    foreach ($entry in Get-ChildItem -LiteralPath $tmp -Force) {
        $dest = Join-Path $InstallDir $entry.Name
        if (Test-Path $dest) {
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
            Log-Err "Cannot merge checkout into $InstallDir: destination exists: $dest"
            exit 1
        }
        Move-Item -LiteralPath $entry.FullName -Destination $InstallDir
    }
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
}

function Ensure-Checkout {
    $hasPyproject = Test-Path (Join-Path $InstallDir 'pyproject.toml')
    $hasExample   = Test-Path (Join-Path $InstallDir 'config.example.toml')

    if ($hasPyproject -and $hasExample) {
        Log-Info "Using existing checkout at $InstallDir"
        # Auto-update when safe: clean working tree + fast-forward available.
        if (Test-Path (Join-Path $InstallDir '.git')) {
            try {
                Push-Location $InstallDir
                git fetch --quiet origin $Branch 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Log-Warn 'git fetch failed; skipping update check.'
                    return
                }
                $local  = (git rev-parse HEAD 2>$null).Trim()
                $remote = (git rev-parse "origin/$Branch" 2>$null).Trim()
                if (-not $local -or -not $remote -or $local -eq $remote) { return }
                $behind = (git rev-list --count "$local..$remote" 2>$null).Trim()
                $dirty  = git status --porcelain 2>$null
                if ($dirty) {
                    # `uv sync` / `npm ci` during install rewrite uv.lock and
                    # extension/package-lock.json, leaving the tree "dirty" and
                    # permanently blocking auto-update — users got stranded on
                    # weeks-old code (e.g. v0.3.89) without noticing. If the ONLY
                    # dirty paths are these regenerated lockfiles, discard them
                    # (the dependency step rebuilds them) and update anyway.
                    $nonLock = git status --porcelain 2>$null |
                        Where-Object { $_ -notmatch '\s(uv\.lock|extension/package-lock\.json)$' }
                    if (-not $nonLock) {
                        Log-Info 'Local changes are only regenerated lockfiles (uv.lock / package-lock.json) — resetting and updating…'
                        # Reset each present lockfile independently: a single
                        # `git checkout -- a b` aborts if any pathspec is missing
                        # (older checkouts lack package-lock.json).
                        foreach ($lf in @('uv.lock', 'extension/package-lock.json')) {
                            git cat-file -e "HEAD:$lf" 2>$null
                            if ($LASTEXITCODE -eq 0) { git checkout HEAD -- $lf 2>$null | Out-Null }
                        }
                        git pull --ff-only --quiet origin $Branch
                        if ($LASTEXITCODE -eq 0) {
                            Log-OK "✓ Updated to $((git rev-parse --short HEAD).Trim())"
                        } else {
                            Log-Warn 'git pull failed after resetting lockfiles; keeping current checkout.'
                        }
                        return
                    }
                    # Genuine local edits: make the stale-version risk impossible
                    # to miss (a quiet one-line skip is why people ran old code).
                    $curLine = git show 'HEAD:pyproject.toml' 2>$null | Select-String '^version = "(.*)"' | Select-Object -First 1
                    $newLine = git show ("origin/{0}:pyproject.toml" -f $Branch) 2>$null | Select-String '^version = "(.*)"' | Select-Object -First 1
                    $curVer = if ($curLine) { $curLine.Matches.Groups[1].Value } else { '?' }
                    $newVer = if ($newLine) { $newLine.Matches.Groups[1].Value } else { '?' }
                    Log-Warn '──────────────────────────────────────────────────────────────'
                    Log-Warn "⚠  NOT updated: checkout is $behind commits behind origin/$Branch and has local edits."
                    Log-Warn "   Version v$curVer -> latest v$newVer; continuing will run OLD code."
                    Log-Warn '   Locally modified files:'
                    git status --porcelain 2>$null | ForEach-Object { Log-Warn "     $_" }
                    Log-Warn "   Update (keep your edits): cd $InstallDir; git stash; git pull --ff-only; git stash pop"
                    Log-Warn "   Or let the installer do it (auto git stash first): `$env:FORCE_UPDATE='1'; then rerun the install command"
                    Log-Warn '──────────────────────────────────────────────────────────────'
                    if ($env:FORCE_UPDATE -eq '1') {
                        Log-Info 'FORCE_UPDATE=1 -> stashing local changes and updating…'
                        git stash push -u -m 'install.ps1 auto-stash before update' 2>$null | Out-Null
                        git pull --ff-only --quiet origin $Branch
                        if ($LASTEXITCODE -eq 0) {
                            Log-OK "✓ Updated to $((git rev-parse --short HEAD).Trim())"
                            git stash pop 2>$null | Out-Null
                            if ($LASTEXITCODE -ne 0) {
                                Log-Warn "  Your edits are saved in git stash but auto-restore hit a conflict — run 'cd $InstallDir; git stash pop' to resolve."
                            }
                        } else {
                            Log-Warn "Auto-update failed; checkout unchanged (edits preserved in git stash — 'git stash pop' to restore)."
                        }
                    }
                    return
                }
                Log-Info "Updating existing checkout: $behind commits behind origin/$Branch — pulling…"
                git pull --ff-only --quiet origin $Branch
                if ($LASTEXITCODE -eq 0) {
                    $sha = (git rev-parse --short HEAD).Trim()
                    Log-OK "✓ Updated to $sha"
                } else {
                    Log-Warn 'git pull failed (non-fast-forward?); keeping current checkout.'
                    Log-Warn "  Force fresh install: Remove-Item -Recurse -Force $InstallDir ; rerun this installer"
                }
            } finally {
                Pop-Location
            }
        }
        return
    }

    if ((Test-Path $InstallDir) -and ((Get-ChildItem -Path $InstallDir -Force | Measure-Object).Count -gt 0)) {
        if (Test-UserDataOnlyRoot $InstallDir) {
            Clone-IntoUserDataRoot
            return
        }
        Log-Err "Target directory is not empty and not an OpenBiliClaw checkout: $InstallDir"
        Log-Err 'Set $env:INSTALL_DIR to an empty/non-existent path, or remove the existing one first.'
        exit 1
    }

    $parent = Split-Path $InstallDir -Parent
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Log-Info "Cloning $RepoUrl (branch $Branch) into $InstallDir"
    git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Log-Err 'git clone failed.'; exit 1 }
}

# -----------------------------------------------------------------------------
# Install and validate the single composition entrypoint

function Prepare-Install([string]$PythonExe) {
    Log-Info "Installing OpenBiliClaw from $InstallDir"
    Push-Location $InstallDir
    try {
        & $PythonExe -m pip install -e .
        if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }
        if (-not (Test-Path 'config.toml')) {
            Copy-Item 'config.example.toml' 'config.toml'
            Log-Info 'Created config.toml from the current typed example'
        }
        & openbiliclaw check --config config.toml --data-dir data
        if ($LASTEXITCODE -ne 0) { throw 'OpenBiliClaw composition readiness check failed.' }
    } finally {
        Pop-Location
    }
}

function Print-InstallSummary {
    $healthHost = if ($ApiHost -in @('0.0.0.0', '::', '[::]')) { '127.0.0.1' } else { $ApiHost }
    Write-Host '================================================================'
    Write-Host 'OpenBiliClaw installed and composition readiness passed.'
    Write-Host "Checkout: $InstallDir"
    Write-Host "Start:    Set-Location $InstallDir ; openbiliclaw serve --config config.toml --data-dir data"
    Write-Host "Health:   http://${healthHost}:$Port/v1/runtime/health"
    Write-Host "Web UI:   http://${healthHost}:$Port/"
    if ($SkipStart) { Write-Host 'SkipStart is retained as a no-op; this installer never starts a background service.' }
    Write-Host 'Edit config.toml to enable providers/model routes, then rerun openbiliclaw check.'
    Write-Host '================================================================'
}

# -----------------------------------------------------------------------------
# Main

function Main {
    Log-Info 'OpenBiliClaw one-command installer (Windows / PowerShell)'
    Require-Command 'git' 'Install Git from https://git-scm.com/downloads'
    $pythonExe = Get-PythonExe
    Detect-ReuseSource
    Ensure-Checkout
    Prepare-Install $pythonExe
    Print-InstallSummary
}

try {
    Main
} catch {
    Log-Err $_.Exception.Message
    exit 1
}
