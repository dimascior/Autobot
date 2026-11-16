"""Tests for the production AutoSwarm Transport adapter.

Validates the Transport contract over durable AutoSwarm call records
without launching real provider sessions. Evidence is collected from
mock call directories that mirror AutoSwarm's record layout.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autobot.autoswarm_transport import AutoSwarmTransport, _read_json
from autobot.transport import Transport, reconcile_dispatched, normalize_outcome
from autobot.witnesses import WITNESS_KINDS


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)


class _MockStore:
    """A content-addressed store backed by a dict."""
    def __init__(self):
        self._data = {}

    def put(self, data):
        import hashlib
        address = "sha256:" + hashlib.sha256(data).hexdigest()
        self._data[address] = bytes(data)
        return address

    def get(self, address):
        return self._data[address]


class AutoSwarmTransportContractTests(unittest.TestCase):
    """The adapter satisfies the Transport interface."""

    def test_is_a_transport(self):
        store = _MockStore()
        t = AutoSwarmTransport(store, "/tmp/fake-home", "/tmp/ws")
        self.assertIsInstance(t, Transport)
        self.assertEqual(t.producer_id, WITNESS_KINDS["obj-11"]["producer"])


class FindSessionTests(unittest.TestCase):
    """find_session returns the session when observable, None otherwise."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_no_call_directory_returns_none(self):
        self.assertIsNone(self.transport.find_session("call_" + "a" * 32))

    def test_task_without_handle_returns_none(self):
        call_id = "call_" + "a" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        self.assertIsNone(self.transport.find_session(call_id))

    def test_running_session_returns_original_task_bytes(self):
        """obj-11 evidence is the original task.json bytes. The
        native_session_id is obtained from handle.json independently."""
        call_id = "call_" + "b" * 32
        task_data = {"call_id": call_id, "provider": "codex",
                     "prompt": "investigate logging",
                     "workspace": str(self.home),
                     "created_at": "2026-01-01T00:00:00Z"}
        _write_json(self._call_dir(call_id) / "task.json", task_data)
        _write_json(self._call_dir(call_id) / "handle.json",
                     {"pid": 12345, "native_session_id": "sess-001",
                      "started_at": "2026-01-01T00:00:01Z",
                      "phase": "execution"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-001")
        self.assertEqual(found["evidence"],
                         (self._call_dir(call_id) / "task.json").read_bytes())
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["call_id"], call_id)
        self.assertEqual(evidence["prompt"], "investigate logging")

    def test_returned_session_returns_original_task_bytes(self):
        """Even when result.json exists, obj-11 evidence is task.json
        (the record carrying received-prompt identity)."""
        call_id = "call_" + "c" * 32
        task_data = {"call_id": call_id, "provider": "codex",
                     "prompt": "refactor bridge",
                     "workspace": str(self.home),
                     "created_at": "2026-01-01T00:00:00Z"}
        _write_json(self._call_dir(call_id) / "task.json", task_data)
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-002"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-002")
        self.assertEqual(found["evidence"],
                         (self._call_dir(call_id) / "task.json").read_bytes())
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["prompt"], "refactor bridge")

    def test_handle_without_session_id_returns_none(self):
        call_id = "call_" + "d" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        _write_json(self._call_dir(call_id) / "handle.json",
                     {"pid": 12345, "phase": "startup"})
        self.assertIsNone(self.transport.find_session(call_id))


class PositiveLaunchFailureTests(unittest.TestCase):
    """positive_launch_failure proves failure only when the launch itself
    never established a provider session."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_no_result_returns_none(self):
        call_id = "call_" + "e" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_completed_result_returns_none(self):
        call_id = "call_" + "f" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "completed", "native_session_id": "s"})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_failed_with_session_returns_none(self):
        call_id = "call_" + "1" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": "s",
                      "error": "execution error",
                      "diagnostics": {"phase": "execution"}})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_startup_failure_returns_reason(self):
        call_id = "call_" + "2" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": None,
                      "error": "CLI not found",
                      "diagnostics": {"phase": "startup"}})
        reason = self.transport.positive_launch_failure(call_id)
        self.assertIsNotNone(reason)
        self.assertIn("CLI not found", reason)

    def test_handshake_failure_returns_reason(self):
        call_id = "call_" + "3" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": None,
                      "error": "auth required",
                      "diagnostics": {"phase": "handshake"}})
        self.assertEqual(self.transport.positive_launch_failure(call_id),
                         "auth required")

    def test_failed_without_session_returns_reason(self):
        call_id = "call_" + "4" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed",
                      "error": "runner crashed before handle"})
        reason = self.transport.positive_launch_failure(call_id)
        self.assertIn("runner crashed", reason)


class CollectTests(unittest.TestCase):
    """collect gathers producer-owned evidence from durable sources."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.gate_root = Path(self.tmpdir.name) / "gate"
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home),
            gate_root=str(self.gate_root))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_stdout_log_not_collected_as_transcript(self):
        """stdout.log is AutoSwarm's pipe capture, not a provider-native
        session transcript. obj-13 must not be emitted from it."""
        call_id = "call_" + "5" * 32
        d = self._call_dir(call_id)
        _write_json(d / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-x"})
        d.mkdir(parents=True, exist_ok=True)
        (d / "stdout.log").write_bytes(b"raw provider output\n")
        collected = self.transport.collect(call_id)
        self.assertEqual(collected["outcome"], "success")
        kinds = [wf[0] for wf in collected["witness_files"]]
        self.assertNotIn("obj-13", kinds)

    def test_collect_governance_individual_entries(self):
        """Each decision.json is a separate obj-12 witness entry with
        its original file bytes — never a combined JSON array."""
        session_id = "sess-gov-001"
        call_id = "call_" + "6" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id})
        evidence_dir = (self.gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        _write_json(evidence_dir / "toolu_001.decision.json",
                     {"verdict": "ALLOW", "tool": "Read"})
        _write_json(evidence_dir / "toolu_002.decision.json",
                     {"verdict": "DENY", "tool": "Bash"})
        collected = self.transport.collect(call_id)
        obj12_entries = [wf for wf in collected["witness_files"]
                         if wf[0] == "obj-12"]
        self.assertEqual(len(obj12_entries), 2)
        for kind, producer, data in obj12_entries:
            self.assertEqual(producer, "helios")
            record = json.loads(data)
            self.assertIsInstance(record, dict)
            self.assertIn("verdict", record)

    def test_no_governance_without_gate_root(self):
        transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))
        call_id = "call_" + "7" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-x"})
        collected = transport.collect(call_id)
        obj12_present = any(wf[0] == "obj-12"
                            for wf in collected["witness_files"])
        self.assertFalse(obj12_present)

    def test_collect_normalizes_outcome(self):
        call_id = "call_" + "7" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed"})
        self.assertEqual(self.transport.collect(call_id)["outcome"],
                         "success")

    def test_collect_without_result_returns_none(self):
        """No result.json means the session is not terminal — collect
        returns None so the Taskforce can safely skip it."""
        call_id = "call_" + "8" * 32
        self._call_dir(call_id).mkdir(parents=True)
        self.assertIsNone(self.transport.collect(call_id))

    def test_collect_always_includes_result_as_obj11(self):
        """result.json is always collected as the obj-11 lifecycle witness,
        even when no governance or transcript evidence exists."""
        call_id = "call_" + "0" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "failed",
                      "error": "auth"})
        collected = self.transport.collect(call_id)
        obj11 = [w for w in collected["witness_files"]
                 if w[0] == "obj-11"]
        self.assertEqual(len(obj11), 1)
        self.assertEqual(obj11[0][1], "autoswarm")
        import json
        witness = json.loads(obj11[0][2])
        self.assertEqual(witness["call_id"], call_id)
        self.assertEqual(collected["outcome"], "failed")


class LaunchProjectionTests(unittest.TestCase):
    """launch resolves the frozen projection and passes the prompt."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.store = _MockStore()
        self.workspace = Path(self.tmpdir.name) / "ws"
        self.workspace.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_launch_resolves_projection_and_calls_cli(self):
        projection = {
            "schema_version": "v1",
            "kind": "PromptProjection",
            "engineering_context": {
                "provenance": "chief-engineer-guidance",
                "author": "planner",
                "guidance": "Investigate the logging layer"
            }
        }
        data = json.dumps(projection, sort_keys=True).encode("utf-8")
        address = self.store.put(data)
        captured = {}

        def mock_run(argv, *, input=None, capture_output=False,
                     text=False, env=None):
            captured["argv"] = argv
            captured["input"] = input
            captured["env_home"] = env.get("AUTOSWARM_HOME") if env else None

            class R:
                returncode = 0
                stdout = json.dumps({
                    "ok": True,
                    "data": {"call_id": "call_" + "a" * 32,
                             "state": "running",
                             "native_session_id": "sess-test"}
                })
                stderr = ""
            return R()

        transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.workspace))
        with patch("autobot.autoswarm_transport.subprocess.run", mock_run):
            native = transport.launch(address, "codex", "builder",
                                      "call_" + "a" * 32)
        self.assertEqual(native, "sess-test")
        self.assertIn("--call-id", captured["argv"])
        self.assertEqual(captured["input"],
                         "Investigate the logging layer")
        self.assertEqual(captured["env_home"], str(self.home))
