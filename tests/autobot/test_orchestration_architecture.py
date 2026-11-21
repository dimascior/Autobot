"""Deterministic end-to-end test of the independent orchestration architecture.

Exercises the complete chain:
    Objective
        → CoordinatorDecision
        → OrchestrationRun
        → OrchestrationNodes (A, B with edge A→B)
        → authority binding for A
        → TaskContract / sequence A
        → A accepted
        → orchestration edge release
        → B authority binding
        → B executes
        → run completion

Also proves:
    - Coordinator elevation modes (DIRECT, ENGINEER_ELEVATED, ORCHESTRATOR_ELEVATED)
    - Project-independent orchestration (zero project bindings)
    - Optional project binding after run creation
    - Node-oriented decision classes
    - v1→v2 decision normalization
    - Engineering bindings
    - Node outputs
    - Restart convergence (idempotent admission)
"""

import json
import os
import tempfile
import unittest

from intelligence.planstore import PlanStore, PLAN_SCHEMA_VERSION
from intelligence.coordinator import (
    CoordinatorService,
    DeterministicCoordinatorPolicy,
    PassthroughCoordinatorPolicy,
    ProductionCoordinatorPolicy,
    ELEVATION_MODES,
    COMPLEXITY_FEATURES,
)
from intelligence.orchestrator import (
    OrchestratorService,
    OrchestratorPlanningView,
    OrchestrationApplicator,
    DeterministicOrchestratorPolicy,
    PassthroughOrchestratorPolicy,
    validate_orchestration_decision,
    validate_orchestration_decisions,
    normalize_decision,
    OrchestrationDecisionError,
    ORCHESTRATOR_DECISION_CLASSES,
    REALIZATION_KINDS,
    _V1_TO_V2,
)
from intelligence.workstream_bridge import (
    node_to_proposal,
    workstream_to_proposal,
)
from intelligence.orchestrator_policy import (
    OpusOrchestratorPolicy,
    _render_orchestrator_view,
    _parse_decisions,
    OPUS_ORCHESTRATOR_POLICY_ID,
)


class FakeClock:
    def __init__(self, start=None):
        from datetime import datetime
        self._now = start or datetime(2026, 11, 21, 12, 0, 0)

    def now(self):
        return self._now

    def advance(self, seconds=1):
        from datetime import timedelta
        self._now += timedelta(seconds=seconds)
        return self._now


def _make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return PlanStore(path), path


class TestSchemaV6(unittest.TestCase):

    def test_schema_version_is_6(self):
        self.assertEqual(PLAN_SCHEMA_VERSION, 6)

    def test_migration_creates_new_tables(self):
        store, path = _make_store()
        try:
            tables = [r[0] for r in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " ORDER BY name").fetchall()]
            for expected in (
                "coordinator_decisions",
                "orchestration_runs",
                "orchestration_nodes",
                "orchestration_edges",
                "orchestration_authority_bindings",
                "orchestration_engineering_bindings",
                "orchestration_project_bindings",
                "orchestration_node_outputs",
            ):
                self.assertIn(expected, tables, expected)
        finally:
            store.close()
            os.unlink(path)

    def test_proposals_origin_columns_exist(self):
        store, path = _make_store()
        try:
            cols = [r[1] for r in store.conn.execute(
                "PRAGMA table_info(proposals)").fetchall()]
            self.assertIn("origin_kind", cols)
            self.assertIn("origin_id", cols)
        finally:
            store.close()
            os.unlink(path)

    def test_proposals_cycle_id_uses_sentinel(self):
        """Orchestration-origin proposals use a sentinel cycle_id."""
        store, path = _make_store()
        try:
            sentinel = store.orchestration_sentinel_cycle()
            self.assertIsNotNone(sentinel)
            self.assertGreater(sentinel, 0)
        finally:
            store.close()
            os.unlink(path)

    def test_orchestrator_project_via_implicit(self):
        """Orchestrator projects use opened_by=IMPLICIT in the legacy
        table and record provenance through project_bindings."""
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-test", "test", "operator", now)
            with store.transaction() as cur:
                project, created = store.open_project(
                    cur, "obj-test", "Test Project", "solution",
                    "IMPLICIT", "orc-decision-1", now)
            self.assertTrue(created)
            self.assertEqual(project["opened_by"], "IMPLICIT")
        finally:
            store.close()
            os.unlink(path)

    def test_sentinel_cycle_exists(self):
        store, path = _make_store()
        try:
            sentinel = store.orchestration_sentinel_cycle()
            self.assertIsNotNone(sentinel)
            row = store.conn.execute(
                "SELECT * FROM cycles WHERE cycle_id = ?",
                (sentinel,)).fetchone()
            self.assertEqual(row["note"], "orchestration_sentinel")
            self.assertEqual(row["status"], "CLOSED")
        finally:
            store.close()
            os.unlink(path)

    def test_project_assignments_has_node_column(self):
        store, path = _make_store()
        try:
            cols = [r[1] for r in store.conn.execute(
                "PRAGMA table_info(project_assignments)").fetchall()]
            self.assertIn("orchestration_node_id", cols)
        finally:
            store.close()
            os.unlink(path)


class TestCoordinator(unittest.TestCase):

    def test_passthrough_policy(self):
        policy = PassthroughCoordinatorPolicy()
        result = policy.evaluate({"text": "test"}, {})
        self.assertEqual(result["elevation_mode"], "DIRECT")

    def test_deterministic_policy(self):
        policy = DeterministicCoordinatorPolicy(
            mode="ORCHESTRATOR_ELEVATED",
            features={"multi_domain": True})
        result = policy.evaluate({"text": "complex work"}, {})
        self.assertEqual(result["elevation_mode"],
                         "ORCHESTRATOR_ELEVATED")
        self.assertTrue(result["features"]["multi_domain"])

    def test_admit_objective_direct(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-1", "research task", "operator", now)
            svc = CoordinatorService(
                store, PassthroughCoordinatorPolicy(), clock)
            result = svc.admit_objective("obj-1", now)
            self.assertTrue(result["new"])
            self.assertEqual(result["decision"]["elevation_mode"], "DIRECT")
            self.assertIsNotNone(result["run"])
            self.assertEqual(result["run"]["mode"], "DIRECT")
            self.assertEqual(result["run"]["status"], "ACTIVE")
        finally:
            store.close()
            os.unlink(path)

    def test_admit_objective_elevated(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-2", "complex multi-domain",
                                "operator", now)
            svc = CoordinatorService(
                store,
                DeterministicCoordinatorPolicy(
                    mode="ORCHESTRATOR_ELEVATED"),
                clock)
            result = svc.admit_objective("obj-2", now)
            self.assertTrue(result["new"])
            self.assertEqual(result["run"]["mode"],
                             "ORCHESTRATOR_ELEVATED")
        finally:
            store.close()
            os.unlink(path)

    def test_admit_idempotent(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-3", "test", "operator", now)
            svc = CoordinatorService(
                store, PassthroughCoordinatorPolicy(), clock)
            r1 = svc.admit_objective("obj-3", now)
            self.assertTrue(r1["new"])
            r2 = svc.admit_objective("obj-3", now)
            self.assertFalse(r2["new"])
            self.assertEqual(r1["decision"]["decision_id"],
                             r2["decision"]["decision_id"])
        finally:
            store.close()
            os.unlink(path)

    def test_create_initial_node_direct(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-4", "simple task", "operator", now)
            svc = CoordinatorService(
                store, PassthroughCoordinatorPolicy(), clock)
            result = svc.admit_objective("obj-4", now)
            objective = store.objective("obj-4")
            node = svc.create_initial_node(result["run"], objective, now)
            self.assertIsNotNone(node)
            self.assertEqual(node["realization_kind"], "TASK_CONTRACT")
            self.assertEqual(node["node_key"], "direct-work")
        finally:
            store.close()
            os.unlink(path)

    def test_create_initial_node_engineer(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-5", "design problem", "operator", now)
            svc = CoordinatorService(
                store,
                DeterministicCoordinatorPolicy(mode="ENGINEER_ELEVATED"),
                clock)
            result = svc.admit_objective("obj-5", now)
            objective = store.objective("obj-5")
            node = svc.create_initial_node(result["run"], objective, now)
            self.assertIsNotNone(node)
            self.assertEqual(node["realization_kind"], "ENGINEER_REASONING")
            self.assertEqual(node["organizational_role"], "ENGINEER")
        finally:
            store.close()
            os.unlink(path)

    def test_orchestrator_elevated_no_initial_node(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-6", "complex", "operator", now)
            svc = CoordinatorService(
                store,
                DeterministicCoordinatorPolicy(
                    mode="ORCHESTRATOR_ELEVATED"),
                clock)
            result = svc.admit_objective("obj-6", now)
            objective = store.objective("obj-6")
            node = svc.create_initial_node(result["run"], objective, now)
            self.assertIsNone(node)
        finally:
            store.close()
            os.unlink(path)


class TestDecisionNormalization(unittest.TestCase):

    def test_v1_to_v2_mapping(self):
        d = normalize_decision({
            "class": "CREATE_WORKSTREAM",
            "project_id": "pj_x",
            "workstream_key": "research-a",
        })
        self.assertEqual(d["class"], "CREATE_NODE")
        self.assertEqual(d["node_key"], "research-a")
        self.assertEqual(d["original_class"], "CREATE_WORKSTREAM")

    def test_v2_passthrough(self):
        d = normalize_decision({
            "class": "CREATE_NODE",
            "node_key": "research-a",
        })
        self.assertEqual(d["class"], "CREATE_NODE")
        self.assertNotIn("original_class", d)

    def test_add_dependency_to_add_edge(self):
        d = normalize_decision({
            "class": "ADD_DEPENDENCY",
            "source_workstream": "ws-a",
            "target_workstream": "ws-b",
            "dependency_kind": "ACCEPTED_ARTIFACT",
        })
        self.assertEqual(d["class"], "ADD_EDGE")
        self.assertEqual(d["source_node"], "ws-a")
        self.assertEqual(d["target_node"], "ws-b")

    def test_all_v1_classes_mapped(self):
        for v1_cls in _V1_TO_V2:
            self.assertIn(v1_cls, ORCHESTRATOR_DECISION_CLASSES)
            self.assertIn(_V1_TO_V2[v1_cls], ORCHESTRATOR_DECISION_CLASSES)


class TestNodeDecisionValidation(unittest.TestCase):

    def test_create_node_valid(self):
        d = validate_orchestration_decision({
            "class": "CREATE_NODE",
            "node_key": "research-a",
        })
        self.assertEqual(d["class"], "CREATE_NODE")

    def test_create_node_missing_key(self):
        with self.assertRaises(OrchestrationDecisionError):
            validate_orchestration_decision({"class": "CREATE_NODE"})

    def test_add_edge_valid(self):
        d = validate_orchestration_decision({
            "class": "ADD_EDGE",
            "source_node": "nd-a",
            "target_node": "nd-b",
            "dependency_kind": "ACCEPTED_ARTIFACT",
        })
        self.assertEqual(d["class"], "ADD_EDGE")

    def test_set_node_realization_kind_valid(self):
        d = validate_orchestration_decision({
            "class": "SET_NODE_REALIZATION_KIND",
            "realization_kind": "TASK_CONTRACT",
        })
        self.assertEqual(d["class"], "SET_NODE_REALIZATION_KIND")

    def test_set_node_realization_kind_invalid(self):
        with self.assertRaises(OrchestrationDecisionError):
            validate_orchestration_decision({
                "class": "SET_NODE_REALIZATION_KIND",
                "realization_kind": "INVALID",
            })

    def test_realization_kinds(self):
        for rk in ("TASK_CONTRACT", "ENGINEER_REASONING", "CONTROL"):
            self.assertIn(rk, REALIZATION_KINDS)


class TestOrchestrationRunNodeLifecycle(unittest.TestCase):

    def test_full_run_lifecycle(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-lc", "lifecycle test", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-lc", "obj-lc", "test/v1", "digest-lc",
                    "DIRECT", {}, None, now)
                run = store.create_orchestration_run(
                    cur, "run-lc", "obj-lc", "cd-lc", "DIRECT",
                    "coordinator", now)

            self.assertEqual(run["status"], "ACTIVE")

            with store.transaction() as cur:
                n1 = store.create_orchestration_node(
                    cur, "nd-a", "run-lc", "research", now,
                    work_profile="RESEARCH",
                    organizational_role="SPECIALIST",
                    realization_kind="TASK_CONTRACT",
                    capability="research",
                    execution_profile="luna-research/v1")
                n2 = store.create_orchestration_node(
                    cur, "nd-b", "run-lc", "implementation", now,
                    work_profile="ENGINEERING",
                    organizational_role="SPECIALIST",
                    realization_kind="TASK_CONTRACT",
                    capability="documentation",
                    execution_profile="luna-local/v1")

            self.assertEqual(n1["status"], "PENDING")
            self.assertEqual(n2["status"], "PENDING")

            with store.transaction() as cur:
                store.create_orchestration_edge(
                    cur, "oe-ab", "run-lc", "nd-a", "nd-b",
                    "ACCEPTED_ARTIFACT")

            ready = store.ready_nodes("run-lc")
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0]["node_id"], "nd-a")

            with store.transaction() as cur:
                store.create_authority_binding(
                    cur, "ab-a", "nd-a", now,
                    proposal_id="prop-a",
                    sequence_id="seq-a")
                store.update_node_status(cur, "nd-a", "EXECUTING")

            binding = store.authority_binding_for_node("nd-a")
            self.assertEqual(binding["sequence_id"], "seq-a")

            with store.transaction() as cur:
                store.release_orchestration_edge(
                    cur, "oe-ab", now,
                    acceptance_receipt_sha256="sha256:acc-a")
                store.update_node_status(cur, "nd-a", "COMPLETED", now)

            edges = store.edges_for_node("nd-b", direction="inbound")
            self.assertEqual(edges[0]["status"], "RELEASED")

            ready_after = store.ready_nodes("run-lc")
            self.assertEqual(len(ready_after), 1)
            self.assertEqual(ready_after[0]["node_id"], "nd-b")

            with store.transaction() as cur:
                store.update_node_status(cur, "nd-b", "COMPLETED", now)
                store.complete_orchestration_run(cur, "run-lc", now)

            run_final = store.orchestration_run("run-lc")
            self.assertEqual(run_final["status"], "COMPLETED")

        finally:
            store.close()
            os.unlink(path)

    def test_project_independent_run(self):
        """An OrchestrationRun with zero project bindings."""
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-pi", "project-independent", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-pi", "obj-pi", "test/v1", "digest-pi",
                    "DIRECT", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-pi", "obj-pi", "cd-pi", "DIRECT",
                    "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-pi-1", "run-pi", "research", now,
                    work_profile="RESEARCH",
                    realization_kind="TASK_CONTRACT")
            bindings = store.project_bindings_for_run("run-pi")
            self.assertEqual(len(bindings), 0)
        finally:
            store.close()
            os.unlink(path)

    def test_optional_project_binding_after_creation(self):
        """Project binding added after run and nodes exist."""
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-pb", "binding test", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-pb", "obj-pb", "test/v1", "digest-pb",
                    "ORCHESTRATOR_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-pb", "obj-pb", "cd-pb",
                    "ORCHESTRATOR_ELEVATED", "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-impl", "run-pb", "implementation", now,
                    work_profile="ENGINEERING",
                    realization_kind="TASK_CONTRACT")
                project, _ = store.open_project(
                    cur, "obj-pb", "Implementation Project", "sol",
                    "IMPLICIT", "orc-proj-dec", now)

            with store.transaction() as cur:
                store.create_project_binding(
                    cur, "nd-impl", project["project_id"], now,
                    binding_reason="engineering realization")

            bindings = store.project_bindings_for_node("nd-impl")
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0]["project_id"],
                             project["project_id"])
        finally:
            store.close()
            os.unlink(path)


class TestEngineeringBindings(unittest.TestCase):

    def test_create_and_adopt(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-eb", "eng binding", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-eb", "obj-eb", "test/v1", "digest-eb",
                    "ENGINEER_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-eb", "obj-eb", "cd-eb",
                    "ENGINEER_ELEVATED", "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-eng", "run-eb", "architecture", now,
                    work_profile="ENGINEERING",
                    organizational_role="ENGINEER",
                    realization_kind="ENGINEER_REASONING",
                    execution_profile="opus-engineer/v1")
                store.create_engineering_binding(
                    cur, "er-1", "nd-eng", "run-eb",
                    "What architecture is correct?", now)

            binding = store.engineering_binding_for_node("nd-eng")
            self.assertEqual(binding["question"],
                             "What architecture is correct?")
            self.assertIsNone(binding["adopted_utc"])

            with store.transaction() as cur:
                store.adopt_engineering_decision(
                    cur, "er-1", "ed-123", "sha256:decision-bytes",
                    now, route_metadata="opus-engineer/v1")

            binding = store.engineering_binding_for_node("nd-eng")
            self.assertEqual(binding["engineering_decision_id"], "ed-123")
            self.assertIsNotNone(binding["adopted_utc"])
        finally:
            store.close()
            os.unlink(path)


class TestNodeOutputs(unittest.TestCase):

    def test_record_and_query(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-no", "node output", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-no", "obj-no", "test/v1", "digest-no",
                    "DIRECT", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-no", "obj-no", "cd-no", "DIRECT",
                    "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-out", "run-no", "work", now,
                    realization_kind="TASK_CONTRACT")
                store.record_node_output(
                    cur, "out-1", "nd-out", "ACCEPTED_ARTIFACT", now,
                    content_sha256="sha256:artifact-bytes",
                    sequence_id="seq-out",
                    attempt_id="att-out")

            outputs = store.outputs_for_node("nd-out")
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0]["output_kind"],
                             "ACCEPTED_ARTIFACT")
            self.assertEqual(outputs[0]["content_sha256"],
                             "sha256:artifact-bytes")
        finally:
            store.close()
            os.unlink(path)


class TestNodeToProposal(unittest.TestCase):

    def test_research_node(self):
        node = {
            "node_id": "nd-r1",
            "node_key": "market-research",
            "work_profile": "RESEARCH",
            "organizational_role": "SPECIALIST",
            "realization_kind": "TASK_CONTRACT",
            "execution_profile": "luna-research/v1",
            "resource_scope": [{"kind": "WEB_SOURCE",
                                "activity": "READ"}],
        }
        proposal = node_to_proposal(node, "Research market trends")
        self.assertEqual(proposal["class"], "INVESTIGATE")
        self.assertEqual(proposal["origin_kind"], "ORCHESTRATION_NODE")
        self.assertEqual(proposal["origin_id"], "nd-r1")
        req = proposal["requirement"]
        self.assertNotIn("work_profile", req,
                         "organizational dimension stays on the node")
        self.assertNotIn("execution_profile", req,
                         "organizational dimension stays on the node")

    def test_engineering_node(self):
        node = {
            "node_id": "nd-e1",
            "node_key": "implement-feature",
            "work_profile": "ENGINEERING",
            "organizational_role": "SPECIALIST",
            "realization_kind": "TASK_CONTRACT",
            "execution_profile": "luna-local/v1",
        }
        proposal = node_to_proposal(node, "Implement the feature")
        self.assertEqual(proposal["class"], "CREATE_TASK")
        req = proposal["requirement"]
        self.assertNotIn("work_profile", req)

    def test_operations_node(self):
        node = {
            "node_id": "nd-o1",
            "node_key": "deploy",
            "work_profile": "GENERAL_OPERATION",
            "realization_kind": "TASK_CONTRACT",
        }
        proposal = node_to_proposal(node, "Deploy the service")
        self.assertEqual(proposal["class"], "CREATE_TASK")

    def test_review_node(self):
        node = {
            "node_id": "nd-rev",
            "node_key": "code-review",
            "work_profile": "ENGINEERING",
            "organizational_role": "REVIEWER",
            "realization_kind": "TASK_CONTRACT",
        }
        pred = [{"edge_id": "e1", "edge_kind": "RECORD",
                 "target": "sha256:impl"}]
        proposal = node_to_proposal(node, "Review implementation", pred)
        self.assertEqual(proposal["class"], "REQUEST_REVIEW")

    def test_workstream_to_proposal_compatibility(self):
        ws = {
            "workstream_key": "legacy-work",
            "work_profile": "RESEARCH",
        }
        proposal = workstream_to_proposal(ws, "Legacy research", "pj-1")
        self.assertIn("workstream", proposal)
        self.assertEqual(proposal["origin_kind"], "ENGINEER_CYCLE")
        self.assertNotIn("origin_id", proposal)

    def test_node_with_predecessors(self):
        node = {
            "node_id": "nd-dep",
            "node_key": "dependent-work",
            "work_profile": "ENGINEERING",
            "realization_kind": "TASK_CONTRACT",
        }
        preds = [{
            "edge_id": "e1", "edge_kind": "RECORD",
            "target": "sha256:predecessor",
            "observation_requirement": "ACCEPTANCE",
        }]
        proposal = node_to_proposal(node, "Dependent work", preds)
        self.assertEqual(len(proposal["requirement"]["predecessors"]), 1)


class TestApplicatorWithNodes(unittest.TestCase):

    def test_create_node_decision(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-an", "applicator node", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-an", "obj-an", "test/v1", "digest-an",
                    "ORCHESTRATOR_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-an", "obj-an", "cd-an",
                    "ORCHESTRATOR_ELEVATED", "coordinator", now)

            applicator = OrchestrationApplicator(
                store, orchestration_id="run-an")
            results = applicator.apply([{
                "class": "CREATE_NODE",
                "node_key": "research-a",
                "work_profile": "RESEARCH",
                "organizational_role": "SPECIALIST",
                "realization_kind": "TASK_CONTRACT",
                "capability": "research",
                "execution_profile": "luna-research/v1",
            }], now)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["class"], "CREATE_NODE")
            self.assertEqual(results[0]["status"], "CREATED")
            self.assertIn("node_id", results[0])

            nodes = store.orchestration_nodes("run-an")
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0]["work_profile"], "RESEARCH")
        finally:
            store.close()
            os.unlink(path)

    def test_v1_create_workstream_normalized(self):
        """v1 CREATE_WORKSTREAM becomes CREATE_NODE via normalization."""
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-v1", "v1 compat", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-v1", "obj-v1", "test/v1", "digest-v1",
                    "ORCHESTRATOR_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-v1", "obj-v1", "cd-v1",
                    "ORCHESTRATOR_ELEVATED", "coordinator", now)
                project, _ = store.open_project(
                    cur, "obj-v1", "V1 Project", "sol",
                    "IMPLICIT", "orc-dec-v1", now)

            applicator = OrchestrationApplicator(
                store, orchestration_id="run-v1")
            results = applicator.apply([{
                "class": "CREATE_WORKSTREAM",
                "project_id": project["project_id"],
                "workstream_key": "legacy-node",
                "work_profile": "ENGINEERING",
            }], now)

            self.assertEqual(results[0]["class"], "CREATE_NODE")
            nodes = store.orchestration_nodes("run-v1")
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0]["node_key"], "legacy-node")
            self.assertIn("workstream_id", results[0])
        finally:
            store.close()
            os.unlink(path)

    def test_add_edge_decision(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-ae", "edge test", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-ae", "obj-ae", "test/v1", "digest-ae",
                    "ORCHESTRATOR_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-ae", "obj-ae", "cd-ae",
                    "ORCHESTRATOR_ELEVATED", "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-src", "run-ae", "source", now,
                    realization_kind="TASK_CONTRACT")
                store.create_orchestration_node(
                    cur, "nd-tgt", "run-ae", "target", now,
                    realization_kind="TASK_CONTRACT")

            applicator = OrchestrationApplicator(
                store, orchestration_id="run-ae")
            results = applicator.apply([{
                "class": "ADD_EDGE",
                "source_node": "nd-src",
                "target_node": "nd-tgt",
                "dependency_kind": "ACCEPTED_ARTIFACT",
            }], now)

            self.assertEqual(results[0]["class"], "ADD_EDGE")
            self.assertEqual(results[0]["status"], "CREATED")

            edges = store.edges_for_node("nd-tgt", direction="inbound")
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0]["source_node_id"], "nd-src")
        finally:
            store.close()
            os.unlink(path)

    def test_complete_node_decision(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-cn", "complete node", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-cn", "obj-cn", "test/v1", "digest-cn",
                    "DIRECT", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-cn", "obj-cn", "cd-cn", "DIRECT",
                    "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-cmp", "run-cn", "work", now,
                    realization_kind="TASK_CONTRACT")

            applicator = OrchestrationApplicator(
                store, orchestration_id="run-cn")
            results = applicator.apply([{
                "class": "COMPLETE_NODE",
                "node_id": "nd-cmp",
            }], now)

            self.assertEqual(results[0]["status"], "APPLIED")
            node = store.orchestration_node("nd-cmp")
            self.assertEqual(node["status"], "COMPLETED")
        finally:
            store.close()
            os.unlink(path)

    def test_bind_node_to_project(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-bp", "bind project", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-bp", "obj-bp", "test/v1", "digest-bp",
                    "ORCHESTRATOR_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-bp", "obj-bp", "cd-bp",
                    "ORCHESTRATOR_ELEVATED", "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-bp", "run-bp", "impl", now,
                    realization_kind="TASK_CONTRACT")
                project, _ = store.open_project(
                    cur, "obj-bp", "P1", "sol", "IMPLICIT",
                    "orc-dec", now)

            applicator = OrchestrationApplicator(
                store, orchestration_id="run-bp")
            results = applicator.apply([{
                "class": "BIND_NODE_TO_PROJECT",
                "node_id": "nd-bp",
                "project_id": project["project_id"],
                "reason": "engineering realization",
            }], now)

            self.assertEqual(results[0]["status"], "APPLIED")
            bindings = store.project_bindings_for_node("nd-bp")
            self.assertEqual(len(bindings), 1)
        finally:
            store.close()
            os.unlink(path)

    def test_engineering_request_creates_binding(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-er", "eng request", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-er", "obj-er", "test/v1", "digest-er",
                    "ORCHESTRATOR_ELEVATED", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-er", "obj-er", "cd-er",
                    "ORCHESTRATOR_ELEVATED", "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-er", "run-er", "architecture", now,
                    realization_kind="ENGINEER_REASONING")

            applicator = OrchestrationApplicator(
                store, orchestration_id="run-er")
            results = applicator.apply([{
                "class": "REQUEST_ENGINEERING_DECISION",
                "node_id": "nd-er",
                "question": "What architecture?",
            }], now)

            self.assertEqual(results[0]["status"], "APPLIED")
            binding = store.engineering_binding_for_node("nd-er")
            self.assertIsNotNone(binding)
            self.assertEqual(binding["question"], "What architecture?")
        finally:
            store.close()
            os.unlink(path)


class TestNodeOfSequenceLookup(unittest.TestCase):

    def test_lookup_via_authority_binding(self):
        store, path = _make_store()
        try:
            from autobot.models import instant
            clock = FakeClock()
            now = instant(clock.now())
            store.add_objective("obj-ns", "lookup", "op", now)
            with store.transaction() as cur:
                store.record_coordinator_decision(
                    cur, "cd-ns", "obj-ns", "test/v1", "digest-ns",
                    "DIRECT", {}, None, now)
                store.create_orchestration_run(
                    cur, "run-ns", "obj-ns", "cd-ns", "DIRECT",
                    "coordinator", now)
                store.create_orchestration_node(
                    cur, "nd-ns", "run-ns", "work", now,
                    realization_kind="TASK_CONTRACT",
                    work_profile="RESEARCH")
                store.create_authority_binding(
                    cur, "ab-ns", "nd-ns", now, sequence_id="seq-ns")

            node = store.node_of_sequence("seq-ns")
            self.assertIsNotNone(node)
            self.assertEqual(node["node_id"], "nd-ns")
            self.assertEqual(node["work_profile"], "RESEARCH")
        finally:
            store.close()
            os.unlink(path)


class TestOpusOrchestratorPolicy(unittest.TestCase):

    def test_policy_id(self):
        policy = OpusOrchestratorPolicy()
        self.assertEqual(policy.policy_id, OPUS_ORCHESTRATOR_POLICY_ID)

    def test_no_reasoner_returns_empty(self):
        policy = OpusOrchestratorPolicy()
        view = OrchestratorPlanningView(
            objectives=[{"objective_id": "obj-1", "text": "test",
                         "status": "ACTIVE"}],
            projects=[], taskforce_state={})
        result = policy.interpret(view)
        self.assertEqual(result, [])

    def test_scripted_reasoner(self):
        decisions = [
            {"class": "CREATE_NODE", "node_key": "research",
             "work_profile": "RESEARCH",
             "realization_kind": "TASK_CONTRACT"},
        ]

        def fake_reasoner(prompt):
            return json.dumps(decisions)

        policy = OpusOrchestratorPolicy(reasoner=fake_reasoner)
        view = OrchestratorPlanningView(
            objectives=[{"objective_id": "obj-1", "text": "research",
                         "status": "ACTIVE"}],
            projects=[], taskforce_state={})
        result = policy.interpret(view)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["class"], "CREATE_NODE")

    def test_parse_bare_json(self):
        text = '[{"class": "CREATE_NODE", "node_key": "a"}]'
        result = _parse_decisions(text)
        self.assertEqual(len(result), 1)

    def test_parse_code_fence(self):
        text = '```json\n[{"class": "CREATE_NODE", "node_key": "a"}]\n```'
        result = _parse_decisions(text)
        self.assertEqual(len(result), 1)

    def test_parse_empty(self):
        self.assertEqual(_parse_decisions("[]"), [])
        self.assertEqual(_parse_decisions(""), [])

    def test_parse_wrapped_object(self):
        text = '{"decisions": [{"class": "CREATE_NODE", "node_key": "b"}]}'
        result = _parse_decisions(text)
        self.assertEqual(len(result), 1)

    def test_parse_embedded_array(self):
        text = 'Some text before\n[{"class": "ADD_EDGE"}]\nafter'
        result = _parse_decisions(text)
        self.assertEqual(len(result), 1)

    def test_render_view_includes_objectives(self):
        view = OrchestratorPlanningView(
            objectives=[{"objective_id": "obj-1",
                         "text": "Research market trends",
                         "status": "ACTIVE"}],
            projects=[], taskforce_state={})
        prompt = _render_orchestrator_view(view)
        self.assertIn("Research market trends", prompt)
        self.assertIn("obj-1", prompt)

    def test_render_view_includes_nodes(self):
        view = OrchestratorPlanningView(
            objectives=[{"objective_id": "obj-1", "text": "t",
                         "status": "ACTIVE"}],
            projects=[], taskforce_state={},
            nodes=[{"node_id": "nd-1", "node_key": "research",
                    "work_profile": "RESEARCH",
                    "realization_kind": "TASK_CONTRACT",
                    "status": "EXECUTING"}])
        prompt = _render_orchestrator_view(view)
        self.assertIn("nd-1", prompt)
        self.assertIn("RESEARCH", prompt)

    def test_invalid_decisions_filtered(self):
        raw = [
            {"class": "CREATE_NODE", "node_key": "valid",
             "work_profile": "RESEARCH",
             "realization_kind": "TASK_CONTRACT"},
            {"class": "INVALID_CLASS"},
            "not a dict",
        ]

        def fake_reasoner(prompt):
            return json.dumps(raw)

        policy = OpusOrchestratorPolicy(reasoner=fake_reasoner)
        view = OrchestratorPlanningView(
            objectives=[{"objective_id": "obj-1", "text": "t",
                         "status": "ACTIVE"}],
            projects=[], taskforce_state={})
        result = policy.interpret(view)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["node_key"], "valid")

    def test_does_not_delegate_to_engineer(self):
        policy = OpusOrchestratorPolicy()
        self.assertFalse(policy.should_delegate_to_engineer(
            None, {"work_profile": "ENGINEERING"}))


class TestProductionCoordinatorPolicy(unittest.TestCase):

    def _evaluate(self, text, constraints=None, context=None):
        policy = ProductionCoordinatorPolicy()
        objective = {"text": text, "constraints": constraints or []}
        return policy.evaluate(objective, context or {})

    def test_simple_research_is_direct(self):
        result = self._evaluate("Research current pricing models")
        self.assertEqual(result["elevation_mode"], "DIRECT")

    def test_architecture_design_is_engineer_elevated(self):
        result = self._evaluate(
            "Design the new authentication architecture")
        self.assertEqual(result["elevation_mode"], "ENGINEER_ELEVATED")
        self.assertTrue(result["features"].get("engineering_design"))

    def test_complex_multi_domain_is_orchestrator(self):
        result = self._evaluate(
            "Research market trends and design a comprehensive "
            "multi-domain deployment architecture including parallel "
            "analysis across engineering and operations")
        self.assertEqual(result["elevation_mode"],
                         "ORCHESTRATOR_ELEVATED")

    def test_bounded_implementation_is_direct(self):
        result = self._evaluate("Fix the login button color")
        self.assertEqual(result["elevation_mode"], "DIRECT")

    def test_research_plus_engineering_elevates(self):
        result = self._evaluate(
            "Research and design a comprehensive migration strategy "
            "with parallel analysis and deploy across multiple domains")
        self.assertEqual(result["elevation_mode"],
                         "ORCHESTRATOR_ELEVATED")

    def test_deterministic_for_same_input(self):
        policy = ProductionCoordinatorPolicy()
        obj = {"text": "Research and architect the new system",
               "constraints": []}
        ctx = {"active_runs": 0}
        r1 = policy.evaluate(obj, ctx)
        r2 = policy.evaluate(obj, ctx)
        self.assertEqual(r1["elevation_mode"], r2["elevation_mode"])
        self.assertEqual(r1["features"], r2["features"])

    def test_elevation_modes_valid(self):
        for mode in ELEVATION_MODES:
            self.assertIn(mode, {"DIRECT", "ENGINEER_ELEVATED",
                                 "ORCHESTRATOR_ELEVATED"})

    def test_high_active_runs_increases_complexity(self):
        policy = ProductionCoordinatorPolicy()
        obj = {"text": "Research new approaches and design architecture",
               "constraints": []}
        low = policy.evaluate(obj, {"active_runs": 0})
        high = policy.evaluate(obj, {"active_runs": 5})
        low_feats = sum(1 for v in low["features"].values() if v)
        high_feats = sum(1 for v in high["features"].values() if v)
        self.assertGreaterEqual(high_feats, low_feats)


if __name__ == "__main__":
    unittest.main()
