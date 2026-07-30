# INSTALLATION-PLAN.md — deploying the MVP-0 control plane

**Target:** the cloud server (2 CPU / 4 GB / 60 GB / Ubuntu 24.04)
**Rule:** the server is control-plane only; no target code, no browsers, no
large builds on the host.

## Prerequisites (user-provided, see USER-ACTIONS-REQUIRED)

- SSH access to the cloud server.
- GitHub App (App ID, Installation ID, private key `.pem`, webhook secret) —
  installed only on the smoke-test repo.
- LangSmith API key + sandbox snapshot + project.
- Relay Base URL / model / key (written to `.env`, not committed).
- `workflow` scope on the `gh` token (to push the CI workflow to the smoke-test
  repo), or the user pushes it.

## Layout on the server

```text
/opt/hermes-open-swe-lab/
  .env                      # secrets (gitignored, server-side only)
  github-app.pem            # GitHub App private key (gitignored)
  control-plane/            # webhook receiver + LangGraph orchestrator
  open-swe-checkout/        # pinned open-swe @ ed12bb8… (in sandbox, not host)
```

## Steps (ordered)

1. Provision the server; confirm free memory >> 1.51 GB (Hermes baseline) so
   MVP-0 does not threaten existing Hermes health.
2. Install Docker (for the LangSmith/local sandbox bridge if used) and Python 3.11.
3. Clone `yzhlx/hermes-open-swe-lab` (this repo) to `/opt/hermes-open-swe-lab`.
4. Write `.env` from the user-provided values (placeholders → real). Never commit.
5. Install the relay adapter: `pip install -e '.[relay]'`.
6. Run provider preflight: `python scripts/provider_preflight.py` → expect PASS.
   (P2/P3 fail ⇒ `STATUS: PROVIDER_INCOMPATIBLE`, stop.)
7. Start the webhook receiver (ngrok for MVP to expose the endpoint).
8. Configure the GitHub App webhook URL to the ngrok/public endpoint.
9. Deploy the LangSmith sandbox snapshot; set `SANDBOX_TYPE=langsmith`.
10. Trigger the loop with a real Issue on the smoke-test repo.
11. Watch LangSmith traces + CI; collect evidence for OPEN-SWE-PHASE-1-RESULT.

## Health checks (continuous)

- Existing Hermes service memory < ~3.5 GB (leave headroom under 4 GB).
- Control-plane process alive; webhook receiving pings.
- No `SANDBOX_TYPE=local` anywhere.
- Concurrency counters at 1/1/1.

If Hermes health is at risk → `STATUS: SERVER_RESOURCE_LIMIT` and stop new work.
