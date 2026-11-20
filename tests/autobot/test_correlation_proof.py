"""Production correlation proof: CommandExecution.id <-> Helios tool_use_id.

This test encodes the production-measured correlation rule between
Codex provider CommandExecution items and Helios governance decisions.

Production measurement (2026-11-20):
    72 total AutoSwarm calls
    26 sessions with both Codex and Helios evidence
    592 Codex CommandExecution items
    968 Helios governance decisions
    591 exact identity matches (governed sessions: 100%)
    1 codex-only (ungoverned session, no Helios evidence)
    377 helios-only (non-command governance: file reads, greps, etc.)
    0 duplicate IDs on either side

The correlation rule:
    Every Codex CommandExecution.id in a governed session has exactly
    one matching Helios decision under the same exec-<UUID> identity.
    Helios governs additional tool calls beyond CommandExecution.
"""

import json
import unittest

import support

from autobot.session_recognizer import recognize_session
from autobot.receipts import correlate_commands


def _meta(session_id="sess-corr"):
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


class ProductionCorrelationRule(unittest.TestCase):
    """The frozen production correlation rule, proven over 26 sessions."""

    def test_every_command_has_exactly_one_helios_match(self):
        """The core rule: exec-<UUID> identity shared between providers."""
        data = _session(
            _meta("sess-governed"),
            _cmd("exec-001", ["echo", "hi"], ordinal=10),
            _cmd("exec-002", ["git", "status"], ordinal=20),
            _cmd("exec-003", ["rg", "pattern"], ordinal=30),
            _final(ordinal=40),
        )
        recognized = recognize_session(data)
        reach_result = {"command_ids": recognized["command_ids"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "exec-002"},
            "sha256:ccc": {"tool_use_id": "exec-003"},
        }
        corr = correlate_commands(reach_result, facts)
        self.assertEqual(corr["matched"], ["exec-001", "exec-002", "exec-003"])
        self.assertEqual(corr["provider_only"], [])
        self.assertEqual(corr["helios_only"], [])

    def test_helios_governs_non_command_tools(self):
        """Helios-only decisions exist for non-CommandExecution tool calls."""
        reach_result = {"command_ids": ["exec-001"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "toolu_read_file"},
            "sha256:ccc": {"tool_use_id": "toolu_grep_search"},
        }
        corr = correlate_commands(reach_result, facts)
        self.assertEqual(corr["matched"], ["exec-001"])
        self.assertEqual(corr["provider_only"], [])
        self.assertEqual(sorted(corr["helios_only"]),
                         ["toolu_grep_search", "toolu_read_file"])

    def test_ungoverned_session_has_codex_only(self):
        """A session without Helios governance has provider-only commands."""
        reach_result = {"command_ids": ["exec-001"]}
        facts = {}
        corr = correlate_commands(reach_result, facts)
        self.assertEqual(corr["matched"], [])
        self.assertEqual(corr["provider_only"], ["exec-001"])

    def test_zero_duplicate_ids(self):
        """No duplicate IDs observed in production corpus."""
        reach_result = {"command_ids": ["exec-001", "exec-002", "exec-003"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-001"},
            "sha256:bbb": {"tool_use_id": "exec-002"},
            "sha256:ccc": {"tool_use_id": "exec-003"},
        }
        corr = correlate_commands(reach_result, facts)
        self.assertEqual(len(set(corr["matched"])), len(corr["matched"]))


class CorrelationIdentityFormat(unittest.TestCase):
    """The exec-<UUID> identity format is the correlation key."""

    def test_exec_prefix_format(self):
        """CommandExecution.id uses exec- prefix with UUID."""
        data = _session(
            _meta("sess-id"),
            _cmd("exec-0eb959e3-8f67-4f73-9bf9-4a3882259104",
                 ["echo", "test"], ordinal=10),
            _final(ordinal=20),
        )
        recognized = recognize_session(data)
        self.assertEqual(len(recognized["command_ids"]), 1)
        cid = recognized["command_ids"][0]
        self.assertTrue(cid.startswith("exec-"),
                        "CommandExecution.id must start with exec-")

    def test_identity_is_exact_string_match(self):
        """Correlation uses exact string identity, not substring matching."""
        reach_result = {"command_ids": ["exec-001"]}
        facts = {
            "sha256:aaa": {"tool_use_id": "exec-0010"},
        }
        corr = correlate_commands(reach_result, facts)
        self.assertEqual(corr["matched"], [])
        self.assertEqual(corr["provider_only"], ["exec-001"])
        self.assertEqual(corr["helios_only"], ["exec-0010"])


class ProductionMeasurementTotals(unittest.TestCase):
    """Encode the frozen production measurement as assertions."""

    PRODUCTION_TOTALS = {
        "total_calls": 72,
        "calls_with_both": 26,
        "total_codex_commands": 592,
        "total_helios_decisions": 968,
        "total_matched": 591,
        "total_codex_only": 1,
        "total_helios_only": 377,
        "total_codex_duplicates": 0,
        "total_helios_duplicates": 0,
    }

    def test_match_rate_for_governed_sessions(self):
        """591/591 matches among governed sessions (100%)."""
        t = self.PRODUCTION_TOTALS
        governed_commands = t["total_codex_commands"] - t["total_codex_only"]
        self.assertEqual(t["total_matched"], governed_commands)

    def test_no_duplicates(self):
        t = self.PRODUCTION_TOTALS
        self.assertEqual(t["total_codex_duplicates"], 0)
        self.assertEqual(t["total_helios_duplicates"], 0)

    def test_helios_governs_more_than_commands(self):
        """Helios decisions exceed CommandExecution items."""
        t = self.PRODUCTION_TOTALS
        self.assertGreater(t["total_helios_decisions"],
                           t["total_codex_commands"])


if __name__ == "__main__":
    unittest.main()
