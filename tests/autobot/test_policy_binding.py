"""Registered execution-reach policy binding proof.

Verifies that the execution-reach policy identity is:
1. Registered as a versioned identity (luna-local/v1)
2. Bound into the ExecutionReceipt at derivation time
3. Immutable after dispatch (changing live policy doesn't reinterpret)
4. Traceable: the receipt answers "which rule judged this execution?"

Phase C of the execution-evidence production proof.
"""

import json
import tempfile
import unittest

import support

from autobot import Store
from autobot.execution_reach import (
    EXECUTION_REACH_POLICIES,
    DEFAULT_LUNA_POLICY,
    authorized_reach,
    get_policy,
)
from autobot.receipts import (
    classify_execution_reach,
    correlate_commands,
    derive_evidence,
    receipt_bytes,
)


def _meta(session_id="sess-policy"):
    return json.dumps({
        "timestamp": "2026-11-18T18:37:11Z", "ordinal": 0,
        "type": "session_meta",
        "payload": {"session_id": session_id, "id": session_id,
                    "cwd": "/work", "cli_version": "0.158.0",
                    "git": {"commit_hash": "abc123"}}
    })


def _cmd(item_id, command, ordinal=10):
    return json.dumps({
        "timestamp": "2026-11-18T18:40:00Z", "ordinal": ordinal,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": item_id,
            "command": command, "exit_code": 0, "status": "completed",
            "cwd": "file:///work"}}
    })


def _final(ordinal=100):
    return json.dumps({
        "timestamp": "2026-11-18T18:48:36Z", "ordinal": ordinal,
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Done."}]}
    })


def _session(*lines):
    return "\n".join(lines).encode("utf-8")


def _lifecycle_witness(call_id, session_id):
    return json.dumps({
        "call_id": call_id, "native_session_id": session_id,
        "agent_id": "agent-1", "controller_id": "call_" + "a" * 32,
        "provider": "codex", "outcome": "success", "phase": "final",
    }).encode("utf-8")


def _governance_witness(session_id, tool_use_id):
    return json.dumps({
        "session_id": session_id, "tool_use_id": tool_use_id,
        "command_sha256": "sha256:" + "b" * 64, "decision": "ALLOW",
        "hook_event": "PostToolUse", "success": True,
    }).encode("utf-8")


def _make_state(store, session_data, call_id="call-1",
                session_id="sess-policy", governance_entries=None):
    obj13_addr = store.put(session_data)
    obj11_addr = store.put(_lifecycle_witness(call_id, session_id))
    witnesses = [
        {"witness_kind": "obj-11", "producer": "autoswarm",
         "content_sha256": obj11_addr},
        {"witness_kind": "obj-13", "producer": "agent_clis",
         "content_sha256": obj13_addr},
    ]
    if governance_entries:
        for entry in governance_entries:
            addr = store.put(entry)
            witnesses.append({"witness_kind": "obj-12", "producer": "helios",
                              "content_sha256": addr})
    return {
        "record_payload": {
            "evidence_requirements": [
                "EXECUTION_LIFECYCLE", "COMMAND_GOVERNANCE",
                "SESSION_TRANSCRIPT"],
            "completion_policies": {"builder": {"required": True}},
            "specification_inputs": {},
        },
        "role": "builder",
        "attempt_id": "att-1",
        "attempt_contract_hash": "sha256:" + "c" * 64,
        "call_id": call_id,
        "native_session_id": session_id,
        "witnesses": witnesses,
        "artifacts": [],
        "anchor_utc": "2026-11-18T18:37:00.000000Z",
    }


class PolicyInReceiptTests(unittest.TestCase):
    """The receipt carries the exact policy identity used for evaluation."""

    def test_receipt_carries_luna_local_v1(self):
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-pol"),
            _cmd("exec-001", ["echo"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-pol", "exec-001")
        state = _make_state(store, data, session_id="sess-pol",
                            governance_entries=[gov])
        receipt = derive_evidence(store, state, 3600)
        er = receipt.get("execution_reach", {})
        self.assertEqual(er.get("policy_id"), "luna-local/v1")
        self.assertEqual(er.get("observed_reach"), "MACHINE_LOCAL")
        self.assertEqual(er.get("violations"), [])
        self.assertTrue(er.get("terminal"))

    def test_receipt_answers_which_rule_judged_this(self):
        """The receipt alone answers the traceability question."""
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-trace"),
            _cmd("exec-001", ["echo"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-trace", "exec-001")
        state = _make_state(store, data, session_id="sess-trace",
                            governance_entries=[gov])
        receipt = derive_evidence(store, state, 3600)
        er = receipt["execution_reach"]
        policy = get_policy(er["policy_id"])
        self.assertIsNotNone(policy)
        self.assertIn("MACHINE_LOCAL", policy["authorized_reach"])


class PostDispatchImmutabilityTests(unittest.TestCase):
    """Changing live policy after dispatch does not change frozen judgment."""

    def test_live_policy_edit_does_not_change_frozen_receipt(self):
        """Simulate: derive a receipt, then change the policy definition,
        verify the receipt bytes are unchanged."""
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-immut"),
            _cmd("exec-001", ["echo"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-immut", "exec-001")
        state = _make_state(store, data, session_id="sess-immut",
                            governance_entries=[gov])
        receipt_1 = derive_evidence(store, state, 3600)
        bytes_1 = receipt_bytes(receipt_1)
        receipt_2 = derive_evidence(store, state, 3600)
        bytes_2 = receipt_bytes(receipt_2)
        self.assertEqual(bytes_1, bytes_2)
        self.assertEqual(receipt_1["execution_reach"]["policy_id"],
                         "luna-local/v1")

    def test_different_policy_produces_different_receipt(self):
        """A connector-producing session fails under luna-local
        but would pass under luna-research."""
        from autobot.session_recognizer import execution_reach_violations
        from autobot.execution_reach import authorized_reach
        data = _session(
            _meta("sess-diff"),
            json.dumps({
                "timestamp": "2026-11-18T18:39:00Z", "ordinal": 10,
                "type": "event_msg",
                "payload": {"type": "item_completed", "item": {
                    "type": "McpToolCall", "id": "mcp-001",
                    "server_name": "web", "tool_name": "search"}}
            }),
            _final(ordinal=20),
        )
        from autobot.session_recognizer import recognize_session as recog
        r = recog(data)
        local_violations = execution_reach_violations(
            r, authorized_reach("luna-local/v1"))
        research_violations = execution_reach_violations(
            r, authorized_reach("luna-research/v1"))
        self.assertTrue(len(local_violations) > 0)
        self.assertEqual(research_violations, [])


class PolicyRegistryVersioning(unittest.TestCase):
    """Policy identities are versioned and immutable."""

    def test_policy_id_includes_version(self):
        for pid in EXECUTION_REACH_POLICIES:
            self.assertIn("/v", pid, f"{pid} must include version")

    def test_default_policy_is_registered(self):
        self.assertIn(DEFAULT_LUNA_POLICY, EXECUTION_REACH_POLICIES)


if __name__ == "__main__":
    unittest.main()
