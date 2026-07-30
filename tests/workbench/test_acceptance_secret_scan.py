from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.acceptance.secret_scan import scan_paths, scan_repository


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Hermes Tests")
    (repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "baseline.txt")
    _git(repo, "commit", "-m", "baseline")
    return repo


def test_scan_includes_staged_and_untracked_without_echoing_values(tmp_path):
    repo = _repo(tmp_path)
    real_token = "ghs_" + "R" * 32
    fake_token = "ghs_" + "FAKE_" + "S" * 28
    (repo / "staged.txt").write_text(real_token, encoding="utf-8")
    _git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text(fake_token, encoding="utf-8")

    result = scan_repository(repo)
    serialized = json.dumps(result)

    assert result["exit_code"] == 1
    assert result["violation_paths"] == ["staged.txt"]
    assert "untracked.txt" not in result["violation_paths"]
    assert real_token not in serialized
    assert fake_token not in serialized


def test_generic_fixture_word_does_not_hide_a_separate_real_match(tmp_path):
    repo = _repo(tmp_path)
    fake_token = "ghs_" + "FAKE_" + "S" * 28
    real_token = "ghs_" + "R" * 32
    (repo / "mixed.txt").write_text(
        f"example fixture {fake_token} followed by {real_token}",
        encoding="utf-8",
    )

    result = scan_repository(repo)

    assert result["exit_code"] == 1
    assert result["violation_paths"] == ["mixed.txt"]
    assert real_token not in json.dumps(result)


def test_missing_staged_candidate_fails_closed(tmp_path):
    repo = _repo(tmp_path)
    path = repo / "missing.txt"
    path.write_text("ordinary staged content", encoding="utf-8")
    _git(repo, "add", "missing.txt")
    path.unlink()

    result = scan_repository(repo)

    assert result["exit_code"] == 1
    assert result["error_paths"] == ["missing.txt"]


def test_read_error_fails_closed_without_content(tmp_path):
    repo = _repo(tmp_path)
    path = repo / "unreadable.txt"
    path.write_text("ordinary content", encoding="utf-8")

    def fail_read(_path: Path) -> bytes:
        raise OSError("synthetic read failure")

    result = scan_paths(repo, ["unreadable.txt"], read_bytes=fail_read)

    assert result["error_paths"] == ["unreadable.txt"]
    assert result["violation_paths"] == []


def test_cli_output_and_artifact_never_include_matched_value(tmp_path):
    repo = _repo(tmp_path)
    real_token = "ghs_" + "R" * 32
    (repo / "candidate.txt").write_text(real_token, encoding="utf-8")
    artifact = tmp_path / "scan.json"
    script = (
        Path(__file__).resolve().parents[2]
        / "tools"
        / "acceptance"
        / "secret_scan.py"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo",
            str(repo),
            "--output",
            str(artifact),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert real_token not in completed.stdout
    assert real_token not in completed.stderr
    assert real_token not in artifact.read_text(encoding="utf-8")
    assert json.loads(completed.stdout)["violation_paths"] == [
        "candidate.txt"
    ]
