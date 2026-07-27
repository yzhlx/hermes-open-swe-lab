"""GitHub App Token Broker (D3 requirement B).

Mints short-lived, per-task GitHub App Installation Tokens. Security invariants
that MUST hold (per AGENTS.md §7 and D3-IMPLEMENTATION-PLAN.md §4.4–4.8):

- The App JWT is signed server-side from ``GITHUB_APP_PRIVATE_KEY_PATH``.
- Installation Tokens are minted on demand and cached in PROCESS MEMORY only.
- A token is NEVER written to SQLite, JSONL, logs, or any file.
- Tokens are delivered ONLY to a Worker that currently HOLDS the task lease.
- Only repos in ``ALLOWED_GITHUB_REPOS`` (smoke-test) may be targeted.
- The token is purged from the broker cache immediately after delivery.
- The token is never included in any audit/evidence export.

Offline testing injects a fake ``app_api`` (exchanges JWT→token with no real
GitHub) and a fake ``jwt_signer`` (no real private key, no network). The real
path requires PyJWT + the App PEM and is NOT_TESTED from this environment.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

from .constants import ALLOWED_GITHUB_REPOS
from .constants import HOST_WORKER_ACTIVE_STATES
from .control_plane import ControlPlane, ControlPlaneError


class GitHubAppTokenBroker:
    def __init__(self, app_id: str, installation_id: str,
                 private_key_pem: Optional[str] = None,
                 jwt_signer: Optional[Callable] = None,
                 app_api: Optional["AppApiClient"] = None,
                 allowed_repos: Optional[set] = None,
                 ttl_seconds: int = 3600,
                 clock: Optional[Callable[[], float]] = None):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key_pem = private_key_pem
        self._jwt_signer = jwt_signer or self._default_jwt_signer
        self._app_api = app_api
        self.allowed_repos = set(allowed_repos) if allowed_repos else set(ALLOWED_GITHUB_REPOS)
        self.ttl_seconds = int(ttl_seconds)
        self._clock = clock or time.time
        # RLock: _jwt() may be called while the lock is already held by
        # mint_installation_token (re-entrant acquire is required, not a deadlock).
        self._lock = threading.RLock()
        self._jwt_cache: Optional[tuple] = None          # (jwt, expires_at)
        self._tok_cache: dict = {}                        # repo -> (token, expires_at)

    # ------------------------------------------------------------------ #
    # JWT (App assertion)
    # ------------------------------------------------------------------ #
    def _default_jwt_signer(self, app_id, private_key_pem, issued_at):
        try:
            import jwt  # PyJWT
        except ImportError as e:  # pragma: no cover - offline never hits this
            raise RuntimeError(
                "PyJWT is required for real JWT signing. Inject a fake "
                "jwt_signer for offline tests.") from e
        return jwt.encode(
            {"iat": int(issued_at), "exp": int(issued_at) + 60, "iss": str(app_id)},
            private_key_pem, algorithm="RS256")

    def _jwt(self) -> str:
        now = self._clock()
        with self._lock:
            if self._jwt_cache and self._jwt_cache[1] > now + 5:
                return self._jwt_cache[0]
            tok = self._jwt_signer(self.app_id, self.private_key_pem, now)
            self._jwt_cache = (tok, now + 60)
            return tok

    # ------------------------------------------------------------------ #
    # Installation Token minting + in-memory cache
    # ------------------------------------------------------------------ #
    def mint_installation_token(self, repo: str, ttl: Optional[int] = None) -> str:
        if repo not in self.allowed_repos:
            raise ControlPlaneError("repo_not_allowed")
        if self._app_api is None:
            raise RuntimeError(
                "No app_api configured. Inject a FakeAppApiClient for offline tests.")
        now = self._clock()
        ttl = ttl or self.ttl_seconds
        with self._lock:
            cached = self._tok_cache.get(repo)
            if cached and cached[1] > now + 30:       # refresh before expiry
                return cached[0]
            jwt = self._jwt()
            token = self._app_api.exchange_installation_token(
                jwt=jwt, installation_id=self.installation_id,
                repositories=[repo], ttl_seconds=ttl)
            self._tok_cache[repo] = (token, now + ttl)
            return token

    # ------------------------------------------------------------------ #
    # Lease-gated delivery (D3 requirement #7)
    # ------------------------------------------------------------------ #
    def get_token_for_job(self, cp: ControlPlane, job_id: int, worker_token: str) -> str:
        """Return a short-lived token ONLY if ``worker_token`` owns the lease.

        The token is handed back in-memory and immediately zeroized from the
        broker cache. Never persisted, never logged.
        """
        h = cp._check_token(worker_token)               # raises unknown_worker_token
        job = cp._get_job(job_id)
        if job["worker_token_hash"] != h:
            raise ControlPlaneError("job_not_owned_by_worker")
        if job["state"] not in ("claimed", *HOST_WORKER_ACTIVE_STATES):
            raise ControlPlaneError("job_not_active")
        repo = job.get("repo") or self._repo_for_job(cp, job_id)
        if not repo or repo not in self.allowed_repos:
            raise ControlPlaneError("repo_not_allowed")
        token = self.mint_installation_token(repo)
        # Zeroize immediately after delivery to the owning worker.
        with self._lock:
            self._tok_cache.pop(repo, None)
        return token

    @staticmethod
    def _repo_for_job(cp: ControlPlane, job_id: int) -> Optional[str]:
        row = cp.conn.execute(
            "SELECT repo FROM issue_tasks WHERE job_id=?", (job_id,)).fetchone()
        return row["repo"] if row else None

    # ------------------------------------------------------------------ #
    # Audit export — tokens are NEVER exported.
    # ------------------------------------------------------------------ #
    def redact_for_export(self) -> dict:
        return {"tokens": "REDACTED_NOT_EXPORTED",
                "cached_repos": sorted(self._tok_cache.keys())}


class AppApiClient:
    """Interface the broker uses to exchange a JWT for an Installation Token."""
    def exchange_installation_token(self, jwt: str, installation_id: str,
                                    repositories: list, ttl_seconds: int) -> str:
        raise NotImplementedError


class RealAppApiClient(AppApiClient):
    """Exchange App JWTs for repository-scoped Installation Tokens."""

    def __init__(self, api_base: str = "https://api.github.com"):
        self.api_base = api_base.rstrip("/")

    def exchange_installation_token(self, jwt: str, installation_id: str,
                                    repositories: list, ttl_seconds: int) -> str:
        del ttl_seconds  # GitHub controls the Installation Token lifetime.
        names = [repo.split("/", 1)[-1] for repo in repositories]
        payload = json.dumps({
            "repositories": names,
            "permissions": {
                "contents": "write",
                "metadata": "read",
                "pull_requests": "write",
            },
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}/app/installations/{installation_id}/access_tokens",
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + jwt,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "hermes-host-worker",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"GitHub Installation Token exchange failed (HTTP {exc.code})"
            ) from exc
        token = body.get("token")
        if not token:
            raise RuntimeError("GitHub Installation Token response omitted token")
        return token
class FakeAppApiClient(AppApiClient):
    """Offline stand-in: no real GitHub, no real key, deterministic fake tokens.

    Records the exchanges (installation id + repos + ttl) for assertions, but the
    returned token values are clearly fake and never persisted.
    """
    def __init__(self, clock: Optional[Callable[[], float]] = None):
        self.clock = clock or time.time
        self.exchanges: list = []

    def exchange_installation_token(self, jwt: str, installation_id: str,
                                    repositories: list, ttl_seconds: int) -> str:
        self.exchanges.append({
            "installation_id": installation_id,
            "repositories": list(repositories),
            "ttl_seconds": ttl_seconds,
            "jwt_present": bool(jwt),
        })
        seed = abs(hash((jwt, tuple(repositories), self.clock()))) % 10 ** 12
        return f"ghs_FAKE_{seed:012d}"
