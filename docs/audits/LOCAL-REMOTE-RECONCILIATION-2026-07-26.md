# Local ↔ Remote Reconciliation — Hermes Open SWE Lab

**Date**: 2026-07-26
**Scenario**: Local workspace ↔ GitHub progress reconciliation (prerequisite to Buzz/Openship reuse audit)
**Participants**: gstack-investigator (reconciliation + audit), gstack-lead (GitHub handoff)

---

## 1. Actual local state (verified, read-only)

| Item | Value |
|------|-------|
| Actual repo root | `F:/project/hermes-open-swe-lab` |
| Current working directory | `F:/project/hermes-open-swe-lab` |
| Current branch (active work) | `d4-delivery-layer` |
| Local HEAD (d4) | `8806010ca3291be79d886ba5b70c946c19585299` |
| HEAD commit subject | `wip(checkpoint): include D4 canonical integration report` |
| Remote | `origin` = `https://github.com/yzhlx/hermes-open-swe-lab.git` |
| Current branch upstream | `origin/d4-delivery-layer` (created this session) |
| Working tree | **clean** (no modified tracked files, no untracked files) |

> Note: The "known" workspace `F:/project/hermes-open-swe-lab_codex` referenced in the stale clues is a **registered git worktree** (branch `codex-primary-agent-policy`, frozen at `3612b526`) — it is NOT the active development location. The active work is on `d4-delivery-layer` in the main repo.

## 2. All local branches (with tracking)

| Branch | HEAD | Upstream | Notes |
|--------|------|----------|-------|
| `main` | `04ed8cf` | — | project constraints only |
| `phase-1-smoke` | `3f803f0` | `origin/phase-1-smoke` (behind 6) | PR #5 base |
| `phase-d0-d1` | `006fdd6` | `origin/phase-d0-d1` | |
| `d3-design` | `9f711ae` | `origin/d3-design` | D4 fork point |
| `d3-integration` | `309c6f1` | `origin/d3-integration` | head of PR #5 |
| `d3-deployment` | `a867863` | `origin/d3-deployment` | separate worktree |
| `provider-live` | `f7700cf` | `origin/provider-live` | separate worktree |
| `codex-primary-agent-policy` | `3612b526` | — (local only) | **frozen**, do-not-touch |
| `d4-delivery-layer` | `8806010` | `origin/d4-delivery-layer` (created) | **active work, now pushed** |
| `audit/buzz-openship-reuse-20260726` | `8806010` | — (local) | audit branch, this session |

## 3. Worktrees

| Path | Branch | HEAD |
|------|--------|------|
| `F:/project/hermes-open-swe-lab` | `audit/buzz-openship-reuse-20260726` (was `d4-delivery-layer`) | `8806010` |
| `F:/project/hermes-open-swe-lab-d3-deployment` | `d3-deployment` | `a867863` |
| `F:/project/hermes-open-swe-lab-d3-integration` | `d3-integration` | `309c6f1` |
| `F:/project/hermes-open-swe-lab-provider-live` | `provider-live` | `f7700cf` |
| `F:/project/hermes-open-swe-lab_codex` | `codex-primary-agent-policy` | `3612b526` (frozen) |

## 4. Verification of the three "known" SHAs (re-checked, not trusted)

| SHA | `cat-file -t` | Ancestor of d4 (`710d88e` base)? | Ancestor of d3-integration (`309c6f1`)? | Ancestor of codex (`3612b526`)? | Meaning |
|-----|--------------|-----------------------------------|------------------------------------------|----------------------------------|---------|
| `955962f6e5bda74b2d0eb8a3028a01cb46de9312` | commit | NO | NO | **YES** | "known local HEAD" belonged to the **codex lineage** (frozen), not the active branch |
| `309c6f108a50890955f787591dd09f1304bc5da7` | commit | NO | **YES (= itself)** | YES | = d3-integration HEAD = remote acceptance-record commit ✓ |
| `932dcd7ef9d584955d316a3ddfca25c69f7dd6e3` | commit | NO | **YES** | YES | = `RUNTIME_CODE_SHA` (last runtime code commit) ✓ |

Conclusion: the stale clues described the **d3-integration / codex** state. The actual active work branch is **`d4-delivery-layer`**, which forked from `d3-design` (`9f711ae`), *before* `932dcd7`/`309c6f1` were added to d3-integration. The 4 D4 modules (`constants/github_app/github_client/repository`) were ported read-only (byte-identical via `git show`) from the frozen `3612b526` into `d4-delivery-layer`.

## 5. Local ↔ GitHub divergence (active branch `d4-delivery-layer`)

| Metric | Value |
|--------|-------|
| Local HEAD | `8806010` |
| Remote HEAD (`origin/d4-delivery-layer`) | `8806010` (created this session) |
| Common merge base (vs `origin/d3-design`) | `9f711ae` |
| Local ahead of `d3-design` | **4** commits: `a597531`, `710d88e`, `e505d9b`, `8806010` |
| Remote ahead | 0 |
| Uncommitted files | 0 (clean) |
| Staged files | 0 |
| Untracked files | 0 |

**Classification (after handoff): `SYNCED`** for the branch itself (local == remote). Relative to its base `d3-design`, it is `LOCAL_AHEAD` by 4 commits. Before this session the branch was `REMOTE_BRANCH_MISSING` + `LOCAL_AHEAD` (no remote counterpart); that is now resolved.

## 6. PR status (live, read-only via `gh`)

### PR #5 — `d3-integration` → `phase-1-smoke`
- State: **OPEN**, `isDraft: true`
- Head: `d3-integration` @ `309c6f108a50890955f787591dd09f1304bc5da7`
- Base: `phase-1-smoke`
- Commits: 14; `statusCheckRollup`: [] (no CI configured); `reviewDecision`: "" (none requested/recorded)
- Not modified by this task.

### PR #6 — `d4-delivery-layer` → `d3-design` (created this session)
- URL: https://github.com/yzhlx/hermes-open-swe-lab/pull/6
- State: **OPEN**, Draft
- Purpose: checkpoint of D4 controlled-delivery-layer before the reuse audit; **not for merge**
- Verification attached: `import hermes_worker.delivery` OK; `tests.test_d4_delivery` → 54 passed; secret scan clean

## 7. Is local work fully pushed?

| Branch | Pushed? | Evidence |
|--------|---------|----------|
| `d4-delivery-layer` (active work) | **YES** | `origin/d4-delivery-layer` = `8806010`; Draft PR #6 |
| `d3-integration` (PR #5) | YES | `origin/d3-integration` = `309c6f1` |
| `codex-primary-agent-policy` (`3612b526`) | **LOCAL ONLY** | intentional — frozen per repo policy ("原样保留，勿碰"); not active work to save |
| `d3-deployment` / `provider-live` | YES | have `origin/*` tracking |

**No local work was lost or overwritten.** The only writes performed were: two WIP checkpoint commits on `d4-delivery-layer` (documentation + canonical-integration report) and a new `audit/...` branch pointer. No `reset`, `clean`, `pull`, `merge`, `rebase`, `force-push`, branch deletion, or worktree deletion was executed. Protected `yzhlx/hermes-learning-os` and the cloud Hermes runtime were not touched.

## 8. Safety rules honored
- No forbidden command executed (no reset --hard / clean / pull / merge / rebase / force-push / stash / branch-or-worktree deletion).
- No auto-merge; no Auto Merge enabled.
- No access to / modification of protected `yzhlx/hermes-learning-os`.
- No secret (`.env` / `.pem` / token / key) printed or committed; secret scan clean.
