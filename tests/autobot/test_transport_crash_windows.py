"""Crash-window and provenance tests for the AutoSwarm Transport adapter.

Exercises the six crash boundaries from the integration hardening gate
and validates the evidence provenance chain. These tests use mock call
directories — no real AutoSwarm processes are launched.
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
    def __init__(self):
        self._data = {}

    def put(self, data):
        import hashlib
        address = "sha256:" + hashlib.sha256(data).hexdigest()
        self._data[address] = bytes(data)
        return address

    def get(self, address):
        return self._data[address]


class CrashWindowTests(unittest.TestCase):
    """Exercise the six crash boundaries for call identity ownership."""

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

    # --- Boundary 1: Before directory claim ---

    def test_b1_no_directory_find_returns_none(self):
        """Before any claim, find_session sees nothing."""
        call_id = "call_" + "a" * 32
        self.assertIsNone(self.transport.find_session(call_id))
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    # --- Boundary 2: After directory claim, before task.json ---

    def test_b2_directory_without_task_find_returns_none(self):
        """A bare directory (stale claim) is unobservable."""
        call_id = "call_" + "b" * 32
        self._call_dir(call_id).mkdir(parents=True)
        self.assertIsNone(self.transport.find_session(call_id))

    def test_b2_directory_without_task_failure_returns_none(self):
        """No result.json means no positive failure proof."""
        call_id = "call_" + "b" * 32
        self._call_dir(call_id).mkdir(parents=True)
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    # --- Boundary 3: After task.json, before runner spawn ---

    def test_b3_task_without_handle_is_unobservable(self):
        """task.json exists but no handle.json: session not yet observable."""
        call_id = "call_" + "c" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        self.assertIsNone(self.transport.find_session(call_id))
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    # --- Boundary 4: After process spawn, before handle.json ---

    def test_b4_spawned_without_handle_is_unobservable(self):
        """Process spawned but handle not yet written: same as B3 from
        the adapter's perspective (it reads files, not PIDs)."""
        call_id = "call_" + "d" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        (d / "runner.log").write_bytes(b"runner started\n")
        self.assertIsNone(self.transport.find_session(call_id))

    # --- Boundary 5: After handle.json, before result.json ---

    def test_b5_handle_with_session_is_observable(self):
        """Handle published with native_session_id: session is observable."""
        call_id = "call_" + "e" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home),
                      "created_at": "2026-01-01T00:00:00Z"})
        _write_json(d / "handle.json",
                     {"pid": 999, "native_session_id": "sess-live",
                      "started_at": "2026-01-01T00:00:01Z",
                      "phase": "execution"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-live")
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_b5_handle_without_session_id_is_unobservable(self):
        """Handle at startup phase (no session id yet): still unobservable."""
        call_id = "call_" + "f" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        _write_json(d / "handle.json",
                     {"pid": 999, "phase": "startup"})
        self.assertIsNone(self.transport.find_session(call_id))
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    # --- Boundary 6: result.json published ---

    def test_b6_completed_result_is_observable_and_not_failure(self):
        call_id = "call_" + "1" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home),
                      "created_at": "2026-01-01T00:00:00Z"})
        _write_json(d / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-done",
                      "finished_at": "2026-01-01T01:00:00Z"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-done")
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_b6_startup_failure_is_positive_proof(self):
        call_id = "call_" + "2" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "error": "CLI missing",
                      "diagnostics": {"phase": "startup"}})
        reason = self.transport.positive_launch_failure(call_id)
        self.assertIsNotNone(reason)
        self.assertIn("CLI missing", reason)

    def test_b6_execution_failure_with_session_is_not_launch_failure(self):
        """Failed AFTER establishing a session: the launch succeeded;
        the execution failed. positive_launch_failure must return None."""
        call_id = "call_" + "3" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": "sess-x",
                      "error": "tool denied",
                      "diagnostics": {"phase": "execution"}})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_b6_cancelled_is_not_launch_failure(self):
        call_id = "call_" + "4" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "cancelled", "native_session_id": "sess-y"})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))


class ReconcileHeldTests(unittest.TestCase):
    """Verify that reconcile_dispatched holds uncertain attempts."""

    def test_uncertain_attempt_is_held_not_failed(self):
        """When find_session returns None and positive_launch_failure
        returns None, the attempt must stay DISPATCHED (HELD), not
        transition to FAILED or be relaunched."""
        transport = AutoSwarmTransport(
            _MockStore(), "/nonexistent", "/nonexistent")
        call_id = "call_" + "5" * 32
        found = transport.find_session(call_id)
        failure = transport.positive_launch_failure(call_id)
        self.assertIsNone(found,
                          "An unobservable session must not produce evidence")
        self.assertIsNone(failure,
                          "Absence alone must not prove launch failure")


class OutcomeNormalizationTests(unittest.TestCase):
    """Exercise every terminal and non-terminal outcome state."""

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

    def _result(self, call_id, outcome, content="", **extra):
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": outcome,
                      "content": content, **extra})

    def test_completed_maps_to_success(self):
        c = "call_" + "a" * 32
        self._result(c, "completed", "done")
        self.assertEqual(self.transport.collect(c)["outcome"], "success")

    def test_failed_passes_through(self):
        c = "call_" + "b" * 32
        self._result(c, "failed", error="crash")
        self.assertEqual(self.transport.collect(c)["outcome"], "failed")

    def test_cancelled_passes_through(self):
        c = "call_" + "c" * 32
        self._result(c, "cancelled")
        self.assertEqual(self.transport.collect(c)["outcome"], "cancelled")

    def test_unknown_outcome_passes_verbatim(self):
        c = "call_" + "d" * 32
        self._result(c, "interrupted")
        self.assertEqual(self.transport.collect(c)["outcome"], "interrupted")

    def test_missing_result_raises(self):
        c = "call_" + "e" * 32
        self._call_dir(c).mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            self.transport.collect(c)

    def test_empty_content_and_no_stdout_produces_no_transcript(self):
        c = "call_" + "f" * 32
        self._result(c, "failed", content="", error="auth")
        collected = self.transport.collect(c)
        kinds = [wf[0] for wf in collected["witness_files"]]
        self.assertNotIn("obj-13", kinds)


class EvidenceProvenanceTests(unittest.TestCase):
    """Validate the actual provenance chain for each evidence kind."""

    def test_obj11_evidence_is_autoswarm_produced(self):
        """obj-11 evidence bytes from find_session must name AutoSwarm
        as the session owner, not the adapter."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        store = _MockStore()
        transport = AutoSwarmTransport(store, str(home), str(home))
        call_id = "call_" + "1" * 32
        d = home / "calls" / call_id
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(home),
                      "created_at": "2026-01-01T00:00:00Z"})
        _write_json(d / "handle.json",
                     {"pid": 1, "native_session_id": "sess-prov",
                      "started_at": "2026-01-01T00:00:01Z",
                      "phase": "execution"})
        found = transport.find_session(call_id)
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["kind"], "AutoSwarmSessionRecord")
        self.assertEqual(evidence["call_id"], call_id)
        self.assertEqual(evidence["native_session_id"], "sess-prov")
        self.assertEqual(evidence["provider"], "codex")
        self.assertEqual(transport.producer_id, "autoswarm")
        tmpdir.cleanup()

    def test_obj12_governance_directory_is_not_standard_autoswarm_layout(self):
        """The adapter's governance/ path does not exist in AutoSwarm's
        standard call directory layout. This test documents the gap:
        obj-12 evidence lives in the Helios gate chain, not in the call
        directory, and requires the specialist's CLI session ID to locate."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        store = _MockStore()
        transport = AutoSwarmTransport(store, str(home), str(home))
        call_id = "call_" + "2" * 32
        d = home / "calls" / call_id
        _write_json(d / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "content": "done"})
        collected = transport.collect(call_id)
        obj12_present = any(wf[0] == "obj-12"
                            for wf in collected["witness_files"])
        self.assertFalse(obj12_present,
                         "obj-12 should not be found at <call_dir>/governance/"
                         " because Helios writes to $GateRoot/evidence/"
                         "integrity/sessions/$SessionId/commands/")
        tmpdir.cleanup()

    def test_obj13_content_is_reconstructed_not_original(self):
        """The adapter wraps result.content in an AgentTranscript envelope.
        This is a reconstructed representation, not original producer bytes.
        Document this as a known provenance gap."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        store = _MockStore()
        transport = AutoSwarmTransport(store, str(home), str(home))
        call_id = "call_" + "3" * 32
        d = home / "calls" / call_id
        _write_json(d / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-t",
                      "content": "investigation output"})
        collected = transport.collect(call_id)
        obj13 = next(wf for wf in collected["witness_files"]
                     if wf[0] == "obj-13")
        transcript = json.loads(obj13[2])
        self.assertEqual(transcript["kind"], "AgentTranscript")
        self.assertIn("content", transcript)
        self.assertNotEqual(transcript["kind"], "NativeProviderExport",
                            "Evidence is the adapter's envelope, not the"
                            " provider's native export format")
        tmpdir.cleanup()
