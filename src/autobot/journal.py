"""The hash-chained TransitionRecord journal (obj-36; D-68, JH-1, INV-10).

One global append-only chain per ledger. Every entry's hash is
``journal_entry_hash(payload, previous)`` under the
``autobot/journal/transition/v1`` domain; the genesis entry omits
``previous`` (D-29, D-37). The stored payload text is the canonical bytes'
UTF-8, so validation re-hashes every entry exactly as it was appended.

Payload vocabulary: ``rule`` is a Chapter 0 transition id (``t-…``) when a
registered machine transition happened, or a ``ch2:``-prefixed rule for
authority primitives the full protocol later subsumes into their owning
transitions. The journal records exactly what happened — no entry claims a
protocol transition that did not occur.
"""

import json

from contract_library import canonicalize, journal_entry_hash

from .models import JournalBroken


def transition_record(machine, subject_id, rule, to_state, recorded_utc,
                      from_state=None, facts=None):
    """An obj-36 payload. Absent fields are omitted, never null (D-37)."""
    payload = {
        "schema_version": "v1",
        "kind": "TransitionRecord",
        "machine": machine,
        "subject_id": subject_id,
        "rule": rule,
        "to_state": to_state,
        "recorded_utc": recorded_utc,
    }
    if from_state is not None:
        payload["from_state"] = from_state
    if facts:
        payload["facts"] = facts
    return payload


def append(cur, payload):
    """Append one TransitionRecord inside the caller's transaction (D-68)."""
    row = cur.execute(
        "SELECT entry_hash FROM transition_journal ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    previous = row["entry_hash"] if row else None
    entry_hash = journal_entry_hash(payload, previous)
    cur.execute(
        "INSERT INTO transition_journal (entry_hash, previous_hash, payload, appended_utc)"
        " VALUES (?, ?, ?, ?)",
        (entry_hash, previous,
         canonicalize(payload).decode("utf-8"), payload["recorded_utc"]),
    )
    return entry_hash


def entries(cur):
    """Walk the whole chain, validating every link and every entry hash, and
    return the payloads in order (D-69 restart validation; recovery's
    reconciliation reads these)."""
    previous = None
    out = []
    for row in cur.execute(
        "SELECT seq, entry_hash, previous_hash, payload FROM transition_journal ORDER BY seq"
    ).fetchall():
        if row["previous_hash"] != previous:
            raise JournalBroken("chain link broken at seq %d" % row["seq"])
        payload = json.loads(row["payload"])
        if journal_entry_hash(payload, previous) != row["entry_hash"]:
            raise JournalBroken("entry hash mismatch at seq %d" % row["seq"])
        previous = row["entry_hash"]
        out.append(payload)
    return out


def validate(cur):
    """Chain validation alone (CH2-failpoint-matrix's chain check)."""
    return len(entries(cur))
