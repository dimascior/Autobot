"""The runtime's effects vocabulary is pinned to Chapter 0: effects,
outcome rules, the architectural retryable split and the attempt_scoped
intent-class checks must equal chapter0/registries/transitions.json
exactly."""

import unittest

import support
from autobot import effects


class EffectsRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = support.load_json(
            support.CH0_REGISTRIES / "transitions.json")

    def test_effects_match_registry(self):
        projected = {}
        for row in self.registry["effects"]:
            self.assertTrue(row["requires_intent"],
                            "%s must require an intent (INV-75)" % row["id"])
            projected[row["id"]] = {
                "type": row["type"],
                "outcome_rule": row["outcome_rule"],
                "receipt": row["receipt"],
                "intent_class": row["intent_class"],
                "standalone": row["standalone"],
                "canonical_on": row["canonical_on"],
            }
        self.assertEqual(projected, effects.EFFECTS)

    def test_outcome_rules_match_registry(self):
        registry_rules = {
            name: {k: v for k, v in rule.items() if k != "note"}
            for name, rule in self.registry["outcome_rules"].items()
        }
        self.assertEqual(registry_rules, effects.OUTCOME_RULES)

    def test_retryable_split_matches_registry(self):
        retry = self.registry["intent_classes"]["retry"]
        self.assertEqual(frozenset(retry["retryable"]), effects.RETRYABLE)
        self.assertEqual(frozenset(retry["not_retryable"]), effects.NOT_RETRYABLE)
        self.assertEqual(effects.RETRYABLE & effects.NOT_RETRYABLE, frozenset())
        self.assertEqual(effects.RETRYABLE | effects.NOT_RETRYABLE,
                         frozenset(effects.EFFECTS))

    def test_attempt_scoped_checks_match_registry(self):
        self.assertEqual(
            self.registry["intent_classes"]["classes"]["attempt_scoped"]["checks"],
            effects.ATTEMPT_SCOPED_CHECKS)


if __name__ == "__main__":
    unittest.main()
