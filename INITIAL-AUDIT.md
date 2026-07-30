# INITIAL-AUDIT.md — Hermes Open SWE Lab (MVP-0)

**Audit type:** Read-only pre-implementation audit (Step 3 of the MVP-0 plan)
**Audit date:** 2026-07-25
**Auditor:** Hermes Eng Auto Architect
**Branch under audit:** `phase-1-smoke` (based on `main` @ `04ed8cf`)
**Guardrails commit:** `8572f03` (chore: add repository agent guardrails)

---

## 1. Local workspace status

| Item | Result |
| --- | --- |
| Working directory | `F:\project\hermes-open-swe-lab` |
| Current branch | `phase-1-smoke` (created from `main`) |
| Uncommitted tracked changes | none |
| Untracked but ignored | `.workbuddy/` (agent memory), `hermes-eng-auto-architect.zip` (exported expert package) — both covered by `.gitignore` |
| Dirty/secret risk | none in tracked tree |

Evidence: `git status` shows only ignored untracked entries; `git diff` is empty on tracked files.

## 2. Remote repository check

| Item | Result |
| --- | --- |
| `origin` URL | `https://github.com/yzhlx/hermes-open-swe-lab.git` |
| Reachability | OK — `git ls-remote --heads origin` returned `refs/heads/main` (`04ed8cf…`) |
| `main` protection | branch exists; we never push to it (see Section 6 of AGENTS.md) |

## 3. Current branch

`phase-1-smoke` — purpose-specific branch for MVP-0 Phase-1 work. All subsequent
changes land here. `main` is untouched and must remain so until PR review/approval.

## 4. Existing AGENTS.md / CLAUDE.md / project docs

| File | Status | Note |
| --- | --- | --- |
| `AGENTS.md` | present, 572 lines | complete and strict; satisfies every Step-1 required constraint (verified by keyword scan) |
| `CLAUDE.md` | absent | not required |
| `README.md` | present | rewritten from UTF-16 stub to UTF-8 (guardrails commit `8572f03`) |

No nested `AGENTS.md` exists that could relax root rules (only the root file is present).

## 5. Cloud server connection conditions

| Item | Result |
| --- | --- |
| Cloud server (2 CPU / 4 GB / 60 GB / Ubuntu 24.04) | remote, separate machine |
| Direct access from this WorkBuddy sandbox | **NONE** — no SSH/credential to the cloud host from this environment |
| Implication | Live control-plane operations (GitHub webhook, LangGraph control, LangSmith sandbox, independent reviewer loop) **cannot be executed from this sandbox** |

These live steps are deferred to after the authorization gate (Step 9) and are
marked `NOT_TESTED` until the cloud server (or equivalent authorized access) runs them.
This is a connectivity/authorization boundary, not a code defect.

## 6. GitHub permission check

| Item | Result |
| --- | --- |
| `gh` CLI | installed (v2.96.0) |
| Authenticated account | `yzhlx` (token in keyring, scopes: `gist`, `read:org`, `repo`) |
| Can push branches / open PRs / create repos | yes |
| Can merge / bypass protection | no (intentionally; merges are user-approved only) |
| GitHub App (Step 9) | **not created yet** → user action required |

## 7. Both experiment repositories

| Repository | Exists? | State |
| --- | --- | --- |
| `yzhlx/hermes-open-swe-lab` | yes | non-empty; this repo |
| `yzhlx/hermes-open-swe-smoke-test` | **no** at audit time | created private during Task 5 (see INSTALLATION-STATUS) |

Per Step 2, the stop condition ("repo missing AND no permission to create") was
**not** met: `yzhlx` has `repo` scope and owns the account, so the smoke-test repo
is created (private) rather than blocking. This is reported transparently at the
authorization gate.

## 8. Protected repository not referenced for operations

`yzhlx/hermes-learning-os` is **not** cloned, fetched, or operated on. The only
matches for the string are the protective policy statements inside `AGENTS.md`
(Section 4) and `README.md`, which exist precisely to forbid access. This is
correct and required, not a violation.

## 9. Open SWE upstream commit reachability

| Item | Result |
| --- | --- |
| Repository | `langchain-ai/open-swe` (public) |
| Pinned commit | `ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6` |
| Reachable? | **yes** — `gh api repos/langchain-ai/open-swe/commits/ed12bb8…` returned the SHA |
| Upstream `main` movement | `main` was pushed 2026-07-24 (still active) → confirms we must stay pinned, not float |

## 10. Uncommitted changes / potential secrets

| Item | Result |
| --- | --- |
| Tracked secret files (`.pem`, `.key`, `.env`, `secrets/`, `credentials/`) | none |
| `.env` present | no |
| Staged diff credential scan | clean (guardrails commit adds only `.gitignore` + `README.md`) |

---

## Audit verdict

- No security boundary is weakened.
- No protected system is accessed.
- The approved upstream commit is reachable and pinned.
- The only external dependency for live testing is the authorization gate (Step 9)
  plus cloud-server access, both of which are user-controlled.

**Go to proceed with MVP-0 implementation prep (Steps 4–8).**
Live execution items are tracked as `NOT_TESTED` pending authorization.
