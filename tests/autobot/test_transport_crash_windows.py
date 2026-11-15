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

    def _result(self, call_id, outcome, **extra):
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": outcome, **extra})

    def test_completed_maps_to_success(self):
        c = "call_" + "a" * 32
        self._result(c, "completed")
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

    def test_missing_result_returns_none(self):
        """No result.json: collect returns None (non-terminal session)."""
        c = "call_" + "e" * 32
        self._call_dir(c).mkdir(parents=True)
        self.assertIsNone(self.transport.collect(c))

    def test_no_stdout_produces_no_transcript(self):
        c = "call_" + "f" * 32
        self._result(c, "failed", error="auth")
        collected = self.transport.collect(c)
        kinds = [wf[0] for wf in collected["witness_files"]]
        self.assertNotIn("obj-13", kinds)

    def test_missing_outcome_defaults_to_failed(self):
        """A result.json with no outcome field must not produce success."""
        c = "call_" + "1" * 32
        _write_json(self._call_dir(c) / "result.json",
                     {"call_id": c, "error": "process vanished"})
        collected = self.transport.collect(c)
        self.assertEqual(collected["outcome"], "failed")

    def test_empty_outcome_defaults_to_failed(self):
        c = "call_" + "2" * 32
        _write_json(self._call_dir(c) / "result.json",
                     {"call_id": c, "outcome": ""})
        collected = self.transport.collect(c)
        self.assertNotEqual(collected["outcome"], "success")


class EvidenceProvenanceTests(unittest.TestCase):
    """Validate the actual provenance chain for each evidence kind."""

    def test_obj11_evidence_is_original_task_json(self):
        """obj-11 evidence from find_session is the byte-identical
        content of task.json — the producer record carrying the
        received-prompt identity."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        store = _MockStore()
        transport = AutoSwarmTransport(store, str(home), str(home))
        call_id = "call_" + "1" * 32
        d = home / "calls" / call_id
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "prompt": "investigate logging",
                      "workspace": str(home),
                      "created_at": "2026-01-01T00:00:00Z"})
        _write_json(d / "handle.json",
                     {"pid": 1, "native_session_id": "sess-prov",
                      "started_at": "2026-01-01T00:00:01Z",
                      "phase": "execution"})
        found = transport.find_session(call_id)
        self.assertEqual(found["evidence"], (d / "task.json").read_bytes())
        self.assertEqual(transport.producer_id, "autoswarm")
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["prompt"], "investigate logging")
        tmpdir.cleanup()

    def test_obj11_is_task_json_even_when_result_exists(self):
        """obj-11 is always task.json regardless of whether result.json
        exists — result.json does not carry the prompt."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        transport = AutoSwarmTransport(_MockStore(), str(home), str(home))
        call_id = "call_" + "2" * 32
        d = home / "calls" / call_id
        _write_json(d / "task.json", {"call_id": call_id, "provider": "codex",
                                       "prompt": "refactor bridge",
                                       "workspace": str(home)})
        _write_json(d / "handle.json", {"pid": 1, "native_session_id": "s"})
        _write_json(d / "result.json", {"call_id": call_id, "outcome": "completed",
                                         "native_session_id": "s"})
        found = transport.find_session(call_id)
        self.assertEqual(found["evidence"], (d / "task.json").read_bytes())
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["prompt"], "refactor bridge")
        tmpdir.cleanup()

    def test_obj12_individual_entries_with_original_bytes(self):
        """Each decision.json becomes a separate obj-12 entry and the
        bytes are the original file content, not a combined array."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        gate_root = Path(tmpdir.name) / "gate"
        transport = AutoSwarmTransport(
            _MockStore(), str(home), str(home),
            gate_root=str(gate_root))
        session_id = "sess-gov"
        call_id = "call_" + "3" * 32
        d = home / "calls" / call_id
        _write_json(d / "result.json", {"call_id": call_id,
                                         "outcome": "completed",
                                         "native_session_id": session_id})
        evidence_dir = (gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        _write_json(evidence_dir / "toolu_abc.decision.json",
                     {"verdict": "ALLOW", "tool": "Read",
                      "session_id": session_id})
        _write_json(evidence_dir / "toolu_def.decision.json",
                     {"verdict": "DENY", "tool": "Bash",
                      "session_id": session_id,
                      "command_sha256": "sha256:aaa"})
        collected = transport.collect(call_id)
        obj12_entries = [wf for wf in collected["witness_files"]
                         if wf[0] == "obj-12"]
        self.assertEqual(len(obj12_entries), 2)
        for kind, producer, data in obj12_entries:
            self.assertEqual(producer, "helios")
            record = json.loads(data)
            self.assertIsInstance(record, dict)
            self.assertEqual(record["session_id"], session_id)
        file_bytes_abc = (evidence_dir / "toolu_abc.decision.json").read_bytes()
        file_bytes_def = (evidence_dir / "toolu_def.decision.json").read_bytes()
        self.assertEqual(obj12_entries[0][2], file_bytes_abc)
        self.assertEqual(obj12_entries[1][2], file_bytes_def)
        tmpdir.cleanup()

    def test_obj12_identity_extraction_per_entry(self):
        """extract_facts recognizes session_id from each individual
        obj-12 entry — impossible on a combined array."""
        from autobot.witness_semantics import extract_facts
        record = {"verdict": "ALLOW", "tool": "Read",
                  "session_id": "sess-id-123"}
        data = json.dumps(record).encode("utf-8")
        facts = extract_facts("obj-12", data)
        self.assertEqual(facts.get("session_id"), "sess-id-123")
        self.assertEqual(facts.get("decision"), "ALLOW")

    def test_obj12_dc414_admission_per_entry(self):
        """authorization_direction_conflict (D-C4-14) detects
        contradictory decision spellings on an individual record."""
        from autobot.witness_semantics import authorization_direction_conflict
        conflicted = {"decision": "DENY", "verdict": "ALLOW",
                      "session_id": "s"}
        data = json.dumps(conflicted).encode("utf-8")
        result = authorization_direction_conflict("obj-12", data)
        self.assertIsNotNone(result)

    def test_obj12_deny_then_ran_with_individual_entries(self):
        """deny_then_ran detects the U-01 contradiction across
        individual obj-12 entries from the same session."""
        from autobot.witness_semantics import extract_facts, deny_then_ran
        cmd_sha = "sha256:deadbeef"
        deny_record = {"verdict": "DENY", "tool": "Bash",
                       "session_id": "s1",
                       "command_sha256": cmd_sha}
        exec_record = {"verdict": "ALLOW", "tool": "Bash",
                       "session_id": "s1",
                       "command_sha256": cmd_sha,
                       "hook_event": "PostToolUse",
                       "success": True}
        entries = []
        for i, rec in enumerate([deny_record, exec_record]):
            data = json.dumps(rec).encode("utf-8")
            facts = extract_facts("obj-12", data)
            entries.append(((i,), "obj-12", facts))
        contradictions = deny_then_ran(entries)
        self.assertTrue(len(contradictions) > 0)
        self.assertEqual(contradictions[0]["command_sha256"], cmd_sha)

    def test_obj12_collector_completeness(self):
        """Every decision file in the evidence tree produces an entry;
        no file is silently dropped and no extra entries appear."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        gate_root = Path(tmpdir.name) / "gate"
        transport = AutoSwarmTransport(
            _MockStore(), str(home), str(home),
            gate_root=str(gate_root))
        session_id = "sess-complete"
        call_id = "call_" + "c" * 32
        _write_json(home / "calls" / call_id / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id})
        evidence_dir = (gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        for i in range(5):
            _write_json(evidence_dir / ("toolu_%03d.decision.json" % i),
                         {"verdict": "ALLOW", "tool": "Read",
                          "session_id": session_id, "index": i})
        collected = transport.collect(call_id)
        obj12_entries = [wf for wf in collected["witness_files"]
                         if wf[0] == "obj-12"]
        self.assertEqual(len(obj12_entries), 5)
        tmpdir.cleanup()

    def test_obj13_not_emitted_from_stdout_pipe(self):
        """AutoSwarm's stdout.log is a runner pipe capture, not a
        provider-native session transcript. obj-13 must be absent."""
        tmpdir = tempfile.TemporaryDirectory()
        home = Path(tmpdir.name)
        transport = AutoSwarmTransport(_MockStore(), str(home), str(home))
        call_id = "call_" + "4" * 32
        d = home / "calls" / call_id
        _write_json(d / "result.json", {"call_id": call_id,
                                         "outcome": "completed",
                                         "native_session_id": "s"})
        d.mkdir(parents=True, exist_ok=True)
        (d / "stdout.log").write_bytes(
            b'{"type":"result","content":"done"}\n')
        collected = transport.collect(call_id)
        kinds = [wf[0] for wf in collected["witness_files"]]
        self.assertNotIn("obj-13", kinds)
        tmpdir.cleanup()
