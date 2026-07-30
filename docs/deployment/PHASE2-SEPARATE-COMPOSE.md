# Phase 2 Separate Compose Local Contract

## Status

`C1_LOCAL_IMPLEMENTATION_IN_PROGRESS / CLOUD_WRITE_NOT_AUTHORIZED`

This document describes the local artifacts for a future isolated Hermes Open SWE Lab control plane. It is not a cloud deployment runbook and does not authorize any operation against `/opt/hermes-cloud`.

## Isolation Identity

- Local source: `F:\Users\user\Documents\协作平台`
- WSL source view: `/mnt/f/Users/user/Documents/协作平台`
- Separate future app root: `/opt/hermes-open-swe-lab-phase2`
- Separate future data root: `/var/lib/hermes-open-swe-lab-phase2`
- Compose project: `hermes-open-swe-lab-phase2`
- Service: `control-plane`
- Host bind: `127.0.0.1:18080`
- Container port: `8080`
- Existing `/opt/hermes-cloud`: out of scope and must remain untouched

## Local Components

- `deploy/cloud/Dockerfile.phase2`: minimal non-root control-plane runtime image.
- `deploy/cloud/docker-compose.phase2.yml`: isolated validation contract with bounded resources and file-based Secrets.
- `deploy/cloud/control_plane_app.py`: loopback/non-root adapter using the hardened Worker API handler.
- `hermes_worker/control_plane_http_client.py`: nonce-protected, value-safe Host Worker HTTP client.
- `hermes_worker/remote_token_broker.py`: lease-gated Installation Token endpoint adapter.
- `deploy/worker/pi_worker_runner.py`: dedicated single-job `HostAgentJobRunner` entry point using `PiCliRunner`, with no echo/mock/local fallback.

## Security Contract

The cloud container:

- runs as UID/GID `10001:10001`;
- binds container port 8080 to `0.0.0.0` only when explicit `HERMES_CONTAINER_MODE=1` is set, while Compose publishes the host side only at `127.0.0.1:18080`;
- is not privileged;
- drops all Linux capabilities;
- enables `no-new-privileges`;
- uses a read-only root filesystem and bounded tmpfs;
- is limited to 0.5 CPU, 512 MiB memory, and 128 PIDs;
- mounts only the separate `/var/lib/hermes-open-swe-lab-phase2` data directory;
- has no Docker socket, Pi state, Host credential directory, or target repository mount;
- receives only restricted Secret files under `/run/secrets`;
- does not receive Provider credentials.

The local Host Worker:

- reaches the control plane outbound only;
- uses HTTPS except explicit loopback test mode;
- reads the Worker token from a restricted local file;
- sends a unique nonce and timestamp on every authenticated request;
- is separately allowlisted for privileged Host job routes by worker-token hash;
- runs Pi in JSON/no-session mode with task transport over stdin and explicit Provider/Model/thinking;
- disables all Pi built-in tools and discovered project/user extensions, skills, prompts, context files, and project trust;
- exposes only workspace-contained `read`, `write`, `edit`, `ls`, `find`, `grep`, and terminating `submit_result` tools from the trusted extension;
- gives Pi no bash, Git, GitHub, Docker, network, Worker-token, or Installation-token tool;
- runs target test commands only in `HermesDockerSandboxBackend`;
- requests Installation Tokens only for the active lease;
- performs Host-owned commit, push, and Draft PR creation;
- has no Merge or Auto-merge capability.

## Value-Free Local Validation

The Compose contract can be rendered only with synthetic local Secret files and a synthetic image tag. Do not point these variables at real credentials during C1:

```text
HERMES_LAB_IMAGE_TAG=hermes-open-swe-lab-phase2:c1-local-contract
HERMES_WORKER_TOKEN_HASHES_SOURCE=<synthetic all-worker hash file>
HERMES_HOST_WORKER_TOKEN_HASHES_SOURCE=<synthetic privileged-host hash file>
HERMES_GITHUB_APP_ID_SOURCE=<synthetic App ID file>
HERMES_GITHUB_INSTALLATION_ID_SOURCE=<synthetic installation ID file>
HERMES_GITHUB_APP_PRIVATE_KEY_SOURCE=<synthetic private-key fixture file>
```

Validation command:

```powershell
$env:HERMES_LAB_IMAGE_TAG = "hermes-open-swe-lab-phase2:c1-local-contract"
$env:HERMES_WORKER_TOKEN_HASHES_SOURCE = "C:\Windows\Temp\hermes-c1-worker-hashes"
$env:HERMES_HOST_WORKER_TOKEN_HASHES_SOURCE = "C:\Windows\Temp\hermes-c1-host-worker-hashes"
$env:HERMES_GITHUB_APP_ID_SOURCE = "C:\Windows\Temp\hermes-c1-app-id"
$env:HERMES_GITHUB_INSTALLATION_ID_SOURCE = "C:\Windows\Temp\hermes-c1-installation-id"
$env:HERMES_GITHUB_APP_PRIVATE_KEY_SOURCE = "C:\Windows\Temp\hermes-c1-private-key-fixture"
docker compose -f deploy/cloud/docker-compose.phase2.yml config --quiet
```

The synthetic fixture values must never be reused in a live environment. C1 does not run `docker compose build` or `up`.

Dedicated runner dry-run:

```powershell
$env:HERMES_CLOUD_URL = "http://127.0.0.1:18080"
$env:HERMES_LOCALHOST_TEST = "1"
$env:HERMES_WORKER_TOKEN_FILE = "C:\Windows\Temp\hermes-c1-synthetic-worker-token"
$env:HERMES_PI_PROVIDER = "not-authorized"
$env:HERMES_PI_MODEL = "not-authorized"
$env:HERMES_PI_THINKING = "off"
$env:HERMES_PI_AGENT_DIR = "C:\Users\user\.pi\agent"
python -m deploy.worker.pi_worker_runner `
  --job-id 1 `
  --repo yzhlx/hermes-open-swe-smoke-test `
  --base main `
  --task "synthetic dry run" `
  --delivery-id pi-local-contract `
  --test-command "python -m pytest -q" `
  --dry-run
```

Expected: JSON reports `runner=HostAgentJobRunner`, `agent=PiCliRunner`, `sandbox=HermesDockerSandboxBackend`, `github_writes=false`, `cloud_writes=false`, and `provider_calls=false`. Dry-run must not read the token file, read Pi credential content, or contact cloud/GitHub/Provider.

## Test Matrix

```text
python -B -m pytest -q -p no:cacheprovider \
  tests/test_control_plane_http_client.py \
  tests/test_pi_cli_runner.py \
  tests/test_pi_worker_runner.py \
  tests/deployment/test_phase2_compose_contract.py

node --test tests/node/pi_workspace_guard.test.mjs
python -B -m pytest -q -p no:cacheprovider tests/test_host_agent_rework.py
python -B -m pytest -q -p no:cacheprovider tests/deployment
python -B -m pytest -q -p no:cacheprovider --ignore=tests/deployment
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/acceptance/run-workbench.ps1
```

Before any C1 commit or future C2 request:

- `git diff --check` must exit 0;
- staged/unstaged/untracked Secret scan must report 0 violations and 0 errors;
- an independent reviewer must report no BLOCKING finding;
- the exact C1 Commit SHA must be recorded;
- no cloud or smoke write may have occurred.

## Future C2 Inputs

C2 is a separate authorization gate. It must use the exact reviewed C1 Commit and include:

- immutable image tag and post-build image digest;
- tracked-source archive hash;
- exact restricted Secret Store source paths by name only;
- pre-deploy port/path/container conflict checks;
- creation and ownership of the separate data directory;
- exact `docker compose build` and `up` commands;
- an SSH local-forward command for the local Host Worker; no public application bind;
- `/healthz` and `/readyz` checks on port 18080;
- container user, mount, privilege, resource, and network inspection;
- data backup and restore commands;
- exact previous image digest and Compose rollback command;
- bounded downtime and stop conditions;
- proof that `/opt/hermes-cloud` was unchanged.

Until C2 is explicitly approved, the only valid status is local implementation/testing. `CLOUD_DEPLOYED`, `PROVIDER_LIVE_PASS`, `GITHUB_WRITE_VERIFIED` for smoke, and `END_TO_END_VERIFIED` remain forbidden.
