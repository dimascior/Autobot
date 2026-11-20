"""Execution-reach classification tests over production session shapes.

These tests use the same event structure as real production Codex sessions
to verify that the reach classifier produces the expected result for each
known session type. They are deterministic regression vectors — the
expected classifications under the current luna-local execution profile
are frozen here.

Historical production vectors (from PRODUCTION_PROOF.md):
    Pilot 3 argument-review:  connector activity  → INTEGRITY_FAILED
    Pilot 6 builder:          sub-agent activity   → INTEGRITY_FAILED
    Pilot 7 reviewer:         connector activity   → INTEGRITY_FAILED
    Pilot 8 builder:          machine-local only   → COMPLETE / PASS
    Pilot 8 reviewer:         machine-local only   → COMPLETE / PASS
"""

import json
import unittest

import support

from autobot.session_recognizer import (
    classify_reach,
    execution_reach_violations,
    recognize_session,
)
from autobot.receipts import (
    LUNA_LOCAL_AUTHORIZED_REACH,
    classify_execution_reach,
)
from autobot import Store


def _meta(session_id="sess-test", model="gpt-6-luna"):
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


def _file(path, ordinal=20):
    return json.dumps({
        "timestamp": "2026-11-18T18:38:00Z", "ordinal": ordinal,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "FileChange", "id": "fc-001",
            "file_path": path}}
    })


def _mcp(server, tool, ordinal=30):
    return json.dumps({
        "timestamp": "2026-11-18T18:39:00Z", "ordinal": ordinal,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "McpToolCall", "id": "mcp-001",
            "server_name": server, "tool_name": tool}}
    })


def _subagent(agent, ordinal=40):
    return json.dumps({
        "timestamp": "2026-11-18T18:39:30Z", "ordinal": ordinal,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": {
            "type": "CollabAgentToolCall", "id": "collab-001",
            "agent_name": agent}}
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


class Pilot8BuilderVector(unittest.TestCase):
    """Pilot 8 builder: machine-local governed execution only."""

    def test_reach_is_machine_local(self):
        data = _session(
            _meta("sess-p8-builder"),
            _file("/work/src/main.py", ordinal=10),
            _cmd("exec-001", ["rg", "-n", "foo"], ordinal=20),
            _cmd("exec-002", ["git", "add", "src/main.py"], ordinal=30),
            _cmd("exec-003", ["git", "commit", "-m", "fix"], ordinal=40),
            _file("/work/.gate.json", ordinal=50),
            _final(ordinal=60),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "MACHINE_LOCAL")
        violations = execution_reach_violations(
            r, LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertEqual(violations, [])
        self.assertTrue(r["terminal"])


class Pilot8ReviewerVector(unittest.TestCase):
    """Pilot 8 reviewer: machine-local governed execution only."""

    def test_reach_is_machine_local(self):
        data = _session(
            _meta("sess-p8-reviewer"),
            _cmd("exec-r01", ["rg", "-n", "pattern", "src/"], ordinal=10),
            _cmd("exec-r02", ["git", "log", "--oneline", "-5"], ordinal=20),
            _file("/work/review.md", ordinal=30),
            _final(ordinal=40),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "MACHINE_LOCAL")
        violations = execution_reach_violations(
            r, LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertEqual(violations, [])


class Pilot3ArgumentReviewVector(unittest.TestCase):
    """Pilot 3 argument-review: used provider connector (GitHub MCP)."""

    def test_connector_produces_integrity_violation(self):
        data = _session(
            _meta("sess-p3-argument-review"),
            _cmd("exec-a01", ["rg", "-n", "sandbox"], ordinal=10),
            _mcp("github-server", "get_file_contents", ordinal=20),
            _mcp("github-server", "get_file_contents", ordinal=30),
            _file("/work/review.md", ordinal=40),
            _final(ordinal=50),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "PROVIDER_CONNECTOR")
        violations = execution_reach_violations(
            r, LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0]["observed_reach"],
                         "PROVIDER_CONNECTOR")
        self.assertEqual(violations[0]["item_type"], "McpToolCall")


class Pilot6BuilderVector(unittest.TestCase):
    """Pilot 6 builder: spawned a sub-agent (F-TF-31)."""

    def test_subagent_produces_integrity_violation(self):
        data = _session(
            _meta("sess-p6-builder"),
            _cmd("exec-b01", ["rg", "-n", "def"], ordinal=10),
            _file("/work/src/module.py", ordinal=20),
            _subagent("review-sub-agent", ordinal=30),
            _cmd("exec-b02", ["git", "add", "."], ordinal=40),
            _final(ordinal=50),
        )
        r = recognize_session(data)
        self.assertIn("PROVIDER_SUBAGENT", r["reach_observed"])
        violations = execution_reach_violations(
            r, LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertTrue(len(violations) > 0)
        self.assertEqual(violations[0]["observed_reach"],
                         "PROVIDER_SUBAGENT")


class Pilot7ReviewerVector(unittest.TestCase):
    """Pilot 7 reviewer: read source through provider connector (F-TF-32)."""

    def test_connector_produces_integrity_violation(self):
        data = _session(
            _meta("sess-p7-reviewer"),
            _mcp("github-server", "get_file_contents", ordinal=10),
            _mcp("github-server", "get_file_contents", ordinal=20),
            _mcp("github-server", "get_file_contents", ordinal=30),
            _file("/work/review.md", ordinal=40),
            _final(ordinal=50),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "PROVIDER_CONNECTOR")
        violations = execution_reach_violations(
            r, LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertEqual(len(violations), 3)


class ResearchProfileVector(unittest.TestCase):
    """A research profile authorizes PROVIDER_CONNECTOR."""

    RESEARCH_AUTHORIZED = frozenset({"MACHINE_LOCAL", "PROVIDER_CONNECTOR"})

    def test_connector_allowed_under_research_profile(self):
        data = _session(
            _meta("sess-research"),
            _mcp("web-search", "search", ordinal=10),
            _mcp("web-search", "read_page", ordinal=20),
            _file("/work/research.md", ordinal=30),
            _final(ordinal=40),
        )
        r = recognize_session(data)
        violations = execution_reach_violations(
            r, self.RESEARCH_AUTHORIZED)
        self.assertEqual(violations, [])

    def test_subagent_still_blocked_under_research_profile(self):
        data = _session(
            _meta("sess-research-bad"),
            _mcp("web-search", "search", ordinal=10),
            _subagent("helper", ordinal=20),
            _final(ordinal=30),
        )
        r = recognize_session(data)
        violations = execution_reach_violations(
            r, self.RESEARCH_AUTHORIZED)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["observed_reach"],
                         "PROVIDER_SUBAGENT")


class ReceiptReachIntegrationTests(unittest.TestCase):
    """The receipt derivation's execution-reach integration."""

    def test_classify_returns_unavailable_without_obj13(self):
        import tempfile, os
        store = Store(tempfile.mkdtemp())
        entries = [{"witness_kind": "obj-11", "producer": "autoswarm",
                    "content_sha256": "sha256:" + "a" * 64}]
        result = classify_execution_reach(store, entries)
        self.assertFalse(result["available"])

    def test_classify_returns_available_with_clean_session(self):
        import tempfile
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-clean"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _final(ordinal=20),
        )
        address = store.put(data)
        entries = [{"witness_kind": "obj-13", "producer": "agent_clis",
                    "content_sha256": address}]
        result = classify_execution_reach(
            store, entries, authorized_reach=LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertTrue(result["available"])
        self.assertEqual(result["observed_reach"], "MACHINE_LOCAL")
        self.assertEqual(result["violations"], [])

    def test_classify_detects_connector_violation(self):
        import tempfile
        store = Store(tempfile.mkdtemp())
        data = _session(
            _meta("sess-connector"),
            _mcp("github", "read", ordinal=10),
            _final(ordinal=20),
        )
        address = store.put(data)
        entries = [{"witness_kind": "obj-13", "producer": "agent_clis",
                    "content_sha256": address}]
        result = classify_execution_reach(
            store, entries, authorized_reach=LUNA_LOCAL_AUTHORIZED_REACH)
        self.assertTrue(result["available"])
        self.assertEqual(result["observed_reach"], "PROVIDER_CONNECTOR")
        self.assertEqual(len(result["violations"]), 1)


if __name__ == "__main__":
    unittest.main()
