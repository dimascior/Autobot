"""Tests for the obj-13 session-record recognizer."""

import json
import unittest

import support  # bootstraps src onto sys.path

from autobot.session_recognizer import (
    EXECUTION_RELEVANT_TYPES,
    EXTERNAL_REACH,
    REACH_CLASSIFICATION,
    classify_reach,
    execution_reach_violations,
    recognize_session,
)


def _session_meta(session_id="sess-001", model="gpt-6-luna",
                   cwd="/work", commit="abc123"):
    return json.dumps({
        "timestamp": "2026-11-18T18:37:11.153Z",
        "ordinal": 0,
        "type": "session_meta",
        "payload": {
            "session_id": session_id,
            "id": session_id,
            "cwd": cwd,
            "cli_version": "0.158.0",
            "base_instructions": {
                "provenance": {"type": "model", "model": model}
            },
            "git": {"commit_hash": commit},
        }
    })


def _command_execution(item_id, command, exit_code=0, ordinal=10):
    return json.dumps({
        "timestamp": "2026-11-18T18:40:09.152Z",
        "ordinal": ordinal,
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CommandExecution",
                "id": item_id,
                "command": command,
                "exit_code": exit_code,
                "status": "completed",
                "cwd": "file:///work",
            }
        }
    })


def _file_change(path, ordinal=20):
    return json.dumps({
        "timestamp": "2026-11-18T18:38:00.000Z",
        "ordinal": ordinal,
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "FileChange",
                "id": "fc-001",
                "file_path": path,
            }
        }
    })


def _mcp_tool_call(server, tool, ordinal=30):
    return json.dumps({
        "timestamp": "2026-11-18T18:39:00.000Z",
        "ordinal": ordinal,
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "McpToolCall",
                "id": "mcp-001",
                "server_name": server,
                "tool_name": tool,
            }
        }
    })


def _collab_agent_call(agent, ordinal=40):
    return json.dumps({
        "timestamp": "2026-11-18T18:39:30.000Z",
        "ordinal": ordinal,
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {
                "type": "CollabAgentToolCall",
                "id": "collab-001",
                "agent_name": agent,
            }
        }
    })


def _final_answer(ordinal=100):
    return json.dumps({
        "timestamp": "2026-11-18T18:45:00.000Z",
        "ordinal": ordinal,
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "Done."}],
        }
    })


def _build_session(*lines):
    return "\n".join(lines).encode("utf-8")


class SessionIdentityTests(unittest.TestCase):

    def test_session_meta_extracts_identity(self):
        data = _build_session(_session_meta("sid-42", "gpt-6-luna",
                                             "/repo", "deadbeef"))
        r = recognize_session(data)
        self.assertEqual(r["session_id"], "sid-42")
        self.assertEqual(r["model"], "gpt-6-luna")
        self.assertEqual(r["cwd"], "/repo")
        self.assertEqual(r["git_commit"], "deadbeef")
        self.assertEqual(r["cli_version"], "0.158.0")

    def test_empty_input_returns_none(self):
        self.assertIsNone(recognize_session(b""))

    def test_non_json_returns_none(self):
        self.assertIsNone(recognize_session(b"not json at all"))

    def test_missing_session_meta(self):
        data = _build_session(_command_execution("e1", ["echo", "hi"]))
        r = recognize_session(data)
        self.assertIsNone(r["session_id"])


class CommandExecutionTests(unittest.TestCase):

    def test_command_ids_collected(self):
        data = _build_session(
            _session_meta(),
            _command_execution("exec-aaa", ["git", "status"], ordinal=10),
            _command_execution("exec-bbb", ["rg", "-n", "foo"], ordinal=20),
        )
        r = recognize_session(data)
        self.assertEqual(r["command_ids"], ["exec-aaa", "exec-bbb"])
        self.assertEqual(len(r["execution_items"]), 2)
        self.assertIn("MACHINE_LOCAL", r["reach_observed"])

    def test_command_details_preserved(self):
        data = _build_session(
            _session_meta(),
            _command_execution("exec-c", ["python", "test.py"],
                                exit_code=1, ordinal=10),
        )
        r = recognize_session(data)
        item = r["execution_items"][0]
        self.assertEqual(item["item_type"], "CommandExecution")
        self.assertEqual(item["item_id"], "exec-c")
        self.assertEqual(item["command"], ["python", "test.py"])
        self.assertEqual(item["exit_code"], 1)


class FileChangeTests(unittest.TestCase):

    def test_file_change_recognized(self):
        data = _build_session(
            _session_meta(),
            _file_change("/repo/src/main.py"),
        )
        r = recognize_session(data)
        self.assertEqual(len(r["execution_items"]), 1)
        self.assertEqual(r["execution_items"][0]["item_type"], "FileChange")
        self.assertIn("MACHINE_LOCAL", r["reach_observed"])


class ExternalReachTests(unittest.TestCase):

    def test_mcp_tool_call_classified_as_connector(self):
        data = _build_session(
            _session_meta(),
            _mcp_tool_call("github-server", "get_file"),
        )
        r = recognize_session(data)
        self.assertIn("PROVIDER_CONNECTOR", r["reach_observed"])
        self.assertEqual(len(r["external_activity"]), 1)
        self.assertEqual(r["external_activity"][0]["server_name"],
                         "github-server")

    def test_collab_agent_classified_as_subagent(self):
        data = _build_session(
            _session_meta(),
            _collab_agent_call("sub-agent-1"),
        )
        r = recognize_session(data)
        self.assertIn("PROVIDER_SUBAGENT", r["reach_observed"])
        self.assertEqual(len(r["external_activity"]), 1)

    def test_mixed_external_reach(self):
        data = _build_session(
            _session_meta(),
            _mcp_tool_call("server", "tool"),
            _collab_agent_call("agent"),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "MIXED_EXTERNAL")


class ReachClassificationTests(unittest.TestCase):

    def test_machine_local_only(self):
        data = _build_session(
            _session_meta(),
            _command_execution("e1", ["git", "status"]),
            _file_change("/repo/file.py"),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "MACHINE_LOCAL")

    def test_connector_reach(self):
        data = _build_session(
            _session_meta(),
            _mcp_tool_call("gh", "read"),
        )
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "PROVIDER_CONNECTOR")

    def test_no_execution_items(self):
        data = _build_session(_session_meta())
        r = recognize_session(data)
        self.assertEqual(classify_reach(r), "NO_EXECUTION")

    def test_none_input(self):
        self.assertIsNone(classify_reach(None))


class ReachViolationTests(unittest.TestCase):

    def test_local_profile_allows_local(self):
        data = _build_session(
            _session_meta(),
            _command_execution("e1", ["git", "status"]),
            _file_change("/repo/file.py"),
        )
        r = recognize_session(data)
        violations = execution_reach_violations(r, {"MACHINE_LOCAL"})
        self.assertEqual(violations, [])

    def test_local_profile_blocks_connector(self):
        data = _build_session(
            _session_meta(),
            _command_execution("e1", ["git", "status"]),
            _mcp_tool_call("github-server", "get_file"),
        )
        r = recognize_session(data)
        violations = execution_reach_violations(r, {"MACHINE_LOCAL"})
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["item_type"], "McpToolCall")
        self.assertEqual(violations[0]["observed_reach"],
                         "PROVIDER_CONNECTOR")

    def test_local_profile_blocks_subagent(self):
        data = _build_session(
            _session_meta(),
            _collab_agent_call("agent-1"),
        )
        r = recognize_session(data)
        violations = execution_reach_violations(r, {"MACHINE_LOCAL"})
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["observed_reach"],
                         "PROVIDER_SUBAGENT")

    def test_research_profile_allows_connector(self):
        data = _build_session(
            _session_meta(),
            _mcp_tool_call("server", "search"),
        )
        r = recognize_session(data)
        violations = execution_reach_violations(
            r, {"MACHINE_LOCAL", "PROVIDER_CONNECTOR"})
        self.assertEqual(violations, [])


class TerminalStateTests(unittest.TestCase):

    def test_final_answer_is_terminal(self):
        data = _build_session(
            _session_meta(),
            _command_execution("e1", ["echo", "done"]),
            _final_answer(),
        )
        r = recognize_session(data)
        self.assertTrue(r["terminal"])

    def test_no_terminal_event(self):
        data = _build_session(
            _session_meta(),
            _command_execution("e1", ["echo", "hi"]),
        )
        r = recognize_session(data)
        self.assertFalse(r["terminal"])


class ItemCountTests(unittest.TestCase):

    def test_all_types_counted(self):
        data = _build_session(
            _session_meta(),
            _command_execution("e1", ["ls"], ordinal=10),
            _command_execution("e2", ["cat", "f"], ordinal=20),
            _file_change("/f", ordinal=30),
            _mcp_tool_call("s", "t", ordinal=40),
        )
        r = recognize_session(data)
        self.assertEqual(r["item_counts"]["session_meta"], 1)
        self.assertEqual(r["item_counts"]["event_msg"], 4)


class ClassificationRegistryTests(unittest.TestCase):

    def test_all_execution_types_have_classification(self):
        for t in EXECUTION_RELEVANT_TYPES:
            self.assertIn(t, REACH_CLASSIFICATION,
                          "%s has no reach classification" % t)

    def test_external_reach_classes_are_recognized(self):
        for cls in EXTERNAL_REACH:
            self.assertIn(cls,
                          set(REACH_CLASSIFICATION.values()),
                          "%s is not in any classification" % cls)


if __name__ == "__main__":
    unittest.main()
