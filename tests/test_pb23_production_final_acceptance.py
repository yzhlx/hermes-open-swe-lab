"""PB-23 — Production Final Acceptance Gate (human-in-the-loop completion).

Ports the validated Demo Runner acceptance logic into the ``hermes_worker``
control plane core and verifies the full FINAL_ACCEPTANCE -> FINAL_ACCEPTED ->
TASK_COMPLETED chain, fail-closed:

  * CI not green  -> no FINAL_ACCEPTANCE, no final_accept, no complete
  * CI green      -> ONE idempotent FINAL_ACCEPTANCE; state USER_ACTION_REQUIRED
  * Human Owner   -> independent HUMAN_OWNER_TOKEN; wrong/missing/empty REJECTED;
                     token NEVER in logs/events/errors; repeated accept idempotent
  * complete()    -> requires FINAL_ACCEPTED; emits TASK_COMPLETED exactly once;
                     cannot bypass the chain by directly mutating state
  * Event Store is the SOLE source of truth; workers cannot impersonate owner

P0 hardening (this file is the regression anchor):
  * Production completion is gated on FINAL_ACCEPTED UNCONDITIONALLY — a job that
    never entered (or never cleared) the final-acceptance pipeline is rejected,
    so the legacy completion bypass is removed in production.
  * Authentication runs BEFORE any TASK_COMPLETED event is written: a wrong or
    foreign worker token can never produce a completion event or a state change.

The Human-Owner token is injected explicitly via ``ControlPlane(human_owner_
token=...)`` — this file NEVER mutates ``hermes_worker.constants`` and reads no
module-level placeholder default (the weak default was removed from constants).

Run:  python -m pytest tests/test_pb23_production_final_acceptance.py -q
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from hermes_worker.control_plane import ControlPlane, ControlPlaneError  # noqa: E402
from hermes_worker.db import hash_token                                  # noqa: E402
import hermes_worker.constants as constants                             # noqa: E402

# Deterministic secrets for the test (production reads HERMES_HUMAN_OWNER_TOKEN).
HUMAN_OWNER_TOKEN = "pb23-owner-secret-zzz-999"
WORKER_TOKEN = "pb23-worker-token-abc-111"
FOREIGN_WORKER_TOKEN = "pb23-foreign-worker-token-def-222"


class PB23FinalAcceptanceTest(unittest.TestCase):
    """DEV-mode acceptance-chain tests. The Human-Owner token is injected
    explicitly through the constructor (no global constant mutation)."""

    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mktemp(suffix=".db")

    def setUp(self):
        # Inject the Human-Owner token explicitly (no module-level global pin).
        self.cp = ControlPlane(self.db, human_owner_token=HUMAN_OWNER_TOKEN)
        self.cp.conn.execute("DELETE FROM events")
        self.cp.conn.execute("DELETE FROM jobs")
        self.cp.conn.execute("DELETE FROM workers")
        self.cp.conn.commit()
        self.cp.register(WORKER_TOKEN)  # known worker
        self.cp.conn.commit()

    def tearDown(self):
        self.cp.conn.close()

    @classmethod
    def tearDownClass(cls):
        for ext in ("", "-wal", "-shm"):
            p = cls.db + ext
            if os.path.exists(p):
                for _ in range(10):
                    try:
                        os.remove(p)
                        break
                    except PermissionError:
                        import time as _t
                        _t.sleep(0.1)

    # -- helpers -------------------------------------------------------------
    def _new_job(self, ci_status=None):
        """Create + claim a job, optionally stamping CI status, owned by WORKER."""
        jid = self.cp.create_job({"command": "x"}, role="coding_agent")
        self.cp.claim(WORKER_TOKEN)  # sets worker_token_hash
        if ci_status is not None:
            self.cp.store_agent_result(jid, {"ci_status": ci_status})
        return jid

    def _event_types(self, jid):
        return [e["event_type"] for e in self.cp.get_events(jid)]

    # -- req 2: CI gate ------------------------------------------------------
    def test_ci_pending_blocks_acceptance_request(self):
        jid = self._new_job(ci_status="pending")
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.assertEqual(ctx.exception.args[0], "acceptance_before_ci")
        self.assertNotIn(constants.FINAL_ACCEPTANCE, self._event_types(jid))

    def test_ci_failure_blocks_acceptance_request(self):
        jid = self._new_job(ci_status="failure")
        with self.assertRaises(ControlPlaneError):
            self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.assertNotIn(constants.FINAL_ACCEPTANCE, self._event_types(jid))

    def test_ci_missing_blocks_acceptance_request(self):
        jid = self._new_job(ci_status=None)
        with self.assertRaises(ControlPlaneError):
            self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.assertNotIn(constants.FINAL_ACCEPTANCE, self._event_types(jid))

    # -- req 3: green CI -> ONE idempotent FINAL_ACCEPTANCE ------------------
    def test_ci_green_emits_single_final_acceptance(self):
        jid = self._new_job(ci_status="success")
        r = self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.assertEqual(r["state"], constants.USER_ACTION_REQUIRED)
        types = self._event_types(jid)
        self.assertEqual(types.count(constants.FINAL_ACCEPTANCE), 1)
        self.assertEqual(self.cp.get_job(jid)["state"],
                         constants.USER_ACTION_REQUIRED)

    def test_repeated_acceptance_request_not_duplicated(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)  # idempotent
        self.assertEqual(self._event_types(jid).count(constants.FINAL_ACCEPTANCE), 1)

    # -- req 4: Human Owner token -------------------------------------------
    def test_final_accept_before_final_acceptance_rejected(self):
        jid = self._new_job(ci_status="success")
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        self.assertEqual(ctx.exception.args[0], "acceptance_before_final_acceptance")

    def test_final_accept_wrong_token_rejected(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.final_accept("totally-wrong-token", jid)
        self.assertEqual(ctx.exception.args[0], "acceptance_token_mismatch")
        self.assertNotIn(constants.FINAL_ACCEPTED, self._event_types(jid))

    def test_final_accept_empty_token_rejected(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.final_accept("", jid)
        self.assertEqual(ctx.exception.args[0], "acceptance_token_missing")
        self.assertNotIn(constants.FINAL_ACCEPTED, self._event_types(jid))

    def test_correct_token_accepted(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        r = self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        self.assertEqual(r["state"], constants.FINAL_ACCEPTED)
        self.assertEqual(self._event_types(jid).count(constants.FINAL_ACCEPTED), 1)
        self.assertEqual(self.cp.get_job(jid)["state"], constants.FINAL_ACCEPTED)

    def test_repeated_accept_idempotent(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        r2 = self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)  # no-op
        self.assertTrue(r2.get("already_accepted"))
        self.assertEqual(self._event_types(jid).count(constants.FINAL_ACCEPTED), 1)

    # -- req 5: complete() gate ---------------------------------------------
    def test_complete_before_acceptance_rejected(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(ctx.exception.args[0], "completion_before_acceptance")
        self.assertNotIn(constants.TASK_COMPLETED, self._event_types(jid))
        self.assertNotEqual(self.cp.get_job(jid)["state"], "completed")

    def test_complete_after_acceptance_emits_task_completed(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        r = self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(r["state"], "completed")
        self.assertEqual(self._event_types(jid).count(constants.TASK_COMPLETED), 1)
        self.assertEqual(self.cp.get_job(jid)["state"], "completed")

    def test_repeated_complete_no_second_task_completed(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        r2 = self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 99})
        self.assertTrue(r2.get("already_finished"))
        self.assertEqual(self._event_types(jid).count(constants.TASK_COMPLETED), 1)

    # -- req 4+5: token never leaks; state-bypass blocked -------------------
    def test_token_not_in_logs_or_events(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        # Scan every event payload for the secret.
        blob = "".join(
            (e["payload"] or "") for e in self.cp.get_events(jid))
        self.assertNotIn(HUMAN_OWNER_TOKEN, blob)
        self.assertNotIn(WORKER_TOKEN, blob)
        # Accepted-by role is recorded, never the token.
        accepted = [e for e in self.cp.get_events(jid)
                    if e["event_type"] == constants.FINAL_ACCEPTED][0]
        self.assertEqual(accepted["payload"], '{"accepted_by": "human_owner"}')

    def test_non_human_owner_role_cannot_accept(self):
        # A worker token must NOT satisfy the Human Owner gate (req 6).
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.final_accept(WORKER_TOKEN, jid)  # worker token, not owner
        self.assertEqual(ctx.exception.args[0], "acceptance_token_mismatch")
        self.assertNotIn(constants.FINAL_ACCEPTED, self._event_types(jid))

    def test_set_state_bypass_blocked(self):
        jid = self._new_job(ci_status="success")
        for forbidden in (constants.USER_ACTION_REQUIRED,
                          constants.FINAL_ACCEPTED,
                          constants.TASK_COMPLETED):
            with self.assertRaises(ControlPlaneError) as ctx:
                self.cp.set_state(jid, forbidden)
            self.assertEqual(ctx.exception.args[0], "state_transition_not_allowed")
        # Legacy non-gated states still settable (no regression).
        self.cp.set_state(jid, "agent_done")
        self.assertEqual(self.cp.get_job(jid)["state"], "agent_done")

    # -- regression: DEV legacy (non-pipeline) completion still works -------
    def test_dev_legacy_completion_without_pipeline(self):
        # In DEV/test, a job that never entered the final-acceptance pipeline
        # may still complete via the legacy path (preserves non-gated flows).
        jid = self._new_job(ci_status=None)  # no FINAL_ACCEPTANCE -> legacy path
        r = self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(r["state"], "completed")
        # TASK_COMPLETED is still emitted once (Event Store remains authoritative).
        self.assertEqual(self._event_types(jid).count(constants.TASK_COMPLETED), 1)


class PB23ProductionCompletionTest(unittest.TestCase):
    """Production-mode completion must be gated on FINAL_ACCEPTED
    unconditionally, and authentication must run BEFORE any TASK_COMPLETED
    event is written (PB-23 P0)."""

    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mktemp(suffix=".db")

    def setUp(self):
        self.cp = ControlPlane(
            self.db,
            allowed_token_hashes={hash_token(WORKER_TOKEN),
                                  hash_token(FOREIGN_WORKER_TOKEN)},
            human_owner_token=HUMAN_OWNER_TOKEN,
            mode="production")
        self.cp.conn.execute("DELETE FROM events")
        self.cp.conn.execute("DELETE FROM jobs")
        self.cp.conn.execute("DELETE FROM workers")
        self.cp.conn.commit()
        self.cp.register(WORKER_TOKEN)
        self.cp.register(FOREIGN_WORKER_TOKEN)
        self.cp.conn.commit()

    def tearDown(self):
        self.cp.conn.close()

    @classmethod
    def tearDownClass(cls):
        for ext in ("", "-wal", "-shm"):
            p = cls.db + ext
            if os.path.exists(p):
                for _ in range(10):
                    try:
                        os.remove(p)
                        break
                    except PermissionError:
                        import time as _t
                        _t.sleep(0.1)

    # -- helpers -------------------------------------------------------------
    def _new_job(self, ci_status=None):
        jid = self.cp.create_job({"command": "x"}, role="coding_agent")
        self.cp.claim(WORKER_TOKEN)  # owned by WORKER_TOKEN
        if ci_status is not None:
            self.cp.store_agent_result(jid, {"ci_status": ci_status})
        return jid

    def _event_types(self, jid):
        return [e["event_type"] for e in self.cp.get_events(jid)]

    def test_production_no_final_acceptance_rejected(self):
        # Job never entered the pipeline -> production refuses completion.
        jid = self._new_job(ci_status="success")
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(ctx.exception.args[0], "completion_before_acceptance")
        self.assertNotIn(constants.TASK_COMPLETED, self._event_types(jid))
        self.assertNotEqual(self.cp.get_job(jid)["state"], "completed")

    def test_production_final_acceptance_only_rejected(self):
        # FINAL_ACCEPTANCE present but no FINAL_ACCEPTED -> production refuses.
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(ctx.exception.args[0], "completion_before_acceptance")
        self.assertNotIn(constants.TASK_COMPLETED, self._event_types(jid))

    def test_production_final_accepted_then_complete(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        r = self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(r["state"], "completed")
        self.assertEqual(self._event_types(jid).count(constants.TASK_COMPLETED), 1)
        self.assertEqual(self.cp.get_job(jid)["state"], "completed")

    def test_production_task_completed_exactly_once(self):
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 0})
        r2 = self.cp.complete(WORKER_TOKEN, jid, {"exit_code": 99})
        self.assertTrue(r2.get("already_finished"))
        self.assertEqual(self._event_types(jid).count(constants.TASK_COMPLETED), 1)

    def test_production_wrong_token_no_event(self):
        # Auth runs BEFORE the event write: an unknown token must not emit
        # TASK_COMPLETED or change state.
        jid = self._new_job(ci_status="success")
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.complete("definitely-not-a-known-token", jid, {"exit_code": 0})
        self.assertEqual(ctx.exception.args[0], "unknown_worker_token")
        self.assertNotIn(constants.TASK_COMPLETED, self._event_types(jid))
        self.assertNotEqual(self.cp.get_job(jid)["state"], "completed")

    def test_production_foreign_worker_token_no_event(self):
        # A registered but non-owning worker token must not emit/complete.
        jid = self._new_job(ci_status="success")  # owned by WORKER_TOKEN
        self.cp.request_final_acceptance(WORKER_TOKEN, jid)
        self.cp.final_accept(HUMAN_OWNER_TOKEN, jid)
        with self.assertRaises(ControlPlaneError) as ctx:
            self.cp.complete(FOREIGN_WORKER_TOKEN, jid, {"exit_code": 0})
        self.assertEqual(ctx.exception.args[0], "job_not_owned_by_worker")
        self.assertNotIn(constants.TASK_COMPLETED, self._event_types(jid))
        self.assertNotEqual(self.cp.get_job(jid)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
