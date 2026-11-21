"""The Engineer's own durable reasoning store (NOT authority).

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

import json
import sqlite3
from contextlib import contextmanager

from .proposals_util import canonical_json_digest

PLAN_SCHEMA_VERSION = 4

# The objective-scoped engineering-work budget applied when you set none
# (cap-47: the limits are yours; these are only the conservative default).
DEFAULT_BUDGET = {
    "max_contracts": 12,
    "max_attempts": 24,
    "max_outside_specialists": 6,
    "max_repairs": 6,
    "max_retention_decisions": 12,
}

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
    # One Engineer cycle per authoritative change batch; the cycle
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
    # Typed Engineer proposals (cap-13). proposal_id is the
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
    # only a Engineer proposal turns one into a contract.
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
    # Execution slots: one row per allocated (or authorized) execution of
    # a sequence — the per-attempt dispatch bookkeeping. (The table keeps
    # its v1 name ``seats``; the ORGANIZATIONAL seat a specialist holds in
    # the Taskforce is ``taskforce_seats``, schema v2.) Provider/model
    # live HERE and in the workforce tables, never in the frozen
    # contract, so the organization can change models without changing
    # authority identity.
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


# Schema v2 (additive, monotonic — every v1 row is preserved): the
# PROJECT model (the Engineer's temporary undertakings and their teams),
# the TASKFORCE model (the persistent retained organization) and the
# Engineer's reasoning provenance. Two things stay separate in every table
# below: taking part in a project (projects, project_workstreams,
# project_team, assignment_decisions, project_assignments) and belonging
# to the Taskforce (taskforce_memberships, taskforce_seats). All of it is
# planning/workforce state, never authority: each row either describes the
# organization (who exists, who is a member, who holds a seat, who worked
# on what, how they performed) or REFERENCES authority objects by their
# existing identities (sequence_id, attempt_id, call_id, record_hash,
# receipt content addresses) without copying them.
_DDL_V2 = (
    # A specialist: one stable engineering-worker identity, independent of
    # any single execution. provider/model/route are IMPLEMENTATION
    # attributes of the worker and may be replaced without changing the
    # identity, any seat it holds, or any contract it is assigned to.
    """CREATE TABLE IF NOT EXISTS specialists (
        specialist_id TEXT PRIMARY KEY,
        handle TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL,
        model TEXT,
        route TEXT NOT NULL,
        offers TEXT NOT NULL,
        origin TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','WITHDRAWN')),
        first_seen_utc TEXT NOT NULL,
        updated_utc TEXT NOT NULL
    )""",
    # TASKFORCE MEMBERSHIP: one specialist's persistent membership in the
    # engineering organization. A row exists only because an explicit,
    # evidence-bound retention decision created it; executing project work
    # never does. A specialist with no row is OUTSIDE the Taskforce.
    """CREATE TABLE IF NOT EXISTS taskforce_memberships (
        membership_id TEXT PRIMARY KEY,
        specialist_id TEXT NOT NULL UNIQUE
            REFERENCES specialists(specialist_id),
        status TEXT NOT NULL CHECK (status IN
            ('ACTIVE','INACTIVE','RELEASED')),
        joined_utc TEXT NOT NULL,
        joined_by_decision TEXT NOT NULL,
        updated_utc TEXT NOT NULL,
        ended_utc TEXT
    )""",
    # TASKFORCE SEAT: one retained organizational (capability, role) a
    # member holds. Created by the retention decision, never by an
    # assignment.
    """CREATE TABLE IF NOT EXISTS taskforce_seats (
        seat_id TEXT PRIMARY KEY,
        membership_id TEXT NOT NULL
            REFERENCES taskforce_memberships(membership_id),
        specialist_id TEXT NOT NULL REFERENCES specialists(specialist_id),
        capability TEXT NOT NULL,
        role TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN
            ('RETAINED','ESTABLISHED','INACTIVE','RELEASED')),
        created_utc TEXT NOT NULL,
        retained_since_utc TEXT,
        last_assignment_id TEXT,
        updated_utc TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_seat_identity
        ON taskforce_seats(specialist_id, capability, role)""",
    """CREATE INDEX IF NOT EXISTS idx_seat_capability
        ON taskforce_seats(capability, role, status)""",
    # Every seat transition, append-only, with what caused it.
    """CREATE TABLE IF NOT EXISTS seat_history (
        history_id INTEGER PRIMARY KEY AUTOINCREMENT,
        seat_id TEXT NOT NULL REFERENCES taskforce_seats(seat_id),
        from_status TEXT NOT NULL,
        to_status TEXT NOT NULL,
        cause TEXT NOT NULL,
        decision_id TEXT,
        demonstration_id TEXT,
        assignment_id TEXT,
        recorded_utc TEXT NOT NULL
    )""",
    # PROJECT: a temporary coordinated engineering undertaking the
    # Engineer opens, as Project Manager, to realize its solution to one
    # objective. At most one is ACTIVE per objective.
    """CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        objective_id TEXT NOT NULL REFERENCES objectives(objective_id),
        ordinal INTEGER NOT NULL,
        title TEXT NOT NULL,
        solution TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('ACTIVE','COMPLETED','CLOSED')),
        opened_by TEXT NOT NULL CHECK (opened_by IN
            ('PROJECT_MANAGER','IMPLICIT')),
        opened_by_proposal TEXT,
        opened_utc TEXT NOT NULL,
        closed_utc TEXT,
        closed_by TEXT
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_project_ordinal
        ON projects(objective_id, ordinal)""",
    # PROJECT WORKSTREAM: one piece of the project's work graph. It
    # REFERENCES the work-creation proposal (and through it the frozen
    # TaskContractRecord and its sequence); it copies no authority state.
    """CREATE TABLE IF NOT EXISTS project_workstreams (
        workstream_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        workstream_key TEXT NOT NULL,
        proposal_id TEXT NOT NULL UNIQUE,
        created_utc TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_workstream_key
        ON project_workstreams(project_id, workstream_key)""",
    # PROJECT TEAM: who takes part in one project, and as what. Temporary
    # by construction: every row ends when the project closes (or earlier,
    # when the Project Manager releases the participant). It says nothing
    # about Taskforce membership.
    """CREATE TABLE IF NOT EXISTS project_team (
        project_id TEXT NOT NULL REFERENCES projects(project_id),
        specialist_id TEXT NOT NULL REFERENCES specialists(specialist_id),
        joined_as TEXT NOT NULL CHECK (joined_as IN
            ('RETAINED_MEMBER','OUTSIDE_SPECIALIST')),
        status TEXT NOT NULL CHECK (status IN ('ACTIVE','RELEASED')),
        joined_utc TEXT NOT NULL,
        first_assignment_id TEXT NOT NULL,
        ended_utc TEXT,
        end_reason TEXT,
        end_decision_id TEXT,
        PRIMARY KEY (project_id, specialist_id)
    )""",
    # The Project Manager's staffing decision for a workstream's next
    # attempt: request the capability from the Taskforce, or name a
    # specialist. A decision NAMES A SPECIALIST; it never names a provider
    # or a model, and it is no part of the TaskContractRecord.
    """CREATE TABLE IF NOT EXISTS assignment_decisions (
        decision_id TEXT PRIMARY KEY,
        objective_id TEXT,
        project_id TEXT,
        sequence_id TEXT NOT NULL,
        attempt_ordinal INTEGER,
        mode TEXT NOT NULL,
        specialist_id TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('OPEN','APPLIED','REFUSED','SUPERSEDED','CLOSED')),
        note TEXT,
        assignment_id TEXT,
        decided_utc TEXT NOT NULL,
        resolved_utc TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_assignment_decision_sequence
        ON assignment_decisions(sequence_id, status)""",
    # PROJECT ASSIGNMENT: one specialist's temporary participation in one
    # workstream, for one attempt — who executes, how they were staffed
    # (a retained seat the Taskforce resolved, or an outside specialist
    # the Project Manager selected) and why. assignment_id is
    # deterministic over (sequence, attempt ordinal) so a restart
    # re-deriving the same staffing converges on one row. Attempt and call
    # identity are READ from the authority plane and recorded here as
    # references. provider/model are execution-routing detail recorded
    # with the assignment; they are never contract identity.
    """CREATE TABLE IF NOT EXISTS project_assignments (
        assignment_id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(project_id),
        workstream_id TEXT REFERENCES project_workstreams(workstream_id),
        specialist_id TEXT NOT NULL REFERENCES specialists(specialist_id),
        seat_id TEXT REFERENCES taskforce_seats(seat_id),
        sequence_id TEXT NOT NULL,
        attempt_ordinal INTEGER NOT NULL,
        attempt_id TEXT,
        call_id TEXT,
        objective_id TEXT,
        capability TEXT NOT NULL,
        role TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT,
        selection_path TEXT NOT NULL CHECK (selection_path IN
            ('RETAINED_SEAT','OUTSIDE_SPECIALIST')),
        decided_by TEXT NOT NULL CHECK (decided_by IN
            ('TASKFORCE','PROJECT_MANAGER')),
        selected_reason TEXT NOT NULL,
        standing_at_assignment TEXT NOT NULL,
        authorized_by_proposal TEXT,
        assignment_decision TEXT,
        execution TEXT,
        status TEXT NOT NULL CHECK (status IN
            ('SELECTED','DISPATCHED','EXECUTING','COLLECTED','SETTLED',
             'ABANDONED')),
        outcome TEXT,
        selected_utc TEXT NOT NULL,
        settled_utc TEXT
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_assignment_attempt
        ON project_assignments(sequence_id, attempt_ordinal)""",
    """CREATE INDEX IF NOT EXISTS idx_assignment_specialist
        ON project_assignments(specialist_id, status)""",
    """CREATE INDEX IF NOT EXISTS idx_assignment_project
        ON project_assignments(project_id, status)""",
    # CAPABILITY DEMONSTRATION: what a specialist verifiably did. One per
    # assignment; references the verification and evidence receipts. It
    # belongs to the SPECIALIST (seat_id is set only when the work was done
    # through a retained seat) and by itself changes no membership.
    """CREATE TABLE IF NOT EXISTS capability_demonstrations (
        demonstration_id TEXT PRIMARY KEY,
        specialist_id TEXT NOT NULL REFERENCES specialists(specialist_id),
        seat_id TEXT REFERENCES taskforce_seats(seat_id),
        assignment_id TEXT NOT NULL
            REFERENCES project_assignments(assignment_id),
        project_id TEXT,
        standing TEXT NOT NULL,
        capability TEXT NOT NULL,
        role TEXT NOT NULL,
        environment TEXT NOT NULL,
        system_domains TEXT NOT NULL,
        sequence_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        call_id TEXT,
        task_contract_id TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        contract_digest TEXT NOT NULL,
        verification_refs TEXT NOT NULL,
        evidence_refs TEXT NOT NULL,
        criteria TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN
            ('QUALIFYING','NOT_QUALIFYING')),
        observed_findings TEXT NOT NULL,
        recorded_utc TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_demonstration_assignment
        ON capability_demonstrations(assignment_id)""",
    """CREATE INDEX IF NOT EXISTS idx_demonstration_specialist
        ON capability_demonstrations(specialist_id, capability, outcome)""",
    # Workforce decisions: RETAIN (creates membership and a retained
    # seat), RELEASE / DEACTIVATE (of a seat), ESTABLISH (the Taskforce's
    # own evidence-bound promotion), and RELEASE_OUTSIDE — an outside
    # specialist let go after project work, which touches no membership
    # and so carries no seat.
    """CREATE TABLE IF NOT EXISTS retention_decisions (
        decision_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN
            ('RETAIN','RELEASE','DEACTIVATE','ESTABLISH',
             'RELEASE_OUTSIDE')),
        specialist_id TEXT NOT NULL REFERENCES specialists(specialist_id),
        seat_id TEXT REFERENCES taskforce_seats(seat_id),
        capability TEXT NOT NULL,
        role TEXT NOT NULL,
        project_id TEXT,
        proposal_id TEXT,
        objective_id TEXT,
        demonstration_ids TEXT NOT NULL,
        reason TEXT,
        rationale TEXT NOT NULL,
        decided_by TEXT NOT NULL,
        decided_utc TEXT NOT NULL
    )""",
    # Workforce events: the durable workforce changes that may wake the
    # Engineer. event_key makes each one exactly-once.
    """CREATE TABLE IF NOT EXISTS workforce_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_key TEXT NOT NULL UNIQUE,
        event_kind TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        facts TEXT,
        recorded_utc TEXT NOT NULL,
        consumed_in_cycle INTEGER
    )""",
    # The Taskforce's answers to a running specialist's permission
    # requests, decided by the deterministic assignment policy.
    """CREATE TABLE IF NOT EXISTS assignment_approvals (
        request_id TEXT PRIMARY KEY,
        assignment_id TEXT,
        call_id TEXT NOT NULL,
        tool_name TEXT,
        subject TEXT,
        decision TEXT NOT NULL CHECK (decision IN ('allow','deny')),
        reason TEXT NOT NULL,
        decided_utc TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_approval_call
        ON assignment_approvals(call_id)""",
    # Objective-scoped engineering-work budget (F-CE-2 closed): the
    # limits are yours (cap-47); usage is always derived from durable
    # rows, never stored.
    """CREATE TABLE IF NOT EXISTS objective_budgets (
        objective_id TEXT PRIMARY KEY,
        max_contracts INTEGER NOT NULL,
        max_attempts INTEGER NOT NULL,
        max_outside_specialists INTEGER NOT NULL,
        max_repairs INTEGER NOT NULL,
        max_retention_decisions INTEGER NOT NULL,
        set_by TEXT NOT NULL,
        set_utc TEXT NOT NULL
    )""",
    # One reasoning record per cycle: the exact proposals the policy
    # returned for one stimulus batch, durable BEFORE they are disposed.
    # A cycle interrupted after this row replays the recorded proposals
    # instead of reasoning again, so a non-deterministic policy is still
    # exactly-once per stimulus batch.
    """CREATE TABLE IF NOT EXISTS cycle_reasoning (
        cycle_id INTEGER PRIMARY KEY REFERENCES cycles(cycle_id),
        input_digest TEXT NOT NULL,
        policy_id TEXT NOT NULL,
        proposals TEXT NOT NULL,
        exchange TEXT,
        recorded_utc TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_reasoning_input
        ON cycle_reasoning(input_digest)""",
    # Design constraints you state with an objective; the finding's own
    # statement; the workforce assignment behind an execution slot.
    "ALTER TABLE objectives ADD COLUMN constraints TEXT",
    "ALTER TABLE findings ADD COLUMN statement TEXT",
    "ALTER TABLE seats ADD COLUMN assignment_id TEXT",
)

# v3 (additive): the Project Manager's staffing decision may ask for
# escalated execution of that one assignment. The column holds the
# escalation class it asked for (routing.ESCALATION_CLASSES) or NULL; it
# is a word about the engineering need, never a provider or a model.
_DDL_V3 = (
    "ALTER TABLE assignment_decisions ADD COLUMN escalation TEXT",
)

_DDL_V4 = (
    "ALTER TABLE project_workstreams ADD COLUMN work_profile TEXT",
    "ALTER TABLE project_workstreams ADD COLUMN organizational_role TEXT",
    "ALTER TABLE project_workstreams ADD COLUMN resource_scope TEXT",
    "ALTER TABLE project_workstreams ADD COLUMN execution_profile TEXT",
    "ALTER TABLE project_workstreams ADD COLUMN status TEXT"
    " DEFAULT 'ACTIVE'",
    "ALTER TABLE project_workstreams ADD COLUMN completion_policy TEXT",
    """CREATE TABLE IF NOT EXISTS project_workstream_dependencies (
        source_workstream_id TEXT NOT NULL
            REFERENCES project_workstreams(workstream_id),
        target_workstream_id TEXT NOT NULL
            REFERENCES project_workstreams(workstream_id),
        dependency_kind TEXT NOT NULL CHECK (dependency_kind IN
            ('ACCEPTED_ARTIFACT','ACCEPTED_DECISION',
             'REVIEW_OF','COMPLETION')),
        required_acceptance INTEGER NOT NULL DEFAULT 1,
        released_utc TEXT,
        accepted_artifact_address TEXT,
        PRIMARY KEY (source_workstream_id, target_workstream_id)
    )""",
)

_MIGRATIONS = {1: _DDL_V1, 2: _DDL_V2, 3: _DDL_V3, 4: _DDL_V4}


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
            # Explicit, monotonic, non-destructive: each version's
            # statements run once, in order, inside this one transaction
            # (a v1 store gains the v2 workforce tables with every
            # existing row untouched).
            for version in range(current + 1, PLAN_SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version]:
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

    def add_objective(self, objective_id, text, authored_by, now,
                      constraints=None):
        """``constraints`` are the design constraints you state with the
        objective (a list of short texts): engineering input the Engineer
        must respect, kept beside the objective they bound."""
        encoded = None
        if constraints:
            if not (isinstance(constraints, (list, tuple)) and all(
                    isinstance(c, str) and c for c in constraints)):
                raise ValueError("constraints must be a list of texts")
            encoded = json.dumps(list(constraints))
        with self.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO objectives (objective_id, text,"
                " authored_by, status, created_utc, constraints)"
                " VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                (objective_id, text, authored_by, now, encoded))
        return objective_id

    def objective(self, objective_id):
        row = self.conn.execute(
            "SELECT * FROM objectives WHERE objective_id = ?",
            (objective_id,)).fetchone()
        return dict(row) if row else None

    def active_objectives(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM objectives WHERE status = 'ACTIVE'"
            " ORDER BY objective_id").fetchall()]

    def complete_objective(self, objective_id, now, project_status="CLOSED",
                           closed_by="operator",
                           reason="objective_closed"):
        """Close one objective and, with it, the project that realized it:
        the project's team is dissolved and its undecided staffing
        decisions are closed (temporary project state ends here). Taskforce
        membership and seats are untouched — they are not project state."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE objectives SET status = 'COMPLETE',"
                " completed_utc = ? WHERE objective_id = ?"
                " AND status = 'ACTIVE'", (now, objective_id))
            for row in cur.execute(
                    "SELECT project_id FROM projects WHERE objective_id = ?"
                    " AND status = 'ACTIVE'", (objective_id,)).fetchall():
                self.close_project(cur, row["project_id"], project_status,
                                   closed_by, now, reason)

    # ----------------------------------------------------------- projects

    def project(self, project_id):
        row = self.conn.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,)).fetchone()
        return dict(row) if row else None

    def active_project(self, objective_id):
        row = self.conn.execute(
            "SELECT * FROM projects WHERE objective_id = ? AND status ="
            " 'ACTIVE' ORDER BY ordinal DESC LIMIT 1",
            (objective_id,)).fetchone()
        return dict(row) if row else None

    def projects(self, objective_id=None):
        sql, args = "SELECT * FROM projects", ()
        if objective_id is not None:
            sql, args = sql + " WHERE objective_id = ?", (objective_id,)
        return [dict(r) for r in self.conn.execute(
            sql + " ORDER BY opened_utc, rowid", args).fetchall()]

    def active_projects(self):
        """All projects with status ACTIVE, across all objectives."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM projects WHERE status = 'ACTIVE'"
            " ORDER BY opened_utc, rowid").fetchall()]

    def open_project(self, cur, objective_id, title, solution, opened_by,
                     proposal_id, now):
        """Open the objective's project inside the caller's transaction.
        At most one project is ACTIVE per objective: an ACTIVE one is
        returned as it is. Returns (project_row, created)."""
        row = cur.execute(
            "SELECT * FROM projects WHERE objective_id = ? AND status ="
            " 'ACTIVE' ORDER BY ordinal DESC LIMIT 1",
            (objective_id,)).fetchone()
        if row is not None:
            return dict(row), False
        ordinal = cur.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE objective_id = ?",
            (objective_id,)).fetchone()["n"] + 1
        project_id = "pj_" + canonical_json_digest(
            {"objective": objective_id, "ordinal": ordinal})[:32]
        cur.execute(
            "INSERT INTO projects (project_id, objective_id, ordinal,"
            " title, solution, status, opened_by, opened_by_proposal,"
            " opened_utc) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (project_id, objective_id, ordinal, title, solution, opened_by,
             proposal_id, now))
        return dict(cur.execute(
            "SELECT * FROM projects WHERE project_id = ?",
            (project_id,)).fetchone()), True

    def adopt_project(self, cur, project_id, title, solution, proposal_id):
        """The Project Manager formally takes over a project the service
        opened implicitly: it gains the solution statement and its
        opener."""
        cur.execute(
            "UPDATE projects SET title = ?, solution = ?, opened_by ="
            " 'PROJECT_MANAGER', opened_by_proposal = ? WHERE project_id"
            " = ? AND status = 'ACTIVE' AND opened_by = 'IMPLICIT'",
            (title, solution, proposal_id, project_id))
        return cur.rowcount == 1

    def close_project(self, cur, project_id, status, closed_by, now,
                      reason):
        """End one project: it stops being ACTIVE, every team member's
        participation ends, and staffing decisions never applied are
        closed. Assignment rows stay as history. Returns the number of
        participants released."""
        cur.execute(
            "UPDATE projects SET status = ?, closed_utc = ?, closed_by = ?"
            " WHERE project_id = ? AND status = 'ACTIVE'",
            (status, now, closed_by, project_id))
        cur.execute(
            "UPDATE project_team SET status = 'RELEASED', ended_utc = ?,"
            " end_reason = ? WHERE project_id = ? AND status = 'ACTIVE'",
            (now, reason, project_id))
        released = cur.rowcount
        cur.execute(
            "UPDATE assignment_decisions SET status = 'CLOSED',"
            " resolved_utc = ?, note = ? WHERE project_id = ? AND status ="
            " 'OPEN'", (now, reason, project_id))
        return released

    def add_workstream(self, cur, project_id, key, proposal_id, now,
                       work_profile=None, organizational_role=None,
                       resource_scope=None, execution_profile=None,
                       status="ACTIVE", completion_policy=None):
        """Record one workstream of a project (idempotent on the
        work-creation proposal). ``key`` is the Project Manager's name for
        it; a key already taken by other work is made unique.

        Generalized dimensions (work_profile, organizational_role,
        resource_scope, execution_profile, status, completion_policy) are
        persisted when provided. resource_scope and completion_policy are
        stored as canonical JSON strings."""
        row = cur.execute(
            "SELECT * FROM project_workstreams WHERE proposal_id = ?",
            (proposal_id,)).fetchone()
        if row is not None:
            return dict(row)
        count = cur.execute(
            "SELECT COUNT(*) AS n FROM project_workstreams WHERE"
            " project_id = ?", (project_id,)).fetchone()["n"]
        key = key or "w%d" % (count + 1)
        if cur.execute(
                "SELECT 1 FROM project_workstreams WHERE project_id = ?"
                " AND workstream_key = ?", (project_id, key)).fetchone():
            key = "%s-%d" % (key, count + 1)
        workstream_id = "ws_" + canonical_json_digest(
            {"project": project_id, "proposal": proposal_id})[:32]
        rs_json = json.dumps(resource_scope, sort_keys=True) \
            if resource_scope is not None else None
        cp_json = json.dumps(completion_policy, sort_keys=True) \
            if completion_policy is not None else None
        cur.execute(
            "INSERT INTO project_workstreams (workstream_id, project_id,"
            " workstream_key, proposal_id, created_utc,"
            " work_profile, organizational_role, resource_scope,"
            " execution_profile, status, completion_policy)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (workstream_id, project_id, key, proposal_id, now,
             work_profile, organizational_role, rs_json,
             execution_profile, status, cp_json))
        return dict(cur.execute(
            "SELECT * FROM project_workstreams WHERE workstream_id = ?",
            (workstream_id,)).fetchone())

    def workstreams(self, project_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM project_workstreams WHERE project_id = ?"
            " ORDER BY created_utc, rowid", (project_id,)).fetchall()]

    def workstream_by_proposal(self, proposal_id):
        row = self.conn.execute(
            "SELECT * FROM project_workstreams WHERE proposal_id = ?",
            (proposal_id,)).fetchone()
        return dict(row) if row else None

    def workstream_by_key(self, project_id, key):
        row = self.conn.execute(
            "SELECT * FROM project_workstreams WHERE project_id = ? AND"
            " workstream_key = ?", (project_id, key)).fetchone()
        return dict(row) if row else None

    def workstream_of_sequence(self, sequence_id):
        """The workstream a sequence realizes (through the proposal that
        created it), or None for work no project owns."""
        row = self.conn.execute(
            "SELECT w.* FROM project_workstreams w JOIN proposals p ON"
            " p.proposal_id = w.proposal_id WHERE p.sequence_id = ?"
            " ORDER BY p.created_utc, p.rowid LIMIT 1",
            (sequence_id,)).fetchone()
        return dict(row) if row else None

    def generalized_workstream(self, workstream_id):
        """Return a workstream with decoded generalized fields."""
        row = self.conn.execute(
            "SELECT * FROM project_workstreams WHERE workstream_id = ?",
            (workstream_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        for field in ("resource_scope", "completion_policy"):
            if d.get(field) and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (ValueError, json.JSONDecodeError):
                    pass
        return d

    # ----------------------------------------- workstream dependencies

    def add_workstream_dependency(self, cur, source_id, target_id,
                                 dependency_kind,
                                 required_acceptance=True):
        """Record a dependency between two workstreams (idempotent)."""
        row = cur.execute(
            "SELECT * FROM project_workstream_dependencies"
            " WHERE source_workstream_id = ? AND target_workstream_id = ?",
            (source_id, target_id)).fetchone()
        if row is not None:
            return dict(row)
        cur.execute(
            "INSERT INTO project_workstream_dependencies"
            " (source_workstream_id, target_workstream_id,"
            "  dependency_kind, required_acceptance)"
            " VALUES (?, ?, ?, ?)",
            (source_id, target_id, dependency_kind,
             1 if required_acceptance else 0))
        return dict(cur.execute(
            "SELECT * FROM project_workstream_dependencies"
            " WHERE source_workstream_id = ? AND target_workstream_id = ?",
            (source_id, target_id)).fetchone())

    def dependencies_for_workstream(self, workstream_id):
        """Dependencies this workstream waits on (as target)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM project_workstream_dependencies"
            " WHERE target_workstream_id = ?",
            (workstream_id,)).fetchall()]

    def dependents_of_workstream(self, workstream_id):
        """Workstreams that depend on this one (as source)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM project_workstream_dependencies"
            " WHERE source_workstream_id = ?",
            (workstream_id,)).fetchall()]

    def release_workstream_dependency(self, cur, source_id, target_id,
                                     released_utc,
                                     accepted_artifact_address=None):
        """Mark a dependency as released by an accepted predecessor."""
        cur.execute(
            "UPDATE project_workstream_dependencies"
            " SET released_utc = ?, accepted_artifact_address = ?"
            " WHERE source_workstream_id = ? AND target_workstream_id = ?"
            " AND released_utc IS NULL",
            (released_utc, accepted_artifact_address, source_id, target_id))

    def dependency_state(self, project_id):
        """Dependency state summary for a project: total, released, pending."""
        rows = self.conn.execute(
            "SELECT d.* FROM project_workstream_dependencies d"
            " JOIN project_workstreams w"
            "   ON d.source_workstream_id = w.workstream_id"
            " WHERE w.project_id = ?", (project_id,)).fetchall()
        total = len(rows)
        released = sum(1 for r in rows if r["released_utc"] is not None)
        return {"total": total, "released": released,
                "pending": total - released}

    # ------------------------------------------------ assignment decisions

    def record_assignment_decision(self, cur, decision_id, objective_id,
                                   project_id, sequence_id, attempt_ordinal,
                                   mode, specialist_id, now,
                                   escalation=None):
        """Record the Project Manager's staffing decision for a
        workstream's next attempt. The latest decision wins: an earlier
        one still OPEN for the sequence is superseded. ``escalation`` is
        the escalation class the decision asked for, if any."""
        cur.execute(
            "UPDATE assignment_decisions SET status = 'SUPERSEDED',"
            " resolved_utc = ?, note = ? WHERE sequence_id = ? AND status"
            " = 'OPEN' AND decision_id != ?",
            (now, "superseded by " + decision_id, sequence_id,
             decision_id))
        cur.execute(
            "INSERT OR IGNORE INTO assignment_decisions (decision_id,"
            " objective_id, project_id, sequence_id, attempt_ordinal,"
            " mode, specialist_id, status, decided_utc, escalation)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)",
            (decision_id, objective_id, project_id, sequence_id,
             attempt_ordinal, mode, specialist_id, now, escalation))
        return cur.rowcount == 1

    def open_staffing_decision(self, sequence_id):
        row = self.conn.execute(
            "SELECT * FROM assignment_decisions WHERE sequence_id = ? AND"
            " status = 'OPEN' ORDER BY decided_utc DESC, rowid DESC"
            " LIMIT 1", (sequence_id,)).fetchone()
        return dict(row) if row else None

    def assignment_decisions(self, sequence_id=None, project_id=None):
        sql, clauses, args = "SELECT * FROM assignment_decisions", [], []
        for column, value in (("sequence_id", sequence_id),
                              ("project_id", project_id)):
            if value is not None:
                clauses.append("%s = ?" % column)
                args.append(value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [dict(r) for r in self.conn.execute(
            sql + " ORDER BY decided_utc, rowid", tuple(args)).fetchall()]

    def resolve_assignment_decision(self, cur, decision_id, status, now,
                                    note=None, assignment_id=None):
        cur.execute(
            "UPDATE assignment_decisions SET status = ?, resolved_utc = ?,"
            " note = COALESCE(?, note), assignment_id = COALESCE(?,"
            " assignment_id) WHERE decision_id = ? AND status = 'OPEN'",
            (status, now, note, assignment_id, decision_id))
        return cur.rowcount == 1

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

    # --------------------------------------------------- workforce events

    def emit_workforce_event(self, cur, event_key, event_kind, subject_id,
                             facts, now):
        """Record one workforce event inside the caller's transaction.
        ``event_key`` is its identity: the same durable workforce change
        re-observed after a restart is the same event, recorded once.
        Returns True when the row is new."""
        cur.execute(
            "INSERT OR IGNORE INTO workforce_events (event_key, event_kind,"
            " subject_id, facts, recorded_utc) VALUES (?, ?, ?, ?, ?)",
            (event_key, event_kind, subject_id,
             json.dumps(facts, sort_keys=True) if facts else None, now))
        return cur.rowcount == 1

    def unconsumed_workforce_events(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM workforce_events WHERE consumed_in_cycle IS NULL"
            " ORDER BY event_id").fetchall()]

    def consume_workforce_events(self, cur, event_ids, cycle_id):
        for event_id in event_ids:
            cur.execute(
                "UPDATE workforce_events SET consumed_in_cycle = ?"
                " WHERE event_id = ? AND consumed_in_cycle IS NULL",
                (cycle_id, event_id))

    # ------------------------------------------------------------ budgets

    def set_budget(self, objective_id, now, set_by="you", **limits):
        """Set (or replace) one objective's engineering-work budget.
        Unnamed limits take the conservative default."""
        values = dict(DEFAULT_BUDGET)
        for key, value in limits.items():
            if key not in DEFAULT_BUDGET:
                raise ValueError("unknown budget dimension %r" % (key,))
            if not (isinstance(value, int) and value >= 0):
                raise ValueError("budget %s must be a non-negative integer"
                                 % key)
            values[key] = value
        with self.transaction() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO objective_budgets (objective_id,"
                " max_contracts, max_attempts, max_outside_specialists,"
                " max_repairs, max_retention_decisions, set_by, set_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (objective_id, values["max_contracts"],
                 values["max_attempts"], values["max_outside_specialists"],
                 values["max_repairs"], values["max_retention_decisions"],
                 set_by, now))
        return values

    def budget_limits(self, objective_id):
        row = self.conn.execute(
            "SELECT * FROM objective_budgets WHERE objective_id = ?",
            (objective_id,)).fetchone()
        if row is None:
            return dict(DEFAULT_BUDGET)
        return {key: row[key] for key in DEFAULT_BUDGET}

    def budget_usage(self, objective_id):
        """What one objective has consumed, DERIVED from durable rows
        (admitted contract proposals, staffed assignments, applied repair
        authorizations, planner retention decisions) — never a counter
        that could drift from them."""
        conn = self.conn
        contracts = conn.execute(
            "SELECT COUNT(DISTINCT sequence_id) AS n FROM proposals"
            " WHERE objective_id = ? AND status = 'ADMITTED'"
            " AND sequence_id IS NOT NULL"
            " AND class IN ('CREATE_TASK','INVESTIGATE','REQUEST_REVIEW')",
            (objective_id,)).fetchone()["n"]
        attempts = conn.execute(
            "SELECT COUNT(*) AS n FROM project_assignments"
            " WHERE objective_id = ? AND status != 'ABANDONED'",
            (objective_id,)).fetchone()["n"]
        outside = conn.execute(
            "SELECT COUNT(*) AS n FROM project_assignments"
            " WHERE objective_id = ? AND status != 'ABANDONED'"
            " AND selection_path = 'OUTSIDE_SPECIALIST'",
            (objective_id,)).fetchone()["n"]
        repairs = conn.execute(
            "SELECT COUNT(*) AS n FROM proposals WHERE objective_id = ?"
            " AND class = 'REPAIR_TASK' AND status = 'APPLIED'",
            (objective_id,)).fetchone()["n"]
        retention = conn.execute(
            "SELECT COUNT(*) AS n FROM retention_decisions"
            " WHERE objective_id = ? AND decided_by = 'planner'",
            (objective_id,)).fetchone()["n"]
        return {"contracts": contracts, "attempts": attempts,
                "outside_specialists": outside, "repairs": repairs,
                "retention_decisions": retention}

    def budget_headroom(self, objective_id, dimension):
        """Remaining units of one dimension (contracts, attempts,
        outside_specialists, repairs, retention_decisions)."""
        limits = self.budget_limits(objective_id)
        used = self.budget_usage(objective_id)
        return limits["max_" + dimension] - used[dimension]

    # ---------------------------------------------------------- reasoning

    def record_reasoning(self, cycle_id, input_digest, policy_id,
                         proposals, exchange, now):
        """Make one cycle's reasoning output durable BEFORE disposal."""
        with self.transaction() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO cycle_reasoning (cycle_id,"
                " input_digest, policy_id, proposals, exchange,"
                " recorded_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (cycle_id, input_digest, policy_id,
                 json.dumps(proposals, sort_keys=True),
                 json.dumps(exchange, sort_keys=True) if exchange else None,
                 now))

    def interrupted_reasoning(self, input_digest):
        """The recorded reasoning of an INTERRUPTED cycle (still OPEN)
        over exactly this stimulus batch, or None."""
        row = self.conn.execute(
            "SELECT r.* FROM cycle_reasoning r JOIN cycles c"
            " ON c.cycle_id = r.cycle_id WHERE r.input_digest = ?"
            " AND c.status = 'OPEN' ORDER BY r.cycle_id DESC LIMIT 1",
            (input_digest,)).fetchone()
        return dict(row) if row else None

    def supersede_cycle(self, cycle_id, by_cycle_id, now):
        with self.transaction() as cur:
            cur.execute(
                "UPDATE cycles SET status = 'CLOSED', closed_utc = ?,"
                " note = ? WHERE cycle_id = ? AND status = 'OPEN'",
                (now, "interrupted; replayed by cycle %d" % by_cycle_id,
                 cycle_id))

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
