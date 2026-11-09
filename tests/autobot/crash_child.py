"""Crash driver for the failpoint matrix (INV-10, D-69, D-71).

Runs the milestone pipeline up to a named stage in its own process, prints
the committed durable facts as JSON, then dies hard (os._exit) — for the
``mid_transaction`` stage with an uncommitted write-in-flight that the
restart must erase. Not a test module; test_recovery.py drives it.

Stages: admitted | ready | executing | reserved | mid_transaction
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
    if stage in ("executing", "reserved", "mid_transaction"):
        info = rt.sequencer.start_execution(sequence_id)
        snapshot["attempt_id"] = info["attempt_id"]
        snapshot["fence"] = info["fence"]
    if stage in ("reserved", "mid_transaction"):
        rt.sequencer.reserve_attempt(snapshot["attempt_id"], snapshot["fence"])

    cur = rt.db.conn.cursor()
    row = cur.execute(
        "SELECT state, version, fence, current_attempt_id FROM sequences"
        " WHERE sequence_id = ?", (sequence_id,),
    ).fetchone()
    snapshot["sequence"] = dict(row)
    snapshot["journal_entries"] = cur.execute(
        "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]
    print(json.dumps(snapshot), flush=True)

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
