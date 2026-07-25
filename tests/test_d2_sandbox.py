"""Offline tests for Phase D2 HermesDockerSandboxBackend (no Docker daemon).

We inject a fake ``docker`` CLI runner that records the exact argument lists
the backend would pass to ``docker`` and returns canned ``ExecResult``s. This
lets us assert, offline:

- ``docker run`` is built with the MVP isolation flags (cpus/memory/pids/network)
- privileged mode and the Docker host socket are NEVER used
- ONLY the task workdir is bind-mounted (no home / SSH / cookies / other repos)
- ``execute`` builds a correct ``docker exec`` with workdir + env
- the GitHub token is injected ONLY for ``push()`` (not clone/commit) and the
  redacted ``_calls`` log never contains the token value
- write/read/edit round-trip on the real (mounted) host workdir
- delete() force-removes the container and wipes the workdir

Real container lifecycle (run/exec/rm against a live daemon) is NOT_TESTED here
and is explicitly marked NOT_TESTED in the implementation.

Run:  python -m unittest tests.test_d2_sandbox -v
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.docker_sandbox import HermesDockerSandboxBackend  # noqa: E402
from hermes_worker.protocol import ExecResult  # noqa: E402


class FakeDocker:
    """Records raw docker args; returns canned results. No real daemon."""

    def __init__(self):
        self.raw_calls = []  # unredacted, for token-presence assertions

    def __call__(self, args):
        self.raw_calls.append(list(args))
        if "inspect" in args:
            return ExecResult(0, "true", "")
        return ExecResult(0, "", "")

    # -- helpers for assertions --------------------------------------------
    def find(self, *prefix):
        for c in self.raw_calls:
            if len(c) >= len(prefix) and c[:len(prefix)] == list(prefix):
                return c
        return None

    def find_redacted(self, backend, *prefix):
        for c in backend._calls:
            if len(c) >= len(prefix) and c[:len(prefix)] == list(prefix):
                return c
        return None


class D2SandboxTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDocker()
        self.backend = HermesDockerSandboxBackend(runner=self.fake)

    # -- create / isolation flags -------------------------------------------
    def test_01_create_builds_isolation_flags(self):
        ws = self.backend.create()
        self.assertTrue(os.path.isdir(ws))
        run = self.fake.find("docker", "run", "-d", "--rm")
        self.assertIsNotNone(run, "expected a `docker run -d --rm` call")
        self.assertIn("--cpus", run)
        self.assertEqual(run[run.index("--cpus") + 1], "1")
        self.assertIn("--memory", run)
        self.assertEqual(run[run.index("--memory") + 1], "2g")
        self.assertIn("--pids-limit", run)
        self.assertEqual(run[run.index("--pids-limit") + 1], "256")
        self.assertIn("--network", run)
        self.assertEqual(run[run.index("--network") + 1], "bridge")
        self.assertIn("--rm", run)
        self.assertNotIn("--privileged", run)

    def test_02_create_forbids_privileged_and_docker_sock(self):
        self.backend.create()
        run = self.fake.find("docker", "run", "-d", "--rm")
        flat = " ".join(run)
        self.assertNotIn("--privileged", run)
        self.assertNotIn("/var/run/docker.sock", run)
        self.assertNotIn("docker.sock", flat)

    def test_03_only_workdir_is_mounted(self):
        ws = self.backend.create()
        run = self.fake.find("docker", "run", "-d", "--rm")
        mounts = [run[i + 1] for i, a in enumerate(run) if a in ("-v", "--volume")]
        # exactly one bind mount: <workdir>:/workspace:rw
        self.assertEqual(len(mounts), 1)
        self.assertTrue(mounts[0].endswith(":/workspace:rw"),
                        f"unexpected mount: {mounts[0]}")
        self.assertTrue(mounts[0].startswith(ws + ":"),
                        f"mount source should be the task workdir: {mounts[0]}")
        flat = " ".join(run)
        for forbidden in ("/root", "/home", ".ssh", "known_hosts",
                          "cookies", "docker.sock", "hermes-learning-os"):
            self.assertNotIn(forbidden, flat,
                             f"forbidden path leaked into run args: {forbidden}")

    def test_04_network_not_host(self):
        self.backend.create()
        run = self.fake.find("docker", "run", "-d", "--rm")
        self.assertEqual(run[run.index("--network") + 1], "bridge")
        self.assertNotEqual(run[run.index("--network") + 1], "host")

    # -- execute -------------------------------------------------------------
    def test_05_execute_builds_exec_with_workdir_and_env(self):
        self.backend.create()
        res = self.backend.execute("echo hi", env={"FOO": "bar"}, cwd="/workspace")
        self.assertEqual(res.exit_code, 0)
        exe = self.fake.find("docker", "exec")
        self.assertIsNotNone(exe)
        self.assertIn("--workdir", exe)
        self.assertIn("/workspace", exe)
        self.assertIn("-e", exe)
        self.assertIn("FOO=bar", exe)
        self.assertIn("bash", exe)
        self.assertIn("-lc", exe)
        self.assertIn("echo hi", exe)
        self.assertNotIn("--privileged", exe)

    # -- github token scoping ------------------------------------------------
    def test_06_token_only_on_push(self):
        self.backend.set_github_token("ghp_FAKE_TOKEN_123")
        self.backend.create()
        # clone / commit must NOT carry the token
        self.backend.git_clone("https://github.com/x/y", "repo")
        self.backend.commit("repo", "msg")
        for c in self.fake.raw_calls:
            if c[:2] == ["docker", "exec"] and "push" not in c:
                self.assertNotIn("ghp_FAKE_TOKEN_123", " ".join(c),
                                 "token leaked into a non-push git call")
        # push MUST carry the token (raw call, pre-redaction)
        self.backend.push("repo", "origin", "feat/x")
        push = None
        for c in self.fake.raw_calls:
            if c[:2] == ["docker", "exec"] and "push" in c:
                push = c
                break
        self.assertIsNotNone(push, "expected a docker exec push call")
        self.assertIn("GITHUB_TOKEN=ghp_FAKE_TOKEN_123", " ".join(push))

    def test_07_token_redacted_in_call_log(self):
        self.backend.set_github_token("ghp_FAKE_TOKEN_123")
        self.backend.create()
        self.backend.push("repo", "origin", "feat/x")
        flat = " ".join(" ".join(c) for c in self.backend._calls)
        self.assertNotIn("ghp_FAKE_TOKEN_123", flat,
                         "token value must never appear in _calls (redacted)")
        self.assertIn("GITHUB_TOKEN=***REDACTED***", flat)

    # -- file round-trip (host workdir == mounted /workspace) ---------------
    def test_08_write_read_edit_roundtrip(self):
        self.backend.create()
        self.backend.write_file("automation-smoke-test/README.md", "# hi\n")
        self.assertEqual(
            self.backend.read_file("automation-smoke-test/README.md"), "# hi\n")
        self.backend.edit_file("automation-smoke-test/README.md",
                               "# hi\n", "# bye\n")
        self.assertEqual(
            self.backend.read_file("automation-smoke-test/README.md"), "# bye\n")

    # -- health / delete -----------------------------------------------------
    def test_09_health_check_true(self):
        self.backend.create()
        self.assertTrue(self.backend.health_check())

    def test_10_delete_removes_container_and_workdir(self):
        ws = self.backend.create()
        self.backend.delete()
        rm = self.fake.find("docker", "rm", "-f")
        self.assertIsNotNone(rm, "expected docker rm -f")
        self.assertFalse(os.path.isdir(ws), "workdir should be wiped on delete")


if __name__ == "__main__":
    unittest.main()
