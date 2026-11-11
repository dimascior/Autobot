"""The M6 conformance suite: acceptance, dependency release, and the
Chief Engineer loop closure.

t-s20 (SL acceptance over durable finalization facts), t-s14 (READ_ONLY
acceptance, the registered PONR row), t-s41 (durable released edges +
obj-35), t-s42 (completion), and t-s01/t-s02/t-s03 readiness consuming
released edges — proven by the full two-contract loop over a real local
Git effect, the multi-edge matrix, refusal gates, forged-authority HALTs
and restart windows.
"""

import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Database, Sequencer, Store, project_state
from autobot.git_executor import execute_local_cas
from autobot.models import ConflictError, IntegrityHalt, StaleFenceError

TARGET = "refs/heads/main"


def edge(edge_id, kind, target, obs="ACCEPTANCE"):
    return {"edge_id": edge_id, "edge_kind": kind, "target": target,
            "observation_requirement": obs}


class AcceptanceTestCase(unittest.TestCase):
    """V-A admitted plus record A (the local SL contract) and a real Git
    repository; helpers drive arbitrary sibling contracts through the full
    M2–M5 arc."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, _ = support.admitted_runtime(self.dir, clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.a = self.rt.registry.admit_payload(
            support.local_record_payload(), submitted_by="planner")
        self.assertEqual(self.a["outcome"], "NEW_RECORD")
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)
        self._repo_counter = 0

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _admit(self, **kwargs):
        result = self.rt.registry.admit_payload(
            support.local_record_payload(**kwargs), submitted_by="planner")
        self.assertEqual(result["outcome"], "NEW_RECORD")
        return result

    def _repo(self):
        self._repo_counter += 1
        path = self.dir / ("repo%d" % self._repo_counter)
        sha_old, sha_new = support.init_git_repo(path)
        return path, sha_old, sha_new

    def _finalize_to_committed(self, admitted):
        """VERIFIED → COMMITTING → real Git CAS → COMMITTED; returns the
        drive info (attempt/fence)."""
        info = support.drive_to_verified(self.rt, admitted)
        sequence_id = admitted["sequence_id"]
        self.seq.grant_permit(sequence_id, permitted_uses=3)
        repo, sha_old, sha_new = self._repo()
        minted = self.seq.begin_commit(sequence_id, info["attempt_id"],
                                       TARGET, sha_old, sha_new)
        report = execute_local_cas(self.seq, minted["intent_id"], repo)
        self.assertEqual(report["outcome"], "HAPPENED")
        self.seq.commit_committed(sequence_id, info["fence"],
                                  report["receipt"])
        return info

    def _state(self, sequence_id):
        return self.rt.db.conn.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (sequence_id,)).fetchone()["state"]

    def _digests(self, sequence_id):
        return self.rt.db.conn.execute(
            "SELECT record_hash, contract_digest, requirement_digest"
            " FROM sequences WHERE sequence_id = ?",
            (sequence_id,)).fetchone()

    def _rules(self, sequence_id=None):
        rules = []
        for r in self.rt.db.conn.execute(
                "SELECT payload FROM transition_journal ORDER BY seq"
        ).fetchall():
            payload = json.loads(r["payload"])
            if sequence_id is None or payload.get(
                    "subject_id") == sequence_id:
                rules.append(payload["rule"])
        return rules

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]


class ChiefEngineerLoopTests(AcceptanceTestCase):
    def test_full_loop_two_contracts(self):
        """The complete control loop: contract A executes through the real
        Git finalization, is ACCEPTED (t-s20, obj-30 from durable facts
        only), releases its dependencies (t-s41, durable rows + obj-35),
        and contract B — BLOCKED the whole way (t-s01) — becomes READY
        only through the authoritative release (t-s02), with
        project_state exposing the successor as actionable work and A
        reaching COMPLETED (t-s42). Includes an in-process restart at
        ACCEPTED and repeated recovery convergence."""
        b = self._admit(
            task_contract_id="tc_" + "b00b" * 8,
            predecessors=[edge("on-a", "RECORD", self.a["record_hash"])])
        self.assertEqual(self.seq.mark_ready(b["sequence_id"]), "BLOCKED")
        self.assertIn("t-s01", self._rules(b["sequence_id"]))

        info = self._finalize_to_committed(self.a)
        a_id = self.a["sequence_id"]
        # B stays blocked through execution, verification and commit.
        self.assertEqual(
            self.seq.reevaluate_blocked(b["sequence_id"])["state"],
            "BLOCKED")
        acceptance = self.seq.accept_commit(a_id, info["fence"])
        self.assertEqual(self._state(a_id), "ACCEPTED")
        self.assertEqual(
            json.loads(self.rt.store.get(acceptance).decode("utf-8"))
            ["kind"], "AcceptanceReceipt")
        # Accepted but NOT released: the successor stays blocked.
        self.assertEqual(
            self.seq.reevaluate_blocked(b["sequence_id"])["state"],
            "BLOCKED")

        # Crash window: restart at ACCEPTED, recover, continue.
        self.rt.db.close()
        db = Database(self.dir / "autobot.db")
        store = Store(self.dir / "store")
        self.rt.db, self.rt.store = db, store
        self.seq = Sequencer(db, store, self.clock, holder="test-instance")
        self.seq.recover()
        self.seq.acquire_lease(ttl_seconds=360000)

        released = self.seq.release_dependencies(a_id)
        self.assertEqual(len(released["release_ids"]), 3)
        self.assertEqual(released["known_successors"], [b["sequence_id"]])
        self.assertEqual(self._state(a_id), "DEPENDENCIES_RELEASED")
        result = self.seq.reevaluate_blocked(b["sequence_id"])
        self.assertEqual(result["state"], "READY")
        self.assertEqual(result["satisfied_edges"][0]["edge_id"], "on-a")
        self.assertEqual(
            result["satisfied_edges"][0]["predecessor_sequence_id"], a_id)

        view = project_state(self.rt.db)
        self.assertIn(b["sequence_id"],
                      view["obligations"]["ready_work"])
        self.assertIn(a_id, view["obligations"]["dependencies_released"])
        self.assertEqual(len(view["released_edges"]), 3)

        self.seq.complete_sequence(a_id)
        self.assertEqual(self._state(a_id), "COMPLETED")
        for rule in ("t-s03", "t-s01", "t-s20", "t-s41", "t-s02", "t-s42"):
            self.assertIn(rule, self._rules())
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

    def test_multi_edge_requires_every_release(self):
        """Two predecessors on different tiers: acceptance without release
        keeps the successor blocked, one missing release keeps it blocked,
        and READY arrives only when every edge has its durable release."""
        d = self._admit(
            task_contract_id="tc_" + "d00d" * 8,
            objective="Mint the D-side requirement for the edge matrix.")
        c = self._admit(
            task_contract_id="tc_" + "c0de" * 8,
            predecessors=[
                edge("on-a", "RECORD", self.a["record_hash"]),
                edge("on-d", "REQUIREMENT",
                     self._digests(d["sequence_id"])["requirement_digest"])])
        self.assertEqual(self.seq.mark_ready(c["sequence_id"]), "BLOCKED")

        info_a = self._finalize_to_committed(self.a)
        self.seq.accept_commit(self.a["sequence_id"], info_a["fence"])
        self.seq.release_dependencies(self.a["sequence_id"])
        self.assertEqual(
            self.seq.reevaluate_blocked(c["sequence_id"])["unsatisfied"],
            ["on-d"])

        info_d = self._finalize_to_committed(d)
        self.seq.accept_commit(d["sequence_id"], info_d["fence"])
        # D is ACCEPTED but unreleased: C must stay blocked.
        self.assertEqual(
            self.seq.reevaluate_blocked(c["sequence_id"])["state"],
            "BLOCKED")
        self.seq.release_dependencies(d["sequence_id"])
        self.assertEqual(
            self.seq.reevaluate_blocked(c["sequence_id"])["state"],
            "READY")

    def test_acceptance_fresh_edge_fails_closed(self):
        """ACCEPTANCE_FRESH freshness semantics belong to the deferred
        reuse/dominance rules: such an edge stays unsatisfied even after
        the predecessor's release."""
        f = self._admit(
            task_contract_id="tc_" + "fade" * 8,
            predecessors=[edge(
                "on-a-fresh", "REQUIREMENT",
                self._digests(self.a["sequence_id"])["requirement_digest"],
                obs="ACCEPTANCE_FRESH")])
        self.assertEqual(self.seq.mark_ready(f["sequence_id"]), "BLOCKED")
        info = self._finalize_to_committed(self.a)
        self.seq.accept_commit(self.a["sequence_id"], info["fence"])
        self.seq.release_dependencies(self.a["sequence_id"])
        result = self.seq.reevaluate_blocked(f["sequence_id"])
        self.assertEqual(result, {"state": "BLOCKED",
                                  "unsatisfied": ["on-a-fresh"]})


class AcceptanceGateTests(AcceptanceTestCase):
    def test_acceptance_gates_and_duplicate_refusal(self):
        """Path and state gates on both acceptance rows, stale fences, the
        unsupported-requirement refusal, and deterministic duplicate
        refusal with the receipt identity intact."""
        info = support.drive_to_verified(self.rt, self.a)
        a_id = self.a["sequence_id"]
        with self.assertRaises(ConflictError):
            self.seq.accept_verified(a_id, info["fence"])  # not READ_ONLY
        with self.assertRaises(ConflictError):
            self.seq.accept_commit(a_id, info["fence"])  # not COMMITTED
        self.seq.grant_permit(a_id, permitted_uses=3)
        repo, sha_old, sha_new = self._repo()
        minted = self.seq.begin_commit(a_id, info["attempt_id"], TARGET,
                                       sha_old, sha_new)
        report = execute_local_cas(self.seq, minted["intent_id"], repo)
        self.seq.commit_committed(a_id, info["fence"], report["receipt"])
        with self.assertRaises(StaleFenceError):
            self.seq.accept_commit(a_id, info["fence"] + 1)
        acceptance = self.seq.accept_commit(a_id, info["fence"])
        before = self._journal_count()
        with self.assertRaises(ConflictError):
            self.seq.accept_commit(a_id, info["fence"])  # duplicate
        self.assertEqual(self._journal_count(), before)
        entries = [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq").fetchall()]
        t_s20 = [e for e in entries if e["rule"] == "t-s20"]
        self.assertEqual(len(t_s20), 1)
        self.assertEqual(t_s20[0]["facts"]["acceptance_sha256"], acceptance)

        # A frozen record whose requirements exceed the SL set refuses
        # acceptance by name. SEM-05 derives the exact set per path at
        # admission and begin_commit refuses non-LOCAL records, so a
        # COMMITTED remote record is reachable only by ledger surgery:
        # the runtime check is defense in depth.
        remote = self.rt.db.conn.execute(
            "SELECT sequence_id FROM sequences WHERE sequence_id != ?",
            (a_id,)).fetchone()["sequence_id"]
        info_r = support.drive_to_verified(self.rt, {"sequence_id": remote})
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE sequences SET state = 'COMMITTED'"
                       " WHERE sequence_id = ?", (remote,))
        with self.assertRaises(ConflictError) as caught:
            self.seq.accept_commit(remote, info_r["fence"])
        self.assertIn("CANONICAL_PUBLICATION", str(caught.exception))
        self.assertEqual(self._state(remote), "COMMITTED")

    def test_superseded_sequence_refuses_acceptance(self):
        """A superseded (or abandoned) sequence is refused at the state
        gate: acceptance never fires on withdrawn authority."""
        info = self._finalize_to_committed(self.a)
        a_id = self.a["sequence_id"]
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE sequences SET state = 'SUPERSEDED'"
                       " WHERE sequence_id = ?", (a_id,))
        with self.assertRaises(ConflictError):
            self.seq.accept_commit(a_id, info["fence"])

    def test_ro_acceptance_release_and_forgery_halts(self):
        """t-s14 accepts a READ_ONLY sequence straight from VERIFIED (the
        registered PONR row), releases and completes; a forged released
        edge and a deleted released edge each HALT recovery."""
        ro = self._admit(task_contract_id="tc_" + "ace0" * 8,
                         finalization_class="READ_ONLY",
                         finalization_requirements=[])
        info = support.drive_to_verified(self.rt, ro)
        ro_id = ro["sequence_id"]
        acceptance = self.seq.accept_verified(ro_id, info["fence"])
        self.assertEqual(self._state(ro_id), "ACCEPTED")
        entries = [json.loads(r["payload"]) for r in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal ORDER BY seq").fetchall()]
        t_s14 = [e for e in entries if e["rule"] == "t-s14"][0]
        self.assertTrue(t_s14["facts"]["ponr"])
        self.assertEqual(t_s14["facts"]["acceptance_sha256"], acceptance)
        released = self.seq.release_dependencies(ro_id)
        self.assertEqual(len(released["release_ids"]), 3)
        with self.assertRaises(ConflictError):
            self.seq.release_dependencies(ro_id)  # duplicate refused
        self.seq.complete_sequence(ro_id)
        self.assertEqual(self._state(ro_id), "COMPLETED")
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

        # Forged released edge: no journaled t-s41, unaccepted predecessor.
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO released_edges (release_id,"
                " predecessor_sequence_id, edge_kind, edge_value,"
                " acceptance_sha256, release_sha256, released_utc)"
                " VALUES ('rel_' || lower(hex(randomblob(16))), ?,"
                " 'RECORD', 'sha256:' || lower(hex(randomblob(32))),"
                " ?, ?, '2026-11-11T00:00:00.000Z')",
                (self.a["sequence_id"], acceptance,
                 released["release_address"]))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM released_edges WHERE"
                       " predecessor_sequence_id = ?",
                       (self.a["sequence_id"],))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        # Deleting a journaled release row HALTs the other direction.
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM released_edges WHERE release_id = ?",
                       (released["release_ids"][0],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_forged_acceptance_state_halts_recovery(self):
        """A sequence surgically placed in ACCEPTED without its journaled
        obj-30 reference is forged authority: recover HALTs (8e)."""
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE sequences SET state = 'ACCEPTED'"
                       " WHERE sequence_id = ?", (self.a["sequence_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_ready_requires_satisfied_edges_on_recovery(self):
        """Forged readiness: a READY successor whose released edges are
        gone HALTs recovery — ledger facts, never inferred graph state."""
        b = self._admit(
            task_contract_id="tc_" + "bead" * 8,
            predecessors=[edge(
                "on-a", "CONTRACT",
                self._digests(self.a["sequence_id"])["contract_digest"])])
        self.seq.mark_ready(b["sequence_id"])
        info = self._finalize_to_committed(self.a)
        self.seq.accept_commit(self.a["sequence_id"], info["fence"])
        self.seq.release_dependencies(self.a["sequence_id"])
        self.assertEqual(
            self.seq.reevaluate_blocked(b["sequence_id"])["state"], "READY")
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM released_edges WHERE"
                       " predecessor_sequence_id = ?",
                       (self.a["sequence_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()


if __name__ == "__main__":
    unittest.main()
