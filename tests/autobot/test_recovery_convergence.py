"""Recovery convergence tests for execution-evidence receipts.

Verifies that repeated derivation over the same evidence produces
byte-identical receipts (the D-C3-2 convergence property), and that
recovery from various intermediate states converges to the same result.
"""

import json
import unittest
import tempfile

import support

from autobot import Store
from autobot.receipts import (
    LUNA_LOCAL_AUTHORIZED_REACH,
    build_receipt,
    classify_execution_reach,
    correlate_commands,
    derive_evidence,
    receipt_bytes,
)
from autobot.execution_reach import DEFAULT_LUNA_POLICY


def _meta(session_id="sess-conv"):
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


def _mcp(server, tool, ordinal=30):
    return json.dumps({
        "timestamp": "2026-11-18T18:39:00Z", "ordinal": ordinal,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": "mcp-001",
            "server_name": server, "tool_name": tool}}
    })


def _session(*lines):
    return "\n".join(lines).encode("utf-8")


def _lifecycle_witness(call_id, session_id):
    return json.dumps({
        "call_id": call_id,
        "native_session_id": session_id,
        "agent_id": "agent-1",
        "controller_id": "call_" + "a" * 32,
        "provider": "codex",
        "outcome": "success",
        "phase": "final",
    }).encode("utf-8")


def _governance_witness(session_id, tool_use_id, decision="ALLOW"):
    return json.dumps({
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "command_sha256": "sha256:" + "b" * 64,
        "decision": decision,
        "hook_event": "PostToolUse",
        "success": True,
    }).encode("utf-8")


def _make_state(store, session_data, call_id="call-1",
                session_id="sess-conv", governance_entries=None):
    """Build a minimal evidence state dict with witnesses in the store."""
    obj13_addr = store.put(session_data)
    obj11_data = _lifecycle_witness(call_id, session_id)
    obj11_addr = store.put(obj11_data)
    witnesses = [
        {"witness_kind": "obj-11", "producer": "autoswarm",
         "content_sha256": obj11_addr},
        {"witness_kind": "obj-13", "producer": "agent_clis",
         "content_sha256": obj13_addr},
    ]
    if governance_entries:
        for entry in governance_entries:
            addr = store.put(entry)
            witnesses.append({
                "witness_kind": "obj-12", "producer": "helios",
                "content_sha256": addr,
            })
    return {
        "record_payload": {
            "evidence_requirements": [
                "EXECUTION_LIFECYCLE", "COMMAND_GOVERNANCE",
                "SESSION_TRANSCRIPT"],
            "completion_policies": {
                "builder": {"required": True},
            },
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


class ConvergenceTests(unittest.TestCase):
    """Repeated derivation produces byte-identical receipts."""

    def test_identical_derivation(self):
        store = Store(tempfile.mkdtemp())
        session_data = _session(
            _meta("sess-conv"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-conv", "exec-001")
        state = _make_state(store, session_data, governance_entries=[gov])
        r1 = derive_evidence(store, state, 3600)
        r2 = derive_evidence(store, state, 3600)
        self.assertEqual(receipt_bytes(r1), receipt_bytes(r2))

    def test_convergence_after_restart(self):
        store = Store(tempfile.mkdtemp())
        session_data = _session(
            _meta("sess-conv"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-conv", "exec-001")
        state = _make_state(store, session_data, governance_entries=[gov])
        r1 = derive_evidence(store, state, 3600)
        store2 = Store(store.root)
        r2 = derive_evidence(store2, state, 3600)
        self.assertEqual(receipt_bytes(r1), receipt_bytes(r2))

    def test_convergence_with_reach_violation(self):
        store = Store(tempfile.mkdtemp())
        session_data = _session(
            _meta("sess-viol"),
            _mcp("github", "read", ordinal=10),
            _final(ordinal=20),
        )
        state = _make_state(store, session_data, session_id="sess-viol")
        r1 = derive_evidence(store, state, 3600)
        r2 = derive_evidence(store, state, 3600)
        self.assertEqual(receipt_bytes(r1), receipt_bytes(r2))
        self.assertEqual(r1["completeness"], "INTEGRITY_FAILED")


class IncompleteStateTests(unittest.TestCase):
    """Receipt classification for incomplete evidence states."""

    def test_no_obj13_produces_incomplete(self):
        store = Store(tempfile.mkdtemp())
        obj11_data = _lifecycle_witness("call-1", "sess-1")
        obj11_addr = store.put(obj11_data)
        state = {
            "record_payload": {
                "evidence_requirements": [
                    "EXECUTION_LIFECYCLE", "SESSION_TRANSCRIPT"],
                "completion_policies": {
                    "builder": {"required": True},
                },
                "specification_inputs": {},
            },
            "role": "builder",
            "attempt_id": "att-1",
            "attempt_contract_hash": "sha256:" + "c" * 64,
            "call_id": "call-1",
            "native_session_id": "sess-1",
            "witnesses": [
                {"witness_kind": "obj-11", "producer": "autoswarm",
                 "content_sha256": obj11_addr},
            ],
            "artifacts": [],
            "anchor_utc": "2026-11-18T18:37:00.000000Z",
        }
        receipt = derive_evidence(store, state, 3600)
        self.assertEqual(receipt["completeness"], "INCOMPLETE")


class CorrelationContradictionTests(unittest.TestCase):
    """Command-correlation mismatches produce contradictions."""

    def test_provider_only_command_is_contradiction(self):
        store = Store(tempfile.mkdtemp())
        session_data = _session(
            _meta("sess-corr"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _cmd("exec-002", ["ls"], ordinal=20),
            _final(ordinal=30),
        )
        gov = _governance_witness("sess-corr", "exec-001")
        state = _make_state(store, session_data, session_id="sess-corr",
                            governance_entries=[gov])
        receipt = derive_evidence(store, state, 3600)
        rules = [c["rule"] for c in receipt.get("contradictions", [])]
        self.assertIn("provider_command_missing_governance", rules)

    def test_full_correlation_no_contradiction(self):
        store = Store(tempfile.mkdtemp())
        session_data = _session(
            _meta("sess-ok"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-ok", "exec-001")
        state = _make_state(store, session_data, session_id="sess-ok",
                            governance_entries=[gov])
        receipt = derive_evidence(store, state, 3600)
        corr_rules = [c["rule"] for c in receipt.get("contradictions", [])
                      if c["rule"].startswith("provider_command")
                      or c["rule"].startswith("governance_missing")]
        self.assertEqual(corr_rules, [])


class PolicyInReceiptTests(unittest.TestCase):
    """The receipt carries the policy identity used for evaluation."""

    def test_receipt_contains_policy_id(self):
        store = Store(tempfile.mkdtemp())
        session_data = _session(
            _meta("sess-pol"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _final(ordinal=20),
        )
        gov = _governance_witness("sess-pol", "exec-001")
        state = _make_state(store, session_data, session_id="sess-pol",
                            governance_entries=[gov])
        receipt = derive_evidence(store, state, 3600)
        er = receipt.get("execution_reach", {})
        self.assertEqual(er.get("policy_id"), DEFAULT_LUNA_POLICY)

    def test_receipt_without_obj13_has_no_reach(self):
        store = Store(tempfile.mkdtemp())
        obj11_data = _lifecycle_witness("call-1", "sess-1")
        obj11_addr = store.put(obj11_data)
        state = {
            "record_payload": {
                "evidence_requirements": [
                    "EXECUTION_LIFECYCLE", "SESSION_TRANSCRIPT"],
                "completion_policies": {"builder": {"required": True}},
                "specification_inputs": {},
            },
            "role": "builder",
            "attempt_id": "att-1",
            "attempt_contract_hash": "sha256:" + "c" * 64,
            "call_id": "call-1",
            "native_session_id": "sess-1",
            "witnesses": [
                {"witness_kind": "obj-11", "producer": "autoswarm",
                 "content_sha256": obj11_addr},
            ],
            "artifacts": [],
            "anchor_utc": "2026-11-18T18:37:00.000000Z",
        }
        receipt = derive_evidence(store, state, 3600)
        self.assertNotIn("execution_reach", receipt)


if __name__ == "__main__":
    unittest.main()
