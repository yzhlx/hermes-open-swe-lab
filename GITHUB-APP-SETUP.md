# GITHUB-APP-SETUP.md — GitHub App for the smoke-test loop

**Status:** DONE — App created & installed on **only** `yzhlx/hermes-open-swe-smoke-test`;
Installation ID obtained via read-only App auth; install scope verified; dedicated
service user `hermes-swe` created; App key landed on server; `.env` written;
server-side read-only auth verify PASS. Webhook OFF.

**Scope rule:** the App is installed on **only** `yzhlx/hermes-open-swe-smoke-test`.
Verified via the installation's allowed-repo list (installation token, read-only):
the single allowed repository is `yzhlx/hermes-open-swe-smoke-test`. No other repo
is in scope; `yzhlx/hermes-learning-os` is **not** accessible. No `SECURITY_BOUNDARY_VIOLATION`.

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

## Server-side key landing (AGENT — DONE)

Executed as user `ubuntu` over SSH (with `sudo`), targeting the dedicated
`hermes-swe` service user for ownership. Never `ubuntu`, never a Hermes user.

1. Created `hermes-swe` (system user, uid 996, `--user-group`, home
   `/var/lib/hermes-swe`, shell `/usr/sbin/nologin`, no sudo/docker).
2. `sudo install -d -m 700 -o hermes-swe -g hermes-swe /etc/hermes-open-swe-lab/secrets`
   (outside any git repo; not under `/opt/hermes-open-swe-lab`'s tracked tree).
3. `scp` `.pem` → `/tmp/github-app-private-key.pem.tmp` (`chmod 600`), then
   `sudo install -o hermes-swe -g hermes-swe -m 600` →
   `/etc/hermes-open-swe-lab/secrets/github-app-private-key.pem`; temp securely removed.
4. SHA-256 verify: **MATCH** (local file == server file; only prefix shown internally).
5. Wrote `/opt/hermes-open-swe-lab/.env` (gitignored, non-secret only, hermes-swe 600):

   ```bash
   GITHUB_APP_ID=4389778
   GITHUB_APP_PRIVATE_KEY_PATH=/etc/hermes-open-swe-lab/secrets/github-app-private-key.pem
   GITHUB_APP_INSTALLATION_ID=148886992
   ALLOWED_GITHUB_REPOS=yzhlx/hermes-open-swe-smoke-test
   # GITHUB_WEBHOOK_SECRET is NOT set — webhook stays OFF (MVP)
   ```

   The PEM **text** is never written to `.env`; the adapter bridges the path → env at runtime.

## Verification (actual results)

- App auth (read-only) → Installation ID `148886992`, account `yzhlx`. ✅
- Key landing: `/etc/hermes-open-swe-lab/secrets/github-app-private-key.pem`
  (`hermes-swe:hermes-swe`, `600`); `openssl pkey -check` → valid; SHA-256 MATCH. ✅
- Scope (read-only, via installation's allowed-repo list, **no 404 probing**):
  the single allowed repository is `yzhlx/hermes-open-swe-smoke-test`
  (total_count 1). `yzhlx/hermes-learning-os` is **not** accessible. ✅
- Server-side read-only auth verify (App JWT signed from the server key path):
  App identity valid, Install ID `148886992` valid, allowed repo list == smoke-test
  only → PASS. ✅
- Webhook: **OFF** (no webhook secret, no webhook URL configured). ✅

## Next (user action required)

- The App key is landed; the next authorization gates are **LangSmith sandbox** and
  **relay credentials** (see `LANGSMITH-SETUP.md` and `USER-ACTIONS-REQUIRED.md`).
- Webhook stays OFF until you explicitly enable it.

## Security notes

- The App private key and webhook secret are **server-side secrets**; they are
  never committed, logged, or sent to the model provider.
- If the key leaks, rotate it immediately and report
  `STATUS: SECURITY_BOUNDARY_VIOLATION`.
