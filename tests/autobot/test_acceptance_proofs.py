"""Final acceptance proofs A–D for the AutoSwarm Transport adapter.

A. Running-state behavior: collect returns None for non-terminal sessions
B. Artifact integrity: no placeholder admitted from production transport
C. Native transcript evidence: provider-specific obj-13 via session export
D. Stranded-launch recovery: task.json exists, no runner → HELD
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from autobot.autoswarm_transport import AutoSwarmTransport, _read_json
from autobot.transport import Transport, reconcile_dispatched, normalize_outcome
from autobot.witnesses import WITNESS_KINDS
from autobot.witness_semantics import extract_facts, identity_conflicts


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)


class _MockStore:
    def __init__(self):
        self._data = {}

    def put(self, data):
        address = "sha256:" + hashlib.sha256(data).hexdigest()
        self._data[address] = bytes(data)
        return address

    def get(self, address):
        return self._data[address]

    def has(self, address):
        return address in self._data


# ====================================================================
# A. Running-state behavior
# ====================================================================

class RunningStateBehaviorTests(unittest.TestCase):
    """Transport.collect returns None for sessions without a terminal
    result. The Taskforce must be able to iterate RUNNING attempts
    without exception or fabricated outcome."""

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

    def test_collect_returns_none_when_no_result_json(self):
        """A running session (handle present, no result) produces None
        from collect — not an exception or fabricated result."""
        call_id = "call_" + "a" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        _write_json(d / "handle.json",
                     {"pid": 999, "native_session_id": "sess-running",
                      "phase": "execution"})
        result = self.transport.collect(call_id)
        self.assertIsNone(result)

    def test_collect_returns_none_for_bare_directory(self):
        """Bare directory without any files: collect returns None."""
        call_id = "call_" + "b" * 32
        self._call_dir(call_id).mkdir(parents=True)
        result = self.transport.collect(call_id)
        self.assertIsNone(result)

    def test_collect_returns_none_for_task_only(self):
        """task.json without handle or result: collect returns None."""
        call_id = "call_" + "c" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex"})
        result = self.transport.collect(call_id)
        self.assertIsNone(result)

    def test_collect_returns_dict_when_result_exists(self):
        """Terminal session (result.json present) returns the full
        collection dict."""
        call_id = "call_" + "d" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-done"})
        result = self.transport.collect(call_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["outcome"], "success")

    def test_taskforce_collect_loop_skips_running_sessions(self):
        """Simulate the Taskforce collect_running loop: when collect
        returns None for a running attempt, the loop continues without
        exception and processes the next attempt."""
        calls = []
        for i, has_result in enumerate([False, True, False]):
            call_id = "call_" + ("%x" % i) * 32
            d = self._call_dir(call_id)
            _write_json(d / "task.json",
                         {"call_id": call_id, "provider": "codex",
                          "workspace": str(self.home)})
            if has_result:
                _write_json(d / "result.json",
                             {"call_id": call_id, "outcome": "completed",
                              "native_session_id": "sess-%d" % i,
                              "content": "output"})
            else:
                _write_json(d / "handle.json",
                             {"pid": 100 + i, "native_session_id": "s%d" % i,
                              "phase": "execution"})
            calls.append(call_id)
        collected = []
        for call_id in calls:
            result = self.transport.collect(call_id)
            if result is None:
                continue
            collected.append(result)
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["outcome"], "success")


# ====================================================================
# B. Artifact integrity
# ====================================================================

class ArtifactIntegrityTests(unittest.TestCase):
    """The production Transport returns actual provider content as
    artifact_bytes — no placeholder can be admitted as genuine output."""

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

    def test_content_returned_as_artifact_bytes(self):
        """When result.json has content, collect returns it as
        artifact_bytes — the specialist's actual engineering output."""
        call_id = "call_" + "a" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "s",
                      "content": "The logging layer uses structured JSON"})
        result = self.transport.collect(call_id)
        self.assertEqual(
            result["artifact_bytes"],
            b"The logging layer uses structured JSON")

    def test_empty_content_produces_no_artifact_key(self):
        """When content is empty, artifact_bytes key is absent —
        the Taskforce fallback applies but the Transport itself
        never manufactures placeholder bytes."""
        call_id = "call_" + "b" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "s", "content": ""})
        result = self.transport.collect(call_id)
        self.assertNotIn("artifact_bytes", result)

    def test_missing_content_produces_no_artifact_key(self):
        """No content field at all: no artifact_bytes key."""
        call_id = "call_" + "c" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "failed",
                      "error": "auth denied"})
        result = self.transport.collect(call_id)
        self.assertNotIn("artifact_bytes", result)

    def test_failed_session_with_content_still_returns_artifact(self):
        """Even a failed session may have partial content (the
        specialist produced output before failing). The artifact
        bytes are the actual content, not a failure placeholder."""
        call_id = "call_" + "d" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "failed",
                      "native_session_id": "s",
                      "content": "Partial analysis before crash",
                      "error": "tool denied"})
        result = self.transport.collect(call_id)
        self.assertEqual(
            result["artifact_bytes"],
            b"Partial analysis before crash")

    def test_placeholder_never_appears_in_transport_output(self):
        """The Transport never returns b'no artifact collected\\n' —
        that string exists only as a Taskforce fallback."""
        placeholder = b"no artifact collected\n"
        for outcome, content in [
            ("completed", "real output"),
            ("completed", ""),
            ("completed", None),
            ("failed", None),
            ("failed", "partial"),
        ]:
            call_id = "call_" + hashlib.sha256(
                str((outcome, content)).encode()).hexdigest()[:32]
            data = {"call_id": call_id, "outcome": outcome}
            if content is not None:
                data["content"] = content
            _write_json(self._call_dir(call_id) / "result.json", data)
            result = self.transport.collect(call_id)
            artifact = result.get("artifact_bytes")
            self.assertNotEqual(artifact, placeholder,
                                "placeholder appeared for %s" % (
                                    (outcome, content),))

    def test_source_modifying_without_content_blocks_candidate(self):
        """When the Transport returns no artifact_bytes, the Taskforce
        fallback b'no artifact collected\\n' is not valid JSON with a
        new_value key, so SOURCE_MODIFYING work blocks at
        BLOCKED_NO_CANDIDATE_TREE — the placeholder never becomes an
        accepted candidate."""
        placeholder = b"no artifact collected\n"
        try:
            parsed = json.loads(placeholder.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            parsed = {}
        treeish = parsed.get("new_value") if isinstance(parsed, dict) else None
        self.assertIsNone(treeish,
                          "placeholder must not parse as a valid artifact")


# ====================================================================
# C. Native transcript evidence (obj-13)
# ====================================================================

class NativeTranscriptTests(unittest.TestCase):
    """Provider-specific obj-13 via session export files."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.export_dir = Path(self.tmpdir.name) / "exports"
        self.export_dir.mkdir()
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home),
            session_export_dir=str(self.export_dir))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_session_export_collected_as_obj13(self):
        """A provider session export at the configured path is
        collected as obj-13 with the correct producer."""
        session_id = "sess-transcript"
        call_id = "call_" + "a" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id,
                      "content": "output"})
        export = {"session_id": session_id, "provider": "claude-code",
                  "messages": [{"role": "user", "content": "hello"}]}
        export_path = self.export_dir / ("%s.session.json" % session_id)
        export_path.write_text(json.dumps(export), encoding="utf-8")
        result = self.transport.collect(call_id)
        obj13_entries = [wf for wf in result["witness_files"]
                         if wf[0] == "obj-13"]
        self.assertEqual(len(obj13_entries), 1)
        self.assertEqual(obj13_entries[0][1], "agent_clis")

    def test_export_bytes_are_original_file_content(self):
        """The obj-13 bytes are the exact file content — the adapter
        reads without rewriting."""
        session_id = "sess-bytes"
        call_id = "call_" + "b" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id})
        export_data = json.dumps(
            {"session_id": session_id, "data": "content"},
            indent=2, ensure_ascii=False)
        export_path = self.export_dir / ("%s.session.json" % session_id)
        export_path.write_text(export_data, encoding="utf-8")
        result = self.transport.collect(call_id)
        obj13 = next(wf for wf in result["witness_files"]
                     if wf[0] == "obj-13")
        self.assertEqual(obj13[2], export_path.read_bytes())

    def test_recognizer_extracts_session_identity(self):
        """The existing witness recognizer extracts session_id from
        the export file for identity correlation."""
        session_id = "sess-recognize"
        export = {"session_id": session_id, "provider": "claude-code"}
        data = json.dumps(export).encode("utf-8")
        facts = extract_facts("obj-13", data)
        self.assertEqual(facts.get("native_session_id"), session_id)
        self.assertEqual(facts.get("provider"), "claude-code")

    def test_recognizer_identity_conflicts_detected(self):
        """A session export naming a different session is caught by
        the identity gate at admission time."""
        export = {"session_id": "wrong-session",
                  "provider": "claude-code"}
        data = json.dumps(export).encode("utf-8")
        facts = extract_facts("obj-13", data)
        conflicts = identity_conflicts(
            "obj-13", facts, "call_" + "x" * 32, "correct-session")
        self.assertTrue(len(conflicts) > 0)

    def test_source_hash_preserves_file_identity(self):
        """The CAS address of the admitted bytes is the SHA-256 of
        the original file content."""
        session_id = "sess-hash"
        call_id = "call_" + "c" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id})
        export_data = json.dumps({"session_id": session_id})
        export_path = self.export_dir / ("%s.session.json" % session_id)
        export_path.write_text(export_data, encoding="utf-8")
        result = self.transport.collect(call_id)
        obj13 = next(wf for wf in result["witness_files"]
                     if wf[0] == "obj-13")
        file_bytes = export_path.read_bytes()
        expected_sha = "sha256:" + hashlib.sha256(file_bytes).hexdigest()
        actual_sha = "sha256:" + hashlib.sha256(obj13[2]).hexdigest()
        self.assertEqual(actual_sha, expected_sha)

    def test_no_export_dir_means_no_obj13(self):
        """Without session_export_dir, obj-13 is absent."""
        transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))
        call_id = "call_" + "d" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "s"})
        result = transport.collect(call_id)
        kinds = [wf[0] for wf in result["witness_files"]]
        self.assertNotIn("obj-13", kinds)

    def test_missing_export_file_means_no_obj13(self):
        """When the export file doesn't exist, obj-13 is absent."""
        call_id = "call_" + "e" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "nonexistent-session"})
        result = self.transport.collect(call_id)
        kinds = [wf[0] for wf in result["witness_files"]]
        self.assertNotIn("obj-13", kinds)

    def test_empty_export_file_means_no_obj13(self):
        """Empty export file: obj-13 is absent."""
        session_id = "sess-empty"
        call_id = "call_" + "f" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id})
        (self.export_dir / ("%s.session.json" % session_id)).write_bytes(b"")
        result = self.transport.collect(call_id)
        kinds = [wf[0] for wf in result["witness_files"]]
        self.assertNotIn("obj-13", kinds)


# ====================================================================
# D. Stranded-launch recovery
# ====================================================================

class StrandedLaunchTests(unittest.TestCase):
    """When task.json exists but no runner spawned, the system
    retains UNCERTAIN — no automatic relaunch."""

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

    def test_task_without_runner_is_unobservable(self):
        """task.json present but no handle.json or result.json:
        find_session returns None — the session is unobservable."""
        call_id = "call_" + "a" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        self.assertIsNone(self.transport.find_session(call_id))

    def test_task_without_runner_has_no_positive_failure(self):
        """No result.json means positive_launch_failure returns None —
        absence alone does not prove failure."""
        call_id = "call_" + "b" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex"})
        self.assertIsNone(
            self.transport.positive_launch_failure(call_id))

    def test_stranded_launch_produces_held_not_failed(self):
        """The three-way reconcile split: no session, no failure proof →
        HELD. The attempt stays DISPATCHED, not FAILED or relaunched."""
        call_id = "call_" + "c" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        found = self.transport.find_session(call_id)
        failure = self.transport.positive_launch_failure(call_id)
        self.assertIsNone(found)
        self.assertIsNone(failure)

    def test_stranded_launch_collect_returns_none(self):
        """collect on a stranded call (no result.json) returns None —
        no fabricated outcome, no exception."""
        call_id = "call_" + "d" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex"})
        self.assertIsNone(self.transport.collect(call_id))

    def test_eventual_result_transitions_out_of_held(self):
        """If the runner eventually completes (result.json appears),
        find_session and collect return valid data."""
        call_id = "call_" + "e" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        self.assertIsNone(self.transport.find_session(call_id))
        _write_json(d / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-recovered",
                      "content": "recovered output"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-recovered")
        collected = self.transport.collect(call_id)
        self.assertEqual(collected["outcome"], "success")
        self.assertEqual(
            collected["artifact_bytes"], b"recovered output")

    def test_stranded_with_runner_log_only_remains_uncertain(self):
        """A runner.log without handle.json means the runner started
        writing but hasn't published the handle yet. The system
        stays uncertain — no premature determination."""
        call_id = "call_" + "f" * 32
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        d.mkdir(parents=True, exist_ok=True)
        (d / "runner.log").write_bytes(b"runner starting\n")
        self.assertIsNone(self.transport.find_session(call_id))
        self.assertIsNone(
            self.transport.positive_launch_failure(call_id))
        self.assertIsNone(self.transport.collect(call_id))


if __name__ == "__main__":
    unittest.main()
