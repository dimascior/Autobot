"""TI-1 WP5: the runtime's real boundaries, probed.

Three places where the runtime meets something it does not own, each held
to what is actually there rather than to a convenient stand-in:

1. The command-governance record family (obj-12). The shared drive path
   used to admit prose, so no driven test exercised the recognizer. It now
   admits the decision record and the result record of one governed
   command in the RECOGNIZED fact family, and this module asserts the
   facts extracted from exactly those bytes. It also holds the same two
   records in the PRODUCER's real shape (key sets, key order and byte
   framing as the Helios command gate writes them; every value synthetic).
   Finding F-TI-5 (CLOSED): the producer frames its records with a UTF-8
   byte order mark and spells its decision ``verdict``; the recognizer
   now reads BOM-framed bytes via ``utf-8-sig`` and recognizes ``verdict``
   as an alternative spelling for the ``decision`` fact (D-C3-5/6/8).

2. The filesystem under closure materialization (F-TI-6, fixed). A
   packaged manifest names entries by string; Windows resolves strings
   that differ only by case, a trailing dot or space, or a stream suffix
   to ONE file, and resolves a drive-letter segment outside the run
   directory - so the bound entrypoint could execute another entry's
   bytes. The closure-path grammar now names the same file on every
   filesystem or the manifest is refused before anything executes; the
   production grammar and auditor2's independent restatement of it are
   compared over planted and seeded random paths.

3. The D-56 constraint vocabulary versus what obj-18 captures: the
   interpreter's and each tool's ``executable_sha256`` is captured
   evidence, and no constraint can name it. Documented, not changed.
"""

import hashlib
import json
import os
import random
import re
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

import auditor2
import support
from test_independent_state import admit, base_runtime
from autobot import Collector, Store, transforms, witness_semantics
from autobot import environment_probe
from autobot.environment_probe import interpreter_identity
from autobot.models import ConflictError, IntegrityHalt
from autobot.verification import environment_satisfies
from contract_library import artifact_root, content_address

SEED = 20261114

# The key sets of the two record kinds, as observed on the live producer
# (the operator's own governed commands; gate 20261113-ti1-017). Names
# only: the constructed producer-shaped records in support.py must keep
# exactly these.
DECISION_KEYS = (
    "command_sha256", "correlation_id", "integrity_status", "reason",
    "session_id", "tier", "timestamp_utc", "tool_use_id", "verdict",
)
RESULT_KEYS = (
    "capability_escalated", "capability_flags", "classifier_reason_codes",
    "command", "command_sha256", "correlation_id", "cwd", "detected_tier",
    "detected_tier_name", "duration_ms", "enforcement_surface",
    "executed_utc", "exit_code", "exit_code_source", "fields_found",
    "fields_missing", "gate_file", "hook_event", "hook_versions",
    "interrupted", "matched_pattern", "output_bytes", "output_preview",
    "policy_hash", "segments_declared", "segments_detected",
    "segments_match", "session_continuity_status", "session_id",
    "settings_integrity_after", "success", "tool_use_id",
    "watched_path_diffs", "write_indicator_matched",
)

# What the recognizer must extract from the drive path's two records.
IDENTITY_FACTS = {
    "session_id": support.GOVERNANCE_SESSION,
    "tool_use_id": support.GOVERNANCE_TOOL_USE,
    "correlation_id": support.GOVERNANCE_CORRELATION,
    "command_sha256": support.governance_command_sha256(),
}
DECISION_FACTS = dict(IDENTITY_FACTS, decision="ALLOW",
                      hook_event="PreToolUse")
RESULT_FACTS = dict(IDENTITY_FACTS, hook_event="PostToolUse", success=True,
                    executed_utc="2026-11-09T12:00:02.000Z")


def setUpModule():
    sys.stderr.write("test_real_boundary: SEED=%d\n" % SEED)


def frozen_attempt(rt, objective, governance):
    """One attempt driven to CANDIDATE_FROZEN whose obj-12 witnesses are
    exactly ``governance`` (byte strings, admitted in order)."""
    seq = rt.sequencer
    admitted = admit(rt, support.local_record_payload(objective=objective))
    seq.mark_ready(admitted["sequence_id"])
    info = seq.start_execution(admitted["sequence_id"])
    attempt_id, fence = info["attempt_id"], info["fence"]
    seq.reserve_attempt(attempt_id, fence)
    dispatched = seq.dispatch_attempt(
        attempt_id, "builder", "fake",
        rt.store.put(("projection: %s\n" % objective).encode("utf-8")))
    start = seq.admit_witness(
        attempt_id, "obj-11", "autoswarm",
        support.session_record_bytes(dispatched["call_id"]))
    seq.attempt_running(attempt_id, support.GOVERNANCE_SESSION,
                        start["content_address"])
    for data in governance:
        seq.admit_witness(attempt_id, "obj-12", "helios", data)
    end = seq.admit_witness(
        attempt_id, "obj-13", "agent_clis",
        ("transcript: %s\n" % objective).encode("utf-8"))
    seq.finish_attempt(attempt_id, "builder", "success",
                       end["content_address"])
    support.mint_and_freeze(rt, attempt_id, fence)
    return {"attempt_id": attempt_id, "fence": fence}


def receipt_of(rt, attempt_id):
    """(completeness, payload, joins by content address) of the attempt's
    admitted obj-15."""
    row = rt.db.conn.execute(
        "SELECT content_sha256, completeness FROM attempt_receipts"
        " WHERE attempt_id = ?", (attempt_id,)).fetchone()
    payload = json.loads(rt.store.get(row["content_sha256"]).decode("utf-8"))
    joins = dict((join["content_sha256"], join)
                 for join in payload["causal_joins"])
    return row["completeness"], payload, joins


class GovernanceWitnessTests(unittest.TestCase):
    """WP5: the drive path's governance evidence carries recognized facts,
    and they are what the receipt joins on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, _va = base_runtime(Path(self.tmp.name))

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def test_the_recognizer_extracts_the_drive_path_facts(self):
        self.assertEqual(
            witness_semantics.extract_facts(
                "obj-12", support.recognized_decision_bytes()),
            DECISION_FACTS)
        self.assertEqual(
            witness_semantics.extract_facts(
                "obj-12", support.recognized_result_bytes()),
            RESULT_FACTS)
        # one governed command: the same execution identity in both
        self.assertEqual(
            support.governance_command_sha256(),
            hashlib.sha256(
                support.GOVERNANCE_COMMAND.encode("utf-8")).hexdigest())

    def test_the_drive_path_joins_and_decides_its_governed_command(self):
        """End to end through the shared drive path: both records are
        admitted byte-exact (D-C3-5), JOINED to the session the ledger
        bound, carried into the receipt with their facts, and the
        execution is decided by its decision record - COMPLETE, with no
        contradiction (D-C3-8)."""
        admitted = admit(self.rt, support.local_record_payload(
            objective="TI-1 governance witness"))
        info = support.drive_to_evidence_complete(self.rt, admitted)
        decision = support.recognized_decision_bytes()
        result = support.recognized_result_bytes()
        rows = self.rt.db.conn.execute(
            "SELECT content_sha256, native_session_id FROM attempt_witnesses"
            " WHERE attempt_id = ? AND witness_kind = 'obj-12'"
            " AND producer = 'helios'", (info["attempt_id"],)).fetchall()
        self.assertEqual(sorted(row["content_sha256"] for row in rows),
                         sorted([content_address(decision),
                                 content_address(result)]))
        self.assertEqual(set(row["native_session_id"] for row in rows),
                         set([support.GOVERNANCE_SESSION]))
        for data in (decision, result):
            self.assertEqual(self.rt.store.get(content_address(data)), data)
        completeness, payload, joins = receipt_of(self.rt,
                                                  info["attempt_id"])
        self.assertEqual(completeness, "COMPLETE")
        self.assertEqual(payload["contradictions"], [])
        for data, facts in ((decision, DECISION_FACTS),
                            (result, RESULT_FACTS)):
            join = joins[content_address(data)]
            self.assertEqual(join["join"], "JOINED")
            self.assertEqual(join["facts"], facts)
        self.assertEqual(self.rt.sequencer.recover()["intents_in_doubt"], [])

    def test_the_decision_record_is_what_decides_the_execution(self):
        """The control for the test above, planted the other way: the
        SAME result record without its decision record is an orphan
        execution (INV-68) - so the COMPLETE classification of the drive
        path really rests on the decision record's recognized facts."""
        info = frozen_attempt(self.rt, "TI-1 orphan execution",
                              (support.recognized_result_bytes(),))
        Collector(self.rt.sequencer, self.rt.store).collect_and_admit(
            info["attempt_id"])
        completeness, payload, _joins = receipt_of(self.rt,
                                                   info["attempt_id"])
        self.assertEqual(completeness, "INTEGRITY_FAILED")
        self.assertEqual([c["rule"] for c in payload["contradictions"]],
                         ["execution_without_decision"])


class GovernanceRecognizerDivergenceTests(unittest.TestCase):
    """F-TI-5 (CLOSED). The recognizer reads the producer's real shape:
    BOM-framed UTF-8 (utf-8-sig) and the ``verdict`` spelling for the
    governance decision. The tests below verify that the correction
    classifies producer-framed DENY-then-executed evidence as
    INTEGRITY_FAILED (deny_then_ran) without weakening any existing
    causal matching rules."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rt, _va = base_runtime(Path(self.tmp.name))

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _classified(self, objective, governance):
        info = frozen_attempt(self.rt, objective, governance)
        Collector(self.rt.sequencer, self.rt.store).collect_and_admit(
            info["attempt_id"])
        return receipt_of(self.rt, info["attempt_id"])

    def test_constructed_records_keep_the_producer_shape(self):
        decision = support.producer_decision_bytes()
        result = support.producer_result_bytes()
        for data, keys in ((decision, DECISION_KEYS), (result, RESULT_KEYS)):
            # the framing Windows PowerShell 5.1 writes: BOM, CRLF only
            self.assertTrue(data.startswith(support.GOVERNANCE_BOM))
            self.assertTrue(data.endswith(b"}\r\n"))
            self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
            document = json.loads(data.decode("utf-8-sig"))
            self.assertEqual(sorted(document), sorted(keys))
        decision_doc = json.loads(decision.decode("utf-8-sig"))
        result_doc = json.loads(result.decode("utf-8-sig"))
        for member in sorted(IDENTITY_FACTS):
            self.assertEqual(decision_doc[member], IDENTITY_FACTS[member])
            self.assertEqual(result_doc[member], IDENTITY_FACTS[member])
        self.assertEqual(decision_doc["verdict"], "ALLOW")
        self.assertNotIn("decision", decision_doc)
        self.assertNotIn("verdict", result_doc)
        self.assertEqual((result_doc["hook_event"], result_doc["success"]),
                         ("PostToolUse", True))
        # constructed, not copied: every digest-shaped value is synthetic
        self.assertEqual(result_doc["policy_hash"], "0" * 64)

    def test_layer_one_bom_framed_records_extract_facts(self):
        """BOM-framed records are parsed via utf-8-sig: the recognizer
        extracts identity and semantic facts whether the BOM is present
        or absent."""
        decision = support.producer_decision_bytes()
        result = support.producer_result_bytes()
        d_facts = witness_semantics.extract_facts("obj-12", decision)
        r_facts = witness_semantics.extract_facts("obj-12", result)
        for member in sorted(IDENTITY_FACTS):
            self.assertEqual(d_facts[member], IDENTITY_FACTS[member])
            self.assertEqual(r_facts[member], IDENTITY_FACTS[member])
        self.assertEqual(d_facts["decision"], "ALLOW")
        self.assertEqual(r_facts["hook_event"], "PostToolUse")
        self.assertTrue(r_facts["success"])
        # the control: bare records extract the same identity facts
        bare_decision = support.producer_decision_bytes(framed=False)
        bare_result = support.producer_result_bytes(framed=False)
        self.assertEqual(support.GOVERNANCE_BOM + bare_decision, decision)
        self.assertEqual(support.GOVERNANCE_BOM + bare_result, result)
        for member in sorted(IDENTITY_FACTS):
            self.assertEqual(
                witness_semantics.extract_facts("obj-12", bare_decision)[
                    member], IDENTITY_FACTS[member])
            self.assertEqual(
                witness_semantics.extract_facts("obj-12", bare_result)[
                    member], IDENTITY_FACTS[member])

    def test_layer_two_the_producer_verdict_is_a_recognized_decision(self):
        """The ``verdict`` spelling is recognized as the ``decision`` fact
        for every governance verdict the producer writes."""
        for verdict in ("ALLOW", "DENY", "DENY_FATAL"):
            bare = support.producer_decision_bytes(verdict=verdict,
                                                   framed=False)
            self.assertEqual(json.loads(bare.decode("utf-8"))["verdict"],
                             verdict)
            self.assertEqual(
                witness_semantics.extract_facts("obj-12", bare)["decision"],
                verdict)
        # the control: the canonical spelling
        self.assertEqual(
            witness_semantics.extract_facts(
                "obj-12",
                support.recognized_decision_bytes(decision="DENY"))[
                    "decision"], "DENY")

    def test_a_producer_framed_deny_then_ran_classifies_integrity_failed(self):
        """The consequence end to end (INV-68, D-C3-6/D-C3-8). A DENY
        decision followed by evidence of execution classifies
        INTEGRITY_FAILED with the deny_then_ran contradiction — in both
        the recognized AND the producer's real framing."""
        completeness, payload, _joins = self._classified(
            "TI-1 F-TI-5 control",
            (support.recognized_decision_bytes(decision="DENY"),
             support.recognized_result_bytes()))
        self.assertEqual(completeness, "INTEGRITY_FAILED")
        self.assertEqual([c["rule"] for c in payload["contradictions"]],
                         ["deny_then_ran"])

        framed = (support.producer_decision_bytes(verdict="DENY"),
                  support.producer_result_bytes())
        completeness, payload, joins = self._classified(
            "TI-1 F-TI-5 producer framing", framed)
        self.assertEqual(completeness, "INTEGRITY_FAILED")
        self.assertEqual([c["rule"] for c in payload["contradictions"]],
                         ["deny_then_ran"])
        for data in framed:
            join = joins[content_address(data)]
            self.assertEqual(join["join"], "JOINED")
            self.assertIn("facts", join)

    def test_bare_records_classify_correctly_for_each_verdict(self):
        """Bare records (no BOM, ``verdict`` spelling) are recognized since
        both the BOM codec and the verdict spelling are registered.
        DENY + execution classifies deny_then_ran; ALLOW + execution
        classifies COMPLETE (allowed and ran — the correct outcome)."""
        completeness, payload, joins = self._classified(
            "TI-1 F-TI-5 bare DENY",
            (support.producer_decision_bytes(verdict="DENY", framed=False),
             support.producer_result_bytes(framed=False)))
        self.assertEqual(completeness, "INTEGRITY_FAILED")
        self.assertEqual([c["rule"] for c in payload["contradictions"]],
                         ["deny_then_ran"])
        self.assertEqual(
            set(join["join"] for join in joins.values()
                if join["witness_kind"] == "obj-12"),
            set(["JOINED"]))

        completeness, payload, joins = self._classified(
            "TI-1 F-TI-5 bare ALLOW",
            (support.producer_decision_bytes(verdict="ALLOW", framed=False),
             support.producer_result_bytes(framed=False)))
        self.assertEqual(completeness, "COMPLETE")
        self.assertEqual(payload["contradictions"], [])
        self.assertEqual(
            set(join["join"] for join in joins.values()
                if join["witness_kind"] == "obj-12"),
            set(["JOINED"]))

    def test_deny_without_execution_is_not_deny_then_ran(self):
        """A DENY decision with no evidence of execution does not trigger
        deny_then_ran: the governance family guarantees a decision PRECEDES
        execution, so DENY alone is expected and diagnostic (D-12), never
        a contradiction."""
        completeness, payload, _joins = self._classified(
            "TI-1 F-TI-5 deny only",
            (support.producer_decision_bytes(verdict="DENY"),))
        self.assertNotIn("deny_then_ran",
                         [c["rule"] for c in payload["contradictions"]])

    def test_cross_session_evidence_refused_at_admission(self):
        """Evidence from another session is refused at admission (D-C3-5):
        the Sequencer's identity gate detects the cross-execution session
        mismatch before the evidence reaches the Collector or receipt.
        This is a STRONGER guarantee than receipt-level CONTRADICTED —
        cross-session witnesses never enter the admitted set."""
        from autobot.models import ConflictError
        cross_decision = support.producer_decision_bytes(
            session_id="other-session-id", verdict="DENY")
        seq = self.rt.sequencer
        admitted = admit(self.rt, support.local_record_payload(
            objective="TI-1 cross session"))
        seq.mark_ready(admitted["sequence_id"])
        info = seq.start_execution(admitted["sequence_id"])
        seq.reserve_attempt(info["attempt_id"], info["fence"])
        dispatched = seq.dispatch_attempt(
            info["attempt_id"], "builder", "fake",
            self.rt.store.put(b"projection: cross session\n"))
        start = seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        seq.attempt_running(info["attempt_id"],
                            support.GOVERNANCE_SESSION,
                            start["content_address"])
        with self.assertRaises(ConflictError) as ctx:
            seq.admit_witness(
                info["attempt_id"], "obj-12", "helios", cross_decision)
        self.assertIn("cross-execution identity", str(ctx.exception))

    def test_cross_command_evidence_does_not_satisfy_causal_join(self):
        """A DENY for one command and execution of a different command in
        the same session: deny_then_ran matches on command_sha256 identity,
        so a different command does not trigger the contradiction."""
        cross = (support.producer_decision_bytes(
                     command="python other_script.py", verdict="DENY"),
                 support.producer_result_bytes())
        completeness, payload, joins = self._classified(
            "TI-1 F-TI-5 cross command", cross)
        self.assertNotIn("deny_then_ran",
                         [c["rule"] for c in payload["contradictions"]])

    def test_every_blocking_verdict_triggers_deny_then_ran(self):
        """The Helios producer blocks execution for DENY, DENY_FATAL,
        UNKNOWN and INTEGRITY_FAILURE. Each, followed by evidence of
        execution, classifies INTEGRITY_FAILED with deny_then_ran."""
        for verdict in ("DENY", "DENY_FATAL", "UNKNOWN",
                        "INTEGRITY_FAILURE"):
            framed = (support.producer_decision_bytes(verdict=verdict),
                      support.producer_result_bytes())
            completeness, payload, _joins = self._classified(
                "TI-1 verdict %s" % verdict, framed)
            self.assertEqual(completeness, "INTEGRITY_FAILED",
                             "verdict %s" % verdict)
            self.assertEqual(
                [c["rule"] for c in payload["contradictions"]],
                ["deny_then_ran"], "verdict %s" % verdict)

    def test_authorizing_verdicts_do_not_trigger_deny_then_ran(self):
        """ALLOW and PASSTHROUGH authorize execution. Neither triggers
        deny_then_ran when the command subsequently runs."""
        for verdict in ("ALLOW", "PASSTHROUGH"):
            framed = (support.producer_decision_bytes(verdict=verdict),
                      support.producer_result_bytes())
            completeness, payload, _joins = self._classified(
                "TI-1 verdict %s" % verdict, framed)
            self.assertEqual(completeness, "COMPLETE",
                             "verdict %s" % verdict)
            self.assertEqual(payload["contradictions"], [],
                             "verdict %s" % verdict)

    def _running_attempt(self, objective):
        """Set up an attempt in RUNNING state, ready for witness admission."""
        seq = self.rt.sequencer
        admitted = admit(self.rt, support.local_record_payload(
            objective=objective))
        seq.mark_ready(admitted["sequence_id"])
        info = seq.start_execution(admitted["sequence_id"])
        seq.reserve_attempt(info["attempt_id"], info["fence"])
        dispatched = seq.dispatch_attempt(
            info["attempt_id"], "builder", "fake",
            self.rt.store.put(("projection: %s\n" % objective).encode()))
        start = seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        seq.attempt_running(info["attempt_id"],
                            support.GOVERNANCE_SESSION,
                            start["content_address"])
        return info

    def test_contradictory_authorization_refused_at_admission(self):
        """D-C4-14: a governance record whose decision and verdict
        disagree on whether execution is authorized is refused at
        witness admission. Both conflict directions are tested.
        No admission row is created; the attempt stays in RUNNING."""
        from autobot.models import ConflictError
        for allow_field, deny_field in (("decision", "verdict"),
                                         ("verdict", "decision")):
            info = self._running_attempt("TI-1 D-C4-14 %s" % allow_field)
            conflicting = json.dumps({
                allow_field: "ALLOW", deny_field: "DENY",
                "session_id": support.GOVERNANCE_SESSION,
                "tool_use_id": support.GOVERNANCE_TOOL_USE,
                "correlation_id": support.GOVERNANCE_CORRELATION,
                "command_sha256": support.governance_command_sha256(),
                "hook_event": "PreToolUse",
            }, sort_keys=True).encode("utf-8")
            with self.assertRaises(ConflictError) as ctx:
                self.rt.sequencer.admit_witness(
                    info["attempt_id"], "obj-12", "helios", conflicting)
            self.assertIn("contradictory authorization", str(ctx.exception))
            self.assertIn("D-C4-14", str(ctx.exception))
            row = self.rt.db.conn.execute(
                "SELECT COUNT(*) AS n FROM attempt_witnesses"
                " WHERE attempt_id = ? AND witness_kind = 'obj-12'",
                (info["attempt_id"],)).fetchone()
            self.assertEqual(row["n"], 0, "no obj-12 admission row created")

    def test_contradictory_authorization_refused_with_execution_evidence(self):
        """D-C4-14 holds even when execution evidence exists — the
        contradictory decision record is refused regardless of whether
        a result record would have triggered deny_then_ran."""
        from autobot.models import ConflictError
        info = self._running_attempt("TI-1 D-C4-14 with execution")
        self.rt.sequencer.admit_witness(
            info["attempt_id"], "obj-12", "helios",
            support.recognized_result_bytes())
        conflicting = json.dumps({
            "decision": "ALLOW", "verdict": "DENY",
            "session_id": support.GOVERNANCE_SESSION,
            "tool_use_id": support.GOVERNANCE_TOOL_USE,
            "command_sha256": support.governance_command_sha256(),
        }, sort_keys=True).encode("utf-8")
        with self.assertRaises(ConflictError) as ctx:
            self.rt.sequencer.admit_witness(
                info["attempt_id"], "obj-12", "helios", conflicting)
        self.assertIn("contradictory authorization", str(ctx.exception))

    def test_same_direction_disagreement_is_not_refused(self):
        """Same-direction disagreement (DENY vs DENY_FATAL, ALLOW vs
        PASSTHROUGH) is NOT contradictory — both values agree on whether
        execution is authorized."""
        for field_a, val_a, field_b, val_b in (
                ("decision", "DENY", "verdict", "DENY_FATAL"),
                ("decision", "ALLOW", "verdict", "PASSTHROUGH")):
            info = self._running_attempt("TI-1 same-dir %s-%s" % (val_a, val_b))
            record = json.dumps({
                field_a: val_a, field_b: val_b,
                "session_id": support.GOVERNANCE_SESSION,
                "tool_use_id": support.GOVERNANCE_TOOL_USE,
                "command_sha256": support.governance_command_sha256(),
            }, sort_keys=True).encode("utf-8")
            result = self.rt.sequencer.admit_witness(
                info["attempt_id"], "obj-12", "helios", record)
            self.assertIn("content_address", result)

    def test_matching_decision_and_verdict_admitted(self):
        """Matching values: ALLOW/ALLOW and DENY/DENY are admitted and
        classify correctly end to end."""
        matching_allow = json.dumps({
            "decision": "ALLOW", "verdict": "ALLOW",
            "session_id": support.GOVERNANCE_SESSION,
            "tool_use_id": support.GOVERNANCE_TOOL_USE,
            "correlation_id": support.GOVERNANCE_CORRELATION,
            "command_sha256": support.governance_command_sha256(),
            "hook_event": "PreToolUse",
        }, sort_keys=True).encode("utf-8")
        completeness, payload, _joins = self._classified(
            "TI-1 matching ALLOW",
            (matching_allow, support.recognized_result_bytes()))
        self.assertEqual(completeness, "COMPLETE")
        self.assertEqual(payload["contradictions"], [])

    def test_legitimate_deny_then_ran_still_fires(self):
        """A legitimate (single-spelling) DENY followed by execution
        still classifies INTEGRITY_FAILED via deny_then_ran — the
        admission check does not interfere with valid evidence."""
        completeness, payload, _joins = self._classified(
            "TI-1 legitimate deny+run",
            (support.producer_decision_bytes(verdict="DENY"),
             support.producer_result_bytes()))
        self.assertEqual(completeness, "INTEGRITY_FAILED")
        self.assertEqual([c["rule"] for c in payload["contradictions"]],
                         ["deny_then_ran"])


# ---------------------------------------------------------- F-TI-6

SCRIPT_A = b"print('A: the bytes bound to the entrypoint')\n"
SCRIPT_B = b"print('B: the bytes bound to another entry')\n"

# Paths that are NOT closure paths: each resolves, on Windows, to
# something other than the plain file its string names.
NON_PORTABLE = (
    "C:tool.py", "D:tool.py", "C:/Windows/tool.py", "pkg/C:tool.py",
    "NUL", "nul.py", "pkg/CON.txt", "aux", "com1.py", "LPT9",
    "nul .txt", "NUL  .tar.gz",
    "tool.py:stream", "tool.py::$DATA",
    "what?.py", "a*.py", "a<b>.py", "quote\".py", "pipe|.py",
    "tool.py.", "pkg./tool.py",
    "VERIFI~1.PY", "pkg/TOOL~2",
)
# Paths that ARE closure paths, close to the refused forms.
PORTABLE = (
    "tool.py", "pkg/Tool.py", "pkg/data.txt", "Pkg2/tool.py",
    "console.py", "nully.txt", "auxiliary/com10.py",
    "notes~.txt", "~lock.py", "backup~/a~b.py",
    "dependency.lock", "inputs/input.bin",
)


def manifest_registration(store, entries, entrypoint, pinned=True,
                          registration_entrypoint=None):
    """Seed every entry's bytes and return a packaged registration whose
    manifest names ``entries`` ((path, bytes) pairs)."""
    for _path, data in entries:
        store.put(data)
    manifest = {"schema_version": "v1", "kind": "PackageManifest",
                "entries": [{"path": path,
                             "content_sha256": content_address(data)}
                            for path, data in entries],
                "entrypoint": entrypoint}
    if pinned:
        manifest["interpreter_id"] = interpreter_identity()
    address = store.put(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8"))
    closure = {"kind": "packaged", "manifest_sha256": address}
    return {"registration_id": "rg_" + "f6".rjust(32, "0"),
            "entrypoint": registration_entrypoint or entrypoint,
            "closure": closure, "artifact_root": artifact_root(closure)}


def aliases_here(directory, first, second):
    """Does THIS filesystem resolve the two names to one file? (A probe:
    it writes two small files inside ``directory`` and looks.)"""
    probe = tempfile.mkdtemp(dir=directory)
    try:
        with open(os.path.join(probe, first), "wb") as handle:
            handle.write(b"first")
        with open(os.path.join(probe, second), "wb") as handle:
            handle.write(b"second")
        with open(os.path.join(probe, first), "rb") as handle:
            return handle.read() != b"first"
    except OSError:
        return None  # the filesystem refuses one of the names outright


class ClosureFilesystemTests(unittest.TestCase):
    """F-TI-6: a closure path names the same file on every filesystem, or
    the closure is refused before anything executes."""

    # (first name, second name): two different strings Windows resolves
    # to one file
    TWINS = (("tool.py", "TOOL.py"), ("tool.py", "tool.py."),
             ("tool.py", "tool.py "), ("tool.py", "tool.py::$DATA"))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = Store(self.dir / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_dir(self, name):
        path = self.dir / name
        path.mkdir()
        return str(path)

    def _refused(self, registration, marker):
        with self.assertRaises(IntegrityHalt) as caught:
            transforms.resolve_closure(self.store, registration)
        self.assertIn(marker, str(caught.exception))

    def _own_drive(self):
        """The drive the temp directory itself is on. Tests that hand a
        drive-letter path to a seam that WRITES use this drive only: if
        the refusal ever regressed, Windows would resolve the path inside
        the run directory, never outside it."""
        return os.path.splitdrive(str(self.dir))[0] or "C:"

    def _twins(self, first, second):
        """Two DIFFERENT manifest strings bound to different bytes; the
        first is the entrypoint."""
        return manifest_registration(
            self.store, [(first, SCRIPT_A), (second, SCRIPT_B)], first)

    def test_a_portable_manifest_materializes_every_entry_faithfully(self):
        """The control: distinct portable paths - nested, mixed case in
        different directories, non-ASCII NFC, tildes that are not the
        short-alias form - each hold their own bytes and the bound
        entrypoint is what would run."""
        entries = [(path, ("entry %d: %s\n" % (index, path)).encode("utf-8"))
                   for index, path in enumerate(PORTABLE)]
        entries.append((unicodedata.normalize("NFC", "docs/caf\u00e9.txt"),
                        b"nfc\n"))
        entries[0] = ("tool.py", SCRIPT_A)
        registration = manifest_registration(self.store, entries, "tool.py")
        resolved = transforms.resolve_closure(self.store, registration)
        run_dir = self._run_dir("control")
        argv = transforms.materialize_closure(
            self.store, resolved, run_dir, resolved["package"]["entrypoint"])
        self.assertEqual(argv[:2], [sys.executable, "-I"])
        for path, data in entries:
            with open(os.path.join(run_dir, *path.split("/")), "rb") as fh:
                self.assertEqual(fh.read(), data, path)
        with open(argv[2], "rb") as fh:
            self.assertEqual(fh.read(), SCRIPT_A)
        on_disk = sum(len(files) for _root, _dirs, files in os.walk(run_dir))
        self.assertEqual(on_disk, len(entries))

    def test_aliasing_entries_are_refused_before_anything_executes(self):
        """Where this filesystem folds two names into one file (probed,
        not assumed) the bound entrypoint would execute the other entry's
        bytes; such a manifest is refused on every filesystem alike."""
        folded_here = [twin for twin in self.TWINS
                       if aliases_here(str(self.dir), twin[0], twin[1])]
        if os.name == "nt":
            # the probe ran against a filesystem that really folds them all
            self.assertEqual(folded_here, list(self.TWINS))
        self._refused(self._twins("tool.py", "TOOL.py"),
                      "same file on a case-insensitive filesystem")
        self._refused(self._twins("pkg/tool.py", "PKG/Tool.py"),
                      "same file on a case-insensitive filesystem")
        self._refused(self._twins("tool.py", "tool.py."), "malformed")
        self._refused(self._twins("tool.py", "tool.py "), "malformed")
        self._refused(self._twins("tool.py", "tool.py::$DATA"), "malformed")

    def test_non_portable_segments_are_not_closure_paths(self):
        """A drive-letter segment leaves the run directory on Windows
        (computed here, never written); device names - with an extension,
        or a space before it - reach the device; stream suffixes and
        wildcard characters do not name a plain file; the 8.3 short-alias
        form names some long name's file where short names are enabled.
        None of them is a closure path anywhere."""
        run_dir = self._run_dir("never-written")
        for path in NON_PORTABLE:
            registration = manifest_registration(
                self.store, [(path, SCRIPT_A)], path)
            self._refused(registration, "malformed")
        if os.name == "nt":
            other = "C:" if os.path.splitdrive(run_dir)[0].upper() != "C:" \
                else "D:"
            target = os.path.join(run_dir, *(other + "tool.py").split("/"))
            self.assertFalse(
                os.path.normcase(os.path.abspath(target)).startswith(
                    os.path.normcase(os.path.abspath(run_dir)) + os.sep))
        self.assertEqual(os.listdir(run_dir), [])

    def test_a_path_is_never_both_a_file_and_a_directory(self):
        for entries in ([("pkg", SCRIPT_A), ("pkg/mod.py", SCRIPT_B)],
                        [("pkg/mod.py", SCRIPT_B), ("PKG", SCRIPT_A)]):
            registration = manifest_registration(self.store, entries,
                                                 entries[0][0])
            self._refused(registration,
                          "both as a file and as a directory")

    def test_names_the_runner_writes_are_not_closure_paths(self):
        """The runner writes its own files into the same run directory
        after materialization (the transform's input and output, the
        verifier's context and subject). A closure entry by one of those
        names would be overwritten - an entrypoint named input.bin would
        execute the INPUT."""
        for path in ("input.bin", "INPUT.BIN", "output.bin", "context.json",
                     "subject", "Subject/artifact.py"):
            registration = manifest_registration(
                self.store, [(path, SCRIPT_A)], path)
            self._refused(registration, "reserved for the runner")

    def test_non_nfc_paths_are_refused_whatever_the_filesystem_does(self):
        """NFC and NFD spellings of one name are different strings and
        different manifest identities; whether a filesystem folds them is
        its own business (NTFS does not, APFS does). The grammar takes the
        frozen D-26 rule - NFC only - so the answer never depends on it."""
        nfd = unicodedata.normalize("NFD", "caf\u00e9.py")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))
        registration = manifest_registration(
            self.store, [(nfd, SCRIPT_A)], nfd)
        self._refused(registration, "malformed")

    def test_registration_entrypoints_obey_the_same_grammar(self):
        """python and native closures materialize the REGISTRATION's
        entrypoint. The frozen D-26 path grammar admits ``C:tool.py``; the
        materialization seam does not."""
        self.store.put(SCRIPT_A)
        self.store.put(support.LIVE_LOCK)
        python_closure = {
            "kind": "python", "script_sha256": content_address(SCRIPT_A),
            "interpreter_id": interpreter_identity(),
            "dependency_lock_sha256": content_address(support.LIVE_LOCK)}
        native_closure = {"kind": "native",
                          "executable_sha256": content_address(SCRIPT_A)}
        run_dir = self._run_dir("entrypoints")
        argv = transforms.materialize_closure(      # the control
            self.store, python_closure, run_dir, "bin/verify.py")
        with open(argv[2], "rb") as fh:
            self.assertEqual(fh.read(), SCRIPT_A)
        drive = self._own_drive()
        planted = (drive + "tool.py", drive + "/tools/tool.py", "nul.py",
                   "nul .py", "tool.py.", "tool.py::$DATA", "TOOL~1.PY",
                   "input.bin", "context.json", "subject/tool.py",
                   "pkg/../escape.py", "pkg\\tool.py")
        for closure in (python_closure, native_closure):
            for entrypoint in planted:
                empty = self._run_dir("refused-%s-%d" % (
                    closure["kind"], planted.index(entrypoint)))
                with self.assertRaises(ConflictError) as caught:
                    transforms.materialize_closure(self.store, closure,
                                                   empty, entrypoint)
                self.assertIn("closure-relative", str(caught.exception))
                self.assertEqual(os.listdir(empty), [])
        # the python form writes its lock beside the script: an entrypoint
        # by the lock's own name would execute the lock
        empty = self._run_dir("refused-lock")
        with self.assertRaises(ConflictError) as caught:
            transforms.materialize_closure(self.store, python_closure,
                                           empty, "Dependency.lock")
        self.assertIn("dependency lock", str(caught.exception))
        self.assertEqual(os.listdir(empty), [])

    def test_materialization_rechecks_a_package_it_did_not_parse(self):
        """Defense at the seam of use: a resolved-closure dict that never
        went through the manifest parser gets no trust. The entrypoint is
        portable in both cases, so it is the ENTRY re-check that refuses:
        case-fold twins, then a drive-letter entry."""
        self.store.put(SCRIPT_A)
        self.store.put(SCRIPT_B)
        twin = {"path": "TOOL.py", "content_sha256": content_address(SCRIPT_B)}
        lettered = {"path": self._own_drive() + "other.py",
                    "content_sha256": content_address(SCRIPT_B)}
        for index, extra in enumerate((twin, lettered)):
            closure = {"kind": "packaged", "package": {
                "entries": [{"path": "tool.py", "content_sha256":
                             content_address(SCRIPT_A)}, extra],
                "entrypoint": "tool.py", "interpreter_id": None}}
            run_dir = self._run_dir("bypass-%d" % index)
            with self.assertRaises(IntegrityHalt) as caught:
                transforms.materialize_closure(self.store, closure, run_dir,
                                               "tool.py")
            self.assertIn("packaged closure", str(caught.exception))
            self.assertEqual(os.listdir(run_dir), [])

    def test_the_two_statements_of_the_grammar_agree(self):
        """D-58 for the new rule itself: the production grammar and
        auditor2's independent restatement, over the planted paths, the
        portable ones and seeded random strings from the risky alphabet -
        and over whole manifests (twins, file-and-directory, runner-owned
        names), where auditor2 treats exactly the refused ones as
        unknown."""
        for path in NON_PORTABLE:
            self.assertFalse(transforms._safe_relative_path(path), path)
        for path in PORTABLE:
            self.assertTrue(transforms._safe_relative_path(path), path)
        rng = random.Random(SEED)
        alphabet = ("ab.~1 /\\:*?\"<>|\t" + "CcOoNnUuLlAaXx"
                    + "\u00e9e\u0301")
        paths = list(NON_PORTABLE) + list(PORTABLE)
        for _ in range(4000):
            paths.append("".join(rng.choice(alphabet)
                                 for _ in range(rng.randint(0, 9))))
        accepted = 0
        for index, path in enumerate(paths):
            verdict = transforms._safe_relative_path(path)
            self.assertEqual(auditor2._safe_relative_path(path), verdict,
                             "seed %d case %d: %r" % (SEED, index, path))
            accepted += bool(verdict)
        self.assertGreater(accepted, 200)                  # not vacuous
        self.assertGreater(len(paths) - accepted, 1500)    # either way
        digest = content_address(SCRIPT_A)
        manifests = (
            ([("tool.py", digest), ("pkg/data.txt", digest)], True),
            ([("tool.py", digest), ("TOOL.py", digest)], False),
            ([("tool.py", digest), ("tool.py", digest)], False),
            ([("pkg", digest), ("pkg/mod.py", digest)], False),
            ([("tool.py", digest), ("Input.bin", digest)], False),
            ([("tool.py", digest), ("nul .txt", digest)], False),
        )
        for entries, valid in manifests:
            data = json.dumps(
                {"schema_version": "v1", "kind": "PackageManifest",
                 "entries": [{"path": path, "content_sha256": address}
                             for path, address in entries],
                 "entrypoint": entries[0][0]},
                sort_keys=True).encode("utf-8")
            if valid:
                parsed = transforms.parse_package_manifest(data)
                self.assertEqual(
                    auditor2.package_entry_addresses(data),
                    [entry["content_sha256"]
                     for entry in parsed["entries"]])
            else:
                with self.assertRaises(IntegrityHalt) as caught:
                    transforms.parse_package_manifest(data)
                self.assertIn("packaged closure", str(caught.exception))
                self.assertIsNone(auditor2.package_entry_addresses(data))

    def test_content_addresses_do_not_alias_by_case(self):
        """The content store's own names: lowercase hex under two-hex
        directories - pure ASCII, so neither case folding nor Unicode
        normalization can make two addresses one file, and an address in
        any other spelling is not an address at all."""
        address = self.store.put(SCRIPT_A)
        self.assertTrue(self.store.has(address))
        upper = "sha256:" + address.split(":")[1].upper()
        with self.assertRaises(IntegrityHalt) as caught:
            self.store.has(upper)
        self.assertIn("D-30", str(caught.exception))
        names = []
        for root, _dirs, files in os.walk(str(self.dir / "store")):
            for name in files:
                names.append(os.path.relpath(
                    os.path.join(root, name),
                    str(self.dir / "store")).replace(os.sep, "/"))
        self.assertTrue(names)
        for name in names:
            self.assertTrue(
                re.match(r"^objects/[0-9a-f]{2}/[0-9a-f]{64}$", name), name)


class EnvironmentConstraintBoundaryTests(unittest.TestCase):
    """D-56's boundary, stated as a checked fact: obj-18 CAPTURES the
    interpreter's and each named tool's executable_sha256, and the frozen
    constraint vocabulary cannot NAME it. A contract pins an interpreter
    by implementation and version; the binary's bytes are recorded
    evidence, never a gate. (Closure execution is different: a python
    closure pins the interpreter IDENTITY string, INV-66.)"""

    def _capture(self):
        return environment_probe.capture_environment(
            {"sequence_id": "sq_" + "0" * 32, "attempt_id": "at_" + "0" * 32,
             "version": 1, "fence": 1},
            support.manual_clock(),
            constraints=[{"tools": {"git": {}}}],
            tool_prober=lambda name: ("2.45.0", "sha256:" + "a" * 64))

    def test_the_capture_records_the_executable_identity(self):
        capture = self._capture()
        with open(sys.executable, "rb") as handle:
            independent = "sha256:" + hashlib.sha256(
                handle.read()).hexdigest()
        self.assertEqual(capture["interpreter"]["executable_sha256"],
                         independent)
        self.assertEqual(capture["tools"]["git"]["executable_sha256"],
                         "sha256:" + "a" * 64)

    def test_no_constraint_can_name_the_executable_hash(self):
        capture = self._capture()
        captured = capture["interpreter"]["executable_sha256"]
        # the control: the vocabulary that exists is satisfied
        self.assertIsNone(environment_satisfies(
            {"interpreter": {
                "implementations": [capture["interpreter"]["implementation"]],
                "versions": [capture["interpreter"]["version"]]},
             "tools": {"git": {"versions": ["2.45.0"]}}}, capture))
        # even the TRUE hash cannot be declared: the key fails closed, so
        # no contract can believe it pinned the binary
        for spelling in ("executable_sha256", "executable_sha256s"):
            refusal = environment_satisfies(
                {"interpreter": {spelling: [captured]}}, capture)
            self.assertIn("not supported", refusal)
            refusal = environment_satisfies(
                {"tools": {"git": {spelling: ["sha256:" + "a" * 64]}}},
                capture)
            self.assertIn("not supported", refusal)


if __name__ == "__main__":
    unittest.main()
