"""ResourceScope model tests: typed resource authorization for assignments."""

import unittest

import support

from autobot.resource_scope import (
    RESOURCE_KINDS,
    ACTIVITY_CLASSES,
    build_scope,
    scope_for_engineering,
    scope_for_research,
    scope_for_operation,
    validate_scope,
    scope_contains,
)


class ScopeModelTests(unittest.TestCase):
    """Resource kinds and activity classes are well-defined."""

    def test_resource_kinds_defined(self):
        expected = {"REPOSITORY", "REPOSITORY_PATH", "FILESYSTEM_READ",
                    "ARTIFACT_WORKSPACE", "PERSISTENT_DEST", "WEB_SOURCE",
                    "SERVICE", "DATABASE", "EFFECT_TARGET"}
        self.assertEqual(RESOURCE_KINDS, expected)

    def test_activity_classes_defined(self):
        self.assertEqual(ACTIVITY_CLASSES,
                         frozenset({"OBSERVATION", "ARTIFACT_PRODUCTION",
                                    "EXTERNAL_EFFECT"}))


class BuildScopeTests(unittest.TestCase):
    """build_scope produces canonical sorted entries."""

    def test_empty_scope(self):
        self.assertEqual(build_scope([]), [])

    def test_single_entry(self):
        scope = build_scope([{
            "kind": "REPOSITORY",
            "identity": "/path/to/repo",
            "activity_class": "OBSERVATION",
        }])
        self.assertEqual(len(scope), 1)
        self.assertEqual(scope[0]["kind"], "REPOSITORY")
        self.assertEqual(scope[0]["identity"], "/path/to/repo")
        self.assertEqual(scope[0]["activity_class"], "OBSERVATION")
        self.assertEqual(scope[0]["constraints"], {})

    def test_entries_sorted_by_kind_identity_activity(self):
        scope = build_scope([
            {"kind": "WEB_SOURCE", "identity": "z",
             "activity_class": "OBSERVATION"},
            {"kind": "REPOSITORY", "identity": "a",
             "activity_class": "OBSERVATION"},
            {"kind": "REPOSITORY", "identity": "a",
             "activity_class": "ARTIFACT_PRODUCTION"},
        ])
        self.assertEqual(scope[0]["kind"], "REPOSITORY")
        self.assertEqual(scope[0]["activity_class"], "ARTIFACT_PRODUCTION")
        self.assertEqual(scope[1]["kind"], "REPOSITORY")
        self.assertEqual(scope[1]["activity_class"], "OBSERVATION")
        self.assertEqual(scope[2]["kind"], "WEB_SOURCE")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            build_scope([{"kind": "UNKNOWN", "identity": "x",
                          "activity_class": "OBSERVATION"}])

    def test_unknown_activity_raises(self):
        with self.assertRaises(ValueError):
            build_scope([{"kind": "REPOSITORY", "identity": "x",
                          "activity_class": "UNKNOWN"}])

    def test_constraints_preserved(self):
        scope = build_scope([{
            "kind": "REPOSITORY_PATH", "identity": "/repo",
            "activity_class": "OBSERVATION",
            "constraints": {"paths": ["src/"]},
        }])
        self.assertEqual(scope[0]["constraints"], {"paths": ["src/"]})


class EngineeringScopeTests(unittest.TestCase):
    """Default engineering scope."""

    def test_engineering_scope_structure(self):
        scope = scope_for_engineering("/repo", "/workspace")
        self.assertEqual(len(scope), 3)
        kinds = [e["kind"] for e in scope]
        self.assertIn("REPOSITORY", kinds)
        self.assertIn("ARTIFACT_WORKSPACE", kinds)

    def test_engineering_scope_activities(self):
        scope = scope_for_engineering("/repo", "/workspace")
        activities = {e["activity_class"] for e in scope}
        self.assertIn("OBSERVATION", activities)
        self.assertIn("ARTIFACT_PRODUCTION", activities)


class ResearchScopeTests(unittest.TestCase):
    """Default research scope."""

    def test_research_scope_base(self):
        scope = scope_for_research("/repo")
        self.assertEqual(len(scope), 1)
        self.assertEqual(scope[0]["kind"], "REPOSITORY")
        self.assertEqual(scope[0]["activity_class"], "OBSERVATION")

    def test_research_scope_with_sources(self):
        scope = scope_for_research("/repo",
                                   source_scopes=["docs.example.com"])
        self.assertEqual(len(scope), 2)
        web = [e for e in scope if e["kind"] == "WEB_SOURCE"]
        self.assertEqual(len(web), 1)
        self.assertEqual(web[0]["identity"], "docs.example.com")


class OperationScopeTests(unittest.TestCase):
    """Default operation scope."""

    def test_operation_scope_base(self):
        scope = scope_for_operation("/repo", "/workspace")
        self.assertEqual(len(scope), 2)

    def test_operation_with_destinations(self):
        scope = scope_for_operation("/repo", "/workspace",
                                    destinations=["/output"],
                                    read_roots=["/data"])
        kinds = [e["kind"] for e in scope]
        self.assertIn("PERSISTENT_DEST", kinds)
        self.assertIn("FILESYSTEM_READ", kinds)

    def test_operation_destination_is_effect(self):
        scope = scope_for_operation("/repo", "/workspace",
                                    destinations=["/out"])
        dest = [e for e in scope if e["kind"] == "PERSISTENT_DEST"]
        self.assertEqual(dest[0]["activity_class"], "EXTERNAL_EFFECT")


class ScopeValidationTests(unittest.TestCase):
    """Validation checks."""

    def test_valid_scope_passes(self):
        scope = build_scope([
            {"kind": "REPOSITORY", "identity": "/r",
             "activity_class": "OBSERVATION"},
        ])
        validate_scope(scope)

    def test_invalid_kind_raises(self):
        with self.assertRaises(ValueError):
            validate_scope([{"kind": "BAD", "identity": "/r",
                             "activity_class": "OBSERVATION"}])

    def test_non_list_raises(self):
        with self.assertRaises(ValueError):
            validate_scope("not a list")


class ScopeContainsTests(unittest.TestCase):
    """scope_contains membership check."""

    def test_present(self):
        scope = build_scope([
            {"kind": "REPOSITORY", "identity": "/repo",
             "activity_class": "OBSERVATION"},
        ])
        self.assertTrue(scope_contains(scope, "REPOSITORY", "/repo",
                                       "OBSERVATION"))

    def test_absent(self):
        scope = build_scope([
            {"kind": "REPOSITORY", "identity": "/repo",
             "activity_class": "OBSERVATION"},
        ])
        self.assertFalse(scope_contains(scope, "REPOSITORY", "/repo",
                                        "ARTIFACT_PRODUCTION"))

    def test_empty_scope(self):
        self.assertFalse(scope_contains([], "REPOSITORY", "/r",
                                        "OBSERVATION"))


if __name__ == "__main__":
    unittest.main()
