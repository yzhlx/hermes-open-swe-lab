# Phase B Production Wiring — Completion Report

## Status

Implementation complete for the requested code-level production wiring scope.
Live Docker and relay acceptance checks remain `NOT_TESTED`.

Branch: `phase-b/production-wiring-20260801` (based on
`monitor/phase-b-integration-20260801`). No push, pull request, merge, or
deployment was performed.

## Scope delivered

- Added explicit, opt-in backend controls:
  - `HERMES_SANDBOX_BACKEND=echo|docker` (default `echo`)
  - `HERMES_AGENT_BACKEND=fake|relay` (default `fake`)
  - `HERMES_REVIEWER_LLM=1` (default `0` / disabled)
- Wired both the polling worker and D3 closed-loop worker to select those
  backends when callers do not inject test doubles.
- Replaced the polling worker's hard-coded echo/write path with the selected
  `AgentRunner`, emitting redacted `sandbox_create`, `execute`, `write_file`,
  and `agent_complete` events.
- Preserved an agent-provided `AgentEvidence.commit_sha`; when absent, created
  a real local commit from the agent's sandbox edits.
- Kept `RelayAgentRunner` on Chat Completions (`use_responses=False`) and made
  its model edit flow rely on the real-commit fallback instead of inventing a
  SHA.
- Added the optional reviewer relay path. It verifies the repository allowlist
  before any model call, accepts only standalone `APPROVE` or
  `REQUEST_CHANGES`, and falls back to deterministic rules for disabled,
  unavailable, or malformed relay responses.

## Changed files

- `hermes_worker/worker.py`
- `hermes_worker/agent_runner.py`
- `hermes_worker/reviewer.py`
- `hermes_worker/scheduler.py`
- `hermes_worker/constants.py`
- `tests/test_production_wiring.py`
- `deliverables/gstack/production-wiring-20260801.md`

Frozen files were not changed: `delivery.py`, `db.py`, `control_plane.py`,
`github_client.py`, `release_delivery_coordinator.py`, and `tests/deployment/`.

## Verification evidence

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_production_wiring.py tests/test_d1_offline.py tests/test_pb4_coding_worker_handoff.py tests/test_d3_closed_loop.py tests/test_pb6_github_repo_allowlist.py` | Exit 0; 51 passed |
| `.venv/bin/pytest -q --ignore=tests/deployment` | Exit 0; full non-deployment suite passed |
| `.venv/bin/pytest --collect-only -q --ignore=tests/deployment \| awk -F': ' '/^tests\\// {sum += $2} END {print sum " tests collected"}'` | Exit 0; 236 tests collected |
| `git diff --check` | Exit 0; no whitespace errors |

The new tests cover Docker selection through a Docker-shaped fake backend,
relay-runner selection with an injected fake relay, reviewer LLM approval,
reviewer relay-error fallback, and the echo/fake default behavior.

## Security implications

- Docker is selected only explicitly; there is no local-host execution fallback.
- Relay paths are disabled by default and force `use_responses=False`.
- Worker event commands and output remain redacted before transmission.
- Reviewer repository allowlist enforcement occurs before relay construction or
  invocation. The relay-review prompt is redacted.
- No credentials, `.env` values, or production systems were accessed or stored.

## Known limitations

- Live Docker and relay execution are `NOT_TESTED`; unit coverage uses isolated
  fakes by design and requires no daemon or credentials.
- A live relay configuration is required only when a relay backend is explicitly
  selected; missing configuration returns a clear construction error.

## Rollback

Set or leave the environment at its defaults:

```text
HERMES_SANDBOX_BACKEND=echo
HERMES_AGENT_BACKEND=fake
HERMES_REVIEWER_LLM=0
```

Or revert the single local commit produced for this delivery after review.

## User actions still required

None for offline behavior. A real closed loop additionally requires approved
Docker sandbox access, relay credentials entered directly into approved secret
storage, and the existing GitHub App/sandbox authorization process.
