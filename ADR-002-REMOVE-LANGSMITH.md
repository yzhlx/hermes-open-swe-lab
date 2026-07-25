# ADR-002 — Remove LangSmith from Hermes Open SWE MVP-0

- **Status:** ACCEPTED
- **Date:** 2026-07-25
- **Supersedes:** LangSmith Sandbox / Trace / Snapshot requirements from the earlier MVP-0 design
- **Related:** `MVP-0-ARCHITECTURE.md`, `SECURITY-BOUNDARIES.md`, `INSTALLATION-STATUS.md`

---

## 1. Context

The earlier MVP-0 design relied on LangSmith for two capabilities:

- **Sandbox** execution of target-repo code changes — the Open SWE agent ran inside a
  LangSmith Sandbox (`SANDBOX_TYPE=langsmith`).
- **Trace / observability** exporter for coding-agent and reviewer actions.

On 2026-07-25, diagnosis (judgment rules A–D) confirmed the LangSmith **Sandboxes**
feature is disabled for the organization:

- Official LangSmith CLI v0.2.42 `langsmith sandbox list` → exit 1 / HTTP 403.
- Direct API `GET /v2/sandboxes/boxes` → HTTP 403 **identical WITH and WITHOUT**
  `X-Tenant-Id`; body `detail.error=FeatureDisabled`,
  `detail.message="Sandbox feature is not enabled for this organization"`.
- Result: `SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED` (org-level product enablement / plan;
  card-binding / billing is required to enable it).

The user has **no available payment card**, and the project must **not** depend on a
card-gated execution environment. Continued dependency on LangSmith would block the
entire MVP-0 closed loop indefinitely and force a billing decision the user cannot
make. Therefore LangSmith is removed from MVP-0 by this architecture decision.

---

## 2. Decision

LangSmith (Sandbox / Trace / Snapshot) is **removed** from MVP-0. It is replaced by:

- A **local Hermes Docker Worker** that actively connects to the cloud control plane
  via outbound HTTPS (pull model) and runs the Open SWE agent inside a
  protocol-conforming `HermesDockerSandboxBackend`.
- A **SQLite Event Store + JSONL run logs** (`runtime/events.db`,
  `runtime/runs/*.jsonl`) as the secret-free observability replacement for LangSmith
  Traces.

This decision **overrides ALL prior** LangSmith Sandbox / Trace / Snapshot requirements.

---

## 3. What is removed (do not roll back)

- `SANDBOX_TYPE=langsmith` and the LangSmith Sandbox runtime.
- LangSmith Trace / snapshot exporter and any "trace id" references in the evidence
  contract.
- All requirements that the cloud control plane spawn a LangSmith sandbox.
- The implicit dependency on card-binding / billing to enable the loop.

### Secret purge — COMPLETED (local + server copies)

The LangSmith secrets/config have been removed (2026-07-25, per user decision):

1. The LangSmith vars were deleted from `/opt/hermes-open-swe-lab/.env` as `hermes-swe`
   (owner/mode preserved: `hermes-swe:hermes-swe 600`); GitHub App + relay config
   untouched. The vars actually present were `SANDBOX_TYPE=langsmith`,
   `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT` (no `LANGSMITH_API_KEY` line remained in
   that file — the live secret lived only in the local temp file).
2. The local temp file `C:\Users\user\.secrets\langsmith\langsmith-api-key.txt` was
   **permanently deleted** (including from the Recycle Bin); the now-empty directory was
   removed. The GitHub App key directory was not touched.
3. The LangSmith CLI on the server (`/var/lib/hermes-swe/.local/bin/langsmith`) was
   removed; no LangSmith cache remained.

The **remote** Service Key was intentionally **NOT revoked** from the web UI — the user
accepted the residual risk (remote Key valid until its original 90-day expiry). No
card/billing action was taken. **Revoking the remote Key is no longer a gate for any
phase.** Suggested statuses: `LOCAL_LANGSMITH_CREDENTIALS_DELETED`,
`REMOTE_SERVICE_KEY_NOT_REVOKED_ACCEPTED_RISK`, `LANGSMITH_RUNTIME_REMOVED`.

---

## 4. What is retained (mandatory)

- Open SWE pinned upstream baseline `ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6`.
- `hermes-open-swe-lab` (this repo); `hermes-open-swe-smoke-test` (strict contract).
- GitHub App + single-repo (smoke-test) permissions; private-key secure landing.
- Relay API adapter (opt-in); Provider P1–P6 preflight; Draft PR workflow.
- Deterministic CI; Independent Reviewer; round-2 label gate.
- `AGENTS.md` guardrails + security boundaries; cloud server dedicated user
  `hermes-swe`.
- No automatic merge; `main` never merged by the agent.

---

## 5. New architecture (summary)

See `MVP-0-ARCHITECTURE.md` for the full diagram, the Worker API protocol, and the
SQLite schema. In short:

> GitHub Issue → webhook → **cloud control plane** (webhook receiver, SQLite task
> queue + Event Store, Worker API) → local **Hermes Worker** polls via outbound HTTPS
> → runs the Open SWE agent inside `HermesDockerSandboxBackend` (Docker, isolated) →
> pushes branch + opens **Draft PR** → GitHub CI → **Independent Reviewer** → round-2
> label gate → rework → re-review → **user acceptance**.

The local Worker connects **out** to the cloud; there are **no inbound ports**, **no
Docker socket exposed to the cloud**, and **no public SSH**. All execution state lives
in the SQLite Event Store; observability never logs secrets.

---

## 6. Security boundaries (updated)

- The custom `HermesDockerSandboxBackend` conforms to the Open SWE `SandboxBackend`
  protocol so the agent runs **through** it (never bypassed, never replaced by the
  forbidden Open SWE `local` backend).
- Docker isolation defaults (MVP): `cpus=1`, `memory=2GB`, `pids_limit=256`,
  `timeout=20min`, `concurrency=1`, `privileged=false`, `host_network=false`,
  `docker_socket_mount` forbidden, `auto_remove=true`. The container mounts ONLY the
  current task's temp workdir.
- No access to: user home, SSH keys, browser cookies, Hermes prod config, other repos,
  the Docker host socket, or Windows system dirs.
- GitHub creds = a short-lived Installation Token for the smoke-test repo only.
- Observability (`events.db` / `runs/*.jsonl`) MUST NOT log: API Key, GitHub Token,
  PEM, Auth header, full `.env`, or suspected secrets. `worker_token_hash` stores only
  a `sha256` of the worker token.

---

## 7. Implementation phases

- **D0** (this change): clean LangSmith references in docs; add this ADR; **do not**
  modify real runtime state or delete secrets yet (HELD on the revocation gate).
- **D1**: implement SQLite task queue + Worker API + local Worker + fake
  `EchoSandboxBackend`; full offline test suite. **Status: DONE** — 7 offline unit
  tests PASS; a server+worker+CLI integration smoke also passes.
- **D2**: implement `HermesDockerSandboxBackend`; verify container lifecycle against a
  local non-sensitive test repo (no GitHub).
- **D3**: integrate the smoke-test repo, GitHub App short token, a read-only task, a
  specified-file-write task, Draft PR, CI, Reviewer, round-2 rework, re-review.
  No access to `yzhlx/hermes-learning-os`.

---

## 8. Consequences

- **Positive:** MVP-0 is no longer blocked on card-binding / billing; the full loop is
  self-hostable.
- **Positive:** observability is first-class and secret-free by construction.
- **Negative:** the local Worker requires a machine with Docker that can reach the
  cloud control plane over HTTPS (outbound). The cloud server no longer needs LangSmith.
- **Negative:** we own the sandbox backend implementation (D2 scope).

---

## 9. Historical note (do not claim LangSmith passed)

LangSmith was diagnosed as `SANDBOX_ACCOUNT_ENABLEMENT_REQUIRED` and never reached a
working Sandbox / Trace in MVP-0. **No LangSmith feature is claimed as "passed".** The
related docs (`LANGSMITH-SETUP.md`, the prior `INSTALLATION-STATUS.md` row 14) are
retained as historical evidence, marked `RETIRED_BY_ARCHITECTURE_DECISION`.
