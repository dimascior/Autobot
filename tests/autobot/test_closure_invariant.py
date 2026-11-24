"""Adversarial tests for the organizational closure invariant.

Every writer that marks a node COMPLETED, a run COMPLETED, or an
objective COMPLETE must call the closure service. These tests pin
each mutation boundary directly, including bypass paths identified
in the architecture review.

The authority/organization separation is preserved throughout:
a NOT_EXAMINED review remains VERIFIED_ACCEPTED in authority while
leaving organizational review coverage unsatisfied.
"""

import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot.models import instant
from intelligence import PlanStore
from intelligence.closure import (
    can_complete_node,
    can_complete_run,
    can_complete_objective,
    _NODE_COMPLETION_RULES,
)
from intelligence.orchestrator import (
    OrchestrationApplicator,
    DeterministicOrchestratorPolicy,
)
from intelligence.coordinator import (
    DeterministicCoordinatorPolicy,
)


class ClosureNodeTests(unittest.TestCase):
    """can_complete_node: the node mutation boundary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)
        self.plan = PlanStore(self.dir / "intelligence.db")

    def tearDown(self):
        if hasattr(self, "_ro_conn"):
            self._ro_conn.close()
        self.plan.close()
        self.rt.db.close()
        self.tmp.cleanup()

    def _ro(self):
        from intelligence.planstore import readonly_view
        if not hasattr(self, "_ro_conn"):
            self._ro_conn = readonly_view(str(self.dir / "autobot.db"))
        return self._ro_conn

    def _make_node(self, run_id, key, kind, status="PENDING"):
        from intelligence.proposals_util import canonical_json_digest
        node_id = "nd_" + canonical_json_digest(
            {"run": run_id, "key": key})[:32]
        now = instant(self.clock.now())
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_node(
                cur, node_id, run_id, key, now,
                realization_kind=kind,
                work_profile="RESEARCH")
        if status != "PENDING":
            with self.plan.transaction() as cur:
                self.plan.update_node_status(cur, node_id, status)
        return node_id

    def _make_run(self, objective_id):
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        self.plan.add_objective(objective_id, "test", "operator", now)
        cd_id = "cd_" + canonical_json_digest(
            {"obj": objective_id, "t": "decision"})[:32]
        with self.plan.transaction() as cur:
            self.plan.record_coordinator_decision(
                cur, cd_id, objective_id, "test-policy/v1",
                "digest_test", "DIRECT", {}, None, now)
        run_id = "run_" + canonical_json_digest(
            {"obj": objective_id})[:32]
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_run(
                cur, run_id, objective_id, cd_id, "DIRECT",
                "test", now)
        return run_id

    # --- Case 1: TASK_CONTRACT EXECUTING → REFUSED ---
    def test_task_contract_executing_refused(self):
        run_id = self._make_run("obj-cn-1")
        node_id = self._make_node(run_id, "work", "TASK_CONTRACT",
                                  status="EXECUTING")
        eligible, reason = can_complete_node(self.plan, self._ro(), node_id)
        self.assertFalse(eligible)
        self.assertIn("no authority binding", reason)

    # --- Case 2: TASK_CONTRACT sequence COMPLETED → APPLIED ---
    def test_task_contract_sequence_completed_applied(self):
        run_id = self._make_run("obj-cn-2")
        node_id = self._make_node(run_id, "work", "TASK_CONTRACT",
                                  status="EXECUTING")
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        binding_id = "ab_" + canonical_json_digest(
            {"node": node_id})[:32]
        seq_id = "sq_test_completed"
        # Create a completed sequence through the sequencer's connection.
        # sequences.record_hash references records, so insert a record first.
        db = self.rt.db.conn
        db.execute(
            "INSERT OR IGNORE INTO records (record_hash, task_contract_id,"
            " requirement_digest, contract_digest, content_address,"
            " eligibility, frozen_utc)"
            " VALUES ('rh_test', 'tc_test', 'rd_test', 'cd_test',"
            " 'ca_test', 'ELIGIBLE', ?)", (now,))
        db.execute(
            "INSERT OR IGNORE INTO sequences (sequence_id, record_hash,"
            " requirement_digest, contract_digest, state, version, fence,"
            " created_utc, updated_utc)"
            " VALUES (?, 'rh_test', 'rd_test', 'cd_test', 'COMPLETED',"
            " 1, 0, ?, ?)", (seq_id, now, now))
        db.commit()
        with self.plan.transaction() as cur:
            self.plan.create_authority_binding(
                cur, binding_id, node_id, now,
                sequence_id=seq_id)
        # Use the write connection so the test-inserted sequence is visible
        eligible, reason = can_complete_node(
            self.plan, self.rt.db.conn, node_id)
        self.assertTrue(eligible, "expected eligible, got: %s" % reason)
        self.assertIn("COMPLETED", reason)

    # --- Case 3: ENGINEER_REASONING unadopted → REFUSED ---
    def test_engineer_reasoning_unadopted_refused(self):
        run_id = self._make_run("obj-cn-3")
        node_id = self._make_node(run_id, "eng", "ENGINEER_REASONING",
                                  status="PENDING")
        now = instant(self.clock.now())
        with self.plan.transaction() as cur:
            self.plan.create_engineering_binding(
                cur, "er_test_unadopted", node_id, run_id,
                "test question", now)
        eligible, reason = can_complete_node(self.plan, self._ro(), node_id)
        self.assertFalse(eligible)
        self.assertIn("not adopted", reason)

    # --- Case 4: ENGINEER_REASONING adopted → APPLIED ---
    def test_engineer_reasoning_adopted_applied(self):
        run_id = self._make_run("obj-cn-4")
        node_id = self._make_node(run_id, "eng", "ENGINEER_REASONING",
                                  status="PENDING")
        now = instant(self.clock.now())
        req_id = "er_test_adopted"
        with self.plan.transaction() as cur:
            self.plan.create_engineering_binding(
                cur, req_id, node_id, run_id, "test question", now)
        with self.plan.transaction() as cur:
            self.plan.adopt_engineering_decision(
                cur, req_id, "ed_test", "sha_test", now)
        eligible, reason = can_complete_node(self.plan, self._ro(), node_id)
        self.assertTrue(eligible)
        self.assertIn("adopted", reason)

    # --- Case 5: no authority reader → REFUSED ---
    def test_task_contract_no_ro_refused(self):
        run_id = self._make_run("obj-cn-5")
        node_id = self._make_node(run_id, "work", "TASK_CONTRACT",
                                  status="EXECUTING")
        now = instant(self.clock.now())
        binding_id = "ab_" + "0" * 32
        with self.plan.transaction() as cur:
            self.plan.create_authority_binding(
                cur, binding_id, node_id, now,
                sequence_id="sq_test_no_ro")
        eligible, reason = can_complete_node(self.plan, None, node_id)
        self.assertFalse(eligible)
        self.assertIn("no authority reader", reason)

    # --- Case 6: missing node → REFUSED ---
    def test_missing_node_refused(self):
        eligible, reason = can_complete_node(
            self.plan, self._ro(), "nd_does_not_exist")
        self.assertFalse(eligible)
        self.assertIn("not found", reason)

    def test_missing_node_id_refused(self):
        eligible, reason = can_complete_node(self.plan, self._ro(), None)
        self.assertFalse(eligible)
        self.assertIn("missing", reason)

    # --- Completion rules registry ---
    def test_completion_rules_cover_known_kinds(self):
        for kind in ("TASK_CONTRACT", "ENGINEER_REASONING", "CONTROL"):
            self.assertIn(kind, _NODE_COMPLETION_RULES)


class ClosureRunTests(unittest.TestCase):
    """can_complete_run: the run mutation boundary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)
        self.plan = PlanStore(self.dir / "intelligence.db")

    def tearDown(self):
        if hasattr(self, "_ro_conn"):
            self._ro_conn.close()
        self.plan.close()
        self.rt.db.close()
        self.tmp.cleanup()

    def _ro(self):
        from intelligence.planstore import readonly_view
        if not hasattr(self, "_ro_conn"):
            self._ro_conn = readonly_view(str(self.dir / "autobot.db"))
        return self._ro_conn

    def _make_run(self, objective_id, mode="DIRECT"):
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        self.plan.add_objective(objective_id, "test", "operator", now)
        cd_id = "cd_" + canonical_json_digest(
            {"obj": objective_id, "t": "decision"})[:32]
        with self.plan.transaction() as cur:
            self.plan.record_coordinator_decision(
                cur, cd_id, objective_id, "test-policy/v1",
                "digest_test", mode, {}, None, now)
        run_id = "run_" + canonical_json_digest(
            {"obj": objective_id})[:32]
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_run(
                cur, run_id, objective_id, cd_id, mode,
                "test", now)
        return run_id

    # --- Case 7: replayed COMPLETE_RUN without objective_id ---
    def test_complete_run_derives_objective_from_run(self):
        """The objective_id comes from the durable run, not the decision."""
        run_id = self._make_run("obj-cr-7")
        eligible, reason, oid = can_complete_run(
            self.plan, self._ro(), run_id)
        self.assertEqual(oid, "obj-cr-7")

    # --- Case 8: all nodes COMPLETED + obligations met → APPLIED ---
    def test_all_completed_eligible(self):
        run_id = self._make_run("obj-cr-8")
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        node_id = "nd_" + canonical_json_digest(
            {"run": run_id, "key": "done"})[:32]
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_node(
                cur, node_id, run_id, "done", now,
                realization_kind="CONTROL")
            self.plan.update_node_status(cur, node_id, "COMPLETED")
        eligible, reason, _ = can_complete_run(
            self.plan, self._ro(), run_id)
        self.assertTrue(eligible)

    def test_executing_node_blocks_run(self):
        run_id = self._make_run("obj-cr-8b")
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        node_id = "nd_" + canonical_json_digest(
            {"run": run_id, "key": "busy"})[:32]
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_node(
                cur, node_id, run_id, "busy", now,
                realization_kind="TASK_CONTRACT")
        eligible, reason, _ = can_complete_run(
            self.plan, self._ro(), run_id)
        self.assertFalse(eligible)
        self.assertIn(node_id, reason)


class ClosureApplicatorTests(unittest.TestCase):
    """The OrchestrationApplicator enforces the closure service."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)
        self.plan = PlanStore(self.dir / "intelligence.db")

    def tearDown(self):
        if hasattr(self, "_ro_conn"):
            self._ro_conn.close()
        self.plan.close()
        self.rt.db.close()
        self.tmp.cleanup()

    def _ro(self):
        from intelligence.planstore import readonly_view
        if not hasattr(self, "_ro_conn"):
            self._ro_conn = readonly_view(str(self.dir / "autobot.db"))
        return self._ro_conn

    def _make_run(self, objective_id):
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        self.plan.add_objective(objective_id, "test", "operator", now)
        cd_id = "cd_" + canonical_json_digest(
            {"obj": objective_id, "t": "decision"})[:32]
        with self.plan.transaction() as cur:
            self.plan.record_coordinator_decision(
                cur, cd_id, objective_id, "test-policy/v1",
                "digest_test", "DIRECT", {}, None, now)
        run_id = "run_" + canonical_json_digest(
            {"obj": objective_id})[:32]
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_run(
                cur, run_id, objective_id, cd_id, "DIRECT",
                "test", now)
        return run_id

    def test_complete_node_refused_via_applicator(self):
        """COMPLETE_NODE through the applicator is refused for
        an EXECUTING node without a completed sequence."""
        run_id = self._make_run("obj-app-1")
        from intelligence.proposals_util import canonical_json_digest
        now = instant(self.clock.now())
        node_id = "nd_" + canonical_json_digest(
            {"run": run_id, "key": "task"})[:32]
        with self.plan.transaction() as cur:
            self.plan.create_orchestration_node(
                cur, node_id, run_id, "task", now,
                realization_kind="TASK_CONTRACT")
            self.plan.update_node_status(cur, node_id, "EXECUTING")

        applicator = OrchestrationApplicator(
            self.plan, orchestration_id=run_id,
            ro=self._ro())
        results = applicator.apply([
            {"class": "COMPLETE_NODE", "node_id": node_id},
        ], now)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "REFUSED")


if __name__ == "__main__":
    unittest.main()
