"""Read-only data readers for the dashboard.

Each reader opens its source in read-only mode and returns plain dicts.
The model layer converts these to frozen view dataclasses.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ro_connect(db_path):
    path_str = str(db_path).replace("\\", "/")
    conn = sqlite3.connect("file:%s?mode=ro" % path_str, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _one(conn, sql, args=()):
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


class IntelligenceReader:
    """Read-only over intelligence.db — organizational state."""

    def __init__(self, db_path):
        self.conn = _ro_connect(db_path)

    def close(self):
        self.conn.close()

    def schema_version(self):
        row = _one(self.conn,
                    "SELECT value FROM meta WHERE key='schema_version'")
        return int(row["value"]) if row else 0

    def objectives(self):
        return _rows(self.conn,
                     "SELECT * FROM objectives ORDER BY created_utc")

    def coordinator_decisions(self):
        try:
            return _rows(self.conn,
                         "SELECT * FROM coordinator_decisions"
                         " ORDER BY created_utc")
        except sqlite3.OperationalError:
            return []

    def orchestration_runs(self):
        try:
            return _rows(self.conn,
                         "SELECT * FROM orchestration_runs"
                         " ORDER BY created_utc")
        except sqlite3.OperationalError:
            return []

    def orchestration_nodes(self, orchestration_id=None):
        try:
            if orchestration_id:
                return _rows(self.conn,
                             "SELECT * FROM orchestration_nodes"
                             " WHERE orchestration_id = ?"
                             " ORDER BY created_utc",
                             (orchestration_id,))
            return _rows(self.conn,
                         "SELECT * FROM orchestration_nodes"
                         " ORDER BY created_utc")
        except sqlite3.OperationalError:
            return []

    def orchestration_edges(self, orchestration_id=None):
        try:
            if orchestration_id:
                return _rows(self.conn,
                             "SELECT * FROM orchestration_edges"
                             " WHERE orchestration_id = ?"
                             " ORDER BY rowid",
                             (orchestration_id,))
            return _rows(self.conn,
                         "SELECT * FROM orchestration_edges ORDER BY rowid")
        except sqlite3.OperationalError:
            return []

    def authority_bindings(self):
        try:
            return _rows(self.conn,
                         "SELECT * FROM orchestration_authority_bindings"
                         " ORDER BY bound_utc")
        except sqlite3.OperationalError:
            return []

    def node_outputs(self):
        try:
            return _rows(self.conn,
                         "SELECT * FROM orchestration_node_outputs"
                         " ORDER BY accepted_utc")
        except sqlite3.OperationalError:
            return []

    def recent_assignments(self, limit=10):
        try:
            return _rows(self.conn,
                         "SELECT a.*, s.handle AS specialist_handle"
                         " FROM project_assignments a"
                         " JOIN specialists s"
                         "   ON s.specialist_id = a.specialist_id"
                         " ORDER BY a.selected_utc DESC LIMIT ?",
                         (limit,))
        except sqlite3.OperationalError:
            return []

    def taskforce_seats(self):
        try:
            return _rows(self.conn,
                         "SELECT ts.*, s.handle AS specialist_handle,"
                         " (SELECT COUNT(*) FROM capability_demonstrations d"
                         "  WHERE d.specialist_id = ts.specialist_id"
                         "  AND d.capability = ts.capability"
                         "  AND d.outcome = 'QUALIFYING') AS qualifying"
                         " FROM taskforce_seats ts"
                         " JOIN specialists s"
                         "   ON s.specialist_id = ts.specialist_id"
                         " ORDER BY ts.status, ts.capability")
        except sqlite3.OperationalError:
            return []

    def taskforce_member_count(self):
        try:
            row = _one(self.conn,
                       "SELECT COUNT(*) AS n FROM taskforce_memberships"
                       " WHERE status = 'ACTIVE'")
            return row["n"] if row else 0
        except sqlite3.OperationalError:
            return 0

    def recent_proposals(self, limit=10):
        return _rows(self.conn,
                     "SELECT proposal_id, class, objective_id, status,"
                     " origin_kind, created_utc, decided_utc"
                     " FROM proposals ORDER BY created_utc DESC LIMIT ?",
                     (limit,))

    def recent_cycles(self, limit=5):
        return _rows(self.conn,
                     "SELECT cycle_id, started_utc, status, note"
                     " FROM cycles ORDER BY cycle_id DESC LIMIT ?",
                     (limit,))


class AuthorityReader:
    """Read-only over autobot.db — authority state."""

    def __init__(self, db_path):
        self.conn = _ro_connect(db_path)

    def close(self):
        self.conn.close()

    def sequence_census(self):
        rows = _rows(self.conn,
                     "SELECT state, COUNT(*) AS n"
                     " FROM sequences GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    def attempt_census(self):
        rows = _rows(self.conn,
                     "SELECT state, COUNT(*) AS n"
                     " FROM attempts GROUP BY state")
        return {r["state"]: r["n"] for r in rows}

    def cas_object_count(self):
        row = _one(self.conn, "SELECT COUNT(*) AS n FROM objects")
        return row["n"] if row else 0

    def released_edge_count(self):
        row = _one(self.conn, "SELECT COUNT(*) AS n FROM released_edges")
        return row["n"] if row else 0

    def active_sequences(self):
        return _rows(self.conn,
                     "SELECT sequence_id, state, record_hash,"
                     " contract_digest, created_utc"
                     " FROM sequences"
                     " WHERE state NOT IN ('COMPLETED','TERMINAL')"
                     " ORDER BY created_utc")

    def running_attempts(self):
        try:
            return _rows(self.conn,
                         "SELECT attempt_id, sequence_id, state"
                         " FROM attempts"
                         " WHERE state = 'RUNNING'"
                         " ORDER BY rowid DESC")
        except sqlite3.OperationalError:
            return []

    def recent_transitions(self, limit=20):
        try:
            rows = _rows(self.conn,
                         "SELECT seq, entry_hash, payload, appended_utc"
                         " FROM transition_journal"
                         " ORDER BY seq DESC LIMIT ?",
                         (limit,))
            results = []
            for r in rows:
                payload = r.get("payload", "{}")
                try:
                    p = json.loads(payload)
                except (json.JSONDecodeError, ValueError):
                    p = {}
                results.append({
                    "seq": r["seq"],
                    "entry_hash": r["entry_hash"],
                    "machine": p.get("machine", "?"),
                    "subject_id": p.get("subject_id", "?"),
                    "from_state": p.get("from_state", "?"),
                    "to_state": p.get("to_state", "?"),
                    "rule": p.get("rule", ""),
                    "recorded_utc": p.get("recorded_utc",
                                          r.get("appended_utc", "")),
                })
            return results
        except sqlite3.OperationalError:
            return []


class StepLogReader:
    """Reads the driver's JSONL step log for real-time activity events."""

    def __init__(self, log_path):
        self.path = Path(log_path)
        self._offset = 0

    def new_entries(self):
        if not self.path.is_file():
            return []
        entries = []
        with open(str(self.path), "r", encoding="utf-8") as fh:
            fh.seek(self._offset)
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass
            self._offset = fh.tell()
        return entries
