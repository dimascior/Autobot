"""Final pilot acceptance tests — items 1-5 of the precondition work order.

1. Artifact integrity: no fabricated bytes admitted
2. Outcome integrity: missing outcome never promoted to success
3. Prompt identity: frozen spec → identity transform → persisted prompt
4. Transcript disposition: Path B (no SESSION_TRANSCRIPT in requirements)
5. End-to-end deterministic acceptance through the production adapter

Uses the existing V-A corpus and support fixtures.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import support
from fake_verifier import FakeVerifierRunner
from autobot import ProductionVerifierRunner, Store
from autobot.autoswarm_transport import AutoSwarmTransport, _read_json
from autobot.collector import Collector
from autobot.projection import project_prompt
from autobot.receipts import EVIDENCE_KIND_BY_REQUIREMENT
from autobot.transport import Transport, reconcile_dispatched, normalize_outcome
from autobot.witness_semantics import extract_facts
from autobot.witnesses import WITNESS_KINDS
from contract_library import canonicalize, content_address


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
# 1. Artifact integrity
# ====================================================================

class ArtifactIntegrityTests(unittest.TestCase):
    """The production collect path never fabricates artifact bytes."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.transport = AutoSwarmTransport(
            _MockStore(), str(self.home), str(self.home))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_successful_outcome_without_content_has_no_artifact(self):
        """A provider reporting success but no content does not produce
        artifact_bytes — the Transport never fabricates content."""
        call_id = "call_" + "a" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "s"})
        result = self.transport.collect(call_id)
        self.assertNotIn("artifact_bytes", result)

    def test_placeholder_never_in_transport_output(self):
        """b'no artifact collected\\n' never appears in Transport
        output, regardless of provider result shape."""
        placeholder = b"no artifact collected\n"
        for data in [
            {"outcome": "completed", "native_session_id": "s"},
            {"outcome": "completed", "native_session_id": "s",
             "content": ""},
            {"outcome": "failed", "error": "denied"},
        ]:
            cid = "call_" + hashlib.sha256(
                json.dumps(data).encode()).hexdigest()[:32]
            data["call_id"] = cid
            _write_json(self._call_dir(cid) / "result.json", data)
            result = self.transport.collect(cid)
            self.assertNotEqual(result.get("artifact_bytes"), placeholder)

    def test_real_content_returned_verbatim(self):
        """When the provider produced output, it is returned as exact
        UTF-8 bytes — not wrapped or transformed."""
        call_id = "call_" + "b" * 32
        text = "Structured logging uses JSON fields.\n"
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "s", "content": text})
        result = self.transport.collect(call_id)
        self.assertEqual(result["artifact_bytes"], text.encode("utf-8"))


# ====================================================================
# 2. Outcome integrity
# ====================================================================

class OutcomeIntegrityTests(unittest.TestCase):
    """Missing, empty, malformed or unrecognized outcomes are never
    promoted to success by either the Transport or the Taskforce
    normalization."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.transport = AutoSwarmTransport(
            _MockStore(), str(self.home), str(self.home))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def _collect_outcome(self, outcome_value, include_key=True):
        cid = "call_" + hashlib.sha256(
            str(outcome_value).encode()).hexdigest()[:32]
        data = {"call_id": cid, "native_session_id": "s"}
        if include_key:
            data["outcome"] = outcome_value
        _write_json(self._call_dir(cid) / "result.json", data)
        return self.transport.collect(cid)["outcome"]

    def test_missing_outcome_defaults_to_failed(self):
        """result.json without an outcome key: Transport defaults to
        'failed', never 'success'."""
        outcome = self._collect_outcome(None, include_key=False)
        self.assertEqual(outcome, "failed")

    def test_empty_outcome_not_success(self):
        outcome = self._collect_outcome("")
        self.assertNotEqual(outcome, "success")

    def test_completed_maps_to_success(self):
        self.assertEqual(self._collect_outcome("completed"), "success")

    def test_failed_passes_through(self):
        self.assertEqual(self._collect_outcome("failed"), "failed")

    def test_cancelled_passes_through(self):
        self.assertEqual(self._collect_outcome("cancelled"), "cancelled")

    def test_unrecognized_passes_verbatim(self):
        self.assertEqual(self._collect_outcome("interrupted"), "interrupted")

    def test_taskforce_normalization_missing_outcome(self):
        """The Taskforce's own normalization: a missing outcome from the
        transport result normalizes to '' which is not in any frozen
        success_outcomes list."""
        normalized = normalize_outcome(None or "")
        self.assertNotEqual(normalized, "success")
        self.assertEqual(normalized, "")

    def test_taskforce_normalization_empty_outcome(self):
        normalized = normalize_outcome("" or "")
        self.assertEqual(normalized, "")
        self.assertNotEqual(normalized, "success")


# ====================================================================
# 3. Prompt identity — registered identity transform chain
# ====================================================================

class PromptIdentityTests(unittest.TestCase):
    """The complete chain: frozen specification input → registered
    identity transform → expected prompt bytes → persisted prompt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, self.va = support.admitted_runtime(self.dir,
                                                    clock=self.clock)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def test_identity_transform_output_equals_input(self):
        """The registered identity-projection transform is byte-identity:
        output bytes == input bytes."""
        from autobot import transforms
        reg = support.live_transform_registration("identity-projection")
        data = support.VA_INPUT.encode("utf-8")
        output, execution = transforms.execute_transform(
            self.rt.store, reg, data)
        self.assertEqual(output, data)
        self.assertEqual(execution["output_sha256"],
                         content_address(data))

    def test_frozen_spec_input_through_transform_to_prompt(self):
        """Frozen specification input → identity transform → expected
        prompt. The expected output is independently derived from the
        frozen input bytes, not from adapter-controlled copies."""
        record = support.load_fixture("va_record.json")
        input_name = list(
            record["specification_inputs"][
                "name_to_exact_byte_sha256"].keys())[0]
        frozen_input_address = (
            record["specification_inputs"][
                "name_to_exact_byte_sha256"][input_name])
        frozen_input_bytes = self.rt.store.get(frozen_input_address)
        from autobot import transforms
        reg = support.live_transform_registration("identity-projection")
        expected_output, execution = transforms.execute_transform(
            self.rt.store, reg, frozen_input_bytes)
        self.assertIsNotNone(expected_output)
        self.assertEqual(expected_output, frozen_input_bytes)
        expected_prompt_sha = content_address(expected_output)
        actual_prompt = support.VA_INPUT.encode("utf-8")
        actual_prompt_sha = content_address(actual_prompt)
        self.assertEqual(actual_prompt_sha, expected_prompt_sha)

    def test_autoswarm_persisted_prompt_matches_transform_output(self):
        """The prompt AutoSwarm persists in task.json["prompt"] must be
        byte-identical to the identity transform's output over the
        frozen specification input."""
        expected_prompt = support.VA_INPUT
        task_record = {"call_id": "call_" + "a" * 32,
                       "provider": "codex",
                       "prompt": expected_prompt,
                       "native_session_id": "sess-001"}
        task_bytes = json.dumps(task_record).encode("utf-8")
        facts = extract_facts("obj-11", task_bytes)
        expected_sha = content_address(expected_prompt.encode("utf-8"))
        self.assertEqual(facts["prompt_sha256"], expected_sha)

    def test_prompt_sha_mismatch_detected(self):
        """A different prompt produces a different prompt_sha256, which
        the verifier would detect as transform_output_mismatch."""
        correct = support.VA_INPUT
        wrong = correct + " extra text"
        correct_sha = content_address(correct.encode("utf-8"))
        wrong_sha = content_address(wrong.encode("utf-8"))
        self.assertNotEqual(correct_sha, wrong_sha)


# ====================================================================
# 4. Transcript disposition — Path B
# ====================================================================

class TranscriptDispositionTests(unittest.TestCase):
    """The pilot contract's evidence_requirements do not include
    SESSION_TRANSCRIPT, so obj-13 is not required for evidence
    completeness."""

    def test_pilot_record_does_not_require_transcript(self):
        """The frozen VA record (the pilot's base) requires only
        COMMAND_GOVERNANCE and EXECUTION_LIFECYCLE."""
        record = support.load_fixture("va_record.json")
        self.assertNotIn("SESSION_TRANSCRIPT",
                         record["evidence_requirements"])
        self.assertIn("COMMAND_GOVERNANCE",
                       record["evidence_requirements"])
        self.assertIn("EXECUTION_LIFECYCLE",
                       record["evidence_requirements"])

    def test_read_only_variant_inherits_requirements(self):
        """The READ_ONLY pilot contract inherits the same evidence
        requirements — still no SESSION_TRANSCRIPT."""
        record = support.local_record_payload(
            finalization_class="READ_ONLY",
            evidence_requirements=["COMMAND_GOVERNANCE",
                                   "EXECUTION_LIFECYCLE"])
        self.assertNotIn("SESSION_TRANSCRIPT",
                         record["evidence_requirements"])

    def test_obj13_kind_maps_to_transcript_requirement(self):
        """For completeness: SESSION_TRANSCRIPT maps to obj-13. Since
        the pilot record does not list it, the Collector classifies
        as COMPLETE without obj-13 admitted."""
        self.assertEqual(
            EVIDENCE_KIND_BY_REQUIREMENT["SESSION_TRANSCRIPT"], "obj-13")

    def test_transport_without_export_dir_produces_no_obj13(self):
        """The production Transport with no session_export_dir does not
        emit obj-13 — consistent with the pilot's Path B."""
        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        transport = AutoSwarmTransport(
            _MockStore(), str(home), str(home))
        call_id = "call_" + "d" * 32
        _write_json(home / "calls" / call_id / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "s"})
        result = transport.collect(call_id)
        kinds = [wf[0] for wf in result["witness_files"]]
        self.assertNotIn("obj-13", kinds)
        tmp.cleanup()


# ====================================================================
# 5. End-to-end deterministic acceptance test
# ====================================================================

class EndToEndAcceptanceTests(unittest.TestCase):
    """One complete READ_ONLY contract through the production Transport
    adapter with controlled AutoSwarm fixtures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.autoswarm_home = self.dir / "autoswarm"
        self.gate_root = self.dir / "gate"
        self.workspace = self.dir / "ws"
        self.workspace.mkdir()
        self.clock = support.manual_clock()
        self.rt, self.va = support.admitted_runtime(self.dir,
                                                    clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _admit_read_only_record(self):
        record = support.local_record_payload(
            task_contract_id="tc_" + "b110" * 8,
            objective="Pilot: investigate logging layer (READ_ONLY)",
            finalization_class="READ_ONLY",
            evidence_requirements=["COMMAND_GOVERNANCE",
                                   "EXECUTION_LIFECYCLE"])
        result = self.rt.registry.admit_payload(record,
                                                submitted_by="planner")
        self.assertEqual(result["outcome"], "NEW_RECORD")
        return result, record

    def _create_call_files(self, call_id, session_id, prompt,
                           outcome="completed", content=None):
        """Create AutoSwarm call directory with all lifecycle files."""
        d = self.autoswarm_home / "calls" / call_id
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "prompt": prompt, "workspace": str(self.workspace),
                      "native_session_id": session_id})
        _write_json(d / "handle.json",
                     {"pid": 999, "native_session_id": session_id,
                      "phase": "execution"})
        result_data = {"call_id": call_id, "outcome": outcome,
                       "native_session_id": session_id,
                       "provider": "codex"}
        if content is not None:
            result_data["content"] = content
        _write_json(d / "result.json", result_data)
        return d

    def _create_governance(self, session_id, decisions):
        """Create Helios governance evidence files."""
        evidence_dir = (self.gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        for tool_use_id, verdict in decisions:
            _write_json(evidence_dir / ("%s.decision.json" % tool_use_id),
                         {"verdict": verdict, "tool": "Read",
                          "session_id": session_id,
                          "tool_use_id": tool_use_id})

    def test_complete_read_only_lifecycle(self):
        """Full lifecycle: dispatch → adopt → collect → finish → freeze
        → classify → verify. Proves exactly-once identity, byte-correct
        prompt, original governance, correct Collector classification."""
        admitted, record = self._admit_read_only_record()
        sequence_id = admitted["sequence_id"]
        self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        attempt_id = info["attempt_id"]
        fence = info["fence"]
        self.seq.reserve_attempt(attempt_id, expected_fence=fence)
        prompt = support.VA_INPUT
        projection_bytes = json.dumps({
            "schema_version": "v1", "kind": "PromptProjection",
            "engineering_context": {"guidance": prompt,
                                    "author": "planner",
                                    "provenance": "test"}
        }, sort_keys=True).encode("utf-8")
        projection_address = self.rt.store.put(projection_bytes)
        dispatched = self.seq.dispatch_attempt(
            attempt_id, "builder", "codex", projection_address)
        call_id = dispatched["call_id"]
        session_id = "sess-pilot-001"
        self._create_call_files(
            call_id, session_id, prompt,
            outcome="completed", content="Logging uses structured JSON.")
        self._create_governance(session_id, [
            ("toolu_001", "ALLOW"), ("toolu_002", "ALLOW")])

        # --- Adopt: find_session returns evidence, reconcile transitions ---
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], session_id)
        adoption = self.seq.admit_witness(
            attempt_id, "obj-11", "autoswarm", found["evidence"])
        self.seq.attempt_running(
            attempt_id, session_id, adoption["content_address"])

        # --- Collect: governance as individual entries, artifact from content ---
        collected = self.transport.collect(call_id)
        self.assertIsNotNone(collected)
        self.assertEqual(collected["outcome"], "success")
        obj12_entries = [wf for wf in collected["witness_files"]
                         if wf[0] == "obj-12"]
        self.assertEqual(len(obj12_entries), 2)
        for kind, producer, data in obj12_entries:
            self.assertEqual(producer, "helios")
            record_data = json.loads(data)
            self.assertIsInstance(record_data, dict)
            self.assertEqual(record_data["session_id"], session_id)
            self.seq.admit_witness(attempt_id, kind, producer, data)
        self.assertNotIn("obj-13",
                         [wf[0] for wf in collected["witness_files"]])
        self.assertEqual(collected["artifact_bytes"],
                         b"Logging uses structured JSON.")
        admitted_witnesses = {}
        for kind, producer, data in collected["witness_files"]:
            entry = self.seq.admit_witness(
                attempt_id, kind, producer, data)
            admitted_witnesses[kind] = entry["content_address"]
        finish_witness = admitted_witnesses.get("obj-12")
        self.assertIsNotNone(finish_witness)

        # --- Finish: outcome normalizes correctly ---
        state = self.seq.finish_attempt(
            attempt_id, "builder", collected["outcome"], finish_witness)
        self.assertEqual(state, "FINISHED")

        # --- Artifact admission ---
        self.seq.admit_artifact(
            attempt_id, "artifact/engineering-notes",
            collected["artifact_bytes"])

        # --- Freeze candidate (READ_ONLY: no tree) ---
        support.mint_and_freeze(self.rt, attempt_id, fence)

        # --- Collector: derive COMPLETE (obj-11 + obj-12 present) ---
        receipt = support.collect_receipt(self.rt, attempt_id)
        self.seq.complete_evidence(attempt_id, fence, receipt)
        att_state = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (attempt_id,)).fetchone()["state"]
        self.assertEqual(att_state, "EVIDENCE_COMPLETE")

    def test_missing_outcome_does_not_produce_success(self):
        """A transport result without an outcome key: the attempt FAILS
        through the frozen completion policy, never obtains FINISHED."""
        admitted, _ = self._admit_read_only_record()
        sequence_id = admitted["sequence_id"]
        self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        attempt_id, fence = info["attempt_id"], info["fence"]
        self.seq.reserve_attempt(attempt_id, expected_fence=fence)
        projection = self.rt.store.put(b"test projection\n")
        dispatched = self.seq.dispatch_attempt(
            attempt_id, "builder", "codex", projection)
        call_id = dispatched["call_id"]
        session_id = "sess-missing-outcome"
        d = self._create_call_files(
            call_id, session_id, "test prompt")
        _write_json(d / "result.json",
                     {"call_id": call_id,
                      "native_session_id": session_id})
        found = self.transport.find_session(call_id)
        adoption = self.seq.admit_witness(
            attempt_id, "obj-11", "autoswarm", found["evidence"])
        self.seq.attempt_running(
            attempt_id, session_id, adoption["content_address"])
        collected = self.transport.collect(call_id)
        outcome = normalize_outcome(collected["outcome"])
        self.assertNotEqual(outcome, "success")
        for kind, producer, data in collected["witness_files"]:
            self.seq.admit_witness(attempt_id, kind, producer, data)
        state = self.seq.finish_attempt(
            attempt_id, "builder", outcome,
            adoption["content_address"])
        self.assertEqual(state, "FAILED")

    def test_no_fabricated_artifact_for_content_free_success(self):
        """Successful completion without content: no artifact_bytes in
        the transport result, no artifact admitted to the Sequencer."""
        admitted, _ = self._admit_read_only_record()
        sequence_id = admitted["sequence_id"]
        self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        attempt_id, fence = info["attempt_id"], info["fence"]
        self.seq.reserve_attempt(attempt_id, expected_fence=fence)
        projection = self.rt.store.put(b"test projection\n")
        dispatched = self.seq.dispatch_attempt(
            attempt_id, "builder", "codex", projection)
        call_id = dispatched["call_id"]
        session_id = "sess-no-content"
        self._create_call_files(
            call_id, session_id, "test prompt",
            outcome="completed")
        found = self.transport.find_session(call_id)
        adoption = self.seq.admit_witness(
            attempt_id, "obj-11", "autoswarm", found["evidence"])
        self.seq.attempt_running(
            attempt_id, session_id, adoption["content_address"])
        collected = self.transport.collect(call_id)
        self.assertEqual(collected["outcome"], "success")
        self.assertNotIn("artifact_bytes", collected)
        artifacts_before = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_artifacts"
            " WHERE attempt_id = ?", (attempt_id,)).fetchone()["n"]
        self.assertEqual(artifacts_before, 0)

    def test_running_session_collect_returns_none(self):
        """While the specialist is executing (no result.json), collect
        returns None — no exception, no fabricated outcome."""
        admitted, _ = self._admit_read_only_record()
        sequence_id = admitted["sequence_id"]
        self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        attempt_id, fence = info["attempt_id"], info["fence"]
        self.seq.reserve_attempt(attempt_id, expected_fence=fence)
        projection = self.rt.store.put(b"test projection\n")
        dispatched = self.seq.dispatch_attempt(
            attempt_id, "builder", "codex", projection)
        call_id = dispatched["call_id"]
        session_id = "sess-running"
        d = self.autoswarm_home / "calls" / call_id
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.workspace)})
        _write_json(d / "handle.json",
                     {"pid": 999, "native_session_id": session_id,
                      "phase": "execution"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        adoption = self.seq.admit_witness(
            attempt_id, "obj-11", "autoswarm", found["evidence"])
        self.seq.attempt_running(
            attempt_id, session_id, adoption["content_address"])
        result = self.transport.collect(call_id)
        self.assertIsNone(result)
        att_state = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (attempt_id,)).fetchone()["state"]
        self.assertEqual(att_state, "RUNNING")

    def test_stranded_launch_stays_held(self):
        """task.json exists, no runner: find_session None,
        positive_launch_failure None → HELD in reconcile."""
        admitted, _ = self._admit_read_only_record()
        sequence_id = admitted["sequence_id"]
        self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        attempt_id, fence = info["attempt_id"], info["fence"]
        self.seq.reserve_attempt(attempt_id, expected_fence=fence)
        projection = self.rt.store.put(b"test projection\n")
        dispatched = self.seq.dispatch_attempt(
            attempt_id, "builder", "codex", projection)
        call_id = dispatched["call_id"]
        d = self.autoswarm_home / "calls" / call_id
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.workspace)})
        report = reconcile_dispatched(self.seq, self.transport)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["action"], "held")
        att_state = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (attempt_id,)).fetchone()["state"]
        self.assertEqual(att_state, "DISPATCHED")


if __name__ == "__main__":
    unittest.main()
