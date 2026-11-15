"""The C4-M2 conformance suite: transform provenance, the role-transform
check, BY_ATTESTATION, packaged closures and artifact retention.

Registered proofs closed here:
- CH4-a1-transform-chain (INV-44): specification identity is exact bytes;
  prompt identity is the declared registered transform's output over those
  bytes; the U-12-calibrated ingestion transform (strict UTF-8, BOM kept,
  universal newlines, nothing appended or stripped) is executed as a real
  registered closure and RE-EXECUTED at admission (D-C3-2 grade), so one
  byte of drift anywhere in the chain changes an identity or fails closed.
- CH4-role-transform-check (INV-49): the child's RECEIVED prompt identity
  comes only from admitted obj-11 producer bytes; a mismatch is an
  authoritative FAIL on the repair path; missing producer evidence blocks
  conclusion; sibling/cross-attempt/stale-registration replays and
  dispatcher (obj-10) drift die at the seam.
- BY_ATTESTATION (D-52/D-53): your obj-37 carries durable identity,
  provenance and FULL scope binding (attempt + contract + candidate + D-47
  part); the runner incorporates an ADMITTED attestation into a canonical
  obj-19 BY_ATTESTATION result and can never synthesize one.
- D-C4-10 packaged closures: the manifest form executes exactly its
  content-addressed entries; the image form HALTs (no container engine is
  part of this runtime); native/python/packaged share one materialization
  discipline.
- CH4-artifact-retention (INV-58): live registration closures keep their
  leaves; reclamation is an explicit act re-derived at the moment of
  deletion; frozen records' specification inputs are never reclaimable;
  recovery REPORTS missing bound leaves for live records.
"""

import json
import tempfile
import unittest
from pathlib import Path

import support
from fake_verifier import (
    attested_receipt_bytes,
    bound_receipt_bytes,
    environment_bytes,
    transform_receipt_bytes,
)
from test_verification import human_record_payload
from autobot import ProductionVerifierRunner, Store
from autobot import transforms
from autobot import verification_receipts
from autobot.environment_probe import interpreter_identity
from autobot.models import AdmissionError, ConflictError, IntegrityHalt
from autobot.projection import project_prompt
from contract_library import (
    artifact_root,
    canonicalize,
    content_address,
    object_hash,
)

# A second registered implementation of the same transform id (registration
# REPLACEMENT): different exact script bytes, same behavior.
IDENTITY_V2_SCRIPT = support.LIVE_IDENTITY_TRANSFORM_SCRIPT + b"# v2\n"


def transform_v2_registration():
    closure = {"kind": "python",
               "script_sha256": content_address(IDENTITY_V2_SCRIPT),
               "interpreter_id": interpreter_identity(),
               "dependency_lock_sha256": content_address(support.LIVE_LOCK)}
    return {"schema_version": "v1", "kind": "TransformRegistration",
            "registration_id": "rg_" + "d9".rjust(32, "0"),
            "transform_id": "identity-projection", "version": "2.0.0",
            "closure": closure, "artifact_root": artifact_root(closure),
            "entrypoint": "transform.py", "execution_requirements": [],
            "registered_by": "you",
            "registered_utc": "2026-11-13T00:00:00.000Z"}


PACKAGED_DATA = b"packaged data file\n"


def packaged_manifest_payload(entrypoint="verify.py", script=None,
                              interpreter=True):
    script = script if script is not None else support.LIVE_PASS_SCRIPT
    manifest = {"schema_version": "v1", "kind": "PackageManifest",
                "entries": [
                    {"path": entrypoint,
                     "content_sha256": content_address(script)},
                    {"path": "pkg/data.txt",
                     "content_sha256": content_address(PACKAGED_DATA)},
                ],
                "entrypoint": entrypoint}
    if interpreter:
        manifest["interpreter_id"] = interpreter_identity()
    return manifest


def packaged_verifier_registration(rt):
    """A packaged VerifierRegistration whose manifest names the live PASS
    script as its entrypoint plus one data entry — every leaf seeded."""
    rt.store.put(support.LIVE_PASS_SCRIPT)
    rt.store.put(PACKAGED_DATA)
    manifest_address = rt.store.put(
        canonicalize(packaged_manifest_payload()))
    closure = {"kind": "packaged", "manifest_sha256": manifest_address}
    return {"schema_version": "v1", "kind": "VerifierRegistration",
            "registration_id": "rg_" + "e1".rjust(32, "0"),
            "verifier_id": "vector-checker", "version": "2.0.0",
            "closure": closure, "artifact_root": artifact_root(closure),
            "entrypoint": "verify.py", "execution_requirements": [],
            "registered_by": "you",
            "registered_utc": "2026-11-13T00:00:00.000Z"}


def packaged_transform_registration(rt):
    rt.store.put(support.LIVE_IDENTITY_TRANSFORM_SCRIPT)
    rt.store.put(PACKAGED_DATA)
    manifest_address = rt.store.put(canonicalize(packaged_manifest_payload(
        entrypoint="transform.py",
        script=support.LIVE_IDENTITY_TRANSFORM_SCRIPT)))
    closure = {"kind": "packaged", "manifest_sha256": manifest_address}
    return {"schema_version": "v1", "kind": "TransformRegistration",
            "registration_id": "rg_" + "e2".rjust(32, "0"),
            "transform_id": "identity-projection", "version": "2.0.0",
            "closure": closure, "artifact_root": artifact_root(closure),
            "entrypoint": "transform.py", "execution_requirements": [],
            "registered_by": "you",
            "registered_utc": "2026-11-13T00:00:00.000Z"}


class TransformExecutionTests(unittest.TestCase):
    """CH4-a1-transform-chain's byte vectors (U-12): the registered
    ingestion transform executed as the exact registered closure over the
    adversarial byte families the probe identified on the real provider."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "store")
        for transform_id in sorted(support.LIVE_TRANSFORM_SPECS):
            _suffix, script = support.LIVE_TRANSFORM_SPECS[transform_id]
            self.store.put(script)
        self.store.put(support.LIVE_LOCK)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, transform_id, data):
        return transforms.execute_transform(
            self.store, support.live_transform_registration(transform_id),
            data)

    def test_identity_projection_is_byte_identity(self):
        """The runtime's declared role transform: output bytes == input
        bytes, BOM and CRLF included, deterministically."""
        data = "﻿line one\r\nline two\r\n".encode("utf-8")
        out1, exec1 = self._run("identity-projection", data)
        out2, exec2 = self._run("identity-projection", data)
        self.assertEqual(out1, data)
        self.assertEqual(exec1["exit_code"], 0)
        self.assertEqual(exec1["output_sha256"], content_address(data))
        self.assertEqual(exec1["output_sha256"], exec2["output_sha256"])
        self.assertEqual(out2, data)

    def test_autoswarm_lf_input_is_a_fixed_point(self):
        """The v13/v14 evidence shape: LF-only, no BOM, final newline —
        ingestion returns the exact same bytes (prompt sha == file sha)."""
        data = support.VA_INPUT.encode("utf-8")
        out, execution = self._run("autoswarm-input-v1", data)
        self.assertEqual(out, data)
        self.assertEqual(execution["output_sha256"], content_address(data))

    def test_autoswarm_normalizes_newlines_and_keeps_the_bom(self):
        """The six monitor-prompt records' shape (PS 5.1 authoring): CRLF
        and lone CR fold to LF; the U+FEFF prefix is KEPT, exactly as the
        installed provider's strict-UTF-8 text ingestion behaves."""
        data = "﻿do the thing\r\nline2\rline3\n".encode("utf-8")
        out, execution = self._run("autoswarm-input-v1", data)
        self.assertEqual(
            out, "﻿do the thing\nline2\nline3\n".encode("utf-8"))
        self.assertEqual(execution["exit_code"], 0)
        self.assertNotEqual(execution["output_sha256"],
                            content_address(data))

    def test_autoswarm_strict_utf8_fails_closed(self):
        """Non-UTF-8 input (UTF-16 bytes, a stray latin-1 byte) is an
        EXECUTION FAILURE — never decoded by guesswork, never PASS/FAIL
        material (INCONCLUSIVE at the check)."""
        for bad in ("prompt text".encode("utf-16"), b"caf\xe9\n"):
            out, execution = self._run("autoswarm-input-v1", bad)
            self.assertIsNone(out)
            self.assertEqual(execution["exit_code"], 3)

    def test_one_byte_appended_or_stripped_changes_identity(self):
        """INV-44: nothing appends or strips — a trailing byte added or
        removed IS a different specification, a different output."""
        base = support.VA_INPUT.encode("utf-8")
        out_base, _ = self._run("autoswarm-input-v1", base)
        out_plus, _ = self._run("autoswarm-input-v1", base + b"\n")
        out_minus, _ = self._run("autoswarm-input-v1", base[:-1])
        identities = {content_address(out_base),
                      content_address(out_plus),
                      content_address(out_minus)}
        self.assertEqual(len(identities), 3)


class Chapter4M2TestCase(unittest.TestCase):
    """Shared plumbing: a runtime with V-A admitted (the fixture corpus),
    the LOCAL declaration, and one live-bound local record."""

    record_kwargs = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, self.va = support.admitted_runtime(self.dir,
                                                    clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.record_payload = support.local_record_payload(
            **self.record_kwargs)
        self.admitted = self.rt.registry.admit_payload(
            self.record_payload, submitted_by="planner")
        self.assertEqual(self.admitted["outcome"], "NEW_RECORD")
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _runner(self, **kwargs):
        return ProductionVerifierRunner(self.seq, self.rt.store, **kwargs)

    def _admit_variant(self, record):
        result = self.rt.registry.admit_payload(record,
                                                submitted_by="planner")
        self.assertEqual(result["outcome"], "NEW_RECORD")
        return result

    def _drive(self, admitted=None, prompt=support.VA_INPUT,
               projection=None):
        info = support.drive_to_evidence_complete(
            self.rt, admitted or self.admitted, prompt=prompt,
            projection=projection)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        return info

    def _sequence_state(self, sequence_id):
        return self.rt.db.conn.execute(
            "SELECT state, repairs_used FROM sequences WHERE sequence_id"
            " = ?", (sequence_id,)).fetchone()

    def _rows(self, attempt_id):
        return [dict(r) for r in self.rt.db.conn.execute(
            "SELECT * FROM attempt_verifications WHERE attempt_id = ?"
            " ORDER BY assertion_id", (attempt_id,)).fetchall()]

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def _entries(self):
        return [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq"
        ).fetchall()]

    def _env_for(self, attempt_id):
        state = self.seq.verification_state(attempt_id)
        if "environment_sha256" in state:
            return self.rt.store.get(state["environment_sha256"])
        env = environment_bytes({"sequence_id": state["sequence_id"],
                                 "attempt_id": state["attempt_id"],
                                 "version": state["version"],
                                 "fence": state["fence"]})
        self.seq.admit_environment_capture(attempt_id, env)
        return env


class TransformChainTests(Chapter4M2TestCase):
    """CH4-a1-transform-chain end to end: the record's exact input bytes,
    the registered calibrated transform, the admitted producer evidence."""

    def _calibrated_record(self, task_contract_id, objective,
                           input_name=None, input_bytes=None):
        record = support.local_record_payload(
            task_contract_id=task_contract_id, objective=objective)
        record["role_transforms"] = {"builder": "autoswarm-input-v1"}
        record["resolved_bindings"]["transform_registrations"] = [
            support.live_transform_hash("autoswarm-input-v1")]
        if input_bytes is not None:
            address = self.rt.store.put(input_bytes)
            record["specification_inputs"] = {
                "name_to_exact_byte_sha256": {input_name: address}}
        return record

    def test_calibrated_chain_passes_end_to_end(self):
        """LF-only input through the REAL autoswarm-input-v1 closure: the
        admission re-executes it, the admitted obj-11 prompt identity
        equals the derived output, t-s10 journals the check."""
        record = self._calibrated_record("tc_" + "a1c0" * 8,
                                         "A1 chain, calibrated transform")
        admitted = self._admit_variant(record)
        info = self._drive(admitted)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        self.seq.conclude_verification(info["attempt_id"], info["fence"])
        rows = {r["assertion_id"]: r for r in self._rows(info["attempt_id"])}
        row = rows["role-transform/builder"]
        self.assertEqual(row["outcome"], "PASS")
        self.assertEqual(row["verifier_id"], "autoswarm-input-v1")
        payload = json.loads(self.rt.store.get(
            row["receipt_sha256"]).decode("utf-8"))
        check = payload["transform_check"]
        input_address = content_address(support.VA_INPUT.encode("utf-8"))
        self.assertEqual(check, {"input_name": "chapter1-brief.md",
                                 "input_sha256": input_address,
                                 "expected_sha256": input_address,
                                 "observed_sha256": input_address})
        self.assertEqual(payload["assertion"]["part"],
                         {"record": self.rt.db.conn.execute(
                             "SELECT record_hash FROM sequences WHERE"
                             " sequence_id = ?",
                             (admitted["sequence_id"],)).fetchone()[
                             "record_hash"],
                          "part_kind": "ROLE_TRANSFORM",
                          "part_id": "builder"})
        t_s10 = [e for e in self._entries() if e["rule"] == "t-s10"][-1]
        self.assertEqual(
            t_s10["facts"]["role_transform_checks"],
            {"role-transform/builder": row["receipt_sha256"]})

    def test_ps51_authored_input_normalizes_and_passes(self):
        """The U-12 evidence shape end to end: a BOM + CRLF authored input
        file; the child received the BOM-kept LF-normalized text — exactly
        what the calibrated transform derives."""
        raw = "﻿Monitor the run.\r\nReport hourly.\r\n"
        record = self._calibrated_record(
            "tc_" + "a1c1" * 8, "A1 chain, PS 5.1 authored input",
            input_name="monitor-brief.md",
            input_bytes=raw.encode("utf-8"))
        admitted = self._admit_variant(record)
        received = "﻿Monitor the run.\nReport hourly.\n"
        info = self._drive(admitted, prompt=received)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        rows = {r["assertion_id"]: r for r in self._rows(info["attempt_id"])}
        payload = json.loads(self.rt.store.get(
            rows["role-transform/builder"]["receipt_sha256"]
        ).decode("utf-8"))
        check = payload["transform_check"]
        self.assertEqual(check["input_sha256"],
                         content_address(raw.encode("utf-8")))
        self.assertEqual(check["expected_sha256"],
                         content_address(received.encode("utf-8")))
        self.assertEqual(check["expected_sha256"], check["observed_sha256"])
        self.assertNotEqual(check["input_sha256"], check["expected_sha256"])

    def test_raw_crlf_received_prompt_fails_and_repairs(self):
        """INV-49's core arm: the child demonstrably received the RAW
        (un-ingested) bytes — a FAIL with transform_output_mismatch, on
        the ordinary repair path (t-a08 + t-s11), never a halt."""
        raw = "﻿Monitor the run.\r\nReport hourly.\r\n"
        record = self._calibrated_record(
            "tc_" + "a1c2" * 8, "A1 chain, raw bytes reached the child",
            input_name="monitor-brief.md",
            input_bytes=raw.encode("utf-8"))
        admitted = self._admit_variant(record)
        info = self._drive(admitted, prompt=raw)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        by_id = {r["assertion_id"]: r for r in outcome["results"]}
        self.assertEqual(by_id["role-transform/builder"]["outcome"], "FAIL")
        payload = json.loads(self.rt.store.get(
            by_id["role-transform/builder"]["receipt_address"]
        ).decode("utf-8"))
        self.assertEqual(payload["conclusion_basis"],
                         "transform_output_mismatch")
        self.assertNotEqual(payload["transform_check"]["observed_sha256"],
                            payload["transform_check"]["expected_sha256"])
        result = self.seq.conclude_verification(info["attempt_id"],
                                                info["fence"])
        self.assertEqual(result, {"attempt": "REJECTED",
                                  "sequence": "READY"})
        t_s11 = [e for e in self._entries() if e["rule"] == "t-s11"][-1]
        self.assertIn("role-transform/builder",
                      t_s11["facts"]["failed_assertion_ids"])

    def test_missing_producer_evidence_blocks_conclusion(self):
        """No admitted producer record carries the received prompt: the
        check is INCONCLUSIVE (missing_producer_evidence) — satisfying
        nothing, blocking conclusion, routed to t-s12's unknown arm."""
        info = self._drive(prompt=None)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertTrue(outcome["inconclusive"])
        rows = {r["assertion_id"]: r for r in self._rows(info["attempt_id"])}
        row = rows["role-transform/builder"]
        self.assertEqual(row["outcome"], "INCONCLUSIVE")
        payload = json.loads(self.rt.store.get(
            row["receipt_sha256"]).decode("utf-8"))
        self.assertEqual(payload["conclusion_basis"],
                         "missing_producer_evidence")
        self.assertIn("expected_sha256", payload["transform_check"])
        self.assertNotIn("observed_sha256", payload["transform_check"])
        with self.assertRaises(ConflictError):
            self.seq.conclude_verification(info["attempt_id"],
                                           info["fence"])
        self.seq.halt_verification(self.admitted["sequence_id"], "unknown")
        self.assertEqual(
            self._sequence_state(self.admitted["sequence_id"])["state"],
            "HALTED")


class RoleTransformSeamTests(Chapter4M2TestCase):
    """CH4-role-transform-check: every authority axis of the admission
    seam attacked independently."""

    def test_runner_claim_is_not_producer_evidence(self):
        """A receipt claiming an observed identity no admitted obj-11
        carries dies at the seam — 'this is the prompt I received' is not
        enough from the child, and not from the runner either."""
        info = self._drive(prompt=None)
        env = self._env_for(info["attempt_id"])
        forged = transform_receipt_bytes(self.seq, info["attempt_id"], env,
                                         outcome="PASS")
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "PASS", "verifier_runner",
                forged, environment=env)
        self.assertIn("producer evidence", str(ctx.exception))
        self.assertEqual(self._journal_count(), before)

    def test_forged_expected_dies_at_the_rederivation(self):
        """D-C3-2 grade: a FULLY consistent FAIL receipt whose expected
        output is not what the bound transform actually derives is refused
        by the admission's own re-execution — the one gate a pure
        bytes-consistency check cannot provide."""
        info = self._drive()
        env = self._env_for(info["attempt_id"])
        junk = self.rt.store.put(b"not the transform output\n")
        observed = "sha256:" + content_address(
            support.VA_INPUT.encode("utf-8")).split(":", 1)[1]
        forged = transform_receipt_bytes(
            self.seq, info["attempt_id"], env, outcome="FAIL",
            conclusion_basis="transform_output_mismatch",
            observed=observed,
            transform_check={"input_name": "chapter1-brief.md",
                             "input_sha256": content_address(
                                 support.VA_INPUT.encode("utf-8")),
                             "expected_sha256": junk,
                             "observed_sha256": observed},
            execution={"closure_kind": "python",
                       "entrypoint": "transform.py",
                       "exit_code": 0, "output_sha256": junk})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "FAIL", "verifier_runner",
                forged, environment=env)
        self.assertIn("re-derive", str(ctx.exception))

    def test_ambiguous_producer_evidence_is_refused(self):
        """Two admitted obj-11 records carrying DISTINCT prompt identities
        (admitted during the execution, as evidence always is): the seam
        refuses rather than choosing."""
        seq = self.seq
        seq.mark_ready(self.admitted["sequence_id"])
        info = seq.start_execution(self.admitted["sequence_id"])
        seq.reserve_attempt(info["attempt_id"],
                            expected_fence=info["fence"])
        projection = self.rt.store.put(b"projection m2 ambiguity\n")
        dispatched = seq.dispatch_attempt(info["attempt_id"], "builder",
                                          "fake", projection)
        start = seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"],
                                         prompt=support.VA_INPUT))
        seq.attempt_running(info["attempt_id"], "native-sess-9",
                            start["content_address"])
        seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"],
                                         prompt="another prompt\n"))
        seq.admit_witness(info["attempt_id"], "obj-12", "helios",
                          b"governance\n")
        end = seq.admit_witness(info["attempt_id"], "obj-13", "agent_clis",
                                b"transcript\n")
        seq.finish_attempt(info["attempt_id"], "builder", "success",
                           end["content_address"])
        support.mint_and_freeze(self.rt, info["attempt_id"], info["fence"])
        receipt = support.collect_receipt(self.rt, info["attempt_id"])
        seq.complete_evidence(info["attempt_id"], info["fence"], receipt)
        env = self._env_for(info["attempt_id"])
        claim = transform_receipt_bytes(self.seq, info["attempt_id"], env,
                                        outcome="PASS")
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "PASS", "verifier_runner",
                claim, environment=env)
        self.assertIn("DISTINCT", str(ctx.exception))

    def test_replay_across_attempts_is_inert(self):
        """A transform receipt minted for sibling A presented on B dies on
        the attempt identity (INV-18)."""
        info_a = self._drive()
        env_a = self._env_for(info_a["attempt_id"])
        receipt_a = transform_receipt_bytes(self.seq, info_a["attempt_id"],
                                            env_a, outcome="PASS")
        sibling = self._admit_variant(support.local_record_payload(
            task_contract_id="tc_" + "a1c3" * 8,
            objective="Replay target"))
        info_b = self._drive(sibling)
        env_b = self._env_for(info_b["attempt_id"])
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info_b["attempt_id"], "builder", "PASS", "verifier_runner",
                receipt_a, environment=env_b)
        self.assertIn("attempt identity", str(ctx.exception))

    def test_unbound_registration_is_refused(self):
        """A receipt binding a REAL admitted registration that is not the
        frozen record's bound one (a v2 replacement) dies on the
        registration equality (INV-52/INV-53)."""
        v2 = transform_v2_registration()
        self.rt.store.put(IDENTITY_V2_SCRIPT)
        self.rt.registry.admit_object(v2, submitted_by="you")
        info = self._drive()
        env = self._env_for(info["attempt_id"])
        forged = transform_receipt_bytes(
            self.seq, info["attempt_id"], env, outcome="PASS",
            registration={"object_sha256": object_hash(
                "TransformRegistration", v2),
                "registration_id": v2["registration_id"],
                "artifact_root": v2["artifact_root"]})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "PASS", "verifier_runner",
                forged, environment=env)
        self.assertIn("bound transform registration", str(ctx.exception))

    def test_role_gates(self):
        """An undeclared role is refused; a declared role that is not the
        DISPATCHED role is refused (the check verifies the child that
        actually ran); the minter and vocabulary gates hold."""
        info = self._drive()
        env = self._env_for(info["attempt_id"])
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info["attempt_id"], "reviewer", "PASS", "verifier_runner",
                b"junk", environment=env)
        self.assertIn("declares no transform", str(ctx.exception))
        with self.assertRaises(ConflictError):
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "PASS", "you", b"junk")
        with self.assertRaises(ConflictError):
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "MAYBE", "verifier_runner",
                b"junk")
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["verifier_llm_v1"],
                                      submitted_by="you")
        human = self.rt.registry.admit_payload(human_record_payload(),
                                               submitted_by="planner")
        info_h = self._drive(human)
        env_h = self._env_for(info_h["attempt_id"])
        with self.assertRaises(ConflictError) as ctx:
            # reviewer IS declared by the record; builder was dispatched
            self.seq.register_transform_check(
                info_h["attempt_id"], "reviewer", "PASS",
                "verifier_runner", b"junk", environment=env_h)
        self.assertIn("dispatched role", str(ctx.exception))

    def test_cross_kind_misuse_fails_closed(self):
        """D-47: a ROLE_TRANSFORM row key is not a declared assertion (the
        assertion seam refuses it), and an ASSERTION-part receipt dies at
        the transform seam's part gate."""
        info = self._drive()
        env = self._env_for(info["attempt_id"])
        with self.assertRaises(ConflictError):
            self.seq.register_verifier_result(
                info["attempt_id"], "role-transform/builder", "PASS",
                "verifier_runner", b"junk", environment=env)
        assertion_receipt = bound_receipt_bytes(
            self.seq, info["attempt_id"], "a2", "PASS", env)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_transform_check(
                info["attempt_id"], "builder", "PASS", "verifier_runner",
                assertion_receipt, environment=env)
        self.assertIn("part_kind", str(ctx.exception))

    def test_conclusion_requires_the_admitted_check(self):
        """INV-49 at conclusion: every assertion PASS and still no
        conclusion without the role-transform row; admitting it (identical
        re-admission idempotent) unlocks t-s10."""
        info = self._drive()
        env = self._env_for(info["attempt_id"])
        for assertion_id in ("a1", "a2"):
            self.seq.register_verifier_result(
                info["attempt_id"], assertion_id, "PASS",
                "verifier_runner",
                bound_receipt_bytes(self.seq, info["attempt_id"],
                                    assertion_id, "PASS", env),
                environment=env)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.conclude_verification(info["attempt_id"],
                                           info["fence"])
        self.assertIn("role-transform", str(ctx.exception))
        receipt = transform_receipt_bytes(self.seq, info["attempt_id"],
                                          env, outcome="PASS")
        first = self.seq.register_transform_check(
            info["attempt_id"], "builder", "PASS", "verifier_runner",
            receipt, environment=env)
        self.assertFalse(first["duplicate"])
        again = self.seq.register_transform_check(
            info["attempt_id"], "builder", "PASS", "verifier_runner",
            receipt, environment=env)
        self.assertTrue(again["duplicate"])
        result = self.seq.conclude_verification(info["attempt_id"],
                                                info["fence"])
        self.assertEqual(result, {"attempt": "VERIFIED",
                                  "sequence": "VERIFIED"})
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


class ProjectionProvenanceTests(Chapter4M2TestCase):
    """obj-10 transform_provenance (INV-44): the dispatcher's CLAIM —
    carried, schema-validated, and re-derived against, never trusted."""

    def _provenance(self, output=None):
        input_address = content_address(support.VA_INPUT.encode("utf-8"))
        return {"transform_id": "identity-projection",
                "registration":
                    support.live_transform_hash("identity-projection"),
                "input_name": "chapter1-brief.md",
                "input_sha256": input_address,
                "output_sha256": output or input_address}

    def _drive_with_projection(self, record, admitted, provenance):
        seq = self.seq
        seq.mark_ready(admitted["sequence_id"])
        info = seq.start_execution(admitted["sequence_id"])
        seq.reserve_attempt(info["attempt_id"],
                            expected_fence=info["fence"])
        identity = {"sequence_id": admitted["sequence_id"],
                    "attempt_id": info["attempt_id"],
                    "fence": info["fence"],
                    "attempt_contract_hash":
                        info["attempt_contract_hash"]}
        _data, address = project_prompt(
            self.rt.store, record, "builder", identity,
            {"author": "you", "guidance": "C4-M2 provenance drive"},
            transform_provenance=provenance)
        dispatched = seq.dispatch_attempt(info["attempt_id"], "builder",
                                          "fake", address)
        start = seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"],
                                         prompt=support.VA_INPUT))
        seq.attempt_running(info["attempt_id"], "native-sess-9",
                            start["content_address"])
        seq.admit_witness(info["attempt_id"], "obj-12", "helios",
                          b"governance\n")
        end = seq.admit_witness(info["attempt_id"], "obj-13", "agent_clis",
                                b"transcript\n")
        seq.finish_attempt(info["attempt_id"], "builder", "success",
                           end["content_address"])
        support.mint_and_freeze(self.rt, info["attempt_id"], info["fence"])
        receipt = support.collect_receipt(self.rt, info["attempt_id"])
        seq.complete_evidence(info["attempt_id"], info["fence"], receipt)
        return info

    def test_valid_provenance_is_carried_and_verified(self):
        record = support.local_record_payload(
            task_contract_id="tc_" + "0b10" * 8,
            objective="obj-10 provenance, honest dispatcher")
        admitted = self._admit_variant(record)
        info = self._drive_with_projection(record, admitted,
                                           self._provenance())
        call = self.rt.db.conn.execute(
            "SELECT projection_address FROM attempt_calls"
            " WHERE attempt_id = ?", (info["attempt_id"],)).fetchone()
        projection = json.loads(self.rt.store.get(
            call["projection_address"]).decode("utf-8"))
        self.assertEqual(projection["transform_provenance"],
                         self._provenance())
        self.assertEqual(projection["role_transform"],
                         "identity-projection")
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        result = self.seq.conclude_verification(info["attempt_id"],
                                                info["fence"])
        self.assertEqual(result["attempt"], "VERIFIED")

    def test_dispatcher_drift_dies_at_the_seam(self):
        """The projection claims a transform output the registered
        transform does not produce: the check's admission refuses — the
        claim never overrides the re-derivation."""
        record = support.local_record_payload(
            task_contract_id="tc_" + "0b11" * 8,
            objective="obj-10 provenance, drifted dispatcher")
        admitted = self._admit_variant(record)
        forged = self._provenance(output="sha256:" + "9" * 64)
        info = self._drive_with_projection(record, admitted, forged)
        with self.assertRaises(ConflictError) as ctx:
            self._runner().verify_attempt(info["attempt_id"])
        self.assertIn("projection claims", str(ctx.exception))
        self.assertNotIn(
            "role-transform/builder",
            [r["assertion_id"] for r in self._rows(info["attempt_id"])])

    def test_projection_provenance_validation(self):
        """project_prompt refuses a provenance for an unbound role or a
        mismatched transform id; the schema refuses a malformed block."""
        record = support.local_record_payload()
        identity = {"sequence_id": "sq_" + "0" * 32,
                    "attempt_id": "at_" + "0" * 32, "fence": 1,
                    "attempt_contract_hash": "sha256:" + "0" * 64}
        context = {"author": "you", "guidance": "negatives"}
        wrong_id = dict(self._provenance())
        wrong_id["transform_id"] = "autoswarm-input-v1"
        with self.assertRaises(ConflictError):
            project_prompt(self.rt.store, record, "builder", identity,
                           context, transform_provenance=wrong_id)
        no_transform = json.loads(json.dumps(record))
        no_transform["role_transforms"] = {}
        with self.assertRaises(ConflictError):
            project_prompt(self.rt.store, no_transform, "builder",
                           identity, context,
                           transform_provenance=self._provenance())
        malformed = dict(self._provenance())
        del malformed["output_sha256"]
        with self.assertRaises(ConflictError) as ctx:
            project_prompt(self.rt.store, record, "builder", identity,
                           context, transform_provenance=malformed)
        self.assertIn("schema", str(ctx.exception))


class AttestedResultTests(Chapter4M2TestCase):
    """BY_ATTESTATION (D-52/D-53): the admission battery over the upgraded
    obj-37 bindings and the runner's incorporation."""

    def setUp(self):
        super().setUp()
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["verifier_llm_v1"],
                                      submitted_by="you")
        self.human = self.rt.registry.admit_payload(
            human_record_payload(), submitted_by="planner")
        self.assertEqual(self.human["outcome"], "NEW_RECORD")

    def _attestation(self, attempt_id, assertion_id="a3", **overrides):
        state = self.seq.verification_state(attempt_id)
        value = {"schema_version": "v1",
                 "kind": "ManualAttestation.ASSERTION",
                 "attested_by": "you", "assertion_id": assertion_id,
                 "attempt_id": attempt_id,
                 "attempt_contract_hash":
                     state["attempt_contract_hash"],
                 "candidate_receipt_sha256":
                     state["candidate_receipt_sha256"],
                 "part": {"record": state["record_hash"],
                          "part_kind": "ASSERTION",
                          "part_id": assertion_id},
                 "attests": "PASS"}
        value.update(overrides)
        return value

    def test_full_d53_binding_battery(self):
        """Wrong candidate, missing part, wrong part record, cross-kind
        part, wrong part selector — each refused at the attestation
        admission; a DETERMINISTIC assertion is never attestable."""
        info = self._drive(self.human)
        aid = info["attempt_id"]
        good = self._attestation(aid)
        state = self.seq.verification_state(aid)
        negatives = [
            self._attestation(aid,
                              candidate_receipt_sha256="sha256:" + "2" * 64),
            self._attestation(aid, part=None),
            self._attestation(aid, part={"record": "sha256:" + "3" * 64,
                                         "part_kind": "ASSERTION",
                                         "part_id": "a3"}),
            self._attestation(aid, part={"record": state["record_hash"],
                                         "part_kind": "ROLE_TRANSFORM",
                                         "part_id": "a3"}),
            self._attestation(aid, part={"record": state["record_hash"],
                                         "part_kind": "ASSERTION",
                                         "part_id": "a2"}),
        ]
        missing = self._attestation(aid)
        del missing["candidate_receipt_sha256"]
        negatives.append(missing)
        for bad in negatives:
            with self.assertRaises(AdmissionError):
                self.seq.attest_assertion(aid, "a3", bad)
        with self.assertRaises(ConflictError):
            self.seq.attest_assertion(aid, "a2",
                                      self._attestation(aid, "a2"))
        admitted = self.seq.attest_assertion(aid, "a3", good)
        self.assertFalse(admitted["duplicate"])
        self.assertTrue(self.rt.store.has(admitted["attestation_address"]))
        row = self.rt.db.conn.execute(
            "SELECT * FROM attempt_attestations WHERE attempt_id = ?",
            (aid,)).fetchone()
        self.assertEqual(row["content_sha256"],
                         admitted["attestation_address"])
        # the admitted attestation is NOT yet a result row
        self.assertEqual(self._rows(aid), [])

    def test_incorporation_binds_the_admitted_attestation(self):
        """register_attested_result admits only the canonical obj-19
        BY_ATTESTATION arm bound to the ADMITTED obj-37 — no attestation,
        wrong attestation digest, smuggled execution, wrong minter and
        wrong mode all die; the row's authority principal is you."""
        info = self._drive(self.human)
        aid = info["attempt_id"]
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_attested_result(
                aid, "a3", "verifier_runner", b"junk")
        self.assertIn("admitted attestation", str(ctx.exception))
        admitted = self.seq.attest_assertion(aid, "a3",
                                             self._attestation(aid))
        address = admitted["attestation_address"]
        with self.assertRaises(ConflictError):
            self.seq.register_attested_result(
                aid, "a3", "you",
                attested_receipt_bytes(self.seq, aid, "a3", address))
        with self.assertRaises(ConflictError):
            self.seq.register_attested_result(
                aid, "a2", "verifier_runner",
                attested_receipt_bytes(self.seq, aid, "a2", address))
        forged = attested_receipt_bytes(self.seq, aid, "a3",
                                        "sha256:" + "4" * 64)
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_attested_result(aid, "a3",
                                              "verifier_runner", forged)
        self.assertIn("attestation", str(ctx.exception))
        smuggled = attested_receipt_bytes(
            self.seq, aid, "a3", address,
            execution={"closure_kind": "python", "entrypoint": "x.py",
                       "exit_code": 0,
                       "output_sha256": "sha256:" + "5" * 64})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_attested_result(aid, "a3",
                                              "verifier_runner", smuggled)
        self.assertIn("schema", str(ctx.exception))
        result = self.seq.register_attested_result(
            aid, "a3", "verifier_runner",
            attested_receipt_bytes(self.seq, aid, "a3", address))
        self.assertFalse(result["duplicate"])
        again = self.seq.register_attested_result(
            aid, "a3", "verifier_runner",
            attested_receipt_bytes(self.seq, aid, "a3", address))
        self.assertTrue(again["duplicate"])
        rows = {r["assertion_id"]: r for r in self._rows(aid)}
        row = rows["a3"]
        self.assertEqual((row["mode"], row["outcome"],
                          row["by_principal"], row["environment_sha256"]),
                         ("HUMAN_ATTESTED", "ATTESTED", "you", None))
        payload = json.loads(self.rt.store.get(
            row["receipt_sha256"]).decode("utf-8"))
        self.assertEqual(payload["result_source"], "BY_ATTESTATION")
        self.assertEqual(payload["attestation_sha256"], address)
        self.assertNotIn("registration", payload)
        self.assertNotIn("execution", payload)
        self.assertNotIn("environment_sha256", payload)

    def test_runner_incorporates_and_concludes_then_recovery_pairs(self):
        """The production runner gives the admitted obj-37 its canonical
        receipt beside the transform check and the executed assertions;
        conclusion reaches VERIFIED; recovery validates the pairing and
        halts when the attestation row vanishes or the receipt's bindings
        are forged."""
        info = self._drive(self.human)
        aid = info["attempt_id"]
        self.seq.attest_assertion(aid, "a3", self._attestation(aid))
        outcome = self._runner().verify_attempt(aid)
        self.assertFalse(outcome["inconclusive"])
        by_id = {r["assertion_id"]: r for r in outcome["results"]}
        self.assertEqual(by_id["a3"]["outcome"], "ATTESTED")
        result = self.seq.conclude_verification(aid, info["fence"])
        self.assertEqual(result["attempt"], "VERIFIED")
        entries = self._entries()
        t_s10 = [e for e in entries if e["rule"] == "t-s10"][-1]
        self.assertEqual(t_s10["facts"]["attested_assertion_ids"], ["a3"])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        att_row = self.rt.db.conn.execute(
            "SELECT * FROM attempt_attestations WHERE attempt_id = ?",
            (aid,)).fetchone()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_attestations WHERE"
                       " admission_id = ?", (att_row["admission_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO attempt_attestations (admission_id,"
                " attempt_id, assertion_id, content_sha256, admitted_utc)"
                " VALUES (?, ?, ?, ?, ?)",
                (att_row["admission_id"], att_row["attempt_id"],
                 att_row["assertion_id"], att_row["content_sha256"],
                 att_row["admitted_utc"]))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_verifications SET"
                       " environment_sha256 = ? WHERE attempt_id = ? AND"
                       " assertion_id = 'a3'",
                       ("sha256:" + "6" * 64, aid))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


class PackagedClosureTests(Chapter4M2TestCase):
    """D-C4-10: the packaged/manifest form executes exactly its
    content-addressed entries under one shared materialization discipline;
    the grammar fails closed; the image form stays a HALT."""

    def test_packaged_verifier_parity_end_to_end(self):
        packaged = packaged_verifier_registration(self.rt)
        self.rt.registry.admit_object(packaged, submitted_by="you")
        record = support.local_record_payload(
            task_contract_id="tc_" + "9ac0" * 8,
            objective="Packaged verifier parity")
        record["resolved_bindings"]["verifier_registrations"] = sorted((
            support.live_registration_hash("registry-checker", "PASS"),
            object_hash("VerifierRegistration", packaged)))
        admitted = self._admit_variant(record)
        info = self._drive(admitted)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        result = self.seq.conclude_verification(info["attempt_id"],
                                                info["fence"])
        self.assertEqual(result["attempt"], "VERIFIED")
        rows = {r["assertion_id"]: r for r in self._rows(info["attempt_id"])}
        payloads = {a_id: json.loads(self.rt.store.get(
            row["receipt_sha256"]).decode("utf-8"))
            for a_id, row in rows.items()}
        # parity: python (a1) and packaged (a2) rode the SAME protocol,
        # the same binding members, the same outcome discipline.
        self.assertEqual(payloads["a1"]["execution"]["closure_kind"],
                         "python")
        self.assertEqual(payloads["a2"]["execution"]["closure_kind"],
                         "packaged")
        for payload in payloads.values():
            self.assertEqual(payload["outcome"],
                             "PASS" if payload["kind"] ==
                             "VerificationReceipt" else payload["outcome"])
            self.assertEqual(payload["result_source"], "BY_VERIFIER")
        output = self.rt.store.get(
            payloads["a2"]["execution"]["output_sha256"])
        self.assertIn(b"autobot_verifier_result", output)

    def test_packaged_transform_checks_end_to_end(self):
        packaged = packaged_transform_registration(self.rt)
        self.rt.registry.admit_object(packaged, submitted_by="you")
        record = support.local_record_payload(
            task_contract_id="tc_" + "9ac1" * 8,
            objective="Packaged transform parity")
        record["resolved_bindings"]["transform_registrations"] = [
            object_hash("TransformRegistration", packaged)]
        admitted = self._admit_variant(record)
        info = self._drive(admitted)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        rows = {r["assertion_id"]: r for r in self._rows(info["attempt_id"])}
        payload = json.loads(self.rt.store.get(
            rows["role-transform/builder"]["receipt_sha256"]
        ).decode("utf-8"))
        self.assertEqual(payload["execution"]["closure_kind"], "packaged")
        self.assertEqual(payload["outcome"], "PASS")

    def test_manifest_grammar_fails_closed(self):
        store = self.rt.store

        def refused(manifest_mutation=None, registration_mutation=None,
                    marker=None):
            manifest = packaged_manifest_payload()
            if manifest_mutation is not None:
                manifest_mutation(manifest)
            address = store.put(canonicalize(manifest))
            closure = {"kind": "packaged", "manifest_sha256": address}
            registration = {"registration_id": "rg_" + "e9".rjust(32, "0"),
                            "entrypoint": manifest.get("entrypoint",
                                                       "verify.py"),
                            "closure": closure,
                            "artifact_root": artifact_root(closure)}
            if registration_mutation is not None:
                registration_mutation(registration)
            with self.assertRaises(IntegrityHalt) as ctx:
                transforms.resolve_closure(store, registration)
            if marker is not None:
                self.assertIn(marker, str(ctx.exception))

        store.put(support.LIVE_PASS_SCRIPT)
        store.put(PACKAGED_DATA)
        refused(lambda m: m["entries"].append(
            {"path": "../escape.py",
             "content_sha256": content_address(b"x")}),
            marker="malformed")
        refused(lambda m: m["entries"].append(
            {"path": "a\\b.py", "content_sha256": content_address(b"x")}),
            marker="malformed")
        refused(lambda m: m["entries"].append(
            {"path": "/abs.py", "content_sha256": content_address(b"x")}),
            marker="malformed")
        refused(lambda m: m["entries"].append(dict(m["entries"][0])),
                marker="repeats")
        refused(lambda m: m.update(entrypoint="missing.py"),
                marker="not among")
        refused(lambda m: m.update(kind="Manifest"), marker="v1")
        refused(registration_mutation=lambda r: r.update(
            entrypoint="other.py"), marker="disagree")
        refused(lambda m: m.update(
            interpreter_id="cpython-0.0.1-nowhere-x86_64"),
            marker="interpreter")
        # a missing entry leaf: manifest resolves, the leaf does not
        ghost = packaged_manifest_payload()
        ghost["entries"][1] = {"path": "pkg/ghost.bin",
                               "content_sha256": "sha256:" + "7" * 64}
        address = store.put(canonicalize(ghost))
        closure = {"kind": "packaged", "manifest_sha256": address}
        registration = {"registration_id": "rg_" + "ea".rjust(32, "0"),
                        "entrypoint": "verify.py", "closure": closure,
                        "artifact_root": artifact_root(closure)}
        with self.assertRaises(IntegrityHalt):
            transforms.resolve_closure(store, registration)
        # non-JSON manifest bytes
        junk_address = store.put(b"\xff\xfe not a manifest")
        closure = {"kind": "packaged", "manifest_sha256": junk_address}
        registration = {"registration_id": "rg_" + "eb".rjust(32, "0"),
                        "entrypoint": "verify.py", "closure": closure,
                        "artifact_root": artifact_root(closure)}
        with self.assertRaises(IntegrityHalt):
            transforms.resolve_closure(store, registration)

    def test_materialization_parity_across_closure_kinds(self):
        """One discipline: python = [interpreter, -I, script]; native =
        [entry]; packaged = entries at their relative paths with the
        pinned-interpreter argv (or direct when unpinned)."""
        import sys
        store = self.rt.store
        store.put(support.LIVE_PASS_SCRIPT)
        store.put(support.LIVE_LOCK)
        store.put(PACKAGED_DATA)
        run_dir = str(self.dir / "mat-python")
        python_closure = {"kind": "python",
                          "script_sha256": content_address(
                              support.LIVE_PASS_SCRIPT),
                          "interpreter_id": interpreter_identity(),
                          "dependency_lock_sha256": content_address(
                              support.LIVE_LOCK)}
        Path(run_dir).mkdir()
        argv = transforms.materialize_closure(store, python_closure,
                                              run_dir, "verify.py")
        self.assertEqual(argv[:2], [sys.executable, "-I"])
        self.assertTrue(argv[2].endswith("verify.py"))
        native_dir = str(self.dir / "mat-native")
        Path(native_dir).mkdir()
        native_closure = {"kind": "native",
                          "executable_sha256": content_address(
                              support.LIVE_PASS_SCRIPT)}
        argv = transforms.materialize_closure(store, native_closure,
                                              native_dir, "tool.exe")
        self.assertEqual(len(argv), 1)
        self.assertTrue(argv[0].endswith("tool.exe"))
        packaged = packaged_verifier_registration(self.rt)
        resolved = transforms.resolve_closure(store, packaged)
        pkg_dir = str(self.dir / "mat-packaged")
        Path(pkg_dir).mkdir()
        argv = transforms.materialize_closure(
            store, resolved, pkg_dir, resolved["package"]["entrypoint"])
        self.assertEqual(argv[:2], [sys.executable, "-I"])
        self.assertTrue(argv[2].endswith("verify.py"))
        data_file = Path(pkg_dir) / "pkg" / "data.txt"
        self.assertEqual(data_file.read_bytes(), PACKAGED_DATA)
        with self.assertRaises(ConflictError):
            transforms.materialize_closure(store, resolved, pkg_dir,
                                           "../escape.py")


class RetentionTests(Chapter4M2TestCase):
    """CH4-artifact-retention (INV-58): retention roots over live
    sequences, the explicit re-derived reclaim, replacement semantics,
    the recovery report."""

    def test_live_roots_refuse_reclaim_across_states(self):
        script_address = content_address(support.LIVE_PASS_SCRIPT)
        lock_address = content_address(support.LIVE_LOCK)
        roots = self.seq.retention_roots()
        self.assertIn(script_address, roots)
        self.assertIn(self.admitted["sequence_id"], roots[script_address])
        self.assertIn(lock_address, roots)
        # DECLARED, READY, EXECUTING..VERIFYING: all live, all retained.
        outcome = self.seq.reclaim([script_address])
        self.assertEqual(outcome["removed"], [])
        self.assertIn("live registration closure leaf",
                      outcome["retained"][script_address])
        info = self._drive()
        outcome = self.seq.reclaim([script_address, lock_address])
        self.assertEqual(outcome["removed"], [])
        self.assertEqual(len(outcome["retained"]), 2)
        # the leaves are still there and dispatch still works
        verified = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(verified["inconclusive"])

    def test_ledger_references_and_frozen_inputs_never_reclaimed(self):
        info = self._drive()
        self._runner().verify_attempt(info["attempt_id"])
        row = self._rows(info["attempt_id"])[0]
        input_address = content_address(support.VA_INPUT.encode("utf-8"))
        outcome = self.seq.reclaim([row["receipt_sha256"], input_address])
        self.assertEqual(outcome["removed"], [])
        self.assertIn("ledger column",
                      outcome["retained"][row["receipt_sha256"]])
        self.assertIn("specification input",
                      outcome["retained"][input_address])
        self.assertTrue(self.rt.store.has(input_address))

    def test_reclaim_removes_only_unreferenced_bytes(self):
        stray = self.rt.store.put(b"orphaned closure bytes nothing binds\n")
        outcome = self.seq.reclaim([stray])
        self.assertEqual(outcome["removed"], [stray])
        self.assertFalse(self.rt.store.has(stray))
        entries = self._entries()
        reclaim_entries = [e for e in entries
                           if e["rule"] == "ch4:reclaim"]
        self.assertEqual(len(reclaim_entries), 1)
        self.assertEqual(reclaim_entries[0]["facts"]["removed"], [stray])
        again = self.seq.reclaim([stray])
        self.assertEqual(again["removed"], [])
        self.assertIn("no bytes", again["retained"][stray])

    def test_replacement_frees_only_after_terminality(self):
        """R2 admitted does not free R1 while any sequence binds it live;
        a leaf becomes eligible only once EVERY binding sequence is
        terminal — proven over the real READ_ONLY completion path — and
        reclaim recomputes at the moment of deletion (a stale pre-scan
        protects nothing)."""
        v2 = transform_v2_registration()
        v2_script = content_address(IDENTITY_V2_SCRIPT)
        self.rt.store.put(IDENTITY_V2_SCRIPT)
        # the stale pre-scan: v2's script binds nothing yet
        roots_before = self.seq.retention_roots()
        self.assertNotIn(v2_script, roots_before)
        self.rt.registry.admit_object(v2, submitted_by="you")
        record = support.local_record_payload(
            task_contract_id="tc_" + "be70" * 8,
            objective="Replacement retention",
            finalization_class="READ_ONLY",
            finalization_requirements=[])
        record["resolved_bindings"]["transform_registrations"] = [
            object_hash("TransformRegistration", v2)]
        admitted = self._admit_variant(record)
        # the stale list would have allowed deletion; the recompute refuses
        outcome = self.seq.reclaim([v2_script])
        self.assertEqual(outcome["removed"], [])
        self.assertIn(admitted["sequence_id"],
                      outcome["retained"][v2_script])
        info = self._drive(admitted)
        verified = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(verified["inconclusive"])
        self.seq.conclude_verification(info["attempt_id"], info["fence"])
        self.seq.accept_verified(admitted["sequence_id"], info["fence"])
        self.seq.release_dependencies(admitted["sequence_id"])
        self.seq.complete_sequence(admitted["sequence_id"])
        self.assertEqual(
            self._sequence_state(admitted["sequence_id"])["state"],
            "COMPLETED")
        # every sequence binding v2 is terminal: eligible now
        outcome = self.seq.reclaim([v2_script])
        self.assertEqual(outcome["removed"], [v2_script])
        self.assertFalse(self.rt.store.has(v2_script))
        # the v1 identity script and the shared lock stay live (bound by
        # the non-terminal local record and the live verifiers)
        v1_script = content_address(
            support.LIVE_TRANSFORM_SPECS["identity-projection"][1])
        lock = content_address(support.LIVE_LOCK)
        outcome = self.seq.reclaim([v1_script, lock])
        self.assertEqual(outcome["removed"], [])

    def test_recovery_reports_missing_bound_leaves(self):
        """The V-A fixture record's registrations carry no executable
        bytes: recovery REPORTS every missing bound leaf for the live
        fixture sequence — never a halt (D-C4-9) — and reports nothing
        for the live-bound record."""
        report = self.seq.recover()
        missing = report["retention"]["missing_bound_leaves"]
        self.assertTrue(missing)
        sequences = {entry["sequence_id"] for entry in missing}
        self.assertIn(self.va["sequence_id"], sequences)
        self.assertNotIn(self.admitted["sequence_id"], sequences)
        for entry in missing:
            self.assertFalse(self.rt.store.has(entry["leaf"]))


if __name__ == "__main__":
    unittest.main()
