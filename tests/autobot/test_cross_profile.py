"""Cross-profile integration tests.

Verifies that WorkProfile, ResourceScope, ExecutionProfile, and Role
form a consistent organizational model where every combination that
the system routes is valid.
"""

import unittest

import support

from autobot.work_profile import (
    WORK_PROFILES, VALID_PROFILES, get_profile, validate_profile,
)
from autobot.resource_scope import (
    RESOURCE_KINDS, ACTIVITY_CLASSES,
    build_scope, scope_for_engineering, scope_for_research,
    scope_for_operation, scope_contains,
)
from autobot.execution_reach import (
    EXECUTION_REACH_POLICIES, authorized_reach,
)
from autobot.roles import ROLES, VALID_ROLES


class ModelDimensionOrthogonality(unittest.TestCase):
    """The five organizational dimensions are distinct."""

    def test_work_profiles_distinct_from_roles(self):
        self.assertTrue(VALID_PROFILES.isdisjoint(VALID_ROLES))

    def test_execution_profiles_are_reach_policies(self):
        for pid in EXECUTION_REACH_POLICIES:
            self.assertIn("/v", pid)

    def test_resource_kinds_distinct_from_activity_classes(self):
        self.assertTrue(RESOURCE_KINDS.isdisjoint(ACTIVITY_CLASSES))


class EngineeringRouteTests(unittest.TestCase):
    """The engineering route: Luna specialist on luna-local/v1."""

    def test_engineering_route_valid(self):
        wp = get_profile("ENGINEERING")
        ep = wp["default_execution_profile"]
        reach = authorized_reach(ep)
        scope = scope_for_engineering("/repo", "/workspace")
        self.assertEqual(ep, "luna-local/v1")
        self.assertIn("MACHINE_LOCAL", reach)
        self.assertNotIn("PROVIDER_CONNECTOR", reach)
        self.assertTrue(scope_contains(scope, "REPOSITORY", "/repo",
                                       "OBSERVATION"))

    def test_engineering_scope_excludes_web(self):
        scope = scope_for_engineering("/repo", "/workspace")
        web = [e for e in scope if e["kind"] == "WEB_SOURCE"]
        self.assertEqual(web, [])


class ResearchRouteTests(unittest.TestCase):
    """The research route: Luna specialist on luna-research/v1."""

    def test_research_route_valid(self):
        wp = get_profile("RESEARCH")
        ep = wp["default_execution_profile"]
        reach = authorized_reach(ep)
        self.assertEqual(ep, "luna-research/v1")
        self.assertIn("MACHINE_LOCAL", reach)
        self.assertIn("PROVIDER_CONNECTOR", reach)
        self.assertNotIn("PROVIDER_SUBAGENT", reach)

    def test_research_scope_allows_web(self):
        scope = scope_for_research("/repo",
                                   source_scopes=["docs.example.com"])
        web = [e for e in scope if e["kind"] == "WEB_SOURCE"]
        self.assertEqual(len(web), 1)

    def test_research_no_external_effect(self):
        wp = get_profile("RESEARCH")
        self.assertFalse(wp["supports_external_effect"])


class OperationRouteTests(unittest.TestCase):
    """The operation route: Luna on luna-resource/v1."""

    def test_operation_route_valid(self):
        wp = get_profile("GENERAL_OPERATION")
        ep = wp["default_execution_profile"]
        reach = authorized_reach(ep)
        self.assertEqual(ep, "luna-resource/v1")
        self.assertIn("MACHINE_LOCAL", reach)
        self.assertIn("PROVIDER_CONNECTOR", reach)

    def test_operation_supports_effect(self):
        wp = get_profile("GENERAL_OPERATION")
        self.assertTrue(wp["supports_external_effect"])

    def test_operation_scope_with_destinations(self):
        scope = scope_for_operation("/repo", "/ws",
                                    destinations=["/out"])
        dest = [e for e in scope
                if e["kind"] == "PERSISTENT_DEST"]
        self.assertEqual(len(dest), 1)
        self.assertEqual(dest[0]["activity_class"], "EXTERNAL_EFFECT")


class SameSpecialistCrossProfileTests(unittest.TestCase):
    """A specialist may work across profiles with different reach."""

    def test_local_policy_blocks_connector(self):
        local_reach = authorized_reach("luna-local/v1")
        self.assertNotIn("PROVIDER_CONNECTOR", local_reach)

    def test_research_policy_allows_connector(self):
        research_reach = authorized_reach("luna-research/v1")
        self.assertIn("PROVIDER_CONNECTOR", research_reach)

    def test_same_specialist_different_reach(self):
        local_reach = authorized_reach("luna-local/v1")
        research_reach = authorized_reach("luna-research/v1")
        self.assertNotEqual(local_reach, research_reach)
        self.assertTrue(local_reach.issubset(research_reach))


class MixedProjectGraphTests(unittest.TestCase):
    """A mixed project uses multiple work profiles."""

    def test_all_three_profiles_coexist(self):
        profiles = ["ENGINEERING", "RESEARCH", "GENERAL_OPERATION"]
        for name in profiles:
            validate_profile(name)
            wp = get_profile(name)
            self.assertIsNotNone(wp)
            ep = wp["default_execution_profile"]
            self.assertIn(ep, EXECUTION_REACH_POLICIES)

    def test_research_to_engineering_dependency(self):
        """Research produces observations; Engineering consumes them."""
        research_wp = get_profile("RESEARCH")
        eng_wp = get_profile("ENGINEERING")
        self.assertIn("ARTIFACT_PRODUCTION",
                      research_wp["default_activity_classes"])
        self.assertIn("OBSERVATION",
                      eng_wp["default_activity_classes"])

    def test_engineering_to_operation_dependency(self):
        """Engineering produces artifacts; Operations place them."""
        eng_wp = get_profile("ENGINEERING")
        op_wp = get_profile("GENERAL_OPERATION")
        self.assertIn("ARTIFACT_PRODUCTION",
                      eng_wp["default_activity_classes"])
        self.assertIn("EXTERNAL_EFFECT",
                      op_wp["default_activity_classes"])


if __name__ == "__main__":
    unittest.main()
