"""Tests for the Objective Orchestrator service."""
import unittest

from intelligence.orchestrator import (
    DeterministicOrchestratorPolicy,
    OrchestratorPlanningView,
    OrchestratorPolicy,
    OrchestratorService,
    PassthroughOrchestratorPolicy,
    ORCHESTRATOR_POLICY_ID,
    ORCHESTRATOR_ROLE,
)


def _objective(oid="obj-test-001", text="test objective", status="ACTIVE"):
    return {"objective_id": oid, "text": text, "status": status,
            "authored_by": "operator", "created_utc": "2026-11-20T00:00:00Z"}


def _project(pid="pj_test001", oid="obj-test-001", title="Test Project",
             status="ACTIVE", work_profile=None, workstreams=None):
    p = {"project_id": pid, "objective_id": oid, "title": title,
         "status": status}
    if work_profile is not None:
        p["work_profile"] = work_profile
    if workstreams is not None:
        p["workstreams"] = workstreams
    return p


class TestOrchestratorPlanningView(unittest.TestCase):

    def test_construction(self):
        view = OrchestratorPlanningView(
            objectives=[_objective()],
            projects=[_project()],
            taskforce_state={"members": 4},
        )
        self.assertEqual(len(view.objectives), 1)
        self.assertEqual(len(view.projects), 1)
        self.assertEqual(view.taskforce["members"], 4)
        self.assertEqual(view.accepted_artifacts, [])
        self.assertEqual(view.findings, [])

    def test_to_dict(self):
        view = OrchestratorPlanningView(
            objectives=[_objective()],
            projects=[_project()],
            taskforce_state={"members": 4},
            work_profiles={"ENGINEERING": {"active": True}},
        )
        d = view.to_dict()
        self.assertEqual(d["role"], ORCHESTRATOR_ROLE)
        self.assertEqual(len(d["objectives"]), 1)
        self.assertIn("ENGINEERING", d["work_profiles"])

    def test_for_objective(self):
        o1 = _objective("obj-001")
        o2 = _objective("obj-002")
        p1 = _project("pj_001", "obj-001")
        p2 = _project("pj_002", "obj-002")
        view = OrchestratorPlanningView(
            objectives=[o1, o2],
            projects=[p1, p2],
            taskforce_state={},
        )
        narrow = view.for_objective("obj-001")
        self.assertIsNotNone(narrow)
        self.assertEqual(len(narrow.objectives), 1)
        self.assertEqual(narrow.objectives[0]["objective_id"], "obj-001")
        self.assertEqual(len(narrow.projects), 1)

    def test_for_objective_missing(self):
        view = OrchestratorPlanningView(
            objectives=[_objective("obj-001")],
            projects=[],
            taskforce_state={},
        )
        self.assertIsNone(view.for_objective("obj-nonexistent"))

    def test_enrichment_fields(self):
        view = OrchestratorPlanningView(
            objectives=[],
            projects=[],
            taskforce_state={},
            work_profiles={"ENGINEERING": {}, "RESEARCH": {}},
            resource_scopes={"repository": {"paths": []}},
            execution_profiles={"luna-local/v1": {}},
            dependency_state={"pending": 0},
        )
        self.assertEqual(len(view.work_profiles), 2)
        self.assertIn("repository", view.resource_scopes)
        self.assertIn("luna-local/v1", view.execution_profiles)
        self.assertEqual(view.dependency_state["pending"], 0)


class TestOrchestratorPolicy(unittest.TestCase):

    def test_base_raises(self):
        policy = OrchestratorPolicy()
        with self.assertRaises(NotImplementedError):
            policy.interpret({})

    def test_deterministic_policy(self):
        decisions = [{"class": "CREATE_PROJECT", "title": "Test"}]
        policy = DeterministicOrchestratorPolicy(decisions=decisions)
        result = policy.interpret({})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["class"], "CREATE_PROJECT")

    def test_deterministic_policy_empty(self):
        policy = DeterministicOrchestratorPolicy()
        result = policy.interpret({})
        self.assertEqual(result, [])

    def test_passthrough_policy(self):
        policy = PassthroughOrchestratorPolicy()
        result = policy.interpret({})
        self.assertEqual(result, [])
        self.assertEqual(policy.policy_id, "passthrough-orchestrator/v1")

    def test_passthrough_always_delegates(self):
        policy = PassthroughOrchestratorPolicy()
        self.assertTrue(policy.should_delegate_to_engineer(
            {}, {"work_profile": "RESEARCH"}))

    def test_engineering_delegation(self):
        policy = OrchestratorPolicy()
        self.assertTrue(policy.should_delegate_to_engineer(
            {}, {"work_profile": "ENGINEERING"}))

    def test_research_no_delegation(self):
        policy = OrchestratorPolicy()
        self.assertFalse(policy.should_delegate_to_engineer(
            {}, {"work_profile": "RESEARCH"}))

    def test_mixed_workstream_delegation(self):
        policy = OrchestratorPolicy()
        project = {
            "work_profile": "GENERAL_OPERATION",
            "workstreams": [
                {"work_profile": "ENGINEERING"},
                {"work_profile": "RESEARCH"},
            ],
        }
        self.assertTrue(policy.should_delegate_to_engineer({}, project))


class TestOrchestratorService(unittest.TestCase):

    def test_empty_objectives(self):
        policy = PassthroughOrchestratorPolicy()
        svc = OrchestratorService(plan=None, policy=policy)
        result = svc.run_cycle(objectives=[], projects=[], taskforce_state={})
        self.assertTrue(result["orchestrator_cycle"])
        self.assertEqual(result["decisions"], [])
        self.assertEqual(result["delegate_to_engineer"], [])

    def test_passthrough_delegates_all(self):
        policy = PassthroughOrchestratorPolicy()
        svc = OrchestratorService(plan=None, policy=policy)
        result = svc.run_cycle(
            objectives=[_objective()],
            projects=[_project(work_profile="ENGINEERING")],
            taskforce_state={"members": 4},
        )
        self.assertTrue(result["orchestrator_cycle"])
        self.assertEqual(len(result["delegate_to_engineer"]), 1)
        self.assertEqual(result["objectives_count"], 1)

    def test_deterministic_decisions(self):
        decisions = [
            {"class": "CREATE_PROJECT", "objective_id": "obj-test-001",
             "work_profile": "RESEARCH"},
        ]
        policy = DeterministicOrchestratorPolicy(decisions=decisions)
        svc = OrchestratorService(plan=None, policy=policy)
        result = svc.run_cycle(
            objectives=[_objective()],
            projects=[],
            taskforce_state={},
        )
        self.assertEqual(len(result["decisions"]), 1)
        self.assertEqual(result["decisions"][0]["class"], "CREATE_PROJECT")

    def test_multiple_projects_delegation(self):
        policy = OrchestratorPolicy.__new__(OrchestratorPolicy)
        policy.policy_id = "test-orchestrator/v1"
        policy.interpret = lambda view: []
        svc = OrchestratorService(plan=None, policy=policy)
        result = svc.run_cycle(
            objectives=[_objective()],
            projects=[
                _project("pj_eng", work_profile="ENGINEERING"),
                _project("pj_res", work_profile="RESEARCH"),
                _project("pj_ops", work_profile="GENERAL_OPERATION"),
            ],
            taskforce_state={},
        )
        self.assertEqual(len(result["delegate_to_engineer"]), 1)
        self.assertIn("pj_eng", result["delegate_to_engineer"])

    def test_build_view(self):
        policy = PassthroughOrchestratorPolicy()
        svc = OrchestratorService(plan=None, policy=policy)
        view = svc.build_view(
            objectives=[_objective()],
            projects=[_project()],
            taskforce_state={"members": 4},
            work_profiles={"ENGINEERING": {}},
        )
        self.assertIsInstance(view, OrchestratorPlanningView)
        self.assertEqual(len(view.objectives), 1)
        self.assertIn("ENGINEERING", view.work_profiles)

    def test_policy_id_in_report(self):
        policy = PassthroughOrchestratorPolicy()
        svc = OrchestratorService(plan=None, policy=policy)
        result = svc.run_cycle(
            objectives=[_objective()],
            projects=[],
            taskforce_state={},
        )
        self.assertEqual(result["policy_id"], "passthrough-orchestrator/v1")

    def test_last_cycle_recorded(self):
        policy = PassthroughOrchestratorPolicy()
        svc = OrchestratorService(plan=None, policy=policy)
        self.assertIsNone(svc._last_cycle)
        svc.run_cycle(
            objectives=[_objective()],
            projects=[],
            taskforce_state={},
        )
        self.assertIsNotNone(svc._last_cycle)
        self.assertTrue(svc._last_cycle["orchestrator_cycle"])


if __name__ == "__main__":
    unittest.main()
