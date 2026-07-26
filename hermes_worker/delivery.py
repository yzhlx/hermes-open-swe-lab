"""Host Worker — GitHub Draft PR controlled delivery layer (MVP-0, D4).

Canonical-component integration
--------------------------------
This module is a *thin coordination layer*. It reuses the approved canonical
components (ported verbatim from the frozen ``codex-primary-agent-policy``
worktree, blob-identical at ``8248268`` and ``3612b526``) instead of
re-implementing them:

* ``hermes_worker.constants``      — single source of truth for
  ``ALLOWED_GITHUB_REPOS`` / ``PROTECTED_REPOS`` (no second allowlist / protected
  set lives in this module).
* ``hermes_worker.repository``     — ``RepositoryPreparer`` (host-side repo
  preparation, used by the Host Worker) and ``HostGitOperations`` (the ONLY
  production git-push implementation; uses ephemeral AskPass credentials, the
  single git-env allowlist, and the single ref-validation regex). There is no
  second AskPass, no second git-env filter, and no second ref-validation here.
* ``hermes_worker.github_client`` — ``GitHubRestClient`` (production Draft-PR
  creation via the REST API; Installation Token sent only as an HTTP header) and
  ``FakeGitHubClient`` (offline stand-in). The D4-internal ``GitHubClient`` ABC
  and ``GhCliGitHubClient`` that previously did BOTH git push and PR API have
  been deleted; this module no longer owns a parallel ``GitHubClient``.
* ``hermes_worker.github_app``     — ``GitHubAppTokenBroker`` is the production
  source of short-lived, per-repo Installation Tokens. The controller never
  holds a token: it receives a ``token_provider(repository) -> token`` callable
  (the Host Worker wires ``broker.mint_installation_token`` or
  ``broker.get_token_for_job``). The token is a local variable inside
  ``deliver()`` and is discarded; it is never written to the controller, the
  ``DeliveryAuthorization``, the DB, the PR body, or any log/exception.
* ``hermes_worker.db``             — single SQLite store + ``delivery_state``
  table for idempotency / recovery (no second SQLite connection or registry).
* ``hermes_worker.redact``         — the one and only secret redactor.
* ``hermes_worker.control_plane.ControlPlaneError`` — the single error taxonomy;
  ``DeliveryError`` subclasses it.

What this layer still owns (and must keep)
-----------------------------------------
* The ``GitWorkspaceInspector`` — a *read-only* local-commit validator (HEAD /
  detached / dirty / commit-in-history / empty / credential-in-diff / diff).
  ``HostGitOperations`` does not provide these read-only checks, so this small
  adapter is retained; it contains no push / commit / credential / remote-write
  logic (all of that lives in ``HostGitOperations``).
* The delivery state machine, authorization binding, branch policy, idempotency
  / recovery orchestration, and the fail-closed security gates.

Credential isolation by construction: the controller NEVER receives or holds a
GitHub token. There is deliberately NO merge method invoked anywhere — the
formal ``GitHubRestClient`` / ``FakeGitHubClient`` both refuse merge, and the
D4 layer never calls one.
"""
from __future__ import annotations

import abc
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .redact import redact as _redact_text
from .control_plane import ControlPlaneError
from . import db as _db
from .secret_scan import scan_diff_for_secrets

# Canonical constants — the single source of truth for repo scope.
from .constants import ALLOWED_GITHUB_REPOS, PROTECTED_REPOS

# Canonical git operations (production push + repo prep) and the single
# ref-validation regex so this module does NOT re-introduce a second one.
from .repository import (
    HostGitOperations,
    RepositoryPreparer,
    CommandResult,
    _SAFE_REF_RE,
)

# Canonical GitHub clients. The D4-internal parallel ``GitHubClient`` ABC and
# ``GhCliGitHubClient`` have been removed; the formal clients own PR creation.
from .github_client import (
    GitHubRestClient,
    FakeGitHubClient,
    GitHubClientError,
)


# --------------------------------------------------------------------------
# Constants (D4-specific policy only; repo scope comes from canonical
# ``constants.py`` above).
# --------------------------------------------------------------------------

# Default branches that must never be the *target* of a direct push.
DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master"})

# Deterministic remote-branch prefix (avoids collision with human branches).
BRANCH_PREFIX = "hermes/delivery"

MAX_BRANCH_LEN = 100

# Branch tokens that are always rejected as a push target.
_ILLEGAL_BRANCH_TOKENS = ("head", "refs", "ref", "config", "hook", "objects")

# Explicit, testable status / block codes returned by the controller.
AUTHORIZATION_REQUIRED = "DELIVERY_BLOCKED_AUTHORIZATION_REQUIRED"
PROTECTED_REPOSITORY = "DELIVERY_BLOCKED_PROTECTED_REPOSITORY"
REPOSITORY_NOT_ALLOWED = "DELIVERY_BLOCKED_REPOSITORY_NOT_ALLOWED"
FORCE_PUSH_NOT_ALLOWED = "DELIVERY_BLOCKED_FORCE_PUSH_NOT_ALLOWED"
TOKEN_PROVIDER_REQUIRED = "DELIVERY_BLOCKED_TOKEN_PROVIDER_REQUIRED"
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
PR_CLOSED = "DELIVERY_BLOCKED_PR_CLOSED"
PR_MERGED = "DELIVERY_BLOCKED_PR_MERGED"
DRY_RUN_NO_WRITE = "DELIVERY_DRY_RUN_NO_WRITE"
COMPLETED = "DELIVERY_COMPLETED"


class DeliveryState:
    """State machine for a single delivery attempt.

    Discipline (per repo AGENTS.md): none of the blocked/failed states may
    transition into COMPLETED. COMPLETED / PR_CREATED are the only success
    terminals; the rest are explicit, testable block/fail states. This mirrors
    the fail-closed terminal-state discipline of ``ControlPlane``.
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
    PR_MERGED = "pr_merged"
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
        TOKEN_PROVIDER_REQUIRED: BLOCKED,
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
        PR_CLOSED: PR_CLOSED,
        PR_MERGED: PR_MERGED,
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
    """Explicit, task+repo+commit-scoped grant.

    This object MUST be supplied by the caller. It is NEVER inferred from an
    environment variable, a config default, a worker lease, or any Coding-Agent
    text, and it NEVER carries a token. The ``grant`` value is a fixed,
    non-default constant so it cannot be accidentally enabled by a truthy string
    such as ``"true"`` / ``"auto"``.
    """

    task_id: str
    repository: str
    commit_sha: str
    grant: str

    REQUIRED_GRANT = "GRANT_PUSH_AND_DRAFT_PR"

    def is_valid_for(self, task_id: str, repository: str, commit_sha: str) -> bool:
        if not self.grant:
            return False
        return (self.task_id == task_id
                and self.repository == repository
                and self.commit_sha == commit_sha
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


class DeliveryError(ControlPlaneError):
    """Raised internally for branch-policy / authorization violations.

    Subclasses ``ControlPlaneError`` so the delivery layer shares the control
    plane's single error taxonomy (no second error hierarchy).
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Read-only local git workspace inspector (D4-specific, read-only only).
#
# ``HostGitOperations`` owns the real git push / commit / changed-files; it does
# NOT provide the read-only commit validations below, so this small adapter is
# retained. It contains no push / commit / credential / remote-write logic, and
# it does NOT re-introduce a ref-validation regex (it reuses repository._SAFE_REF_RE
# via the controller's branch policy).
# --------------------------------------------------------------------------

class GitWorkspaceInspector(abc.ABC):
    """Validates the local commit before any delivery happens (read-only).

    Subclasses expose ``path`` (the local repo path used for the canonical
    ``HostGitOperations.push``); it is intentionally NOT an abstract method so a
    dataclass subclass may hold it as a plain field.
    """

    @abc.abstractmethod
    def exists(self) -> bool: ...

    @abc.abstractmethod
    def is_git_repo(self) -> bool: ...

    @abc.abstractmethod
    def get_head_sha(self) -> str: ...

    @abc.abstractmethod
    def is_clean(self) -> bool: ...

    @abc.abstractmethod
    def is_detached_head(self) -> bool: ...

    @abc.abstractmethod
    def commit_in_history(self, sha: str) -> bool: ...

    @abc.abstractmethod
    def is_empty_commit(self, sha: str) -> bool: ...

    @abc.abstractmethod
    def commit_contains_credential(self, sha: str) -> bool: ...

    @abc.abstractmethod
    def get_commit_diff(self, sha: str) -> str: ...


class LocalGitWorkspaceInspector(GitWorkspaceInspector):
    """Real workspace validation via the git CLI (read-only)."""

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

    def is_detached_head(self) -> bool:
        r = self._git("symbolic-ref", "-q", "HEAD")
        return r.returncode != 0

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

    def get_commit_diff(self, sha: str) -> str:
        r = self._git("show", "--format=", "--no-color", sha)
        if r.returncode != 0:
            return ""
        return r.stdout


@dataclass
class FakeGitWorkspaceInspector(GitWorkspaceInspector):
    """Deterministic, offline git workspace inspector for tests (no real git)."""

    path: str = "/fake/workspace"
    _exists: bool = True
    _is_repo: bool = True
    _head_sha: str = "a" * 40
    _clean: bool = True
    _detached: bool = False
    _in_history: bool = True
    _empty: bool = False
    _has_credential: bool = False
    _diff: str = ""

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

    def is_detached_head(self) -> bool:
        return self._detached

    def commit_in_history(self, sha: str) -> bool:
        return self._in_history

    def is_empty_commit(self, sha: str) -> bool:
        return self._empty

    def commit_contains_credential(self, sha: str) -> bool:
        return self._has_credential

    def get_commit_diff(self, sha: str) -> str:
        return self._diff


# --------------------------------------------------------------------------
# Offline stand-ins for the canonical production clients.
#
# These are TEST SEAMS only — they record calls and convert parameters. They do
# NOT re-implement token handling, HTTP, idempotency, or Draft-PR logic; they
# delegate to the canonical ``FakeGitHubClient`` and ``HostGitOperations``.
# --------------------------------------------------------------------------

class FakeHostGitOperations(HostGitOperations):
    """Offline stand-in for ``HostGitOperations``. Records push calls and returns
    a ``CommandResult``; never runs git.

    The token arrives as a *parameter* (the formal credential path) and is NOT
    persisted here — only ``token_present`` / ``token_len`` are recorded, so
    tests can assert the token was passed via the formal path rather than
    embedded in a branch / refspec.
    """

    def __init__(self, *, fail_push: bool = False,
                 push_error: str = "fake push failed"):
        super().__init__()
        self.fail_push = fail_push
        self.push_error = push_error
        self.push_calls: list = []

    def push(self, repo_path, branch, token):
        # Structural guarantees (asserted by tests):
        #  - no "--force": this client has no force parameter at all.
        #  - no delete refspec: always pushes HEAD:refs/heads/{branch}.
        #  - the controller has already rejected default/protected branches.
        self.push_calls.append({
            "repo_path": str(repo_path),
            "branch": branch,
            "token_present": bool(token),
            "token_len": len(token) if token else 0,
        })
        if self.fail_push:
            return CommandResult(1, "", self.push_error)
        return CommandResult(0, f"push {branch} ok", "")


class FakeGitHubRestClient(FakeGitHubClient):
    """Offline GitHub client for D4 tests: the canonical ``FakeGitHubClient``
    extended with the production-shaped
    ``create_draft_pr(repo, branch, base, title, body, token)`` signature and a
    head-branch query. Param/result conversion only — no HTTP, no token
    handling, no Draft-PR logic (delegated to the canonical fake).
    """

    def __init__(self, *, fail_pr: bool = False, return_non_draft: bool = False,
                 pr_error: str = "fake pr failed",
                 pr_url_template: str = "https://github.com/{repo}/pull/{n}"):
        super().__init__()
        self.fail_pr = fail_pr
        self.return_non_draft = return_non_draft
        self.pr_error = pr_error
        self.pr_url_template = pr_url_template
        self.push_calls: list = []   # retained for parity; push now via git_ops
        self.pr_calls: list = []
        self.order: list = []
        self.merge_calls: list = []  # MUST stay empty (auto-merge forbidden)

    def create_draft_pr(self, repo, branch, base, title, body, token=None):
        self.pr_calls.append({
            "repo": repo, "branch": branch, "base": base, "title": title,
            "body": body, "draft": True, "token_present": bool(token)})
        self.order.append(("pr", branch))
        if self.fail_pr:
            raise GitHubClientError(self.pr_error)
        # Register the repo so the canonical fake can resolve head_sha/branches.
        self.ensure_repo(repo)
        # Delegate to the canonical fake (records the PR, sets draft=True).
        pr = super().create_draft_pr(
            full_name=repo, branch=branch, title=title, body=body)
        if self.return_non_draft:
            pr["draft"] = False
        pr["url"] = self.pr_url_template.format(repo=repo, n=pr["number"])
        return pr

    def get_pr_by_head(self, repository, head_branch):
        for pr in self.prs.values():
            if (pr.get("full_name") == repository
                    and pr.get("branch") == head_branch):
                return dict(pr)
        return None

    def merge_pr(self, pr_number):
        # Present only so a test can assert it is NEVER invoked.
        self.merge_calls.append(pr_number)
        raise GitHubClientError("merge_not_permitted_for_automation")


# --------------------------------------------------------------------------
# Idempotency / recovery registry
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
    """In-memory registry (tests / ephemeral runs)."""

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


class DbBackedDeliveryRegistry(DeliveryRegistry):
    """Registry backed by the existing ``hermes_worker.db`` store (single SQLite
    file, schema owned by db.py). This replaces the previous second SQLite
    connection / ``delivery_registry`` table — there is now exactly one DB.
    """

    def __init__(self, db_path: str):
        self._conn = _db.init_db(db_path)

    def get(self, key: str) -> Optional[dict]:
        return _db.select_delivery_state(self._conn, key)

    def get_by_task(self, repository: str, task_id: str,
                    remote_branch: str) -> Optional[dict]:
        return _db.select_delivery_state_by_task(
            self._conn, repository=repository, task_id=task_id,
            remote_branch=remote_branch)

    def put(self, key: str, record: dict) -> None:
        _db.upsert_delivery_state(
            self._conn, key=key, state=record.get("state"),
            repository=record.get("repository"), task_id=record.get("task_id"),
            commit_sha=record.get("commit_sha"),
            remote_branch=record.get("remote_branch"),
            pr_url=record.get("pr_url"), pr_number=record.get("pr_number"))


# --------------------------------------------------------------------------
# Delivery controller
# --------------------------------------------------------------------------

class DeliveryController:
    def __init__(self, *, git: GitWorkspaceInspector,
                 git_operations: HostGitOperations,
                 github_client,
                 token_provider: Optional[Callable[[str], str]] = None,
                 registry: Optional[DeliveryRegistry] = None,
                 db_path: Optional[str] = None,
                 allowed_repos: Optional[set] = None,
                 protected_repos: Optional[set] = None,
                 protected_branches: Optional[set] = None,
                 default_branch: str = "main",
                 remote_name: str = "origin",
                 branch_prefix: str = BRANCH_PREFIX,
                 max_branch_len: int = MAX_BRANCH_LEN):
        # Read-only local-commit inspector (provides repo_path for git push).
        self.git = git
        # Canonical production git-push implementation.
        self.git_ops = git_operations
        # Canonical GitHub PR client (GitHubRestClient in prod / the offline
        # FakeGitHubRestClient in tests). PR creation + head-branch query only.
        self.github = github_client
        # Short-lived token source (broker-backed in prod). Never stored here.
        self.token_provider = token_provider
        if registry is None and db_path is not None:
            registry = DbBackedDeliveryRegistry(db_path)
        self.registry = registry or InMemoryDeliveryRegistry()
        # Canonical repo scope is the single source of truth.
        self.allowed_repos = set(allowed_repos) if allowed_repos is not None \
            else set(ALLOWED_GITHUB_REPOS)
        self.protected_repos = set(protected_repos) if protected_repos is not None \
            else set(PROTECTED_REPOS)
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
        if low.startswith("refs/") or low.startswith("ref/"):
            raise DeliveryError(BRANCH_INVALID, f"ref-style branch rejected: {name!r}")
        if any(tok == low or tok in low.split("/")
               for tok in _ILLEGAL_BRANCH_TOKENS):
            raise DeliveryError(BRANCH_INVALID, f"illegal branch token: {name!r}")
        # Reuse the canonical ref-validation regex from repository.py — this
        # module does NOT define a second ref-validation.
        if not _SAFE_REF_RE.match(low):
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
        if self.git.is_detached_head():
            return self._blocked(HEAD_NOT_EXPLICIT_COMMIT,
                                 "workspace is in detached HEAD state")
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

        # 1) Authorization gate (fail-closed), binding task + repo + commit.
        if (not isinstance(authorization, DeliveryAuthorization)
                or not authorization.is_valid_for(task_id, repository, local_commit_sha)):
            return self._blocked(
                AUTHORIZATION_REQUIRED,
                "explicit task/repo/commit-scoped authorization required; "
                "none supplied or mismatch (env token / lease / agent claim "
                "do NOT count)", dry_run=dry_run)

        # 2) Repository allowlist + protected repo (protected ALWAYS wins).
        #    Canonical PROTECTED_REPOS is checked first; even if a repo were
        #    mistakenly added to the allowlist it is unconditionally blocked.
        if repository in self.protected_repos:
            return self._blocked(
                PROTECTED_REPOSITORY,
                f"repository is protected and never writable: {repository}",
                dry_run=dry_run)
        if repository not in self.allowed_repos:
            return self._blocked(
                REPOSITORY_NOT_ALLOWED,
                f"repository not in allowlist: {repository}", dry_run=dry_run)

        # 3) Force push / remote-branch deletion is never allowed.
        if force:
            return self._blocked(
                FORCE_PUSH_NOT_ALLOWED, "force push is disabled and rejected",
                dry_run=dry_run)

        # 4) Branch policy (also rejects default/protected + path-injection).
        try:
            remote_branch = self.derive_remote_branch(
                task_id=task_id, issue_number=issue_number, explicit=remote_branch)
        except DeliveryError as e:
            return self._blocked(e.code, e.message, dry_run=dry_run)

        # 5) Idempotency key (stable fields).
        key = self.idempotency_key(repository, task_id, local_commit_sha,
                                   remote_branch)

        # 6) Local commit validation (blocked states never reach push/PR).
        blocked = self._validate_local_commit(local_commit_sha, expected_sha)
        if blocked is not None:
            blocked.idempotency_key = key
            return blocked

        # 7) Pre-push diff secret scan (reuses redact; never pushes a leaked secret).
        secret_hits = scan_diff_for_secrets(self.git.get_commit_diff(local_commit_sha))
        if secret_hits:
            return DeliveryStatus(
                state=DeliveryState.BLOCKED,
                status_code=COMMIT_CONTAINS_CREDENTIAL,
                # secret_hits are ALREADY redacted (no raw secret value present).
                message=f"commit diff contains a suspected credential "
                        f"{secret_hits}; delivery blocked",
                idempotency_key=key, dry_run=dry_run)

        # 8) Idempotency / conflict / recovery resolution.
        existing = self.registry.get(key)
        if existing is not None:
            prev_state = existing.get("state")
            if prev_state == DeliveryState.PR_CREATED:
                # Already delivered for this exact key -> reuse, no 2nd PR.
                return DeliveryStatus(
                    state=DeliveryState.PR_CREATED,
                    status_code=DUPLICATE_SUPPRESSED,
                    message="delivery already completed for this key; duplicate suppressed",
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
            if prev_state == DeliveryState.PR_CLOSED:
                return DeliveryStatus(
                    state=DeliveryState.PR_CLOSED, status_code=PR_CLOSED,
                    message="previous draft PR was closed; manual action required",
                    pr_url=existing.get("pr_url"),
                    pr_number=existing.get("pr_number"),
                    idempotency_key=key, dry_run=dry_run)
            if prev_state == DeliveryState.PUSHED or prev_state == DeliveryState.PR_FAILED:
                # Crash / retry recovery: push already done, resume PR creation.
                pass
            # PUSH_FAILED / DRY_RUN -> allow a fresh attempt below.

        prior = self.registry.get_by_task(repository, task_id, remote_branch)
        if prior is not None and prior.get("commit_sha") != local_commit_sha:
            # Same task/repo/branch but a DIFFERENT commit -> conflict.
            return DeliveryStatus(
                state=DeliveryState.COMMIT_CHANGED,
                status_code=CONFLICT_COMMIT_CHANGED,
                message="same task/branch already delivered with a different commit",
                idempotency_key=key, dry_run=dry_run)

        # 9) Build structured plans (authorization present, force=false).
        push_plan = self._build_push_plan(
            repository, local_branch, local_commit_sha, remote_branch,
            authorization, dry_run)
        pr_request = self._build_pr_request(
            repository, base_branch, remote_branch, title, body, task_id,
            issue_number, local_commit_sha, test_summary, security_summary)

        # 10) Dry-run: no GitHub write calls at all.
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

        resume_pr_only = existing is not None and existing.get("state") in (
            DeliveryState.PUSHED, DeliveryState.PR_FAILED,
            DeliveryState.PR_CLOSED, DeliveryState.PR_MERGED)

        # Obtain a short-lived token via the formal provider (broker-backed in
        # prod). It is a LOCAL variable only — never stored on the controller,
        # never written to DB / PR body / logs.
        if self.token_provider is None:
            return self._blocked(
                TOKEN_PROVIDER_REQUIRED,
                "no token provider configured; cannot mint a delivery token",
                dry_run=dry_run)
        token = self.token_provider(repository)

        # 11) Push — exclusively via the canonical HostGitOperations. Force is
        #     structurally impossible (no force parameter); the refspec is
        #     always HEAD:refs/heads/{branch}; default/protected branches were
        #     already rejected by the branch policy above.
        if not resume_pr_only:
            try:
                result = self.git_ops.push(
                    repo_path=self.git.path, branch=remote_branch, token=token)
            except GitHubClientError as e:
                self.registry.put(key, {
                    "state": DeliveryState.PUSH_FAILED, "repository": repository,
                    "task_id": task_id, "commit_sha": local_commit_sha,
                    "remote_branch": remote_branch, "pr_url": None,
                    "pr_number": None})
                return DeliveryStatus(
                    state=DeliveryState.PUSH_FAILED, status_code=PUSH_FAILED,
                    message=f"push failed: {_redact_text(str(e))}",
                    push_plan=push_plan, idempotency_key=key)
            if result.exit_code != 0:
                self.registry.put(key, {
                    "state": DeliveryState.PUSH_FAILED, "repository": repository,
                    "task_id": task_id, "commit_sha": local_commit_sha,
                    "remote_branch": remote_branch, "pr_url": None,
                    "pr_number": None})
                return DeliveryStatus(
                    state=DeliveryState.PUSH_FAILED, status_code=PUSH_FAILED,
                    message=f"push failed: {_redact_text(result.stderr)}",
                    push_plan=push_plan, idempotency_key=key)
            self.registry.put(key, {
                "state": DeliveryState.PUSHED, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch, "pr_url": None, "pr_number": None})

        # 12) Draft PR — via the canonical GitHub client. Reuse an existing PR
        #     for the branch if present (robust against push-then-crash / retry
        #     duplication). If the injected client exposes a head-branch query
        #     (the offline FakeGitHubRestClient does), use it; otherwise rely on
        #     the registry alone.
        existing_pr = None
        get_pr = getattr(self.github, "get_pr_by_head", None)
        if callable(get_pr):
            existing_pr = get_pr(repository=repository, head_branch=remote_branch)
        if existing_pr is not None:
            st = existing_pr.get("state")
            if st == "closed":
                self.registry.put(key, {
                    "state": DeliveryState.PR_CLOSED, "repository": repository,
                    "task_id": task_id, "commit_sha": local_commit_sha,
                    "remote_branch": remote_branch,
                    "pr_url": existing_pr.get("html_url") or existing_pr.get("url"),
                    "pr_number": existing_pr.get("number")})
                return DeliveryStatus(
                    state=DeliveryState.PR_CLOSED, status_code=PR_CLOSED,
                    message="existing draft PR for branch was closed; manual action required",
                    pr_url=existing_pr.get("html_url") or existing_pr.get("url"),
                    pr_number=existing_pr.get("number"),
                    idempotency_key=key, push_plan=push_plan)
            if st == "merged":
                self.registry.put(key, {
                    "state": DeliveryState.PR_MERGED, "repository": repository,
                    "task_id": task_id, "commit_sha": local_commit_sha,
                    "remote_branch": remote_branch,
                    "pr_url": existing_pr.get("html_url") or existing_pr.get("url"),
                    "pr_number": existing_pr.get("number")})
                return DeliveryStatus(
                    state=DeliveryState.PR_MERGED, status_code=PR_MERGED,
                    message="existing draft PR for branch was merged; not re-delivered",
                    pr_url=existing_pr.get("html_url") or existing_pr.get("url"),
                    pr_number=existing_pr.get("number"),
                    idempotency_key=key, push_plan=push_plan)
            # open PR already exists -> reuse (no 2nd PR created).
            self.registry.put(key, {
                "state": DeliveryState.PR_CREATED, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch,
                "pr_url": existing_pr.get("html_url") or existing_pr.get("url"),
                "pr_number": existing_pr.get("number")})
            return DeliveryStatus(
                state=DeliveryState.PR_CREATED, status_code=DUPLICATE_SUPPRESSED,
                message="existing draft PR for branch reused; no duplicate created",
                pr_url=existing_pr.get("html_url") or existing_pr.get("url"),
                pr_number=existing_pr.get("number"),
                idempotency_key=key, push_plan=push_plan,
                pr_request=pr_request)

        try:
            pr = self.github.create_draft_pr(
                repo=repository, branch=remote_branch, base=base_branch,
                title=title, body=pr_request.body, token=token)
        except GitHubClientError as e:
            self.registry.put(key, {
                "state": DeliveryState.PR_FAILED, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch, "pr_url": None, "pr_number": None})
            return DeliveryStatus(
                state=DeliveryState.PR_FAILED, status_code=PR_CREATION_FAILED,
                message=f"draft PR creation failed: {_redact_text(str(e))}",
                push_plan=push_plan, idempotency_key=key)

        if not pr.get("draft"):
            # GitHub ignored the draft flag -> fail-closed, do NOT mark completed.
            self.registry.put(key, {
                "state": DeliveryState.PR_FAILED, "repository": repository,
                "task_id": task_id, "commit_sha": local_commit_sha,
                "remote_branch": remote_branch,
                "pr_url": pr.get("html_url") or pr.get("url"),
                "pr_number": pr.get("number")})
            return DeliveryStatus(
                state=DeliveryState.PR_FAILED, status_code=PR_CREATION_FAILED,
                message="GitHub returned a non-draft PR; delivery refused",
                push_plan=push_plan, idempotency_key=key)

        self.registry.put(key, {
            "state": DeliveryState.PR_CREATED, "repository": repository,
            "task_id": task_id, "commit_sha": local_commit_sha,
            "remote_branch": remote_branch,
            "pr_url": pr.get("html_url") or pr.get("url"),
            "pr_number": pr.get("number")})
        return DeliveryStatus(
            state=DeliveryState.COMPLETED, status_code=COMPLETED,
            message="draft PR created", push_plan=push_plan,
            pr_request=pr_request,
            pr_url=pr.get("html_url") or pr.get("url"),
            pr_number=pr.get("number"), idempotency_key=key)
