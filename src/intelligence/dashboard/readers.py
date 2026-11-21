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

    def assignments_by_sequence(self):
        try:
            rows = _rows(self.conn,
                         "SELECT a.sequence_id, a.provider, a.model,"
                         " s.handle AS specialist_handle"
                         " FROM project_assignments a"
                         " JOIN specialists s"
                         "   ON s.specialist_id = a.specialist_id"
                         " ORDER BY a.selected_utc DESC")
            index = {}
            for r in rows:
                sid = r.get("sequence_id")
                if sid and sid not in index:
                    index[sid] = r
            return index
        except sqlite3.OperationalError:
            return {}

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


class CASReader:
    """Read-only over the content-addressed store."""

    def __init__(self, store_root):
        self.root = Path(store_root)

    def object_count(self):
        if not self.root.is_dir():
            return 0
        count = 0
        for d in self.root.iterdir():
            if d.is_dir() and len(d.name) == 2:
                count += sum(1 for f in d.iterdir() if f.is_file())
        return count

    def exists(self, address):
        prefix = address.replace("sha256:", "")
        if len(prefix) < 3:
            return False
        path = self.root / prefix[:2] / prefix
        return path.is_file()

    def size(self, address):
        prefix = address.replace("sha256:", "")
        if len(prefix) < 3:
            return 0
        path = self.root / prefix[:2] / prefix
        try:
            return path.stat().st_size
        except (OSError, FileNotFoundError):
            return 0


class AutoSwarmReader:
    """Read-only over AutoSwarm call directories.

    Reads $AUTOSWARM_HOME/calls/<call_id>/ for active session state:
    handle.json (launched), result.json (finished), stdout.log."""

    def __init__(self, autoswarm_home):
        self.home = Path(autoswarm_home) if autoswarm_home else None
        self._calls_dir = None
        if self.home and (self.home / "calls").is_dir():
            self._calls_dir = self.home / "calls"

    def active_sessions(self):
        """Find call directories with handle.json but no result.json."""
        if not self._calls_dir:
            return []
        sessions = []
        try:
            for d in sorted(self._calls_dir.iterdir(), reverse=True):
                if not d.is_dir():
                    continue
                handle = d / "handle.json"
                result = d / "result.json"
                if handle.is_file() and not result.is_file():
                    entry = {"call_id": d.name}
                    try:
                        h = json.loads(handle.read_text(encoding="utf-8"))
                        entry.update({
                            "native_session_id": h.get("native_session_id"),
                            "provider": h.get("provider"),
                            "model": h.get("model"),
                            "started_utc": h.get("started_utc"),
                            "workspace": h.get("workspace"),
                        })
                    except (json.JSONDecodeError, OSError):
                        pass
                    # Tail stdout for live execution detail
                    stdout = d / "stdout.log"
                    if stdout.is_file():
                        try:
                            text = stdout.read_text(
                                encoding="utf-8", errors="replace")
                            lines = text.strip().split("\n")
                            entry["stdout_lines"] = len(lines)
                            entry["stdout_tail"] = lines[-5:]
                        except OSError:
                            pass
                    sessions.append(entry)
                if len(sessions) >= 10:
                    break
        except OSError:
            pass
        return sessions

    def recent_completed(self, limit=5):
        """Recent calls that have result.json."""
        if not self._calls_dir:
            return []
        completed = []
        try:
            dirs = sorted(self._calls_dir.iterdir(),
                          key=lambda d: d.stat().st_mtime
                          if d.is_dir() else 0, reverse=True)
            for d in dirs:
                if not d.is_dir():
                    continue
                result_path = d / "result.json"
                if result_path.is_file():
                    try:
                        r = json.loads(
                            result_path.read_text(encoding="utf-8"))
                        completed.append({
                            "call_id": d.name,
                            "native_session_id": r.get(
                                "native_session_id"),
                            "outcome": r.get("outcome"),
                            "provider": r.get("provider"),
                        })
                    except (json.JSONDecodeError, OSError):
                        completed.append({"call_id": d.name})
                    if len(completed) >= limit:
                        break
        except OSError:
            pass
        return completed

    def call_state(self, call_id):
        """State of one call: handle, result, stdout tail."""
        if not self._calls_dir:
            return None
        d = self._calls_dir / call_id
        if not d.is_dir():
            return None
        state = {"call_id": call_id}
        handle = d / "handle.json"
        result = d / "result.json"
        if handle.is_file():
            try:
                state["handle"] = json.loads(
                    handle.read_text(encoding="utf-8"))
            except Exception:
                pass
        if result.is_file():
            try:
                state["result"] = json.loads(
                    result.read_text(encoding="utf-8"))
            except Exception:
                pass
            state["status"] = "FINISHED"
        else:
            state["status"] = "RUNNING"
        stdout = d / "stdout.log"
        if stdout.is_file():
            try:
                text = stdout.read_text(encoding="utf-8",
                                         errors="replace")
                lines = text.strip().split("\n")
                state["stdout_lines"] = len(lines)
                state["stdout_tail"] = lines[-3:] if lines else []
            except OSError:
                pass
        return state


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
