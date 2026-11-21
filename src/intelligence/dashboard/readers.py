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
                         "SELECT a.*, s.handle AS specialist_handle,"
                         " s.provider AS spec_provider"
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


class ConfigReader:
    """Reads routing and specialist configuration from config.toml.

    This is the same data the Coordinator and driver use — the
    routing policy, specialist catalog, and model configuration."""

    def __init__(self, project_root):
        self.root = Path(project_root)
        self._config = None

    def _load(self):
        if self._config is not None:
            return self._config
        config_path = self.root / "config.toml"
        local_path = self.root / "config.local.toml"
        config = {}
        for path in (config_path, local_path):
            if path.is_file():
                try:
                    import tomli
                    with open(str(path), "rb") as f:
                        layer = tomli.load(f)
                except ImportError:
                    layer = _parse_toml_fallback(str(path))
                except Exception:
                    layer = _parse_toml_fallback(str(path))
                for k, v in layer.items():
                    if isinstance(v, dict) and isinstance(
                            config.get(k), dict):
                        config[k].update(v)
                    else:
                        config[k] = v
        self._config = config
        return config

    def default_route(self):
        c = self._load()
        return c.get("routing", {}).get("default", "?")

    def escalation_route(self):
        c = self._load()
        return c.get("routing", {}).get("escalation", "?")

    def planner_model(self):
        c = self._load()
        return c.get("models", {}).get("planner", "?")

    def routes(self):
        c = self._load()
        result = {}
        for section_key in ("routing.routes", "routes"):
            sect = c.get("routing", {}).get("routes", {})
            if not sect and section_key == "routes":
                sect = c.get("routes", {})
            for name, spec in sect.items():
                if isinstance(spec, dict):
                    result[name] = {
                        "agent": spec.get("agent", "?"),
                        "model": spec.get("model", "?"),
                        "effort": spec.get("effort"),
                    }
        return result

    def specialists(self):
        c = self._load()
        result = {}
        for section in (c.get("specialists", {}),
                        c.get("taskforce", {}).get("specialists", {})):
            if isinstance(section, dict):
                for handle, spec in section.items():
                    if isinstance(spec, dict):
                        result[handle] = {
                            "offers": spec.get("offers", []),
                        }
        return result


def _parse_toml_fallback(path):
    """Minimal TOML key=value parser for when tomli is unavailable."""
    result = {}
    current_section = result
    section_path = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    key = line.strip("[]").strip()
                    parts = key.split(".")
                    current_section = result
                    for p in parts:
                        current_section = current_section.setdefault(p, {})
                    section_path = parts
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if v.lower() in ("true", "false"):
                        v = v.lower() == "true"
                    elif v.isdigit():
                        v = int(v)
                    current_section[k] = v
    except Exception:
        pass
    return result


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
