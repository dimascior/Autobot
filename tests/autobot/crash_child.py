"""Crash driver for the failpoint matrix (INV-10, D-69, D-71).

Runs the milestone pipeline up to a named stage in its own process, prints
the committed durable facts as JSON, then dies hard (os._exit) — for the
``mid_transaction`` stage with an uncommitted write-in-flight that the
restart must erase. Not a test module; test_recovery.py drives it.

Stages: admitted | ready | executing | reserved | mid_transaction
      | intent | mid_intent_tx | dispatched | running | verifying
      | m5_post_effect
"""

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "src"))
sys.path.insert(0, str(HERE.parent))

import support  # noqa: E402


def main():
    data_dir = Path(sys.argv[1])
    stage = sys.argv[2]
    rt, result = support.admitted_runtime(
        data_dir, clock=support.manual_clock(), holder="crash-child")
    sequence_id = result["sequence_id"]
    snapshot = {"stage": stage, "sequence_id": sequence_id}

    if stage != "admitted":
        rt.sequencer.acquire_lease(ttl_seconds=3600)
        rt.sequencer.mark_ready(sequence_id)
    if stage in ("executing", "reserved", "mid_transaction", "intent",
                 "mid_intent_tx", "dispatched", "running", "verifying"):
        info = rt.sequencer.start_execution(sequence_id)
        snapshot["attempt_id"] = info["attempt_id"]
        snapshot["fence"] = info["fence"]
        snapshot["attempt_contract_hash"] = info["attempt_contract_hash"]
    if stage in ("reserved", "mid_transaction", "intent", "mid_intent_tx",
                 "dispatched", "running", "verifying"):
        rt.sequencer.reserve_attempt(snapshot["attempt_id"], snapshot["fence"])
    if stage in ("dispatched", "running", "verifying"):
        projection = rt.store.put(b"projection bytes for the crash test\n")
        dispatched = rt.sequencer.dispatch_attempt(
            snapshot["attempt_id"], role="builder", provider="fake",
            projection_address=projection)
        snapshot["call_id"] = dispatched["call_id"]
    if stage in ("running", "verifying"):
        admitted = rt.sequencer.admit_witness(
            snapshot["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(snapshot["call_id"],
                                         native_session_id="native-crash-1"))
        snapshot["witness_admission_id"] = admitted["admission_id"]
        rt.sequencer.attempt_running(
            snapshot["attempt_id"], "native-crash-1",
            admitted["content_address"])
    if stage == "verifying":
        rt.sequencer.admit_witness(
            snapshot["attempt_id"], "obj-12", "helios",
            b"command governance log for the crash test\n")
        transcript = rt.sequencer.admit_witness(
            snapshot["attempt_id"], "obj-13", "agent_clis",
            b"transcript for the crash test\n")
        rt.sequencer.finish_attempt(
            snapshot["attempt_id"], "builder", "success",
            transcript["content_address"])
        rt.sequencer.freeze_candidate(
            snapshot["attempt_id"], snapshot["fence"],
            rt.store.put(b"candidate for the crash test\n"))
        # C3-M1: the registered Collector derives-and-admits obj-15; the
        # crash may now also land between the admission and the
        # transition, and recovery converges either way.
        receipt = support.collect_receipt(rt, snapshot["attempt_id"])
        rt.sequencer.complete_evidence(
            snapshot["attempt_id"], snapshot["fence"], receipt)
        registered = rt.sequencer.register_verifier_result(
            snapshot["attempt_id"], "a2", "PASS", "verifier_runner",
            json.dumps({"schema_version": "v1",
                        "kind": "VerificationReceipt",
                        "assertion_id": "a2", "outcome": "PASS",
                        "attempt_id": snapshot["attempt_id"]},
                       sort_keys=True).encode("utf-8"))
        snapshot["verification_id"] = registered["verification_id"]
    if stage in ("intent", "mid_intent_tx"):
        rt.sequencer.grant_permit(sequence_id, permitted_uses=2)
        rt.sequencer.reserve_use(sequence_id, snapshot["attempt_id"])
        snapshot["intent_id"] = rt.sequencer.mint_intent(
            sequence_id, snapshot["attempt_id"], "eff-canonical-push",
            target="refs/heads/main", expected_old="sha256:" + "ab" * 32)
    if stage == "m5_post_effect":
        # The post-PONR crash window: the authorized real Git CAS has
        # happened; the process dies before any observation or resolution.
        from autobot.git_executor import _git
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        local = rt.registry.admit_payload(
            support.local_record_payload(), submitted_by="planner")
        info = support.drive_to_verified(rt, local)
        rt.sequencer.grant_permit(local["sequence_id"], permitted_uses=1)
        repo = data_dir / "repo"
        sha_old, sha_new = support.init_git_repo(repo)
        minted = rt.sequencer.begin_commit(
            local["sequence_id"], info["attempt_id"], "refs/heads/main",
            sha_old, sha_new)
        proc = _git(repo, "update-ref", "refs/heads/main", sha_new, sha_old)
        snapshot.update({
            "m5_sequence_id": local["sequence_id"],
            "m5_attempt_id": info["attempt_id"],
            "m5_fence": info["fence"],
            "intent_id": minted["intent_id"],
            "reservation_id": minted["reservation_id"],
            "repo": str(repo), "sha_old": sha_old, "sha_new": sha_new,
            "update_ref_exit": proc.returncode})

    cur = rt.db.conn.cursor()
    row = cur.execute(
        "SELECT state, version, fence, current_attempt_id FROM sequences"
        " WHERE sequence_id = ?", (sequence_id,),
    ).fetchone()
    snapshot["sequence"] = dict(row)
    snapshot["journal_entries"] = cur.execute(
        "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]
    if stage in ("intent", "mid_intent_tx"):
        irow = cur.execute(
            "SELECT state, version, fence FROM effect_intents"
            " WHERE intent_id = ?", (snapshot["intent_id"],),
        ).fetchone()
        snapshot["intent"] = dict(irow)
    if stage in ("dispatched", "running", "verifying"):
        crow = cur.execute(
            "SELECT role, provider, call_id, projection_address,"
            " native_session_id FROM attempt_calls WHERE attempt_id = ?",
            (snapshot["attempt_id"],),
        ).fetchone()
        snapshot["call"] = dict(crow)
    if stage in ("running", "verifying"):
        snapshot["witnesses"] = cur.execute(
            "SELECT COUNT(*) AS n FROM attempt_witnesses"
            " WHERE attempt_id = ?", (snapshot["attempt_id"],),
        ).fetchone()["n"]
    print(json.dumps(snapshot), flush=True)

    if stage == "mid_intent_tx":
        # A torn intent transition the crash must erase.
        torn = rt.db.conn.cursor()
        torn.execute("BEGIN IMMEDIATE")
        torn.execute(
            "UPDATE effect_intents SET state = 'HAPPENED', version = version + 1"
            " WHERE intent_id = ?", (snapshot["intent_id"],),
        )
        torn.execute(
            "INSERT INTO transition_journal (entry_hash, previous_hash, payload,"
            " appended_utc) VALUES ('torn-intent', NULL, '{}', 'torn')",
        )
        os._exit(137)

    if stage == "mid_transaction":
        # A torn write the crash must erase: begun, applied, never committed.
        torn = rt.db.conn.cursor()
        torn.execute("BEGIN IMMEDIATE")
        torn.execute(
            "UPDATE sequences SET state = 'HALTED', version = version + 1"
            " WHERE sequence_id = ?", (sequence_id,),
        )
        torn.execute(
            "INSERT INTO transition_journal (entry_hash, previous_hash, payload,"
            " appended_utc) VALUES ('torn', NULL, '{}', 'torn')",
        )
        os._exit(137)

    os._exit(137)


if __name__ == "__main__":
    main()
