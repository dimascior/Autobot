"""The runtime's state vocabulary is pinned to Chapter 0: every machine the
schema stores (sequence, attempt, effect_intent, permit_reservation,
contract_admission) must equal chapter0/registries/states.json exactly —
ids, order, initial state and terminal set. The runtime cannot drift from
the frozen registries."""

import unittest

import support
from autobot import models


class StatesRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.machines = support.load_json(
            support.CH0_REGISTRIES / "states.json")["machines"]

    def _check(self, machine, states, initial, terminal):
        reg = self.machines[machine]
        self.assertEqual(tuple(s["id"] for s in reg["states"]), states)
        self.assertEqual(reg["initial"], initial)
        self.assertEqual(
            frozenset(s["id"] for s in reg["states"] if s["terminal"]),
            terminal,
        )

    def test_sequence_machine(self):
        self._check("sequence", models.SEQUENCE_STATES,
                    models.SEQUENCE_INITIAL, models.SEQUENCE_TERMINAL)

    def test_attempt_machine(self):
        self._check("attempt", models.ATTEMPT_STATES,
                    models.ATTEMPT_INITIAL, models.ATTEMPT_TERMINAL)

    def test_effect_intent_machine(self):
        self._check("effect_intent", models.EFFECT_INTENT_STATES,
                    models.EFFECT_INTENT_INITIAL, models.EFFECT_INTENT_TERMINAL)

    def test_permit_reservation_machine(self):
        self._check("permit_reservation", models.PERMIT_RESERVATION_STATES,
                    models.PERMIT_RESERVATION_INITIAL,
                    models.PERMIT_RESERVATION_TERMINAL)

    def test_contract_admission_machine(self):
        self._check("contract_admission", models.ADMISSION_STATES,
                    models.ADMISSION_INITIAL, models.ADMISSION_TERMINAL)


if __name__ == "__main__":
    unittest.main()
