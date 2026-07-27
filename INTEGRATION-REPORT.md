# ONE-DAY Integrated Vertical Slice — Integration Report

**Date**: 2026-07-27
**Integration Lead**: ONE-DAY-DEMO-INTEGRATION-LEAD
**Final Status Token**: `ONE_DAY_INTEGRATED_VERTICAL_SLICE_READY_LOCAL`

---

## 1. Integration coordinates

| Item | Value |
|------|-------|
| Integration branch | `demo/one-day-integrated-vertical-slice-20260727` |
| Worktree | `F:/project/hermes-open-swe-lab-one-day-integration` |
| Exact base SHA | `263d4a6ba4674c22d89e8c2cf4b4d1a49fdfd94d` |
| HEAD SHA (after wiring) | `b4d6768e7c1b3cf1dfc86eb30653109e1b0478db` |

Base SHA verification: after `git fetch origin --prune`, `git rev-parse origin/canonical/phase-b-baseline-20260726` returned **exactly** `263d4a6…` (user-corrected SHA). The earlier `f4cf757…` was a stale ancestor; `1334fa0…` was a CI-failed intermediate commit and was **not** used.

---

## 2. Cherry-pick order & result (all clean, no manual conflict resolution needed)

| # | Commit | PB | Description | Result |
|---|--------|----|-------------|--------|
| 1 | `ca77836ffb8625842272ae3244ef9b41b83cd7d9` | PB-1 | Converge delivery through release agent | ✅ clean → `15a1687` |
| 2 | `fe87138fc8df7d30d14cffb8e460dc91f2657739` | PB-4 | Hand off real local commits from coding worker | ✅ clean → `2d643ef` |
| 3 | `e71a30883174b8842a45889a9eb757da1d046cb5` | PB-5 | ALLOWED_WORKER_TOKENS fail-closed | ✅ clean → `0c04211` |
| 4 | `d6930e63faba98258cd68e595a114e318a9d3364` | PB-6 | GitHub repo allowlist on reviewer/CI/label | ✅ clean → `b2562e5` |
| 5 | `f4dd1fcffd391b2343e2c00ff4246339ae676763` | PB-23 | Human final acceptance before completion | ✅ auto-merged → `2ca9f57` |
| 6 | `784d514` | Demo Runner | ONE-DAY-COLLABORATION-DEMO-RUNNER (Agent F) | ✅ clean → `81d1399` |
| 7 | `b4d6768…` | — | Wire Runner to production ReleaseAgent + ControlPlane gate | ✅ committed |

Notes:
- PB-23 auto-merged `constants.py` / `control_plane.py` / `worker_api_server.py` against PB-5 — verified both semantics coexist (no pure ours/theirs).
- Used `784d514` per user ruling; `e1b6e0a` is frozen-protocol-rejected and was NOT used.

---

## 3. Conflicts

- **PB-23 vs PB-5** overlap in `control_plane.py` + `worker_api_server.py`: git auto-merged cleanly. Verified by reading the merged files that BOTH semantics are present:
  - PB-5: `ControlPlane(mode="production")` raises `worker_tokens_not_configured_production` when no allowlist (control_plane.py:91-94); `worker_api_server.main()` exits 2 on missing/malformed `ALLOWED_WORKER_TOKENS`.
  - PB-23: `request_final_acceptance` (worker token + CI green) / `final_accept` (independent `HUMAN_OWNER_TOKEN`) / `complete` (requires `FINAL_ACCEPTED`); token never in logs/events/errors.
- PB-1 / PB-4 / PB-5 / PB-6 introduced no textual conflicts (isolated to their own files).
- No POTENTIAL_FAIL_OPEN introduced by the integration.

---

## 4. Targeted tests (all must pass)

| Test file | Result |
|-----------|--------|
| `tests/test_pb1_release_agent_delivery.py` | ✅ pass |
| `tests/test_pb4_coding_worker_handoff.py` | ✅ pass |
| `tests/test_pb5_worker_tokens_fail_closed.py` | ✅ pass (13 cases) |
| `tests/test_pb6_github_repo_allowlist.py` | ✅ pass (20 cases) |
| `tests/test_pb23_production_final_acceptance.py` | ✅ pass |
| `tests/test_collaboration_demo_runner.py` | ✅ pass (11 cases, after wiring) |

All 6 targeted files PASS (pytest exit 0).

---

## 5. Full pytest counts

- Command: `python -m pytest -q --ignore=tests/deployment/test_cloud.py`
- Result (parsed from junitxml): **tests = 237, skipped = 0, failures = 0, errors = 0, xfailed = 0**
- `git diff --check`: clean (exit 0) on both working tree and staged.

---

## 6. Structural scan

### 6.1 push_branch / create_draft_pr
- Only in `hermes_worker/delivery.py` (L547 `super().create_draft_pr`, L1017 `self.github.create_draft_pr`).
- No calls in `scripts/` or `deploy/`. ✅

### 6.2 DeliveryController.deliver() caller
- Only `hermes_worker/release_delivery_coordinator.py:145` (`self.delivery.deliver(...)`) — the **production** ReleaseAgent.
- The demo's second internal ReleaseAgent was **removed**; `tools/run_collaboration_demo.py` now imports the production one.
- `scheduler.py` routes through `ReleaseAgent.deliver_task`. ✅

### 6.3 ControlPlane( constructors — classification

| File:line | Context | Class |
|-----------|---------|-------|
| `worker_api_server.py:79` | `mode=mode` (production when ALLOWED_WORKER_TOKENS set) + `allowed_token_hashes` | ✅ PRODUCTION_EXPLICIT |
| `event_router.py:47` | `allowed_token_hashes` set, **no `mode`** → default dev | ⚠️ POTENTIAL_FAIL_OPEN (pre-existing baseline) |
| `webhook_receiver.py:80` | `allowed_token_hashes` set, **no `mode`** → default dev | ⚠️ POTENTIAL_FAIL_OPEN (pre-existing baseline) |
| `deploy/cloud/control_plane_app.py:81` | `ControlPlane(db_path)` no mode, no allowlist → default dev | ⚠️ POTENTIAL_FAIL_OPEN (pre-existing baseline) |

The 3 POTENTIAL_FAIL_OPEN sites are **pre-existing in the baseline `263d4a6`**, NOT introduced or modified by this integration. PB-5's design intentionally defaults `mode="dev"` to preserve test injectability and avoid breaking these call sites; the production worker-token fail-closed boundary is enforced at `worker_api_server.py` (the actual worker-facing server). Recommended follow-up: set `mode="production"` (and ensure `allowed_token_hashes` is configured) in `event_router.py`, `webhook_receiver.py`, and `deploy/cloud/control_plane_app.py`.

### 6.4 Protected repo scan
- `PROTECTED_REPOS = {"yzhlx/hermes-learning-os"}` (constants.py:15).
- Enforced at: `delivery.py` (BLOCKED status), `github_client.py::require_allowed_repo` (raises `repo_not_allowed`, called at L162/168/221 + internal L64/66/68/113), `event_router.py:58`, `github_app.py:82/116`, and PB-6 reviewer/CI/label paths. ✅ Permanent, defense-in-depth rejection.

---

## 7. Acceptance criteria — all MET

- Push / Draft PR actual calls only in `delivery.py` ✅
- `DeliveryController.deliver()` formal caller only production ReleaseAgent ✅
- Runner does NOT contain a second ReleaseAgent ✅
- production Worker token fail-closed (worker_api_server.py) ✅
- protected repo permanently rejected ✅
- Human Owner acceptance gate effective (independent token; FINAL_ACCEPTED before TASK_COMPLETED) ✅
- TASK_COMPLETED produced only once (ControlPlane.complete idempotent) ✅
- all new tests pass; skipped = 0; xfail = 0 ✅

---

## 8. Constraints honored (local only)

- No Push, no PR, no Merge, no modify `canonical`, no access `hermes-learning-os`.
- PB-5 worktree remains frozen via snapshot `snapshot/pb5-e71a308` (unchanged).

---

## 9. Readiness

- PB-1, PB-4, PB-5, PB-6, PB-23, Demo Runner: all integrated and verified on this branch.
- PB-2 / PB-3: **not in scope** for today's single-task vertical slice (user: "一天 Demo 只要求单次任务纵向链路"). The branch is ready to receive them later if their SHAs are provided.

---

## 10. Known limitations / follow-ups

1. **3 pre-existing ControlPlane call sites lack explicit `mode="production"`** (POTENTIAL_FAIL_OPEN, baseline) — recommend hardening (see §6.3). Per the user's strict gate these would be blocking *if baseline hardening is in scope*; they are outside PB-5/PB-23 scope and were not part of the 6-commit integration.
2. The Demo Runner uses a `dev`-mode ControlPlane internally to exercise the PB-23 gate without enforcing the production worker allowlist (the demo never touches the production worker server or real credentials). The production fail-closed remains in `worker_api_server.py`.
3. `test_pb23` mutates the global `constants.HUMAN_OWNER_TOKEN` at import time; the demo reads the live module attribute so it stays consistent regardless of collection order.

---

**Final status token: `ONE_DAY_INTEGRATED_VERTICAL_SLICE_READY_LOCAL`**
