"""Pin the runtime's principal and witness-kind tables to the frozen
Chapter 0 registries (the models.py/effects.py cross-check discipline):
``principals.py`` must equal principals.json id→kind, and ``witnesses.py``
must cover exactly the ``class: "witness"`` entries of objects.json with
their registered minters as the only permitted D-21 producers."""

import unittest

import support
from autobot.principals import PRINCIPALS
from autobot.witnesses import WITNESS_KINDS


class PrincipalsPinTests(unittest.TestCase):
    def test_principals_mirror_the_registry(self):
        registry = support.load_json(
            support.CH0_REGISTRIES / "principals.json")["principals"]
        expected = {entry["id"]: entry["kind"] for entry in registry}
        self.assertEqual(PRINCIPALS, expected)

    def test_witness_kinds_mirror_the_registry(self):
        registry = support.load_json(
            support.CH0_REGISTRIES / "objects.json")["objects"]
        witnesses = {entry["id"]: entry for entry in registry
                     if entry["class"] == "witness"}
        self.assertEqual(set(WITNESS_KINDS), set(witnesses))
        for object_id, pinned in WITNESS_KINDS.items():
            self.assertEqual(pinned["name"], witnesses[object_id]["name"])
            self.assertEqual(pinned["producer"],
                             witnesses[object_id]["minter"])

    def test_every_witness_producer_is_a_registered_principal(self):
        for pinned in WITNESS_KINDS.values():
            self.assertIn(pinned["producer"], PRINCIPALS)


if __name__ == "__main__":
    unittest.main()
