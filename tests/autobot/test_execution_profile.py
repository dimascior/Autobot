"""ExecutionProfile formalization tests.

Verifies the complete set of registered execution profiles and their
relationships to WorkProfiles, roles, and the routing architecture.
"""

import unittest

import support

from autobot.execution_reach import (
    EXECUTION_REACH_POLICIES,
    DEFAULT_LUNA_POLICY,
    authorized_reach,
    get_policy,
    policy_ids,
)
from autobot.work_profile import WORK_PROFILES, default_execution_profile
from autobot.roles import ROLES, default_execution_profile_for_role


class CompleteProfileRegistryTests(unittest.TestCase):
    """All six execution profiles are registered."""

    def test_six_profiles_registered(self):
        expected = {
            "luna-local/v1",
            "luna-research/v1",
            "luna-resource/v1",
            "opus-engineer/v1",
            "opus-orchestrator/v1",
            "opus-high-context/v1",
        }
        self.assertEqual(set(EXECUTION_REACH_POLICIES.keys()), expected)

    def test_every_profile_versioned(self):
        for pid in EXECUTION_REACH_POLICIES:
            self.assertIn("/v", pid)


class ReachHierarchyTests(unittest.TestCase):
    """Reach policies form a contained hierarchy."""

    def test_local_is_subset_of_research(self):
        local = authorized_reach("luna-local/v1")
        research = authorized_reach("luna-research/v1")
        self.assertTrue(local.issubset(research))

    def test_local_is_subset_of_resource(self):
        local = authorized_reach("luna-local/v1")
        resource = authorized_reach("luna-resource/v1")
        self.assertTrue(local.issubset(resource))

    def test_research_is_subset_of_engineer(self):
        research = authorized_reach("luna-research/v1")
        engineer = authorized_reach("opus-engineer/v1")
        self.assertTrue(research.issubset(engineer))

    def test_orchestrator_is_empty(self):
        self.assertEqual(authorized_reach("opus-orchestrator/v1"),
                         frozenset())

    def test_high_context_has_connector(self):
        hc = authorized_reach("opus-high-context/v1")
        self.assertIn("MACHINE_LOCAL", hc)
        self.assertIn("PROVIDER_CONNECTOR", hc)
        self.assertNotIn("PROVIDER_SUBAGENT", hc)


class WorkProfileExecutionProfileConsistency(unittest.TestCase):
    """WorkProfile defaults reference valid ExecutionProfiles."""

    def test_engineering_uses_local(self):
        self.assertEqual(default_execution_profile("ENGINEERING"),
                         "luna-local/v1")

    def test_research_uses_research(self):
        self.assertEqual(default_execution_profile("RESEARCH"),
                         "luna-research/v1")

    def test_operation_uses_resource(self):
        self.assertEqual(default_execution_profile("GENERAL_OPERATION"),
                         "luna-resource/v1")

    def test_all_work_profile_defaults_registered(self):
        for wp_name, wp in WORK_PROFILES.items():
            ep = wp["default_execution_profile"]
            self.assertIn(ep, EXECUTION_REACH_POLICIES,
                          "%s -> %s" % (wp_name, ep))


class RoleExecutionProfileConsistency(unittest.TestCase):
    """Role defaults reference valid ExecutionProfiles."""

    def test_orchestrator_uses_orchestrator_profile(self):
        self.assertEqual(
            default_execution_profile_for_role("ORCHESTRATOR"),
            "opus-orchestrator/v1")

    def test_engineer_uses_engineer_profile(self):
        self.assertEqual(
            default_execution_profile_for_role("ENGINEER"),
            "opus-engineer/v1")

    def test_specialist_uses_local_profile(self):
        self.assertEqual(
            default_execution_profile_for_role("SPECIALIST"),
            "luna-local/v1")

    def test_all_role_defaults_registered(self):
        for role_name, role in ROLES.items():
            ep = role["default_execution_profile"]
            self.assertIn(ep, EXECUTION_REACH_POLICIES,
                          "%s -> %s" % (role_name, ep))


class HighContextProfileTests(unittest.TestCase):
    """opus-high-context/v1 for escalation tasks."""

    def test_registered(self):
        self.assertIsNotNone(get_policy("opus-high-context/v1"))

    def test_anthropic_provider(self):
        p = get_policy("opus-high-context/v1")
        self.assertEqual(p["provider"], "anthropic")
        self.assertEqual(p["model_family"], "opus")

    def test_reach_between_local_and_engineer(self):
        hc = authorized_reach("opus-high-context/v1")
        local = authorized_reach("luna-local/v1")
        engineer = authorized_reach("opus-engineer/v1")
        self.assertTrue(local.issubset(hc))
        self.assertTrue(hc.issubset(engineer))


if __name__ == "__main__":
    unittest.main()
