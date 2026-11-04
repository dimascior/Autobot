"""SQLite ledger -- items, dependencies, dispatches, cycles, audit, quarantine,
and resolutions.

State authority lives here. Four ideas the schema enforces:

1. kind (provenance): only `task` items are dispatchable. `finding`, `execution`
   and `gate` items are historical evidence; `unknown` is a placeholder for a
   predecessor we have seen referenced but not yet ingested.
2. a managed lifecycle: ingest sets a NEW item's initial status, but never
   rewrites the status of an item it has already seen (state.py / dispatch.py own
   transitions). Terminal statuses are sticky. Re-ingesting the same evidence is
   therefore idempotent -- no duplicate items, no status churn.
3. execution != resolution: a finished execution parks its task at
   `awaiting_resolution`. The ONLY path that moves a task to `completed` is
   `authorize_resolution`, which an explicit authority invokes. No loop stage
   auto-promotes a successful run into a closed task.
4. non-destructive migration: schema upgrades ADD tables/columns and preserve
   every existing row. A timestamped backup is taken first and row counts are
   validated after; on any failure the backup is restored.
"""

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 3

# Only these kinds are ever dispatched. Everything else is evidence/context.
DISPATCHABLE_KINDS = ("task",)
# A predecessor only stops blocking its dependents once it is completed.
SATISFIED_STATUSES = ("completed",)
# Statuses that exclude an item from dispatch outright.
TERMINAL_STATUSES = ("completed", "cancelled", "superseded")
# Statuses surfaced for human / retry-policy reconciliation (never auto-dispatch).
RECONCILE_STATUSES = ("stale", "failed", "awaiting_resolution")
# A resolution evaluator proposes exactly one of these verdicts.
RESOLUTION_VERDICTS = ("resolved", "rework", "review")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'task',
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT NOT NULL,
    source_ref TEXT DEFAULT '',
    task_class TEXT NOT NULL DEFAULT 'investigate',
    route TEXT NOT NULL DEFAULT 'luna',
    priority INTEGER DEFAULT 0,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    resolved_utc TEXT,
    cooldown_until TEXT,
    dispatch_count INTEGER DEFAULT 0,
    content_hash TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS dependencies (
    blocker_id TEXT NOT NULL,
    blocked_id TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id),
    FOREIGN KEY (blocker_id) REFERENCES items(id),
    FOREIGN KEY (blocked_id) REFERENCES items(id)
);

CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    started_utc TEXT NOT NULL,
    completed_utc TEXT,
    items_ingested INTEGER DEFAULT 0,
    items_planned INTEGER DEFAULT 0,
    items_dispatched INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    cycle_id INTEGER NOT NULL,
    call_id TEXT,
    model TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    dispatched_utc TEXT NOT NULL,
    completed_utc TEXT,
    result_summary TEXT,
    outcome TEXT,
    FOREIGN KEY (item_id) REFERENCES items(id),
    FOREIGN KEY (cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS approval_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    cycle_id INTEGER NOT NULL,
    proposed_prompt TEXT NOT NULL,
    proposed_model TEXT NOT NULL,
    reason TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_utc TEXT NOT NULL,
    resolved_utc TEXT,
    FOREIGN KEY (item_id) REFERENCES items(id),
    FOREIGN KEY (cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS resolutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    dispatch_id INTEGER,
    verdict TEXT NOT NULL,                 -- resolved | rework | review
    evaluator TEXT NOT NULL,               -- evaluator identity (rule id / model)
    rationale TEXT DEFAULT '',
    acceptance_criteria TEXT DEFAULT '',   -- snapshot of the task's criteria at eval time
    source_refs TEXT DEFAULT '[]',         -- json list of the evidence records compared
    evidence_hashes TEXT DEFAULT '{}',     -- json map: ref -> sha256 (evidence lineage)
    status TEXT NOT NULL DEFAULT 'proposed', -- proposed | authorized | rejected
    authority TEXT,                        -- who authorized the material state change
    authorized_utc TEXT,
    created_utc TEXT NOT NULL,
    FOREIGN KEY (item_id) REFERENCES items(id),
    FOREIGN KEY (dispatch_id) REFERENCES dispatches(id)
);

CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    reason TEXT NOT NULL,
    first_seen_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    UNIQUE(path)
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    input_json TEXT,
    output_json TEXT,
    timestamp_utc TEXT NOT NULL,
    FOREIGN KEY (cycle_id) REFERENCES cycles(id)
);

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_domain ON items(domain);
CREATE INDEX IF NOT EXISTS idx_items_cooldown ON items(cooldown_until);
CREATE INDEX IF NOT EXISTS idx_dispatches_item ON dispatches(item_id);
CREATE INDEX IF NOT EXISTS idx_dispatches_status ON dispatches(status);
CREATE INDEX IF NOT EXISTS idx_dispatches_call ON dispatches(call_id);
CREATE INDEX IF NOT EXISTS idx_resolutions_item ON resolutions(item_id);
CREATE INDEX IF NOT EXISTS idx_resolutions_status ON resolutions(status);
CREATE INDEX IF NOT EXISTS idx_audit_cycle ON audit(cycle_id);
"""

# Columns the provenance schema (v2) introduced on top of a pre-provenance (v1)
# items table. Used by the v1->v2 migration to ADD them without dropping rows.
_V2_ITEM_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'task'",
    "evidence": "TEXT DEFAULT ''",
    "source_ref": "TEXT DEFAULT ''",
    "task_class": "TEXT NOT NULL DEFAULT 'investigate'",
    "route": "TEXT NOT NULL DEFAULT 'luna'",
    "last_seen_utc": "TEXT",
    "resolved_utc": "TEXT",
    "cooldown_until": "TEXT",
    "dispatch_count": "INTEGER DEFAULT 0",
    "content_hash": "TEXT DEFAULT ''",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ── migrations (additive, non-destructive) ───────────────────────────

def _columns_of(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_missing_columns(conn, table: str, columns: dict) -> list:
    """ADD COLUMN for every column not already present. Returns names added."""
    have = _columns_of(conn, table)
    added = []
    for name, decl in columns.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            added.append(name)
    return added


def _migrate_1_to_2(conn):
    """Pre-provenance ledger -> provenance schema.

    Adds the v2 item columns onto the existing items table (rows preserved; new
    columns take their defaults) and creates the tables v2 introduced. Purely
    additive -- no DROP, no row rewrite. Legacy rows keep their data; because v1
    had no `kind`, they default to kind='task', so a legacy ledger should be
    reviewed after upgrade (the pre-migration backup is retained for that).
    """
    if "items" in _table_names(conn):
        _add_missing_columns(conn, "items", _V2_ITEM_COLUMNS)
    # tables new in v2 (idempotent; full definitions live in _SCHEMA)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL, reason TEXT NOT NULL,
            first_seen_utc TEXT NOT NULL, last_seen_utc TEXT NOT NULL,
            UNIQUE(path)
        );
        """
    )


def _migrate_2_to_3(conn):
    """Provenance schema -> resolution schema.

    Adds the `resolutions` table (durable verdicts + evidence lineage). Pure
    CREATE TABLE -- it touches no existing row.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS resolutions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            dispatch_id INTEGER,
            verdict TEXT NOT NULL,
            evaluator TEXT NOT NULL,
            rationale TEXT DEFAULT '',
            acceptance_criteria TEXT DEFAULT '',
            source_refs TEXT DEFAULT '[]',
            evidence_hashes TEXT DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'proposed',
            authority TEXT,
            authorized_utc TEXT,
            created_utc TEXT NOT NULL,
            FOREIGN KEY (item_id) REFERENCES items(id),
            FOREIGN KEY (dispatch_id) REFERENCES dispatches(id)
        );
        """
    )


# version N is reached by running MIGRATIONS[N] against a db at version N-1.
MIGRATIONS = {
    2: _migrate_1_to_2,
    3: _migrate_2_to_3,
}


def _table_names(conn) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


class Ledger:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), isolation_level="DEFERRED")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    # ── schema / migration ───────────────────────────────────────────

    def _row_counts(self) -> dict:
        return {t: self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                for t in _table_names(self.conn)}

    def _set_version(self, version: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(version),),
        )

    def _current_version(self) -> int:
        tables = _table_names(self.conn)
        if not tables:
            return 0            # brand-new file
        if "meta" not in tables:
            return 1            # legacy tables, no version marker -> pre-provenance
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 1

    def _ensure_schema(self):
        ver = self._current_version()

        if ver == 0:
            # fresh database: create everything at the latest version directly
            self.conn.executescript(_SCHEMA)
            self._set_version(SCHEMA_VERSION)
            self.conn.commit()
            return

        if ver > SCHEMA_VERSION:
            raise RuntimeError(
                f"ledger db schema v{ver} is newer than this code (v{SCHEMA_VERSION}); "
                "upgrade the coordinator before opening this database."
            )

        if ver < SCHEMA_VERSION:
            self._migrate(ver)

        # idempotent safety net: create any objects/indexes still missing, then
        # stamp the version. Never drops anything.
        self.conn.executescript(_SCHEMA)
        self._set_version(SCHEMA_VERSION)
        self.conn.commit()

    def _migrate(self, from_ver: int):
        """Run every migration from_ver+1..SCHEMA_VERSION, non-destructively.

        A timestamped copy of the db file is taken first (rollback artifact).
        Row counts of every pre-existing table are validated unchanged after;
        on any failure the migration is rolled back and the backup restored.
        """
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.commit()
        stamp = _now().replace(":", "").replace("-", "")
        backup = Path(str(self.db_path) + f".bak.v{from_ver}.{stamp}")
        shutil.copy2(self.db_path, backup)

        # Migrations are additive DDL (CREATE TABLE / ADD COLUMN), each
        # auto-committed. The file backup above is the rollback artifact: on ANY
        # failure -- a migration error OR a row-count change on a pre-existing
        # table -- the live db is restored from it and construction fails loudly.
        pre = self._row_counts()
        try:
            for target in range(from_ver + 1, SCHEMA_VERSION + 1):
                fn = MIGRATIONS.get(target)
                if fn:
                    fn(self.conn)
            self.conn.commit()
            post = self._row_counts()
            for tbl, n in pre.items():
                if post.get(tbl, -1) != n:
                    raise RuntimeError(
                        f"migration changed row count for '{tbl}' "
                        f"({n} -> {post.get(tbl)})"
                    )
        except Exception as exc:  # noqa: BLE001
            self._restore_backup(backup)
            raise RuntimeError(
                f"migration v{from_ver}->v{SCHEMA_VERSION} failed ({exc}); "
                f"database restored from {backup}"
            ) from exc
        import logging
        logging.getLogger(__name__).info(
            "migrated ledger v%d -> v%d (%d tables preserved; backup %s)",
            from_ver, SCHEMA_VERSION, len(pre), backup.name,
        )

    def _restore_backup(self, backup: Path):
        """Replace the live db file with the pre-migration backup."""
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
        for suffix in ("-wal", "-shm"):
            side = Path(str(self.db_path) + suffix)
            if side.exists():
                side.unlink()
        shutil.copy2(backup, self.db_path)

    # ── items ────────────────────────────────────────────────────────

    def upsert_item(
        self,
        *,
        id: str,
        domain: str,
        title: str,
        kind: str = "task",
        description: str = "",
        evidence: str = "",
        source: str = "",
        source_ref: str = "",
        task_class: str = "investigate",
        route: str = "luna",
        priority: int = 0,
        status: str = "open",
    ) -> bool:
        """Insert a new item, or update the content of an existing one.

        Idempotent: ingest never rewrites a managed status (state/dispatch own
        transitions), only upgrades a placeholder. updated_utc moves only when
        the item's content actually changes; last_seen_utc moves every sweep.
        Returns True only when a brand-new item was created.
        """
        ts = _now()
        chash = _content_hash(kind, domain, title, description, evidence,
                              task_class, route, priority)
        existing = self.conn.execute(
            "SELECT status, kind, content_hash FROM items WHERE id=?", (id,)
        ).fetchone()

        if existing:
            # Decide the resulting status: ingest does not move managed states.
            # The one exception is upgrading an `unknown/unresolved` placeholder
            # into the real item it turned out to be.
            if existing["kind"] == "unknown" and existing["status"] == "unresolved":
                new_status, new_kind = status, kind
            else:
                new_status, new_kind = existing["status"], existing["kind"]
            changed = existing["content_hash"] != chash
            upd = (
                "UPDATE items SET kind=?, domain=?, title=?, description=?, "
                "evidence=?, source=?, source_ref=?, task_class=?, route=?, "
                "priority=?, status=?, content_hash=?, last_seen_utc=?"
                + (", updated_utc=?" if changed else "")
                + " WHERE id=?"
            )
            params = [new_kind, domain, title, description, evidence, source,
                      source_ref, task_class, route, priority, new_status,
                      chash, ts]
            if changed:
                params.append(ts)
            params.append(id)
            self.conn.execute(upd, params)
            return False

        self.conn.execute(
            """INSERT INTO items
               (id, kind, domain, title, description, evidence, status, source,
                source_ref, task_class, route, priority, created_utc,
                updated_utc, last_seen_utc, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (id, kind, domain, title, description, evidence, status, source,
             source_ref, task_class, route, priority, ts, ts, ts, chash),
        )
        return True

    def ensure_placeholder(self, item_id: str) -> bool:
        """Create a placeholder for a referenced-but-unseen predecessor.

        Kept as kind=unknown / status=unresolved so it is never dispatched and
        always blocks its dependents until the real predecessor is ingested
        (which upgrades it) or a human resolves it.
        """
        row = self.conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone()
        if row:
            return False
        ts = _now()
        self.conn.execute(
            """INSERT INTO items
               (id, kind, domain, title, description, evidence, status, source,
                source_ref, task_class, route, priority, created_utc,
                updated_utc, last_seen_utc, content_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, "unknown", "unknown", item_id, "", "", "unresolved",
             "placeholder", "", "investigate", "luna", 0, ts, ts, ts, ""),
        )
        return True

    def set_status(self, item_id: str, status: str):
        ts = _now()
        extras = ""
        params: list = [status, ts]
        if status in TERMINAL_STATUSES:
            extras = ", resolved_utc=?"
            params.append(ts)
        params.append(item_id)
        self.conn.execute(
            f"UPDATE items SET status=?, updated_utc=?{extras} WHERE id=?",
            params,
        )

    def set_cooldown(self, item_id: str, minutes: int):
        ts = _now()
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self.conn.execute(
            "UPDATE items SET cooldown_until=?, updated_utc=? WHERE id=?",
            (until, ts, item_id),
        )

    def get_item(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def is_unblocked(self, item_id: str) -> bool:
        """True iff every predecessor is in a satisfied (completed) status."""
        for bid in self.blockers_of(item_id):
            b = self.get_item(bid)
            if not b or b["status"] not in SATISFIED_STATUSES:
                return False
        return True

    def dispatchable_tasks(self) -> list[dict]:
        """Candidate work: dispatchable kind, open, off cooldown, and unblocked."""
        ts = _now()
        placeholders = ",".join("?" for _ in DISPATCHABLE_KINDS)
        rows = self.conn.execute(
            f"""SELECT * FROM items
                WHERE kind IN ({placeholders}) AND status='open'
                AND (cooldown_until IS NULL OR cooldown_until <= ?)
                ORDER BY priority DESC, created_utc ASC""",
            (*DISPATCHABLE_KINDS, ts),
        ).fetchall()
        return [dict(r) for r in rows if self.is_unblocked(r["id"])]

    def items_by_status(self, *statuses: str) -> list[dict]:
        placeholders = ",".join("?" for _ in statuses)
        rows = self.conn.execute(
            f"SELECT * FROM items WHERE status IN ({placeholders}) "
            "ORDER BY priority DESC, created_utc ASC",
            statuses,
        ).fetchall()
        return [dict(r) for r in rows]

    def reconciliation_items(self) -> list[dict]:
        return self.items_by_status(*RECONCILE_STATUSES)

    def all_items(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM items ORDER BY priority DESC, created_utc ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_by_kind_status(self) -> dict:
        rows = self.conn.execute(
            "SELECT kind, status, COUNT(*) AS n FROM items GROUP BY kind, status"
        ).fetchall()
        return {f"{r['kind']}/{r['status']}": r["n"] for r in rows}

    # ── dependencies ─────────────────────────────────────────────────

    def add_dependency(self, blocker_id: str, blocked_id: str):
        """Record a predecessor edge, preserving unresolved predecessors.

        A referenced predecessor that has not been ingested is NOT dropped: we
        mint an `unknown/unresolved` placeholder so the edge survives and the
        dependent stays blocked until the predecessor's real status is known.
        """
        if blocker_id == blocked_id:
            return
        if not self.conn.execute("SELECT 1 FROM items WHERE id=?", (blocked_id,)).fetchone():
            return
        self.ensure_placeholder(blocker_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO dependencies VALUES (?,?,?)",
            (blocker_id, blocked_id, _now()),
        )

    def blockers_of(self, item_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT blocker_id FROM dependencies WHERE blocked_id=?", (item_id,)
        ).fetchall()
        return [r["blocker_id"] for r in rows]

    def blocked_by(self, item_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT blocked_id FROM dependencies WHERE blocker_id=?", (item_id,)
        ).fetchall()
        return [r["blocked_id"] for r in rows]

    def all_dependencies(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM dependencies").fetchall()
        return [dict(r) for r in rows]

    # ── cycles ───────────────────────────────────────────────────────

    def start_cycle(self, trigger: str, dry_run: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO cycles (trigger, started_utc, dry_run) VALUES (?,?,?)",
            (trigger, _now(), 1 if dry_run else 0),
        )
        self.conn.commit()
        return cur.lastrowid

    def complete_cycle(
        self, cycle_id: int, *, ingested: int = 0, planned: int = 0, dispatched: int = 0
    ):
        self.conn.execute(
            """UPDATE cycles SET completed_utc=?, items_ingested=?,
               items_planned=?, items_dispatched=? WHERE id=?""",
            (_now(), ingested, planned, dispatched, cycle_id),
        )
        self.conn.commit()

    def cycles_since(self, hours: int = 1) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = self.conn.execute(
            "SELECT * FROM cycles WHERE started_utc >= ? ORDER BY id DESC",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── dispatches ───────────────────────────────────────────────────

    def reserve_dispatch(
        self, *, item_id: str, cycle_id: int, model: str, prompt: str
    ) -> int:
        """Atomically reserve a dispatch BEFORE launching the child.

        The item is moved to `in_progress` and a `pending` dispatch row (no
        call_id yet) is written in one transaction. This is the restart-safety
        anchor: the moment this commits, the item leaves the dispatchable pool,
        so a crash/restart between here and the child's launch can never cause a
        second dispatch. A reservation left with no call_id is caught by
        staleness and surfaced for reconciliation -- never silently re-run.
        """
        ts = _now()
        cur = self.conn.execute(
            """INSERT INTO dispatches
               (item_id, cycle_id, call_id, model, prompt, status, dispatched_utc)
               VALUES (?,?,?,?,?,?,?)""",
            (item_id, cycle_id, "", model, prompt, "pending", ts),
        )
        self.conn.execute(
            "UPDATE items SET status='in_progress', dispatch_count=dispatch_count+1, "
            "updated_utc=? WHERE id=?",
            (ts, item_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def mark_dispatch_launched(self, dispatch_id: int, call_id: str):
        self.conn.execute(
            "UPDATE dispatches SET status='running', call_id=? WHERE id=?",
            (call_id, dispatch_id),
        )
        self.conn.commit()

    def fail_reservation(self, dispatch_id: int, item_id: str, reason: str,
                         revert_status: str = "open"):
        """A reserved dispatch whose child never launched: mark the row failed
        and return the item to `revert_status` (default open, so a later cycle
        may retry). Only call this when the launch provably did not start a
        child -- otherwise leave the reservation for reconciliation."""
        ts = _now()
        self.conn.execute(
            "UPDATE dispatches SET status='failed', completed_utc=?, outcome=?, "
            "result_summary=? WHERE id=?",
            (ts, "launch-failed", reason[:500], dispatch_id),
        )
        self.conn.execute(
            "UPDATE items SET status=?, updated_utc=? WHERE id=?",
            (revert_status, ts, item_id),
        )
        self.conn.commit()

    def record_dispatch(
        self,
        *,
        item_id: str,
        cycle_id: int,
        model: str,
        prompt: str,
        call_id: str = "",
    ) -> int:
        ts = _now()
        cur = self.conn.execute(
            """INSERT INTO dispatches
               (item_id, cycle_id, call_id, model, prompt, status, dispatched_utc)
               VALUES (?,?,?,?,?,?,?)""",
            (item_id, cycle_id, call_id, model, prompt, "pending", ts),
        )
        self.conn.execute(
            "UPDATE items SET dispatch_count=dispatch_count+1, updated_utc=? WHERE id=?",
            (ts, item_id),
        )
        return cur.lastrowid

    def update_dispatch(self, dispatch_id: int, *, status: str, outcome: str = "",
                        result_summary: str = "", call_id: str = ""):
        sets = ["status=?", "completed_utc=?"]
        params: list = [status, _now()]
        if outcome:
            sets.append("outcome=?")
            params.append(outcome)
        if result_summary:
            sets.append("result_summary=?")
            params.append(result_summary)
        if call_id:
            sets.append("call_id=?")
            params.append(call_id)
        params.append(dispatch_id)
        self.conn.execute(
            f"UPDATE dispatches SET {','.join(sets)} WHERE id=?", params
        )

    def active_dispatches(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM dispatches WHERE status IN ('pending','running')"
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_dispatch(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM dispatches WHERE item_id=? ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None

    def dispatches_for(self, item_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM dispatches WHERE item_id=? ORDER BY id DESC", (item_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def was_dispatched_recently(self, item_id: str, minutes: int = 30) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        row = self.conn.execute(
            "SELECT 1 FROM dispatches WHERE item_id=? AND dispatched_utc >= ? LIMIT 1",
            (item_id, cutoff),
        ).fetchone()
        return row is not None

    def has_open_dispatch(self, item_id: str) -> bool:
        """True iff a pending/running dispatch already exists for this item --
        a second guard against duplicate launch across a restart."""
        row = self.conn.execute(
            "SELECT 1 FROM dispatches WHERE item_id=? AND status IN ('pending','running') "
            "LIMIT 1",
            (item_id,),
        ).fetchone()
        return row is not None

    # ── approval queue ───────────────────────────────────────────────

    def queue_approval(
        self, *, item_id: str, cycle_id: int, prompt: str, model: str, reason: str = ""
    ) -> int:
        existing = self.conn.execute(
            "SELECT id FROM approval_queue WHERE item_id=? AND status='pending'",
            (item_id,),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = self.conn.execute(
            """INSERT INTO approval_queue
               (item_id, cycle_id, proposed_prompt, proposed_model, reason, status, created_utc)
               VALUES (?,?,?,?,?,?,?)""",
            (item_id, cycle_id, prompt, model, reason, "pending", _now()),
        )
        return cur.lastrowid

    def pending_approvals(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM approval_queue WHERE status='pending' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def approve(self, approval_id: int):
        self.conn.execute(
            "UPDATE approval_queue SET status='approved', resolved_utc=? WHERE id=?",
            (_now(), approval_id),
        )

    def reject_approval(self, approval_id: int):
        self.conn.execute(
            "UPDATE approval_queue SET status='rejected', resolved_utc=? WHERE id=?",
            (_now(), approval_id),
        )

    # ── resolutions (evidence-backed closure) ────────────────────────

    def record_resolution(
        self,
        *,
        item_id: str,
        verdict: str,
        evaluator: str,
        dispatch_id: Optional[int] = None,
        rationale: str = "",
        acceptance_criteria: str = "",
        source_refs: Optional[list] = None,
        evidence_hashes: Optional[dict] = None,
    ) -> int:
        """Record a PROPOSED resolution verdict with its evidence lineage.

        This never changes item status. A proposed resolution is a durable
        recommendation only; `authorize_resolution` (an explicit authority) is
        the single path that enacts the material state change.

        Idempotent per (item_id, dispatch_id): re-evaluating the same dispatch
        does not pile up duplicate proposals -- the existing proposal is returned.
        """
        if verdict not in RESOLUTION_VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}")
        existing = self.conn.execute(
            "SELECT id FROM resolutions WHERE item_id=? AND status='proposed' "
            "AND IFNULL(dispatch_id,-1)=IFNULL(?,-1)",
            (item_id, dispatch_id),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = self.conn.execute(
            """INSERT INTO resolutions
               (item_id, dispatch_id, verdict, evaluator, rationale,
                acceptance_criteria, source_refs, evidence_hashes, status, created_utc)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (item_id, dispatch_id, verdict, evaluator, rationale,
             acceptance_criteria,
             json.dumps(source_refs or [], default=str),
             json.dumps(evidence_hashes or {}, default=str),
             "proposed", _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_resolution(self, resolution_id: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM resolutions WHERE id=?", (resolution_id,)
        ).fetchone()
        return dict(row) if row else None

    def latest_resolution(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM resolutions WHERE item_id=? ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None

    def resolutions_for(self, item_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM resolutions WHERE item_id=? ORDER BY id DESC", (item_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def proposed_resolutions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM resolutions WHERE status='proposed' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def has_open_resolution(self, item_id: str, dispatch_id: Optional[int]) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM resolutions WHERE item_id=? AND status='proposed' "
            "AND IFNULL(dispatch_id,-1)=IFNULL(?,-1) LIMIT 1",
            (item_id, dispatch_id),
        ).fetchone()
        return row is not None

    def authorize_resolution(self, resolution_id: int, authority: str) -> dict:
        """Enact a proposed resolution. THE ONLY path that closes a task.

        `authority` is the designated authority's identity (required, non-empty).
        - resolved -> the task is marked completed (verified closure).
        - rework   -> a new authorized follow-up task is created; the original is
                      superseded.
        - review   -> no state change; the authority has acknowledged that the
                      evidence was insufficient, and the item stays
                      awaiting_resolution for another pass.
        """
        if not authority or not str(authority).strip():
            raise ValueError("authorize_resolution requires a non-empty authority")
        res = self.get_resolution(resolution_id)
        if not res:
            raise ValueError(f"no resolution {resolution_id}")
        if res["status"] != "proposed":
            raise ValueError(f"resolution {resolution_id} is {res['status']}, not proposed")

        ts = _now()
        self.conn.execute(
            "UPDATE resolutions SET status='authorized', authority=?, authorized_utc=? "
            "WHERE id=?",
            (authority, ts, resolution_id),
        )
        item_id = res["item_id"]
        outcome = {"resolution_id": resolution_id, "verdict": res["verdict"],
                   "item_id": item_id, "authority": authority}

        if res["verdict"] == "resolved":
            self.set_status(item_id, "completed")
            outcome["effect"] = "item completed"
        elif res["verdict"] == "rework":
            item = self.get_item(item_id) or {}
            n = len(self.resolutions_for(item_id))
            rework_id = f"{item_id}-rework-{n}"
            self.upsert_item(
                id=rework_id, kind="task", domain=item.get("domain", "backlog"),
                title=f"[rework] {item.get('title', item_id)}",
                description=(f"Rework of {item_id} ({res['rationale']}).\n\n"
                             f"{item.get('description', '')}"),
                evidence=item.get("evidence", ""), source="rework",
                source_ref=f"resolution:{resolution_id}",
                task_class=item.get("task_class", "investigate"),
                route=item.get("route", "luna"),
                priority=int(item.get("priority", 0) or 0), status="open",
            )
            self.set_status(item_id, "superseded")
            outcome["effect"] = f"created rework task {rework_id}; original superseded"
            outcome["rework_task"] = rework_id
        else:  # review
            outcome["effect"] = "insufficient evidence; left awaiting_resolution"

        self.conn.commit()
        return outcome

    def reject_resolution(self, resolution_id: int, authority: str) -> dict:
        if not authority or not str(authority).strip():
            raise ValueError("reject_resolution requires a non-empty authority")
        res = self.get_resolution(resolution_id)
        if not res:
            raise ValueError(f"no resolution {resolution_id}")
        if res["status"] != "proposed":
            raise ValueError(f"resolution {resolution_id} is {res['status']}, not proposed")
        self.conn.execute(
            "UPDATE resolutions SET status='rejected', authority=?, authorized_utc=? "
            "WHERE id=?",
            (authority, _now(), resolution_id),
        )
        self.conn.commit()
        return {"resolution_id": resolution_id, "status": "rejected", "authority": authority}

    # ── quarantine ───────────────────────────────────────────────────

    def quarantine_file(self, path: str, reason: str):
        ts = _now()
        self.conn.execute(
            """INSERT INTO quarantine (path, reason, first_seen_utc, last_seen_utc)
               VALUES (?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET reason=excluded.reason,
                   last_seen_utc=excluded.last_seen_utc""",
            (path, reason, ts, ts),
        )

    def quarantined(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM quarantine ORDER BY last_seen_utc DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── audit ────────────────────────────────────────────────────────

    def log_audit(self, cycle_id: int, stage: str,
                  input_data: dict = None, output_data: dict = None):
        self.conn.execute(
            "INSERT INTO audit (cycle_id, stage, input_json, output_json, timestamp_utc) VALUES (?,?,?,?,?)",
            (
                cycle_id,
                stage,
                json.dumps(input_data, default=str) if input_data else None,
                json.dumps(output_data, default=str) if output_data else None,
                _now(),
            ),
        )

    # ── lifecycle ────────────────────────────────────────────────────

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()
