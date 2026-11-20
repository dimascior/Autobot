"""Organizational role model tests."""

import unittest

import support

from autobot.roles import (
    ROLES,
    VALID_ROLES,
    get_role,
    default_execution_profile_for_role,
    validate_role,
)
from autobot.execution_reach import EXECUTION_REACH_POLICIES


class RoleRegistrationTests(unittest.TestCase):
    """Every registered role has valid structure."""

    def test_five_roles_registered(self):
        self.assertEqual(VALID_ROLES,
                         frozenset({"ORCHESTRATOR", "ENGINEER",
                                    "PROJECT_MANAGER", "SPECIALIST",
                                    "REVIEWER"}))

    def test_all_roles_have_required_fields(self):
        required = {"description", "default_model",
                    "default_execution_profile",
                    "can_reason", "can_execute"}
        for name, role in ROLES.items():
            self.assertEqual(set(role.keys()), required, name)

    def test_orchestrator_cannot_execute(self):
        r = get_role("ORCHESTRATOR")
        self.assertTrue(r["can_reason"])
        self.assertFalse(r["can_execute"])

    def test_engineer_can_both(self):
        r = get_role("ENGINEER")
        self.assertTrue(r["can_reason"])
        self.assertTrue(r["can_execute"])

    def test_specialist_executes_only(self):
        r = get_role("SPECIALIST")
        self.assertFalse(r["can_reason"])
        self.assertTrue(r["can_execute"])

    def test_reviewer_executes_only(self):
        r = get_role("REVIEWER")
        self.assertFalse(r["can_reason"])
        self.assertTrue(r["can_execute"])

    def test_pm_reasons_only(self):
        r = get_role("PROJECT_MANAGER")
        self.assertTrue(r["can_reason"])
        self.assertFalse(r["can_execute"])


class RoleExecutionProfileBinding(unittest.TestCase):
    """Every role's default ExecutionProfile is registered."""

    def test_all_role_profiles_registered(self):
        for name, role in ROLES.items():
            ep = role["default_execution_profile"]
            self.assertIn(ep, EXECUTION_REACH_POLICIES,
                          "%s default profile %s not registered" % (name, ep))


class RoleModelRoutingTests(unittest.TestCase):
    """Organizational routing expectations."""

    def test_orchestrator_on_opus(self):
        r = get_role("ORCHESTRATOR")
        self.assertIn("opus", r["default_model"])

    def test_engineer_on_opus(self):
        r = get_role("ENGINEER")
        self.assertIn("opus", r["default_model"])

    def test_specialist_on_luna(self):
        r = get_role("SPECIALIST")
        self.assertIn("luna", r["default_model"])

    def test_reviewer_on_luna(self):
        r = get_role("REVIEWER")
        self.assertIn("luna", r["default_model"])


class RoleDefaultsTests(unittest.TestCase):

    def test_default_execution_profile_for_role(self):
        self.assertEqual(default_execution_profile_for_role("ORCHESTRATOR"),
                         "opus-orchestrator/v1")
        self.assertEqual(default_execution_profile_for_role("ENGINEER"),
                         "opus-engineer/v1")
        self.assertEqual(default_execution_profile_for_role("SPECIALIST"),
                         "luna-local/v1")
        self.assertIsNone(default_execution_profile_for_role("UNKNOWN"))


class RoleValidationTests(unittest.TestCase):

    def test_valid_roles_pass(self):
        for name in VALID_ROLES:
            validate_role(name)

    def test_unknown_role_raises(self):
        with self.assertRaises(ValueError):
            validate_role("UNKNOWN")


if __name__ == "__main__":
    unittest.main()
