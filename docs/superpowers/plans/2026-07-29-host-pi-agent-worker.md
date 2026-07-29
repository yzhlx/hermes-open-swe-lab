# Dedicated Host Pi Agent Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Host Codex CLI execution path with an explicit-model Pi Agent that can modify only one isolated task worktree while Host-owned code retains Docker tests, GitHub credentials, commit, Push, Draft PR, and same-PR rework.

**Architecture:** `PiCliRunner` launches Pi 0.82+ in JSON mode with prompt transport over stdin, no session, no discovered extensions/resources, no project trust, and no built-in tools. One trusted CLI extension exposes only contained read/write/edit/list/find/grep plus a terminating structured-result tool; it rejects symlinks, traversal, `.git`, Secret-like paths/content, and every path outside the exact worktree. The existing Host job flow becomes provider-neutral and emits new Pi/agent identities while accepting legacy Codex states for replay compatibility.

**Tech Stack:** Python 3.11, Pi CLI JSON event stream, TypeScript Pi extension loaded through jiti, Node.js filesystem APIs and `node:test`, pytest, Docker sandbox backend.

---

## File Map

**Create**

- `deploy/worker/pi_workspace_guard.mjs`: pure path, symlink, protected-name, and credential-like-content guards shared by extension and Node tests.
- `deploy/worker/pi_workspace_extension.ts`: contained Pi tools and terminating `submit_result` tool; no bash/network/Git/Docker tools.
- `hermes_worker/pi_cli_runner.py`: fail-closed Pi subprocess adapter, environment allowlist, JSON parser, redacted diagnostics, timeout/tree termination, and workspace evidence.
- `deploy/worker/pi_worker_runner.py`: dedicated remote Host Pi Worker entry point.
- `tests/node/pi_workspace_guard.test.mjs`: traversal/symlink/protected-file/content unit tests.
- `tests/test_pi_cli_runner.py`: subprocess command, stdin, environment, event sanitization, timeout, missing-result, and workspace-binding tests.
- `tests/test_pi_worker_runner.py`: config, explicit model/provider, dry-run, binary/extension preflight, and repository allowlist tests.
- `docs/authorization/PHASE-2-PI-AGENT-SUPERSESSION.md`: value-free record that Codex provisioning is superseded; no live Provider/deployment authorization.

**Rename and modify**

- `hermes_worker/codex_job_runner.py` -> `hermes_worker/host_agent_job_runner.py`: provider-neutral orchestration and Pi event/state/identity names.
- `tests/test_codex_host_flow.py` -> `tests/test_host_agent_flow.py`: retain repository/Docker/Host flow coverage and replace CLI-specific tests with Pi contracts.
- `tests/test_codex_host_rework.py` -> `tests/test_host_agent_rework.py`: preserve exact-PR/rework/security tests with provider-neutral fixtures.
- `tests/test_codex_worker_runner.py` -> `tests/test_pi_worker_runner.py`.

**Modify**

- `hermes_worker/constants.py`: add active/terminal Pi Agent states and retain legacy Codex states only for replay compatibility.
- `hermes_worker/control_plane_http_client.py`: default Worker identity/capability becomes `host-pi-worker` / `host-pi`.
- `tests/test_control_plane_http_client.py`: new identity/events plus legacy-state compatibility.
- `docs/deployment/PHASE2-SEPARATE-COMPOSE.md`: Pi Agent runtime, explicit model/provider, contained tool extension, and no-live dry run.
- `PROGRESS.md` and `BLOCKED.md`: accurately record local Pi migration status and remaining Provider/C2C/C3/C4 gates.

**Delete after tests migrate**

- `hermes_worker/codex_cli_runner.py`
- `deploy/worker/codex_worker_runner.py`

Historical authorization documents remain preserved and unmodified.

---

### Task 1: Pure Workspace Guard

**Files:**
- Create: `deploy/worker/pi_workspace_guard.mjs`
- Create: `tests/node/pi_workspace_guard.test.mjs`
- Test: `tests/test_pi_cli_runner.py`

- [ ] **Step 1: Write failing traversal/protection tests**

Cover relative `..`, absolute external paths, alternate drive paths, `@` prefixes, existing symlinks/junctions, symlink parents for new files, `.git`, `.env*`, PEM/key/credential names, and credential-shaped write content. Require ordinary in-workspace source files and new nested source files to pass.

```javascript
assert.throws(() => resolveReadable(root, "../outside.txt"), /workspace_path_denied/);
assert.throws(() => resolveWritable(root, ".git/config"), /protected_path/);
assert.throws(() => assertSafeContent("token=ghs_example_value"), /credential_like_content/);
assert.equal(resolveReadable(root, "src/app.py"), join(root, "src/app.py"));
```

- [ ] **Step 2: Run tests and require failure because the module is absent**

```bash
node --test tests/node/pi_workspace_guard.test.mjs
```

Expected: nonzero, module-not-found.

- [ ] **Step 3: Implement fail-closed guard helpers**

Export:

```javascript
export async function resolveReadable(root, inputPath) {}
export async function resolveWritable(root, inputPath) {}
export function assertSafeRelativePath(root, candidate) {}
export function assertSafeContent(content) {}
export function isProtectedRelativePath(relativePath) {}
```

Normalize a single leading `@`; resolve against the canonical worktree; use `realpath` for existing targets and nearest existing parent; reject symlinks/reparse points in every traversed component; compare Windows paths case-insensitively; reject NULs and paths outside the root; never include rejected content in errors.

- [ ] **Step 4: Run Node and pytest wrapper tests**

```bash
node --test tests/node/pi_workspace_guard.test.mjs
python -B -m pytest -q -p no:cacheprovider tests/test_pi_cli_runner.py -k workspace_guard
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/worker/pi_workspace_guard.mjs tests/node/pi_workspace_guard.test.mjs tests/test_pi_cli_runner.py
git commit -m "feat: add contained Pi workspace guard"
```

### Task 2: Contained Pi Extension

**Files:**
- Create: `deploy/worker/pi_workspace_extension.ts`
- Modify: `tests/test_pi_cli_runner.py`

- [ ] **Step 1: Write failing static and extension-load tests**

Require tools to be exactly `read`, `write`, `edit`, `ls`, `find`, `grep`, and `submit_result`; reject `bash`, `exec`, shell spawning, HTTP, Git, Docker, and external path tools. Require `submit_result` to return `terminate: true`.

- [ ] **Step 2: Implement custom tools using Node APIs only**

Start Pi with `--no-builtin-tools`; register all seven tools with strict TypeBox schemas. Use the guard before every filesystem operation, `withFileMutationQueue()` around complete write/edit mutation windows, built-in truncation helpers at 50KB/2000 lines, and relative path output only. Skip symlinks and protected names during recursive find/grep/list.

```typescript
pi.on("session_start", (_event, ctx) => {
  if (ctx.mode !== "json" || canonical(ctx.cwd) !== canonical(requiredRoot)) {
    throw new Error("pi_workspace_binding_failure");
  }
  pi.setActiveTools(["read", "write", "edit", "ls", "find", "grep", "submit_result"]);
});
```

`submit_result` accepts only bounded `summary` and `status` (`completed` or `blocked`), returns those fields in `details`, and terminates the run.

- [ ] **Step 3: Verify extension loads without a Provider call**

```bash
PI_OFFLINE=1 PI_TELEMETRY=0 pi --no-session --no-context-files --no-skills --no-prompt-templates --no-extensions --no-builtin-tools --no-approve -e deploy/worker/pi_workspace_extension.ts --list-models no-such-model
```

Expected: Exit 0, no extension-load diagnostic, no Provider request.

- [ ] **Step 4: Run guard/contract tests**

```bash
node --test tests/node/pi_workspace_guard.test.mjs
python -B -m pytest -q -p no:cacheprovider tests/test_pi_cli_runner.py -k extension
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/worker/pi_workspace_extension.ts deploy/worker/pi_workspace_guard.mjs tests/test_pi_cli_runner.py tests/node/pi_workspace_guard.test.mjs
git commit -m "feat: expose contained tools to Host Pi Agent"
```

### Task 3: Pi CLI Subprocess Adapter

**Files:**
- Create: `hermes_worker/pi_cli_runner.py`
- Modify: `tests/test_pi_cli_runner.py`
- Delete later: `hermes_worker/codex_cli_runner.py`

- [ ] **Step 1: Write failing runner tests**

Test explicit provider/model/thinking, exact CLI isolation flags, task only on stdin, exact cwd/Git-root binding, environment allowlist, dedicated `PI_CODING_AGENT_DIR`, telemetry/update suppression, timeout process-tree termination, JSON-line parsing, reasoning omission, recursive redaction, terminating result requirement, changed-file collection, and diagnostics outside every Git repository.

- [ ] **Step 2: Define provider-neutral result contract**

```python
@dataclass(frozen=True)
class AgentRunResult:
    exit_code: int
    timed_out: bool
    duration_seconds: float
    events_jsonl_path: str
    final_message_path: str
    stdout_summary: str
    stderr_summary: str
    changed_files: list[str]
    command_redacted: str
    diagnostic_dir: str = ""
    workspace_evidence_path: str = ""
    workspace_binding_ok: bool = True
    provider: str = ""
    model: str = ""
```

- [ ] **Step 3: Implement `PiCliRunner`**

The argv must be equivalent to:

```text
pi --mode json --no-session --no-context-files --no-skills --no-prompt-templates --no-extensions --no-builtin-tools --no-approve -e deploy/worker/pi_workspace_extension.ts --provider "$HERMES_PI_PROVIDER" --model "$HERMES_PI_MODEL" --thinking "$HERMES_PI_THINKING"
```

Send only the bounded/redacted task over stdin. Require a successful `submit_result` `tool_execution_end`; an Exit-0 process without that result becomes `pi_result_missing` and nonzero. Persist only recursively sanitized JSON events, omit reasoning, and never persist raw stdout.

- [ ] **Step 4: Run focused tests**

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_pi_cli_runner.py
```

Expected: all PASS with no live Provider call.

- [ ] **Step 5: Commit**

```bash
git add hermes_worker/pi_cli_runner.py tests/test_pi_cli_runner.py
git commit -m "feat: add fail-closed Host Pi CLI runner"
```

### Task 4: Provider-Neutral Host Job Flow

**Files:**
- Rename: `hermes_worker/codex_job_runner.py` -> `hermes_worker/host_agent_job_runner.py`
- Modify: `hermes_worker/constants.py`
- Modify: `hermes_worker/control_plane_http_client.py`
- Rename/modify: `tests/test_codex_host_flow.py` -> `tests/test_host_agent_flow.py`
- Rename/modify: `tests/test_codex_host_rework.py` -> `tests/test_host_agent_rework.py`
- Modify: `tests/test_control_plane_http_client.py`

- [ ] **Step 1: Rename files and write failing identity/state assertions**

Require new runtime values:

```text
AGENT_RUNNING
AGENT_FAILED
AGENT_NO_CHANGES
agent_result
host-pi-worker
host-pi
pi/job-<job>-<delivery>
```

Retain `CODEX_RUNNING`, `CODEX_FAILED`, and `CODEX_NO_CHANGES` only in compatibility state sets and tests that prove old event/SQLite replay remains accepted.

- [ ] **Step 2: Convert orchestration types and fields**

Rename `CodexJobRunner` to `HostAgentJobRunner`, `CodexJobResult` to `HostAgentJobResult`, constructor parameter `codex_runner` to `agent_runner`, and internal field to `self.agent`. Replace all user-facing event/error/PR text with Pi/Agent terminology while preserving lease, exact repository, exact delivery, same branch, same Draft PR, PR Head, token-broker, Docker-only test, commit/Push ownership, and idempotency behavior.

- [ ] **Step 3: Update remote Worker identity**

Default registration becomes:

```python
name="host-pi-worker"
capabilities=["host-pi", "contained-workspace-tools", "docker-sandbox", "draft-pr"]
```

- [ ] **Step 4: Run focused flow and rework tests**

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_host_agent_flow.py tests/test_host_agent_rework.py tests/test_control_plane_http_client.py
```

Expected: all PASS; exact same-PR rework and legacy replay remain covered.

- [ ] **Step 5: Commit**

```bash
git add hermes_worker/host_agent_job_runner.py hermes_worker/constants.py hermes_worker/control_plane_http_client.py tests/test_host_agent_flow.py tests/test_host_agent_rework.py tests/test_control_plane_http_client.py
git commit -m "refactor: make Host job flow Pi agent neutral"
```

### Task 5: Dedicated Remote Pi Worker Entry Point

**Files:**
- Create: `deploy/worker/pi_worker_runner.py`
- Rename/modify: `tests/test_codex_worker_runner.py` -> `tests/test_pi_worker_runner.py`
- Delete: `deploy/worker/codex_worker_runner.py`

- [ ] **Step 1: Write failing config/preflight tests**

Require explicit `HERMES_PI_PROVIDER`, `HERMES_PI_MODEL`, `HERMES_PI_THINKING`, absolute `HERMES_PI_AGENT_DIR`, verified Pi binary, verified extension/guard paths, loopback HTTP only under explicit localhost test, immutable Docker image, smoke repository allowlist, and no backend/local/mock/echo fallback.

- [ ] **Step 2: Implement value-free dry-run**

Dry-run must report:

```json
{
  "runner": "HostAgentJobRunner",
  "agent": "PiCliRunner",
  "tools": ["read", "write", "edit", "ls", "find", "grep", "submit_result"],
  "sandbox": "HermesDockerSandboxBackend",
  "github_writes": false,
  "cloud_writes": false,
  "provider_calls": false
}
```

It must not read Worker token or Pi credential files.

- [ ] **Step 3: Wire live path without executing it**

Construct `ControlPlaneHttpClient`, `RemoteTokenBroker`, `PiCliRunner`, `RepositoryPreparer`, `HostGitOperations`, immutable `HermesDockerSandboxBackend`, and `GitHubRestClient`. Preflight Pi binary/extension/config before loading Worker token or mutating a remote job.

- [ ] **Step 4: Run entry-point tests and dry-run**

```bash
python -B -m pytest -q -p no:cacheprovider tests/test_pi_worker_runner.py
python -m deploy.worker.pi_worker_runner --job-id 1 --repo yzhlx/hermes-open-swe-smoke-test --base main --task "synthetic dry run" --delivery-id pi-local-contract --test-command "python -m pytest -q" --dry-run
```

Expected: PASS, `provider_calls=false`, no token-file read, no cloud/GitHub write.

- [ ] **Step 5: Commit**

```bash
git add deploy/worker/pi_worker_runner.py tests/test_pi_worker_runner.py
git rm deploy/worker/codex_worker_runner.py
git commit -m "feat: wire dedicated remote Host Pi Worker"
```

### Task 6: Remove Codex Runtime and Update Current Documentation

**Files:**
- Delete: `hermes_worker/codex_cli_runner.py`
- Modify: `docs/deployment/PHASE2-SEPARATE-COMPOSE.md`
- Create: `docs/authorization/PHASE-2-PI-AGENT-SUPERSESSION.md`
- Modify: `docs/authorization/PHASE-2-C2C-C3-HUMAN-PROVISIONING-CHECKLIST.md`
- Modify: `PROGRESS.md`
- Modify: `BLOCKED.md`

- [ ] **Step 1: Remove executable Codex references from current runtime/docs**

Do not rewrite historical authorization/result documents. Mark the earlier Codex provisioning checklist section as superseded by Pi requirements. Record `pi 0.82.1` as locally available and explicitly state that no Host Pi Provider call has occurred.

- [ ] **Step 2: Add Pi Human Owner prerequisites**

Require path-only/value-free readiness for `PI_CODING_AGENT_DIR`, explicit provider/model/thinking, and credential-store permissions. Never request or print credential contents. Record current candidate `deepkey/gpt-5.6-sol` only as the parent-session observation, not an authorized Host Worker selection.

- [ ] **Step 3: Scan for live Codex imports/commands**

```bash
rg -n "CodexCliRunner|codex exec|HERMES_CODEX_BINARY|deploy.worker.codex_worker_runner" hermes_worker deploy tests docs/deployment PROGRESS.md BLOCKED.md
```

Expected: no current runtime/deployment matches. Historical authorization documents may still contain immutable records.

- [ ] **Step 4: Run docs and Secret gates**

```bash
git diff --check
python tools/acceptance/secret_scan.py --repo . --output C:/Windows/Temp/hermes-pi-agent-secret-scan.json
```

Expected: 0 violations, 0 errors.

- [ ] **Step 5: Commit**

```bash
git add docs/deployment/PHASE2-SEPARATE-COMPOSE.md docs/authorization/PHASE-2-PI-AGENT-SUPERSESSION.md docs/authorization/PHASE-2-C2C-C3-HUMAN-PROVISIONING-CHECKLIST.md PROGRESS.md BLOCKED.md
git rm hermes_worker/codex_cli_runner.py
git commit -m "docs: supersede Codex with contained Host Pi Agent"
```

### Task 7: Full Verification and Independent Review

**Files:** all files above; review artifact remains untracked under `.pi-subagents/reviews/`.

- [ ] **Step 1: Run focused Node/Python tests**

```bash
node --test tests/node/pi_workspace_guard.test.mjs
python -B -m pytest -q -p no:cacheprovider tests/test_pi_cli_runner.py tests/test_pi_worker_runner.py tests/test_host_agent_flow.py tests/test_host_agent_rework.py tests/test_control_plane_http_client.py
```

- [ ] **Step 2: Run complete regression suites**

```bash
python -B -m pytest -q -p no:cacheprovider --ignore=tests/deployment
python -B -m pytest -q -p no:cacheprovider tests/deployment
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/acceptance/run-workbench.ps1
```

- [ ] **Step 3: Run final fail-closed gates**

Require `git diff --check`, 0 Secret violations/errors across staged/unstaged/untracked files, no unexpected staging, no live Provider call, no cloud/GitHub/smoke write, and no residual process/container.

- [ ] **Step 4: Independent security/correctness review**

Review path canonicalization/symlink handling, tool allowlist, Pi CLI flags, environment/credential boundary, JSON persistence/redaction, missing-result behavior, legacy state compatibility, exact PR Head/rework, and Docker-only tests. No BLOCKING/IMPORTANT finding may remain.

- [ ] **Step 5: Create one local implementation commit if review fixes are needed**

No Push, cloud deployment, Provider call, Installation Token, Host Worker registration, smoke write, or Merge occurs without later exact authorization.

---

## Self-Review

- **Spec coverage:** Pi replaces the runtime executable and Worker entry point; workspace containment, credential isolation, Host-owned Git/GitHub, Docker-only tests, same-PR rework, explicit Provider/Model, offline validation, legacy replay, and no-live gates each have a task.
- **Placeholders:** The plan uses concrete file paths and named environment variables; implementation configuration must fail closed when required variables are absent.
- **Type consistency:** `PiCliRunner.run()` returns `AgentRunResult`; `HostAgentJobRunner` accepts `agent_runner`; `pi_worker_runner` constructs both; new states/events/identities are used consistently and legacy Codex names remain compatibility-only.
