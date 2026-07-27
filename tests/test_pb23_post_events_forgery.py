"""Tests for the PB-23 post_events event-forgery bypass fix.

Security context
----------------
A job-owning worker could POST a forged ``FINAL_ACCEPTED`` (or ``TASK_COMPLETED``)
event through ``ControlPlane.post_events`` because that method wrote whatever
``event_type`` the worker supplied with NO allowlist. The production HTTP entry
point (``worker_api_server`` / ``control_plane_app``) forwards the worker-supplied
``events`` array straight to ``post_events``. After the forgery, ``complete()``
saw ``_has_event(job_id, FINAL_ACCEPTED) == True`` and completed WITHOUT any
Human-Owner token — defeating BOTH P0 (completion before acceptance) and P1
(independent owner secret).

Fix
---
``post_events`` is now deny-by-default: only the worker-writable allowlist
(``constants.WORKER_WRITABLE_EVENTS`` = {sandbox_create, execute, write_file})
is accepted. ANY other type — including every control-plane reserved event and
any unknown type — is rejected (``ControlPlaneError: worker_event_type_not_allowed``)
BEFORE any DB write, so the batch is atomically refused (no partial insert).
Reserved events are produced ONLY by the trusted internal methods
(``request_final_acceptance`` / ``final_accept`` / ``complete``) and ``append_event``.

These tests drive ``ControlPlane`` directly (no HTTP).
"""
import os
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.control_plane import ControlPlane, ControlPlaneError
from hermes_worker.db import hash_token
from hermes_worker import constants


WORKER_TOKEN = "test-worker-token"
# Independent Human-Owner secret (distinct from the worker token).
OWNER_TOKEN = "test-owner-token-secret"


def _tmp_db():
    path = os.path.join(
        tempfile.gettempdir(), f"pb23-forgery-{uuid.uuid4().hex}.db")
    if os.path.exists(path):
        os.remove(path)
    return path


def _make_cp(mode: str):
    """Build a ControlPlane in the requested mode with a registered worker."""
    db = _tmp_db()
    if mode == "production":
        # Production is fail-closed: explicit allowlist + owner token required.
        cp = ControlPlane(
            db, mode="production",
            allowed_token_hashes={hash_token(WORKER_TOKEN)},
            human_owner_token=OWNER_TOKEN,
        )
    else:
        cp = ControlPlane(db, mode="dev")
    return cp, db


def _setup_job(cp: ControlPlane) -> int:
    """Register the worker, create a job, and make the worker own it."""
    cp.register(WORKER_TOKEN, name="w")
    jid = cp.create_job(
        payload={"task_id": "T1", "repository": "x/y"},
        task_id="T1", repo="x/y", role="coding_agent")
    cp.update_job(jid, worker_token_hash=hash_token(WORKER_TOKEN))
    return jid


class PostEventsForgeryTests(unittest.TestCase):
    # (a) Worker forges FINAL_ACCEPTED -> rejected, event count unchanged,
    #     and complete() still raises completion_before_acceptance.
    def test_worker_forges_final_accepted_rejected(self):
        cp, _ = _make_cp("production")
        jid = _setup_job(cp)
        before = len(cp.get_events(jid))
        with self.assertRaises(ControlPlaneError) as ctx:
            cp.post_events(WORKER_TOKEN, jid,
                           [{"id": "x", "type": constants.FINAL_ACCEPTED}])
        self.assertEqual(str(ctx.exception), "worker_event_type_not_allowed")
        # No event row written by the rejected forgery.
        self.assertEqual(len(cp.get_events(jid)), before)
        # The forgery did not take: completion is still refused (P0 intact).
        with self.assertRaises(ControlPlaneError) as c2:
            cp.complete(WORKER_TOKEN, jid,
                        {"result": {"result": "TASK_COMPLETED"}})
        self.assertEqual(str(c2.exception), "completion_before_acceptance")

    # (b) Worker forges TASK_COMPLETED -> rejected, job state unchanged,
    #     and complete() still raises completion_before_acceptance.
    def test_worker_forges_task_completed_rejected(self):
        cp, _ = _make_cp("production")
        jid = _setup_job(cp)
        before_state = cp.get_job(jid)["state"]
        with self.assertRaises(ControlPlaneError) as ctx:
            cp.post_events(WORKER_TOKEN, jid,
                           [{"id": "x", "type": constants.TASK_COMPLETED}])
        self.assertEqual(str(ctx.exception), "worker_event_type_not_allowed")
        # Job state unchanged by the rejected forgery.
        self.assertEqual(cp.get_job(jid)["state"], before_state)
        with self.assertRaises(ControlPlaneError) as c2:
            cp.complete(WORKER_TOKEN, jid,
                        {"result": {"result": "TASK_COMPLETED"}})
        self.assertEqual(str(c2.exception), "completion_before_acceptance")

    # (c) Mixed batch (one allowed + one reserved) -> ENTIRE batch atomically
    #     rejected, ZERO events written (no partial insert).
    def test_mixed_batch_atomically_rejected(self):
        cp, _ = _make_cp("production")
        jid = _setup_job(cp)
        before = len(cp.get_events(jid))
        with self.assertRaises(ControlPlaneError) as ctx:
            cp.post_events(WORKER_TOKEN, jid, [
                {"id": "a", "type": "execute"},
                {"id": "b", "type": constants.FINAL_ACCEPTED},
            ])
        self.assertEqual(str(ctx.exception), "worker_event_type_not_allowed")
        # Neither the allowed nor the reserved event was written.
        self.assertEqual(len(cp.get_events(jid)), before)
        types = {e["event_type"] for e in cp.get_events(jid)}
        self.assertNotIn("execute", types)
        self.assertNotIn(constants.FINAL_ACCEPTED, types)

    # (d) Only legitimate ordinary events -> accepted, count increases; the
    #     full PB-23 downstream chain still completes normally.
    def test_legitimate_events_accepted_and_downstream_works(self):
        cp, _ = _make_cp("production")
        jid = _setup_job(cp)
        res = cp.post_events(WORKER_TOKEN, jid, [
            {"id": "a", "type": "sandbox_create", "payload": {}},
            {"id": "b", "type": "execute", "payload": {}},
            {"id": "c", "type": "write_file", "payload": {}},
        ])
        self.assertEqual(res["accepted"], 3)
        self.assertEqual(len(cp.get_events(jid)), 3)
        types = {e["event_type"] for e in cp.get_events(jid)}
        self.assertEqual(types, {"sandbox_create", "execute", "write_file"})

        # Downstream PB-23 chain is unaffected by the telemetry events.
        cp.update_job(jid, ci_status="success")
        cp.request_final_acceptance(WORKER_TOKEN, jid)
        cp.final_accept(OWNER_TOKEN, jid)
        cp.complete(WORKER_TOKEN, jid,
                    {"result": {"result": "TASK_COMPLETED"}})
        self.assertEqual(cp.get_job(jid)["state"], "completed")
        # 3 telemetry + FINAL_ACCEPTANCE + FINAL_ACCEPTED + TASK_COMPLETED.
        self.assertEqual(len(cp.get_events(jid)), 6)

    # (e) Forgery rejection in BOTH production and dev mode; in production the
    #     gate is also enforced by complete() (forgery absence -> still blocked).
    def test_forgery_rejected_in_both_modes(self):
        for mode in ("dev", "production"):
            with self.subTest(mode=mode):
                cp, _ = _make_cp(mode)
                jid = _setup_job(cp)
                before = len(cp.get_events(jid))
                with self.assertRaises(ControlPlaneError):
                    cp.post_events(WORKER_TOKEN, jid,
                                   [{"id": "x", "type": constants.FINAL_ACCEPTED}])
                # Zero events written in either mode.
                self.assertEqual(len(cp.get_events(jid)), before)

        # Production additionally refuses completion because the forged event
        # was never persisted.
        cp, _ = _make_cp("production")
        jid = _setup_job(cp)
        with self.assertRaises(ControlPlaneError):
            cp.post_events(WORKER_TOKEN, jid,
                           [{"id": "x", "type": constants.FINAL_ACCEPTED}])
        with self.assertRaises(ControlPlaneError) as c2:
            cp.complete(WORKER_TOKEN, jid,
                        {"result": {"result": "TASK_COMPLETED"}})
        self.assertEqual(str(c2.exception), "completion_before_acceptance")


if __name__ == "__main__":
    unittest.main(verbosity=2)
