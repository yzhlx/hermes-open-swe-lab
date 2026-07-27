"""PB-5: Enforce ALLOWED_WORKER_TOKENS fail-closed (deny-by-default).

ControlPlane and the Worker API must fail-closed in production mode:

  * production + no token config (``None`` / empty) -> construction/startup fails;
  * production + missing / empty / malformed ``ALLOWED_WORKER_TOKENS`` -> fails;
  * a configured (allowed) token can register / heartbeat / claim / submit results;
  * an unlisted token is rejected (direct raise + HTTP 400);
  * the raw token never appears in logs or exception/error messages;
  * test/dev mode with an explicitly injected fake token still runs (injectability).

No real secrets are used: tokens are opaque test strings and only their sha256
hashes are stored. The raw strings are never logged or echoed.
"""
import os
import sys
import io
import time
import json
import tempfile
import threading
import logging
import unittest
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.worker_api_server import (          # noqa: E402
    run_server, resolve_production_worker_tokens, WorkerTokenConfigError,
)
from hermes_worker.control_plane import ControlPlane, ControlPlaneError  # noqa: E402
from hermes_worker.db import hash_token                 # noqa: E402


# Opaque test tokens (NOT secrets; never logged).
VALID = "pb5-valid-worker-token-001"
UNLISTED = "pb5-unlisted-worker-token-002"
FAKE = "pb5-fake-dev-token-003"
# Independent Human-Owner token for production-mode ControlPlane constructions.
OWNER = "pb5-human-owner-token-004"


def _clean(db):
    for ext in ("", "-wal", "-shm"):
        p = db + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _post(base, path, token, nonce, body, owner=None):
    data = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json",
               "X-Worker-Token": token, "X-Nonce": nonce,
               "X-Timestamp": str(int(time.time()))}
    # The Human-Owner acceptance gate uses a DEDICATED header (never the worker
    # token) so a worker token can never impersonate the Human Owner.
    if owner:
        headers["X-Human-Owner-Token"] = owner
    req = urllib.request.Request(
        base + path, data=data, headers=headers, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class ProductionControlPlaneFailClosed(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        _clean(self.db)

    def test_prod_none_fails(self):
        with self.assertRaises(ControlPlaneError):
            ControlPlane(self.db, mode="production")

    def test_prod_empty_set_fails(self):
        with self.assertRaises(ControlPlaneError):
            ControlPlane(self.db, mode="production", allowed_token_hashes=set())

    def test_prod_valid_set_ok(self):
        cp = ControlPlane(self.db, mode="production",
                          allowed_token_hashes={hash_token(VALID)},
                          human_owner_token=OWNER)
        self.assertEqual(cp.register(VALID)["ok"], True)

    def test_prod_unlisted_rejected_and_no_token_leak(self):
        cp = ControlPlane(self.db, mode="production",
                          allowed_token_hashes={hash_token(VALID)},
                          human_owner_token=OWNER)
        with self.assertRaises(ControlPlaneError) as ctx:
            cp.register(UNLISTED)
        # Error is a generic digest; it must not contain the raw token.
        self.assertEqual(str(ctx.exception), "worker_not_allowlisted")
        self.assertNotIn(UNLISTED, str(ctx.exception))
        self.assertNotIn(VALID, str(ctx.exception))


class ProductionEnvFailClosed(unittest.TestCase):
    def test_env_unset_fails(self):
        with self.assertRaises(WorkerTokenConfigError):
            resolve_production_worker_tokens(None)

    def test_env_empty_fails(self):
        with self.assertRaises(WorkerTokenConfigError):
            resolve_production_worker_tokens("")

    def test_env_malformed_fails(self):
        # Non-empty but yields no usable tokens (only separators/whitespace).
        with self.assertRaises(WorkerTokenConfigError):
            resolve_production_worker_tokens("   ,  ,  ")

    def test_env_valid_ok(self):
        toks = resolve_production_worker_tokens("a, b ,c")
        self.assertEqual(set(toks), {"a", "b", "c"})


class ProductionServerFailClosed(unittest.TestCase):
    def test_server_prod_no_tokens_fails(self):
        db = tempfile.mktemp(suffix=".db")
        try:
            with self.assertRaises(WorkerTokenConfigError):
                run_server("127.0.0.1", 0, db, allowed_tokens=[],
                           mode="production")
        finally:
            _clean(db)

    def test_server_prod_valid_token_runs_and_rejects_unlisted(self):
        db = tempfile.mktemp(suffix=".db")
        try:
            srv = run_server("127.0.0.1", 0, db, allowed_tokens=[VALID],
                             mode="production", human_owner_token=OWNER)
            port = srv.server_address[1]
            base = f"http://127.0.0.1:{port}"
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            time.sleep(0.2)

            # Allowed token registers (200).
            st, _ = _post(base, "/worker/register", VALID, "n-v-1", {"name": "v"})
            self.assertEqual(st, 200)

            # Unlisted token is rejected (400) and the error body is a digest
            # that contains no raw token.
            st2, body2 = _post(base, "/worker/register", UNLISTED, "n-u-1",
                               {"name": "u"})
            self.assertEqual(st2, 400)
            self.assertEqual(body2.get("error"), "worker_not_allowlisted")
            self.assertNotIn(UNLISTED, json.dumps(body2))
            self.assertNotIn(VALID, json.dumps(body2))

            # Full flow for the allowed token: heartbeat -> claim -> final
            # acceptance (worker opens the gate after green CI) -> Human-Owner
            # acceptance -> complete. Production complete() is gated on
            # FINAL_ACCEPTED (PB-23 P0), so the acceptance chain must run first.
            st3, _ = _post(base, "/worker/heartbeat", VALID, "n-h-1", {})
            self.assertEqual(st3, 200)

            cp = ControlPlane(db, mode="production",
                              allowed_token_hashes={hash_token(VALID)},
                              human_owner_token=OWNER)
            jid = cp.create_job({"command": "x"})

            st4, b4 = _post(base, "/worker/jobs/claim", VALID, "n-c-1", {})
            self.assertEqual(st4, 200)
            self.assertEqual(b4["job_id"], jid)

            # Mark CI green, then open the final-acceptance gate (worker token).
            cp.store_agent_result(jid, {"ci_status": "success"})
            st_ra, _ = _post(base, f"/worker/jobs/{jid}/request-final-acceptance",
                            VALID, "n-ra-1", {})
            self.assertEqual(st_ra, 200)

            # Human-Owner accepts via the dedicated header (independent secret).
            st_fa, _ = _post(base, f"/worker/jobs/{jid}/final-accept", VALID,
                            "n-fa-1", {}, owner=OWNER)
            self.assertEqual(st_fa, 200)

            # Now production completion is authorized.
            st5, _ = _post(base, f"/worker/jobs/{jid}/complete", VALID,
                          "n-co-1", {"result": {"exit_code": 0}})
            self.assertEqual(st5, 200)

            # An unlisted token cannot submit results (rejected before any
            # completion event is written).
            st6, body6 = _post(base, f"/worker/jobs/{jid}/complete", UNLISTED,
                              "n-co-2", {"result": {}})
            self.assertEqual(st6, 400)

            srv.shutdown(); srv.server_close()
        finally:
            _clean(db)


class NoTokenLeakInLogs(unittest.TestCase):
    def test_rejected_request_token_not_logged(self):
        db = tempfile.mktemp(suffix=".db")
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            srv = run_server("127.0.0.1", 0, db, allowed_tokens=[VALID],
                             mode="production", human_owner_token=OWNER)
            port = srv.server_address[1]
            base = f"http://127.0.0.1:{port}"
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            time.sleep(0.2)
            # Rejected registration attempt with the unlisted raw token.
            _post(base, "/worker/register", UNLISTED, "n-leak-1", {"name": "u"})
            srv.shutdown(); srv.server_close()
            logged = buf.getvalue()
            # The raw token must never reach any log sink.
            self.assertNotIn(UNLISTED, logged)
            self.assertNotIn(VALID, logged)
        finally:
            root.removeHandler(handler)
            _clean(db)


class DevModeInjectionStillRuns(unittest.TestCase):
    def test_dev_explicit_fake_token(self):
        db = tempfile.mktemp(suffix=".db")
        try:
            cp = ControlPlane(db, mode="dev",
                              allowed_token_hashes={hash_token(FAKE)})
            self.assertEqual(cp.register(FAKE)["ok"], True)
            # dev allow-all is still available when no list is supplied.
            cp2 = ControlPlane(db, mode="dev")
            self.assertEqual(cp2.register("any-dev-token")["ok"], True)
        finally:
            _clean(db)

    def test_dev_server_explicit_fake_token(self):
        db = tempfile.mktemp(suffix=".db")
        try:
            srv = run_server("127.0.0.1", 0, db, allowed_tokens=[FAKE],
                             mode="dev")
            port = srv.server_address[1]
            base = f"http://127.0.0.1:{port}"
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            time.sleep(0.2)
            st, _ = _post(base, "/worker/register", FAKE, "n-d-1", {})
            self.assertEqual(st, 200)
            srv.shutdown(); srv.server_close()
        finally:
            _clean(db)


if __name__ == "__main__":
    unittest.main()
