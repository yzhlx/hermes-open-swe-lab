# GITHUB-APP-SETUP.md — GitHub App for the smoke-test loop

**Status:** USER ACTION REQUIRED · NOT_TESTED (no App created yet)
**Scope rule:** the App must be installed on **only** `yzhlx/hermes-open-swe-smoke-test`.

> These are instructions for the user. The agent never creates the App, holds
> the private key, or reads the webhook secret. Do **not** paste any of these
> values into chat, an Issue, or a PR.

## Steps (user)

1. GitHub → top-right avatar → **Settings** → **Developer settings** →
   **GitHub Apps** → **New GitHub App**.
2. Name it (e.g. `hermes-open-swe-mvp0`). Homepage URL can be the lab repo.
3. **Webhook**:
   - For MVP, point the webhook URL at the control-plane endpoint exposed via
     `ngrok` (the cloud server prints the public URL).
   - Generate a **Webhook secret** (random 40+ char string) and store it in
     `/opt/hermes-open-swe-lab/.env` as `GITHUB_WEBHOOK_SECRET`.
4. **Permissions** (minimum):
   - Repository contents: **Read and write**
   - Pull requests: **Read and write**
   - Issues: **Read and write**
   - Metadata: **Read-only**
5. **Subscribe to events** (minimum):
   - Issues
   - Issue comments
   - Pull request
   - Pull request review comments
6. **Generate a private key** (`.pem`). Save it to
   `/opt/hermes-open-swe-lab/github-app.pem` (gitignored, server-side secret
   storage only). Note the **App ID**.
7. **Install the App** on **only** `yzhlx/hermes-open-swe-smoke-test`
   (choose "Only select repositories" → the smoke-test repo). Note the
   **Installation ID**.
8. Write the following to `/opt/hermes-open-swe-lab/.env` (placeholders shown):

   ```bash
   GITHUB_APP_ID=<App ID>
   GITHUB_APP_INSTALLATION_ID=<Installation ID>
   GITHUB_WEBHOOK_SECRET=<webhook secret>
   # github-app.pem is read from disk; its path is configured separately
   ```

## Verification (after user completes)

- The control plane can mint an installation token scoped to the smoke-test repo.
- A test webhook ping is received and logged (no secret printed).
- The App cannot act on `yzhlx/hermes-open-swe-lab` or `yzhlx/hermes-learning-os`
  (installation is scoped to the smoke-test repo only).

## Security notes

- The App private key and webhook secret are **server-side secrets**; they are
  never committed, logged, or sent to the model provider.
- If the key leaks, rotate it immediately and report
  `STATUS: SECURITY_BOUNDARY_VIOLATION`.
