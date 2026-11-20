"""Tests for execution-reach policy registration and command correlation."""

import json
import unittest

import support

from autobot.execution_reach import (
    EXECUTION_REACH_POLICIES,
    DEFAULT_LUNA_POLICY,
    authorized_reach,
    get_policy,
    policy_ids,
)
from autobot.receipts import (
    LUNA_LOCAL_AUTHORIZED_REACH,
    correlate_commands,
)


class PolicyRegistryTests(unittest.TestCase):
    """Every registered policy has the required shape."""

    def test_all_policies_have_required_fields(self):
        for pid, policy in EXECUTION_REACH_POLICIES.items():
            self.assertIn("description", policy, pid)
            self.assertIn("authorized_reach", policy, pid)
            self.assertIn("provider", policy, pid)
            self.assertIn("model_family", policy, pid)
            self.assertIsInstance(policy["authorized_reach"], frozenset, pid)

    def test_default_luna_policy_exists(self):
        policy = get_policy(DEFAULT_LUNA_POLICY)
        self.assertIsNotNone(policy)
        self.assertEqual(policy["authorized_reach"],
                         frozenset({"MACHINE_LOCAL"}))

    def test_luna_local_authorized_reach_matches_policy(self):
        self.assertEqual(LUNA_LOCAL_AUTHORIZED_REACH,
                         authorized_reach(DEFAULT_LUNA_POLICY))

    def test_research_policy_allows_connector(self):
        policy = get_policy("luna-research/v1")
        self.assertIsNotNone(policy)
        self.assertIn("PROVIDER_CONNECTOR", policy["authorized_reach"])
        self.assertNotIn("PROVIDER_SUBAGENT", policy["authorized_reach"])

    def test_opus_engineer_allows_all_external(self):
        policy = get_policy("opus-engineer/v1")
        self.assertIsNotNone(policy)
        self.assertIn("PROVIDER_CONNECTOR", policy["authorized_reach"])
        self.assertIn("PROVIDER_SUBAGENT", policy["authorized_reach"])

    def test_orchestrator_has_no_execution_reach(self):
        policy = get_policy("opus-orchestrator/v1")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["authorized_reach"], frozenset())

    def test_unknown_policy_returns_none(self):
        self.assertIsNone(get_policy("nonexistent/v1"))

    def test_unknown_policy_returns_empty_reach(self):
        self.assertEqual(authorized_reach("nonexistent/v1"), frozenset())

    def test_policy_ids_sorted(self):
        ids = policy_ids()
        self.assertEqual(ids, sorted(ids))
        self.assertIn(DEFAULT_LUNA_POLICY, ids)


class CommandCorrelationTests(unittest.TestCase):
    """Command-correlation joins obj-13 command IDs against obj-12 facts."""

    def test_full_match(self):
        reach = {"command_ids": ["exec-001", "exec-002"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "exec-002"},
        }
        result = correlate_commands(reach, facts)
        self.assertEqual(len(result["matched"]), 2)
        self.assertEqual(result["provider_only"], [])
        self.assertEqual(result["helios_only"], [])

    def test_provider_only(self):
        reach = {"command_ids": ["exec-001", "exec-002"]}
        facts = {"sha256:aaa": {"tool_use_id": "exec-001"}}
        result = correlate_commands(reach, facts)
        self.assertEqual(result["matched"], ["exec-001"])
        self.assertEqual(result["provider_only"], ["exec-002"])
        self.assertEqual(result["helios_only"], [])

    def test_helios_only(self):
        reach = {"command_ids": ["exec-001"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "exec-003"},
        }
        result = correlate_commands(reach, facts)
        self.assertEqual(result["matched"], ["exec-001"])
        self.assertEqual(result["provider_only"], [])
        self.assertEqual(result["helios_only"], ["exec-003"])

    def test_empty_inputs(self):
        result = correlate_commands({}, {})
        self.assertEqual(result["matched"], [])
        self.assertEqual(result["provider_only"], [])
        self.assertEqual(result["helios_only"], [])

    def test_no_governance_facts(self):
        reach = {"command_ids": ["exec-001"]}
        facts = {"sha256:aaa": {"outcome": "success"}}
        result = correlate_commands(reach, facts)
        self.assertEqual(result["provider_only"], ["exec-001"])
        self.assertEqual(result["helios_only"], [])


if __name__ == "__main__":
    unittest.main()
