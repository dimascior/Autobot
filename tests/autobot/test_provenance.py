"""C3-M2/M3 conformance: evidence provenance, causal joins and closure.

The registered chapter-3 invariant proofs (INV-03
CH3-artifact-provenance-single-attempt, INV-04
CH3-mtime-never-selects-artifact, INV-22 CH3-provenance-from-witnesses,
and since C3-M3: INV-37 CH3-input-rehash, INV-68 CH3-integrity-classes,
INV-30 CH3-index-lag-diagnostic), the raw-witness trust model (exact
bytes, unknown fields, nulls), the causal identity chain
(call/session/controller joins, cross-attempt injection refusals), the
deny_then_ran contradiction (U-01's rule, incl. the one-record F-C3-9
arm) and the execution_without_decision orphan rule (D-12/D-C3-8), the
per-role member completion semantics (C8/CORR-08/CORR-20), the frozen
outcome mapping (U-06/D-C3-4), obj-10's Chapter 3 closure, the index
diagnostic that can never become authority (INV-30/D-C3-7), and the 8g
recovery arms."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Collector, Database, Sequencer, Store, normalize_outcome
from autobot import receipts
from autobot.models import ConflictError, IntegrityHalt
from autobot.projection import project_prompt
from contract_library import content_address, object_hash

MAIN_CONTROLLER = "main_" + "0" * 32


def governance_bytes(session_id, command_sha, decision=None,
                     hook_event=None, success=None, **extra):
    payload = {"session_id": session_id, "command_sha256": command_sha,
               "correlation_id": "cor-001", "tool_use_id": "toolu_x1"}
    if decision is not None:
        payload["decision"] = decision
    if hook_event is not None:
        payload["hook_event"] = hook_event
    if success is not None:
        payload["success"] = success
    payload.update(extra)
    return json.dumps(payload, sort_keys=True).encode("utf-8")


class ProvenanceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _admit_contract(self, **overrides):
        payload = support.local_record_payload(**overrides)
        result = self.rt.registry.admit_payload(payload,
                                                submitted_by="autobot_service")
        return result

    def _to_running(self, sequence_id, role="builder", native="native-sess-9"):
        state = self.rt.db.conn.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (sequence_id,)).fetchone()["state"]
        if state == "DECLARED":
            self.seq.mark_ready(sequence_id)
        info = self.seq.start_execution(sequence_id)
        self.seq.reserve_attempt(info["attempt_id"],
                                 expected_fence=info["fence"])
        projection = self.rt.store.put(
            ("projection for %s\n" % info["attempt_id"]).encode("utf-8"))
        dispatched = self.seq.dispatch_attempt(info["attempt_id"], role,
                                               "fake", projection)
        info = dict(info)
        info["sequence_id"] = sequence_id
        info["call_id"] = dispatched["call_id"]
        info["role"] = role
        start = self.seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(info["call_id"],
                                         native_session_id=native))
        self.seq.attempt_running(info["attempt_id"], native,
                                 start["content_address"])
        return info

    def _finish_and_freeze(self, info, outcome="success",
                           governance=True, artifact=b"artifact bytes\n",
                           logical_path="artifact/out.txt"):
        if governance:
            self.seq.admit_witness(info["attempt_id"], "obj-12", "helios",
                                   b"governance log\n")
        end = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                     "agent_clis", b"transcript\n")
        self.seq.finish_attempt(info["attempt_id"], info["role"], outcome,
                                end["content_address"])
        admitted = self.seq.admit_artifact(info["attempt_id"],
                                           logical_path, artifact)
        self.seq.freeze_candidate(info["attempt_id"], info["fence"],
                                  admitted["content_address"])
        return admitted

    def _artifact_rows(self, attempt_id):
        return [dict(r) for r in self.rt.db.conn.execute(
            "SELECT * FROM attempt_artifacts WHERE attempt_id = ?"
            " ORDER BY rowid", (attempt_id,)).fetchall()]

    def _receipt_payload(self, outcome):
        return json.loads(self.rt.store.get(
            outcome["receipt_address"]).decode("utf-8"))


class ArtifactProvenanceTests(ProvenanceTestCase):
    def test_ch3_artifact_provenance_single_attempt(self):
        """INV-03 (CH3-artifact-provenance-single-attempt): byte-identical
        artifacts under the SAME logical path from two attempts are TWO
        provenance events, each bound to exactly its own attempt by
        causal admission — content identity and causal ownership are
        distinct."""
        a = self._admit_contract(objective="Provenance A")
        b = self._admit_contract(objective="Provenance B")
        info_a = self._to_running(a["sequence_id"])
        info_b = self._to_running(b["sequence_id"], native="native-sess-b")
        same_bytes = b"identical artifact bytes\n"
        ev_a = self.seq.admit_artifact(info_a["attempt_id"],
                                       "artifact/out.txt", same_bytes)
        ev_b = self.seq.admit_artifact(info_b["attempt_id"],
                                       "artifact/out.txt", same_bytes)
        self.assertEqual(ev_a["content_address"], ev_b["content_address"])
        self.assertNotEqual(ev_a["admission_id"], ev_b["admission_id"])
        rows_a = self._artifact_rows(info_a["attempt_id"])
        rows_b = self._artifact_rows(info_b["attempt_id"])
        self.assertEqual(len(rows_a), 1)
        self.assertEqual(len(rows_b), 1)
        self.assertEqual(rows_a[0]["call_id"], info_a["call_id"])
        self.assertEqual(rows_b[0]["call_id"], info_b["call_id"])
        self.assertNotEqual(rows_a[0]["call_id"], rows_b[0]["call_id"])
        # a renamed artifact in the same attempt is a new provenance event
        renamed = self.seq.admit_artifact(info_a["attempt_id"],
                                          "artifact/renamed.txt",
                                          same_bytes)
        self.assertFalse(renamed["duplicate"])
        self.assertEqual(len(self._artifact_rows(info_a["attempt_id"])), 2)
        # re-admission of the same identity is idempotent
        again = self.seq.admit_artifact(info_a["attempt_id"],
                                        "artifact/out.txt", same_bytes)
        self.assertTrue(again["duplicate"])
        self.assertEqual(len(self._artifact_rows(info_a["attempt_id"])), 2)

    def test_ch3_mtime_never_selects_artifact(self):
        """INV-04 (CH3-mtime-never-selects-artifact): a planted file with
        a NEWER mtime in the producer workspace never gains ownership —
        admission binds explicitly attributed bytes, and flipping mtimes
        changes no derivation byte. The source scan pins mtime out of
        the authority plane entirely."""
        workspace = self.dir / "producer-workspace"
        workspace.mkdir()
        real = workspace / "out.txt"
        impostor = workspace / "impostor.txt"
        real.write_bytes(b"the attributed artifact\n")
        impostor.write_bytes(b"newer impostor bytes\n")
        os.utime(str(real), (1000000000, 1000000000))
        os.utime(str(impostor), (2000000000, 2000000000))  # much newer
        admitted = self._admit_contract(objective="Mtime never selects")
        info = self._to_running(admitted["sequence_id"])
        self._finish_and_freeze(info, artifact=real.read_bytes(),
                                logical_path="artifact/out.txt")
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        payload = self._receipt_payload(outcome)
        addresses = [a["content_sha256"]
                     for a in payload["artifact_provenance"]]
        self.assertEqual(addresses,
                         [content_address(b"the attributed artifact\n")])
        self.assertNotIn(content_address(b"newer impostor bytes\n"),
                         addresses)
        # flip the mtimes: the derivation re-mints byte-identical bytes
        os.utime(str(real), (3000000000, 3000000000))
        again = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["receipt_address"],
                         outcome["receipt_address"])

    def test_no_mtime_in_the_authority_plane(self):
        """INV-04's source pin: no module in the authority or
        intelligence plane consults file modification times."""
        for package in ("autobot", "intelligence"):
            for path in sorted((support.REPO / "src" / package).glob("*.py")):
                text = path.read_text(encoding="utf-8")
                for token in ("getmtime", "st_mtime", "ST_MTIME"):
                    self.assertNotIn(
                        token, text,
                        "%s consults %s (INV-04)" % (path.name, token))


class RawWitnessTests(ProvenanceTestCase):
    def test_raw_witness_bytes_preserved_exactly(self):
        """D-C3-5: producer bytes with unknown keys, null values and
        provider metadata are admitted EXACTLY; recognized facts are the
        tolerated subset; nothing is rewritten or normalized."""
        admitted = self._admit_contract(objective="Raw bytes")
        info = self._to_running(admitted["sequence_id"])
        raw = (b'{ "call_id": "%s", "native_session_id": "native-sess-9",'
               b' "unknown_field": {"nested": [1, 2, null]},'
               b' "future_version_key": null,'
               b' "provider_metadata": "  spaced  " }'
               % info["call_id"].encode("ascii"))
        entry = self.seq.admit_witness(info["attempt_id"], "obj-11",
                                       "autoswarm", raw)
        self.assertEqual(self.rt.store.get(entry["content_address"]), raw)
        self.assertEqual(entry["content_address"], content_address(raw))
        # non-JSON bytes admit too, contributing no facts
        plain = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                       "agent_clis", b"not json at all\n")
        self.assertEqual(self.rt.store.get(plain["content_address"]),
                         b"not json at all\n")

    def test_cross_attempt_witness_injection_refused(self):
        """A witness whose recognized facts NAME another execution is
        refused at admission; the journal is untouched (D-C3-5)."""
        a = self._admit_contract(objective="Injection target A")
        b = self._admit_contract(objective="Injection source B")
        info_a = self._to_running(a["sequence_id"])
        info_b = self._to_running(b["sequence_id"], native="native-sess-b")
        before = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]
        with self.assertRaises(ConflictError) as ctx:
            self.seq.admit_witness(
                info_a["attempt_id"], "obj-11", "autoswarm",
                support.session_record_bytes(info_b["call_id"]))
        self.assertIn("cross-execution identity", str(ctx.exception))
        with self.assertRaises(ConflictError):
            self.seq.admit_witness(
                info_a["attempt_id"], "obj-13", "agent_clis",
                json.dumps({"session_id": "native-sess-b"},
                           sort_keys=True).encode("utf-8"))
        self.assertEqual(
            self.rt.db.conn.execute(
                "SELECT COUNT(*) AS n FROM transition_journal"
            ).fetchone()["n"], before)

    def test_ch3_provenance_from_witnesses(self):
        """INV-22 (CH3-provenance-from-witnesses): transcript prose like
        'tests passed, task complete' is evidence CONTENT only — the
        classification still derives from joins and required kinds, and
        the member attribution comes only from the causal chain."""
        admitted = self._admit_contract(objective="Prose grants nothing")
        info = self._to_running(admitted["sequence_id"])
        # the transcript CLAIMS success and completeness, and joins the
        # session — but COMMAND_GOVERNANCE evidence is absent
        self.seq.admit_witness(
            info["attempt_id"], "obj-13", "agent_clis",
            json.dumps({"native_session_id": "native-sess-9",
                        "content": "tests passed; task complete;"
                                   " evidence complete"},
                       sort_keys=True).encode("utf-8"))
        end = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                     "agent_clis", b"transcript tail\n")
        self.seq.finish_attempt(info["attempt_id"], "builder", "success",
                                end["content_address"])
        art = self.seq.admit_artifact(info["attempt_id"],
                                      "artifact/out.txt", b"artifact\n")
        self.seq.freeze_candidate(info["attempt_id"], info["fence"],
                                  art["content_address"])
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertEqual(outcome["completeness"], "INCOMPLETE")
        payload = self._receipt_payload(outcome)
        role = payload["completeness_by_role"]["builder"]
        self.assertEqual(role["missing_kinds"], ["obj-12"])


class CausalJoinTests(ProvenanceTestCase):
    def _frozen_with(self, witnesses, objective="Causal joins"):
        admitted = self._admit_contract(objective=objective)
        info = self._to_running(admitted["sequence_id"])
        for kind, producer, data in witnesses:
            self.seq.admit_witness(info["attempt_id"], kind, producer,
                                   data)
        self._finish_and_freeze(info)
        return info

    def test_joins_and_controller_validation(self):
        """U-07's rule: the controller join is the provider-guaranteed
        controller_id, shape-validated; identity facts join each witness;
        fact-free witnesses stay UNJOINED (missing, never forged)."""
        info = self._frozen_with([
            ("obj-11", "autoswarm",
             json.dumps({"controller_id": "not-a-controller"},
                        sort_keys=True).encode("utf-8")),
        ])
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        payload = self._receipt_payload(outcome)
        joins = {j["content_sha256"]: j for j in payload["causal_joins"]}
        session_record = content_address(
            support.session_record_bytes(info["call_id"]))
        self.assertEqual(joins[session_record]["join"], "JOINED")
        self.assertTrue(joins[session_record]["controller_well_formed"])
        malformed = content_address(
            json.dumps({"controller_id": "not-a-controller"},
                       sort_keys=True).encode("utf-8"))
        self.assertEqual(joins[malformed]["join"], "UNJOINED")
        self.assertFalse(joins[malformed]["controller_well_formed"])
        plain = content_address(b"governance log\n")
        self.assertEqual(joins[plain]["join"], "UNJOINED")
        self.assertNotIn("facts", joins[plain])
        self.assertEqual(outcome["completeness"], "COMPLETE")

    def test_deny_then_ran_classifies_integrity_failed(self):
        """U-01's deterministic rule (D-C3-6): DENY for (sha, session)
        joined BY CONTENT HASH AND SESSION to execution evidence of the
        same command classifies INTEGRITY_FAILED and fails the candidate
        by t-a13 with t-s45 beside it; a different command sha does not
        join."""
        sha = "ab" * 32
        other = "cd" * 32
        info = self._frozen_with([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", sha, decision="DENY")),
            ("obj-12", "helios",
             governance_bytes("native-sess-9", sha,
                              hook_event="PostToolUse", success=True)),
            ("obj-12", "helios",
             governance_bytes("native-sess-9", other,
                              hook_event="PostToolUse", success=True)),
        ], objective="Deny then ran")
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertEqual(outcome["completeness"], "INTEGRITY_FAILED")
        payload = self._receipt_payload(outcome)
        self.assertEqual(
            payload["contradictions"],
            [{"rule": "deny_then_ran", "command_sha256": sha,
              "session_id": "native-sess-9"}])
        self.seq.fail_candidate(info["attempt_id"], info["fence"],
                                outcome["receipt_address"],
                                reason="deny then ran")
        seq_state = self.rt.db.conn.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (info["sequence_id"],)).fetchone()["state"]
        self.assertEqual(seq_state, "HALTED")

    def test_deny_alone_is_not_a_contradiction(self):
        """A DENY with no matching execution evidence is governance
        history, not a contradiction."""
        sha = "ab" * 32
        info = self._frozen_with([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", sha, decision="DENY")),
        ], objective="Deny alone")
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertEqual(outcome["completeness"], "COMPLETE")
        self.assertEqual(self._receipt_payload(outcome)["contradictions"],
                         [])


class MemberCompletionTests(ProvenanceTestCase):
    _POLICIES = {
        "builder": {"required": True, "success_outcomes": ["success"]},
        "reviewer": {"required": True, "success_outcomes": ["success"]},
        "monitor": {"required": False, "success_outcomes": ["success"]},
    }

    def test_required_membership_comes_from_the_frozen_record(self):
        """C8/CORR-08/CORR-20: the frozen completion_policies decide the
        required membership — a required role without attributable
        evidence is INCOMPLETE; an absent optional role costs nothing;
        the dispatched role's joined evidence cannot satisfy another
        role."""
        admitted = self._admit_contract(
            objective="Member completeness",
            completion_policies=self._POLICIES)
        info = self._to_running(admitted["sequence_id"])
        self._finish_and_freeze(info)
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertEqual(outcome["completeness"], "INCOMPLETE")
        payload = self._receipt_payload(outcome)
        members = payload["member_completeness"]
        self.assertTrue(members["builder"]["attributed"])
        self.assertTrue(members["reviewer"]["required"])
        self.assertFalse(members["reviewer"]["attributed"])
        self.assertFalse(members["monitor"]["required"])
        # the session record carries no outcome fact, so none is reported
        self.assertNotIn("reported_outcomes", members["builder"])

    def test_outcome_mapping_and_role_policy(self):
        """U-06/D-C3-4: completed maps to success; failed and cancelled
        pass through; unknown strings pass through verbatim and never
        satisfy a policy that does not list them — and a cancelled
        REQUIRED member follows its own frozen success-outcome policy
        (the CORR-08 poller)."""
        self.assertEqual(normalize_outcome("completed"), "success")
        self.assertEqual(normalize_outcome("failed"), "failed")
        self.assertEqual(normalize_outcome("cancelled"), "cancelled")
        self.assertEqual(normalize_outcome("exploded"), "exploded")
        # D-25: success_outcomes is a SET — canonical ordering required.
        policies = {
            "builder": {"required": True, "success_outcomes": ["success"]},
            "poller": {"required": True,
                       "success_outcomes": ["cancelled", "success"]},
        }
        admitted = self._admit_contract(objective="Cancelled builder",
                                        completion_policies=policies)
        info = self._to_running(admitted["sequence_id"])
        end = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                     "agent_clis", b"transcript\n")
        self.assertEqual(
            self.seq.finish_attempt(info["attempt_id"], "builder",
                                    normalize_outcome("cancelled"),
                                    end["content_address"]),
            "FAILED")  # t-a11: cancelled is not a builder success
        admitted2 = self._admit_contract(objective="Cancelled poller",
                                         completion_policies=policies)
        info2 = self._to_running(admitted2["sequence_id"], role="poller",
                                 native="native-sess-p")
        end2 = self.seq.admit_witness(info2["attempt_id"], "obj-13",
                                      "agent_clis", b"poller transcript\n")
        self.assertEqual(
            self.seq.finish_attempt(info2["attempt_id"], "poller",
                                    normalize_outcome("cancelled"),
                                    end2["content_address"]),
            "FINISHED")  # the poller's frozen policy permits cancelled


class ProjectionClosureTests(ProvenanceTestCase):
    def test_obj10_schema_minter_and_determinism(self):
        """obj-10's Chapter 3 closure: the dispatcher-minted payload
        validates against the mirrored schema; same inputs re-mint the
        same address; a different attempt or different approved context
        changes the projection; the record binding is recomputed."""
        graph = support.load_fixture("graph.json")
        entries = support.load_json(
            support.CH0_REGISTRIES / "objects.json")["objects"]
        obj10 = [o for o in entries if o["id"] == "obj-10"][0]
        self.assertEqual(obj10["minter"], "dispatcher")
        record = support.local_record_payload(objective="Projection")
        identity = {"sequence_id": "sq_" + "0" * 32,
                    "attempt_id": "at_" + "0" * 32,
                    "fence": 1,
                    "attempt_contract_hash": "sha256:" + "0" * 64}
        context = {"author": "planner", "guidance": "Build the thing."}
        data1, addr1 = project_prompt(self.rt.store, record, "builder",
                                      identity, context)
        payload = json.loads(data1.decode("utf-8"))
        self.assertEqual(payload["minter"], "dispatcher")
        self.assertEqual(payload["authority_effect"], "none")
        self.assertNotIn("provider", json.dumps(payload))
        _, _, rh = (payload["record"]["requirement_digest"],
                    payload["record"]["contract_digest"],
                    payload["record"]["record_hash"])
        self.assertTrue(rh.startswith("sha256:"))
        data2, addr2 = project_prompt(self.rt.store, record, "builder",
                                      identity, context)
        self.assertEqual((data1, addr1), (data2, addr2))
        other_attempt = dict(identity, attempt_id="at_" + "1" * 32)
        _, addr3 = project_prompt(self.rt.store, record, "builder",
                                  other_attempt, context)
        self.assertNotEqual(addr1, addr3)
        richer = dict(context, expected_outputs="A tested module.")
        _, addr4 = project_prompt(self.rt.store, record, "builder",
                                  identity, richer)
        self.assertNotEqual(addr1, addr4)


class ProvenanceRecoveryTests(ProvenanceTestCase):
    def test_recovery_halts_on_artifact_forgery(self):
        """8g: a tampered correlation column, an unjournaled row and a
        deleted row each HALT recovery; the honest graph recovers clean
        and re-collection converges byte-identically after restart."""
        admitted = self._admit_contract(objective="Artifact recovery")
        info = self._to_running(admitted["sequence_id"])
        event = self._finish_and_freeze(info)
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_artifacts SET role = 'reviewer'"
                       " WHERE admission_id = ?", (event["admission_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_artifacts SET role = 'builder'"
                       " WHERE admission_id = ?", (event["admission_id"],))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        fake = self.rt.store.put(b"unjournaled artifact\n")
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO attempt_artifacts (admission_id, attempt_id,"
                " sequence_id, call_id, role, logical_path,"
                " content_sha256, admitted_utc) VALUES ('art_forged', ?,"
                " ?, ?, 'builder', 'artifact/forged.txt', ?,"
                " '2026-11-09T12:00:00.000Z')",
                (info["attempt_id"], info["sequence_id"],
                 info["call_id"], fake))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_artifacts WHERE admission_id"
                       " = 'art_forged'")
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_artifacts WHERE admission_id"
                       " = ?", (event["admission_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()  # the journal names the admission

    def test_restart_reconstructs_the_causal_graph(self):
        """Restart halfway through causal joining: a new runtime over the
        durable state re-derives the SAME receipt bytes (joins, members,
        artifacts included) and the 8g coherence checks pass."""
        admitted = self._admit_contract(objective="Restart joins")
        info = self._to_running(admitted["sequence_id"])
        self.seq.admit_witness(
            info["attempt_id"], "obj-12", "helios",
            governance_bytes("native-sess-9", "ab" * 32,
                             hook_event="PostToolUse", success=True))
        self._finish_and_freeze(info, governance=False)
        first = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.rt.db.close()
        db = Database(self.dir / "autobot.db")
        store = Store(self.dir / "store")
        self.rt.db, self.rt.store = db, store
        seq = Sequencer(db, store, self.clock, holder="test-instance")
        self.rt.sequencer = seq
        self.seq = seq
        seq.recover()
        self.assertEqual(seq.acquire_lease(ttl_seconds=360000), 2)
        again = Collector(seq, store).collect_and_admit(
            info["attempt_id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["receipt_address"],
                         first["receipt_address"])


class InputRehashTests(ProvenanceTestCase):
    """INV-37 (CH3-input-rehash): specification inputs are re-hashed at
    dispatch and at collection; a change is INTEGRITY_FAILED; the receipt
    binds input_rehash (the chapter-1 obj-15 always binding, D-C3-9)."""

    INPUT_NAME = "chapter1-brief.md"

    def _input_address(self):
        record = support.load_fixture("va_record.json")
        return record["specification_inputs"][
            "name_to_exact_byte_sha256"][self.INPUT_NAME]

    def _tamper_input(self):
        address = self._input_address()
        path = self.rt.store._path_for(address)
        path.write_bytes(b"tampered specification input\n")
        return address

    def test_dispatch_refuses_tampered_input(self):
        """The dispatch half: a declared input that no longer re-hashes
        refuses t-a02 BEFORE any external launch — fail closed at the
        boundary where repair is still possible."""
        admitted = self._admit_contract(objective="Input rehash dispatch")
        self.seq.mark_ready(admitted["sequence_id"])
        info = self.seq.start_execution(admitted["sequence_id"])
        self.seq.reserve_attempt(info["attempt_id"],
                                 expected_fence=info["fence"])
        projection = self.rt.store.put(b"projection for input gate\n")
        self._tamper_input()
        with self.assertRaises(ConflictError) as caught:
            self.seq.dispatch_attempt(info["attempt_id"], "builder",
                                      "fake", projection)
        self.assertIn("INV-37", str(caught.exception))

    def test_collection_binds_input_rehash_and_change_fails_integrity(self):
        """The collection half: the receipt always binds the re-hash
        result; intact inputs ride a COMPLETE receipt, and a post-freeze
        change mints a NEW version classified INTEGRITY_FAILED — never a
        silent pass, never a mutation of the admitted version."""
        admitted = self._admit_contract(objective="Input rehash collect")
        info = self._to_running(admitted["sequence_id"])
        self._finish_and_freeze(info)
        address = self._input_address()
        first = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertEqual(first["completeness"], "COMPLETE")
        payload = self._receipt_payload(first)
        self.assertEqual(
            payload["input_rehash"],
            {self.INPUT_NAME: {"content_sha256": address, "intact": True}})
        self._tamper_input()
        second = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        self.assertFalse(second["duplicate"])
        self.assertNotEqual(second["receipt_address"],
                            first["receipt_address"])
        self.assertEqual(second["completeness"], "INTEGRITY_FAILED")
        payload = self._receipt_payload(second)
        self.assertEqual(
            payload["input_rehash"],
            {self.INPUT_NAME: {"content_sha256": address,
                               "intact": False}})

    def test_recovery_halts_on_tampered_input(self):
        """The recovery interplay (D-C3-9, the D-C3-3 pattern): a frozen
        input that no longer resolves to its declared bytes HALTs
        recover() at the records loop — the operator owns the store, and
        no arm ever trusts or replaces the bytes."""
        self._admit_contract(objective="Input rehash recovery")
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        self._tamper_input()
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


class IntegrityClassesTests(ProvenanceTestCase):
    """INV-68 (CH3-integrity-classes) / D-12: an orphan, a DENY-then-ran
    event, or execution with no recorded decision is INTEGRITY_FAILED;
    DENY counts are diagnostic."""

    SHA = "ab" * 32

    def _collect(self, witnesses, objective):
        admitted = self._admit_contract(objective=objective)
        info = self._to_running(admitted["sequence_id"])
        for kind, producer, data in witnesses:
            self.seq.admit_witness(info["attempt_id"], kind, producer,
                                   data)
        self._finish_and_freeze(info, governance=False)
        outcome = Collector(self.seq, self.rt.store).collect_and_admit(
            info["attempt_id"])
        return outcome, self._receipt_payload(outcome)

    def test_orphan_execution_is_integrity_failed(self):
        """A governance record proving a command EXECUTED with no
        recorded decision anywhere in the admitted set is the registered
        orphan class: a positive observation of a fail-open."""
        outcome, payload = self._collect([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", self.SHA,
                              hook_event="PostToolUse", success=True)),
        ], "Orphan execution")
        self.assertEqual(outcome["completeness"], "INTEGRITY_FAILED")
        self.assertEqual(payload["contradictions"], [
            {"rule": "execution_without_decision",
             "tool_use_id": "toolu_x1", "command_sha256": self.SHA,
             "session_id": "native-sess-9"}])

    def test_self_decided_execution_is_clean(self):
        """The real per-command record family carries the decision and
        the execution result in one record: decided by itself, no
        contradiction, COMPLETE."""
        outcome, payload = self._collect([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", self.SHA, decision="ALLOW",
                              hook_event="PostToolUse", success=True)),
        ], "Self-decided execution")
        self.assertEqual(outcome["completeness"], "COMPLETE")
        self.assertEqual(payload["contradictions"], [])

    def test_sibling_decision_decides_the_execution(self):
        """A separate decision record naming the same tool_use decides
        the execution record — the deterministic join, never ordering."""
        outcome, payload = self._collect([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", self.SHA,
                              decision="ALLOW")),
            ("obj-12", "helios",
             governance_bytes("native-sess-9", self.SHA,
                              hook_event="PostToolUse", success=True)),
        ], "Sibling decision")
        self.assertEqual(outcome["completeness"], "COMPLETE")
        self.assertEqual(payload["contradictions"], [])

    def test_single_record_deny_and_ran_contradicts(self):
        """F-C3-9: ONE record proving both the DENY and the execution is
        the strongest deny_then_ran witness — the original elif dropped
        it; the orphan rule stays silent (the decision IS recorded)."""
        outcome, payload = self._collect([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", self.SHA, decision="DENY",
                              hook_event="PostToolUse", success=True)),
        ], "One-record deny then ran")
        self.assertEqual(outcome["completeness"], "INTEGRITY_FAILED")
        self.assertEqual(payload["contradictions"], [
            {"rule": "deny_then_ran", "command_sha256": self.SHA,
             "session_id": "native-sess-9"}])

    def test_enforced_deny_is_diagnostic_only(self):
        """An enforced DENY (no execution evidence) classifies NOTHING:
        DENY counts are diagnostic (D-12), visible in the admitted facts
        and the causal joins, never in the classification."""
        outcome, payload = self._collect([
            ("obj-12", "helios",
             governance_bytes("native-sess-9", self.SHA,
                              decision="DENY")),
        ], "Enforced deny")
        self.assertEqual(outcome["completeness"], "COMPLETE")
        self.assertEqual(payload["contradictions"], [])


class IndexLagDiagnosticTests(ProvenanceTestCase):
    """INV-30 (CH3-index-lag-diagnostic): evidence completeness depends
    only on raw witnesses; INDEX_LAG never blocks unless an assertion
    declares an HDB-derived query (none can in v1). The watermark
    interface is U-05's resolved status document (D-C3-7)."""

    def _frozen(self, objective="Index diagnostic"):
        admitted = self._admit_contract(objective=objective)
        info = self._to_running(admitted["sequence_id"])
        self._finish_and_freeze(info)
        return info

    def _status_document(self, committed):
        from autobot.collector import INDEX_STATUS_SCHEMA
        return {"schema": INDEX_STATUS_SCHEMA,
                "backend": "postgresql",
                "counts": {"catalog_records": 139408},
                "source_states": {"evidence": 806},
                "storage": {"copied_bytes": 123},
                "watermarks": {
                    "latest_observed_utc": committed,
                    "latest_committed_utc": committed},
                "integrity": {"observation_without_artifact": 0,
                              "closed": True}}

    def test_index_lag_never_blocks_and_never_enters_the_receipt(self):
        """The structural INV-30 proof: a hugely lagging catalog, a
        raising probe and an unrecognized document all leave the receipt
        BYTES and the transitions identical — the payload is a pure
        function of durable ledger facts, so a live probe result cannot
        enter it without breaking the D-C3-2 byte-equality."""
        info = self._frozen()
        plain = Collector(self.seq, self.rt.store)
        first = plain.collect_and_admit(info["attempt_id"])
        bytes_first = self.rt.store.get(first["receipt_address"])
        lagging = Collector(
            self.seq, self.rt.store,
            catalog_probe=lambda: self._status_document(
                "2020-01-01 00:00:00.000000+00:00"))

        def raising_probe():
            raise RuntimeError("catalog down")

        broken = Collector(self.seq, self.rt.store,
                           catalog_probe=raising_probe)
        garbage = Collector(self.seq, self.rt.store,
                            catalog_probe=lambda: "not a document")
        for collector in (lagging, broken, garbage):
            again = collector.collect_and_admit(info["attempt_id"])
            self.assertTrue(again["duplicate"])
            self.assertEqual(again["receipt_address"],
                             first["receipt_address"])
            self.assertEqual(
                self.rt.store.get(again["receipt_address"]), bytes_first)
        diagnostic = lagging.index_state(info["attempt_id"])
        self.assertTrue(diagnostic["available"])
        self.assertTrue(diagnostic["lagging_behind_attempt"])
        self.assertGreater(diagnostic["lag_behind_attempt_seconds"], 0)
        # the lag blocks nothing: the registered completion transition
        # consumes the admitted COMPLETE classification and proceeds
        self.seq.complete_evidence(info["attempt_id"], info["fence"],
                                   first["receipt_address"])
        state = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (info["attempt_id"],)).fetchone()["state"]
        self.assertEqual(state, "EVIDENCE_COMPLETE")

    def test_index_state_recognizes_the_u05_interface(self):
        """The recognized watermark facts are exactly U-05's resolved
        status document: watermarks, integrity.closed, schema — unknown
        fields ignored, the witness-facts way."""
        info = self._frozen("Index facts")
        committed = "2020-01-01 00:00:00.000000+00:00"
        collector = Collector(
            self.seq, self.rt.store,
            catalog_probe=lambda: self._status_document(committed))
        diagnostic = collector.index_state(info["attempt_id"])
        self.assertEqual(diagnostic["latest_committed_utc"], committed)
        self.assertEqual(diagnostic["latest_observed_utc"], committed)
        self.assertTrue(diagnostic["integrity_closed"])
        self.assertEqual(diagnostic["newest_admitted_utc"],
                         "2026-11-09T12:00:00.000Z")
        self.assertEqual(
            diagnostic["schema"], "helios.evidence.catalog.status.v1")

    def test_index_state_reports_unavailability_and_never_raises(self):
        """Absent probe, failing probe and unrecognized document each
        REPORT a diagnostic state — nothing raises, nothing blocks."""
        plain = Collector(self.seq, self.rt.store)
        self.assertEqual(plain.index_state(),
                         {"available": False,
                          "reason": "no catalog probe configured"})

        def raising_probe():
            raise RuntimeError("catalog down")

        broken = Collector(self.seq, self.rt.store,
                           catalog_probe=raising_probe)
        self.assertEqual(broken.index_state()["reason"],
                         "catalog probe failed: RuntimeError")
        garbage = Collector(self.seq, self.rt.store,
                            catalog_probe=lambda: ["nope"])
        self.assertFalse(garbage.index_state()["available"])
        # an unparseable watermark stays verbatim with no lag math
        info = self._frozen("Index verbatim")
        odd = Collector(
            self.seq, self.rt.store,
            catalog_probe=lambda: self._status_document("not an instant"))
        diagnostic = odd.index_state(info["attempt_id"])
        self.assertEqual(diagnostic["latest_committed_utc"],
                         "not an instant")
        self.assertNotIn("lagging_behind_attempt", diagnostic)

    def test_receipt_plane_is_blind_to_the_index(self):
        """The source scan (the INV-04 pattern): the derivation, the
        seam and the intelligence plane never name the index surface,
        the obj-15 schema has no index member, and no frozen contract
        assertion can declare an HDB-derived query in v1."""
        import autobot
        import intelligence.taskforce
        src = Path(autobot.__file__).resolve().parent
        for name in ("receipts.py", "sequencer.py", "witness_semantics.py"):
            text = (src / name).read_text(encoding="utf-8")
            for token in ("index_state", "latest_committed_utc",
                          "catalog_probe"):
                self.assertNotIn(token, text,
                                 "%s must stay blind to the index" % name)
        taskforce_text = Path(
            intelligence.taskforce.__file__).read_text(encoding="utf-8")
        for token in ("index_state", "latest_committed_utc",
                      "catalog_probe"):
            self.assertNotIn(token, taskforce_text)
        for schema_path in (
                src / "schemas_chapter3" / "execution_receipt.schema.json",
                support.REPO / "docs" / "architecture" / "chapter3"
                / "schemas" / "execution_receipt.schema.json"):
            schema_text = schema_path.read_text(encoding="utf-8")
            self.assertNotIn("index", schema_text.lower())
            self.assertNotIn("watermark", schema_text.lower())
        contract_schema = (src / "schemas"
                           / "task_contract_record.schema.json")
        self.assertNotIn("hdb",
                         contract_schema.read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main()
