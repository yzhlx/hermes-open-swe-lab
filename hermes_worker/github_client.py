"""GitHub repository operations client (D3 requirement A/C).

Abstracts clone / push / create-draft-PR / get-CI-status / add-label so the
orchestrator runs fully offline against :class:`FakeGitHubClient` and, in
production, against a real client backed by ``gh`` + ``git`` (NOT_TESTED from
this environment — needs credentials + network + the smoke-test repo).

Hard boundary: the automation NEVER calls merge. :meth:`FakeGitHubClient.merge_pr`
exists only so the test can assert the orchestrator never invokes it.
"""
from __future__ import annotations

import os
import json
import subprocess
import urllib.error
import urllib.request
from typing import Optional

from .constants import ALLOWED_GITHUB_REPOS, PROTECTED_REPOS


class GitHubClientError(Exception):
    pass


def normalize_repo(full_name: Optional[str]) -> Optional[str]:
    """Canonical ``owner/repo`` form for EXACT-match allowlist checks.

    Lower-cases, trims whitespace, and strips a trailing ``.git``. It does NOT
    do prefix/substring matching — callers test set-membership of the returned
    value, so ``yzhlx/hermes-open-swe-smoke-test-evil`` is never a match for
    ``yzhlx/hermes-open-swe-smoke-test``.
    """
    if full_name is None:
        return None
    s = full_name.strip().lower()
    if not s:
        return None
    if s.endswith(".git"):
        s = s[:-4]
    return s


def require_allowed_repo(full_name: Optional[str],
                         allowed=None,
                         protected=None) -> str:
    """Fail-closed guard for any gh/GitHub-backed read or write (PB-6).

    Raises :class:`GitHubClientError` with ``repo_not_allowed`` when:
      * the repo is not configured (``None``/empty) -> production fail-closed;
      * the repo is in ``PROTECTED_REPOS`` -> explicit deny (defense-in-depth);
      * the repo is not an exact, normalized member of ``allowed``.

    Otherwise returns the normalized ``owner/repo`` string. The error message
    never references a token, and no GitHub call is made before this returns.
    """
    if allowed is None:
        allowed = ALLOWED_GITHUB_REPOS
    if protected is None:
        protected = PROTECTED_REPOS
    norm = normalize_repo(full_name)
    if norm is None:
        raise GitHubClientError("repo_not_allowed")
    if norm in {normalize_repo(p) for p in protected}:
        raise GitHubClientError("repo_not_allowed")
    if norm not in {normalize_repo(a) for a in allowed}:
        raise GitHubClientError("repo_not_allowed")
    return norm


class GitHubClient:
    """Interface implemented by both Fake and real clients."""
    def clone(self, full_name: str, dest: str) -> str:
        raise NotImplementedError
    def push_branch(self, full_name: str, branch: str, head_sha: str,
                    token: Optional[str] = None) -> None:
        raise NotImplementedError
    def create_draft_pr(self, full_name: str, branch: str, title: str,
                        body: str = "") -> dict:
        raise NotImplementedError
    def get_pr(self, pr_number: int) -> dict:
        raise NotImplementedError
    def set_ci_status(self, pr_number: int, status: str) -> None:
        raise NotImplementedError
    def get_ci_status(self, pr_number: int, repo: Optional[str] = None) -> str:
        raise NotImplementedError
    def add_label(self, pr_number: int, label: str,
                  repo: Optional[str] = None) -> None:
        raise NotImplementedError
    def merge_pr(self, pr_number: int) -> None:
        raise NotImplementedError


class FakeGitHubClient(GitHubClient):
    """In-memory GitHub simulation for offline closed-loop tests.

    The test harness drives CI PASS/FAIL and inspects PR/label state to assert
    the D3 invariants. Clone records a marker file; push records branch head;
    create_draft_pr records a draft PR and seeds CI=pending.
    """
    def __init__(self, clock=None):
        self.repos: dict = {}
        self.prs: dict = {}
        self._pr_seq = 0
        self._ci: dict = {}
        self._labels: dict = {}
        self.allowed = set(ALLOWED_GITHUB_REPOS)
        self.merge_called = False

    def _check_repo(self, full_name: str):
        if full_name not in self.allowed:
            raise GitHubClientError("repo_not_allowed")

    def ensure_repo(self, full_name: str):
        if full_name not in self.repos:
            self.repos[full_name] = {"branches": {}, "default_branch": "main"}
        return self.repos[full_name]

    def clone(self, full_name: str, dest: str) -> str:
        self._check_repo(full_name)
        self.ensure_repo(full_name)            # register the repo so push/PR work
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, ".git-cloned"), "w", encoding="utf-8") as f:
            f.write(full_name)
        return dest

    def push_branch(self, full_name: str, branch: str, head_sha: str,
                    token: Optional[str] = None) -> None:
        self._check_repo(full_name)
        self.repos[full_name]["branches"][branch] = head_sha

    def create_draft_pr(self, full_name: str, branch: str, title: str,
                        body: str = "") -> dict:
        self._check_repo(full_name)
        self._pr_seq += 1
        pr = {
            "number": self._pr_seq,
            "full_name": full_name,
            "branch": branch,
            "head_sha": self.repos[full_name]["branches"].get(branch),
            "draft": True,
            "merged": False,
            "state": "open",
            "title": title,
        }
        self.prs[pr["number"]] = pr
        self._ci[pr["number"]] = "pending"
        self._labels[pr["number"]] = set()
        return dict(pr)

    def get_pr(self, pr_number: int) -> dict:
        return dict(self.prs[pr_number])

    def set_ci_status(self, pr_number: int, status: str) -> None:
        self._ci[pr_number] = status

    def get_ci_status(self, pr_number: int, repo: Optional[str] = None) -> str:
        # PB-6: enforce allowlist when a repo is supplied. Offline callers that
        # omit repo keep their existing behavior (trusted in-memory harness).
        if repo is not None:
            require_allowed_repo(repo)
        return self._ci.get(pr_number, "pending")

    def add_label(self, pr_number: int, label: str,
                  repo: Optional[str] = None) -> None:
        if repo is not None:
            require_allowed_repo(repo)
        self._labels.setdefault(pr_number, set()).add(label)

    def has_label(self, pr_number: int, label: str) -> bool:
        return label in self._labels.get(pr_number, set())

    def merge_pr(self, pr_number: int) -> None:
        # Intentionally present ONLY so the test can assert it is never called.
        self.merge_called = True
        raise GitHubClientError("merge_not_permitted_for_automation")

    # --- test inspection helpers ---
    def ci_map(self) -> dict:
        return dict(self._ci)
    def label_map(self) -> dict:
        return {k: set(v) for k, v in self._labels.items()}
    def pr_numbers(self):
        return list(self.prs.keys())


class RealGitHubClient(GitHubClient):
    """Legacy D3 client retained for CI/review compatibility.

    Repository preparation, push, and Draft PR creation fail closed here. The
    host Codex architecture uses ``RepositoryPreparer``, ``HostGitOperations``,
    and ``GitHubRestClient`` so it cannot fall back to clone or ``gh auth``.
    """
    def __init__(self, gh_bin: str = "gh", git_bin: str = "git",
                 default_branch: str = "main"):
        self.gh = gh_bin
        self.git = git_bin
        self.default_branch = default_branch

    def clone(self, full_name: str, dest: str) -> str:
        raise GitHubClientError(
            "git_clone_disabled_use_repository_preparer"
        )

    def push_branch(self, full_name: str, branch: str, head_sha: str,
                    token: Optional[str] = None) -> None:
        raise GitHubClientError(
            "legacy_push_disabled_use_host_git_operations"
        )

    def create_draft_pr(self, full_name: str, branch: str, title: str,
                        body: str = "") -> dict:
        raise GitHubClientError(
            "legacy_pr_create_disabled_use_github_rest_client"
        )

    def get_ci_status(self, pr_number: int, repo: Optional[str] = None) -> str:
        # PB-6: enforce repo allowlist BEFORE any gh call (fail-closed). An
        # unallowed / protected / unconfigured repo raises and never reaches gh.
        require_allowed_repo(repo)
        out = subprocess.run(
            [self.gh, "pr", "checks", str(pr_number), "--json", "state"],
            check=True, capture_output=True, text=True)
        import json
        states = [c.get("state") for c in json.loads(out.stdout)]
        if any(s == "FAILURE" for s in states):
            return "failure"
        if all(s == "SUCCESS" for s in states) and states:
            return "success"
        return "pending"

    def set_ci_status(self, pr_number: int, status: str) -> None:  # pragma: no cover
        raise NotImplementedError("CI status is set by GitHub Actions, not the client")

    def add_label(self, pr_number: int, label: str,
                  repo: Optional[str] = None) -> None:
        # PB-6: enforce repo allowlist BEFORE any gh call (fail-closed).
        require_allowed_repo(repo)
        subprocess.run([self.gh, "pr", "edit", str(pr_number), "--add-label", label],
                       check=True, capture_output=True, text=True)

    def merge_pr(self, pr_number: int) -> None:
        # Never called by the automation. User merges via UI/gh.
        raise GitHubClientError("merge_not_permitted_for_automation")
class GitHubRestClient:
    """Minimal host-side GitHub client for Draft PR creation.

    The Installation Token is accepted only in memory and sent as an HTTP
    header. It is never placed in a command, URL, Git config, or returned data.
    """

    def __init__(self, api_base: str = "https://api.github.com"):
        self.api_base = api_base.rstrip("/")

    def create_draft_pr(self, repo: str, branch: str, base: str, title: str,
                        body: str, token: str) -> dict:
        if repo not in ALLOWED_GITHUB_REPOS:
            raise GitHubClientError("repo_not_allowed")
        payload = json.dumps({
            "title": title,
            "head": branch,
            "base": base,
            "body": body,
            "draft": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.api_base}/repos/{repo}/pulls",
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "hermes-host-worker",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GitHubClientError(
                f"draft_pr_create_failed_http_{exc.code}"
            ) from exc
        return {
            "number": result["number"],
            "url": result.get("html_url"),
            "draft": bool(result.get("draft", True)),
        }
