"""Host Worker — GitHub Draft PR controlled delivery layer (MVP-0, D4).

This module implements the *controlled* delivery of an already-completed local
task commit into a GitHub **Draft** pull request. It is deliberately narrow:

- It does NOT run the coding agent, the sandbox, the reviewer, or Feishu.
- It does NOT introduce a new ExecutionAgent abstraction or a second agent.
- It does NOT modify the WSL Codex route, the Open SWE upstream, or the
  protected repository ``yzhlx/hermes-learning-os``.

It only turns a *validated* local commit plus an *explicit, task/repo-scoped*
authorization into:

  1. a structured, force-free ``PushPlan``;
  2. a structured, always-draft ``DraftPrRequest``;

behind a fail-closed authorization gate, a repository allowlist (with a
hard-denied protected repo), a branch policy, and an idempotency registry.

All GitHub write calls go through an injected ``GitHubClient`` so they can be
fully exercised by ``FakeGitHubClient`` (no real network, no real token). The
delivery controller itself NEVER receives or holds a GitHub credential: the
token lives only in the GitHub client, which is constructed by the Host Worker
/ Token Broker. This keeps credentials out of logs, PR bodies, exceptions, and
evidence files *by construction*.

Local git validation goes through an injected ``GitWorkspace`` (a real
``LocalGitWorkspace`` using the git CLI, or a ``FakeGitWorkspace`` in tests).

Safety boundaries honoured (see repo AGENTS.md):
- ``yzhlx/hermes-learning-os`` is always denied and is never writable, even if
  accidentally added to the allowlist.
- No real Push / PR / Merge is performed unless an explicit per-task, per-repo
  authorization is supplied; all gated paths are verified via Fake / dry-run.
"""
from __future__ import annotations

import abc
import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from .redact import redact as _redact_text


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

# Never writable through this module, even if someone adds it to the allowlist.
PROTECTED_REPOS = frozenset({"yzhlx/hermes-learning-os"})

# Default branches that must never be the *target* of a direct push.
DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master"})

# Deterministic remote-branch prefix (avoids collision with human branches).
BRANCH_PREFIX = "hermes/delivery"

MAX_BRANCH_LEN = 100

# Branch tokens that are always rejected as a push target.
_ILLEGAL_BRANCH_TOKENS = ("head", "refs", "ref", "config", "hook", "objects")

# Safe branch character set: alphanumeric, plus . _ / - ; must start alphanumeric.
_SAFE_BRANCH_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")

# Explicit, testable status / block codes returned by the controller.
AUTHORIZATION_REQUIRED = "DELIVERY_BLOCKED_AUTHORIZATION_REQUIRED"
PROTECTED_REPOSITORY = "DELIVERY_BLOCKED_PROTECTED_REPOSITORY"
REPOSITORY_NOT_ALLOWED = "DELIVERY_BLOCKED_REPOSITORY_NOT_ALLOWED"
FORCE_PUSH_NOT_ALLOWED = "DELIVERY_BLOCKED_FORCE_PUSH_NOT_ALLOWED"
WORKSPACE_MISSING = "DELIVERY_BLOCKED_WORKSPACE_MISSING"
NOT_A_GIT_REPO = "DELIVERY_BLOCKED_NOT_A_GIT_REPOSITORY"
HEAD_NOT_EXPLICIT_COMMIT = "DELIVERY_BLOCKED_HEAD_NOT_COMMIT"
WORKSPACE_DIRTY = "DELIVERY_BLOCKED_WORKSPACE_DIRTY"
COMMIT_NOT_IN_HISTORY = "DELIVERY_BLOCKED_COMMIT_NOT_IN_HISTORY"
COMMIT_EMPTY = "DELIVERY_BLOCKED_COMMIT_EMPTY"
COMMIT_CONTAINS_CREDENTIAL = "DELIVERY_BLOCKED_COMMIT_CONTAINS_CREDENTIAL"
COMMIT_SHA_MISMATCH = "DELIVERY_BLOCKED_COMMIT_SHA_MISMATCH"
BRANCH_INVALID = "DELIVERY_BLOCKED_BRANCH_INVALID"
BRANCH_PROTECTED = "DELIVERY_BLOCKED_BRANCH_PROTECTED"
CONFLICT_COMMIT_CHANGED = "DELIVERY_CONFLICT_COMMIT_CHANGED"
DUPLICATE_SUPPRESSED = "DELIVERY_DUPLICATE_SUPPRESSED"
PUSH_FAILED = "DELIVERY_PUSH_FAILED"
PR_CREATION_FAILED = "DELIVERY_PR_CREATION_FAILED"
DRY_RUN_NO_WRITE = "DELIVERY_DRY_RUN_NO_WRITE"
COMPLETED = "DELIVERY_COMPLETED"


class DeliveryState:
    """State machine for a single delivery attempt.

    Discipline (per repo AGENTS.md): none of the blocked/failed states may
    transition into COMPLETED. COMPLETED / PR_CREATED are the only success
    terminals; the rest are explicit, testable block/fail states.
    """

    NOT_STARTED = "not_started"
    PLAN_GENERATED = "plan_generated"
    AUTHORIZATION_REQUIRED = "authorization_required"
    BLOCKED = "blocked"
    PUSHING = "pushing"
    PUSHED = "pushed"
    CREATING_PR = "creating_pr"
    PR_CREATED = "pr_created"
    PR_CLOSED = "pr_closed"
    COMMIT_CHANGED = "commit_changed"
    DUPLICATE_DETECTED = "duplicate_detected"
    PUSH_FAILED = "push_failed"
    PR_FAILED = "pr_failed"
    DRY_RUN = "dry_run"
    COMPLETED = "completed"

    # Map a status code to its terminal-ish state (for the failure states).
    _CODE_TO_STATE = {
        AUTHORIZATION_REQUIRED: AUTHORIZATION_REQUIRED,
        PROTECTED_REPOSITORY: BLOCKED,
        REPOSITORY_NOT_ALLOWED: BLOCKED,
        FORCE_PUSH_NOT_ALLOWED: BLOCKED,
        WORKSPACE_MISSING: BLOCKED,
        NOT_A_GIT_REPO: BLOCKED,
        HEAD_NOT_EXPLICIT_COMMIT: BLOCKED,
        WORKSPACE_DIRTY: BLOCKED,
        COMMIT_NOT_IN_HISTORY: BLOCKED,
        COMMIT_EMPTY: BLOCKED,
        COMMIT_CONTAINS_CREDENTIAL: BLOCKED,
        COMMIT_SHA_MISMATCH: BLOCKED,
        BRANCH_INVALID: BLOCKED,
        BRANCH_PROTECTED: BLOCKED,
        CONFLICT_COMMIT_CHANGED: COMMIT_CHANGED,
        DUPLICATE_SUPPRESSED: DUPLICATE_DETECTED,
        PUSH_FAILED: PUSH_FAILED,
        PR_CREATION_FAILED: PR_FAILED,
        DRY_RUN_NO_WRITE: DRY_RUN,
        COMPLETED: COMPLETED,
    }

    @classmethod
    def from_code(cls, code: str) -> str:
        return cls._CODE_TO_STATE.get(code, cls.BLOCKED)


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------

@dataclass
class DeliveryAuthorization:
    """Explicit, task+repo-scoped grant.

    This object MUST be supplied by the caller. It is NEVER inferred from an
    environment variable, a config default, or any Coding-Agent text. The grant
    value is a fixed, non-default constant so it cannot be accidentally enabled
    by a truthy string such as ``"true"`` / ``"auto"`` / ``"default"``.
    """

    task_id: str
    repository: str
    grant: str

    REQUIRED_GRANT = "GRANT_PUSH_AND_DRAFT_PR"

    def is_valid_for(self, task_id: str, repository: str) -> bool:
        if not self.grant:
            return False
        return (self.task_id == task_id
                and self.repository == repository
                and self.grant == self.REQUIRED_GRANT)


@dataclass
class PushPlan:
    repository: str
    local_branch: str
    local_commit_sha: str
    remote_branch: str
    remote_name: str
    force: bool = False
    authorization_status: str = "required"
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "local_branch": self.local_branch,
            "local_commit_sha": self.local_commit_sha,
            "remote_branch": self.remote_branch,
            "remote_name": self.remote_name,
            "force": self.force,
            "authorization_status": self.authorization_status,
            "dry_run": self.dry_run,
        }


@dataclass
class DraftPrRequest:
    repository: str
    base_branch: str
    head_branch: str
    title: str
    body: str
    draft: bool = True
    task_id: Optional[str] = None
    issue_number: Optional[int] = None
    commit_sha: str = ""
    test_summary: str = ""
    security_summary: str = ""

    def __post_init__(self) -> None:
        # Hard gate: a Draft PR request from this layer is ALWAYS a draft,
        # regardless of what the caller passed in ``draft``.
        self.draft = True

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "base_branch": self.base_branch,
            "head_branch": self.head_branch,
            "title": self.title,
            "body": self.body,
            "draft": self.draft,
            "task_id": self.task_id,
            "issue_number": self.issue_number,
            "commit_sha": self.commit_sha,
            "test_summary": self.test_summary,
            "security_summary": self.security_summary,
        }


@dataclass
class DeliveryStatus:
    state: str
    status_code: str
    message: str
    push_plan: Optional[PushPlan] = None
    pr_request: Optional[DraftPrRequest] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    idempotency_key: Optional[str] = None
    dry_run: bool = False

    @property
    def completed(self) -> bool:
        """True only for a successfully delivered (or already-existing) PR."""
        return self.state in (DeliveryState.COMPLETED, DeliveryState.PR_CREATED)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "status_code": self.status_code,
            "message": self.message,
            "completed": self.completed,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "idempotency_key": self.idempotency_key,
            "dry_run": self.dry_run,
            "push_plan": self.push_plan.to_dict() if self.push_plan else None,
            "pr_request": self.pr_request.to_dict() if self.pr_request else None,
        }


class DeliveryError(Exception):
    """Raised internally for branch-policy violations (caught by ``deliver``)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Git workspace abstraction (real + fake)
# --------------------------------------------------------------------------

class GitWorkspace(abc.ABC):
    """Validates the local commit before any delivery happens."""

    @abc.abstractmethod
    def exists(self) -> bool: ...

    @abc.abstractmethod
    def is_git_repo(self) -> bool: ...

    @abc.abstractmethod
    def get_head_sha(self) -> str: ...

    @abc.abstractmethod
    def is_clean(self) -> bool: ...

    @abc.abstractmethod
    def commit_in_history(self, sha: str) -> bool: ...

    @abc.abstractmethod
    def is_empty_commit(self, sha: str) -> bool: ...

    @abc.abstractmethod
    def commit_contains_credential(self, sha: str) -> bool: ...


class LocalGitWorkspace(GitWorkspace):
    """Real workspace validation via the git CLI."""

    def __init__(self, path: str):
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", self._path, *args],
            capture_output=True, text=True, timeout=120)

    def exists(self) -> bool:
        return os.path.isdir(self._path)

    def is_git_repo(self) -> bool:
        if not self.exists():
            return False
        r = self._git("rev-parse", "--is-inside-work-tree")
        return r.returncode == 0 and r.stdout.strip() == "true"

    def get_head_sha(self) -> str:
        r = self._git("rev-parse", "HEAD")
        if r.returncode != 0:
            raise ValueError("HEAD is not an explicit commit")
        return r.stdout.strip()

    def is_clean(self) -> bool:
        r = self._git("status", "--porcelain")
        return r.returncode == 0 and r.stdout.strip() == ""

    def commit_in_history(self, sha: str) -> bool:
        r = self._git("merge-base", "--is-ancestor", sha, "HEAD")
        return r.returncode == 0

    def is_empty_commit(self, sha: str) -> bool:
        r = self._git("diff-tree", "--no-commit-id", "--name-only", "-r",
                      "--root", sha)
        if r.returncode != 0:
            return True  # cannot prove non-empty -> treat cautiously as empty
        return r.stdout.strip() == ""

    def commit_contains_credential(self, sha: str) -> bool:
        r = self._git("show", "--format=", "--no-color", sha)
        if r.returncode != 0:
            return False
        return _redact_text(r.stdout) != r.stdout


@dataclass
class FakeGitWorkspace(GitWorkspace):
    """Deterministic, offline git workspace for tests (no real git)."""

    path: str = "/fake/workspace"
    _exists: bool = True
    _is_repo: bool = True
    _head_sha: str = "a" * 40
    _clean: bool = True
    _in_history: bool = True
    _empty: bool = False
    _has_credential: bool = False

    def exists(self) -> bool:
        return self._exists

    def is_git_repo(self) -> bool:
        return self._is_repo

    def get_head_sha(self) -> str:
        if not self._exists or not self._is_repo:
            raise ValueError("HEAD is not an explicit commit")
        return self._head_sha

    def is_clean(self) -> bool:
        return self._clean

    def commit_in_history(self, sha: str) -> bool:
        return self._in_history

    def is_empty_commit(self, sha: str) -> bool:
        return self._empty

    def commit_contains_credential(self, sha: str) -> bool:
        return self._has_credential


# --------------------------------------------------------------------------
# GitHub client abstraction (real + fake)
# --------------------------------------------------------------------------

class GitHubClientError(Exception):
    pass


class GitHubClient(abc.ABC):
    """GitHub write surface used by the delivery layer.

    NOTE: there is deliberately NO merge method here. Auto-merge is structurally
    impossible from this layer.
    """

    @abc.abstractmethod
    def push(self, *, repository: str, remote_name: str,
             local_commit_sha: str, remote_branch: str,
             force: bool = False) -> dict: ...

    @abc.abstractmethod
    def create_pull_request(self, *, repository: str, base_branch: str,
                            head_branch: str, title: str, body: str,
                            draft: bool = True) -> dict: ...


class FakeGitHubClient(GitHubClient):
    """Records calls, never touches the network, never receives a token."""

    def __init__(self, *, fail_push: bool = False, fail_pr: bool = False,
                 push_error: str = "fake push failed",
                 pr_error: str = "fake pr failed",
                 pr_url_template: str = "https://github.com/{repo}/pull/{n}"):
        self.fail_push = fail_push
        self.fail_pr = fail_pr
        self.push_error = push_error
        self.pr_error = pr_error
        self.pr_url_template = pr_url_template
        self.push_calls: list = []
        self.pr_calls: list = []
        self.order: list = []          # interleaved call order (push/pr)
        self.merge_calls: list = []    # MUST stay empty (auto-merge forbidden)
        self._pr_counter = 0

    def push(self, *, repository, remote_name, local_commit_sha, remote_branch,
             force=False):
        self.push_calls.append({
            "repository": repository, "remote_name": remote_name,
            "local_commit_sha": local_commit_sha, "remote_branch": remote_branch,
            "force": force})
        self.order.append(("push", remote_branch))
        if self.fail_push:
            raise GitHubClientError(self.push_error)
        return {"ok": True, "remote_commit_sha": local_commit_sha,
                "remote_branch": remote_branch}

    def create_pull_request(self, *, repository, base_branch, head_branch,
                            title, body, draft=True):
        self.pr_calls.append({
            "repository": repository, "base_branch": base_branch,
            "head_branch": head_branch, "title": title, "body": body,
            "draft": draft})
        self.order.append(("pr", head_branch))
        if self.fail_pr:
            raise GitHubClientError(self.pr_error)
        self._pr_counter += 1
        return {"ok": True,
                "html_url": self.pr_url_template.format(repo=repository, n=self._pr_counter),
                "number": self._pr_counter, "draft": draft}

    def merge_pull_request(self, *args, **kwargs):
        # Present only so a test can assert it is NEVER invoked.
        self.merge_calls.append((args, kwargs))
        raise RuntimeError("auto-merge must never be called by the delivery layer")


class GhCliGitHubClient(GitHubClient):
    """Real GitHub client via the ``gh`` CLI + ``git`` (authenticated out-of-band
    by the Host Worker / Token Broker via ``GITHUB_TOKEN``). The delivery
    controller never passes a token here, so credentials stay in the worker
    process and are redacted on the subprocess boundary by the worker's redaction
    layer. This code path is only exercised in production behind the authorization
    gate; the test suite uses ``FakeGitHubClient`` instead.
    """

    def __init__(self, gh_bin: str = "gh", git_bin: str = "git", timeout: int = 120):
        self.gh = gh_bin
        self.git = git_bin
        self.timeout = timeout

    def push(self, *, repository, remote_name, local_commit_sha, remote_branch,
             force=False):
        if force:
            raise GitHubClientError("force push is disabled by policy")
        proc = subprocess.run(
            [self.git, "push", remote_name,
             f"{local_commit_sha}:refs/heads/{remote_branch}"],
            capture_output=True, text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise GitHubClientError(proc.stderr or "git push failed")
        return {"ok": True, "remote_commit_sha": local_commit_sha,
                "remote_branch": remote_branch}

    def create_pull_request(self, *, repository, base_branch, head_branch,
                            title, body, draft=True):
        if not draft:
            raise GitHubClientError("only draft pull requests are permitted")
        proc = subprocess.run(
            [self.gh, "pr", "create", "--repo", repository,
             "--base", base_branch, "--head", head_branch,
             "--title", title, "--body", body, "--draft"],
            capture_output=True, text=True, timeout=self.timeout)
        if proc.returncode != 0:
            raise GitHubClientError(proc.stderr or "gh pr create failed")
        return {"ok": True, "html_url": proc.stdout.strip(),
                "number": None, "draft": True}


# --------------------------------------------------------------------------
# Idempotency registry
# --------------------------------------------------------------------------

class DeliveryRegistry(abc.ABC):
    @abc.abstractmethod
    def get(self, key: str) -> Optional[dict]: ...

    @abc.abstractmethod
    def get_by_task(self, repository: str, task_id: str,
                    remote_branch: str) -> Optional[dict]: ...

    @abc.abstractmethod
    def put(self, key: str, record: dict) -> None: ...


class InMemoryDeliveryRegistry(DeliveryRegistry):
    def __init__(self):
        self._store: dict = {}

    def get(self, key: str) -> Optional[dict]:
        return self._store.get(key)

    def get_by_task(self, repository: str, task_id: str,
                    remote_branch: str) -> Optional[dict]:
        for rec in self._store.values():
            if (rec.get("repository") == repository
                    and rec.get("task_id") == task_id
                    and rec.get("remote_branch") == remote_branch):
                return rec
        return None

    def put(self, key: str, record: dict) -> None:
        self._store[key] = dict(record)


class SqliteDeliveryRegistry(DeliveryRegistry):
    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path) or "."
        os.makedirs(parent, exist_ok=True)
        self.conn = __import__("sqlite3").connect(db_path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS delivery_registry (
                   key TEXT PRIMARY KEY,
                   state TEXT,
                   repository TEXT,
                   task_id TEXT,
                   commit_sha TEXT,
                   remote_branch TEXT,
                   pr_url TEXT,
                   pr_number INTEGER,
                   updated_at REAL)""")
        self.conn.commit()

    def get(self, key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT state, repository, task_id, commit_sha, remote_branch, "
            "pr_url, pr_number FROM delivery_registry WHERE key=?",
            (key,)).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def get_by_task(self, repository: str, task_id: str,
                    remote_branch: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT state, repository, task_id, commit_sha, remote_branch, "
            "pr_url, pr_number FROM delivery_registry "
            "WHERE repository=? AND task_id=? AND remote_branch=?",
            (repository, task_id, remote_branch)).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {"state": row[0], "repository": row[1], "task_id": row[2],
                "commit_sha": row[3], "remote_branch": row[4],
                "pr_url": row[5], "pr_number": row[6]}

    def put(self, key: str, record: dict) -> None:
        self.conn.execute(
            """INSERT INTO delivery_registry(
                   key, state, repository, task_id, commit_sha, remote_branch,
                   pr_url, pr_number, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 state=excluded.state, repository=excluded.repository,
                 task_id=excluded.task_id, commit_sha=excluded.commit_sha,
                 remote_branch=excluded.remote_branch, pr_url=excluded.pr_url,
                 pr_number=excluded.pr_number, updated_at=excluded.updated_at""",
            (key, record.get("state"), record.get("repository"),
             record.get("task_id"), record.get("commit_sha"),
             record.get("remote_branch"), record.get("pr_url"),
             record.get("pr_number"), time.time()))
        self.conn.commit()


# --------------------------------------------------------------------------
# Delivery controller
# --------------------------------------------------------------------------

class DeliveryController:
    def __init__(self, *, github_client: GitHubClient, git_workspace: GitWorkspace,
                 registry: Optional[DeliveryRegistry] = None,
                 allowed_repos: Optional[set] = None,
                 protected_repos: Optional[set] = None,
                 protected_branches: Optional[set] = None,
                 default_branch: str = "main",
                 remote_name: str = "origin",
                 branch_prefix: str = BRANCH_PREFIX,
                 max_branch_len: int = MAX_BRANCH_LEN):
        self.github = github_client
        self.git = git_workspace
        self.registry = registry or InMemoryDeliveryRegistry()
        self.allowed_repos = set(allowed_repos or set())
        self.protected_repos = set(protected_repos or PROTECTED_REPOS)
        self.protected_branches = set(protected_branches or DEFAULT_PROTECTED_BRANCHES)
        self.default_branch = default_branch
        self.remote_name = remote_name
        self.branch_prefix = branch_prefix
        self.max_branch_len = max_branch_len

    # -- branch policy ------------------------------------------------------
    def sanitize_branch_name(self, name: str) -> str:
        if not name:
            raise DeliveryError(BRANCH_INVALID, "empty branch name")
        low = name.lower().strip()
        if (low.startswith("-") or low.endswith("/") or low.endswith(".")
                or ".." in low):
            raise DeliveryError(BRANCH_INVALID, f"illegal branch name: {name!r}")
        if any(tok == low or tok in low.split("/")
               for tok in _ILLEGAL_BRANCH_TOKENS):
            raise DeliveryError(BRANCH_INVALID, f"illegal branch token: {name!r}")
        if not _SAFE_BRANCH_RE.match(low):
            raise DeliveryError(BRANCH_INVALID,
                                f"invalid characters in branch: {name!r}")
        if len(low) > self.max_branch_len:
            raise DeliveryError(BRANCH_INVALID, "branch name too long")
        if low in self.protected_branches or low == self.default_branch:
            raise DeliveryError(BRANCH_PROTECTED,
                                f"branch is protected/default: {low!r}")
        return low

    def derive_remote_branch(self, task_id: Optional[str] = None,
                             issue_number: Optional[int] = None,
                             explicit: Optional[str] = None) -> str:
        if explicit:
            return self.sanitize_branch_name(explicit)
        if task_id:
            base = f"{self.branch_prefix}-{task_id}"
        elif issue_number is not None:
            base = f"{self.branch_prefix}-issue-{issue_number}"
        else:
            raise DeliveryError(BRANCH_INVALID,
                                "need task_id, issue_number, or explicit branch")
        return self.sanitize_branch_name(base)

    # -- idempotency key ----------------------------------------------------
    @staticmethod
    def idempotency_key(repository: str, task_id: str, commit_sha: str,
                        remote_branch: str) -> str:
        return f"{repository}|{task_id}|{commit_sha}|{remote_branch}"

    # -- plan builders ------------------------------------------------------
    def _build_push_plan(self, repository, local_branch, local_commit_sha,
                         remote_branch, authorization, dry_run) -> PushPlan:
        return PushPlan(
            repository=repository,
            local_branch=local_branch,
            local_commit_sha=local_commit_sha,
            remote_branch=remote_branch,
            remote_name=self.remote_name,
            force=False,  # always false; force push is rejected upstream too
            authorization_status="granted" if isinstance(
                authorization, DeliveryAuthorization) else "required",
            dry_run=dry_run)

    def _build_pr_request(self, repository, base_branch, remote_branch, title,
                          body, task_id, issue_number, commit_sha,
                          test_summary, security_summary) -> DraftPrRequest:
        # Body is redacted before it leaves the controller (credential isolation).
        redacted_body = _redact_text(body)
        return DraftPrRequest(
            repository=repository,
            base_branch=base_branch,
            head_branch=remote_branch,
            title=title,
            body=redacted_body,
            draft=True,
            task_id=task_id,
            issue_number=issue_number,
            commit_sha=commit_sha,
            test_summary=test_summary,
            security_summary=security_summary)

    # -- local commit validation -------------------------------------------
    def _validate_local_commit(self, local_commit_sha: str,
                               expected_sha: str) -> Optional[DeliveryStatus]:
        if not self.git.exists():
            return self._blocked(WORKSPACE_MISSING, "workspace path does not exist")
        if not self.git.is_git_repo():
            return self._blocked(NOT_A_GIT_REPO, "workspace is not a git repository")
        try:
            self.git.get_head_sha()
        except Exception:
            return self._blocked(HEAD_NOT_EXPLICIT_COMMIT,
                                 "HEAD is not an explicit commit")
        if not self.git.is_clean():
            return self._blocked(WORKSPACE_DIRTY,
                                 "workspace has uncommitted or untracked changes")
        if not self.git.commit_in_history(local_commit_sha):
            return self._blocked(COMMIT_NOT_IN_HISTORY,
                                 "target commit not in current branch history")
        if self.git.is_empty_commit(local_commit_sha):
            return self._blocked(COMMIT_EMPTY, "commit is empty (no file changes)")
        if self.git.commit_contains_credential(local_commit_sha):
            return self._blocked(COMMIT_CONTAINS_CREDENTIAL,
                                 "commit contains an obvious credential")
        if local_commit_sha != expected_sha:
            return self._blocked(COMMIT_SHA_MISMATCH,
                                 "commit SHA does not match task record")
        return None

    def _blocked(self, code: str, message: str, dry_run: bool = False,
                 idempotency_key: Optional[str] = None) -> DeliveryStatus:
        return DeliveryStatus(
            state=DeliveryState.from_code(code), status_code=code,
            message=message, dry_run=dry_run, idempotency_key=idempotency_key)

    # -- orchestration ------------------------------------------------------
    def deliver(self, *, task_id: str, repository: str, local_commit_sha: str,
                expected_sha: str, authorization: DeliveryAuthorization,
                title: str, body: str, test_summary: str, security_summary: str,
                local_branch: Optional[str] = None,
                base_branch: Optional[str] = None,
                issue_number: Optional[int] = None,
                remote_branch: Optional[str] = None,
                force: bool = False, dry_run: bool = False) -> DeliveryStatus:
        base_branch = base_branch or self.default_branch
        local_branch = local_branch or base_branch
        dry_run = bool(dry_run)

        # 1) Authorization gate (fail-closed).
        if (not isinstance(authorization, DeliveryAuthorization)
                or not authorization.is_valid_for(task_id, repository)):
            return self._blocked(
                AUTHORIZATION_REQUIRED,
                "explicit task/repo-scoped authorization required; "
                "none supplied or mismatch", dry_run=dry_run)

        # 2) Repository allowlist + protected repo (protected always wins).
        if repository in self.protected_repos:
            return self._blocked(
                PROTECTED_REPOSITORY,
                f"repository is protected and never writable: {repository}",
                dry_run=dry_run)
        if repository not in self.allowed_repos:
            return self._blocked(
                REPOSITORY_NOT_ALLOWED,
                f"repository not in allowlist: {repository}", dry_run=dry_run)

        # 3) Force push is never allowed.
        if force:
            return self._blocked(
                FORCE_PUSH_NOT_ALLOWED, "force push is disabled and rejected",
                dry_run=dry_run)

        # 4) Branch policy.
        try:
            remote_branch = self.derive_remote_branch(
                task_id=task_id, issue_number=issue_number, explicit=remote_branch)
        except DeliveryError as e:
            return self._blocked(e.code, e.message, dry_run=dry_run)

        # 5) Idempotency key (stable fields).
        key = self.idempotency_key(repository, task_id, local_commit_sha,
                                   remote_branch)

        # 6) Local commit validation.
        blocked = self._validate_local_commit(local_commit_sha, expected_sha)
        if blocked is not None:
            blocked.idempotency_key = key
            return blocked

        # 7) Idempotency / conflict resolution.
        existing = self.registry.get(key)
        if existing is not None:
            # Same key (same repo/task/commit/branch) already recorded.
            prev_state = existing.get("state")
            if prev_state in (DeliveryState.PUSHED, DeliveryState.PR_CREATED,
                              DeliveryState.PR_CLOSED):
                if prev_state == DeliveryState.PR_CLOSED:
                    return DeliveryStatus(
                        state=DeliveryState.PR_CLOSED,
                        status_code=DUPLICATE_SUPPRESSED,
                        message="previous draft PR was closed; manual action required",
                        pr_url=existing.get("pr_url"),
                        pr_number=existing.get("pr_number"),
                        idempotency_key=key, dry_run=dry_run)
                # PR already exists (or push done) -> do NOT create a second PR.
                return DeliveryStatus(
                    state=DeliveryState.PR_CREATED,
                    status_code=DUPLICATE_SUPPRESSED,
                    message="delivery already completed for this key; "
                            "duplicate suppressed",
                    pr_url=existing.get("pr_url"),
                    pr_number=existing.get("pr_number"),
                    idempotency_key=key, dry_run=dry_run,
                    push_plan=self._build_push_plan(
                        repository, local_branch, local_commit_sha, remote_branch,
                        authorization, dry_run),
                    pr_request=self._build_pr_request(
                        repository, base_branch, remote_branch, title, body, task_id,
                        issue_number, local_commit_sha, test_summary,
                        security_summary))
            # A prior failed attempt for the same key: suppress (no duplicate).
            return DeliveryStatus(
                state=prev_state, status_code=DUPLICATE_SUPPRESSED,
                message=f"prior attempt recorded ({prev_state}); duplicate suppressed",
                idempotency_key=key, dry_run=dry_run)

        prior = self.registry.get_by_task(repository, task_id, remote_branch)
        if prior is not None and prior.get("commit_sha") != local_commit_sha:
            # Same task/repo/branch but a DIFFERENT commit -> conflict.
            return DeliveryStatus(
                state=DeliveryState.COMMIT_CHANGED,
                status_code=CONFLICT_COMMIT_CHANGED,
                message="same task/branch already delivered with a different commit",
                idempotency_key=key, dry_run=dry_run)

        # 8) Build structured plans (authorization present, force=false).
        push_plan = self._build_push_plan(
            repository, local_branch, local_commit_sha, remote_branch,
            authorization, dry_run)
        pr_request = self._build_pr_request(
            repository, base_branch, remote_branch, title, body, task_id,
            issue_number, local_commit_sha, test_summary, security_summary)

        # 9) Dry-run: no GitHub write calls at all.
        if dry_run:
            self.registry.put(key, {
                "state": DeliveryState.DRY_RUN, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch, "pr_url": None, "pr_number": None})
            return DeliveryStatus(
                state=DeliveryState.DRY_RUN, status_code=DRY_RUN_NO_WRITE,
                message="dry-run: no GitHub write performed",
                push_plan=push_plan, pr_request=pr_request,
                idempotency_key=key, dry_run=True)

        # 10) Push, then Draft PR (ordered; fail-closed at each step).
        try:
            self.github.push(
                repository=repository, remote_name=self.remote_name,
                local_commit_sha=local_commit_sha, remote_branch=remote_branch,
                force=False)
        except GitHubClientError as e:
            self.registry.put(key, {
                "state": DeliveryState.PUSH_FAILED, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch, "pr_url": None, "pr_number": None})
            return DeliveryStatus(
                state=DeliveryState.PUSH_FAILED, status_code=PUSH_FAILED,
                message=f"push failed: {_redact_text(str(e))}",
                push_plan=push_plan, idempotency_key=key)

        self.registry.put(key, {
            "state": DeliveryState.PUSHED, "repository": repository,
            "task_id": task_id, "commit_sha": local_commit_sha,
            "remote_branch": remote_branch, "pr_url": None, "pr_number": None})

        try:
            pr = self.github.create_pull_request(
                repository=repository, base_branch=base_branch,
                head_branch=remote_branch, title=title, body=_redact_text(body),
                draft=True)
        except GitHubClientError as e:
            self.registry.put(key, {
                "state": DeliveryState.PR_FAILED, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch, "pr_url": None, "pr_number": None})
            return DeliveryStatus(
                state=DeliveryState.PR_FAILED, status_code=PR_CREATION_FAILED,
                message=f"draft PR creation failed: {_redact_text(str(e))}",
                push_plan=push_plan, idempotency_key=key)

        self.registry.put(key, {
            "state": DeliveryState.PR_CREATED, "repository": repository,
            "task_id": task_id, "commit_sha": local_commit_sha,
            "remote_branch": remote_branch, "pr_url": pr.get("html_url"),
            "pr_number": pr.get("number")})
        return DeliveryStatus(
            state=DeliveryState.COMPLETED, status_code=COMPLETED,
            message="draft PR created", push_plan=push_plan,
            pr_request=pr_request, pr_url=pr.get("html_url"),
            pr_number=pr.get("number"), idempotency_key=key)
