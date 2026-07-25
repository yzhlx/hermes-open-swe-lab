"""Offline tests for Phase D1 (no Docker, no GitHub, no real secrets).

Covers the full Worker API + control-plane + Echo sandbox protocol:
- register idempotency
- full job flow (claim -> run -> complete) with observability columns
- empty claim when no work
- idempotent complete (no overwrite of finished job)
- heartbeat timeout re-queue (lease expiry -> pending)
- event idempotency (duplicate event_id ignored)
- unknown worker token rejected

Run:  python -m unittest tests.test_d1_offline -v
"""
import os
import sys
import time
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.worker_api_server import run_server  # noqa: E402
from hermes_worker.control_plane import ControlPlane  # noqa: E402
from hermes_worker.worker import HermesWorker  # noqa: E402


class D1OfflineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mktemp(suffix=".db")
        srv = run_server(host="127.0.0.1", port=0, db_path=cls.db)
        cls.port = srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.token = "test-worker-token-abc123"
        cls._srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.2)  # let the server thread bind

    def setUp(self):
        # One connection per test (main thread, no threading) keeps the test
        # fast and avoids leaking sqlite handles that would lock the WAL files
        # at teardown. We start every test from a clean queue but always keep
        # the worker token registered, since several tests call cp.claim()
        # directly instead of going through the HermesWorker HTTP path.
        self.cp = ControlPlane(self.db)
        self.cp.conn.execute("DELETE FROM events")
        self.cp.conn.execute("DELETE FROM jobs")
        self.cp.conn.execute("DELETE FROM workers")
        self.cp.conn.commit()
        self.cp.register(self.token)  # idempotent; ensures token is known
        self.cp.conn.commit()

    def tearDown(self):
        self.cp.conn.close()

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown()
        cls._srv.server_close()
        time.sleep(0.2)
        for ext in ("", "-wal", "-shm"):
            p = cls.db + ext
            if not os.path.exists(p):
                continue
            for _ in range(10):
                try:
                    os.remove(p)
                    break
                except PermissionError:
                    time.sleep(0.1)

    def test_01_register_idempotent(self):
        w = HermesWorker(self.base, self.token)
        w.register()
        w.register()  # second call must not error / duplicate
        rows = self.cp.conn.execute("SELECT * FROM workers").fetchall()
        self.assertEqual(len(rows), 1)

    def test_02_full_job_flow(self):
        jid = self.cp.create_job(
            {"command": "echo hi", "model": "m", "role": "coding_agent"},
            task_id="T1", issue_number=1, pr_number=2)
        w = HermesWorker(self.base, self.token)
        self.assertTrue(w.run_once())
        job = self.cp.get_job(jid)
        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["exit_code"], 0)
        self.assertEqual(job["issue_number"], 1)
        self.assertIsNotNone(job["ended_at"])
        evs = self.cp.get_events(jid)
        types = [e["event_type"] for e in evs]
        self.assertIn("sandbox_create", types)
        self.assertIn("execute", types)
        self.assertIn("write_file", types)

    def test_03_empty_claim(self):
        w = HermesWorker(self.base, self.token)
        self.assertFalse(w.run_once())  # no jobs left

    def test_04_idempotent_complete(self):
        jid = self.cp.create_job({"command": "x"})
        r = self.cp.claim(self.token)
        self.assertEqual(r["job_id"], jid)
        self.cp.complete(self.token, jid, {"exit_code": 0})
        r2 = self.cp.complete(self.token, jid, {"exit_code": 99})
        self.assertTrue(r2["already_finished"])
        self.assertEqual(self.cp.get_job(jid)["exit_code"], 0)  # not overwritten

    def test_05_heartbeat_timeout_requel(self):
        jid = self.cp.create_job({"command": "slow"})
        self.cp.claim(self.token)
        # force lease into the past
        self.cp.conn.execute("UPDATE jobs SET lease_expires=? WHERE id=?",
                             (self.cp._now() - 10, jid))
        self.cp.conn.commit()
        tok2 = "worker-two-token"
        self.cp.register(tok2)
        self.cp.heartbeat(tok2)  # reaps expired lease
        job = self.cp.get_job(jid)
        self.assertEqual(job["state"], "pending")
        self.assertIsNone(job["worker_token_hash"])

    def test_06_event_idempotency(self):
        self.cp.create_job({"command": "e"})
        r = self.cp.claim(self.token)  # claim grabs the (only) pending job
        jid = r["job_id"]
        ev = [{"id": "ev-x", "type": "execute", "payload": {"x": 1}}]
        self.cp.post_events(self.token, jid, ev)
        self.cp.post_events(self.token, jid, ev)  # duplicate event_id
        self.assertEqual(len(self.cp.get_events(jid)), 1)

    def test_07_unknown_token_rejected(self):
        with self.assertRaises(Exception):
            self.cp.claim("not-a-real-token")


if __name__ == "__main__":
    unittest.main()
