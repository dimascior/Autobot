"""The Chief Engineer's own durable reasoning store (NOT authority).

Three information classes stay distinct in every interface of this
package (the directive's memory model):

1. **Authoritative state** is read from autobot.db — through
   ``readonly_view``, a SQLite connection opened ``mode=ro``, so this
   plane is physically unable to mutate authority through its state
   reads. Authoritative writes exist only behind the public
   Registry/Sequencer APIs.
2. **Evidence/history** is referenced by content address (receipts,
   witnesses, projections in the CAS); the plan store keeps references,
   never authoritative copies.
3. **Reasoning state** — objectives (cap-01: authored by you), planning
   cycles, typed proposals (cap-13), findings, seats and watermarks —
   lives HERE, in a dedicated ``intelligence.db`` beside the planner.
   Losing this file loses no authority: contracts, sequences, receipts
   and released edges all remain in the ledger.

Same storage discipline as the authority database (WAL, foreign keys,
synchronous=FULL, BEGIN IMMEDIATE, explicit monotonic migrations, refuse
a newer schema), stdlib only.
"""

import sqlite3
from contextlib import contextmanager

PLAN_SCHEMA_VERSION = 1

_DDL_V1 = (
    # cap-01: the project objective is yours; the planner references it.
    """CREATE TABLE IF NOT EXISTS objectives (
        objective_id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        authored_by TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','COMPLETE')),
        created_utc TEXT NOT NULL,
        completed_utc TEXT,
        consumed_in_cycle INTEGER
    )""",
    # One Chief Engineer cycle per authoritative change batch; the cycle
    # binds the exact ProjectState snapshot it reasoned over.
    """CREATE TABLE IF NOT EXISTS cycles (
        cycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_utc TEXT NOT NULL,
        snapshot_entries INTEGER NOT NULL,
        snapshot_head TEXT,
        status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
        closed_utc TEXT,
        note TEXT
    )""",
    # Planning events derived from the authoritative journal: the PRIMARY
    # KEY is the journal seq itself, so one durable transition yields at
    # most one planning event, however often the pump runs.
    """CREATE TABLE IF NOT EXISTS planning_events (
        journal_seq INTEGER PRIMARY KEY,
        entry_hash TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        machine TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        rule TEXT NOT NULL,
        facts TEXT,
        recorded_utc TEXT NOT NULL,
        consumed_in_cycle INTEGER
    )""",
    # Typed Chief Engineer proposals (cap-13). proposal_id is the
    # DETERMINISTIC identity (for contract classes: the candidate's
    # Chapter 1 contract_digest), so a restarted planner re-deriving the
    # same engineering intent converges on the same row, and a duplicate
    # submission converges at the Registry (obj-32).
    """CREATE TABLE IF NOT EXISTS proposals (
        proposal_id TEXT PRIMARY KEY,
        cycle_id INTEGER NOT NULL REFERENCES cycles(cycle_id),
        class TEXT NOT NULL,
        objective_id TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('EVALUATED','SUBMITTED','ADMITTED','DUPLICATE',
             'QUEUED_FOR_YOU','REJECTED','WAITING','APPLIED','CLOSED')),
        rationale TEXT NOT NULL,
        body TEXT NOT NULL,
        candidate TEXT,
        contract_digest TEXT,
        task_contract_id TEXT,
        admission_id INTEGER,
        sequence_id TEXT,
        snapshot_entries INTEGER NOT NULL,
        snapshot_head TEXT,
        created_utc TEXT NOT NULL,
        decided_utc TEXT,
        note TEXT
    )""",
    # Structured specialist findings: engineering information, never
    # work. Each is traceable to its attempt and its evidence addresses;
    # only a Chief Engineer proposal turns one into a contract.
    """CREATE TABLE IF NOT EXISTS findings (
        finding_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL,
        sequence_id TEXT NOT NULL,
        call_id TEXT,
        role TEXT NOT NULL,
        finding_class TEXT NOT NULL,
        affected_component TEXT NOT NULL,
        evidence_refs TEXT NOT NULL,
        significance TEXT NOT NULL,
        suggested_follow_up TEXT,
        confidence TEXT NOT NULL,
        relation_to_requirement TEXT NOT NULL,
        admitted_utc TEXT NOT NULL,
        consumed_in_cycle INTEGER
    )""",
    # Taskforce seats: one row per allocated (or authorized) execution
    # seat. Provider/model live HERE (cap-14, agent_factory), never in
    # the frozen contract, so the organization can change models without
    # changing authority identity.
    """CREATE TABLE IF NOT EXISTS seats (
        seat_id TEXT PRIMARY KEY,
        sequence_id TEXT NOT NULL,
        attempt_id TEXT,
        call_id TEXT,
        role TEXT NOT NULL,
        capability TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT,
        selected_by TEXT,
        projection_address TEXT,
        artifact_address TEXT,
        cycle_id INTEGER,
        sibling_group TEXT,
        authorized_by_proposal TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('AUTHORIZED','DISPATCHED','COLLECTED','CLOSED')),
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )""",
    # One continuously reasoning driver at a time (the planner-side
    # mirror of the Sequencer's instance lease; it guards REASONING
    # concurrency only — authority is already single-writer).
    """CREATE TABLE IF NOT EXISTS driver_lease (
        lease_key TEXT PRIMARY KEY CHECK (lease_key = 'driver'),
        holder TEXT NOT NULL,
        acquired_utc TEXT NOT NULL,
        expires_utc TEXT NOT NULL
    )""",
)


class PlanStoreTooNew(Exception):
    """intelligence.db is newer than the running code."""


class DriverBusy(Exception):
    """Another unexpired driver holds the reasoning lease."""


def readonly_view(authority_db_path):
    """A READ-ONLY connection to the authority ledger (mode=ro): the one
    handle this plane may use for authoritative state and journal reads.
    Any write on it raises ``sqlite3.OperationalError``."""
    conn = sqlite3.connect(
        "file:%s?mode=ro" % str(authority_db_path).replace("\\", "/"),
        uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class PlanStore:
    def __init__(self, path, busy_timeout_ms=5000):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA busy_timeout=%d" % int(busy_timeout_ms))
        try:
            self._migrate()
        except BaseException:
            self.conn.close()
            raise

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        cur = self.conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            yield cur
        except BaseException:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def _migrate(self):
        with self.transaction() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS meta"
                        " (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = cur.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            current = int(row["value"]) if row else 0
            if current > PLAN_SCHEMA_VERSION:
                raise PlanStoreTooNew(
                    "intelligence.db is schema v%d but this code knows v%d"
                    % (current, PLAN_SCHEMA_VERSION))
            if current < 1:
                for statement in _DDL_V1:
                    cur.execute(statement)
            if current != PLAN_SCHEMA_VERSION:
                cur.execute(
                    "INSERT OR REPLACE INTO meta(key, value)"
                    " VALUES ('schema_version', ?)",
                    (str(PLAN_SCHEMA_VERSION),))

    # ---------------------------------------------------------- watermark

    def watermark(self):
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='authority_watermark'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def set_watermark(self, cur, seq):
        cur.execute(
            "INSERT OR REPLACE INTO meta(key, value)"
            " VALUES ('authority_watermark', ?)", (str(int(seq)),))

    # --------------------------------------------------------- objectives

    def add_objective(self, objective_id, text, authored_by, now):
        with self.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO objectives (objective_id, text,"
                " authored_by, status, created_utc)"
                " VALUES (?, ?, ?, 'ACTIVE', ?)",
                (objective_id, text, authored_by, now))
        return objective_id

    def active_objectives(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM objectives WHERE status = 'ACTIVE'"
            " ORDER BY objective_id").fetchall()]

    def complete_objective(self, objective_id, now):
        with self.transaction() as cur:
            cur.execute(
                "UPDATE objectives SET status = 'COMPLETE',"
                " completed_utc = ? WHERE objective_id = ?"
                " AND status = 'ACTIVE'", (now, objective_id))

    # ------------------------------------------------------------- cycles

    def open_cycle(self, now, snapshot_entries, snapshot_head):
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO cycles (started_utc, snapshot_entries,"
                " snapshot_head, status) VALUES (?, ?, ?, 'OPEN')",
                (now, snapshot_entries, snapshot_head))
            return cur.lastrowid

    def close_cycle(self, cycle_id, now, note=None):
        with self.transaction() as cur:
            cur.execute(
                "UPDATE cycles SET status = 'CLOSED', closed_utc = ?,"
                " note = ? WHERE cycle_id = ? AND status = 'OPEN'",
                (now, note, cycle_id))

    # ------------------------------------------------------------- events

    def unconsumed_events(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM planning_events WHERE consumed_in_cycle IS NULL"
            " ORDER BY journal_seq").fetchall()]

    def consume_events(self, cur, seqs, cycle_id):
        for seq in seqs:
            cur.execute(
                "UPDATE planning_events SET consumed_in_cycle = ?"
                " WHERE journal_seq = ? AND consumed_in_cycle IS NULL",
                (cycle_id, seq))

    # ---------------------------------------------------------- proposals

    def proposal(self, proposal_id):
        row = self.conn.execute(
            "SELECT * FROM proposals WHERE proposal_id = ?",
            (proposal_id,)).fetchone()
        return dict(row) if row else None

    def proposals_by_status(self, *statuses):
        marks = ",".join("?" for _ in statuses)
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM proposals WHERE status IN (%s)"
            " ORDER BY created_utc, proposal_id" % marks,
            tuple(statuses)).fetchall()]

    def set_proposal_status(self, proposal_id, status, now, **fields):
        allowed = ("contract_digest", "task_contract_id", "admission_id",
                   "sequence_id", "note")
        sets, args = ["status = ?", "decided_utc = ?"], [status, now]
        for key in allowed:
            if key in fields:
                sets.append("%s = ?" % key)
                args.append(fields[key])
        args.append(proposal_id)
        with self.transaction() as cur:
            cur.execute(
                "UPDATE proposals SET %s WHERE proposal_id = ?"
                % ", ".join(sets), tuple(args))

    # ----------------------------------------------------------- findings

    def unconsumed_findings(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM findings WHERE consumed_in_cycle IS NULL"
            " ORDER BY admitted_utc, finding_id").fetchall()]

    def consume_findings(self, cur, finding_ids, cycle_id):
        for finding_id in finding_ids:
            cur.execute(
                "UPDATE findings SET consumed_in_cycle = ?"
                " WHERE finding_id = ? AND consumed_in_cycle IS NULL",
                (cycle_id, finding_id))

    def consume_objectives(self, cur, objective_ids, cycle_id):
        for objective_id in objective_ids:
            cur.execute(
                "UPDATE objectives SET consumed_in_cycle = ?"
                " WHERE objective_id = ? AND consumed_in_cycle IS NULL",
                (cycle_id, objective_id))

    # -------------------------------------------------------------- seats

    def seats_for(self, sequence_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM seats WHERE sequence_id = ?"
            " ORDER BY created_utc, seat_id", (sequence_id,)).fetchall()]

    def open_authorization(self, sequence_id):
        row = self.conn.execute(
            "SELECT * FROM seats WHERE sequence_id = ? AND"
            " status = 'AUTHORIZED' ORDER BY created_utc LIMIT 1",
            (sequence_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------- driver lease

    def acquire_driver_lease(self, holder, now, expires):
        with self.transaction() as cur:
            row = cur.execute(
                "SELECT holder, expires_utc FROM driver_lease"
                " WHERE lease_key = 'driver'").fetchone()
            if row is not None and row["holder"] != holder \
                    and row["expires_utc"] > now:
                raise DriverBusy(
                    "driver lease held by %r until %s"
                    % (row["holder"], row["expires_utc"]))
            cur.execute(
                "INSERT OR REPLACE INTO driver_lease"
                " (lease_key, holder, acquired_utc, expires_utc)"
                " VALUES ('driver', ?, ?, ?)", (holder, now, expires))

    def release_driver_lease(self, holder):
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM driver_lease WHERE lease_key = 'driver'"
                " AND holder = ?", (holder,))
