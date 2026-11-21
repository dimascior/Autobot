"""Read-only data readers for the dashboard.

Each reader opens its source in read-only mode and returns plain dicts.
The model layer converts these to frozen view dataclasses.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from autobot.models import ATTEMPT_TERMINAL, SEQUENCE_TERMINAL
except ImportError:  # the readers still work without the authority package
    SEQUENCE_TERMINAL = frozenset((
        "COMPLETED", "SUPERSEDED", "ABANDONED", "DELEGATION_REVOKED",
        "PUBLISHED_UNVERIFIED"))
    ATTEMPT_TERMINAL = frozenset((
        "VERIFIED", "REJECTED", "FAILED", "STALE", "FENCED", "RECONCILED"))

# Assignment statuses in which a seat holder is still working on something.
_ASSIGNMENT_IN_FLIGHT = ("SELECTED", "DISPATCHED", "EXECUTING", "COLLECTED")


def _placeholders(values):
    return ",".join("?" * len(values))


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
                         " s.provider AS spec_provider,"
                         " w.workstream_key AS workstream_key"
                         " FROM project_assignments a"
                         " JOIN specialists s"
                         "   ON s.specialist_id = a.specialist_id"
                         " LEFT JOIN project_workstreams w"
                         "   ON w.workstream_id = a.workstream_id"
                         " ORDER BY a.selected_utc DESC LIMIT ?",
                         (limit,))
        except sqlite3.OperationalError:
            return []

    def taskforce_seats(self):
        """One row per seat with its demonstrations split by whether they
        were recorded through the seat, and the time of the retention
        decision behind its current status (ESTABLISH or RETAIN)."""
        demo = ("(SELECT COUNT(*) FROM capability_demonstrations d"
                " WHERE %s AND d.outcome = '%s')")
        via_seat = "d.seat_id = ts.seat_id"
        without_seat = ("d.specialist_id = ts.specialist_id"
                        " AND d.capability = ts.capability"
                        " AND d.role = ts.role"
                        " AND (d.seat_id IS NULL OR d.seat_id != ts.seat_id)")
        try:
            return _rows(self.conn,
                         "SELECT ts.*, s.handle AS specialist_handle,"
                         " s.provider AS spec_provider,"
                         " s.model AS spec_model,"
                         " s.route AS spec_route,"
                         " " + demo % (via_seat, "QUALIFYING") + " AS seat_q,"
                         " " + demo % (via_seat, "NOT_QUALIFYING")
                         + " AS seat_nq,"
                         " " + demo % (without_seat, "QUALIFYING")
                         + " AS other_q,"
                         " " + demo % (without_seat, "NOT_QUALIFYING")
                         + " AS other_nq,"
                         " (SELECT MAX(r.decided_utc) FROM retention_decisions r"
                         "  WHERE r.specialist_id = ts.specialist_id"
                         "  AND r.capability = ts.capability"
                         "  AND r.role = ts.role"
                         "  AND r.kind = CASE ts.status"
                         "    WHEN 'ESTABLISHED' THEN 'ESTABLISH'"
                         "    WHEN 'RETAINED' THEN 'RETAIN'"
                         "    ELSE r.kind END) AS since_utc"
                         " FROM taskforce_seats ts"
                         " JOIN specialists s"
                         "   ON s.specialist_id = ts.specialist_id"
                         " ORDER BY ts.status, ts.capability")
        except sqlite3.OperationalError:
            # an older plan store: fall back to the capability-wide count
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

    def seat_work(self):
        """Assignments a seat holder is still working on, by seat."""
        try:
            return _rows(self.conn,
                         "SELECT a.seat_id, a.sequence_id, a.status,"
                         " a.selected_utc, w.workstream_key"
                         " FROM project_assignments a"
                         " LEFT JOIN project_workstreams w"
                         "   ON w.workstream_id = a.workstream_id"
                         " WHERE a.seat_id IS NOT NULL AND a.status IN (%s)"
                         " ORDER BY a.selected_utc"
                         % _placeholders(_ASSIGNMENT_IN_FLIGHT),
                         _ASSIGNMENT_IN_FLIGHT)
        except sqlite3.OperationalError:
            return []

    def last_assignment_routes(self):
        """specialist_id -> the route that specialist's newest assignment
        ran on: provider and model, and the route's name and effort when
        the assignment recorded them (execution.route)."""
        try:
            rows = _rows(self.conn,
                         "SELECT a.specialist_id, a.provider, a.model,"
                         " a.execution, a.selected_utc"
                         " FROM project_assignments a"
                         " WHERE a.selected_utc = (SELECT MAX(b.selected_utc)"
                         "  FROM project_assignments b"
                         "  WHERE b.specialist_id = a.specialist_id)")
        except sqlite3.OperationalError:
            return {}
        out = {}
        for r in rows:
            try:
                execution = json.loads(r.get("execution") or "{}")
            except (ValueError, TypeError):
                execution = {}
            route = execution.get("route") if isinstance(execution, dict) \
                else None
            route = route if isinstance(route, dict) else {}
            out[r["specialist_id"]] = {
                "provider": r.get("provider"), "model": r.get("model"),
                "route_name": route.get("name"),
                "effort": route.get("effort"),
                "selected_utc": r.get("selected_utc")}
        return out

    def demonstrations_by_model(self):
        """Demonstrations per specialist, by the provider and model of the
        assignment that produced them."""
        try:
            return _rows(self.conn,
                         "SELECT d.specialist_id, a.provider, a.model,"
                         " d.outcome, COUNT(*) AS n,"
                         " MAX(d.recorded_utc) AS last_utc"
                         " FROM capability_demonstrations d"
                         " LEFT JOIN project_assignments a"
                         "   ON a.assignment_id = d.assignment_id"
                         " GROUP BY d.specialist_id, a.provider, a.model,"
                         " d.outcome")
        except sqlite3.OperationalError:
            return []

    def specialists(self):
        """The specialist catalog with Taskforce membership."""
        try:
            return _rows(self.conn,
                         "SELECT s.specialist_id, s.handle, s.provider,"
                         " s.model, s.status, s.route,"
                         " EXISTS (SELECT 1 FROM taskforce_memberships m"
                         "  WHERE m.specialist_id = s.specialist_id"
                         "  AND m.status = 'ACTIVE') AS member"
                         " FROM specialists s ORDER BY s.handle")
        except sqlite3.OperationalError:
            return []

    def active_project_teams(self):
        try:
            return _rows(self.conn,
                         "SELECT project_id, specialist_id, joined_as"
                         " FROM project_team WHERE status = 'ACTIVE'")
        except sqlite3.OperationalError:
            return []

    def projects(self):
        try:
            return _rows(self.conn,
                         "SELECT project_id, objective_id, title, status,"
                         " opened_utc, closed_utc FROM projects"
                         " ORDER BY opened_utc")
        except sqlite3.OperationalError:
            return []

    def workstreams(self):
        """Every project workstream with the sequence its proposal opened."""
        try:
            return _rows(self.conn,
                         "SELECT w.workstream_id, w.project_id,"
                         " w.workstream_key, w.created_utc,"
                         " p.sequence_id, p.class AS proposal_class"
                         " FROM project_workstreams w"
                         " LEFT JOIN proposals p"
                         "   ON p.proposal_id = w.proposal_id"
                         " ORDER BY w.created_utc, w.rowid")
        except sqlite3.OperationalError:
            return []

    # --- the live fields of the agent view -------------------------------

    def driver_lease(self):
        """The driver's lease. ``acquired_utc`` is renewed on every driver
        step, so it is the driver's heartbeat."""
        try:
            return _one(self.conn,
                        "SELECT holder, acquired_utc, expires_utc"
                        " FROM driver_lease WHERE lease_key = 'driver'")
        except sqlite3.OperationalError:
            return None

    def cycles_recent(self, limit=3):
        """The newest Engineer cycles and how many proposals each filed."""
        try:
            return _rows(self.conn,
                         "SELECT c.cycle_id, c.started_utc, c.status,"
                         " c.closed_utc, c.note,"
                         " (SELECT COUNT(*) FROM proposals p"
                         "  WHERE p.cycle_id = c.cycle_id) AS proposals"
                         " FROM cycles c ORDER BY c.cycle_id DESC LIMIT ?",
                         (limit,))
        except sqlite3.OperationalError:
            return []

    def open_proposals(self):
        """Proposals waiting on you (QUEUED_FOR_YOU) or on authority
        (WAITING)."""
        try:
            return _rows(self.conn,
                         "SELECT proposal_id, cycle_id, class, objective_id,"
                         " status, rationale, sequence_id, created_utc"
                         " FROM proposals"
                         " WHERE status IN ('WAITING', 'QUEUED_FOR_YOU')"
                         " ORDER BY created_utc")
        except sqlite3.OperationalError:
            return []

    def open_staffing(self):
        """The Project Manager's staffing decisions not yet applied."""
        try:
            return _rows(self.conn,
                         "SELECT d.decision_id, d.objective_id, d.project_id,"
                         " d.sequence_id, d.attempt_ordinal, d.mode,"
                         " d.decided_utc, s.handle AS specialist_handle"
                         " FROM assignment_decisions d"
                         " LEFT JOIN specialists s"
                         "   ON s.specialist_id = d.specialist_id"
                         " WHERE d.status = 'OPEN' ORDER BY d.decided_utc")
        except sqlite3.OperationalError:
            return []

    def engineering_decisions(self):
        """The Engineer's reasoning bindings; content_sha256 names the
        decision text the driver hashed."""
        try:
            return _rows(self.conn,
                         "SELECT engineering_request_id, orchestration_id,"
                         " engineering_decision_id, content_sha256,"
                         " created_utc, adopted_utc"
                         " FROM orchestration_engineering_bindings"
                         " ORDER BY created_utc")
        except sqlite3.OperationalError:
            return []

    def planning_events_for(self, journal_seqs):
        """Planning events keyed by the journal seq they were derived
        from."""
        seqs = [s for s in journal_seqs if s is not None]
        if not seqs:
            return {}
        try:
            rows = _rows(self.conn,
                         "SELECT journal_seq, event_kind, facts, recorded_utc"
                         " FROM planning_events WHERE journal_seq IN (%s)"
                         % _placeholders(seqs), tuple(seqs))
        except sqlite3.OperationalError:
            return {}
        return {r["journal_seq"]: r for r in rows}

    def workforce_events_recent(self, limit=10):
        try:
            return _rows(self.conn,
                         "SELECT event_id, event_kind, subject_id, facts,"
                         " recorded_utc FROM workforce_events"
                         " ORDER BY event_id DESC LIMIT ?", (limit,))
        except sqlite3.OperationalError:
            return []

    def approvals_recent(self, limit=5):
        try:
            return _rows(self.conn,
                         "SELECT call_id, tool_name, subject, decision,"
                         " decided_utc FROM assignment_approvals"
                         " ORDER BY decided_utc DESC LIMIT ?", (limit,))
        except sqlite3.OperationalError:
            return []

    def _census(self, sql):
        try:
            return {r["k"]: r["n"] for r in _rows(self.conn, sql)}
        except sqlite3.OperationalError:
            return {}

    def approval_census(self):
        return self._census("SELECT decision AS k, COUNT(*) AS n"
                            " FROM assignment_approvals GROUP BY decision")

    def demonstration_census(self):
        return self._census("SELECT outcome AS k, COUNT(*) AS n"
                            " FROM capability_demonstrations GROUP BY outcome")

    def findings_census(self):
        return self._census("SELECT significance AS k, COUNT(*) AS n"
                            " FROM findings GROUP BY significance")

    def budgeted_objectives(self):
        try:
            return {r["objective_id"] for r in _rows(
                self.conn, "SELECT objective_id FROM objective_budgets")}
        except sqlite3.OperationalError:
            return set()

    def assignments_by_attempt(self):
        """Assignment facts keyed by attempt id: who was staffed, how, on
        which workstream."""
        try:
            rows = _rows(self.conn,
                         "SELECT a.attempt_id, a.sequence_id, a.call_id,"
                         " a.objective_id, a.capability, a.role,"
                         " a.provider, a.model, a.selection_path,"
                         " s.handle AS specialist_handle,"
                         " w.workstream_key"
                         " FROM project_assignments a"
                         " JOIN specialists s"
                         "   ON s.specialist_id = a.specialist_id"
                         " LEFT JOIN project_workstreams w"
                         "   ON w.workstream_id = a.workstream_id"
                         " WHERE a.attempt_id IS NOT NULL")
        except sqlite3.OperationalError:
            return {}
        return {r["attempt_id"]: r for r in rows}

    def taskforce_member_count(self):
        try:
            row = _one(self.conn,
                       "SELECT COUNT(*) AS n FROM taskforce_memberships"
                       " WHERE status = 'ACTIVE'")
            return row["n"] if row else 0
        except sqlite3.OperationalError:
            return 0

    def assignments_by_sequence(self):
        """The newest assignment per sequence: who holds or held it, how
        they were staffed, and how it ended."""
        try:
            rows = _rows(self.conn,
                         "SELECT a.sequence_id, a.provider, a.model,"
                         " a.status, a.outcome, a.selection_path,"
                         " a.selected_utc, a.settled_utc, a.call_id,"
                         " a.attempt_id, s.handle AS specialist_handle"
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
        terminal = tuple(sorted(SEQUENCE_TERMINAL))
        return _rows(self.conn,
                     "SELECT sequence_id, state, record_hash,"
                     " contract_digest, created_utc"
                     " FROM sequences"
                     " WHERE state NOT IN (%s)"
                     " ORDER BY created_utc" % _placeholders(terminal),
                     terminal)

    def sequence_states(self):
        """sequence_id -> {state, updated_utc}; updated_utc is when the
        sequence entered its current state."""
        rows = _rows(self.conn,
                     "SELECT sequence_id, state, updated_utc FROM sequences")
        return {r["sequence_id"]: r for r in rows}

    def in_flight_attempts(self):
        """Every attempt not yet in a terminal state, with its execution
        call when one is bound (schema v3+)."""
        terminal = tuple(sorted(ATTEMPT_TERMINAL))
        try:
            return _rows(self.conn,
                         "SELECT a.attempt_id, a.sequence_id, a.state,"
                         " a.created_utc, a.updated_utc, c.call_id,"
                         " c.provider, c.role, c.native_session_id"
                         " FROM attempts a"
                         " LEFT JOIN attempt_calls c"
                         "   ON c.attempt_id = a.attempt_id"
                         " WHERE a.state NOT IN (%s)"
                         " ORDER BY a.created_utc" % _placeholders(terminal),
                         terminal)
        except sqlite3.OperationalError:
            return _rows(self.conn,
                         "SELECT attempt_id, sequence_id, state,"
                         " created_utc, updated_utc FROM attempts"
                         " WHERE state NOT IN (%s)"
                         " ORDER BY created_utc" % _placeholders(terminal),
                         terminal)

    def attempt_sequences(self):
        """attempt_id -> sequence_id, for naming journal subjects."""
        return {r["attempt_id"]: r["sequence_id"] for r in _rows(
            self.conn, "SELECT attempt_id, sequence_id FROM attempts")}

    # --- the live fields of the agent view -------------------------------

    def instance_lease(self):
        try:
            return _one(self.conn,
                        "SELECT holder, epoch, acquired_utc, expires_utc"
                        " FROM instance_lease WHERE lease_key = 'sequencer'")
        except sqlite3.OperationalError:
            return None

    def journal_head(self):
        try:
            return _one(self.conn,
                        "SELECT seq, appended_utc FROM transition_journal"
                        " ORDER BY seq DESC LIMIT 1")
        except sqlite3.OperationalError:
            return None

    def _census(self, sql):
        try:
            return {r["k"]: r["n"] for r in _rows(self.conn, sql)}
        except sqlite3.OperationalError:
            return {}

    def receipt_census(self):
        return self._census("SELECT completeness AS k, COUNT(*) AS n"
                            " FROM attempt_receipts GROUP BY completeness")

    def verification_census(self):
        return self._census("SELECT outcome AS k, COUNT(*) AS n"
                            " FROM attempt_verifications GROUP BY outcome")

    def effect_census(self):
        return self._census("SELECT state AS k, COUNT(*) AS n"
                            " FROM effect_intents GROUP BY state")

    def witness_count(self):
        try:
            row = _one(self.conn, "SELECT COUNT(*) AS n FROM attempt_witnesses")
        except sqlite3.OperationalError:
            return 0
        return row["n"] if row else 0

    def integrity_failures(self):
        """Attempts whose execution receipt the collector classified
        INTEGRITY_FAILED."""
        try:
            return _rows(self.conn,
                         "SELECT r.attempt_id, a.sequence_id, r.admitted_utc"
                         " FROM attempt_receipts r"
                         " JOIN attempts a ON a.attempt_id = r.attempt_id"
                         " WHERE r.completeness = 'INTEGRITY_FAILED'"
                         " ORDER BY r.admitted_utc")
        except sqlite3.OperationalError:
            return []

    def sequence_index(self):
        """Every sequence's identities and its frozen record's CAS address,
        for resolving the record's predecessor edges."""
        try:
            return _rows(self.conn,
                         "SELECT s.sequence_id, s.state, s.record_hash,"
                         " s.contract_digest, s.requirement_digest,"
                         " r.content_address"
                         " FROM sequences s LEFT JOIN records r"
                         "   ON r.record_hash = s.record_hash")
        except sqlite3.OperationalError:
            return []

    def released_edges(self):
        try:
            return _rows(self.conn,
                         "SELECT predecessor_sequence_id, edge_kind,"
                         " edge_value, released_utc FROM released_edges")
        except sqlite3.OperationalError:
            return []

    def halt_facts(self, sequence_id, updated_utc=None):
        """Why a sequence halted: the rule and facts of the journal row that
        moved it to HALTED. Cached per (sequence, time it entered its
        state), so each halt is looked up once."""
        cache = self.__dict__.setdefault("_halt_cache", {})
        key = (sequence_id, updated_utc)
        if key in cache:
            return cache[key]
        found = None
        try:
            rows = _rows(self.conn,
                         "SELECT seq, payload FROM transition_journal"
                         " WHERE payload LIKE ? ORDER BY seq DESC LIMIT 40",
                         ("%" + sequence_id + "%",))
        except sqlite3.OperationalError:
            rows = []
        for r in rows:
            try:
                p = json.loads(r.get("payload") or "{}")
            except (ValueError, TypeError):
                continue
            if (p.get("machine") == "sequence"
                    and p.get("subject_id") == sequence_id
                    and str(p.get("to_state", "")).startswith("HALTED")):
                facts = p.get("facts") if isinstance(p.get("facts"),
                                                     dict) else {}
                found = {"rule": p.get("rule"),
                         "reason": facts.get("reason"),
                         "completeness": facts.get("completeness"),
                         "recorded_utc": p.get("recorded_utc")}
                break
        cache[key] = found
        return found

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
                facts = p.get("facts")
                results.append({
                    "seq": r["seq"],
                    "entry_hash": r["entry_hash"],
                    "machine": p.get("machine", "?"),
                    "subject_id": p.get("subject_id", "?"),
                    # absent on a record's admission (D-37): None, not "?"
                    "from_state": p.get("from_state"),
                    "to_state": p.get("to_state", "?"),
                    "rule": p.get("rule", ""),
                    "recorded_utc": p.get("recorded_utc",
                                          r.get("appended_utc", "")),
                    "facts": facts if isinstance(facts, dict) else {},
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

    def read_json(self, address):
        """A CAS object parsed as JSON, or None. Objects are immutable, so
        each address is read from disk once."""
        cache = self.__dict__.setdefault("_json_cache", {})
        if not address:
            return None
        if address in cache:
            return cache[address]
        prefix = address.replace("sha256:", "")
        value = None
        if len(prefix) >= 3:
            try:
                with open(str(self.root / prefix[:2] / prefix), "rb") as fh:
                    value = json.loads(fh.read().decode("utf-8"))
            except (OSError, ValueError):
                value = None
        cache[address] = value
        return value


class AutoSwarmReader:
    """Read-only over AutoSwarm call directories.

    Reads $AUTOSWARM_HOME/calls/<call_id>/ for active session state:
    handle.json (launched), result.json (finished), stdout.log."""

    def __init__(self, autoswarm_home):
        self.home = Path(autoswarm_home) if autoswarm_home else None
        self._calls_dir = None
        if self.home and (self.home / "calls").is_dir():
            self._calls_dir = self.home / "calls"

    @staticmethod
    def _read_json(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    @staticmethod
    def _stdout_tail(path, max_bytes=65536):
        """(line_count, last lines) of a log, reading at most ``max_bytes``
        from its end; an empty log is 0 lines."""
        try:
            size = path.stat().st_size
            with open(str(path), "rb") as fh:
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                data = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return 0, []
        text = data.strip()
        if not text:
            return 0, []
        lines = text.split("\n")
        return len(lines), lines[-5:]

    def call_files(self, call_id):
        """What AutoSwarm recorded for one call: the runner's handle.json
        (pid, started_at, phase, native session), the ExecutionManifest's
        route and constraints, and whether result.json exists."""
        if not self._calls_dir or not call_id:
            return {}
        d = self._calls_dir / call_id
        if not d.is_dir():
            return {}
        handle = self._read_json(d / "handle.json")
        manifest = self._read_json(d / "manifest.json")
        assignment = manifest.get("provider_assignment") or {}
        constraints = manifest.get("constraints") or {}
        output_lines, tail, output_utc = 0, [], None
        stdout = d / "stdout.log"
        if stdout.is_file():
            output_lines, tail = self._stdout_tail(stdout)
            try:
                from datetime import datetime, timezone
                output_utc = datetime.fromtimestamp(
                    stdout.stat().st_mtime, timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ") if output_lines else None
            except OSError:
                output_utc = None
        return {
            "output_lines": output_lines,
            "last_line": tail[-1] if tail else None,
            "output_utc": output_utc,
            "pid": handle.get("pid"),
            "started_utc": handle.get("started_at"),
            "phase": handle.get("phase"),
            "native_session_id": handle.get("native_session_id"),
            "provider": assignment.get("provider"),
            "model": assignment.get("model"),
            "sequence_id": manifest.get("sequence_id"),
            "attempt_id": manifest.get("attempt_id"),
            "finalization_class": constraints.get("finalization_class"),
            "source_revision": constraints.get("source_revision"),
            "finished": (d / "result.json").is_file(),
        }

    def active_sessions(self):
        """Call directories with handle.json but no result.json, newest
        first. handle.json holds pid/started_at/phase/native_session_id;
        the route lives in manifest.json."""
        if not self._calls_dir:
            return []
        def mtime(d):
            try:
                return d.stat().st_mtime
            except OSError:
                return 0

        sessions = []
        try:
            dirs = [d for d in self._calls_dir.iterdir() if d.is_dir()]
            dirs.sort(key=mtime, reverse=True)
            for d in dirs:
                handle = d / "handle.json"
                result = d / "result.json"
                if handle.is_file() and not result.is_file():
                    entry = {"call_id": d.name}
                    files = self.call_files(d.name)
                    entry.update({
                        "native_session_id": files.get("native_session_id"),
                        "provider": files.get("provider"),
                        "model": files.get("model"),
                        "started_utc": files.get("started_utc"),
                        "workspace": None,
                        "sequence_id": files.get("sequence_id"),
                    })
                    stdout = d / "stdout.log"
                    if stdout.is_file():
                        count, tail = self._stdout_tail(stdout)
                        entry["stdout_lines"] = count
                        entry["stdout_tail"] = tail
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
