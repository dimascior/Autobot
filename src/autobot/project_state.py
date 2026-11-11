"""ProjectState — the Chief Engineer's authoritative read-only view (M3).

``project_state(db)`` renders the ledger's durable facts as one dict:
sequences by state with their record identities, every current attempt with
its bound call and admitted-witness count, state-derived work eligibility,
the outstanding obligations (including the in-doubt intent set and the
verification gate that follows EVIDENCE_COMPLETE), permit/reservation
standings, queued admissions, the instance lease, and the journal head.

It writes NOTHING and interprets nothing: the facts are states and rows,
deterministically ordered; the intelligence consuming them stays in the
integration layer (D-63/D-64). Deep validation (chain, content re-hashing,
pairing) is ``Sequencer.recover()``'s contract, not this view's.
"""

from .models import ATTEMPT_TERMINAL


def _rows(cur, sql, args=()):
    return [dict(r) for r in cur.execute(sql, args).fetchall()]


def project_state(db):
    cur = db.conn.cursor()

    sequences = _rows(cur, (
        "SELECT s.sequence_id, s.state, s.version, s.fence, s.record_hash,"
        " s.contract_digest, s.current_attempt_id, r.task_contract_id"
        " FROM sequences s JOIN records r ON r.record_hash = s.record_hash"
        " ORDER BY s.sequence_id"))

    by_state = {}
    for row in sequences:
        by_state.setdefault(row["state"], []).append(row["sequence_id"])

    attempts = []
    for row in cur.execute(
            "SELECT a.attempt_id, a.sequence_id, a.state, a.version, a.fence,"
            " a.attempt_contract_hash,"
            " c.role, c.provider, c.call_id, c.projection_address,"
            " c.native_session_id,"
            " (SELECT COUNT(*) FROM attempt_witnesses w"
            "   WHERE w.attempt_id = a.attempt_id) AS witnesses,"
            " (SELECT COUNT(*) FROM attempt_verifications v"
            "   WHERE v.attempt_id = a.attempt_id) AS verifications"
            " FROM attempts a LEFT JOIN attempt_calls c"
            " ON c.attempt_id = a.attempt_id"
            " ORDER BY a.attempt_id").fetchall():
        entry = {"attempt_id": row["attempt_id"],
                 "sequence_id": row["sequence_id"], "state": row["state"],
                 "version": row["version"], "fence": row["fence"],
                 "attempt_contract_hash": row["attempt_contract_hash"],
                 "witnesses": row["witnesses"],
                 "verifications": row["verifications"], "call": None}
        if row["call_id"] is not None:
            entry["call"] = {"role": row["role"], "provider": row["provider"],
                             "call_id": row["call_id"],
                             "projection_address": row["projection_address"],
                             "native_session_id": row["native_session_id"]}
        attempts.append(entry)

    def _attempts_in(state):
        return [a["attempt_id"] for a in attempts if a["state"] == state]

    work_eligibility = {
        "mark_ready": by_state.get("DECLARED", []),
        "start_execution": by_state.get("READY", []),
        "reserve_attempt": _attempts_in("DECLARED"),
        "dispatch_attempt": _attempts_in("RESERVED"),
    }

    obligations = {
        "dispatched_awaiting_session": _attempts_in("DISPATCHED"),
        "running": _attempts_in("RUNNING"),
        "finished_awaiting_freeze": _attempts_in("FINISHED"),
        "frozen_awaiting_evidence": _attempts_in("CANDIDATE_FROZEN"),
        # The M4 verification surface consumes these; the gate after a
        # VERIFIED sequence is finalization (M5).
        "awaiting_verification": [
            {"attempt_id": a, "next_gate": "verification"}
            for a in _attempts_in("EVIDENCE_COMPLETE")],
        "halted_sequences": by_state.get("HALTED", []),
        "verified_awaiting_finalization": by_state.get("VERIFIED", []),
        "intents_in_doubt": _rows(cur, (
            "SELECT intent_id, state, effect_id, attempt_id"
            " FROM effect_intents WHERE state IN"
            " ('PENDING','UNKNOWN_OUTCOME') ORDER BY intent_id")),
        "queued_admissions": [r["admission_id"] for r in cur.execute(
            "SELECT admission_id FROM admissions"
            " WHERE state = 'QUEUED_FOR_YOU' ORDER BY admission_id")],
        "reservations_outstanding": _rows(cur, (
            "SELECT reservation_id, sequence_id, attempt_id,"
            " lease_expires_utc FROM reservations WHERE state = 'RESERVED'"
            " ORDER BY reservation_id")),
    }

    permits = _rows(cur, (
        "SELECT sequence_id, permitted_uses, consumed_uses, reserved_uses"
        " FROM permit_state ORDER BY sequence_id"))

    lease_row = cur.execute(
        "SELECT holder, epoch, expires_utc FROM instance_lease"
        " WHERE lease_key = 'sequencer'").fetchone()
    lease = dict(lease_row) if lease_row is not None else None

    head = cur.execute(
        "SELECT entry_hash FROM transition_journal"
        " ORDER BY seq DESC LIMIT 1").fetchone()
    count = cur.execute(
        "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    live_attempts = [a for a in attempts
                     if a["state"] not in ATTEMPT_TERMINAL]

    return {
        "sequences": sequences,
        "sequences_by_state": by_state,
        "attempts": attempts,
        "live_attempts": [a["attempt_id"] for a in live_attempts],
        "work_eligibility": work_eligibility,
        "obligations": obligations,
        "permits": permits,
        "lease": lease,
        "journal": {"entries": count,
                    "head_hash": head["entry_hash"] if head else None},
    }
