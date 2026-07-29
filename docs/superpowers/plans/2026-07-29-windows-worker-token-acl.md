# Windows Worker Token ACL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make strict Worker token loading enforce NTFS ACLs on Windows instead of interpreting Windows `st_mode` as Unix group/other bits.

**Architecture:** Keep `load_token` as the single entry point. On POSIX, retain the existing `0600` mode gate. On Windows strict mode, reject non-regular/reparse paths and invoke a noninteractive SID-based PowerShell DACL validator that allows only the current user and SYSTEM; never print ACL or token content.

**Tech Stack:** Python stdlib, Windows PowerShell/Get-Acl, pytest.

---

### Task 1: Reproduce and specify Windows behavior

**Files:**
- Modify: `tests/deployment/test_worker.py`

- [ ] Add unit tests proving strict Windows mode delegates to DACL validation, accepts a restricted DACL result, rejects a broad/failing DACL result, and never falls back to Unix mode bits.
- [ ] Run the focused tests and require the new acceptance test to fail against the current implementation.

### Task 2: Implement the platform-correct gate

**Files:**
- Modify: `deploy/worker/worker_runner.py`

- [ ] Add a value-free `_windows_acl_restricted(path)` helper using `powershell.exe -NoProfile -NonInteractive` and SID comparisons.
- [ ] Reject symlink/reparse/non-regular paths before reading.
- [ ] Route strict Windows checks to DACL validation and POSIX checks to the existing mode-bit rule.
- [ ] Keep token content in memory only and preserve existing fatal error behavior.
- [ ] Run focused tests to green.

### Task 3: Verify and deliver

**Files:**
- Update authorization evidence under `docs/authorization/` only after verification.

- [ ] Run focused Pi Worker/deployment tests, full non-deployment tests, deployment tests, acceptance, diff check, and Secret scan.
- [ ] Obtain an independent Haiku correctness/security review.
- [ ] Commit only tracked code/test/doc changes; preserve handoff/authorization files as untracked unless explicitly selected.
- [ ] Request and perform a separate ordinary fast-forward PR update; then request a fresh C3 retry authorization.
