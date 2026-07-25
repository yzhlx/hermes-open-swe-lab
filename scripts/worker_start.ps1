# worker_start.ps1 — launch the local Hermes Worker (Windows).
#
# Safety model (same as the WSL/Linux launcher):
#   - outbound HTTPS only; no inbound port; no Docker socket exposed to cloud;
#   - local restricted config from $HOME/.hermes/worker.env (never committed);
#   - secrets are never echoed to the console or logs.
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Resolve-Path (Join-Path $Here '..')
Set-Location $Root

$envFile = Join-Path $env:USERPROFILE '.hermes/worker.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
    }
  }
}
$env:PYTHONPATH = "$Root;" + ($env:PYTHONPATH -replace '[;]+$', '')

# Docker preflight (when HERMES_WORKER_BACKEND=docker) is performed inside the
# worker runner so it fails clearly if the daemon is down.
python -m deploy.worker.worker_runner
