"""Tests for the workstream-to-proposal bridge."""
import unittest

from intelligence.workstream_bridge import (
    workstream_to_proposal,
    _infer_capability,
    _build_roles,
)


class TestWorkstreamToProposal(unittest.TestCase):

    def test_research_workstream(self):
        ws = {
            "workstream_id": "ws_test",
            "workstream_key": "literature-review",
            "work_profile": "RESEARCH",
            "execution_profile": "luna-research/v1",
            "organizational_role": "SPECIALIST",
            "resource_scope": [
                {"kind": "WEB_SOURCE", "identity": "public-web",
                 "activity_class": "OBSERVATION", "constraints": {}},
            ],
        }
        p = workstream_to_proposal(ws, "Research actuators", "pj_test")
        self.assertEqual(p["class"], "INVESTIGATE")
        self.assertEqual(p["requirement"]["work_profile"], "RESEARCH")
        self.assertEqual(p["requirement"]["execution_profile"],
                         "luna-research/v1")
        self.assertIn("resource_scope", p["requirement"])
        self.assertEqual(p["requirement"]["capability"], "research")

    def test_engineering_source_modifying(self):
        ws = {
            "workstream_key": "implementation",
            "work_profile": "ENGINEERING",
            "organizational_role": "builder",
        }
        p = workstream_to_proposal(ws, "Build the feature", "pj_eng")
        self.assertEqual(p["class"], "CREATE_TASK")
        self.assertEqual(p["requirement"]["work_profile"], "ENGINEERING")
        self.assertEqual(p["requirement"]["execution_profile"],
                         "luna-local/v1")

    def test_general_operation(self):
        ws = {
            "workstream_key": "report-gen",
            "work_profile": "GENERAL_OPERATION",
            "organizational_role": "builder",
            "resource_scope": [
                {"kind": "PERSISTENT_DEST", "identity": "/output",
                 "activity_class": "EXTERNAL_EFFECT", "constraints": {}},
            ],
        }
        p = workstream_to_proposal(ws, "Generate report", "pj_ops")
        self.assertEqual(p["class"], "CREATE_TASK")
        self.assertEqual(p["requirement"]["execution_profile"],
                         "luna-resource/v1")
        self.assertIn("resource_scope", p["requirement"])

    def test_review_workstream(self):
        ws = {
            "workstream_key": "review",
            "work_profile": "RESEARCH",
            "organizational_role": "reviewer",
        }
        predecessors = [{"edge_id": "review-of", "edge_kind": "RECORD",
                         "target": "sha256:" + "a" * 64,
                         "observation_requirement": "ACCEPTANCE"}]
        p = workstream_to_proposal(ws, "Review research", "pj_test",
                                   predecessors=predecessors)
        self.assertEqual(p["class"], "REQUEST_REVIEW")
        self.assertEqual(p["requirement"]["capability"], "verification")

    def test_no_work_profile_defaults(self):
        ws = {"workstream_key": "basic"}
        p = workstream_to_proposal(ws, "Do work", "pj_test")
        self.assertNotIn("work_profile", p["requirement"])
        self.assertEqual(p["class"], "INVESTIGATE")

    def test_predecessors_carried(self):
        ws = {
            "workstream_key": "dependent",
            "work_profile": "ENGINEERING",
        }
        preds = [{"edge_id": "on-research", "edge_kind": "RECORD",
                  "target": "sha256:" + "b" * 64,
                  "observation_requirement": "ACCEPTANCE"}]
        p = workstream_to_proposal(ws, "Use research", "pj_test",
                                   predecessors=preds)
        self.assertEqual(len(p["requirement"]["predecessors"]), 1)


class TestCapabilityInference(unittest.TestCase):

    def test_research_profile(self):
        self.assertEqual(_infer_capability("RESEARCH", "SPECIALIST"),
                         "research")

    def test_operations_profile(self):
        self.assertEqual(_infer_capability("GENERAL_OPERATION", "SPECIALIST"),
                         "operations")

    def test_reviewer_role(self):
        self.assertEqual(_infer_capability("ENGINEERING", "reviewer"),
                         "verification")

    def test_builder_role(self):
        self.assertEqual(_infer_capability(None, "builder"),
                         "documentation")


class TestBuildRoles(unittest.TestCase):

    def test_specialist_research(self):
        roles = _build_roles("SPECIALIST", "research", None)
        self.assertIn("investigator", roles)
        self.assertTrue(roles["investigator"]["required"])

    def test_reviewer(self):
        roles = _build_roles("reviewer", "verification", None)
        self.assertIn("reviewer", roles)

    def test_custom_completion(self):
        cp = {"success_outcomes": ["success", "partial"]}
        roles = _build_roles("builder", "documentation", cp)
        self.assertEqual(roles["builder"]["success_outcomes"],
                         ["success", "partial"])


if __name__ == "__main__":
    unittest.main()
