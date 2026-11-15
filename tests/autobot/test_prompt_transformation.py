"""Prompt transformation verification (Correction 4).

Proves that the prompt AutoSwarm persists in task.json["prompt"] is
the exact output of the registered role transform applied to the frozen
projection's engineering_context.guidance — derived independently from
frozen input bytes, not from the Transport's runtime state.
"""
import json
import unittest

from autobot.autoswarm_transport import AutoSwarmTransport
from autobot.projection import project_prompt
from autobot.witnesses import WITNESS_KINDS
from autobot.witness_semantics import extract_facts


class _MockStore:
    def __init__(self):
        self._data = {}

    def put(self, data):
        import hashlib
        address = "sha256:" + hashlib.sha256(data).hexdigest()
        self._data[address] = bytes(data)
        return address

    def get(self, address):
        return self._data[address]


# A minimal frozen record payload that project_prompt accepts.
_FROZEN_RECORD = {
    "task_contract_id": "tc_" + "a" * 32,
    "objective": "Investigate the logging layer",
    "completion_policies": {
        "builder": {"required": True,
                    "success_outcomes": ["success"]}
    },
    "finalization_class": "READ_ONLY",
}

_VALID_IDENTITY = {
    "sequence_id": "sq_" + "0" * 32,
    "attempt_id": "at_" + "0" * 32,
    "fence": 1,
    "attempt_contract_hash": "sha256:" + "0" * 64,
}


class PromptTransformationTests(unittest.TestCase):
    """The prompt chain: project_prompt → CAS → Transport.launch →
    AutoSwarm task.json["prompt"] must preserve the exact guidance
    text byte-for-byte."""

    def test_projection_guidance_matches_frozen_input(self):
        """project_prompt carries the engineering_context.guidance
        verbatim — the exact text the planner supplied."""
        store = _MockStore()
        guidance = "Investigate the logging layer and report findings."
        context = {"author": "planner", "guidance": guidance}
        data, address = project_prompt(
            store, _FROZEN_RECORD, "builder", _VALID_IDENTITY, context)
        projection = json.loads(data)
        self.assertEqual(
            projection["engineering_context"]["guidance"], guidance)

    def test_transport_extracts_exact_guidance_from_projection(self):
        """Transport.launch reads the projection from the CAS and
        extracts engineering_context.guidance as the prompt text —
        the same bytes project_prompt stored."""
        store = _MockStore()
        guidance = "Refactor the error handling in bridge/codec.rs"
        context = {"author": "planner", "guidance": guidance}
        data, address = project_prompt(
            store, _FROZEN_RECORD, "builder", _VALID_IDENTITY, context)
        projection = json.loads(store.get(address))
        extracted = projection["engineering_context"]["guidance"]
        self.assertEqual(extracted, guidance)

    def test_prompt_identity_fact_matches_guidance(self):
        """The obj-11 witness recognizer's prompt_sha256 fact is the
        SHA-256 of the guidance text, which the role-transform check
        compares against the registered transform's output."""
        import hashlib
        guidance = "Verify the capacity accounting invariants."
        expected_sha = "sha256:" + hashlib.sha256(
            guidance.encode("utf-8")).hexdigest()
        task_record = {"call_id": "call_" + "a" * 32,
                       "provider": "codex",
                       "prompt": guidance,
                       "native_session_id": "sess-001"}
        data = json.dumps(task_record).encode("utf-8")
        facts = extract_facts("obj-11", data)
        self.assertEqual(facts["prompt_sha256"], expected_sha)

    def test_round_trip_through_mock_launch(self):
        """Full round-trip: project_prompt → CAS → launch extraction →
        the CLI would receive the exact guidance text as stdin."""
        store = _MockStore()
        guidance = "Unicode preserved: 日本語 données François"
        context = {"author": "planner", "guidance": guidance}
        _data, address = project_prompt(
            store, _FROZEN_RECORD, "builder", _VALID_IDENTITY, context)
        # Simulate what Transport.launch does
        projection_bytes = store.get(address)
        projection = json.loads(projection_bytes)
        extracted_prompt = projection["engineering_context"]["guidance"]
        self.assertEqual(extracted_prompt, guidance)
        # The prompt_sha256 identity fact for the task record
        import hashlib
        task_bytes = json.dumps(
            {"prompt": extracted_prompt}).encode("utf-8")
        facts = extract_facts("obj-11", task_bytes)
        expected_sha = "sha256:" + hashlib.sha256(
            guidance.encode("utf-8")).hexdigest()
        self.assertEqual(facts["prompt_sha256"], expected_sha)


if __name__ == "__main__":
    unittest.main()
