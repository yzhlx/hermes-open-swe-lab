# GITHUB-APP-SETUP.md — GitHub App for the smoke-test loop

**Status:** PARTIAL — App created & installed on **only** `yzhlx/hermes-open-swe-smoke-test`;
Installation ID obtained via read-only App auth; install scope verified (lab + learning-os
are **not** in scope). **Server-side key landing is BLOCKED** on creating the dedicated
Open SWE service user (must **not** reuse the existing Hermes service user `ubuntu`).

**Scope rule:** the App is installed on **only** `yzhlx/hermes-open-swe-smoke-test`.
Verified: `GET /repos/{repo}/installation` → smoke-test `200`, lab `404`, learning-os `404`.
No `SECURITY_BOUNDARY_VIOLATION`.

> These are instructions for the user and the agent. The agent never prints, logs, or
> commits the private key, JWT, installation token, or webhook secret. Do **not** paste
> any of these values into chat, an Issue, or a PR.

## What the user did (DONE)

1. GitHub → **Settings** → **Developer settings** → **GitHub Apps** → created the App.
   - **App ID: `4389778`**
2. Permissions granted (minimum): Repository contents R/W, Pull requests R/W,
   Issues R/W, Metadata read-only.
3. Subscribed to events: Issues, Issue comments, Pull request, Pull request review comments.
4. Generated a private key `.pem` → downloaded to the local machine
   (`F:\Download\hermes-open-swe-lab-yzhlx.2026-07-24.private-key.pem`,
   1679 bytes, mtime `2026-07-25 14:59:37`).
5. Installed the App on **only** `yzhlx/hermes-open-swe-smoke-test`
   ("Only select repositories").

## Installation ID (obtained by agent, read-only App auth)

- `GET /repos/yzhlx/hermes-open-swe-smoke-test/installation` with an App JWT
  (signed locally from the private key) → **Installation ID `148886992`**
  (account `yzhlx`). No JWT / token was printed.
- This value is written to `.env` as `GITHUB_APP_INSTALLATION_ID` during server landing.

## How Open SWE reads the key (baseline finding)

Open SWE (`langchain-ai/open-swe` @ `ed12bb8…`, `agent/utils/github_app.py`) reads the
private key **only** from the env var `GITHUB_APP_PRIVATE_KEY` as PEM **text**:
`GITHUB_APP_PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")` and
`private_key = GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")`. It does **not** support a
file path.

### Minimal file-loading adapter (added)

`scripts/load_github_app_key.py` (deployment-side) reads the PEM from
`GITHUB_APP_PRIVATE_KEY_PATH` and injects it into the live process environment as
`GITHUB_APP_PRIVATE_KEY` **before** Open SWE imports `agent.utils.github_app`.

- The PEM text lives **only** in the process environment at runtime — never in `.env`,
  git, or logs.
- A configured-but-unreadable / non-PEM file fails loudly (no silent empty key).
- If `GITHUB_APP_PRIVATE_KEY_PATH` is unset → no-op, so the upstream default (reading
  `GITHUB_APP_PRIVATE_KEY` directly) is preserved.

## Server-side key landing (AGENT — BLOCKED on dedicated service user)

Do **not** proceed until a dedicated Open SWE service user exists (see blocker below).
Then, as that user (never `ubuntu`):

1. `sudo install -d -m 700 -o <svc> -g <svc> /etc/hermes-open-swe-lab/secrets`
   (outside any git repo; not under `/opt/hermes-open-swe-lab`'s tracked tree).
2. `scp` the `.pem` → `/etc/hermes-open-swe-lab/secrets/github-app-private-key.pem`,
   then `chmod 600`, `chown <svc>:<svc>`.
3. SHA-256 verify local file == server file (compare hashes; never print key content).
4. Write `/opt/hermes-open-swe-lab/.env` (gitignored, non-secret only):

   ```bash
   GITHUB_APP_ID=4389778
   GITHUB_APP_PRIVATE_KEY_PATH=/etc/hermes-open-swe-lab/secrets/github-app-private-key.pem
   GITHUB_APP_INSTALLATION_ID=148886992
   ALLOWED_GITHUB_REPOS=yzhlx/hermes-open-swe-smoke-test
   # GITHUB_WEBHOOK_SECRET is NOT set — webhook stays OFF (MVP)
   ```

   The PEM **text** is never written here; the adapter bridges the path → env at runtime.

## Verification (actual results so far)

- App auth (read-only) → Installation ID `148886992`, account `yzhlx`. ✅
- Install scope: smoke-test `200`; `yzhlx/hermes-open-swe-lab` `404`;
  `yzhlx/hermes-learning-os` `404`. ✅ (no other repo accessible)
- Webhook: **OFF** (no webhook secret, no webhook URL configured). ✅

## Blocker (user action required)

- **Dedicated Open SWE service user does not exist on the cloud server.**
  The existing Hermes runtime runs as `ubuntu` (and partially `root`); per the
  security rule we must **not** reuse `ubuntu` as the key-file owner.
- **Next user action:** create the dedicated service user (proposed name
  `hermes-swe`) on the cloud server, or tell the agent which username to use.
  After that, the agent performs the server-side key landing above.

## Security notes

- The App private key and webhook secret are **server-side secrets**; they are
  never committed, logged, or sent to the model provider.
- If the key leaks, rotate it immediately and report
  `STATUS: SECURITY_BOUNDARY_VIOLATION`.
