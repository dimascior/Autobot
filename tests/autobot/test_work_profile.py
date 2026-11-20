"""WorkProfile model tests: semantic classification of Autobot workstreams."""

import unittest

import support

from autobot.work_profile import (
    WORK_PROFILES,
    VALID_PROFILES,
    get_profile,
    default_execution_profile,
    default_finalization_class,
    validate_profile,
)
from autobot.execution_reach import EXECUTION_REACH_POLICIES


class ProfileRegistrationTests(unittest.TestCase):
    """Every registered profile has valid structure."""

    def test_three_profiles_registered(self):
        self.assertEqual(VALID_PROFILES,
                         frozenset({"ENGINEERING", "RESEARCH",
                                    "GENERAL_OPERATION"}))

    def test_all_profiles_have_required_fields(self):
        required = {"description", "default_finalization_class",
                    "default_execution_profile",
                    "default_activity_classes",
                    "supports_external_effect"}
        for name, profile in WORK_PROFILES.items():
            self.assertEqual(set(profile.keys()), required, name)

    def test_engineering_profile(self):
        p = get_profile("ENGINEERING")
        self.assertEqual(p["default_finalization_class"], "SOURCE_MODIFYING")
        self.assertEqual(p["default_execution_profile"], "luna-local/v1")
        self.assertTrue(p["supports_external_effect"])

    def test_research_profile(self):
        p = get_profile("RESEARCH")
        self.assertEqual(p["default_finalization_class"], "READ_ONLY")
        self.assertEqual(p["default_execution_profile"], "luna-research/v1")
        self.assertFalse(p["supports_external_effect"])

    def test_general_operation_profile(self):
        p = get_profile("GENERAL_OPERATION")
        self.assertEqual(p["default_finalization_class"], "SOURCE_MODIFYING")
        self.assertEqual(p["default_execution_profile"], "luna-resource/v1")
        self.assertTrue(p["supports_external_effect"])

    def test_unknown_profile_returns_none(self):
        self.assertIsNone(get_profile("UNKNOWN"))


class ProfileExecutionProfileBinding(unittest.TestCase):
    """Every WorkProfile's default ExecutionProfile is registered."""

    def test_all_default_profiles_registered(self):
        for name, profile in WORK_PROFILES.items():
            ep = profile["default_execution_profile"]
            self.assertIn(ep, EXECUTION_REACH_POLICIES,
                          "%s default profile %s not registered" % (name, ep))


class ProfileDefaultsTests(unittest.TestCase):
    """Default accessors work correctly."""

    def test_default_execution_profile(self):
        self.assertEqual(default_execution_profile("ENGINEERING"),
                         "luna-local/v1")
        self.assertEqual(default_execution_profile("RESEARCH"),
                         "luna-research/v1")
        self.assertEqual(default_execution_profile("GENERAL_OPERATION"),
                         "luna-resource/v1")
        self.assertIsNone(default_execution_profile("UNKNOWN"))

    def test_default_finalization_class(self):
        self.assertEqual(default_finalization_class("ENGINEERING"),
                         "SOURCE_MODIFYING")
        self.assertEqual(default_finalization_class("RESEARCH"),
                         "READ_ONLY")
        self.assertEqual(default_finalization_class("GENERAL_OPERATION"),
                         "SOURCE_MODIFYING")
        self.assertIsNone(default_finalization_class("UNKNOWN"))


class ProfileValidationTests(unittest.TestCase):
    """Validation rejects unknown profiles."""

    def test_valid_profiles_pass(self):
        for name in VALID_PROFILES:
            validate_profile(name)

    def test_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            validate_profile("UNKNOWN")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            validate_profile("")


if __name__ == "__main__":
    unittest.main()
