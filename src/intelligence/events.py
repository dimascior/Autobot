"""Planning events: the durable changes that legitimately wake the
Chief Engineer, derived from the authoritative TransitionRecord journal.

Event identity IS journal identity: ``planning_events.journal_seq`` is
the journal's own monotonic seq (PRIMARY KEY), so one durable transition
yields at most one planning event however often the pump runs and across
any restart — the watermark only bounds how far the scan must look back.
The pump reads through the READ-ONLY authority connection and writes
only the plan store.

Not every journal row is a planning event: attempt plumbing (t-a01..06,
witness admissions, reservations) is Taskforce progress the pump drives
directly; the Chief Engineer wakes for the registered decision points
below. Two intelligence-plane events — a new objective (cap-01) and an
admitted specialist finding — live as unconsumed rows of their own
tables rather than journal derivations.
"""

import json

# (machine, to_state) -> event kind, refined by rule where it matters.
_SEQUENCE_EVENTS = {
    "READY": "work_ready",
    "BLOCKED": "work_blocked",
    "HALTED": "sequence_halted",
    "VERIFIED": "sequence_verified",
    "COMMITTED": "sequence_committed",
    "ACCEPTED": "sequence_accepted",
    "DEPENDENCIES_RELEASED": "dependencies_released",
    "COMPLETED": "sequence_completed",
}
_READY_RULES = {
    "t-s11": "repair_ready",      # verification failed, repair budget left
    "t-s08": "retry_ready",       # attempt failed/stale, retry budget left
    "t-s18": "stale_base_ready",  # canonical ref moved under the candidate
}
_ATTEMPT_EVENTS = {
    "REJECTED": "verification_failed",
    "FAILED": "attempt_failed",
    "STALE": "attempt_stale",
}
_ADMISSION_EVENTS = {
    "FROZEN": "contract_admitted",
    "QUEUED_FOR_YOU": "admission_queued_for_you",
    "DUPLICATE": "admission_duplicate",
    "REJECTED": "admission_rejected",
}


def classify(payload):
    """The planning-event kind for one journal payload, or None."""
    machine = payload.get("machine")
    to_state = payload.get("to_state")
    if machine == "sequence":
        kind = _SEQUENCE_EVENTS.get(to_state)
        if to_state == "READY":
            kind = _READY_RULES.get(payload.get("rule"), kind)
        return kind
    if machine == "attempt":
        return _ATTEMPT_EVENTS.get(to_state)
    if machine == "contract_admission":
        return _ADMISSION_EVENTS.get(to_state)
    if machine == "registry_objects" and to_state == "ADMITTED":
        return "authority_object_admitted"
    return None


def pump_authority_events(ro_conn, plan):
    """Scan the journal past the plan-store watermark, record one
    planning event per registered decision point, advance the watermark.
    Returns {"scanned": n, "events": m, "last_seq": seq}."""
    watermark = plan.watermark()
    rows = ro_conn.execute(
        "SELECT seq, entry_hash, payload FROM transition_journal"
        " WHERE seq > ? ORDER BY seq", (watermark,)).fetchall()
    scanned = len(rows)
    events = 0
    last = watermark
    if not rows:
        return {"scanned": 0, "events": 0, "last_seq": watermark}
    with plan.transaction() as cur:
        for row in rows:
            last = row["seq"]
            payload = json.loads(row["payload"])
            kind = classify(payload)
            if kind is None:
                continue
            facts = payload.get("facts")
            cur.execute(
                "INSERT OR IGNORE INTO planning_events (journal_seq,"
                " entry_hash, event_kind, machine, subject_id, rule,"
                " facts, recorded_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["seq"], row["entry_hash"], kind, payload["machine"],
                 payload["subject_id"], payload["rule"],
                 json.dumps(facts, sort_keys=True) if facts else None,
                 payload["recorded_utc"]))
            events += cur.rowcount
        plan.set_watermark(cur, last)
    return {"scanned": scanned, "events": events, "last_seq": last}
