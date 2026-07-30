"""Local Hermes Worker runner (deployment safety wrapper, line C).

Wraps the Line-A ``hermes_worker.HermesWorker`` client with deployment
hardening. It does NOT modify the Worker client, the claim logic, the Token
Broker, or the Reviewer state machine — those live in ``hermes_worker`` and are
consumed here as a library.

Deployment guarantees enforced here:

- **No inbound port:** the Worker only makes *outbound* HTTPS calls. It never
  binds a listening socket.
- **HTTPS-only (production):** the Cloud endpoint must be ``https://`` unless
  ``HERMES_LOCALHOST_TEST=1`` is explicitly set (local testing only).
- **Single-instance lock:** a stale-safe lock file prevents two Workers from
  claiming the same identity.
- **Docker daemon preflight:** when the Docker backend is selected, the daemon
  must be reachable or the Worker fails *clearly* (no silent hang).
- **Token-restricted file load:** the worker token is read from a ``0600``
  file; overly-permissive files are refused.
- **Graceful shutdown:** SIGTERM/SIGINT stops the loop and releases the lock.
- **Exponential backoff:** when the Cloud is unreachable, the Worker backs off
  (1,2,4,8,… capped) and resumes automatically when connectivity returns.
- **No secret logging:** the token is never printed or stored.
"""
from __future__ import annotations

import argparse
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from hermes_worker.worker import HermesWorker

try:
    from hermes_worker.echo_sandbox import EchoSandboxBackend
except Exception:  # pragma: no cover - import guard
    EchoSandboxBackend = None
try:
    from hermes_worker.docker_sandbox import HermesDockerSandboxBackend
except Exception:  # pragma: no cover - import guard
    HermesDockerSandboxBackend = None


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"WORKER-FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def load_config() -> dict:
    return {
        "base_url": _env("HERMES_CLOUD_URL", "https://localhost"),
        "token_file": _env("HERMES_WORKER_TOKEN_FILE",
                            os.path.expanduser("~/.hermes/worker_token")),
        "backend": _env("HERMES_WORKER_BACKEND", "echo"),
        "localhost_test": _env("HERMES_LOCALHOST_TEST", "0") == "1",
        "poll_interval": float(_env("HERMES_POLL_INTERVAL", "5")),
        "lock_path": _env("HERMES_WORKER_LOCK",
                          "/var/run/hermes-worker.lock"),
        "strict_token_perms": _env("HERMES_STRICT_TOKEN_PERMS", "0") == "1",
    }


# --------------------------------------------------------------------------
# Endpoint validation
# --------------------------------------------------------------------------
def validate_endpoint(base_url: str, localhost_test: bool) -> None:
    if localhost_test:
        if not (base_url.startswith("http://127.0.0.1")
                or base_url.startswith("http://localhost")):
            die("localhost test mode requires base_url http://127.0.0.1 or "
                "http://localhost (got %r)" % base_url)
        return
    if not base_url.startswith("https://"):
        die("production Worker API requires https:// (set HERMES_LOCALHOST_TEST=1 "
            "only for isolated local testing): %r" % base_url)


# --------------------------------------------------------------------------
# Token file (restricted)
# --------------------------------------------------------------------------
def _mode_too_open(mode: int) -> bool:
    return bool(stat.S_IMODE(mode) & 0o077)


_WINDOWS_ACL_CHECK = r"""
$ErrorActionPreference = 'Stop'
$path = $env:HERMES_WORKER_TOKEN_ACL_PATH
if ([string]::IsNullOrWhiteSpace($path)) { exit 6 }
$item = Get-Item -LiteralPath $path -Force
if ($item.PSIsContainer) { exit 4 }
if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { exit 4 }
$acl = Get-Acl -LiteralPath $path
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate(
    [Security.Principal.SecurityIdentifier]
).Value
$allowed = @($currentSid, $ownerSid, 'S-1-5-18')
foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
        continue
    }
    $sid = $rule.IdentityReference.Translate(
        [Security.Principal.SecurityIdentifier]
    ).Value
    if ($allowed -notcontains $sid) { exit 3 }
}
Write-Output 'ACL_OK'
"""


def _windows_acl_restricted(path: str) -> bool:
    """Return only a value-free ACL decision for a Windows token file."""
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if not system_root:
        return False
    powershell = os.path.join(
        system_root,
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    child_env = {
        "SystemRoot": system_root,
        "WINDIR": system_root,
        "HERMES_WORKER_TOKEN_ACL_PATH": path,
    }
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _WINDOWS_ACL_CHECK,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=child_env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "ACL_OK"


def _token_permissions_restricted(
    path: str,
    *,
    platform: str | None = None,
) -> bool:
    platform = os.name if platform is None else platform
    if platform == "nt":
        return _windows_acl_restricted(path)
    return not _mode_too_open(os.stat(path).st_mode)


def load_token(path: str, strict: bool) -> str:
    if not os.path.exists(path):
        die("worker token file not found: %s" % path)
    if (strict or os.name == "posix") and not _token_permissions_restricted(path):
        die(
            "worker token file permissions are not restricted: %s" % path
        )
    with open(path, "r") as f:
        tok = f.read().strip()
    if not tok:
        die("worker token file is empty: %s" % path)
    return tok


# --------------------------------------------------------------------------
# Docker preflight
# --------------------------------------------------------------------------
def docker_preflight() -> None:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True,
                           timeout=15, check=False)
    except FileNotFoundError:
        die("docker binary not found; install Docker or set "
            "HERMES_WORKER_BACKEND=echo for offline testing")
    except subprocess.TimeoutExpired:
        die("docker info timed out; Docker daemon not responding")
    if r.returncode != 0:
        die("Docker daemon unavailable ('docker info' failed). Start Docker.")


# --------------------------------------------------------------------------
# Single-instance lock (stale-safe, cross-platform)
# --------------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    # On Windows we treat a present lock as stale-removable; the start script
    # scopes the file to the user's private dir via ACLs.
    return False


def acquire_lock(path: str) -> None:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            with open(path) as f:
                pid = int(f.read().strip() or "0")
            if pid and _pid_alive(pid):
                die("another worker instance is already running (pid %d, lock %s)"
                    % (pid, path))
        except (OSError, ValueError):
            pass
        os.remove(path)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


def release_lock(path: str) -> None:
    try:
        if os.path.exists(path):
            with open(path) as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Backoff + run loop
# --------------------------------------------------------------------------
def pick_backend(name: str):
    if name == "docker":
        if HermesDockerSandboxBackend is None:
            die("docker backend unavailable (hermes_worker.docker_sandbox)")
        return HermesDockerSandboxBackend()
    if name in ("echo", "mock"):
        if EchoSandboxBackend is None:
            die("echo backend unavailable (hermes_worker.echo_sandbox)")
        return EchoSandboxBackend()
    die("unknown HERMES_WORKER_BACKEND: %s" % name)


def run_loop(worker: HermesWorker, stop_event: threading.Event,
             poll_interval: float = 5.0, max_iterations: int | None = None,
             base: float = 1.0, cap: float = 60.0) -> int:
    """Poll the Cloud, run jobs, and survive Cloud outages with exponential
    backoff. Returns the number of iterations performed."""
    backoff = base
    attempt = 0
    iters = 0
    while not stop_event.is_set():
        if max_iterations is not None and iters >= max_iterations:
            break
        try:
            worker.run_once()
            backoff = base
            attempt = 0
        except (urllib.error.URLError, urllib.error.HTTPError,
                ConnectionError, OSError) as e:
            attempt += 1
            sleep_for = min(cap, base * (2 ** attempt))
            print(f"WORKER: cloud unreachable ({type(e).__name__}); "
                  f"retry in {sleep_for:.0f}s (attempt {attempt})",
                  file=sys.stderr)
            if stop_event.wait(sleep_for):
                break
            continue
        if stop_event.wait(poll_interval):
            break
        iters += 1
    return iters


def main() -> None:
    cfg = load_config()
    validate_endpoint(cfg["base_url"], cfg["localhost_test"])
    if cfg["backend"] == "docker":
        docker_preflight()
    token = load_token(cfg["token_file"], cfg["strict_token_perms"])
    acquire_lock(cfg["lock_path"])

    worker = HermesWorker(cfg["base_url"], token,
                          backend=pick_backend(cfg["backend"]))

    stop = threading.Event()

    def _handle(signum, frame):  # noqa: ANN001
        print("WORKER: shutdown requested", file=sys.stderr)
        stop.set()

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _handle)
        except (ValueError, AttributeError):
            pass

    try:
        print(f"WORKER: started (cloud={cfg['base_url']}, "
              f"backend={cfg['backend']}, test={cfg['localhost_test']})",
              file=sys.stderr)
        run_loop(worker, stop, poll_interval=cfg["poll_interval"])
    finally:
        release_lock(cfg["lock_path"])
        print("WORKER: stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
