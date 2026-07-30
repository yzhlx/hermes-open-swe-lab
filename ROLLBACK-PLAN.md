# ROLLBACK-PLAN.md — MVP-0 rollback procedures

Every change is reversible and delivered via PR. `main` is never merged
automatically, so the blast radius is small. Apply the minimal rollback needed.

## 1. Lab repo implementation PR / branch

- **If the Draft PR is not yet merged:** close the PR, delete the
  `phase-1-smoke` branch (`git push origin --delete phase-1-smoke` only if
  explicitly desired; otherwise just leave it closed). `main` is untouched.
- **If any commit reached `main` (should not happen):** `git revert <sha>` on a
  new branch + PR; never force-push `main`.

## 2. Smoke-test repo

- The agent's Draft PR is **never merged** in MVP-0 → simply close it; `main`
  of the smoke-test repo remains at the seeded state.
- If a stray commit reached `main` of the smoke-test repo: `git revert` via PR.
- The CI workflow (`.github/workflows/smoke-contract.yml`) can be removed by PR
  if needed (it is protected-from-agent but owner-editable).

## 3. GitHub App

- Uninstall the App from the smoke-test repo (GitHub Settings → Applications).
- Revoke/rotate the private key (`.pem`) and webhook secret; report
  `STATUS: SECURITY_BOUNDARY_VIOLATION` if a leak is suspected.

## 4. LangSmith sandbox

- Disable/delete the sandbox snapshot; revoke the API key.
- Confirm no trace contains secrets (redaction verified by unit tests).

## 5. Relay adapter / config

- Remove `OPEN_SWE_OPENAI_*` from `/opt/hermes-open-swe-lab/.env` → the adapter
  returns `None` and Open SWE uses upstream default (opt-in, no code change).
- To disable entirely: uninstall the package (`pip uninstall hermes-open-swe-relay`).

## 6. Cloud server

- Stop the control-plane process and ngrok tunnel.
- No production Hermes component was modified, so no Hermes rollback is needed.

## Verification after rollback

- `git ls-remote` shows `main` SHAs unchanged for both repos.
- No open Draft PR is merged.
- `.env` and `github-app.pem` are absent or emptied on the server.
- A final note is appended to `OPEN-SWE-PHASE-1-RESULT.md` recording the
  rollback and its reason.
