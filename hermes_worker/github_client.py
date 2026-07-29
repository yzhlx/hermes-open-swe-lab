"""GitHub repository operations client (D3 requirement A/C).

Abstracts clone / push / create-draft-PR / get-CI-status / add-label so the
orchestrator runs fully offline against :class:`FakeGitHubClient` and, in
production, against a real client backed by ``gh`` + ``git`` (NOT_TESTED from
this environment — needs credentials + network + the smoke-test repo).

Hard boundary: automation clients expose no merge operation. Merging remains a
user-only action outside this module.
"""
from __future__ import annotations

import os
import json
import subprocess
import urllib.error
import urllib.request
from typing import Callable, Optional

from .constants import ALLOWED_GITHUB_REPOS


class GitHubClientError(Exception):
    pass


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
    def get_ci_status(
        self,
        pr_number: int,
        expected_head: Optional[str] = None,
    ) -> str:
        raise NotImplementedError
    def add_label(self, pr_number: int, label: str) -> None:
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

    def get_ci_status(
        self,
        pr_number: int,
        expected_head: Optional[str] = None,
    ) -> str:
        if (
            expected_head is not None
            and self.prs.get(pr_number, {}).get("head_sha") != expected_head
        ):
            return "pending"
        return self._ci.get(pr_number, "pending")

    def add_label(self, pr_number: int, label: str) -> None:
        self._labels.setdefault(pr_number, set()).add(label)

    def has_label(self, pr_number: int, label: str) -> bool:
        return label in self._labels.get(pr_number, set())

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

    def get_ci_status(
        self,
        pr_number: int,
        expected_head: Optional[str] = None,
    ) -> str:
        if expected_head is not None:
            raise GitHubClientError(
                "legacy_ci_head_binding_unsupported"
            )
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

    def add_label(self, pr_number: int, label: str) -> None:
        subprocess.run([self.gh, "pr", "edit", str(pr_number), "--add-label", label],
                       check=True, capture_output=True, text=True)

class GitHubRestClient:
    """Host-side GitHub REST adapter with ephemeral broker credentials."""

    def __init__(
        self,
        api_base: str = "https://api.github.com",
        *,
        repo: Optional[str] = None,
        token_provider: Optional[Callable[[], str]] = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.repo = repo
        self._token_provider = token_provider

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hermes-host-worker",
            "Content-Type": "application/json",
        }

    def _request_json(
        self,
        path: str,
        *,
        token: str,
        method: str = "GET",
        payload: Optional[dict] = None,
        error_prefix: str,
    ) -> dict:
        request = urllib.request.Request(
            f"{self.api_base}{path}",
            data=(
                json.dumps(payload).encode("utf-8")
                if payload is not None
                else None
            ),
            method=method,
            headers=self._headers(token),
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise GitHubClientError(
                f"{error_prefix}_http_{exc.code}"
            ) from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _scheduler_context(self) -> tuple[str, str]:
        if self.repo not in ALLOWED_GITHUB_REPOS:
            raise GitHubClientError("repo_not_allowed")
        if self._token_provider is None:
            raise GitHubClientError("github_token_provider_unavailable")
        token = self._token_provider()
        if not token:
            raise GitHubClientError("github_token_unavailable")
        return self.repo, token

    def create_draft_pr(self, repo: str, branch: str, base: str, title: str,
                        body: str, token: str) -> dict:
        if repo not in ALLOWED_GITHUB_REPOS:
            raise GitHubClientError("repo_not_allowed")
        result = self._request_json(
            f"/repos/{repo}/pulls",
            token=token,
            method="POST",
            payload={
                "title": title,
                "head": branch,
                "base": base,
                "body": body,
                "draft": True,
            },
            error_prefix="draft_pr_create_failed",
        )
        return {
            "number": result["number"],
            "url": result.get("html_url"),
            "draft": bool(result.get("draft", True)),
        }

    def get_pr(self, pr_number: int) -> dict:
        repo, token = self._scheduler_context()
        result = self._request_json(
            f"/repos/{repo}/pulls/{int(pr_number)}",
            token=token,
            error_prefix="pr_read_failed",
        )
        head = result.get("head") or {}
        return {
            "number": result.get("number", int(pr_number)),
            "head_sha": head.get("sha"),
            "draft": bool(result.get("draft", False)),
            "merged": bool(result.get("merged", False)),
            "state": result.get("state"),
        }

    def get_ci_status(
        self,
        pr_number: int,
        expected_head: Optional[str] = None,
    ) -> str:
        repo, token = self._scheduler_context()
        head_sha = expected_head
        if not head_sha:
            pr = self._request_json(
                f"/repos/{repo}/pulls/{int(pr_number)}",
                token=token,
                error_prefix="pr_read_failed",
            )
            head_sha = (pr.get("head") or {}).get("sha")
        if not head_sha:
            return "pending"
        result = self._request_json(
            f"/repos/{repo}/commits/{head_sha}/check-runs",
            token=token,
            error_prefix="ci_read_failed",
        )
        checks = result.get("check_runs") or []
        if not checks:
            return "pending"
        failure_conclusions = {
            "failure", "cancelled", "timed_out", "action_required",
            "startup_failure", "stale",
        }
        if any(
            str(check.get("conclusion") or "").lower()
            in failure_conclusions
            for check in checks
        ):
            return "failure"
        successful = {"success", "neutral", "skipped"}
        if all(
            str(check.get("status") or "").lower() == "completed"
            and str(check.get("conclusion") or "").lower() in successful
            for check in checks
        ):
            return "success"
        return "pending"

    def add_label(self, pr_number: int, label: str) -> None:
        repo, token = self._scheduler_context()
        self._request_json(
            f"/repos/{repo}/issues/{int(pr_number)}/labels",
            token=token,
            method="POST",
            payload={"labels": [label]},
            error_prefix="label_add_failed",
        )
