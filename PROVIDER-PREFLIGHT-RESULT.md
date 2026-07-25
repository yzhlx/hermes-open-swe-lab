# PROVIDER-PREFLIGHT-RESULT.md — Open SWE relay provider preflight

**Script:** `scripts/provider_preflight.py`
**Spec:** MVP-0 Step 7 · AGENTS.md Section 10 & 12

## Checks (P1–P6)

| ID | Check | Why it matters |
| --- | --- | --- |
| P1 | basic response | provider is reachable and answers |
| P2 | single tool call | Open SWE needs at least one tool call |
| P3 | consecutive tool calls | multi-turn tool use is required |
| P4 | streaming | some paths stream tokens |
| P5 | long context | large prompts must not error |
| P6 | structured error + secret redaction | safe failure + no secret leak |

> **P2 or P3 failure ⇒ `STATUS: PROVIDER_INCOMPATIBLE`** and GitHub write tests
> are stopped (no Draft PR is opened against the smoke-test repo).

## Current status

| Item | Status | Evidence |
| --- | --- | --- |
| Script authored & structurally valid | DONE | `python scripts/provider_preflight.py --json` → `{"status":"NOT_TESTED",...}` exit 2 |
| Live P1–P6 run | **NOT_TESTED** | no relay credentials present in this sandbox |
| Secret redaction in error path | DONE (code) | unit-tested in `tests/test_redact.py` |

### Evidence — no-config run (this sandbox)

```bash
$ python scripts/provider_preflight.py
PROVIDER_PREFLIGHT: NOT_TESTED — no relay configured (OPEN_SWE_OPENAI_BASE_URL is empty).
$ echo $?
2
```

No key, no `Authorization` header, and no model name is emitted. The script
refuses to proceed without credentials and never contacts any endpoint.

## What will happen once relay credentials exist

1. `OPEN_SWE_OPENAI_*` are written to `/opt/hermes-open-swe-lab/.env` on the
   cloud control plane (user action, Step 9).
2. The control plane runs `python scripts/provider_preflight.py`.
3. If P1–P6 pass → `STATUS: PASS`, proceed to GitHub write tests.
4. If P2 or P3 fail → `STATUS: PROVIDER_INCOMPATIBLE`, stop, preserve evidence.
5. If P4/P5/P6 fail → reported; treated as provider limitation, not a hard stop
   unless it breaks Open SWE's required behavior.

## Notes

- The script talks directly to the relay (it is the verification harness, not the
  production adapter). The production path uses `hermes_open_swe_relay`.
- All error text and snippets pass through `redact()`; keys/headers are never
  printed.
