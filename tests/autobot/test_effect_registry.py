"""Effect registry tests."""

import unittest

import support

from autobot.effect_registry import (
    EFFECT_FAMILIES,
    IMPLEMENTED_FAMILIES,
    DEFERRED_FAMILIES,
    get_family,
    family_names,
    is_implemented,
)


class EffectFamilyRegistrationTests(unittest.TestCase):

    def test_six_families_registered(self):
        self.assertEqual(len(EFFECT_FAMILIES), 6)
        expected = {"GIT_CAS", "FILESYSTEM_PLACE", "WEB_MUTATION",
                    "MESSAGE_DELIVERY", "DATABASE_MUTATION",
                    "SERVICE_OPERATION"}
        self.assertEqual(set(EFFECT_FAMILIES.keys()), expected)

    def test_all_have_required_fields(self):
        required = {"description", "effect_id", "executor",
                    "receipt_kind", "reversible", "requires_permit",
                    "observation_rule", "production_status"}
        for name, fam in EFFECT_FAMILIES.items():
            self.assertEqual(set(fam.keys()), required, name)

    def test_git_cas_proven(self):
        fam = get_family("GIT_CAS")
        self.assertEqual(fam["production_status"], "PROVEN")
        self.assertEqual(fam["executor"], "git_sequencer")
        self.assertEqual(fam["receipt_kind"], "CommitReceipt")

    def test_filesystem_implemented(self):
        fam = get_family("FILESYSTEM_PLACE")
        self.assertEqual(fam["production_status"], "IMPLEMENTED")
        self.assertTrue(fam["reversible"])

    def test_deferred_families(self):
        for name in ("WEB_MUTATION", "MESSAGE_DELIVERY",
                     "DATABASE_MUTATION", "SERVICE_OPERATION"):
            fam = get_family(name)
            self.assertEqual(fam["production_status"], "DEFERRED", name)


class ImplementationStatusTests(unittest.TestCase):

    def test_implemented_families(self):
        self.assertEqual(IMPLEMENTED_FAMILIES,
                         frozenset({"GIT_CAS", "FILESYSTEM_PLACE"}))

    def test_deferred_families(self):
        self.assertEqual(len(DEFERRED_FAMILIES), 4)

    def test_is_implemented(self):
        self.assertTrue(is_implemented("GIT_CAS"))
        self.assertTrue(is_implemented("FILESYSTEM_PLACE"))
        self.assertFalse(is_implemented("WEB_MUTATION"))

    def test_family_names_sorted(self):
        names = family_names()
        self.assertEqual(names, sorted(names))

    def test_unknown_family(self):
        self.assertIsNone(get_family("UNKNOWN"))
        self.assertFalse(is_implemented("UNKNOWN"))


class EffectObservationRules(unittest.TestCase):

    def test_local_effects(self):
        for name in ("GIT_CAS", "FILESYSTEM_PLACE", "DATABASE_MUTATION"):
            fam = get_family(name)
            self.assertEqual(fam["observation_rule"], "local", name)

    def test_remote_effects(self):
        for name in ("WEB_MUTATION", "MESSAGE_DELIVERY",
                     "SERVICE_OPERATION"):
            fam = get_family(name)
            self.assertEqual(fam["observation_rule"], "remote", name)


if __name__ == "__main__":
    unittest.main()
