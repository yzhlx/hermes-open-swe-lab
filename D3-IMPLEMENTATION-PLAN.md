# D3 Implementation Plan — GitHub Issue → PR Automation Loop (DESIGN ONLY)

> **Status: DESIGN ONLY. NOT IMPLEMENTED.**
> This document is the agreed design for Phase D3. It is created as part of
> "D2 baseline freeze + D3 startup prep". Per the stage constraints, the
> following actions are **explicitly NOT performed** in this document or this
> turn:
> - Webhook is **not** enabled.
> - Webhook secret is **not** generated.
> - No real GitHub App **Installation Token** is written or minted.
> - The smoke-test repo (`yzhlx/hermes-open-swe-smoke-test`) is **not** modified.
> - No new real PR is created (D3 plan lives on branch `d3-design`, no PR).
> - The relay model is **not** called.
> - `yzhlx/hermes-learning-os` is **not** accessed.
>
> Upstream baseline remains pinned: `langchain-ai/open-swe@ed12bb8d…`.

---

## 1. Goal and end-to-end flow

Automate the closed loop from a GitHub Issue to a merged PR **entirely within
the allowed target repo** `yzhlx/hermes-open-swe-smoke-test`, with an
independent reviewer and a user-controlled final merge.

```text
GitHub Issue (on smoke-test repo)
  → GitHub Webhook (issues / issue_comment / pull_request events)
  → Cloud Control Plane (verify signature, dedupe, enqueue task)
  → Local Worker claims task (authenticated internal API)
  → Local Docker Sandbox (HermesDockerSandboxBackend) runs the coding Agent
  → Agent edits the smoke-test repo working tree
  → Control Plane mints a SHORT-LIVED GitHub App Installation Token (per task)
  → Token injected ONLY at the `git push` step
  → Push feature branch → open DRAFT PR (never auto-merge)
  → GitHub CI must pass (status check gate)
  → Independent Reviewer reviews the Draft PR (separate from coding Agent)
  → If changes requested: Scheduler adds `round-2` label (ONLY the scheduler)
  → Agent performs 2nd round on the SAME PR (new head commit)
  → Reviewer re-reviews the new head
  → User is the FINAL merger (agent never merges)
```

---

## 2. Components and responsibilities

| Component | Responsibility | Trust boundary |
| --- | --- | --- |
| **GitHub** | Source of Issues/PRs; hosts smoke-test repo; runs CI; hosts GitHub App | External |
| **Cloud Control Plane** | Webhook receiver, task queue (SQLite), Installation-Token minting, scheduler, reviewer orchestration, event store | Holds GitHub App credentials; NEVER holds long-lived tokens |
| **Local Worker** | Claims tasks; runs Agent inside Docker sandbox; pushes branch; opens Draft PR | Untrusted code execution isolation; fetches creds on demand |
| **HermesDockerSandboxBackend** | Real container lifecycle (validated D2.5): cpus=1, mem=2GB, pids=256, no privileged, bridge net, no docker.sock, single workdir mount, --rm | Hard isolation |
| **Independent Reviewer** | Separate process/agent; reviews PR diff; never self-approves | Isolated from coding Agent |
| **GitHub CI** | Enforces `BASELINE_AUTOMATION_PASSED` / `SECOND_ROUND_FEEDBACK_APPLIED` contract (from smoke-test repo validator) | External gate |

---

## 3. Security and isolation boundaries (must hold)

- **Repo allowlist:** the only repo any automated GitHub operation may touch is
  `yzhlx/hermes-open-swe-smoke-test`. `yzhlx/hermes-learning-os` and the cloud
  Hermes runtime are **never** accessed (protected systems).
- **Token lifecycle:** short-lived, per-task, minted cloud-side, delivered to
  the Worker over an authenticated internal channel, injected only at push,
  redacted in all logs. Never persisted to SQLite, JSONL, or image layers.
- **Sandbox isolation:** identical to D2.5 validated limits; Agent code runs
  inside the container and cannot see host home, SSH keys, `.env`, Docker
  socket, or any non-target repo.
- **Reviewer separation:** the coding Agent and the reviewer are distinct
  processes; the Agent cannot approve its own PR.

---

## 4. The 15 D3 design points (explicit answers)

### 4.1 GitHub Webhook signature verification
- The Control Plane exposes one HTTPS endpoint (e.g. `/webhook/github`).
- On every request: compute `HMAC-SHA256` over the raw request body using the
  **Webhook secret** (`${WEBHOOK_SECRET}`, server-side only, never in git/logs).
- Compare against the `X-Hub-Signature-256` header using constant-time
  comparison. Missing/invalid signature → `403`, event dropped, logged as
  `WEBHOOK_SIGNATURE_REJECTED` (no secret value logged).
- Only `POST` with valid `X-GitHub-Event` is accepted.

### 4.2 Webhook delivery deduplication
- Every GitHub delivery carries a unique `X-GitHub-Delivery` UUID.
- The Control Plane records each seen UUID in a `webhook_events` table
  (delivery_id, event_type, received_at, task_id). Duplicate UUID → idempotent
  no-op (already enqueued/processed).
- Additionally, task idempotency key = `(repo, issue_number)` so retries of the
  same Issue never create two tasks.

### 4.3 Repository allowlist enforcement
- A single constant `ALLOWED_GITHUB_REPOS = ["yzhlx/hermes-open-swe-smoke-test"]`
  is checked at **every** GitHub-touching boundary: webhook receipt (event repo
  must match), token minting (installation must target the allowed repo),
  worker push (remote URL must be the allowed repo), and PR creation.
- Any event/operation referencing a non-allowed repo → `REPO_NOT_ALLOWED`,
  dropped, no further action. `hermes-learning-os` can never pass this check.

### 4.4 Short-lived Installation Token generation (cloud-side)
- Control Plane holds the GitHub App PEM (`${GITHUB_APP_PRIVATE_KEY_PATH}`,
  server-side, 600 perms) and App ID / Installation ID.
- To mint a token: sign a short-lived JWT (App ID, `iat`, `exp` ~1 min),
  exchange via `POST /app/installations/{id}/access_tokens` with
  `repositories: ["hermes-open-swe-smoke-test"]` and a TTL (e.g. 1 hour,
  minimum viable). Returns an Installation Token valid **only** for the
  allowed repo.

### 4.5 Token non-persistence
- The Installation Token exists **only** in the Control Plane's process memory
  for the duration of the mint→deliver window, and in the Worker's process
  environment for the duration of the push.
- **Never** written to: SQLite (tasks/events/workers tables), JSONL event log
  (`command`/`token` columns redacted), container image layers, or any file.
- Token is overwritten/zeroized immediately after the push returns.

### 4.6 Worker fetches credentials on demand
- The Worker does **not** hold GitHub App credentials. At claim time it calls
  an authenticated internal Control Plane endpoint
  (`POST /internal/task/{id}/token`) presenting its worker token; the Control
  Plane returns a short-lived Installation Token scoped to the task's repo.
- This keeps the App PEM isolated in the Control Plane (matches D2 boundary
  B15: Worker pulls, does not hold secrets).

### 4.7 Token injected only at the `git push` step
- During Agent coding/exec inside the sandbox, **no** GitHub token is present.
- `HermesDockerSandboxBackend.push()` receives the token as a parameter and
  injects it **only** as `docker exec -e GITHUB_TOKEN=***` for the push
  subprocess (consistent with D2 `set_github_token()` hook). Clone/commit do
  not carry the token.

### 4.8 Token redaction in logs and command records
- Extend `_redact()` (currently masks `-e/--env` values) to ALSO mask tokens
  appearing inside the command string itself (patterns: `ghp_…`,
  `github_pat_…`, `https://x:TOKEN@host`, raw 40-hex). This closes the
  command-embedded-secret gap noted in the PR #1 review.
- The event store and `export_run_evidence.py` persist only redacted `_calls`.

### 4.9 Draft PR by default, never auto-merge
- All PRs are created with `draft: true`. The Agent never calls merge.
- Merge is reserved exclusively for the user (final merger).

### 4.10 CI must pass
- A required status check (GitHub Actions in the smoke-test repo) must be
  `success` before the Independent Reviewer is invoked.
- Reviewer stage is gated on CI green; if CI fails, the loop returns to the
  Agent for a fix round (new head on same PR), not a new PR.

### 4.11 Reviewer / Agent separation
- The Independent Reviewer is a distinct process/agent (same pattern as the
  PR #1 cold review): it reads the PR diff and the sandbox evidence, outputs
  `APPROVE` / `REQUEST_CHANGES`. The coding Agent cannot self-approve.

### 4.12 `round-2` label set ONLY by the scheduler
- Only the Control Plane scheduler may add the `round-2` PR label (mirrors the
  smoke-test repo's `orchestrate_round2.py` sole-owner rule).
- CI enforces: if a feedback marker is present without the `round-2` label
  while in `FEEDBACK_REQUESTED`/`REWORK_RUNNING`, it fails loudly
  (`ROUND_2_LABEL_GATE_FAILED`) — no silent degradation to round 1.

### 4.13 Second round reuses the SAME PR
- Feedback is applied as a new head commit on the existing Draft PR. No new PR
  is opened for round 2. The reviewer re-reviews the new head.

### 4.14 User is the final merger
- After reviewer `APPROVE`, the PR remains a Draft. Only the user merges via
  the GitHub UI / `gh`. The automation never merges.

### 4.15 Failure / disconnect / token-expiry recovery
- **Webhook retry:** GitHub retries failed deliveries; dedupe (4.2) makes
  retries safe.
- **Worker disconnect:** lease + heartbeat (D1) re-queues the task; a different
  or reconnected Worker reclaims it. In-flight container is cleaned by
  `--rm` / `delete()`.
- **Token expiry mid-run:** if push happens after token TTL, Worker requests a
  fresh token from Control Plane (4.6) and retries push once; repeated failure
  → task marked `FAILED`, escalated to user.
- **CI failure:** loop back to Agent fix round (same PR), re-run CI.
- **Reviewer rejection:** scheduler adds `round-2`, Agent 2nd round, re-review.
- **Repeated failure (N attempts):** task enters `ESCALATED` state; user
  notified, no automatic escalation beyond that.

---

## 5. Data-model additions for D3 (all on Control Plane SQLite)

```sql
-- dedupe webhook deliveries (4.2)
CREATE TABLE webhook_events (
  delivery_id TEXT PRIMARY KEY,
  event_type  TEXT NOT NULL,
  repo        TEXT NOT NULL,
  received_at TEXT NOT NULL,
  task_id     TEXT
);

-- task idempotency key (repo, issue_number) — unique
-- tasks table gains columns: pr_number, pr_head_sha, round, token_status
-- NO token column is ever added (4.5)
```

Event store JSONL: `command` and `token` fields are redacted before write (4.8).

---

## 6. Webhook receiver pseudo-implementation (illustrative)

```python
@app.post("/webhook/github")
async def github_webhook(request):
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not verify_hmac(body, sig, WEBHOOK_SECRET):        # 4.1
        return Response(403)                               # no secret logged
    delivery = request.headers.get("X-GitHub-Delivery")
    if seen(delivery):                                     # 4.2
        return Response(200)                               # idempotent
    event = json.loads(body)
    repo = event["repository"]["full_name"]
    if repo not in ALLOWED_GITHUB_REPOS:                   # 4.3
        return Response(200)                               # dropped, no action
    task_id = enqueue(repo, event)                         # idempotent key
    record_delivery(delivery, event["action"], repo, task_id)
    return Response(200)
```

---

## 7. Token minting pseudo-implementation (illustrative)

```python
def mint_installation_token(repo: str, ttl_minutes: int = 60) -> str:
    assert repo in ALLOWED_GITHUB_REPOS                       # 4.3
    jwt = sign_app_jwt(GITHUB_APP_ID, private_key_path)       # PEM server-side
    resp = github_post(
        f"/app/installations/{INSTALLATION_ID}/access_tokens",
        jwt=jwt, json={"repositories": [repo], "expires_at": iso_in(ttl_minutes)},
    )
    return resp["token"]                                      # in-memory only (4.5)
```

The token is returned to the Worker over the authenticated internal channel and
immediately zeroized in the Control Plane after delivery.

---

## 8. Out-of-scope / forbidden in this stage

- No Webhook enablement, no Webhook secret generation.
- No real Installation Token minting or writing.
- No modification of the smoke-test repo.
- No new real PR (this plan is on `d3-design`, no PR).
- No relay-model invocation.
- No access to `yzhlx/hermes-learning-os`.

---

## 9. Prerequisites / user actions before D3 execution

1. Generate a GitHub Webhook secret; store server-side (not in git).
2. Confirm GitHub App `yzhlx/hermes-open-swe-smoke-test` permissions
   (pull_requests: read+write) and that the App is installed only there.
3. (Optional) Provide relay credentials if the Agent should use a
   non-default model; otherwise Open SWE default is used.
4. Deploy the Cloud Control Plane with an HTTPS endpoint reachable by GitHub
   (or use a GitHub App–driven model that avoids public ingress).
5. Keep the per-task short-lived token TTL minimal.

---

## 10. Open questions

- **Webhook ingress topology:** public HTTPS endpoint vs. GitHub App
  polling / worker-initiated pull. The latter avoids exposing the Control
  Plane but adds latency. Decision deferred to D3 execution.
- **Token TTL tuning:** 60 min default vs. shorter (e.g. 15 min) with
  on-demand refresh at push time.
- **Reviewer model:** which model backs the Independent Reviewer (reuse the
  relay adapter; must remain isolated from the coding Agent process).

---

## 11. Security hardening implemented (2026-07-25) — reviewer non-blocking → hard gates

The D2 independent reviewer raised 6 non-blocking items. Per user decision they
are **promoted to deployment hard gates** and implemented on branch `d3-design`
(Draft PR vs `phase-1-smoke`). Each is validated **offline** (fake webhook
secret, fake GitHub token, localhost servers, no real webhook, no smoke-test
repo write, no relay call).

| # | Gate | File(s) | Offline test |
| --- | --- | --- | --- |
| 1 | Worker register allowlist | `control_plane.register`, `worker_api_server.run_server(allowed_tokens=)`, `ALLOWED_WORKER_TOKENS` | `test_01_*`, `test_01b` |
| 2 | HTTPS required (real deploy) | `worker.__init__` (`insecure_local_ok` gate) | `test_02` |
| 3 | Replay protection (Worker + Webhook) | `control_plane.check_replay`, `webhook_receiver.verify_signature` + `deliveries` dedup | `test_03a`–`test_03h` |
| 4 | Mid-job lease keepalive | `control_plane.keepalive`, `worker._keepalive_loop` | `test_04a`, `test_04b` |
| 5 | Command-embedded secret redaction | `hermes_worker/redact.py`, `docker_sandbox._redact`, `worker` event/result `command` | `test_05a`, `test_redact.py` |
| 6 | Atomic claim (no TOCTOU) | `control_plane.claim` `UPDATE…RETURNING` | `test_06` (12 jobs × 4 workers concurrency) |

**Result:** `tests/test_d3_security.py` = 15/15 OK. Full baseline (adapter 12 +
D1 7 + D2 11 + redact 10 + D3 15) = **55 tests OK**.

**Not done (by design):** webhook NOT enabled; no real webhook secret; smoke-test
repo untouched; relay model not called; `yzhlx/hermes-learning-os` untouched;
no merge. Only a DRAFT PR is opened for review + user acceptance.

**Files changed:** `hermes_worker/control_plane.py`, `hermes_worker/db.py`
(`nonces`/`deliveries` tables), `hermes_worker/worker_api_server.py`,
`hermes_worker/worker.py`, `hermes_worker/docker_sandbox.py`,
`hermes_worker/echo_sandbox.py` (create/delete lifecycle fix), new
`hermes_worker/redact.py`, new `hermes_worker/webhook_receiver.py`, tests
`test_d3_security.py`, `test_redact.py`, `test_d1_offline.py` (opt-in
`insecure_local_ok` for offline), plus `SECURITY-BOUNDARIES.md` (B17–B22).
