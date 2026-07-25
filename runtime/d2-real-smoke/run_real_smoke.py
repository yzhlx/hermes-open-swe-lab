"""D2.5 real Docker container-lifecycle smoke (NO GitHub / NO token / NO webhook).

Drives the REAL HermesDockerSandboxBackend (runner=None -> live `docker` CLI)
against the user's local Docker daemon. Everything is local and non-sensitive:

- image: hermes-d2-smoke:local (python:3.11-slim + git, built locally)
- a minimal git repo + README + deterministic verify script
- a LOCAL bare git remote inside the mounted workdir (file://, no network)
- no SSH keys, no GitHub App creds, no user-home reads, no relay model

Emits a structured JSON report on stdout (evidence). No secrets are printed.

Run:  python runtime/d2-real-smoke/run_real_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from hermes_worker.docker_sandbox import HermesDockerSandboxBackend  # noqa: E402
from hermes_worker.protocol import ExecResult  # noqa: E402

IMAGE = "hermes-d2-smoke:local"
REPORT = {
    "image": IMAGE,
    "steps": {},
    "inspect": {},
    "security": {},
    "residual_containers": None,
    "runtime_sec": None,
    "pass": False,
}


def log(k, v):
    REPORT["steps"][k] = v


def host(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def main():
    t0 = time.time()
    # Non-sensitive local smoke dir (gitignored at runtime/).
    smoke_dir = os.path.join(ROOT, "runtime", "d2-real-smoke")
    os.makedirs(smoke_dir, exist_ok=True)

    backend = HermesDockerSandboxBackend(image=IMAGE)
    try:
        # 1) create
        ws = backend.create()
        log("1_create", {"workspace": ws, "container": backend._container})
        REPORT["container_redacted"] = backend._container[:12] + "…"

        # 2) health_check (real docker inspect)
        ok = backend.health_check()
        log("2_health_check", {"healthy": ok})

        # 3) execute (bash available; deterministic env probe)
        r = backend.execute(
            "echo HERMES_OK; id -un; bash --version | head -1; git --version")
        log("3_execute", {"exit_code": r.exit_code, "stdout": r.stdout.strip()})

        # configure git identity + default branch INSIDE the container
        backend.execute(
            "git config --global user.email hermes-smoke@local && "
            "git config --global user.name hermes-smoke && "
            "git config --global init.defaultBranch main && "
            "git config --global safe.directory '*'")

        # Build a LOCAL bare remote INSIDE the mounted workdir (host side).
        # It becomes /workspace/bare.git inside the container.
        host(f'git init --bare -q "{os.path.join(ws, "bare.git")}"')

        # 7) git_clone from the local bare remote (empty) into /workspace/repo
        rc = backend.git_clone("file:///workspace/bare.git", "repo")
        log("7_git_clone", {"exit_code": rc.exit_code, "stderr": rc.stderr.strip()})
        # pin the unborn branch to `main` (deterministic; empty-bare clone may
        # otherwise default to the bare repo's HEAD = master)
        backend.execute("git -C repo symbolic-ref HEAD refs/heads/main")

        # 4) write_file  (host ws -> mounted /workspace)
        backend.write_file("repo/README.md", "# D2.5 real smoke\n")
        backend.write_file(
            "repo/verify.sh",
            "#!/bin/sh\n"
            "echo 'verify: start'\n"
            "test -f repo/README.md && echo 'verify: README present'\n"
            "echo 'verify: done'\n")
        log("4_write_file", {"paths": ["repo/README.md", "repo/verify.sh"]})

        # 5) read_file
        content = backend.read_file("repo/README.md")
        log("5_read_file", {"content": content.strip()})

        # 6) edit_file
        backend.edit_file("repo/README.md", "# D2.5 real smoke\n",
                          "# D2.5 real smoke (edited)\n")
        log("6_edit_file", {"content": backend.read_file("repo/README.md").strip()})

        # run the deterministic verify script (explicit interpreter: a mounted
        # Windows file does not carry the exec bit through Docker Desktop)
        rv = backend.execute("bash repo/verify.sh")
        log("3b_execute_verify", {"exit_code": rv.exit_code,
                                  "stdout": rv.stdout.strip()})

        # 8) git status / 9) git diff (before commit)
        rs = backend.git_status("repo")
        rd = backend.git_diff("repo")
        log("8_git_status", {"exit_code": rs.exit_code, "stdout": rs.stdout.strip()})
        log("9_git_diff", {"exit_code": rd.exit_code, "stdout": rd.stdout.strip()[:300]})

        # stage, then 10) git commit + 11) push to local bare remote
        backend.execute("git -C repo add -A")
        rcommit = backend.commit("repo", "add README and verify script")
        log("10_git_commit", {"exit_code": rcommit.exit_code,
                              "stdout": rcommit.stdout.strip()})
        rpush = backend.push("repo", "origin", "main")
        log("11_git_push", {"exit_code": rpush.exit_code,
                            "stdout": rpush.stdout.strip(),
                            "stderr": rpush.stderr.strip()})

        # verify the LOCAL bare remote actually received the commit (host side,
        # before delete() wipes ws). The pushed branch is `main`.
        bare_dir = os.path.join(ws, "bare.git")
        inspect = host(f'git --git-dir="{bare_dir}" log --oneline -1 main 2>&1')
        log("11b_bare_remote_log", {"rc": inspect.returncode,
                                    "out": inspect.stdout.strip() or inspect.stderr.strip()})

        # 12) stop (keep for reuse) then delete
        backend.stop()
        log("12a_stop", {"alive_after_stop": backend.health_check()})
        backend.delete()
        log("12b_delete", {"container_after_delete": backend._container})

        # --- REAL resource limits via docker inspect (host side) ----------
        # (captured BEFORE delete; re-create a probe container for inspect)
        probe = HermesDockerSandboxBackend(image=IMAGE)
        pws = probe.create()
        pname = probe._container
        insp = subprocess.run(
            ["docker", "inspect", pname], capture_output=True, text=True)
        data = json.loads(insp.stdout)[0]
        hc = data["HostConfig"]
        # `--cpus 1` is stored by the daemon as NanoCpus (1e9) on modern Docker;
        # fall back to the CpuQuota/CpuPeriod ratio if present.
        nano = hc.get("NanoCpus") or 0
        cpu_quota = hc.get("CpuQuota") or 0
        cpu_period = hc.get("CpuPeriod") or 100000
        if nano:
            cpus = round(nano / 1_000_000_000, 3)
        elif cpu_quota:
            cpus = round(cpu_quota / cpu_period, 3)
        else:
            cpus = None
        REPORT["inspect"] = {
            "image": data["Config"]["Image"],
            "cpus": cpus,
            "memory_bytes": hc.get("Memory"),
            "pids_limit": hc.get("PidsLimit"),
            "privileged": hc.get("Privileged"),
            "network_mode": hc.get("NetworkMode"),
            "auto_remove": hc.get("AutoRemove"),
            "mounts": [
                {"source": m["Source"], "destination": m["Destination"],
                 "type": m["Type"]}
                for m in data.get("Mounts", [])
            ],
        }
        probe.delete()

        # --- Security / failure scenarios --------------------------------
        sec = HermesDockerSandboxBackend(image=IMAGE)
        sec.create()
        # container must NOT see host docker.sock / ssh / .env
        r_sock = sec.execute("ls -la /var/run/docker.sock 2>&1; "
                             "ls -la /root/.ssh 2>&1; "
                             "ls -la $HOME/.ssh 2>&1")
        REPORT["security"]["no_docker_sock_or_ssh"] = r_sock.stdout.strip()
        # failed exec records exit code
        r_fail = sec.execute("python3 -c 'import sys; sys.exit(7)'")
        REPORT["security"]["failed_exec_exit_code"] = r_fail.exit_code
        # timeout kills the command (host-side subprocess timeout)
        r_to = sec.execute("sleep 30", timeout=5)
        REPORT["security"]["timeout_exit_code"] = r_to.exit_code
        # path escape rejected (host FS escape guard) — checked while ws is live
        escaped = False
        try:
            sec.write_file("../../escape.txt", "x")
        except PermissionError:
            escaped = True
        REPORT["security"]["path_escape_rejected"] = escaped
        # logs contain no token / PEM / Authorization
        flat_log = " ".join(" ".join(c) for c in sec._calls)
        REPORT["security"]["no_secret_in_logs"] = all(
            s not in flat_log for s in ("GITHUB_TOKEN=", ".pem", "Authorization"))
        # timeout -> container still cleaned on delete (idempotent)
        sec.delete()
        sec.delete()  # second call must be a safe no-op (idempotent)

        # residual container check
        rem = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=hermes-",
             "--format", "{{.Names}}"], capture_output=True, text=True)
        REPORT["residual_containers"] = [
            n for n in rem.stdout.splitlines() if n.strip()]

        REPORT["runtime_sec"] = round(time.time() - t0, 2)
        # PASS criteria
        REPORT["pass"] = bool(
            log_pass(REPORT) and REPORT["residual_containers"] == []
            and REPORT["security"]["path_escape_rejected"]
            and REPORT["security"]["no_secret_in_logs"]
            and REPORT["inspect"]["privileged"] is False
            and REPORT["inspect"]["network_mode"] != "host"
            and len(REPORT["inspect"]["mounts"]) == 1)
    finally:
        # best-effort cleanup of any stray container from this run
        try:
            backend.delete()
        except Exception:
            pass

    print(json.dumps(REPORT, indent=2, ensure_ascii=False))


def log_pass(R):
    s = R["steps"]
    insp = R["inspect"]
    checks = [
        s.get("1_create", {}).get("container"),
        s.get("2_health_check", {}).get("healthy") is True,
        s.get("3_execute", {}).get("exit_code") == 0,
        s.get("3b_execute_verify", {}).get("exit_code") == 0,
        s.get("7_git_clone", {}).get("exit_code") == 0,
        s.get("5_read_file", {}).get("content") == "# D2.5 real smoke",
        s.get("6_edit_file", {}).get("content") == "# D2.5 real smoke (edited)",
        s.get("10_git_commit", {}).get("exit_code") == 0,
        s.get("11_git_push", {}).get("exit_code") == 0,
        s.get("11b_bare_remote_log", {}).get("rc") == 0,
        insp.get("cpus") == 1.0,
        insp.get("memory_bytes") == 2147483648,
        insp.get("pids_limit") == 256,
        insp.get("auto_remove") is True,
    ]
    return all(checks)


if __name__ == "__main__":
    main()
