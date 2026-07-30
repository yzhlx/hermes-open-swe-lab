# UPSTREAM-BASELINE.md — Open SWE pinned baseline

**Purpose:** Record the approved Open SWE upstream baseline and the policy for
changing it. (AGENTS.md Section 5.)

---

## Approved baseline

```text
Repository: langchain-ai/open-swe
Commit:     ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6
```

## Verification of the pinned commit

Command (read-only):

```bash
gh api repos/langchain-ai/open-swe/commits/ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6 --jq '.sha'
```

Result (2026-07-25):

```text
ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6
```

The commit is reachable and immutable by SHA.

## Why a pinned SHA, not `main`

`langchain-ai/open-swe` `main` is actively developed (last push observed
2026-07-24). Following a floating `main` would break reproducibility and could
silently pull in behavior changes that violate MVP-0 scope or security
boundaries. The SHA is the contract.

## Policy for any future upstream change

Every upstream update MUST:

1. use an explicit commit SHA (never `main`/`HEAD`);
2. be performed on a dedicated branch (e.g. `upstream-bump-<sha>`);
3. include a compare report (`git diff <old> <new>` + narrative);
4. document conflicts and security changes;
5. pass all relevant tests;
6. be delivered through a pull request;
7. remain unmerged until approved by the user.

## How the baseline is consumed in MVP-0

MVP-0 does **not** vendor Open SWE source into this repo. Instead:

- This lab repo holds the **adaptation layer** (relay adapter, preflight, smoke
  contract, docs) that is designed to sit in front of / around the pinned Open
  SWE build on the cloud control plane.
- The cloud control plane (out of scope for this sandbox) checks out the pinned
  SHA in an isolated sandbox and runs the MVP-0 loop against the smoke-test repo.
- Any deviation from the pinned SHA on the control plane must be visible in the
  final evidence report (before/after SHA).

## Evidence

| Claim | Evidence |
| --- | --- |
| Commit reachable | `gh api` returned `ed12bb8d86b737a66a0a11b2995d73a9c64cf1e6` |
| Not floating `main` | documented policy; `main` observed still moving (2026-07-24) |
| Repo not modified | no clone/fetch of `langchain-ai/open-swe` from this sandbox |
