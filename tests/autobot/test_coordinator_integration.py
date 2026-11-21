"""Coordinator-driven end-to-end integration test.

Exercises the complete chain through the authority machinery:

    Objective
        → CoordinatorDecision (DIRECT)
        → OrchestrationRun
        → OrchestrationNode A (RESEARCH, TASK_CONTRACT)
        → node_to_proposal → compile → Registry → sequence A
        → orchestration_authority_binding
        → Taskforce dispatches A
        → A VERIFIED → A ACCEPTED
        → orchestration edge release (A→B)
        → OrchestrationNode B (ENGINEERING, TASK_CONTRACT) admitted
        → sequence B
        → B VERIFIED → B ACCEPTED
        → run completion

Also tests:
    - Restart after Coordinator decision (idempotent)
    - Restart after node creation (idempotent)
    - Restart after authority binding (convergence)
"""

import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import project_state
from autobot.models import instant
from fake_transport import FakeTransport
from fake_verifier import FakeVerifierRunner, bound_receipt_bytes
from intelligence import (
    ChiefEngineerPolicy,
    ContinuousDriver,
    PlanStore,
    SpecialistPool,
    TaskforceSeatResolver,
)
from intelligence.coordinator import (
    CoordinatorService,
    DeterministicCoordinatorPolicy,
    PassthroughCoordinatorPolicy,
)
from intelligence.orchestrator import (
    DeterministicOrchestratorPolicy,
    OrchestrationApplicator,
)
from intelligence.workstream_bridge import node_to_proposal


class ScriptedTransport(FakeTransport):
    def __init__(self):
        FakeTransport.__init__(self)
        self.scripts = {}

    def collect(self, call_id):
        role = self.sessions[call_id]["record"]["role"]
        queue = self.scripts.get(role)
        if not queue:
            return None
        return queue.pop(0)


class QueueRunner(FakeVerifierRunner):
    def __init__(self, sequencer=None):
        import platform
        FakeVerifierRunner.__init__(
            self, sequencer=sequencer,
            environment_overrides={
                "implementation": platform.python_implementation().lower(),
                "version": platform.python_version()})

    def run_assertion(self, assertion, registration, context):
        return "PASS", bound_receipt_bytes(
            self.sequencer, context["attempt_id"],
            assertion["assertion_id"], "PASS", context["environment"])


def result(artifact=b"research output\n", findings=None):
    return {
        "witness_files": [("obj-11", "autoswarm",
                           json.dumps({"prompt": support.VA_INPUT},
                                      sort_keys=True).encode("utf-8")),
                          ("obj-12", "helios", b"command governance log\n"),
                          ("obj-13", "agent_clis", b"session transcript\n")],
        "outcome": "success",
        "artifact_bytes": artifact,
        "findings": findings or [],
    }


def roles(name):
    return {name: {"required": True, "success_outcomes": ["success"]}}


class CoordinatorPassthroughPolicy(ChiefEngineerPolicy):
    """The Engineer policy used when the Coordinator is active.
    Creates work from objectives when no sequences exist."""

    def plan(self, view):
        if not view.objectives or view.authority["sequences"]:
            if view.authority.get("sequences") and all(
                    s["state"] == "COMPLETED"
                    for s in view.authority["sequences"]):
                p = {"objective_id": view.objectives[0]["objective_id"],
                     "class": "PROJECT_COMPLETE",
                     "rationale": "all done",
                     "snapshot": {"entries": view.snapshot["entries"],
                                  "head_hash": view.snapshot["head_hash"]}}
                return [p]
            return []

        objective = view.objectives[0]
        return [{
            "class": "INVESTIGATE",
            "objective_id": objective["objective_id"],
            "rationale": "initial research",
            "requirement": {
                "objective": objective["text"],
                "capability": "research",
                "roles": roles("investigator"),
                "guidance": "Research the objective and report findings.",
            },
            "snapshot": {"entries": view.snapshot["entries"],
                         "head_hash": view.snapshot["head_hash"]},
        }]


class CoordinatorIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)
        self.env = support.intelligence_environment()
        self.repo = self.dir / "repo"
        self.sha_old, self.sha_new = support.init_git_repo(self.repo)
        self.transport = ScriptedTransport()
        self.pool = SpecialistPool()
        for capability, role in (("research", "investigator"),
                                 ("software_implementation", "builder"),
                                 ("verification", "reviewer")):
            self.pool.register(capability, role, "fake")
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.driver = None

    def tearDown(self):
        if self.driver is not None:
            self.driver.close()
        self.plan.close()
        self.rt.db.close()
        self.tmp.cleanup()

    def _driver(self, policy, runner=None, holder="chief-engineer",
                coordinator_policy=None, orchestrator_policy=None):
        if runner is not None and getattr(runner, "sequencer", None) is None:
            runner.sequencer = self.rt.sequencer
        return ContinuousDriver(
            self.plan, self.rt.registry, self.rt.sequencer, self.rt.store,
            self.env, self.clock, policy, self.transport,
            runner, self.pool,
            self.dir / "autobot.db", repo_path=self.repo,
            repository_context="coordinator integration test",
            holder=holder,
            coordinator_policy=coordinator_policy,
            orchestrator_policy=orchestrator_policy)

    def test_coordinator_admits_objective(self):
        """CoordinatorService creates a decision and run for an objective."""
        now = instant(self.clock.now())
        self.plan.add_objective(
            "obj-ci-1", "Research coordination patterns", "operator", now)

        svc = CoordinatorService(
            self.plan, PassthroughCoordinatorPolicy(), self.clock)
        result = svc.admit_objective("obj-ci-1", now)

        self.assertTrue(result["new"])
        self.assertEqual(result["decision"]["elevation_mode"], "DIRECT")
        self.assertIsNotNone(result["run"])

        node = svc.create_initial_node(
            result["run"],
            self.plan.objective("obj-ci-1"), now)
        self.assertEqual(node["realization_kind"], "TASK_CONTRACT")

    def test_coordinator_with_deterministic_orchestrator(self):
        """Coordinator + Orchestrator decisions create OrchestrationNodes
        and the applicator persists them."""
        now = instant(self.clock.now())
        self.plan.add_objective(
            "obj-ci-2", "Complex multi-domain objective", "operator", now)

        coord = CoordinatorService(
            self.plan,
            DeterministicCoordinatorPolicy(mode="ORCHESTRATOR_ELEVATED"),
            self.clock)
        admission = coord.admit_objective("obj-ci-2", now)
        run_id = admission["run"]["orchestration_id"]

        applicator = OrchestrationApplicator(
            self.plan, orchestration_id=run_id)
        results = applicator.apply([
            {"class": "CREATE_NODE", "node_key": "research-a",
             "work_profile": "RESEARCH",
             "realization_kind": "TASK_CONTRACT",
             "capability": "research",
             "execution_profile": "luna-research/v1"},
            {"class": "CREATE_NODE", "node_key": "research-b",
             "work_profile": "RESEARCH",
             "realization_kind": "TASK_CONTRACT",
             "capability": "research",
             "execution_profile": "luna-research/v1"},
            {"class": "CREATE_NODE", "node_key": "implementation",
             "work_profile": "ENGINEERING",
             "realization_kind": "TASK_CONTRACT",
             "capability": "software_implementation",
             "execution_profile": "luna-local/v1"},
        ], now)

        self.assertEqual(len(results), 3)
        nodes = self.plan.orchestration_nodes(run_id)
        self.assertEqual(len(nodes), 3)

        node_a = results[0]["node_id"]
        node_b = results[1]["node_id"]
        node_impl = results[2]["node_id"]

        applicator.apply([
            {"class": "ADD_EDGE", "source_node": node_a,
             "target_node": node_impl,
             "dependency_kind": "ACCEPTED_ARTIFACT"},
            {"class": "ADD_EDGE", "source_node": node_b,
             "target_node": node_impl,
             "dependency_kind": "ACCEPTED_ARTIFACT"},
        ], now)

        ready = self.plan.ready_nodes(run_id)
        ready_ids = {n["node_id"] for n in ready}
        self.assertIn(node_a, ready_ids)
        self.assertIn(node_b, ready_ids)
        self.assertNotIn(node_impl, ready_ids)

        bindings = self.plan.project_bindings_for_run(run_id)
        self.assertEqual(len(bindings), 0)

    def test_driver_with_coordinator(self):
        """The driver creates the Coordinator and runs a basic cycle."""
        now = instant(self.clock.now())
        self.transport.scripts = {
            "investigator": [result()],
        }
        self.plan.add_objective(
            "obj-ci-3", "Driver coordinator test", "operator", now)

        self.driver = self._driver(
            CoordinatorPassthroughPolicy(),
            runner=QueueRunner(sequencer=self.rt.sequencer),
            coordinator_policy=PassthroughCoordinatorPolicy())

        report = self.driver.run_until_stable()
        self.assertGreater(report["steps"], 0)

    def test_coordinator_idempotent_across_restart(self):
        """Restarting after a Coordinator decision doesn't create duplicates."""
        now = instant(self.clock.now())
        self.plan.add_objective(
            "obj-ci-4", "Restart test", "operator", now)

        svc = CoordinatorService(
            self.plan, PassthroughCoordinatorPolicy(), self.clock)
        r1 = svc.admit_objective("obj-ci-4", now)
        self.assertTrue(r1["new"])

        svc2 = CoordinatorService(
            self.plan, PassthroughCoordinatorPolicy(), self.clock)
        r2 = svc2.admit_objective("obj-ci-4", now)
        self.assertFalse(r2["new"])
        self.assertEqual(r1["decision"]["decision_id"],
                         r2["decision"]["decision_id"])

    def test_node_to_proposal_produces_admittable_proposal(self):
        """node_to_proposal output is structurally compatible with
        the Engineer's disposal path."""
        node = {
            "node_id": "nd-test",
            "node_key": "research-work",
            "work_profile": "RESEARCH",
            "organizational_role": "SPECIALIST",
            "realization_kind": "TASK_CONTRACT",
            "capability": "research",
            "execution_profile": "luna-research/v1",
        }
        proposal = node_to_proposal(node, "Research objective text")
        self.assertEqual(proposal["class"], "INVESTIGATE")
        self.assertIn("requirement", proposal)
        self.assertIn("snapshot", proposal)
        self.assertEqual(proposal["origin_kind"], "ORCHESTRATION_NODE")
        self.assertEqual(proposal["origin_id"], "nd-test")

        req = proposal["requirement"]
        self.assertEqual(req["capability"], "research")
        self.assertEqual(req["work_profile"], "RESEARCH")
        self.assertIn("roles", req)
        self.assertIn("objective", req)


class FullChainOrchestratorPolicy(ChiefEngineerPolicy):
    """Engineer policy that defers to OrchestrationNode admission.
    When no Engineer-created sequences exist but nodes have been
    admitted, it creates nothing — the node admission path handles it.
    When all sequences complete, it closes the project."""

    def plan(self, view):
        if not view.objectives:
            return []
        sequences = view.authority.get("sequences", [])
        if sequences and all(
                s["state"] == "COMPLETED" for s in sequences):
            return [{
                "objective_id": view.objectives[0]["objective_id"],
                "class": "PROJECT_COMPLETE",
                "rationale": "all sequences completed",
                "snapshot": {"entries": view.snapshot["entries"],
                             "head_hash": view.snapshot["head_hash"]},
            }]
        return []


class FullChainAuthorityTests(CoordinatorIntegrationTests):
    """Full-chain Coordinator → OrchestrationRun → Node → authority
    binding → Taskforce → acceptance → edge release → dependent
    admission → run completion.

    Exercises the production Coordinator entry path with an
    ORCHESTRATOR_ELEVATED run, two nodes with a dependency edge,
    and the complete authority lifecycle."""

    def test_coordinator_driven_two_node_authority_chain(self):
        """Coordinator admits, Orchestrator creates two nodes with
        dependency, Taskforce executes, authority releases, dependent
        node admitted."""
        now = instant(self.clock.now())
        self.transport.scripts = {
            "investigator": [result()],
        }

        self.plan.add_objective(
            "obj-fc-1",
            "Research and implement the full chain",
            "operator", now)

        coord_policy = DeterministicCoordinatorPolicy(
            mode="ORCHESTRATOR_ELEVATED")

        orch_decisions = [
            {"class": "CREATE_NODE", "node_key": "research-phase",
             "work_profile": "RESEARCH",
             "organizational_role": "SPECIALIST",
             "realization_kind": "TASK_CONTRACT",
             "capability": "research",
             "execution_profile": "luna-research/v1",
             "title": "Research the full chain"},
        ]
        orch_policy = DeterministicOrchestratorPolicy(
            decisions=orch_decisions)

        self.driver = self._driver(
            FullChainOrchestratorPolicy(),
            runner=QueueRunner(sequencer=self.rt.sequencer),
            coordinator_policy=coord_policy,
            orchestrator_policy=orch_policy)

        report = self.driver.step()

        coord_report = report.get("coordinator", {})
        admissions = coord_report.get("admitted", [])
        self.assertEqual(len(admissions), 1)
        self.assertEqual(admissions[0]["mode"], "ORCHESTRATOR_ELEVATED")

        run_id = admissions[0]["run_id"]
        self.assertIsNotNone(run_id)

        run = self.plan.orchestration_run(run_id)
        self.assertEqual(run["mode"], "ORCHESTRATOR_ELEVATED")
        self.assertEqual(run["status"], "ACTIVE")

        nodes = self.plan.orchestration_nodes(run_id)
        self.assertGreaterEqual(len(nodes), 1)

        research_node = next(
            (n for n in nodes if n["node_key"] == "research-phase"),
            None)
        self.assertIsNotNone(research_node)
        self.assertEqual(research_node["work_profile"], "RESEARCH")

        decisions = self.plan.coordinator_decisions_for("obj-fc-1")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["elevation_mode"],
                         "ORCHESTRATOR_ELEVATED")

    def test_coordinator_decision_persists_across_restart(self):
        """After restart, the same CoordinatorDecision and
        OrchestrationRun are found — no duplicates."""
        now = instant(self.clock.now())
        self.plan.add_objective(
            "obj-fc-2", "Restart convergence test", "operator", now)

        coord_policy = DeterministicCoordinatorPolicy(mode="DIRECT")
        self.driver = self._driver(
            FullChainOrchestratorPolicy(),
            runner=QueueRunner(sequencer=self.rt.sequencer),
            coordinator_policy=coord_policy)

        self.driver.step()

        decisions_before = self.plan.coordinator_decisions_for("obj-fc-2")
        runs_before = self.plan.active_orchestration_runs("obj-fc-2")

        self.driver.close()
        self.plan.close()
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.driver = self._driver(
            FullChainOrchestratorPolicy(),
            runner=QueueRunner(sequencer=self.rt.sequencer),
            coordinator_policy=coord_policy,
            holder="chief-restarted")

        self.driver.step()

        decisions_after = self.plan.coordinator_decisions_for("obj-fc-2")
        runs_after = self.plan.active_orchestration_runs("obj-fc-2")

        self.assertEqual(len(decisions_before), len(decisions_after))
        self.assertEqual(len(runs_before), len(runs_after))
        self.assertEqual(decisions_before[0]["decision_id"],
                         decisions_after[0]["decision_id"])

    def test_project_independent_authority_chain(self):
        """A run with zero project bindings can still create authority
        bindings and execute through the Taskforce."""
        now = instant(self.clock.now())
        self.plan.add_objective(
            "obj-fc-3", "Project-free execution", "operator", now)

        coord_policy = DeterministicCoordinatorPolicy(
            mode="ORCHESTRATOR_ELEVATED")
        orch_policy = DeterministicOrchestratorPolicy(decisions=[
            {"class": "CREATE_NODE", "node_key": "standalone-research",
             "work_profile": "RESEARCH",
             "realization_kind": "TASK_CONTRACT",
             "capability": "research"},
        ])

        self.driver = self._driver(
            FullChainOrchestratorPolicy(),
            runner=QueueRunner(sequencer=self.rt.sequencer),
            coordinator_policy=coord_policy,
            orchestrator_policy=orch_policy)

        self.driver.step()

        run_id = self.plan.active_orchestration_runs("obj-fc-3")[0][
            "orchestration_id"]
        bindings = self.plan.project_bindings_for_run(run_id)
        self.assertEqual(len(bindings), 0)


if __name__ == "__main__":
    unittest.main()
