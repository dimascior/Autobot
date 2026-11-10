"""The dedicated authoritative database (D-62, D-67, D-68, D-70).

Stdlib ``sqlite3`` only, in a dedicated ``autobot.db``: WAL journal mode,
``foreign_keys=ON``, ``synchronous=FULL``, a bounded busy timeout and
explicit ``BEGIN IMMEDIATE`` transactions. No ORM. Migrations are explicit,
monotonic and non-destructive, and a database newer than the running code is
refused (D-70). The old coordinator ledger is a different file and is never
touched by this module (D-64).
"""

import sqlite3
from contextlib import contextmanager

from .models import SchemaTooNew

SCHEMA_VERSION = 4

# Schema v1 (D-67): explicit state tables for Sequence, Attempt,
# InstanceLease, permit state/reservations and EffectIntent, plus the
# object/content index and the TransitionRecord journal. Terminal-state sets
# in partial indexes mirror models.py (which a test pins to states.json).
_DDL_V1 = (
    """CREATE TABLE IF NOT EXISTS objects (
        object_hash TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        content_address TEXT NOT NULL,
        minted_utc TEXT NOT NULL
    )""",
    # SEM-19 / D-23: the Registry enforces repository_id uniqueness — one
    # logical repository per id; a newer declaration for the same repository
    # is a revision of that binding.
    """CREATE TABLE IF NOT EXISTS declarations (
        repository_id TEXT PRIMARY KEY,
        object_hash TEXT NOT NULL REFERENCES objects(object_hash)
    )""",
    # Frozen TaskContractRecords: digests stored beside the record, never
    # inside the hashed bytes (D-33, INV-72).
    """CREATE TABLE IF NOT EXISTS records (
        record_hash TEXT PRIMARY KEY,
        task_contract_id TEXT NOT NULL,
        requirement_digest TEXT NOT NULL,
        contract_digest TEXT NOT NULL,
        content_address TEXT NOT NULL,
        eligibility TEXT NOT NULL,
        frozen_utc TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS admissions (
        admission_id INTEGER PRIMARY KEY AUTOINCREMENT,
        state TEXT NOT NULL CHECK (state IN
            ('RECEIVED','DRAFT','QUEUED_FOR_YOU','FROZEN','DUPLICATE','REJECTED')),
        payload_content_address TEXT NOT NULL,
        submitted_by TEXT NOT NULL,
        contract_digest TEXT,
        record_hash TEXT,
        issues TEXT,
        received_utc TEXT NOT NULL,
        decided_utc TEXT
    )""",
    # obj-08: ledger state row, by_id_and_version. fence is the sequence's
    # current fence (INV-13); INV-54 allows a second live Sequence for the
    # same contract_digest only under force_new_sequence, so liveness per
    # digest is checked in the admission transaction, not by a unique index.
    """CREATE TABLE IF NOT EXISTS sequences (
        sequence_id TEXT PRIMARY KEY,
        record_hash TEXT NOT NULL REFERENCES records(record_hash),
        requirement_digest TEXT NOT NULL,
        contract_digest TEXT NOT NULL,
        state TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version >= 1 AND version <= 9007199254740991),
        fence INTEGER NOT NULL CHECK (fence >= 0 AND fence <= 9007199254740991),
        current_attempt_id TEXT,
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_sequences_contract
        ON sequences(contract_digest)""",
    # obj-09-backed attempt rows. INV-12: one current (non-terminal) attempt
    # per Sequence, enforced durably by a partial unique index.
    """CREATE TABLE IF NOT EXISTS attempts (
        attempt_id TEXT PRIMARY KEY,
        sequence_id TEXT NOT NULL REFERENCES sequences(sequence_id),
        state TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version >= 1 AND version <= 9007199254740991),
        fence INTEGER NOT NULL CHECK (fence >= 1 AND fence <= 9007199254740991),
        attempt_contract_hash TEXT NOT NULL REFERENCES objects(object_hash),
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_one_current
        ON attempts(sequence_id)
        WHERE state NOT IN ('VERIFIED','REJECTED','FAILED','STALE','FENCED','RECONCILED')""",
    # obj-39 / INV-25a: one InstanceLease holder at a time. A single row;
    # epoch increases on every (re)acquisition and fences out stale holders.
    """CREATE TABLE IF NOT EXISTS instance_lease (
        lease_key TEXT PRIMARY KEY CHECK (lease_key = 'sequencer'),
        holder TEXT NOT NULL,
        epoch INTEGER NOT NULL CHECK (epoch >= 1 AND epoch <= 9007199254740991),
        acquired_utc TEXT NOT NULL,
        expires_utc TEXT NOT NULL
    )""",
    # obj-23: per-Sequence standing permit counters. INV-48's arithmetic is a
    # table CHECK as well as runtime logic.
    """CREATE TABLE IF NOT EXISTS permit_state (
        sequence_id TEXT PRIMARY KEY REFERENCES sequences(sequence_id),
        permitted_uses INTEGER NOT NULL CHECK (permitted_uses >= 0 AND permitted_uses <= 9007199254740991),
        consumed_uses INTEGER NOT NULL DEFAULT 0 CHECK (consumed_uses >= 0),
        reserved_uses INTEGER NOT NULL DEFAULT 0 CHECK (reserved_uses >= 0),
        version INTEGER NOT NULL CHECK (version >= 1 AND version <= 9007199254740991),
        CHECK (consumed_uses + reserved_uses <= permitted_uses)
    )""",
    # obj-24 / INV-42: permit uses are reservation rows; INV-60: each has its
    # own renewable lease window.
    """CREATE TABLE IF NOT EXISTS reservations (
        reservation_id TEXT PRIMARY KEY,
        sequence_id TEXT NOT NULL REFERENCES permit_state(sequence_id),
        attempt_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('RESERVED','CONSUMED','RELEASED')),
        lease_expires_utc TEXT NOT NULL,
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )""",
    # obj-25: EffectIntent rows (schema now per D-67; lifecycle lands after
    # the D-71 milestone passes).
    """CREATE TABLE IF NOT EXISTS effect_intents (
        intent_id TEXT PRIMARY KEY,
        effect_id TEXT NOT NULL,
        intent_class TEXT NOT NULL,
        state TEXT NOT NULL,
        sequence_id TEXT,
        attempt_id TEXT,
        fence INTEGER,
        reservation_id TEXT,
        superseded_intent_id TEXT,
        created_utc TEXT NOT NULL,
        expires_utc TEXT,
        hard_timeout_utc TEXT,
        version INTEGER NOT NULL CHECK (version >= 1 AND version <= 9007199254740991)
    )""",
    # obj-32: append-only admission journal facts (payload preserved in the
    # CAS by content address).
    """CREATE TABLE IF NOT EXISTS duplicate_admissions (
        duplicate_admission_id TEXT PRIMARY KEY,
        payload_contract_digest TEXT NOT NULL,
        existing_record_hash TEXT NOT NULL,
        payload_content_address TEXT NOT NULL,
        submitted_by TEXT NOT NULL,
        received_utc TEXT NOT NULL
    )""",
    # obj-36: the hash-chained TransitionRecord journal (JH-1). payload holds
    # the canonical bytes' UTF-8 text, so every entry re-hashes exactly.
    """CREATE TABLE IF NOT EXISTS transition_journal (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_hash TEXT NOT NULL UNIQUE,
        previous_hash TEXT,
        payload TEXT NOT NULL,
        appended_utc TEXT NOT NULL
    )""",
)

# Schema v2 (D-70: explicit, monotonic, non-destructive): the obj-25 retry
# identity columns (t-i06's retry_same_effect/target/expected_old are checked
# against durable facts, CORR-56/INV-73).
_DDL_V2 = (
    "ALTER TABLE effect_intents ADD COLUMN target TEXT",
    "ALTER TABLE effect_intents ADD COLUMN expected_old TEXT",
    "ALTER TABLE effect_intents ADD COLUMN new_value TEXT",
)

# Schema v3 (D-70, additive): the attempt execution reference (t-a02's bound
# call identity, the dispatched role, provider, projection and native session
# handle) — one execution per attempt, keyed by the attempt itself.
_DDL_V3 = (
    """CREATE TABLE IF NOT EXISTS attempt_calls (
        attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
        role TEXT NOT NULL,
        provider TEXT NOT NULL,
        call_id TEXT NOT NULL,
        projection_address TEXT NOT NULL,
        native_session_id TEXT,
        created_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )""",
)

# Schema v4 (D-70, additive): the witness-admission seam (M3). Each row is
# one Chapter 1 D-21 structured admission entry — the witness_kind
# discriminator (a registered WitnessRecord object id, obj-11..obj-14), the
# producer (that kind's registered minter, a principals.json id), the D-31
# content address of the exact producer bytes, the D-27 admission instant,
# and the execution correlation (call_id; native session id once known) read
# from the ledger at admission. The identity index makes re-admission of
# identical evidence idempotent. Witness payload SEMANTICS stay Chapter 3's.
_DDL_V4 = (
    """CREATE TABLE IF NOT EXISTS attempt_witnesses (
        admission_id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
        witness_kind TEXT NOT NULL,
        producer TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        call_id TEXT NOT NULL,
        native_session_id TEXT,
        admitted_utc TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_witness_identity
        ON attempt_witnesses(attempt_id, witness_kind, producer, content_sha256)""",
)

_MIGRATIONS = {1: _DDL_V1, 2: _DDL_V2, 3: _DDL_V3, 4: _DDL_V4}


class Database:
    """One connection, one writer, explicit transactions (D-62, D-63)."""

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
            # A refused open (e.g. SchemaTooNew, D-70) must not leak the
            # connection handle it opened.
            self.conn.close()
            raise

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        """D-68: the one atomic unit. BEGIN IMMEDIATE takes the write lock up
        front, so verify-mutate-mint-journal commits or none of it exists."""
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
            cur.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            row = cur.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            current = int(row["value"]) if row else 0
            if current > SCHEMA_VERSION:
                raise SchemaTooNew(
                    "autobot.db is schema v%d but this runtime knows v%d; "
                    "refusing to open (D-70)" % (current, SCHEMA_VERSION)
                )
            for version in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version]:
                    cur.execute(statement)
            if current != SCHEMA_VERSION:
                cur.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
