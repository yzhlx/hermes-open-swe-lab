#!/usr/bin/env python3
"""Fail-closed secret scan for changed and untracked delivery files."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable

EXCLUDED_PREFIXES = (
    ".pi-subagents/",
    ".playwright-cli/",
    "runtime/",
)

SECRET_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        r"gh[oprsu]_[A-Za-z0-9_]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"\bsk-[A-Za-z0-9]{20,}",
        r"AKIA[0-9A-Z]{16}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"glpat-[A-Za-z0-9_-]{10,}",
    )
)

SYNTHETIC_MARKERS = (
    "FAKE",
    "SYNTHETIC",
    "REDACTED",
    "DO-NOT-USE",
    "DO_NOT_USE",
    "DONOTUSE",
    "PLACEHOLDER",
    "DUMMY",
)


def _git_paths(repo: Path, args: list[str], label: str) -> tuple[list[str], list[str]]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return [], [f"<git:{label}>"]
    paths = [
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in completed.stdout.split(b"\0")
        if item
    ]
    return paths, []


def collect_candidate_paths(repo: Path) -> tuple[list[str], list[str]]:
    candidates: set[str] = set()
    errors: list[str] = []
    commands = (
        (["diff", "--name-only", "--diff-filter=ACMR", "-z", "--"], "unstaged"),
        (["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z", "--"], "staged"),
        (["ls-files", "--others", "--exclude-standard", "-z", "--"], "untracked"),
    )
    for args, label in commands:
        paths, command_errors = _git_paths(repo, args, label)
        errors.extend(command_errors)
        for relative_path in paths:
            if not relative_path.startswith(EXCLUDED_PREFIXES):
                candidates.add(relative_path)
    return sorted(candidates), sorted(set(errors))


def _synthetic_match(value: str) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in SYNTHETIC_MARKERS)


def scan_paths(
    repo: Path,
    relative_paths: Iterable[str],
    *,
    read_bytes: Callable[[Path], bytes] | None = None,
) -> dict:
    root = repo.resolve()
    reader = read_bytes or (lambda path: path.read_bytes())
    violations: set[str] = set()
    errors: set[str] = set()
    files_scanned = 0

    for relative_path in sorted(set(relative_paths)):
        normalized = relative_path.replace("\\", "/")
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.add(normalized)
            continue
        if not candidate.is_file():
            errors.add(normalized)
            continue
        try:
            content = reader(candidate).decode("utf-8", errors="replace")
        except OSError:
            errors.add(normalized)
            continue
        files_scanned += 1
        for pattern in SECRET_PATTERNS:
            for match in pattern.finditer(content):
                if not _synthetic_match(match.group(0)):
                    violations.add(normalized)
                    break
            if normalized in violations:
                break

    return {
        "files_scanned": files_scanned,
        "violation_count": len(violations),
        "violation_paths": sorted(violations),
        "error_count": len(errors),
        "error_paths": sorted(errors),
    }


def scan_repository(repo: Path) -> dict:
    root = repo.resolve()
    candidates, git_errors = collect_candidate_paths(root)
    result = scan_paths(root, candidates)
    all_errors = sorted(set(result["error_paths"]) | set(git_errors))
    result.update(
        {
            "candidate_count": len(candidates),
            "error_count": len(all_errors),
            "error_paths": all_errors,
        }
    )
    result["exit_code"] = int(
        result["violation_count"] > 0 or result["error_count"] > 0
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    result = scan_repository(args.repo)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
