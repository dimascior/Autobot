"""Production profile activation proof: SESSION_TRANSCRIPT in evidence.

Verifies that SOURCE_MODIFYING_GOVERNED contracts include
SESSION_TRANSCRIPT in their evidence_requirements, and that the
receipt derivation correctly handles:
    - Missing obj-13 → INCOMPLETE
    - Terminal clean obj-13 → eligible for COMPLETE
    - Forbidden reach → INTEGRITY_FAILED

Phase D of the execution-evidence production proof.
"""

import json
import tempfile
import unittest

import support

from autobot import Store
from autobot.receipts import derive_evidence, receipt_bytes
from intelligence.production_seed import (
    EVIDENCE_PROFILES,
    EVIDENCE_BY_CLASS,
    BY_CLASS_PROFILE,
    production_defaults,
)


def _meta(session_id="sess-prof"):
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


def _mcp(server, tool, ordinal=30):
    return json.dumps({
        "timestamp": "2026-11-18T18:39:00Z", "ordinal": ordinal,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": "mcp-001",
            "server_name": server, "tool_name": tool}}
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


class ProductionProfileTests(unittest.TestCase):
    """SESSION_TRANSCRIPT is in the production evidence profile."""

    def test_source_modifying_governed_includes_session_transcript(self):
        reqs = EVIDENCE_PROFILES["SOURCE_MODIFYING_GOVERNED"]
        self.assertIn("SESSION_TRANSCRIPT", reqs)
        self.assertIn("COMMAND_GOVERNANCE", reqs)
        self.assertIn("EXECUTION_LIFECYCLE", reqs)

    def test_by_class_source_modifying_includes_session_transcript(self):
        reqs = EVIDENCE_BY_CLASS["SOURCE_MODIFYING"]
        self.assertIn("SESSION_TRANSCRIPT", reqs)

    def test_read_only_does_not_include_session_transcript(self):
        reqs = EVIDENCE_PROFILES["READ_ONLY_REPORT"]
        self.assertNotIn("SESSION_TRANSCRIPT", reqs)

    def test_production_defaults_by_class_sm_includes_st(self):
        defaults = production_defaults(evidence_profile=BY_CLASS_PROFILE)
        by_class = defaults.get("evidence_requirements_by_class", {})
        sm_reqs = by_class.get("SOURCE_MODIFYING", [])
        self.assertIn("SESSION_TRANSCRIPT", sm_reqs)


class ReceiptCompletenessWithSessionTranscript(unittest.TestCase):
    """Receipt classification when SESSION_TRANSCRIPT is required."""

    def _make_state(self, store, witnesses, session_id="sess-prof"):
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
            "call_id": "call-1",
            "native_session_id": session_id,
            "witnesses": witnesses,
            "artifacts": [],
            "anchor_utc": "2026-11-18T18:37:00.000000Z",
        }

    def test_missing_obj13_produces_incomplete(self):
        store = Store(tempfile.mkdtemp())
        obj11_addr = store.put(_lifecycle_witness("call-1", "sess-miss"))
        gov_addr = store.put(_governance_witness("sess-miss", "exec-001"))
        state = self._make_state(store, [
            {"witness_kind": "obj-11", "producer": "autoswarm",
             "content_sha256": obj11_addr},
            {"witness_kind": "obj-12", "producer": "helios",
             "content_sha256": gov_addr},
        ], session_id="sess-miss")
        receipt = derive_evidence(store, state, 3600)
        self.assertEqual(receipt["completeness"], "INCOMPLETE")

    def test_terminal_clean_obj13_eligible_for_complete(self):
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-clean"),
            _cmd("exec-001", ["echo"], ordinal=10),
            _final(ordinal=20),
        )
        obj13_addr = store.put(data)
        obj11_addr = store.put(_lifecycle_witness("call-1", "sess-clean"))
        gov_addr = store.put(_governance_witness("sess-clean", "exec-001"))
        state = self._make_state(store, [
            {"witness_kind": "obj-11", "producer": "autoswarm",
             "content_sha256": obj11_addr},
            {"witness_kind": "obj-12", "producer": "helios",
             "content_sha256": gov_addr},
            {"witness_kind": "obj-13", "producer": "agent_clis",
             "content_sha256": obj13_addr},
        ], session_id="sess-clean")
        from autobot.receipts import classify_execution_reach
        from autobot.execution_reach import authorized_reach, DEFAULT_LUNA_POLICY
        reach = classify_execution_reach(
            store, state["witnesses"],
            authorized_reach=authorized_reach(DEFAULT_LUNA_POLICY))
        self.assertTrue(reach.get("available"),
                        "obj-13 should be available; got: %s" % reach)
        receipt = derive_evidence(store, state, 3600)
        self.assertIn(receipt["completeness"], ("COMPLETE", "INCOMPLETE"))
        er = receipt.get("execution_reach", {})
        self.assertEqual(er.get("observed_reach"), "MACHINE_LOCAL")
        self.assertEqual(er.get("violations"), [])

    def test_forbidden_reach_produces_integrity_failed(self):
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-viol"),
            _mcp("github", "read", ordinal=10),
            _final(ordinal=20),
        )
        obj13_addr = store.put(data)
        obj11_addr = store.put(_lifecycle_witness("call-1", "sess-viol"))
        state = self._make_state(store, [
            {"witness_kind": "obj-11", "producer": "autoswarm",
             "content_sha256": obj11_addr},
            {"witness_kind": "obj-13", "producer": "agent_clis",
             "content_sha256": obj13_addr},
        ], session_id="sess-viol")
        receipt = derive_evidence(store, state, 3600)
        self.assertEqual(receipt["completeness"], "INTEGRITY_FAILED")
        er = receipt.get("execution_reach", {})
        self.assertEqual(er.get("observed_reach"), "PROVIDER_CONNECTOR")
        self.assertTrue(len(er.get("violations", [])) > 0)


if __name__ == "__main__":
    unittest.main()
