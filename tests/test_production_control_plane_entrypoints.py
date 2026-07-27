"""PRODUCTION-CONTROL-PLANE-ENTRYPOINTS-FAIL-CLOSED-HARDENING.

Verifies the 3 production ``ControlPlane`` entry points
(``hermes_worker.event_router``, ``hermes_worker.webhook_receiver``,
``deploy/cloud/control_plane_app``) never silently fall back to dev allow-all:

  * production entry with no / empty / whitespace-only ``ALLOWED_WORKER_TOKENS``
    -> startup fails (no listen, no ControlPlane, no allow-all);
  * production entry with a valid allowlist -> ``ControlPlane(mode="production"
    with a non-empty allowed_token_hashes)`` is actually built;
  * an unlisted worker token is rejected (``worker_not_allowlisted``);
  * the raw token never appears in logs, exceptions, or HTTP responses;
  * ``HERMES_HUMAN_OWNER_TOKEN`` is never accepted as a worker token.

All token parsing reuses the SINGLE PB-5 helper
:func:`hermes_worker.worker_api_server.resolve_production_worker_token_hashes`
so there is exactly one parse implementation (no copied logic).
"""
import os
import sys
import io
import hmac
import hashlib
import sqlite3
import subprocess
import logging
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.worker_api_server import (          # noqa: E402
    resolve_production_worker_token_hashes, WorkerTokenConfigError,
)
from hermes_worker.db import hash_token                 # noqa: E402
from hermes_worker import event_router as er_mod        # noqa: E402
from hermes_worker import webhook_receiver as wr_mod     # noqa: E402


VALID = "prod-valid-worker-token-AAA-111"
UNLISTED = "prod-unlisted-worker-token-BBB-222"
OWNER = "prod-human-owner-token-CCC-333"
SENTINEL = "sentinel-worker-token-do-not-leak-XYZ"


def _clean(db):
    for ext in ("", "-wal", "-shm"):
        p = db + ext
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


class SharedHelperFailClosed(unittest.TestCase):
    """The single token-parse helper is fail-closed and never returns an empty
    set."""

    def test_no_token_raises(self):
        with self.assertRaises(WorkerTokenConfigError):
            resolve_production_worker_token_hashes(None)

    def test_empty_token_raises(self):
        with self.assertRaises(WorkerTokenConfigError):
            resolve_production_worker_token_hashes("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(WorkerTokenConfigError):
            resolve_production_worker_token_hashes("   ,  ,  ")

    def test_valid_returns_nonempty_set(self):
        hashes = resolve_production_worker_token_hashes("a, b ,c")
        self.assertEqual(hashes,
                         {hash_token("a"), hash_token("b"), hash_token("c")})

    def test_human_owner_token_rejected_as_worker(self):
        # The Human-Owner token must stay independent from worker tokens:
        # listing it in ALLOWED_WORKER_TOKENS is rejected.
        old = os.environ.get("HERMES_HUMAN_OWNER_TOKEN")
        os.environ["HERMES_HUMAN_OWNER_TOKEN"] = OWNER
        try:
            with self.assertRaises(WorkerTokenConfigError):
                resolve_production_worker_token_hashes(f"{VALID},{OWNER}")
        finally:
            if old is None:
                os.environ.pop("HERMES_HUMAN_OWNER_TOKEN", None)
            else:
                os.environ["HERMES_HUMAN_OWNER_TOKEN"] = old


class ProductionEntryConstructionFailClosed(unittest.TestCase):
    """Constructing the production objects with no allowlist must refuse."""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")

    def tearDown(self):
        _clean(self.db)

    def test_event_router_prod_without_hashes_raises(self):
        with self.assertRaises(WorkerTokenConfigError):
            er_mod.EventRouter(self.db, "secret", mode="production")

    def test_webhook_receiver_prod_without_hashes_raises(self):
        with self.assertRaises(WorkerTokenConfigError):
            wr_mod.WebhookReceiver(self.db, "secret", mode="production")

    def test_control_plane_prod_empty_hashes_raises(self):
        with self.assertRaises(ControlPlaneError):
            ControlPlane(self.db, allowed_token_hashes=set(), mode="production")


class ProductionModeExplicit(unittest.TestCase):
    """A valid allowlist in production mode builds a production ControlPlane."""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.hashes = {hash_token(VALID)}

    def tearDown(self):
        _clean(self.db)

    def test_event_router_prod_valid_constructs(self):
        r = er_mod.EventRouter(self.db, "secret",
                               allowed_token_hashes=self.hashes, mode="production")
        self.assertEqual(r.mode, "production")

    def test_webhook_receiver_prod_valid_constructs(self):
        r = wr_mod.WebhookReceiver(self.db, "secret",
                                   allowed_token_hashes=self.hashes, mode="production")
        self.assertEqual(r.mode, "production")

    def test_control_plane_prod_valid_mode_and_register(self):
        cp = ControlPlane(self.db, allowed_token_hashes=self.hashes,
                          mode="production")
        self.assertEqual(cp.mode, "production")
        self.assertEqual(cp.register(VALID)["ok"], True)

    def test_unlisted_worker_rejected(self):
        cp = ControlPlane(self.db, allowed_token_hashes=self.hashes,
                          mode="production")
        with self.assertRaises(ControlPlaneError) as ctx:
            cp.register(UNLISTED)
        self.assertEqual(str(ctx.exception), "worker_not_allowlisted")

    def test_event_router_handle_builds_production_controlplane(self):
        # Spy on ControlPlane construction inside handle() to prove the
        # production entry actually builds ControlPlane(mode="production",
        # allowed_token_hashes=<valid set>) — never dev allow-all.
        r = er_mod.EventRouter(self.db, "secret",
                               allowed_token_hashes=self.hashes, mode="production")
        captured = {}
        real_cp = er_mod.ControlPlane

        class FakeCP:
            def record_delivery(self, d):
                return False  # -> handle() takes the deduped early-return

            conn = None

            def _now(self):
                return 0.0

            def close(self):
                pass

        def spy(*a, **kw):
            captured.update(kw)
            return FakeCP()

        er_mod.ControlPlane = spy
        try:
            body = (b'{"repository":{"full_name":'
                    b'"yzhlx/hermes-open-swe-smoke-test"},'
                    b'"issue":{"number":1},"action":"opened"}')
            sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
            r.handle(delivery_id="d1", signature=sig, event="issues",
                     raw_body=body)
        finally:
            er_mod.ControlPlane = real_cp
        self.assertEqual(captured.get("mode"), "production")
        self.assertEqual(captured.get("allowed_token_hashes"), self.hashes)

    def test_webhook_receiver_handle_builds_production_controlplane(self):
        r = wr_mod.WebhookReceiver(self.db, "secret",
                                   allowed_token_hashes=self.hashes, mode="production")
        captured = {}
        real_cp = wr_mod.ControlPlane

        class FakeConn:
            def execute(self, *a, **k):
                raise sqlite3.IntegrityError()  # -> dedup branch

            def commit(self):
                pass

        class FakeCP:
            conn = FakeConn()

            def _now(self):
                return 0.0

            def close(self):
                pass

        def spy(*a, **kw):
            captured.update(kw)
            return FakeCP()

        wr_mod.ControlPlane = spy
        try:
            body = (b'{"repository":{"full_name":'
                    b'"yzhlx/hermes-open-swe-smoke-test"},'
                    b'"issue":{"number":1}}')
            sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
            r.handle(delivery_id="d1", signature=sig, event="issues",
                     raw_body=body)
        finally:
            wr_mod.ControlPlane = real_cp
        self.assertEqual(captured.get("mode"), "production")
        self.assertEqual(captured.get("allowed_token_hashes"), self.hashes)


class NoTokenLeak(unittest.TestCase):
    """Raw tokens must never reach logs, errors, or responses."""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.hashes = {hash_token(VALID)}
        self.buf = io.StringIO()
        self.handler = logging.StreamHandler(self.buf)
        self.handler.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(self.handler)

    def tearDown(self):
        logging.getLogger().removeHandler(self.handler)
        _clean(self.db)

    def test_rejected_register_no_token_in_logs(self):
        cp = ControlPlane(self.db, allowed_token_hashes=self.hashes,
                          mode="production")
        try:
            cp.register(UNLISTED)
        except ControlPlaneError:
            pass
        logged = self.buf.getvalue()
        self.assertNotIn(UNLISTED, logged)
        self.assertNotIn(VALID, logged)


class ProductionMainsFailClosed(unittest.TestCase):
    """Real ``main()`` entry points abort before binding a socket when
    ``ALLOWED_WORKER_TOKENS`` is missing / empty / whitespace-only, or when it
    collides with the Human-Owner token. No token value is printed."""

    def _run_entry(self, module, env_extra):
        env = dict(os.environ)
        env.pop("ALLOWED_WORKER_TOKENS", None)
        env.pop("HERMES_HUMAN_OWNER_TOKEN", None)
        env.update(env_extra)
        return subprocess.run(
            [sys.executable, "-m", module], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=30)

    def test_control_plane_app_no_token(self):
        for val in (None, "", "   ,  "):
            with self.subTest(val=val):
                env = {} if val is None else {"ALLOWED_WORKER_TOKENS": val}
                proc = self._run_entry("deploy.cloud.control_plane_app", env)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("SECURITY", proc.stderr)
                self.assertNotIn(SENTINEL, proc.stderr)
                self.assertNotIn(SENTINEL, proc.stdout)

    def test_webhook_receiver_no_token(self):
        for val in (None, "", "   ,  "):
            with self.subTest(val=val):
                env = {} if val is None else {"ALLOWED_WORKER_TOKENS": val}
                proc = self._run_entry("hermes_worker.webhook_receiver", env)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("FATAL", proc.stderr)

    def test_event_router_no_token(self):
        for val in (None, "", "   ,  "):
            with self.subTest(val=val):
                env = {} if val is None else {"ALLOWED_WORKER_TOKENS": val}
                proc = self._run_entry("hermes_worker.event_router", env)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("FATAL", proc.stderr)

    def test_owner_collision_leaks_no_token(self):
        # ALLOWED_WORKER_TOKENS == HERMES_HUMAN_OWNER_TOKEN must be rejected and
        # the token value must never be printed.
        env = {"ALLOWED_WORKER_TOKENS": SENTINEL,
               "HERMES_HUMAN_OWNER_TOKEN": SENTINEL}
        for module in ("deploy.cloud.control_plane_app",
                       "hermes_worker.webhook_receiver",
                       "hermes_worker.event_router"):
            with self.subTest(module=module):
                proc = self._run_entry(module, env)
                self.assertEqual(proc.returncode, 2)
                self.assertNotIn(SENTINEL, proc.stderr)
                self.assertNotIn(SENTINEL, proc.stdout)


if __name__ == "__main__":
    unittest.main()
