"""Offline security-hardening tests for D3 (no real webhook, no real token).

Covers the six deployment hard-gates promoted from the reviewer's non-blocking
notes:

  1. Worker registration server-side allowlist.
  2. Real deployments must use HTTPS (worker refuses plaintext base_url).
  3. Replay protection: worker API nonce/timestamp; webhook HMAC + delivery dedup.
  4. Mid-job lease keepalive.
  5. Log/command redaction covers command-embedded secrets.
  6. Atomic claim (no TOCTOU) under concurrency.

Everything runs offline against an in-memory-style temp SQLite DB and ephemeral
localhost servers. The webhook uses a FAKE test secret; no GitHub webhook is
enabled, no real token is minted, the smoke-test repo is never touched, and the
relay model is never called.
"""
import os
import sys
import time
import json
import hmac
import hashlib
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.worker_api_server import run_server          # noqa: E402
from hermes_worker.control_plane import ControlPlane, ControlPlaneError  # noqa: E402
from hermes_worker.worker import HermesWorker                   # noqa: E402
from hermes_worker.echo_sandbox import EchoSandboxBackend       # noqa: E402
from hermes_worker.docker_sandbox import HermesDockerSandboxBackend  # noqa: E402
from hermes_worker.webhook_receiver import (                    # noqa: E402
    WebhookReceiver, TEST_WEBHOOK_SECRET, verify_signature,
)
from hermes_worker.protocol import ExecResult                   # noqa: E402
from hermes_worker.db import hash_token                          # noqa: E402

# Four distinct worker tokens, all pre-allowed. A fifth is deliberately NOT.
TOKENS = ["wk-allowed-aaa", "wk-allowed-bbb", "wk-allowed-ccc", "wk-allowed-ddd"]
TOKEN_BAD = "wk-not-allowed-zzz"
ALLOWED_HASHES = {hash_token(t) for t in TOKENS}

ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"
FORBIDDEN_REPO = "yzhlx/hermes-learning-os"
FAKE_GH_TOKEN = "ghp_FAKESECRETPAT0123456789ABCDEFGHIJKL"


def _sign(raw: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


class SlowEchoBackend(EchoSandboxBackend):
    """Echo backend whose execute() blocks briefly to simulate a long job."""
    def __init__(self, sleep: float = 1.0, **kw):
        super().__init__(**kw)
        self._sleep = sleep

    def execute(self, command, cwd=None, env=None, timeout=1200):
        time.sleep(self._sleep)
        return super().execute(command, cwd=cwd, env=env, timeout=timeout)


class TrackingWorker(HermesWorker):
    """Worker that records every job id it claims (for concurrency assertion)."""
    def __init__(self, *a, claimed_log=None, **kw):
        super().__init__(*a, **kw)
        self._claimed_log = claimed_log

    def _run_job(self, job_id, payload):
        if self._claimed_log is not None:
            self._claimed_log.append(job_id)
        super()._run_job(job_id, payload)


class D3SecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mktemp(suffix=".db")
        # Direct control-plane handle (main thread, no threading) for unit tests.
        cls.cp = ControlPlane(cls.db, allowed_token_hashes=ALLOWED_HASHES,
                              replay_window=300)
        # Worker API server (allowed tokens, replay window 300s).
        srv = run_server("127.0.0.1", 0, cls.db, allowed_tokens=TOKENS,
                         replay_window=300, lease_seconds=1200)
        cls.port = srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)
        # Webhook receiver (fake secret, allowlist = smoke-test only).
        cls.webhook = WebhookReceiver(cls.db, TEST_WEBHOOK_SECRET)
        wsrv = cls.webhook.run_server("127.0.0.1", 0)
        cls.wport = wsrv.server_address[1]
        cls.wbase = f"http://127.0.0.1:{cls.wport}"
        cls._wsrv = wsrv
        threading.Thread(target=wsrv.serve_forever, daemon=True).start()
        time.sleep(0.2)

    def setUp(self):
        for t in ("jobs", "events", "workers", "nonces", "deliveries"):
            self.cp.conn.execute(f"DELETE FROM {t}")
        self.cp.conn.commit()
        # The server-side allowlist (item 1) rejects unknown tokens, so every
        # test must start from a registered (allowed) worker set.
        for t in TOKENS:
            self.cp.register(t)

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown(); cls._srv.server_close()
        cls._wsrv.shutdown(); cls._wsrv.server_close()
        time.sleep(0.2)
        for ext in ("", "-wal", "-shm"):
            p = cls.db + ext
            if os.path.exists(p):
                for _ in range(10):
                    try:
                        os.remove(p); break
                    except PermissionError:
                        time.sleep(0.1)

    # ---- helpers ----
    def _raw_worker_post(self, path, token, nonce, ts, body=None):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json",
                     "X-Worker-Token": token, "X-Nonce": nonce,
                     "X-Timestamp": str(int(ts))}, method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=10)
            return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _raw_webhook(self, payload, delivery, event="issues", sig=None,
                     proto="http", secret=TEST_WEBHOOK_SECRET):
        raw = json.dumps(payload).encode()
        if sig is None:
            sig = _sign(raw, secret)
        req = urllib.request.Request(
            self.wbase + "/webhook/github", data=raw,
            headers={"Content-Type": "application/json",
                     "X-Hub-Signature-256": sig,
                     "X-GitHub-Delivery": delivery,
                     "X-GitHub-Event": event,
                     "X-Forwarded-Proto": proto}, method="POST")
        try:
            r = urllib.request.urlopen(req, timeout=10)
            return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    # =================== 1) worker allowlist ===================
    def test_01_allowlist_rejects_unknown_token(self):
        with self.assertRaises(ControlPlaneError):
            self.cp.register(TOKEN_BAD)
        # allowed token registers fine
        self.assertEqual(self.cp.register(TOKENS[0])["ok"], True)

    def test_01b_allowlist_http_rejected(self):
        # A non-allowed token's register request is rejected by the server (400).
        st, _ = self._raw_worker_post("/worker/register", TOKEN_BAD,
                                      "n-allow-bad", time.time(),
                                      {"name": "x"})
        self.assertEqual(st, 400)
        st, _ = self._raw_worker_post("/worker/register", TOKENS[1],
                                      "n-allow-ok", time.time(), {"name": "x"})
        self.assertEqual(st, 200)

    # =================== 2) HTTPS gate ===================
    def test_02_https_required_for_real_deploy(self):
        with self.assertRaises(ValueError):
            HermesWorker("http://127.0.0.1:1", TOKENS[0])  # plaintext not allowed
        # explicit opt-in only for local/offline tests
        HermesWorker("http://127.0.0.1:1", TOKENS[0], insecure_local_ok=True)
        # https is always accepted
        HermesWorker("https://cp.example.com", TOKENS[0])

    # =================== 3a) worker API replay protection ===================
    def test_03a_replay_direct(self):
        self.cp.check_replay("nr1", time.time())            # ok
        with self.assertRaises(ControlPlaneError):
            self.cp.check_replay("nr1", time.time())        # duplicate nonce
        with self.assertRaises(ControlPlaneError):
            self.cp.check_replay("", time.time())           # missing nonce
        with self.assertRaises(ControlPlaneError):
            self.cp.check_replay("nr2", time.time() - 1000)  # stale

    def test_03b_replay_http(self):
        st, _ = self._raw_worker_post("/worker/heartbeat", TOKENS[0],
                                      "nr-http-1", time.time(), {})
        self.assertEqual(st, 200)
        st, body = self._raw_worker_post("/worker/heartbeat", TOKENS[0],
                                         "nr-http-1", time.time(), {})
        self.assertEqual(st, 400)
        self.assertEqual(body.get("error"), "replay_detected")

    # =================== 3b) webhook signature + dedup ===================
    def test_03c_webhook_valid_creates_job(self):
        payload = {"repository": {"full_name": ALLOWED_REPO},
                   "issue": {"number": 7}, "action": "opened"}
        raw = json.dumps(payload).encode()
        res = self.webhook.handle(delivery_id="d-valid-1",
                                  signature=_sign(raw), event="issues",
                                  raw_body=raw)
        self.assertTrue(res["ok"])
        job = self.cp.get_job(res["job_id"])
        self.assertEqual(job["state"], "pending")

    def test_03d_webhook_dedup(self):
        payload = {"repository": {"full_name": ALLOWED_REPO},
                   "issue": {"number": 8}}
        raw = json.dumps(payload).encode()
        before = len(self.cp.list_jobs())
        r1 = self.webhook.handle(delivery_id="d-dup-1", signature=_sign(raw),
                                 event="issues", raw_body=raw)
        r2 = self.webhook.handle(delivery_id="d-dup-1", signature=_sign(raw),
                                 event="issues", raw_body=raw)
        self.assertTrue(r1["ok"])
        self.assertTrue(r2["deduped"])
        self.assertEqual(len(self.cp.list_jobs()), before + 1)  # no new job

    def test_03e_webhook_bad_signature(self):
        payload = {"repository": {"full_name": ALLOWED_REPO}}
        raw = json.dumps(payload).encode()
        with self.assertRaises(ControlPlaneError) as ctx:
            self.webhook.handle(delivery_id="d-bad-1",
                                signature="sha256=deadbeef", event="issues",
                                raw_body=raw)
        self.assertEqual(str(ctx.exception), "bad_signature")

    def test_03f_webhook_disallowed_repo(self):
        for repo in (FORBIDDEN_REPO, "yzhlx/some-other-repo"):
            payload = {"repository": {"full_name": repo}}
            raw = json.dumps(payload).encode()
            with self.assertRaises(ControlPlaneError) as ctx:
                self.webhook.handle(delivery_id="d-repo-" + repo,
                                    signature=_sign(raw), event="issues",
                                    raw_body=raw)
            self.assertEqual(str(ctx.exception), "repo_not_allowed")

    def test_03g_webhook_tls_gate(self):
        recv = WebhookReceiver(self.db, TEST_WEBHOOK_SECRET, require_tls=True)
        payload = {"repository": {"full_name": ALLOWED_REPO}}
        raw = json.dumps(payload).encode()
        with self.assertRaises(ControlPlaneError) as ctx:
            recv.handle(delivery_id="d-tls-1", signature=_sign(raw),
                        event="issues", raw_body=raw, forwarded_proto="http")
        self.assertEqual(str(ctx.exception), "tls_required")
        # over https it is accepted
        res = recv.handle(delivery_id="d-tls-2", signature=_sign(raw),
                          event="issues", raw_body=raw, forwarded_proto="https")
        self.assertTrue(res["ok"])

    def test_03h_webhook_http_layer(self):
        payload = {"repository": {"full_name": ALLOWED_REPO},
                   "issue": {"number": 9}}
        st, _ = self._raw_webhook(payload, "d-http-1")              # valid -> 200
        self.assertEqual(st, 200)
        st, body = self._raw_webhook(payload, "d-http-2",
                                     sig="sha256=wrong")            # bad sig -> 401
        self.assertEqual(st, 401)
        bad = {"repository": {"full_name": FORBIDDEN_REPO}}
        st, _ = self._raw_webhook(bad, "d-http-3")                  # bad repo -> 403
        self.assertEqual(st, 403)
        st, body = self._raw_webhook(payload, "d-http-1")           # dup -> 200 dedup
        self.assertEqual(st, 200)
        self.assertTrue(body.get("deduped"))

    # =================== 4) mid-job lease keepalive ===================
    def test_04a_keepalive_extends_lease(self):
        jid = self.cp.create_job({"command": "x"})
        self.cp.claim(TOKENS[0])
        # force the lease into the past
        self.cp.conn.execute("UPDATE jobs SET lease_expires=? WHERE id=?",
                             (self.cp._now() - 100, jid))
        self.cp.conn.commit()
        now = self.cp._now()
        res = self.cp.keepalive(TOKENS[0], jid)
        self.assertGreater(res["lease_expires"], now + 1190)  # ~ now+1200
        # another worker cannot keepalive a job it does not own
        with self.assertRaises(ControlPlaneError):
            self.cp.keepalive(TOKENS[1], jid)

    def test_04b_worker_keepalive_survives_reaper(self):
        # Short lease (1s) + a reaper that heartbeats every 0.15s. Without
        # keepalive the job would be reaped; with it, the long job completes.
        srv = run_server("127.0.0.1", 0, self.db, allowed_tokens=TOKENS,
                         replay_window=300, lease_seconds=1)
        port = srv.server_address[1]
        base = f"http://127.0.0.1:{port}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)
        try:
            jid = self.cp.create_job({"command": "sleep 1"})
            w = HermesWorker(base, TOKENS[0], backend=SlowEchoBackend(sleep=1.0),
                             insecure_local_ok=True, keepalive_interval=0.2)
            t = threading.Thread(target=w.run_once)
            t.start()
            # concurrent reaper
            deadline = time.time() + 2.0
            while time.time() < deadline and t.is_alive():
                self.cp.heartbeat(TOKENS[1])  # triggers reap_expired_leases
                time.sleep(0.15)
            t.join(timeout=3)
            job = self.cp.get_job(jid)
            self.assertEqual(job["state"], "completed")
            self.assertEqual(job["worker_token_hash"], hash_token(TOKENS[0]))
        finally:
            srv.shutdown(); srv.server_close()

    # =================== 5) command-embedded secret redaction ===================
    def test_05a_docker_calls_redacted(self):
        def fake_runner(args):
            return ExecResult(0, "", "")
        b = HermesDockerSandboxBackend(runner=fake_runner)
        b.create()
        b.execute(f"git push https://{FAKE_GH_TOKEN}@github.com/o/r.git main")
        b.set_github_token(FAKE_GH_TOKEN)
        b.push("repo", "origin", "main")
        flat = " ".join(" ".join(str(a) for a in call) for call in b._calls)
        self.assertNotIn(FAKE_GH_TOKEN, flat)
        self.assertIn("***REDACTED***", flat)

    # =================== 6) atomic claim under concurrency ===================
    def test_06_atomic_claim_no_double(self):
        N = 12
        jids = [self.cp.create_job({"command": "x"}) for _ in range(N)]
        claimed = []
        workers = [
            TrackingWorker(self.base, TOKENS[i], backend=EchoSandboxBackend(),
                           insecure_local_ok=True, claimed_log=claimed)
            for i in range(len(TOKENS))
        ]
        # Bounded loop: each worker claims+completes until the queue is empty,
        # then exits on the first empty claim (no unbounded 2s poll sleep).
        threads = [threading.Thread(target=w.loop, kwargs={"max_iterations": 20})
                   for w in workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        # Every job claimed exactly once, no duplicate, no miss.
        self.assertEqual(len(claimed), N)
        self.assertEqual(set(claimed), set(jids))
        for j in jids:
            self.assertEqual(self.cp.get_job(j)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
