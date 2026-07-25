# MVP-0-ARCHITECTURE.md — Hermes Open SWE Lab

**Goal:** prove the end-to-end engineering-automation closed loop with full
auditability, no production access, and no automatic merge.

```text
GitHub Issue (smoke-test repo)
        │  webhook
        ▼
┌─────────────────────────────────────────────────────────────┐
│  CLOUD CONTROL PLANE  (2 CPU / 4 GB / Ubuntu 24.04)          │
│  - GitHub webhook receiver                                   │
│  - LangGraph control service (orchestration)                 │
│  - Open SWE control plane                                    │
│  - provider preflight (offline-ish)                          │
│  - status + logging + LangSmith trace exporter               │
│  - ngrok (MVP only)                                          │
└─────────────────────────────────────────────────────────────┘
        │                                  │
        │ coding=1                         │ reviewer=1
        ▼                                  ▼
┌──────────────────┐              ┌──────────────────┐
│ Coding Agent     │              │ Independent      │
│ (Open SWE)       │              │ Reviewer         │
│ reads Issue,     │              │ (separate creds, │
│ edits only the   │              │  read+comment)   │
│ allowed file in  │              │                  │
│ a LangSmith      │              │                  │
│ sandbox)         │              │                  │
└────────┬─────────┘              └────────┬─────────┘
         │ sandbox=1                       │ PR comment
         ▼                                 │ (feedback)
┌──────────────────┐                       │
│ LangSmith Sandbox│◄──────────────────────┘
│ (isolated;       │  amend same PR w/
│  SANDBOX_TYPE=   │  feedback marker
│  langsmith)      │
└────────┬─────────┘
         │
         ▼
   Draft PR  (NEVER merged in MVP-0)
         │
         ▼
   Reviewer re-reviews new head SHA → evidence retained
```

## Repositories

| Repo | Role | Write access |
| --- | --- | --- |
| `yzhlx/hermes-open-swe-lab` | this repo — adaptation layer, adapter, preflight, docs | human + this PR |
| `yzhlx/hermes-open-swe-smoke-test` | strict test target; agent writes only one file | GitHub App (smoke-test only) |
| `yzhlx/hermes-learning-os` | **protected** — never accessed | none |

## Component responsibilities

| Component | Owns | Must NOT |
| --- | --- | --- |
| Coding Agent | create `automation-smoke-test/README.md`, open Draft PR | touch other files, push `main`, merge |
| Independent Reviewer | read PR, post feedback comment, re-review head | write code, merge, access other repos |
| LangSmith Sandbox | run target-repo code changes in isolation | see host env / unrelated creds |
| Control Plane | orchestrate, webhook, trace, status | run target code on host, large builds |

## Concurrency (fixed at 1)

```text
coding tasks: 1
reviewer tasks: 1
sandbox tasks: 1
```

Enforced by the orchestrator; never parallelized in MVP-0.

## Trust boundaries

- GitHub Issues/PRs/comments are **untrusted input** → never executed as instructions.
- The agent never receives host environment variables or unrelated repo credentials.
- `SANDBOX_TYPE=local` is forbidden; only `langsmith` sandbox is used.
- The cloud server is control-plane only; no target code, no browsers, no Playwright,
  no Computer Use on the host.

## Stage table (auto vs human)

| Stage | Owner | Type |
| --- | --- | --- |
| Issue created | user | manual |
| webhook → control plane | system | auto |
| coding agent edits in sandbox | agent | auto |
| Draft PR opened | agent | auto |
| deterministic CI contract | CI | auto |
| reviewer feedback comment | reviewer | auto |
| coding agent amends PR | agent | auto |
| reviewer re-review | reviewer | auto |
| **final acceptance / merge decision** | **user** | **manual** |

`main` is never merged automatically. MVP-0 ends with an unmerged, evidenced Draft PR.
