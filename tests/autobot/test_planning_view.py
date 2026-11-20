"""PlanningView tests: structured state surface for reasoning roles."""

import unittest

import support

from autobot.planning_view import (
    build_orchestrator_view,
    build_engineer_view,
    enrich_view,
)


class OrchestratorViewTests(unittest.TestCase):

    def test_basic_view(self):
        view = build_orchestrator_view(
            objective={"objective_id": "obj-1", "text": "test"},
            projects=[{"project_id": "proj-1"}],
            taskforce_state={"members": 4},
        )
        self.assertEqual(view["role"], "ORCHESTRATOR")
        self.assertEqual(view["objective"]["objective_id"], "obj-1")
        self.assertEqual(len(view["projects"]), 1)

    def test_view_with_artifacts(self):
        view = build_orchestrator_view(
            objective={"objective_id": "obj-1"},
            projects=[],
            taskforce_state={},
            accepted_artifacts=[{"address": "sha256:abc"}],
        )
        self.assertEqual(len(view["accepted_artifacts"]), 1)

    def test_view_with_findings(self):
        view = build_orchestrator_view(
            objective={"objective_id": "obj-1"},
            projects=[],
            taskforce_state={},
            findings=[{"finding_id": "f-1"}],
        )
        self.assertEqual(len(view["findings"]), 1)

    def test_view_with_failures(self):
        view = build_orchestrator_view(
            objective={"objective_id": "obj-1"},
            projects=[],
            taskforce_state={},
            failed_attempts=[{"attempt_id": "att-1"}],
        )
        self.assertEqual(len(view["failed_attempts"]), 1)

    def test_defaults_are_empty_lists(self):
        view = build_orchestrator_view(
            objective={}, projects=[], taskforce_state={})
        self.assertEqual(view["accepted_artifacts"], [])
        self.assertEqual(view["findings"], [])
        self.assertEqual(view["failed_attempts"], [])
        self.assertEqual(view["effect_outcomes"], [])


class EngineerViewTests(unittest.TestCase):

    def test_basic_view(self):
        view = build_engineer_view(
            objective={"objective_id": "obj-1"},
            project={"project_id": "proj-1"},
            workstreams=[{"workstream": "ws-1"}],
        )
        self.assertEqual(view["role"], "ENGINEER")
        self.assertEqual(view["project"]["project_id"], "proj-1")

    def test_engineer_has_no_effect_outcomes(self):
        view = build_engineer_view(
            objective={}, project={}, workstreams=[])
        self.assertNotIn("effect_outcomes", view)


class EnrichmentTests(unittest.TestCase):

    def test_enrich_with_profiles(self):
        view = build_orchestrator_view(
            objective={}, projects=[], taskforce_state={})
        enriched = enrich_view(
            view,
            work_profiles={"ws-1": "ENGINEERING"},
            execution_profiles={"ws-1": "luna-local/v1"},
        )
        self.assertEqual(enriched["work_profiles"]["ws-1"], "ENGINEERING")
        self.assertEqual(enriched["execution_profiles"]["ws-1"],
                         "luna-local/v1")

    def test_enrich_preserves_original(self):
        view = build_orchestrator_view(
            objective={"id": "obj-1"}, projects=[], taskforce_state={})
        enriched = enrich_view(view, work_profiles={"ws": "RESEARCH"})
        self.assertEqual(enriched["objective"]["id"], "obj-1")
        self.assertNotIn("work_profiles", view)

    def test_enrich_with_dependencies(self):
        view = build_orchestrator_view(
            objective={}, projects=[], taskforce_state={})
        enriched = enrich_view(
            view, dependency_state={"ws-1": "SATISFIED"})
        self.assertEqual(enriched["dependency_state"]["ws-1"], "SATISFIED")


if __name__ == "__main__":
    unittest.main()
