"""Crash driver for the failpoint matrix (INV-10, D-69, D-71).

Runs the milestone pipeline up to a named stage in its own process, prints
the committed durable facts as JSON, then dies hard (os._exit) — for the
``mid_transaction`` stage with an uncommitted write-in-flight that the
restart must erase. Not a test module; test_recovery.py drives it.

Stages: admitted | ready | executing | reserved | mid_transaction
      | intent | mid_intent_tx | dispatched
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
                 "mid_intent_tx", "dispatched"):
        info = rt.sequencer.start_execution(sequence_id)
        snapshot["attempt_id"] = info["attempt_id"]
        snapshot["fence"] = info["fence"]
    if stage in ("reserved", "mid_transaction", "intent", "mid_intent_tx",
                 "dispatched"):
        rt.sequencer.reserve_attempt(snapshot["attempt_id"], snapshot["fence"])
    if stage == "dispatched":
        projection = rt.store.put(b"projection bytes for the crash test\n")
        rt.sequencer.dispatch_attempt(
            snapshot["attempt_id"], role="builder", provider="fake",
            call_id="call-crash-1", projection_address=projection)
    if stage in ("intent", "mid_intent_tx"):
        rt.sequencer.grant_permit(sequence_id, permitted_uses=2)
        rt.sequencer.reserve_use(sequence_id, snapshot["attempt_id"])
        snapshot["intent_id"] = rt.sequencer.mint_intent(
            sequence_id, snapshot["attempt_id"], "eff-canonical-push",
            target="refs/heads/main", expected_old="sha256:" + "ab" * 32)

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
    if stage == "dispatched":
        crow = cur.execute(
            "SELECT role, provider, call_id, projection_address,"
            " native_session_id FROM attempt_calls WHERE attempt_id = ?",
            (snapshot["attempt_id"],),
        ).fetchone()
        snapshot["call"] = dict(crow)
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
