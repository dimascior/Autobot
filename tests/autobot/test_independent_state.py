"""TI-1 WP1: the independent state oracle, proven both ways.

``auditor2`` re-derives a runtime directory's durable authority facts with
code that shares nothing with the Sequencer: a fresh read-only connection,
its own file walk and SHA-256, its own canonical writer and identity
constructions (``preimage2``), its own statement of every rule.

Positive direction - the oracle sweeps real runtimes the production plane
built (the drive_to_verified flow, a FAIL/repair/halt flow, an attested
flow, transform mismatch and calibrated pass, the full SL loop through
completion, a READ_ONLY completion, an effect-intent retry history, and a
hard-killed child's directory) and finds nothing; its journal witness
proves it read THAT ledger; its retention walk equals the Sequencer's.

Negative direction (the chapter-1 checker-negative shape) - a clean COPY of
one runtime passes first; then each planted defect makes the named check
fail. The oracle is tested like the code it audits.

A fresh ``-I`` interpreter runs the same audit over a preserved directory
and must emit the identical report with no production module loaded.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import auditor2
import preimage2
import support
from test_chapter4_m2 import packaged_manifest_payload
from test_independent_identity import (
    PRODUCTION_PACKAGES,
    assert_oracle_isolated,
)
from test_verification import human_record_payload
from autobot import ProductionVerifierRunner
from autobot.git_executor import execute_local_cas
from autobot.models import AdmissionError, IntegrityHalt
from contract_library import (
    artifact_root,
    canonicalize,
    content_address,
    object_hash,
)

HERE = Path(__file__).resolve().parent
AUDITOR2_PATH = HERE / "auditor2.py"
CRASH_CHILD = HERE / "crash_child.py"

TARGET = "refs/heads/main"
RAW_INPUT = "﻿Monitor the run.\r\nReport hourly.\r\n"
RECEIVED_INPUT = "﻿Monitor the run.\nReport hourly.\n"


def edge(edge_id, kind, target):
    return {"edge_id": edge_id, "edge_kind": kind, "target": target,
            "observation_requirement": "ACCEPTANCE"}


def describe(findings):
    return "\n".join("%(check)s [%(subject)s] %(detail)s" % finding
                     for finding in findings) or "(no findings)"


def checks_of(report):
    return set(finding["check"] for finding in report["findings"])


# ------------------------------------------------------------ scenarios

def base_runtime(directory):
    """V-A admitted (the fixture corpus), the LOCAL declaration, a held
    instance lease."""
    rt, va = support.admitted_runtime(directory,
                                      clock=support.manual_clock())
    graph = support.load_fixture("graph.json")
    rt.registry.admit_object(graph["declaration_local"],
                             submitted_by="you")
    rt.sequencer.acquire_lease(ttl_seconds=360000)
    return rt, va


def admit(rt, record):
    result = rt.registry.admit_payload(record, submitted_by="planner")
    if result["outcome"] != "NEW_RECORD":
        raise AssertionError("scenario record not admitted: %r" % (result,))
    return result


def build_verified(directory):
    rt, _va = base_runtime(directory)
    admitted = admit(rt, support.local_record_payload())
    info = support.drive_to_verified(rt, admitted)
    return rt, {"admitted": admitted, "info": info}


def build_repair_then_halt(directory):
    """A genuine FAIL: t-a08 + t-s11 (repair), then t-a08 + t-s12."""
    rt, _va = base_runtime(directory)
    admitted = admit(rt, support.local_record_payload(
        objective="TI-1 repair flow", vector_behavior="FAIL"))
    runner = ProductionVerifierRunner(rt.sequencer, rt.store)
    outcomes = []
    for _ in range(2):
        info = support.drive_to_evidence_complete(rt, admitted)
        runner.verify_attempt(info["attempt_id"])
        outcomes.append(rt.sequencer.conclude_verification(
            info["attempt_id"], info["fence"]))
    return rt, {"admitted": admitted, "outcomes": outcomes}


def build_attested(directory):
    """HUMAN_REQUIRED: your obj-37 admitted, incorporated, concluded."""
    rt, _va = base_runtime(directory)
    graph = support.load_fixture("graph.json")
    rt.registry.admit_object(graph["verifier_llm_v1"], submitted_by="you")
    admitted = admit(rt, human_record_payload())
    info = support.drive_to_evidence_complete(rt, admitted)
    attempt_id = info["attempt_id"]
    state = rt.sequencer.verification_state(attempt_id)
    rt.sequencer.attest_assertion(attempt_id, "a3", {
        "schema_version": "v1", "kind": "ManualAttestation.ASSERTION",
        "attested_by": "you", "assertion_id": "a3",
        "attempt_id": attempt_id,
        "attempt_contract_hash": state["attempt_contract_hash"],
        "candidate_receipt_sha256": state["candidate_receipt_sha256"],
        "part": {"record": state["record_hash"], "part_kind": "ASSERTION",
                 "part_id": "a3"},
        "attests": "PASS"})
    ProductionVerifierRunner(rt.sequencer, rt.store).verify_attempt(
        attempt_id)
    result = rt.sequencer.conclude_verification(attempt_id, info["fence"])
    return rt, {"admitted": admitted, "result": result}


def build_transform_flows(directory):
    """The calibrated A1 transform over a BOM+CRLF input: the child that
    received the RAW bytes FAILs (transform_output_mismatch, repair path);
    the child that received the normalized text passes."""
    rt, _va = base_runtime(directory)
    runner = ProductionVerifierRunner(rt.sequencer, rt.store)
    address = rt.store.put(RAW_INPUT.encode("utf-8"))

    def calibrated(task_contract_id, objective):
        record = support.local_record_payload(
            task_contract_id=task_contract_id, objective=objective)
        record["role_transforms"] = {"builder": "autoswarm-input-v1"}
        record["resolved_bindings"]["transform_registrations"] = [
            support.live_transform_hash("autoswarm-input-v1")]
        record["specification_inputs"] = {
            "name_to_exact_byte_sha256": {"monitor-brief.md": address}}
        return record

    bad = admit(rt, calibrated("tc_" + "71a0" * 8, "TI-1 raw bytes reached"
                                                  " the child"))
    info = support.drive_to_evidence_complete(rt, bad, prompt=RAW_INPUT)
    runner.verify_attempt(info["attempt_id"])
    mismatch = rt.sequencer.conclude_verification(info["attempt_id"],
                                                  info["fence"])
    good = admit(rt, calibrated("tc_" + "71a1" * 8, "TI-1 calibrated"
                                                   " transform pass"))
    info = support.drive_to_evidence_complete(rt, good,
                                              prompt=RECEIVED_INPUT)
    runner.verify_attempt(info["attempt_id"])
    passed = rt.sequencer.conclude_verification(info["attempt_id"],
                                                info["fence"])
    return rt, {"mismatch": mismatch, "passed": passed}


def build_sl_loop(directory):
    """Contract A through the real Git finalization to COMPLETED; B
    BLOCKED on A's record tier, READY only through the durable release."""
    rt, _va = base_runtime(directory)
    seq = rt.sequencer
    a = admit(rt, support.local_record_payload())
    b = admit(rt, support.local_record_payload(
        task_contract_id="tc_" + "b00b" * 8,
        predecessors=[edge("on-a", "RECORD", a["record_hash"])]))
    seq.mark_ready(b["sequence_id"])
    info = support.drive_to_verified(rt, a)
    a_id = a["sequence_id"]
    seq.grant_permit(a_id, permitted_uses=3)
    repo = Path(directory) / "repo"
    sha_old, sha_new = support.init_git_repo(repo)
    minted = seq.begin_commit(a_id, info["attempt_id"], TARGET, sha_old,
                              sha_new)
    effect = execute_local_cas(seq, minted["intent_id"], repo)
    seq.commit_committed(a_id, info["fence"], effect["receipt"])
    acceptance = seq.accept_commit(a_id, info["fence"])
    released = seq.release_dependencies(a_id)
    successor = seq.reevaluate_blocked(b["sequence_id"])
    seq.complete_sequence(a_id)
    return rt, {"a": a, "b": b, "info": info, "acceptance": acceptance,
                "released": released, "successor": successor}


def build_read_only_completed(directory):
    rt, _va = base_runtime(directory)
    seq = rt.sequencer
    admitted = admit(rt, support.local_record_payload(
        task_contract_id="tc_" + "ace0" * 8,
        objective="TI-1 READ_ONLY completion",
        finalization_class="READ_ONLY", finalization_requirements=[]))
    info = support.drive_to_verified(rt, admitted)
    sequence_id = admitted["sequence_id"]
    acceptance = seq.accept_verified(sequence_id, info["fence"])
    seq.release_dependencies(sequence_id)
    seq.complete_sequence(sequence_id)
    return rt, {"admitted": admitted, "acceptance": acceptance}


def build_intent_history(directory):
    """The effect-intent machine: mint, window expiry (t-i03), retry with
    a bound successor (t-i06), observed resolution (t-i01); reservations
    reserved, released and consumed."""
    rt, va = support.admitted_runtime(directory,
                                      clock=support.manual_clock())
    seq = rt.sequencer
    seq.acquire_lease(ttl_seconds=360000)
    sequence_id = va["sequence_id"]
    seq.mark_ready(sequence_id)
    attempt_id = seq.start_execution(sequence_id)["attempt_id"]
    seq.grant_permit(sequence_id, permitted_uses=3)
    held = seq.reserve_use(sequence_id, attempt_id)
    first = seq.mint_intent(sequence_id, attempt_id, "eff-canonical-push",
                            target=TARGET,
                            expected_old="sha256:" + "ab" * 32)
    rt.clock.advance(301)
    expired = seq.expire_intents()
    successor = seq.retry_intent(first)
    resolved = seq.resolve_intent(successor, "target_equals_new")
    spare = seq.reserve_use(sequence_id, attempt_id)
    seq.release_reservation(spare, cause="ti-1 pre-ponr exit")
    seq.consume_reservation(held, cause="ti-1 ponr")
    return rt, {"expired": expired, "first": first,
                "successor": successor, "resolved": resolved}


def clone_runtime(source, destination):
    """Copy the two things an audit reads: the ledger file(s) and the
    content store. The source's connections must be closed."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True)
    for name in ("autobot.db", "autobot.db-wal", "autobot.db-shm"):
        if (source / name).exists():
            shutil.copy2(str(source / name), str(destination / name))
    shutil.copytree(str(source / "store"), str(destination / "store"))
    return destination


def sql(directory, statement, parameters=()):
    """One statement through a PLAIN sqlite3 connection (no production
    code on the planting path)."""
    conn = sqlite3.connect(str(Path(directory) / "autobot.db"),
                           isolation_level=None)
    try:
        return conn.execute(statement, parameters).fetchall()
    finally:
        conn.close()


def object_path(directory, address):
    hexpart = address.split(":", 1)[1]
    return Path(directory) / "store" / "objects" / hexpart[:2] / hexpart


class AuditorIsolationTests(unittest.TestCase):
    def test_auditor2_imports_only_the_stdlib_and_its_sibling_oracle(self):
        assert_oracle_isolated(
            self, AUDITOR2_PATH,
            allowed=("hashlib", "json", "os", "sqlite3", "sys",
                     "unicodedata", "preimage2"))

    def test_no_production_object_is_reachable_from_the_module(self):
        for name, value in sorted(vars(auditor2).items()):
            origin = getattr(value, "__module__", None) or ""
            self.assertFalse(
                origin.split(".")[0] in PRODUCTION_PACKAGES,
                "auditor2.%s comes from %s" % (name, origin))

    def test_restated_vocabulary_pins_the_chapter0_registry(self):
        """The terminal set and initial states the auditor restates ARE
        states.json's."""
        machines = support.load_json(
            support.CH0_REGISTRIES / "states.json")["machines"]
        self.assertEqual(
            auditor2.SEQUENCE_TERMINAL,
            frozenset(state["id"] for state in
                      machines["sequence"]["states"] if state["terminal"]))
        initial = dict((machine, row[4]) for row in auditor2.STATE_MACHINES
                       for machine in (row[0],))
        for machine in ("sequence", "attempt", "effect_intent"):
            self.assertEqual(initial[machine],
                             machines[machine]["initial"], machine)
            self.assertEqual(auditor2.REPLAY_INITIAL[machine],
                             machines[machine]["initial"], machine)
        self.assertEqual(auditor2.REPLAY_INITIAL["contract_admission"],
                         machines["contract_admission"]["initial"])

    def test_every_ledger_column_has_a_role_or_holds_no_digest(self):
        """The role table covers the LIVE schema: every column the current
        migrations create is either classified or cannot hold a digest by
        construction (the runtime check is the audit itself; this pins the
        table against a silently added column)."""
        with tempfile.TemporaryDirectory() as tmp:
            rt = support.make_runtime(tmp, support.manual_clock())
            columns = set()
            for (table,) in rt.db.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall():
                for row in rt.db.conn.execute(
                        "PRAGMA table_info(%s)" % table).fetchall():
                    columns.add((table, row[1]))
            rt.db.close()
        self.assertEqual(set(auditor2.COLUMN_ROLES) - columns, set(),
                         "the role table names columns that do not exist")
        digest_capable = set(
            column for column in columns
            if column[1].endswith(("_sha256", "_hash", "_digest",
                                   "_address")))
        self.assertEqual(digest_capable - set(auditor2.COLUMN_ROLES), set(),
                         "a digest-capable column has no audit role")


class ScenarioSweepTests(unittest.TestCase):
    """The oracle over real production runtimes: nothing found, the right
    ledger read, the retention walk equal to the Sequencer's, and the
    production recovery agreeing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.db.close()
        self.tmp.cleanup()

    def _sweep(self):
        report = auditor2.audit(self.dir)
        self.assertEqual(report["findings"], [],
                         describe(report["findings"]))
        conn = self.rt.db.conn
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]
        head = conn.execute(
            "SELECT entry_hash FROM transition_journal ORDER BY seq DESC"
            " LIMIT 1").fetchone()["entry_hash"]
        # the witness: the oracle read THIS directory's ledger, all of it
        self.assertEqual(report["witness"]["data_dir"],
                         os.path.realpath(str(self.dir)))
        self.assertGreater(count, 0)
        self.assertEqual(report["witness"]["journal_entries"], count)
        self.assertEqual(report["witness"]["journal_head"], head)
        self.assertEqual(report["witness"]["rows"]["sequences"],
                         conn.execute("SELECT COUNT(*) AS n FROM sequences"
                                      ).fetchone()["n"])
        # retention reachability: two walkers, one map
        self.assertEqual(report["retention_roots"],
                         self.rt.sequencer.retention_roots())
        # the production recovery agrees (and reports the same leaf gaps)
        recovered = self.rt.sequencer.recover()
        self.assertEqual(recovered["journal_entries"], count)
        self.assertEqual(
            sorted((gap["sequence_id"], gap["registration"], gap["leaf"])
                   for gap in recovered["retention"][
                       "missing_bound_leaves"]),
            sorted((gap["sequence_id"], gap["registration"], gap["leaf"])
                   for gap in report["reported"].get(
                       "missing_bound_leaves", [])))
        return report

    def _states(self):
        return dict((row["sequence_id"], row["state"]) for row in
                    self.rt.db.conn.execute(
                        "SELECT sequence_id, state FROM sequences"))

    def test_drive_to_verified_flow(self):
        self.rt, facts = build_verified(self.dir)
        self.assertEqual(self._states()[facts["admitted"]["sequence_id"]],
                         "VERIFIED")
        report = self._sweep()
        self.assertGreater(report["witness"]["store_objects"], 20)
        self.assertGreater(len(report["required_references"]), 15)

    def test_fail_repair_then_halt_flow(self):
        self.rt, facts = build_repair_then_halt(self.dir)
        self.assertEqual(facts["outcomes"],
                         [{"attempt": "REJECTED", "sequence": "READY"},
                          {"attempt": "REJECTED", "sequence": "HALTED"}])
        self._sweep()

    def test_attested_flow(self):
        self.rt, facts = build_attested(self.dir)
        self.assertEqual(facts["result"]["attempt"], "VERIFIED")
        self._sweep()
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_attestations"
        ).fetchone()["n"], 1)

    def test_transform_mismatch_and_calibrated_pass_flows(self):
        self.rt, facts = build_transform_flows(self.dir)
        self.assertEqual(facts["mismatch"],
                         {"attempt": "REJECTED", "sequence": "READY"})
        self.assertEqual(facts["passed"]["attempt"], "VERIFIED")
        report = self._sweep()
        # the normalized output is durable execution evidence the oracle
        # resolved independently (it is NOT the declared input's address)
        self.assertIn(content_address(RECEIVED_INPUT.encode("utf-8")),
                      report["required_references"])

    def test_source_local_loop_through_completion(self):
        self.rt, facts = build_sl_loop(self.dir)
        states = self._states()
        self.assertEqual(states[facts["a"]["sequence_id"]], "COMPLETED")
        self.assertEqual(states[facts["b"]["sequence_id"]], "READY")
        report = self._sweep()
        for address in (facts["acceptance"],
                        facts["released"]["release_address"]):
            self.assertIn(address, report["required_references"])
        # the completed sequence is terminal: it roots nothing any more
        for ids in report["retention_roots"].values():
            self.assertNotIn(facts["a"]["sequence_id"], ids)

    def test_read_only_completion_flow(self):
        self.rt, facts = build_read_only_completed(self.dir)
        self.assertEqual(self._states()[facts["admitted"]["sequence_id"]],
                         "COMPLETED")
        self._sweep()

    def test_effect_intent_history(self):
        self.rt, facts = build_intent_history(self.dir)
        self.assertEqual(facts["expired"], [facts["first"]])
        self.assertEqual(facts["resolved"], "HAPPENED")
        self._sweep()
        states = dict((row["intent_id"], row["state"]) for row in
                      self.rt.db.conn.execute(
                          "SELECT intent_id, state FROM effect_intents"))
        self.assertEqual(states, {facts["first"]: "SUPERSEDED_BY_RETRY",
                                  facts["successor"]: "HAPPENED"})

    def test_packaged_closures_and_gapped_registrations(self):
        """The retention walk over every closure form: a packaged manifest
        with entries, a manifest whose bytes are absent, a manifest that
        is not a PackageManifest, a manifest whose entries alias each
        other (F-TI-6) and the image form - the two walkers still agree
        leaf for leaf."""
        self.rt, _va = base_runtime(self.dir)
        rt = self.rt
        rt.store.put(support.LIVE_PASS_SCRIPT)
        data = rt.store.put(b"packaged data file\n")
        manifest = rt.store.put(canonicalize(packaged_manifest_payload()))
        junk = rt.store.put(b"\xff\xfe not a manifest")
        absent = "sha256:" + "7" * 64
        # F-TI-6: well-formed JSON whose two entry paths are one file
        # under case folding - not a valid manifest for either walker
        twin_entry = rt.store.put(b"an entry of a case-fold twin manifest\n")
        twins = rt.store.put(canonicalize({
            "schema_version": "v1", "kind": "PackageManifest",
            "entries": [{"path": "TOOL.py", "content_sha256": twin_entry},
                        {"path": "tool.py", "content_sha256": twin_entry}],
            "entrypoint": "tool.py"}))
        closures = (
            ("e1", {"kind": "packaged", "manifest_sha256": manifest}),
            ("e2", {"kind": "packaged", "manifest_sha256": absent}),
            ("e3", {"kind": "packaged", "manifest_sha256": junk}),
            ("e4", {"kind": "packaged",
                    "image_digest": "sha256:" + "8" * 64}),
            ("e5", {"kind": "native",
                    "executable_sha256": content_address(
                        support.LIVE_PASS_SCRIPT)}),
            ("e6", {"kind": "packaged", "manifest_sha256": twins}),
        )
        bound = {}
        for suffix, closure in closures:
            registration = {
                "schema_version": "v1", "kind": "VerifierRegistration",
                "registration_id": "rg_" + suffix.rjust(32, "0"),
                "verifier_id": "vector-checker", "version": "2.0.0",
                "closure": closure, "artifact_root": artifact_root(closure),
                "entrypoint": "verify.py", "execution_requirements": [],
                "registered_by": "you",
                "registered_utc": "2026-11-13T00:00:00.000Z"}
            rt.registry.admit_object(registration, submitted_by="you")
            record = support.local_record_payload(
                task_contract_id="tc_" + ("c10" + suffix[-1]) * 8,
                objective="TI-1 closure form %s" % suffix)
            record["resolved_bindings"]["verifier_registrations"] = sorted((
                support.live_registration_hash("registry-checker", "PASS"),
                object_hash("VerifierRegistration", registration)))
            bound[suffix] = admit(rt, record)["sequence_id"]
        report = self._sweep()
        roots = report["retention_roots"]
        self.assertEqual(roots[manifest], [bound["e1"]])
        self.assertEqual(roots[data], [bound["e1"]])
        self.assertEqual(roots[absent], [bound["e2"]])
        self.assertEqual(roots[junk], [bound["e3"]])
        self.assertNotIn("sha256:" + "8" * 64, roots)
        self.assertIn(bound["e5"], roots[content_address(
            support.LIVE_PASS_SCRIPT)])
        # the twin manifest is bound; its entries cannot be known
        self.assertEqual(roots[twins], [bound["e6"]])
        self.assertNotIn(twin_entry, roots)


class PlantedCorruptionTests(unittest.TestCase):
    """The oracle tested like a checker: one clean runtime (the SL loop,
    which populates every table), copied; the control copy passes; each
    planted defect makes exactly the named check fail."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name) / "base"
        rt, cls.facts = build_sl_loop(cls.base)
        conn = rt.db.conn
        attempt_id = cls.facts["info"]["attempt_id"]
        cls.attempt_id = attempt_id
        cls.rows = dict(
            (row["assertion_id"], dict(row)) for row in conn.execute(
                "SELECT * FROM attempt_verifications WHERE attempt_id = ?",
                (attempt_id,)))
        cls.witnesses = dict(
            (row["witness_kind"], dict(row)) for row in conn.execute(
                "SELECT * FROM attempt_witnesses WHERE attempt_id = ?",
                (attempt_id,)))
        cls.input_address = content_address(
            support.VA_INPUT.encode("utf-8"))
        cls.va_record = support.load_fixture("vector_payloads.json")["V-E"]
        cls.flat_address = rt.store.put(canonicalize(cls.va_record))
        rt.db.close()
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _copy(self):
        type(self).counter += 1
        return clone_runtime(
            self.base, Path(self.tmp.name) / ("copy%d" % self.counter))

    def _flip(self, directory, address):
        path = object_path(directory, address)
        data = bytearray(path.read_bytes())
        data[-1] ^= 0x01
        path.write_bytes(bytes(data))

    def _defects(self):
        """(name, plant(copy_dir), check the audit must then report)."""
        a_id = self.facts["a"]["sequence_id"]
        b_id = self.facts["b"]["sequence_id"]
        attempt_id = self.attempt_id
        a1, a2 = self.rows["a1"], self.rows["a2"]
        defects = []

        def add(name, plant, check):
            defects.append((name, plant, check))

        # ---- the journal
        add("journal_forged_payload", lambda d: sql(
            d, "UPDATE transition_journal SET payload = '{\"forged\":true}'"
               " WHERE seq = (SELECT MIN(seq) FROM transition_journal)"),
            "journal.entry_hash")

        def rewrite_fact(d):
            seq, text = sql(
                d, "SELECT seq, payload FROM transition_journal WHERE"
                   " payload LIKE '%\"rule\":\"t-s10\"%'")[0]
            forged = text.replace('"to_state":"VERIFIED"',
                                  '"to_state":"ACCEPTED"')
            assert forged != text
            sql(d, "UPDATE transition_journal SET payload = ? WHERE seq = ?",
                (forged, seq))
        add("journal_fact_rewritten", rewrite_fact, "journal.entry_hash")

        def respace(d):
            seq, text = sql(
                d, "SELECT seq, payload FROM transition_journal"
                   " ORDER BY seq LIMIT 1")[0]
            sql(d, "UPDATE transition_journal SET payload = ? WHERE seq = ?",
                (json.dumps(json.loads(text), indent=1, sort_keys=True),
                 seq))
        add("journal_payload_not_canonical", respace,
            "journal.payload_not_canonical")

        def relink(d):
            hashes = [row[0] for row in sql(
                d, "SELECT entry_hash FROM transition_journal ORDER BY seq")]
            sql(d, "UPDATE transition_journal SET previous_hash = ? WHERE"
                   " entry_hash = ?", (hashes[0], hashes[-1]))
        add("journal_entry_relinked", relink, "journal.chain_link")

        def truncate_tail(d):
            sql(d, "DELETE FROM transition_journal WHERE seq = (SELECT"
                   " MAX(seq) FROM transition_journal)")
        # the chain still validates: only the pairing sees a lost tail
        add("journal_tail_truncated", truncate_tail, "pairing.sequence")

        def rehash_tail(d):
            """A forger who KNOWS the rule rewrites the last entry and
            recomputes its hash: the chain validates, the pairing does
            not."""
            seq, previous, text = sql(
                d, "SELECT seq, previous_hash, payload FROM"
                   " transition_journal ORDER BY seq DESC LIMIT 1")[0]
            payload = json.loads(text)
            payload["to_state"] = "HALTED"
            sql(d, "UPDATE transition_journal SET payload = ?, entry_hash"
                   " = ? WHERE seq = ?",
                (preimage2.canonical_text(payload),
                 preimage2.journal_entry_hash2(payload, previous), seq))
        add("journal_tail_rehashed_by_a_forger", rehash_tail,
            "pairing.sequence")

        # ---- rows against their journal
        add("sequence_state_forged", lambda d: sql(
            d, "UPDATE sequences SET state = 'HALTED' WHERE sequence_id = ?",
            (b_id,)), "pairing.sequence")
        add("sequence_version_forged", lambda d: sql(
            d, "UPDATE sequences SET version = version + 5 WHERE"
               " sequence_id = ?", (a_id,)), "pairing.sequence_version")
        add("sequence_fence_forged", lambda d: sql(
            d, "UPDATE sequences SET fence = fence + 1 WHERE sequence_id"
               " = ?", (b_id,)), "pairing.sequence_fence")
        add("attempt_state_forged", lambda d: sql(
            d, "UPDATE attempts SET state = 'REJECTED' WHERE attempt_id = ?",
            (attempt_id,)), "pairing.attempt")
        add("attempt_version_forged", lambda d: sql(
            d, "UPDATE attempts SET version = version + 1 WHERE attempt_id"
               " = ?", (attempt_id,)), "pairing.attempt_version")
        add("reservation_state_forged", lambda d: sql(
            d, "UPDATE reservations SET state = 'RELEASED'"),
            "pairing.permit_reservation")
        add("intent_state_forged", lambda d: sql(
            d, "UPDATE effect_intents SET state = 'ABSENT'"),
            "pairing.effect_intent")
        add("admission_state_forged", lambda d: sql(
            d, "UPDATE admissions SET state = 'REJECTED' WHERE admission_id"
               " = (SELECT MAX(admission_id) FROM admissions)"),
            "pairing.contract_admission")
        add("journaled_sequence_row_deleted", lambda d: sql(
            d, "DELETE FROM sequences WHERE sequence_id = ?", (b_id,)),
            "pairing.sequence")
        add("permit_counter_drift", lambda d: sql(
            d, "UPDATE permit_state SET reserved_uses = reserved_uses + 1"),
            "permits.counters")
        add("repair_counter_forged", lambda d: sql(
            d, "UPDATE sequences SET repairs_used = 1 WHERE sequence_id = ?",
            (a_id,)), "pairing.sequence_repairs")
        add("lease_epoch_forged", lambda d: sql(
            d, "UPDATE instance_lease SET epoch = epoch + 1"),
            "pairing.instance_lease")
        add("released_edge_forged", lambda d: sql(
            d, "UPDATE released_edges SET edge_value = ? WHERE edge_kind ="
               " 'RECORD'", ("sha256:" + "9" * 64,)),
            "pairing.released_edge")
        add("released_edge_deleted", lambda d: sql(
            d, "DELETE FROM released_edges WHERE edge_kind = 'CONTRACT'"),
            "pairing.released_edge")

        # ---- content: bytes behind committed references
        add("receipt_byte_flipped", lambda d: self._flip(
            d, a2["receipt_sha256"]), "content.altered")
        add("receipt_bytes_deleted", lambda d: object_path(
            d, a1["receipt_sha256"]).unlink(), "content.missing")
        add("specification_input_altered", lambda d: self._flip(
            d, self.input_address), "content.altered")
        add("acceptance_receipt_deleted", lambda d: object_path(
            d, self.facts["acceptance"]).unlink(), "content.missing")
        add("verifier_output_evidence_deleted", lambda d: object_path(
            d, json.loads(object_path(d, a2["receipt_sha256"]).read_text(
                encoding="utf-8"))["execution"]["output_sha256"]).unlink(),
            "content.missing")

        # ---- bindings retargeted by direct UPDATE (bytes all resolve)
        add("receipt_column_retargeted", lambda d: sql(
            d, "UPDATE attempt_verifications SET receipt_sha256 = ? WHERE"
               " verification_id = ?",
            (a1["receipt_sha256"], a2["verification_id"])),
            "binding.obj-19/20")
        add("environment_column_retargeted", lambda d: sql(
            d, "UPDATE attempt_verifications SET environment_sha256 = ?"
               " WHERE verification_id = ?",
            (a1["receipt_sha256"], a1["verification_id"])),
            "binding.obj-19/20")
        add("witness_column_retargeted", lambda d: sql(
            d, "UPDATE attempt_witnesses SET content_sha256 = ? WHERE"
               " admission_id = ?",
            (self.witnesses["obj-13"]["content_sha256"],
             self.witnesses["obj-12"]["admission_id"])),
            "pairing.attempt_witness")
        add("candidate_tree_forged", lambda d: sql(
            d, "UPDATE attempt_candidates SET candidate_tree = ?",
            ("deadbeef" * 5,)), "binding.obj-16")
        add("candidate_column_retargeted", lambda d: sql(
            d, "UPDATE attempt_candidates SET content_sha256 = ?",
            (a1["receipt_sha256"],)), "binding.obj-16")
        add("environment_row_deleted", lambda d: sql(
            d, "DELETE FROM attempt_environments"),
            "pairing.attempt_environment")
        add("verification_outcome_forged", lambda d: sql(
            d, "UPDATE attempt_verifications SET outcome = 'FAIL' WHERE"
               " verification_id = ?", (a2["verification_id"],)),
            "binding.obj-19/20")
        add("projection_column_retargeted", lambda d: sql(
            d, "UPDATE attempt_calls SET projection_address = ?",
            ("sha256:" + "0" * 64,)), "content.missing")

        # ---- identities
        add("registration_identity_forged", lambda d: sql(
            d, "UPDATE objects SET object_hash = ? WHERE object_hash = ?",
            ("sha256:" + "f" * 64,
             support.live_registration_hash("vector-checker", "PASS"))),
            "identity.object_hash")
        add("record_tier_forged", lambda d: sql(
            d, "UPDATE records SET contract_digest = ? WHERE record_hash"
               " = ?", ("sha256:" + "e" * 64, self.facts["a"]["record_hash"])),
            "identity.record_tiers")
        add("flat_record_identity_planted", lambda d: sql(
            d, "INSERT INTO objects (object_hash, kind, content_address,"
               " minted_utc) VALUES (?, 'TaskContractRecord', ?, ?)",
            (object_hash("TaskContractRecord", self.va_record),
             self.flat_address, "2026-11-13T00:00:00.000Z")),
            "identity.object_hash")

        # ---- the schema itself
        def shadow_column(d):
            sql(d, "ALTER TABLE sequences ADD COLUMN shadow_address TEXT")
            sql(d, "UPDATE sequences SET shadow_address = ?",
                (a1["receipt_sha256"],))
        add("unclassified_digest_column", shadow_column,
            "ledger.unclassified_digest_column")
        return defects

    def test_control_copy_passes(self):
        report = auditor2.audit(self._copy())
        self.assertEqual(report["findings"], [],
                         describe(report["findings"]))
        self.assertGreater(report["witness"]["journal_entries"], 50)

    def test_planted_defects_caught(self):
        defects = self._defects()
        self.assertGreaterEqual(len(defects), 35)
        for name, plant, expected in defects:
            with self.subTest(defect=name):
                copy = self._copy()
                control = auditor2.audit(copy)
                self.assertEqual(control["findings"], [],
                                 "%s: the control copy must pass first"
                                 % name)
                plant(copy)
                report = auditor2.audit(copy)
                self.assertIn(
                    expected, checks_of(report),
                    "%s: expected %s, got:\n%s"
                    % (name, expected, describe(report["findings"])))

    def test_an_audit_never_writes(self):
        """Read-only by construction: the ledger bytes and the directory
        listing are identical after an audit."""
        copy = self._copy()

        def snapshot():
            listing = sorted(
                str(path.relative_to(copy)) for path in copy.rglob("*"))
            digest = hashlib.sha256(
                (copy / "autobot.db").read_bytes()).hexdigest()
            return listing, digest

        before = snapshot()
        report = auditor2.audit(copy)
        self.assertEqual(report["witness"]["read_mode"], "immutable")
        auditor2.audit(copy)
        self.assertEqual(snapshot(), before)
        # A ledger a writer still holds open: the audit reads THROUGH the
        # WAL (it sees a commit the main file does not hold yet), and
        # neither the ledger file nor the WAL changes, nor does any file
        # appear or disappear.
        writer = sqlite3.connect(str(copy / "autobot.db"),
                                 isolation_level=None)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO meta (key, value) VALUES"
                           " ('ti1-wal-probe', 'committed in the WAL')")
            writer.execute("COMMIT")

            def live_snapshot():
                return (sorted(path.name for path in copy.iterdir()),
                        hashlib.sha256((copy / "autobot.db").read_bytes()
                                       ).hexdigest(),
                        hashlib.sha256(
                            (copy / "autobot.db-wal").read_bytes()
                        ).hexdigest())

            live_before = live_snapshot()
            self.assertIn("autobot.db-wal", live_before[0])
            report = auditor2.audit(copy)
            self.assertEqual(report["witness"]["read_mode"],
                             "wal-read-only")
            self.assertEqual(report["witness"]["rows"]["meta"], 2)
            self.assertEqual(report["findings"], [])
            self.assertEqual(live_snapshot(), live_before)
        finally:
            writer.close()

    def test_path_confusion_is_never_a_clean_audit(self):
        """An audit that could not read a ledger says so; it is never
        'clean'. And a clean audit of ANOTHER runtime cannot pass for this
        one: the journal witness differs."""
        root = Path(self.tmp.name)
        with self.assertRaises(auditor2.AuditUnavailable):
            auditor2.audit(root / "does-not-exist")
        empty = root / "empty-dir"
        empty.mkdir()
        with self.assertRaises(auditor2.AuditUnavailable):
            auditor2.audit(empty)
        foreign = root / "foreign"
        (foreign / "store" / "objects").mkdir(parents=True)
        conn = sqlite3.connect(str(foreign / "autobot.db"))
        conn.execute("CREATE TABLE notes (body TEXT)")
        conn.commit()
        conn.close()
        with self.assertRaises(auditor2.AuditUnavailable) as caught:
            auditor2.audit(foreign)
        self.assertIn("not an Autobot ledger", str(caught.exception))
        no_store = root / "no-store"
        no_store.mkdir()
        shutil.copy2(str(self.base / "autobot.db"),
                     str(no_store / "autobot.db"))
        with self.assertRaises(auditor2.AuditUnavailable):
            auditor2.audit(no_store)
        # the audit did not create the ledger it was pointed at
        self.assertFalse((empty / "autobot.db").exists())
        other = root / "other-runtime"
        rt, _va = support.admitted_runtime(other)
        rt.db.close()
        mine = auditor2.audit(self._copy())["witness"]
        theirs = auditor2.audit(other)["witness"]
        self.assertNotEqual(mine["journal_head"], theirs["journal_head"])
        self.assertNotEqual(mine["journal_entries"],
                            theirs["journal_entries"])


class FreshProcessTests(unittest.TestCase):
    """The test_cross_process pattern: a separate, isolated interpreter
    audits a PRESERVED runtime directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _child(self, directory):
        result = subprocess.run(
            [sys.executable, "-I", str(AUDITOR2_PATH), str(directory)],
            capture_output=True, text=True, timeout=180)
        report = json.loads(result.stdout) if result.stdout.strip() else None
        return result, report

    def test_fresh_process_reproduces_the_in_process_report(self):
        rt, _facts = build_verified(self.dir / "runtime")
        rt.db.close()
        preserved = self.dir / "runtime"
        in_process = auditor2.audit(preserved)
        result, child = self._child(preserved)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = set(child.pop("loaded_modules"))
        self.assertEqual(loaded & set(PRODUCTION_PACKAGES), set(),
                         "the audit process loaded production code")
        self.assertIn("preimage2", loaded)
        self.assertEqual(child, in_process)
        self.assertEqual(child["findings"], [])
        # the same fresh process over a planted defect exits 1 and names it
        receipt = sql(preserved, "SELECT receipt_sha256 FROM"
                                 " attempt_verifications LIMIT 1")[0][0]
        path = object_path(preserved, receipt)
        data = bytearray(path.read_bytes())
        data[0] ^= 0x01
        path.write_bytes(bytes(data))
        result, child = self._child(preserved)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("content.altered", checks_of(child))

    def test_fresh_process_over_a_hard_killed_child(self):
        """The crash matrix's process boundary, read by the independent
        oracle: the child wrote the ledger and died hard (exit 137); a
        fresh isolated interpreter audits exactly what it left."""
        data_dir = self.dir / "crashed"
        proc = subprocess.run(
            [sys.executable, "-I", str(CRASH_CHILD), str(data_dir),
             "verifying"],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(proc.returncode, 137, proc.stdout + proc.stderr)
        snapshot = json.loads(proc.stdout.strip().splitlines()[-1])
        result, child = self._child(data_dir)
        self.assertEqual(result.returncode, 0,
                         result.stderr + describe(
                             child["findings"] if child else []))
        self.assertEqual(child["findings"], [])
        self.assertEqual(child["witness"]["journal_entries"],
                         snapshot["journal_entries"])
        self.assertEqual(
            set(child["loaded_modules"]) & set(PRODUCTION_PACKAGES), set())

    def test_unreadable_directory_exits_two(self):
        result, child = self._child(self.dir / "nothing-here")
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(child)
        self.assertIn("auditor2:", result.stderr)


class RegistrySeamTests(unittest.TestCase):
    """F-TI-1 at the seam where it was reachable: the flat object
    admission could index a TaskContractRecord under an identity recovery
    itself rejects. Control first, then the plant."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rt, self.va = support.admitted_runtime(self.dir)
        self.vectors = support.load_fixture("vector_payloads.json")

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _counts(self):
        conn = self.rt.db.conn
        return (conn.execute("SELECT COUNT(*) AS n FROM objects"
                             ).fetchone()["n"],
                conn.execute("SELECT COUNT(*) AS n FROM transition_journal"
                             ).fetchone()["n"])

    def test_record_enters_only_through_the_admission_machine(self):
        # control: the registered path admits the record and both
        # recoveries stay clean
        control = self.rt.registry.admit_payload(self.vectors["V-C"],
                                                 submitted_by="planner")
        self.assertEqual(control["outcome"], "NEW_RECORD")
        self.rt.sequencer.recover()
        self.assertEqual(auditor2.audit(self.dir)["findings"], [])
        before = self._counts()
        # plant: the same kind of payload through the flat object seam
        with self.assertRaises(AdmissionError) as caught:
            self.rt.registry.admit_object(self.vectors["V-E"],
                                          submitted_by="you")
        self.assertIn("enters only through admit_payload",
                      str(caught.exception))
        self.assertEqual(self._counts(), before)  # nothing committed
        self.rt.sequencer.recover()
        self.assertEqual(auditor2.audit(self.dir)["findings"], [])

    def test_a_flat_record_identity_is_refused_by_both_recoveries(self):
        """What the seam forecloses, planted directly: an objects row
        indexing a record under the library's flat hash. The production
        recovery HALTs on it (INV-11) and the independent oracle names
        the same row."""
        self.rt.sequencer.recover()  # the control
        self.assertEqual(auditor2.audit(self.dir)["findings"], [])
        record = self.vectors["V-E"]
        flat = object_hash("TaskContractRecord", record)
        address = self.rt.store.put(canonicalize(record))
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO objects (object_hash, kind, content_address,"
                " minted_utc) VALUES (?, 'TaskContractRecord', ?, ?)",
                (flat, address, "2026-11-13T00:00:00.000Z"))
        with self.assertRaises(IntegrityHalt) as caught:
            self.rt.sequencer.recover()
        self.assertIn("does not recompute", str(caught.exception))
        report = auditor2.audit(self.dir)
        named = [finding for finding in report["findings"]
                 if finding["check"] == "identity.object_hash"]
        self.assertEqual([finding["subject"] for finding in named], [flat])


class RetentionOracleTests(unittest.TestCase):
    """INV-58 from the outside: whatever the independent oracle finds the
    ledger REQUIRES must survive an explicit reclaim of exactly those
    addresses.

    This oracle is where F-TI-4 surfaced: reclaim protected closure roots,
    frozen inputs and a list of ledger columns, while recovery ALSO
    resolves the journaled finalization receipts (obj-26/obj-30/obj-35,
    two of them released_edges columns the list lacked) and the execution
    evidence every admitted obj-19/obj-20 binds. Reclaiming any of those
    turned a clean ledger into a permanent recovery HALT (D-66)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.rt = None

    def tearDown(self):
        if self.rt is not None:
            self.rt.db.close()
        self.tmp.cleanup()

    def _reclaim_everything_required(self):
        before = auditor2.audit(self.dir)
        self.assertEqual(before["findings"], [],
                         describe(before["findings"]))  # the control
        required = before["required_references"]
        self.assertGreater(len(required), 20)
        outcome = self.rt.sequencer.reclaim(required)
        self.assertEqual(
            outcome["removed"], [],
            "reclaim deleted bytes the ledger still requires")
        self.assertEqual(sorted(outcome["retained"]), required)
        after = auditor2.audit(self.dir)
        self.assertEqual(after["findings"], [], describe(after["findings"]))
        self.rt.sequencer.recover()
        return outcome

    def test_reclaim_retains_every_required_reference_after_completion(self):
        """The whole SL loop is COMPLETED (terminal): no closure root
        protects anything of it any more, so only reclaim's own committed-
        reference rules stand between the named addresses and deletion."""
        self.rt, facts = build_sl_loop(self.dir)
        outcome = self._reclaim_everything_required()
        for address in (facts["acceptance"],
                        facts["released"]["release_address"]):
            self.assertIn(address, outcome["retained"])
            # the refusal names its reason (matrix rows pin this text)
            self.assertIn("journaled finalization receipt",
                          outcome["retained"][address])

    def test_reclaim_retains_execution_evidence_distinct_from_inputs(self):
        """The calibrated transform's normalized output is execution
        evidence a receipt binds and recovery re-reads; it is nobody's
        specification input and no ledger column holds it."""
        self.rt, _facts = build_transform_flows(self.dir)
        outcome = self._reclaim_everything_required()
        evidence = content_address(RECEIVED_INPUT.encode("utf-8"))
        self.assertIn(evidence, outcome["retained"])
        self.assertIn("execution evidence bound",
                      outcome["retained"][evidence])


if __name__ == "__main__":
    unittest.main()
