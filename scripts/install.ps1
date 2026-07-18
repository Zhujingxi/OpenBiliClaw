#requires -Version 5.1
<# Install the vNext OpenBiliClaw API + worker. Docker is preferred when available. #>
[CmdletBinding()]
param(
    [string] $InstallDir = $env:INSTALL_DIR,
    [string] $Mode = $env:MODE,
    [string] $ApiHost = $env:HOST,
    [string] $LiteLLMAdminUrl = $env:OPENBILICLAW_LITELLM_ADMIN_URL,
    [int] $Port = 0,
    [switch] $SkipStart
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = `
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE 'OpenBiliClaw' }
if (-not $Mode) { $Mode = 'auto' }
if (-not $ApiHost) { $ApiHost = '0.0.0.0' }
if ($Port -le 0) { $Port = 8420 }
$repoUrl = if ($env:OPENBILICLAW_REPO_URL) { $env:OPENBILICLAW_REPO_URL } else { 'https://github.com/whiteguo233/OpenBiliClaw.git' }
$branch = if ($env:OPENBILICLAW_BRANCH) { $env:OPENBILICLAW_BRANCH } else { 'main' }

function Fail([string] $Message) { throw "OpenBiliClaw install failed: $Message" }
function Log([string] $Message) { Write-Host "[openbiliclaw] $Message" }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail 'git is required' }
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $python) { Fail 'Python 3.11+ is required' }
& $python.Source -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) { Fail 'Python 3.11+ is required' }

if (Test-Path $InstallDir -PathType Leaf) { Fail "install path is not a directory: $InstallDir" }
if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir | Out-Null }
$pyproject = Join-Path $InstallDir 'pyproject.toml'
if (-not (Test-Path $pyproject)) {
    if (@(Get-ChildItem -LiteralPath $InstallDir -Force).Count -ne 0) {
        Fail "install directory is non-empty and is not an OpenBiliClaw checkout"
    }
    Log "Cloning OpenBiliClaw into $InstallDir"
    git clone --branch $branch --depth 1 $repoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Fail 'git clone failed' }
} else {
    Log "Using existing checkout at $InstallDir (local changes are preserved)"
}

if ($Mode -eq 'auto') {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        docker compose version *> $null
        $Mode = if ($LASTEXITCODE -eq 0) { 'docker' } else { 'local' }
    } else {
        $Mode = 'local'
    }
}
if ($Mode -notin @('docker', 'local')) { Fail 'MODE must be auto, docker, or local' }

if ($Mode -eq 'docker') {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail 'Docker is required' }
    docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { Fail 'Docker Compose v2 is required' }
} else {
    $envFile = Join-Path $InstallDir '.env'
    # The Python bootstrap alone validates and reads an existing private .env.
    if (-not (Test-Path $envFile) -and -not $env:OPENBILICLAW_LITELLM_BASE_URL) {
        $env:OPENBILICLAW_LITELLM_BASE_URL = Read-Host 'LiteLLM base URL'
    }
    if (-not (Test-Path $envFile) -and -not $env:OPENBILICLAW_LITELLM_API_KEY) {
        $secure = Read-Host 'LiteLLM API key' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $env:OPENBILICLAW_LITELLM_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
}

$arguments = @(
    (Join-Path $InstallDir 'scripts\runtime_bootstrap.py'),
    '--project-dir', $InstallDir,
    '--mode', $Mode,
    '--host', $ApiHost,
    '--port', $Port
)
if ($LiteLLMAdminUrl) { $arguments += @('--litellm-admin-url', $LiteLLMAdminUrl) }
if ($SkipStart -or $env:SKIP_START -eq '1') { $arguments += '--skip-start' }
if ($env:ROTATE_ACCESS -eq '1') { $arguments += '--rotate-access' }

if ($SkipStart -or $env:SKIP_START -eq '1') {
    Log "Preparing the $Mode runtime and applying migration (services remain stopped)"
} else {
    Log "Starting the $Mode runtime and verifying migration, API, worker, and protected access"
}
& $python.Source @arguments
if ($LASTEXITCODE -ne 0) { Fail "bootstrap exited with code $LASTEXITCODE" }
Log "Runtime secrets are stored in $InstallDir\.env with an owner-only Windows ACL and are reused on rerun."
Log 'On first install, save the Web password and extension key from the first_run_access status event; plaintext is not stored and cannot be shown again.'
if ($Mode -eq 'docker') {
    Log 'Open setup to use the persisted LiteLLM Admin URL and configure provider aliases.'
}
Log 'Web and extension clients use the generated vNext API contract.'
