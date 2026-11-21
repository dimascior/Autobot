"""The Taskforce: Autobot's persistent engineering organization — and the
catalog of specialists a project may draw on.

Two things are kept apart here, in every table and every function:

    TAKING PART IN A PROJECT            BELONGING TO THE TASKFORCE
    ProjectAssignment  (temporary)      TaskforceMembership (persistent)
    project team       (temporary)      TaskforceSeat       (a retained
                                                             capability/role)

The Engineer states WHAT a piece of work needs — a capability and a role.
That requirement is frozen into the TaskContractRecord, which never names
a provider, a model or a specialist. WHO executes one attempt of it is a
ProjectAssignment, decided on one of two paths:

    Engineer/PM requests the capability -> the Taskforce resolves a
        retained member (``TaskforceSeatResolver.resolve``)
    Engineer/PM explicitly selects an outside specialist -> a
        project-specific assignment

The Taskforce never puts an outside specialist on a project: composing the
temporary team is the Project Manager's decision. The Taskforce owns the
persistent side — retained membership, retained seats, capability history,
demonstrated environment competence, availability and the resolution of
retained capabilities.

An outside specialist may execute project work without ever becoming a
member: an assignment creates no membership, no seat and no provisional
standing of any kind. Verified work is recorded as a
CapabilityDemonstration that belongs to the specialist; the demonstration
by itself changes nothing either. Membership exists only through one
explicit, evidence-bound workforce decision:

    outside specialist -> verified work -> CapabilityDemonstration
        -> RETAIN_SPECIALIST -> TaskforceMembership -> retained seat
        -> (repeated verified work) -> ESTABLISHED

- ``SpecialistPool`` is the catalog: the engineering workers that exist,
  what each may be tried for, and how each is routed (provider, model,
  tools, context budget, expected output). Routing is an implementation
  attribute of a worker, resolved downstream of staffing; it is never
  contract identity and never a staffing decision.
- ``TaskforceRoster`` is the durable organization (``intelligence.db``):
  stable specialist identities, memberships, seats, every seat transition
  and the decision that caused it, the project assignments and teams, and
  the demonstrations.
- ``TaskforceSeatResolver`` resolves a capability request against the
  retained seats, and checks a Project Manager's explicit selection
  against the constraints that bind it (the specialist exists and offers
  the work, a review stays independent of its subject).

Authority is untouched by all of this. Retention is preferential
ELIGIBILITY for future staffing and nothing else: a retained or
established member is assigned through the same TaskContractRecord,
AttemptContract, ExecutionManifest, Collector, Verifier and Sequencer as
anyone, and the Taskforce never certifies its own specialists — every
demonstration that supports a seat is derived from verification receipts
the authority plane admitted.

The registered principal. Chapter 0 froze seat, route and model selection
as cap-14, owned by the principal ``agent_factory`` and forbidden to the
``task_force`` agents (no agent chooses its own model). That registry is
immutable history. ``agent_factory`` is not an actor of this architecture:
it is the wire identity of the staffing function, kept so every manifest
already admitted stays valid. Records that cross the execution boundary
keep the registered id (``selected_by: "agent_factory"``);
``staffed_by: "taskforce"`` names the architecture's term. The function
stays deterministic code, so the forbidden edge holds exactly as frozen.
"""

import json

from .capabilities import CAPABILITIES
from .proposals_util import canonical_json_digest

# The registered cap-14 principal the staffing function is recorded under
# (principals.json): the wire identity on assignments and manifests.
STAFFING_PRINCIPAL = "agent_factory"
STAFFING_FUNCTION = "taskforce"

# The standing of a specialist with no retained seat for a (capability,
# role): outside the Taskforce for that work. It is the ABSENCE of a seat.
OUTSIDE = "OUTSIDE"
SEAT_STATES = ("RETAINED", "ESTABLISHED", "INACTIVE", "RELEASED")
# Seat states that carry retained standing (the Taskforce may resolve them).
MEMBER_STATES = ("RETAINED", "ESTABLISHED")
MEMBERSHIP_STATES = ("ACTIVE", "INACTIVE", "RELEASED")

# The deterministic seat transition table: (from, to) -> the one cause
# that may produce it. Nothing else moves a seat. No transition is caused
# by an assignment or by a demonstration: a seat begins with a retention
# decision.
SEAT_TRANSITIONS = {
    (OUTSIDE, "RETAINED"): "retention_decision",
    ("RELEASED", "RETAINED"): "retention_decision",
    ("INACTIVE", "RETAINED"): "retention_decision",
    ("RETAINED", "ESTABLISHED"): "established_threshold",
    ("RETAINED", "INACTIVE"): "deactivation_decision",
    ("ESTABLISHED", "INACTIVE"): "deactivation_decision",
    ("RETAINED", "RELEASED"): "release_decision",
    ("ESTABLISHED", "RELEASED"): "release_decision",
    ("INACTIVE", "RELEASED"): "release_decision",
}

RELEASE_REASONS = ("task_only", "obsolete_capability", "poor_performance",
                   "environment_mismatch", "capacity")
# A specialist released for one of these is not selected again for that
# capability; the others describe the organization, not the worker.
RELEASE_FOR_CAUSE = ("poor_performance", "environment_mismatch")

# How one attempt is staffed.
#   taskforce           request the capability; the Taskforce resolves a
#                       retained member
#   different_member    the same, excluding whoever did the last attempt
#   outside_specialist  the Project Manager names an outside specialist
#   same_member         the Project Manager keeps whoever did the last
#                       attempt
STAFFING_MODES = ("taskforce", "same_member", "different_member",
                  "outside_specialist")
# Modes in which the Project Manager, not the Taskforce, picks the person.
PM_SELECTED_MODES = ("outside_specialist", "same_member")

# Assignments whose specialist is still occupied by the execution.
BUSY_STATES = ("SELECTED", "DISPATCHED", "EXECUTING")
OPEN_STATES = ("SELECTED", "DISPATCHED", "EXECUTING", "COLLECTED")

_STATUS_RANK = {"ESTABLISHED": 2, "RETAINED": 1}


class WorkforceError(Exception):
    pass


def specialist_identity(handle):
    """The stable specialist id: a function of the worker's handle alone,
    so provider and model can be replaced without changing identity."""
    return "sp_" + canonical_json_digest({"handle": handle})[:32]


def membership_identity(specialist_id):
    return "tm_" + canonical_json_digest({"specialist": specialist_id})[:32]


def seat_identity(specialist_id, capability, role):
    return "ts_" + canonical_json_digest(
        {"specialist": specialist_id, "capability": capability,
         "role": role})[:32]


def assignment_identity(sequence_id, attempt_ordinal):
    """Deterministic over the attempt it staffs: a restart re-deriving the
    same staffing converges on the same row."""
    return "asg_" + canonical_json_digest(
        {"sequence": sequence_id, "ordinal": attempt_ordinal})[:32]


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


# --------------------------------------------------------------- the pool

class SpecialistPool:
    """The catalog: registered engineering workers, in registration order
    (so every listing over it is reproducible).

    A registration names one thing a worker may be tried for:
    ``register(capability, role, provider, model=...)``. Registrations
    that share a ``handle`` describe the SAME worker; the default handle
    is the provider and model, so one provider/model pair is one worker
    unless you name several."""

    def __init__(self):
        self._profiles = {}
        self._order = []
        # The organization's execution-routing policy (routing.py), when
        # configuration declares one. None: every worker carries its own
        # route and nothing is escalated.
        self.routing = None

    @staticmethod
    def default_handle(provider, model=None):
        return "%s/%s" % (provider, model) if model else provider

    def register(self, capability, role, provider, model=None,
                 tool_requirements=(), context_budget=None,
                 expected_output="artifacts", completion_policy=None,
                 handle=None, effort=None, origin="outside_pool",
                 max_concurrent=1, provider_args=(), route_name=None):
        if capability not in CAPABILITIES:
            raise WorkforceError(
                "capability %r is not in the registered vocabulary"
                % (capability,))
        if not (isinstance(role, str) and role):
            raise WorkforceError("role must be a non-empty string")
        if not (isinstance(provider, str) and provider):
            raise WorkforceError("provider must be a non-empty string")
        if not (isinstance(max_concurrent, int) and max_concurrent >= 1):
            raise WorkforceError("max_concurrent must be a positive integer")
        handle = handle or self.default_handle(provider, model)
        profile = self._profiles.get(handle)
        if profile is None:
            profile = {
                "handle": handle,
                "specialist_id": specialist_identity(handle),
                "provider": provider,
                "model": model,
                "offers": [],
                "tool_requirements": list(tool_requirements),
                "context_budget": context_budget,
                "expected_output": expected_output,
                "completion_policy": completion_policy or
                    {"required": True, "success_outcomes": ["success"]},
                "effort": effort,
                "origin": origin,
                "max_concurrent": max_concurrent,
                # Native session arguments of the route, and the name the
                # routing policy gives it (None for a worker that carries
                # its own route).
                "provider_args": list(provider_args or ()),
                "route_name": route_name,
            }
            self._profiles[handle] = profile
            self._order.append(handle)
        elif (profile["provider"], profile["model"]) != (provider, model):
            raise WorkforceError(
                "handle %r already names the worker %s/%s; use reroute()"
                " to replace a worker's provider or model"
                % (handle, profile["provider"], profile["model"]))
        offer = [capability, role]
        if offer not in profile["offers"]:
            profile["offers"].append(offer)
        return profile

    def reroute(self, handle, provider=None, model=None, **route):
        """Replace how one worker is executed. Identity, membership, seats
        and every frozen contract are unaffected: the activity is stable,
        the execution implementation is replaceable."""
        profile = self._profiles.get(handle)
        if profile is None:
            raise WorkforceError("no worker is registered as %r" % (handle,))
        if provider is not None:
            profile["provider"] = provider
            profile["model"] = model
        elif model is not None:
            profile["model"] = model
        for key in ("tool_requirements", "context_budget",
                    "expected_output", "effort", "route_name"):
            if key in route:
                profile[key] = route[key]
        if "provider_args" in route:
            profile["provider_args"] = list(route["provider_args"] or ())
        return profile

    def profiles(self):
        return [self._profiles[handle] for handle in self._order]

    def profile(self, handle):
        return self._profiles.get(handle)

    def position(self, handle):
        return self._order.index(handle)

    def can_source(self, capability, role=None):
        """Whether any registered worker may be tried for this request."""
        for profile in self.profiles():
            for cap, offered_role in profile["offers"]:
                if cap == capability and role in (None, offered_role):
                    return True
        return False


def _route_of(profile):
    route = {"tool_requirements": profile["tool_requirements"],
             "context_budget": profile["context_budget"],
             "expected_output": profile["expected_output"],
             "completion_policy": profile["completion_policy"],
             "effort": profile["effort"],
             "max_concurrent": profile["max_concurrent"]}
    # Present only when set, so a worker that carries neither keeps the
    # route text it has always had.
    if profile.get("provider_args"):
        route["provider_args"] = list(profile["provider_args"])
    if profile.get("route_name"):
        route["route_name"] = profile["route_name"]
    return route


def _offered_role(specialist, capability, role):
    """The role under which this worker offers the capability (the asked
    role, or its first offer of the capability when none is asked)."""
    for cap, offered in _loads(specialist["offers"], []):
        if cap == capability and role in (None, offered):
            return offered
    return None


# ------------------------------------------------------------- the roster

class TaskforceRoster:
    """The durable engineering organization over the plan store: who
    exists, who is a Taskforce member and in which seats, who took part in
    which project, and what each verifiably did."""

    def __init__(self, plan, pool, established_threshold=3,
                 available_providers=None):
        if not (isinstance(established_threshold, int)
                and established_threshold >= 2):
            raise WorkforceError(
                "established_threshold must be an integer >= 2 (an"
                " established seat rests on REPEATED verified work)")
        self.plan = plan
        self.pool = pool
        self.established_threshold = established_threshold
        # A runtime availability narrowing (None = every provider is up).
        self.available_providers = available_providers

    # ------------------------------------------------------ pool mirror

    def sync_pool(self, now):
        """Mirror the catalog into the durable specialist table: new
        workers are first seen, rerouted workers keep their identity,
        workers no longer in the catalog are withdrawn (their history,
        membership and seats stay). Writes only when something changed."""
        known = {row["handle"]: dict(row) for row in self.plan.conn.execute(
            "SELECT * FROM specialists").fetchall()}
        changes = []
        present = set()
        for profile in self.pool.profiles():
            present.add(profile["handle"])
            route = json.dumps(_route_of(profile), sort_keys=True)
            offers = json.dumps(sorted(profile["offers"]))
            row = known.get(profile["handle"])
            if row is None:
                changes.append(("insert", profile, route, offers))
            elif (row["provider"], row["model"], row["route"],
                  row["offers"], row["status"]) != (
                    profile["provider"], profile["model"], route, offers,
                    "ACTIVE"):
                changes.append(("update", profile, route, offers))
        withdrawn = [handle for handle, row in known.items()
                     if handle not in present and row["status"] == "ACTIVE"]
        if not (changes or withdrawn):
            return 0
        with self.plan.transaction() as cur:
            for kind, profile, route, offers in changes:
                if kind == "insert":
                    cur.execute(
                        "INSERT OR IGNORE INTO specialists (specialist_id,"
                        " handle, provider, model, route, offers, origin,"
                        " status, first_seen_utc, updated_utc)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                        (profile["specialist_id"], profile["handle"],
                         profile["provider"], profile["model"], route,
                         offers, profile["origin"], now, now))
                else:
                    cur.execute(
                        "UPDATE specialists SET provider = ?, model = ?,"
                        " route = ?, offers = ?, status = 'ACTIVE',"
                        " updated_utc = ? WHERE handle = ?",
                        (profile["provider"], profile["model"], route,
                         offers, now, profile["handle"]))
            for handle in withdrawn:
                cur.execute(
                    "UPDATE specialists SET status = 'WITHDRAWN',"
                    " updated_utc = ? WHERE handle = ?", (now, handle))
        return len(changes) + len(withdrawn)

    # ------------------------------------------------------------ reads

    def _rows(self, sql, args=()):
        return [dict(r) for r in self.plan.conn.execute(sql, args).fetchall()]

    def _filtered(self, table, filters, order, statuses=None,
                  status_column="status"):
        clauses, args = [], []
        if statuses:
            clauses.append("%s IN (%s)" % (
                status_column, ",".join("?" for _ in statuses)))
            args.extend(statuses)
        for column, value in filters:
            if value is not None:
                clauses.append("%s = ?" % column)
                args.append(value)
        sql = "SELECT * FROM %s" % table
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self._rows(sql + " ORDER BY " + order, tuple(args))

    def specialist(self, specialist_id):
        row = self.plan.conn.execute(
            "SELECT * FROM specialists WHERE specialist_id = ?",
            (specialist_id,)).fetchone()
        return dict(row) if row else None

    def specialists(self):
        """Every known worker, in catalog order (the order they were first
        seen, which is registration order)."""
        return self._rows("SELECT * FROM specialists ORDER BY"
                          " first_seen_utc, rowid")

    def snapshot(self):
        """Plain-data snapshot of the Taskforce for reasoning views."""
        members = self.memberships(statuses=("ACTIVE",))
        all_seats = self.seats()
        return {
            "members": [{"specialist_id": m["specialist_id"],
                         "membership_id": m["membership_id"],
                         "status": m["status"],
                         "joined_utc": m["joined_utc"]}
                        for m in members],
            "seats": [{"seat_id": s["seat_id"],
                       "specialist_id": s["specialist_id"],
                       "capability": s["capability"],
                       "role": s["role"],
                       "status": s["status"]}
                      for s in all_seats],
            "member_count": len(members),
            "seat_count": len(all_seats),
        }

    def membership(self, specialist_id):
        row = self.plan.conn.execute(
            "SELECT * FROM taskforce_memberships WHERE specialist_id = ?",
            (specialist_id,)).fetchone()
        return dict(row) if row else None

    def memberships(self, statuses=None):
        return self._filtered("taskforce_memberships", (),
                              "joined_utc, rowid", statuses=statuses)

    def is_member(self, specialist_id):
        """Whether the specialist is a Taskforce member right now (holds
        at least one RETAINED or ESTABLISHED seat)."""
        row = self.membership(specialist_id)
        return row is not None and row["status"] == "ACTIVE"

    def seat(self, seat_id):
        row = self.plan.conn.execute(
            "SELECT * FROM taskforce_seats WHERE seat_id = ?",
            (seat_id,)).fetchone()
        return dict(row) if row else None

    def seat_for(self, specialist_id, capability, role):
        row = self.plan.conn.execute(
            "SELECT * FROM taskforce_seats WHERE specialist_id = ?"
            " AND capability = ? AND role = ?",
            (specialist_id, capability, role)).fetchone()
        return dict(row) if row else None

    def seats(self, statuses=None, capability=None, specialist_id=None):
        return self._filtered(
            "taskforce_seats",
            (("capability", capability), ("specialist_id", specialist_id)),
            "created_utc, rowid", statuses=statuses)

    def standing(self, specialist_id, capability, role):
        """The specialist's standing for exactly this work: the status of
        its retained seat, or OUTSIDE when it holds none (never had one,
        or it was deactivated or released)."""
        seat = self.seat_for(specialist_id, capability, role)
        if seat is not None and seat["status"] in MEMBER_STATES:
            return seat["status"]
        return OUTSIDE

    def history(self, seat_id):
        return self._rows(
            "SELECT * FROM seat_history WHERE seat_id = ?"
            " ORDER BY history_id", (seat_id,))

    def assignment(self, assignment_id):
        row = self.plan.conn.execute(
            "SELECT * FROM project_assignments WHERE assignment_id = ?",
            (assignment_id,)).fetchone()
        return dict(row) if row else None

    def assignment_for(self, sequence_id, attempt_ordinal):
        return self.assignment(
            assignment_identity(sequence_id, attempt_ordinal))

    def assignment_by_attempt(self, attempt_id):
        row = self.plan.conn.execute(
            "SELECT * FROM project_assignments WHERE attempt_id = ?",
            (attempt_id,)).fetchone()
        return dict(row) if row else None

    def assignment_by_call(self, call_id):
        row = self.plan.conn.execute(
            "SELECT * FROM project_assignments WHERE call_id = ?",
            (call_id,)).fetchone()
        return dict(row) if row else None

    def assignments(self, statuses=None, seat_id=None, specialist_id=None,
                    sequence_id=None, objective_id=None, project_id=None):
        return self._filtered(
            "project_assignments",
            (("seat_id", seat_id), ("specialist_id", specialist_id),
             ("sequence_id", sequence_id), ("objective_id", objective_id),
             ("project_id", project_id)),
            "selected_utc, rowid", statuses=statuses)

    def demonstration(self, demonstration_id):
        row = self.plan.conn.execute(
            "SELECT * FROM capability_demonstrations"
            " WHERE demonstration_id = ?", (demonstration_id,)).fetchone()
        return dict(row) if row else None

    def demonstrations(self, specialist_id=None, seat_id=None,
                       capability=None, role=None, outcome=None,
                       limit=None):
        rows = self._filtered(
            "capability_demonstrations",
            (("specialist_id", specialist_id), ("seat_id", seat_id),
             ("capability", capability), ("role", role),
             ("outcome", outcome)),
            "recorded_utc, rowid")
        return rows[-limit:] if limit else rows

    def decisions(self, seat_id=None, specialist_id=None, capability=None,
                  role=None):
        return self._filtered(
            "retention_decisions",
            (("seat_id", seat_id), ("specialist_id", specialist_id),
             ("capability", capability), ("role", role)),
            "decided_utc, rowid")

    def team(self, project_id):
        """The project's team: everyone who took part, as what, and
        whether their participation is still open."""
        rows = self._rows(
            "SELECT * FROM project_team WHERE project_id = ?"
            " ORDER BY joined_utc, rowid", (project_id,))
        for row in rows:
            specialist = self.specialist(row["specialist_id"])
            row["handle"] = specialist["handle"] if specialist else None
        return rows

    # ----------------------------------------------------- availability

    def busy_count(self, seat_id):
        return self.plan.conn.execute(
            "SELECT COUNT(*) AS n FROM project_assignments"
            " WHERE seat_id = ? AND status IN (%s)"
            % ",".join("?" for _ in BUSY_STATES),
            (seat_id,) + BUSY_STATES).fetchone()["n"]

    def specialist_busy_count(self, specialist_id):
        """Executions this worker is occupied by right now, on every
        project and in every standing."""
        return self.plan.conn.execute(
            "SELECT COUNT(*) AS n FROM project_assignments"
            " WHERE specialist_id = ? AND status IN (%s)"
            % ",".join("?" for _ in BUSY_STATES),
            (specialist_id,) + BUSY_STATES).fetchone()["n"]

    def provider_available(self, specialist):
        if specialist["status"] != "ACTIVE":
            return False
        return (self.available_providers is None
                or specialist["provider"] in self.available_providers)

    def specialist_available(self, specialist):
        """Whether the worker can take one more execution now: it is in
        the catalog, its provider is up, and it is below its own
        concurrency limit."""
        if specialist is None or not self.provider_available(specialist):
            return False
        limit = int(_loads(specialist["route"], {}).get(
            "max_concurrent") or 1)
        return self.specialist_busy_count(
            specialist["specialist_id"]) < limit

    def seat_available(self, seat, specialist=None):
        specialist = specialist or self.specialist(seat["specialist_id"])
        return self.specialist_available(specialist)

    def experience(self, specialist_id, capability, role=None,
                   environment=None, subsystem=None):
        """The verified performance of one specialist in one capability,
        from its demonstration records alone (whatever its standing was
        when it did the work)."""
        rows = self.demonstrations(specialist_id=specialist_id,
                                   capability=capability, role=role)
        qualifying = [r for r in rows if r["outcome"] == "QUALIFYING"]
        summary = {
            "qualifying": len(qualifying),
            "not_qualifying": len(rows) - len(qualifying),
            "independent_sequences": len(
                {r["sequence_id"] for r in qualifying}),
            "environment": 0,
            "system": 0,
        }
        for row in qualifying:
            if environment and row["environment"] == environment:
                summary["environment"] += 1
            if subsystem and subsystem in _loads(
                    row["system_domains"], []):
                summary["system"] += 1
        return summary

    def released_for_cause(self, specialist_id, capability, role):
        """Whether the latest workforce decision about this specialist in
        this work was a release for cause (and no retention followed)."""
        latest = None
        for decision in self.decisions(specialist_id=specialist_id,
                                       capability=capability, role=role):
            if decision["kind"] in ("RETAIN", "RELEASE", "RELEASE_OUTSIDE"):
                latest = decision
        return bool(latest and latest["kind"] != "RETAIN"
                    and latest["reason"] in RELEASE_FOR_CAUSE)

    # ------------------------------------------------------ transitions

    def _sync_membership(self, cur, specialist_id, now):
        """A membership's status follows the seats it holds: ACTIVE while
        any is RETAINED or ESTABLISHED, INACTIVE while the best is
        INACTIVE, RELEASED when every seat is released."""
        row = cur.execute(
            "SELECT status FROM taskforce_memberships WHERE specialist_id"
            " = ?", (specialist_id,)).fetchone()
        if row is None:
            return None
        statuses = {r["status"] for r in cur.execute(
            "SELECT status FROM taskforce_seats WHERE specialist_id = ?",
            (specialist_id,)).fetchall()}
        if statuses & set(MEMBER_STATES):
            status = "ACTIVE"
        elif "INACTIVE" in statuses:
            status = "INACTIVE"
        else:
            status = "RELEASED"
        if status != row["status"]:
            cur.execute(
                "UPDATE taskforce_memberships SET status = ?, updated_utc"
                " = ?, ended_utc = ? WHERE specialist_id = ?",
                (status, now, now if status == "RELEASED" else None,
                 specialist_id))
        return status

    def _transition(self, cur, seat, to_status, now, decision_id=None,
                    demonstration_id=None, assignment_id=None):
        """Apply one registered seat transition and record it. Refuses
        anything the transition table does not name."""
        key = (seat["status"], to_status)
        cause = SEAT_TRANSITIONS.get(key)
        if cause is None:
            raise WorkforceError(
                "seat %s cannot move %s -> %s: not a registered seat"
                " transition" % (seat["seat_id"], key[0], key[1]))
        retained_since = seat.get("retained_since_utc")
        if to_status == "RETAINED" and key[0] != "INACTIVE":
            retained_since = now
        cur.execute(
            "UPDATE taskforce_seats SET status = ?, retained_since_utc = ?,"
            " updated_utc = ? WHERE seat_id = ? AND status = ?",
            (to_status, retained_since, now, seat["seat_id"], key[0]))
        if cur.rowcount != 1:
            raise WorkforceError(
                "seat %s changed underneath the transition %s -> %s"
                % (seat["seat_id"], key[0], key[1]))
        cur.execute(
            "INSERT INTO seat_history (seat_id, from_status, to_status,"
            " cause, decision_id, demonstration_id, assignment_id,"
            " recorded_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (seat["seat_id"], key[0], to_status, cause, decision_id,
             demonstration_id, assignment_id, now))
        seat = dict(seat)
        seat["status"] = to_status
        seat["retained_since_utc"] = retained_since
        self._sync_membership(cur, seat["specialist_id"], now)
        return seat

    def _maybe_establish(self, cur, seat, now):
        """RETAINED + repeated independent verified work = ESTABLISHED.
        Deterministic and evidence-bound; it changes ranking, never
        authority. The count is of the specialist's QUALIFYING
        demonstrations in this seat's capability and role, on independent
        sequences — including the work that earned the seat."""
        if seat["status"] != "RETAINED":
            return seat
        rows = [dict(r) for r in cur.execute(
            "SELECT demonstration_id, sequence_id FROM"
            " capability_demonstrations WHERE specialist_id = ? AND"
            " capability = ? AND role = ? AND outcome = 'QUALIFYING'"
            " ORDER BY recorded_utc, rowid",
            (seat["specialist_id"], seat["capability"],
             seat["role"])).fetchall()]
        independent = {}
        for row in rows:
            independent.setdefault(row["sequence_id"],
                                   row["demonstration_id"])
        if len(independent) < self.established_threshold:
            return seat
        ids = sorted(independent.values())
        decision_id = self._decision(
            cur, "ESTABLISH", seat["specialist_id"], seat["seat_id"],
            seat["capability"], seat["role"], None, None, ids, None,
            "%d independent verified qualifying demonstrations reached"
            " the established threshold of %d"
            % (len(independent), self.established_threshold),
            "taskforce", now)
        seat = self._transition(cur, seat, "ESTABLISHED", now,
                                decision_id=decision_id)
        self.plan.emit_workforce_event(
            cur, "seat_established:" + seat["seat_id"], "seat_established",
            seat["seat_id"],
            {"specialist_id": seat["specialist_id"],
             "capability": seat["capability"], "role": seat["role"],
             "demonstrations": ids}, now)
        return seat

    # ------------------------------------------------------ assignments

    def open_assignment(self, sequence_id, attempt_ordinal, selection,
                        objective_id, authorized_by_proposal, now,
                        execution=None, project_id=None, workstream_id=None,
                        assignment_decision=None):
        """Persist one ProjectAssignment — who executes this attempt, on
        which staffing path and why — atomically and BEFORE anything
        launches. It records participation in the project and NOTHING
        about Taskforce membership: no membership and no seat is created
        or changed here, whoever the specialist is.

        Idempotent on (sequence, attempt ordinal): a restart that
        re-derives the staffing gets the row already written, never a
        second assignment. Returns (assignment, created)."""
        assignment_id = assignment_identity(sequence_id, attempt_ordinal)
        specialist = selection["specialist"]
        capability, role = selection["capability"], selection["role"]
        decided_by = selection.get("decided_by") or "TASKFORCE"
        with self.plan.transaction() as cur:
            row = cur.execute(
                "SELECT * FROM project_assignments WHERE assignment_id"
                " = ?", (assignment_id,)).fetchone()
            if row is not None:
                return dict(row), False
            seat = cur.execute(
                "SELECT * FROM taskforce_seats WHERE specialist_id = ?"
                " AND capability = ? AND role = ?",
                (specialist["specialist_id"], capability,
                 role)).fetchone()
            through_seat = seat is not None and \
                seat["status"] in MEMBER_STATES
            standing = seat["status"] if through_seat else OUTSIDE
            path = "RETAINED_SEAT" if through_seat else "OUTSIDE_SPECIALIST"
            seat_id = seat["seat_id"] if through_seat else None
            cur.execute(
                "INSERT INTO project_assignments (assignment_id,"
                " project_id, workstream_id, specialist_id, seat_id,"
                " sequence_id, attempt_ordinal, objective_id, capability,"
                " role, provider, model, selection_path, decided_by,"
                " selected_reason, standing_at_assignment,"
                " authorized_by_proposal, assignment_decision, execution,"
                " status, selected_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SELECTED', ?)",
                (assignment_id, project_id, workstream_id,
                 specialist["specialist_id"], seat_id, sequence_id,
                 attempt_ordinal, objective_id, capability, role,
                 specialist["provider"], specialist["model"], path,
                 decided_by,
                 json.dumps(selection["reason"], sort_keys=True), standing,
                 authorized_by_proposal, assignment_decision,
                 json.dumps(execution, sort_keys=True) if execution
                 else None, now))
            if through_seat:
                cur.execute(
                    "UPDATE taskforce_seats SET last_assignment_id = ?,"
                    " updated_utc = ? WHERE seat_id = ?",
                    (assignment_id, now, seat_id))
            if project_id is not None:
                self._join_team(
                    cur, project_id, specialist["specialist_id"],
                    "RETAINED_MEMBER" if through_seat
                    else "OUTSIDE_SPECIALIST", assignment_id, now)
            if assignment_decision is not None:
                self.plan.resolve_assignment_decision(
                    cur, assignment_decision, "APPLIED", now,
                    assignment_id=assignment_id)
            self.plan.emit_workforce_event(
                cur, "assignment:" + assignment_id,
                "retained_seat_assigned" if through_seat
                else "outside_specialist_assigned",
                assignment_id,
                {"seat_id": seat_id,
                 "specialist_id": specialist["specialist_id"],
                 "capability": capability, "role": role,
                 "sequence_id": sequence_id, "project_id": project_id,
                 "decided_by": decided_by, "standing": standing}, now)
            row = cur.execute(
                "SELECT * FROM project_assignments WHERE assignment_id"
                " = ?", (assignment_id,)).fetchone()
        return dict(row), True

    def _join_team(self, cur, project_id, specialist_id, joined_as,
                   assignment_id, now):
        row = cur.execute(
            "SELECT status FROM project_team WHERE project_id = ? AND"
            " specialist_id = ?", (project_id, specialist_id)).fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO project_team (project_id, specialist_id,"
                " joined_as, status, joined_utc, first_assignment_id)"
                " VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                (project_id, specialist_id, joined_as, now, assignment_id))
        elif row["status"] == "RELEASED":
            cur.execute(
                "UPDATE project_team SET status = 'ACTIVE', ended_utc ="
                " NULL, end_reason = NULL, end_decision_id = NULL WHERE"
                " project_id = ? AND specialist_id = ?",
                (project_id, specialist_id))

    def bind_execution(self, assignment_id, attempt_id, call_id, now,
                       execution=None, route=None):
        """Record the authority-minted attempt and call identity the
        assignment is executing under (references, never copies).

        ``route`` is the execution route the attempt is DISPATCHED on
        (provider, model, effort, the route's name, its native session
        arguments). It is recorded once, with the first binding that
        carries it — which is BEFORE the ledger binds the call to its
        provider — so the assignment row says what this attempt runs on,
        whatever its specialist or the policy is routed to later, and a
        re-entry after a stop repeats exactly that route."""
        with self.plan.transaction() as cur:
            row = cur.execute(
                "SELECT status, execution, provider, model FROM"
                " project_assignments WHERE assignment_id = ?",
                (assignment_id,)).fetchone()
            if row is None:
                raise WorkforceError(
                    "assignment %s does not exist" % assignment_id)
            merged = _loads(row["execution"], {})
            merged.update(execution or {})
            provider, model = row["provider"], row["model"]
            if route and "route" not in merged:
                merged["route"] = {
                    key: route.get(key)
                    for key in ("name", "provider", "model", "effort",
                                "served_as", "provider_args")
                    if route.get(key) is not None}
                provider = route.get("provider") or provider
                model = route.get("model")
            status = row["status"]
            if status == "SELECTED" and call_id is not None:
                status = "DISPATCHED"
            cur.execute(
                "UPDATE project_assignments SET attempt_id ="
                " COALESCE(?, attempt_id), call_id = COALESCE(?, call_id),"
                " execution = ?, status = ?, provider = ?, model = ?"
                " WHERE assignment_id = ?",
                (attempt_id, call_id,
                 json.dumps(merged, sort_keys=True) if merged else None,
                 status, provider, model, assignment_id))

    def set_assignment_status(self, cur, assignment_id, status, now,
                              outcome=None):
        settled = now if status in ("SETTLED", "ABANDONED") else None
        cur.execute(
            "UPDATE project_assignments SET status = ?, outcome ="
            " COALESCE(?, outcome), settled_utc = COALESCE(?, settled_utc)"
            " WHERE assignment_id = ?",
            (status, outcome, settled, assignment_id))

    def assignment_view(self, row):
        """The assignment as the execution path consumes it: the request
        (capability, role), the assigned specialist and its route.
        Provider and model are resolved HERE, downstream of the staffing
        decision; none of it is contract identity."""
        specialist = self.specialist(row["specialist_id"])
        route = _loads(specialist["route"], {}) if specialist else {}
        execution = _loads(row["execution"], {})
        # Provider and model are read from the specialist AS IT IS ROUTED
        # NOW for an attempt no route has been recorded for (a rerouted
        # worker executes its next attempt on the new route). The route
        # is recorded with the assignment just before the attempt is
        # dispatched; from then on the row holds it, and that — never the
        # live policy — is what a re-entry or a relaunch repeats, session
        # arguments included (F-TF-28).
        dispatched = execution.get("route")
        live = not dispatched and row["status"] == "SELECTED" \
            and specialist is not None
        provider = specialist["provider"] if live else row["provider"]
        model = specialist["model"] if live else row["model"]
        effort = route.get("effort")
        provider_args = list(route.get("provider_args") or [])
        route_name = route.get("route_name")
        served_as = "default" if route_name else None
        routing = getattr(self.pool, "routing", None)
        if dispatched:
            effort = dispatched.get("effort")
            route_name = dispatched.get("name")
            served_as = dispatched.get("served_as")
            if "provider_args" in dispatched:
                provider_args = list(dispatched["provider_args"])
            elif routing is not None and route_name in routing.names():
                # An assignment dispatched before session arguments were
                # recorded with the route: the policy's, by route name.
                provider_args = routing.route(route_name)["provider_args"]
        elif live and routing is not None and (
                execution.get("escalation")
                or routing.class_for(row["capability"],
                                     row["role"]) == "escalation"):
            # Escalated execution: the Project Manager asked for it for
            # this assignment, or the organization escalates this kind of
            # work. Either way it runs on the escalation route, whoever
            # was staffed, and no identity changes.
            escalated = routing.resolve("escalation")
            provider, model = escalated["provider"], escalated["model"]
            effort = escalated["effort"]
            provider_args = escalated["provider_args"]
            route_name = escalated["name"]
            served_as = escalated["served_as"]
        return {
            "assignment_id": row["assignment_id"],
            "project_id": row["project_id"],
            "seat_id": row["seat_id"],
            "specialist_id": row["specialist_id"],
            "handle": specialist["handle"] if specialist else None,
            "capability": row["capability"],
            "role": row["role"],
            "provider": provider,
            "model": model,
            "tool_requirements": route.get("tool_requirements", []),
            "context_budget": route.get("context_budget"),
            "expected_output": route.get("expected_output") or "artifacts",
            "completion_policy": route.get("completion_policy"),
            "effort": effort,
            "provider_args": provider_args,
            "route_name": route_name,
            "served_as": served_as,
            "selected_by": STAFFING_PRINCIPAL,
            "staffed_by": STAFFING_FUNCTION,
            "selection_path": row["selection_path"],
            "decided_by": row["decided_by"],
            "standing_at_assignment": row["standing_at_assignment"],
            "execution": _loads(row["execution"], {}),
        }

    # ---------------------------------------------- workforce decisions

    @staticmethod
    def _decision_identity(kind, specialist_id, capability, role,
                           demonstration_ids, proposal_id, reason):
        """The decision IS its identity: the same workforce decision
        re-derived after a restart converges on one row."""
        return "dec:" + canonical_json_digest(
            {"kind": kind, "specialist": specialist_id,
             "capability": capability, "role": role,
             "demonstrations": sorted(demonstration_ids),
             "proposal": proposal_id, "reason": reason})

    def _decision(self, cur, kind, specialist_id, seat_id, capability,
                  role, proposal_id, objective_id, demonstration_ids,
                  reason, rationale, decided_by, now, project_id=None):
        decision_id = self._decision_identity(
            kind, specialist_id, capability, role, demonstration_ids,
            proposal_id, reason)
        cur.execute(
            "INSERT OR IGNORE INTO retention_decisions (decision_id, kind,"
            " specialist_id, seat_id, capability, role, project_id,"
            " proposal_id, objective_id, demonstration_ids, reason,"
            " rationale, decided_by, decided_utc)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, kind, specialist_id, seat_id, capability, role,
             project_id, proposal_id, objective_id,
             json.dumps(sorted(demonstration_ids)), reason, rationale,
             decided_by, now))
        return decision_id

    def retain(self, specialist_id, capability, role, demonstration_ids,
               rationale, now, proposal_id=None, objective_id=None,
               decided_by="planner"):
        """Retain one specialist: create its Taskforce membership (when it
        has none) and its retained seat for this capability and role.

        This is the ONLY way membership comes to exist. The decision must
        bind QUALIFYING demonstrations of exactly this specialist,
        capability and role — retention rests on verified evidence, never
        on anyone's opinion and never on the mere fact that work was
        done. It grants no authority-plane capability.

        Returns {"status": "APPLIED"|"CONVERGED"|"REJECTED", ...}."""
        specialist = self.specialist(specialist_id)
        if specialist is None:
            return {"status": "REJECTED",
                    "note": "specialist %r is not known to the organization"
                            % (specialist_id,)}
        if capability not in CAPABILITIES or not (
                isinstance(role, str) and role):
            return {"status": "REJECTED",
                    "note": "retention names a registered capability and a"
                            " role"}
        seat = self.seat_for(specialist_id, capability, role)
        current = seat["status"] if seat else OUTSIDE
        if current in MEMBER_STATES:
            return {"status": "CONVERGED", "seat": seat,
                    "note": "the specialist already holds this seat (%s)"
                            % current}
        ids = sorted(set(demonstration_ids or []))
        if not ids:
            return {"status": "REJECTED",
                    "note": "retention must bind at least one supporting"
                            " demonstration"}
        for demonstration_id in ids:
            row = self.demonstration(demonstration_id)
            if row is None:
                return {"status": "REJECTED",
                        "note": "demonstration %r does not exist"
                                % (demonstration_id,)}
            if (row["specialist_id"], row["capability"], row["role"]) != (
                    specialist_id, capability, role):
                return {"status": "REJECTED",
                        "note": "demonstration %r is not this specialist's"
                                " %s/%s work"
                                % (demonstration_id, capability, role)}
            if row["outcome"] != "QUALIFYING":
                return {"status": "REJECTED",
                        "note": "demonstration %r is NOT_QUALIFYING and"
                                " cannot support retention"
                                % (demonstration_id,)}
        with self.plan.transaction() as cur:
            membership = cur.execute(
                "SELECT * FROM taskforce_memberships WHERE specialist_id"
                " = ?", (specialist_id,)).fetchone()
            membership_created = membership is None
            membership_id = membership["membership_id"] if membership \
                else membership_identity(specialist_id)
            seat_created = seat is None
            seat_id = seat["seat_id"] if seat else seat_identity(
                specialist_id, capability, role)
            if membership_created:
                # TaskforceMembership begins here and nowhere else.
                cur.execute(
                    "INSERT INTO taskforce_memberships (membership_id,"
                    " specialist_id, status, joined_utc,"
                    " joined_by_decision, updated_utc)"
                    " VALUES (?, ?, 'ACTIVE', ?, ?, ?)",
                    (membership_id, specialist_id, now,
                     self._decision_identity(
                         "RETAIN", specialist_id, capability, role, ids,
                         proposal_id, None), now))
            if seat_created:
                cur.execute(
                    "INSERT INTO taskforce_seats (seat_id, membership_id,"
                    " specialist_id, capability, role, status,"
                    " created_utc, retained_since_utc, updated_utc)"
                    " VALUES (?, ?, ?, ?, ?, 'RETAINED', ?, ?, ?)",
                    (seat_id, membership_id, specialist_id, capability,
                     role, now, now, now))
            decision_id = self._decision(
                cur, "RETAIN", specialist_id, seat_id, capability, role,
                proposal_id, objective_id, ids, None, rationale,
                decided_by, now)
            if seat_created:
                cur.execute(
                    "INSERT INTO seat_history (seat_id, from_status,"
                    " to_status, cause, decision_id, recorded_utc)"
                    " VALUES (?, ?, 'RETAINED', ?, ?, ?)",
                    (seat_id, OUTSIDE,
                     SEAT_TRANSITIONS[(OUTSIDE, "RETAINED")], decision_id,
                     now))
                seat = dict(cur.execute(
                    "SELECT * FROM taskforce_seats WHERE seat_id = ?",
                    (seat_id,)).fetchone())
                self._sync_membership(cur, specialist_id, now)
            else:
                seat = self._transition(cur, seat, "RETAINED", now,
                                        decision_id=decision_id)
            self.plan.emit_workforce_event(
                cur, "seat_retained:" + decision_id, "seat_retained",
                seat["seat_id"],
                {"specialist_id": specialist_id,
                 "capability": capability, "role": role,
                 "demonstrations": ids, "decision_id": decision_id,
                 "membership_id": membership_id,
                 "membership_created": membership_created}, now)
            seat = self._maybe_establish(cur, seat, now)
        return {"status": "APPLIED", "decision_id": decision_id,
                "seat": seat, "membership_id": membership_id,
                "membership_created": membership_created}

    def release(self, specialist_id, capability, role, reason, rationale,
                now, proposal_id=None, objective_id=None,
                disposition="RELEASE", decided_by="planner",
                project_id=None):
        """Release a specialist.

        A MEMBER's seat is released (or DEACTIVATEd, which is reversible);
        its membership follows the seats it still holds. An OUTSIDE
        specialist — one with no retained seat for this work — is let go
        from the project it worked on: the decision is recorded, its
        participation in ``project_id`` ends, and NO membership or seat
        row is created or changed. History, demonstrations and every past
        assignment stay either way."""
        if reason not in RELEASE_REASONS:
            return {"status": "REJECTED",
                    "note": "reason %r is not one of %r"
                            % (reason, RELEASE_REASONS)}
        if disposition not in ("RELEASE", "DEACTIVATE"):
            return {"status": "REJECTED",
                    "note": "disposition must be RELEASE or DEACTIVATE"}
        if self.specialist(specialist_id) is None:
            return {"status": "REJECTED",
                    "note": "specialist %r is not known to the organization"
                            % (specialist_id,)}
        seat = self.seat_for(specialist_id, capability, role)
        ids = [r["demonstration_id"] for r in self.demonstrations(
            specialist_id=specialist_id, capability=capability, role=role)]
        if seat is None or seat["status"] == "RELEASED":
            if disposition == "DEACTIVATE":
                return {"status": "REJECTED",
                        "note": "an outside specialist holds no seat to"
                                " deactivate"}
            busy = [a for a in self.assignments(
                statuses=BUSY_STATES, specialist_id=specialist_id)
                if (a["capability"], a["role"]) == (capability, role)]
            if busy:
                return {"status": "REJECTED",
                        "note": "the specialist has an assignment still"
                                " executing; release it after the work"
                                " settles"}
            with self.plan.transaction() as cur:
                decision_id = self._decision(
                    cur, "RELEASE_OUTSIDE", specialist_id, None, capability,
                    role, proposal_id, objective_id, ids, reason, rationale,
                    decided_by, now, project_id=project_id)
                if project_id is not None and not cur.execute(
                        "SELECT 1 FROM project_assignments WHERE project_id"
                        " = ? AND specialist_id = ? AND status IN (%s)"
                        % ",".join("?" for _ in OPEN_STATES),
                        (project_id, specialist_id)
                        + OPEN_STATES).fetchone():
                    cur.execute(
                        "UPDATE project_team SET status = 'RELEASED',"
                        " ended_utc = ?, end_reason = ?, end_decision_id"
                        " = ? WHERE project_id = ? AND specialist_id = ?"
                        " AND status = 'ACTIVE'",
                        (now, "released_by_project_manager:" + reason,
                         decision_id, project_id, specialist_id))
                self.plan.emit_workforce_event(
                    cur, "outside_released:" + decision_id,
                    "outside_specialist_released", specialist_id,
                    {"specialist_id": specialist_id,
                     "capability": capability, "role": role,
                     "reason": reason, "decision_id": decision_id,
                     "project_id": project_id}, now)
            return {"status": "APPLIED", "decision_id": decision_id,
                    "seat": None, "membership_changed": False}
        target = "INACTIVE" if disposition == "DEACTIVATE" else "RELEASED"
        if seat["status"] == target:
            return {"status": "CONVERGED", "seat": seat,
                    "note": "seat is already %s" % target}
        if (seat["status"], target) not in SEAT_TRANSITIONS:
            return {"status": "REJECTED",
                    "note": "a %s seat cannot move to %s"
                            % (seat["status"], target)}
        if self.busy_count(seat["seat_id"]):
            return {"status": "REJECTED",
                    "note": "the seat has an assignment still executing;"
                            " release it after the work settles"}
        with self.plan.transaction() as cur:
            decision_id = self._decision(
                cur, "DEACTIVATE" if target == "INACTIVE" else "RELEASE",
                specialist_id, seat["seat_id"], capability, role,
                proposal_id, objective_id, ids, reason, rationale,
                decided_by, now, project_id=project_id)
            seat = self._transition(cur, seat, target, now,
                                    decision_id=decision_id)
            self.plan.emit_workforce_event(
                cur, "seat_released:" + decision_id,
                "seat_deactivated" if target == "INACTIVE"
                else "seat_released", seat["seat_id"],
                {"specialist_id": specialist_id,
                 "capability": capability, "role": role,
                 "reason": reason, "decision_id": decision_id}, now)
        return {"status": "APPLIED", "decision_id": decision_id,
                "seat": seat, "membership_changed": True}

    def retention_candidates(self):
        """Outside specialists whose verified, QUALIFYING work no
        workforce decision has considered yet. Being listed here is not a
        standing: nothing happens to a candidate until an explicit
        RETAIN_SPECIALIST (or a release) decides."""
        considered = {}
        for decision in self.decisions():
            key = (decision["specialist_id"], decision["capability"],
                   decision["role"])
            considered.setdefault(key, set()).update(
                _loads(decision["demonstration_ids"], []))
        groups, order = {}, []
        for demo in self.demonstrations(outcome="QUALIFYING"):
            key = (demo["specialist_id"], demo["capability"], demo["role"])
            if demo["demonstration_id"] in considered.get(key, ()):
                continue
            if self.standing(*key) != OUTSIDE:
                continue
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(demo)
        out = []
        for key in order:
            specialist = self.specialist(key[0]) or {}
            seat = self.seat_for(*key)
            out.append({
                "specialist_id": key[0],
                "handle": specialist.get("handle"),
                "capability": key[1], "role": key[2],
                "seat_status": seat["status"] if seat else OUTSIDE,
                "is_member": self.is_member(key[0]),
                "qualifying_demonstrations": [
                    d["demonstration_id"] for d in groups[key]],
                "projects": sorted({d["project_id"] for d in groups[key]
                                    if d["project_id"]})})
        return out

    # ------------------------------------------------------------ views

    def seat_view(self, seat):
        """One retained seat as the organization sees it: what it
        represents, what its holder has demonstrated, what supports it,
        and whether it is free. Authoritative task state is deliberately
        absent — the references lead to it."""
        specialist = self.specialist(seat["specialist_id"]) or {}
        demos = self.demonstrations(
            specialist_id=seat["specialist_id"],
            capability=seat["capability"], role=seat["role"])
        qualifying = [d for d in demos if d["outcome"] == "QUALIFYING"]
        all_capabilities = sorted({
            d["capability"] for d in self.demonstrations(
                specialist_id=seat["specialist_id"], outcome="QUALIFYING")})
        domains = sorted({domain for d in qualifying
                          for domain in _loads(d["system_domains"], [])})
        active = self.assignments(statuses=BUSY_STATES,
                                  seat_id=seat["seat_id"])
        return {
            "seat_id": seat["seat_id"],
            "membership_id": seat["membership_id"],
            "specialist_id": seat["specialist_id"],
            "handle": specialist.get("handle"),
            "status": seat["status"],
            "capability": seat["capability"],
            "role": seat["role"],
            "capabilities": all_capabilities or [seat["capability"]],
            "demonstrated_environments": sorted(
                {d["environment"] for d in qualifying}),
            "system_domains": domains,
            "availability": ("AVAILABLE" if specialist and
                             self.specialist_available(specialist)
                             else "UNAVAILABLE"),
            "active_assignments": [
                {"assignment_id": a["assignment_id"],
                 "sequence_id": a["sequence_id"], "status": a["status"]}
                for a in active],
            "retained_since": seat["retained_since_utc"],
            "last_assignment": seat["last_assignment_id"],
            "performance": {
                "qualifying": len(qualifying),
                "not_qualifying": len(demos) - len(qualifying),
                "demonstrations": [d["demonstration_id"] for d in demos],
            },
        }

    def view(self, recent=12):
        """The compact, deterministic workforce state the Engineer reasons
        over (PlanningView.taskforce): the Taskforce's members and
        retained seats (with availability), the outside specialists a
        project may draw on, demonstrations, and who is working now."""
        seats = self.seats()
        views = [self.seat_view(seat) for seat in seats]
        retained = [v for v in views if v["status"] in MEMBER_STATES]
        inactive = [v for v in views if v["status"] not in MEMBER_STATES]
        seat_status = {(s["specialist_id"], s["capability"], s["role"]):
                       s["status"] for s in seats}
        members = []
        for membership in self.memberships():
            specialist = self.specialist(membership["specialist_id"]) or {}
            members.append({
                "membership_id": membership["membership_id"],
                "specialist_id": membership["specialist_id"],
                "handle": specialist.get("handle"),
                "status": membership["status"],
                "joined": membership["joined_utc"],
                "joined_by_decision": membership["joined_by_decision"],
                "seats": [s["seat_id"] for s in seats
                          if s["specialist_id"]
                          == membership["specialist_id"]]})
        outside = []
        capabilities = {}

        def bucket(capability):
            return capabilities.setdefault(capability, {
                "retained_seats": 0, "retained_available": 0,
                "outside_specialists": 0})

        for specialist in self.specialists():
            specialist_id = specialist["specialist_id"]
            active = specialist["status"] == "ACTIVE"
            open_offers = []
            for capability, role in _loads(specialist["offers"], []):
                entry = bucket(capability)
                status = seat_status.get((specialist_id, capability, role))
                if status in MEMBER_STATES:
                    continue  # the Taskforce resolves this one
                if not active:
                    continue
                entry["outside_specialists"] += 1
                experience = self.experience(specialist_id, capability,
                                             role)
                open_offers.append({
                    "capability": capability, "role": role,
                    "standing": OUTSIDE,
                    "former_seat": status,
                    "qualifying": experience["qualifying"],
                    "not_qualifying": experience["not_qualifying"],
                    "released_for_cause": self.released_for_cause(
                        specialist_id, capability, role)})
            if open_offers:
                outside.append({
                    "specialist_id": specialist_id,
                    "handle": specialist["handle"],
                    "origin": specialist["origin"],
                    "first_seen": specialist["first_seen_utc"],
                    "is_member": self.is_member(specialist_id),
                    "busy": self.specialist_busy_count(specialist_id),
                    "available": self.specialist_available(specialist),
                    "offers": open_offers})
        for seat_view in retained:
            entry = bucket(seat_view["capability"])
            entry["retained_seats"] += 1
            if seat_view["availability"] == "AVAILABLE":
                entry["retained_available"] += 1
        active = [
            {"assignment_id": a["assignment_id"],
             "project_id": a["project_id"], "seat_id": a["seat_id"],
             "specialist_id": a["specialist_id"],
             "sequence_id": a["sequence_id"],
             "capability": a["capability"], "role": a["role"],
             "selection_path": a["selection_path"],
             "decided_by": a["decided_by"], "status": a["status"]}
            for a in self.assignments(statuses=OPEN_STATES)]
        demonstrations = [
            {"demonstration_id": d["demonstration_id"],
             "specialist_id": d["specialist_id"], "seat_id": d["seat_id"],
             "standing": d["standing"],
             "capability": d["capability"], "role": d["role"],
             "environment": d["environment"],
             "sequence_id": d["sequence_id"], "outcome": d["outcome"],
             "criteria": _loads(d["criteria"], {})}
            for d in self.demonstrations(limit=recent)]
        return {
            "members": members,
            "retained_seats": retained,
            "inactive_seats": inactive,
            "outside_specialists": outside,
            "available_capabilities": capabilities,
            "active_assignments": active,
            "recent_demonstrations": demonstrations,
            "retention_candidates": self.retention_candidates(),
            "established_threshold": self.established_threshold,
        }


# ----------------------------------------------------------- the resolver

class TaskforceSeatResolver:
    """Resolves who executes one attempt. Pure over roster state: it
    decides and explains, and writes nothing —
    ``TaskforceRoster.open_assignment`` persists what it returns.

        capability request      -> an available retained seat, or nobody
                                   (the Taskforce resolves ONLY retained
                                   members; it never brings an outside
                                   specialist onto a project)
        the Project Manager's   -> that specialist, when the selection
        explicit selection         satisfies the constraints that bind it
    """

    def __init__(self, roster):
        self.roster = roster

    def _retained_candidates(self, capability, role, environment,
                             subsystem, exclude):
        ranked = []
        for seat in self.roster.seats(statuses=MEMBER_STATES,
                                      capability=capability):
            if role is not None and seat["role"] != role:
                continue
            if seat["specialist_id"] in exclude:
                continue
            specialist = self.roster.specialist(seat["specialist_id"])
            experience = self.roster.experience(
                seat["specialist_id"], capability, seat["role"],
                environment, subsystem)
            ranked.append({
                "seat_id": seat["seat_id"],
                "specialist_id": seat["specialist_id"],
                "handle": specialist["handle"] if specialist else None,
                "status": seat["status"],
                "role": seat["role"],
                "available": self.roster.seat_available(seat, specialist),
                "environment_experience": experience["environment"],
                "system_experience": experience["system"],
                "qualifying": experience["qualifying"],
                "not_qualifying": experience["not_qualifying"],
                "retained_since": seat["retained_since_utc"],
            })
        ranked.sort(key=lambda c: (
            -_STATUS_RANK[c["status"]],
            -c["environment_experience"], -c["system_experience"],
            -c["qualifying"], c["not_qualifying"],
            c["retained_since"] or "", c["seat_id"]))
        return ranked

    def outside_candidates(self, capability, role=None, exclude=()):
        """The outside specialists a Project Manager could select for this
        work, in catalog order (free ones first), each with what makes it
        eligible or not. Listing only — nothing here assigns anyone."""
        exclude = set(exclude or ())
        ranked = []
        for position, specialist in enumerate(self.roster.specialists()):
            specialist_id = specialist["specialist_id"]
            offered = _offered_role(specialist, capability, role)
            if offered is None:
                continue
            if self.roster.standing(specialist_id, capability,
                                    offered) != OUTSIDE:
                continue  # a retained member for this work
            for_cause = self.roster.released_for_cause(
                specialist_id, capability, offered)
            experience = self.roster.experience(specialist_id, capability,
                                                offered)
            active = specialist["status"] == "ACTIVE"
            prohibited = specialist_id in exclude
            ranked.append({
                "specialist_id": specialist_id,
                "handle": specialist["handle"],
                "role": offered,
                "is_member": self.roster.is_member(specialist_id),
                "available": self.roster.specialist_available(specialist),
                "busy": self.roster.specialist_busy_count(specialist_id),
                "qualifying": experience["qualifying"],
                "not_qualifying": experience["not_qualifying"],
                "released_for_cause": for_cause,
                "prohibited": prohibited,
                "eligible": active and not for_cause and not prohibited,
                "position": position,
            })
        ranked.sort(key=lambda c: (not c["eligible"], c["busy"],
                                   c["position"]))
        return ranked

    def check_selection(self, specialist_id, capability, role=None,
                        independent_of=(), continuity=False):
        """Whether the Project Manager may put this specialist on this
        work. Returns None when it may, else the reason it may not.

        ``continuity`` is the same-specialist case (keeping whoever did
        the last attempt), where a retained member is as valid as an
        outside specialist."""
        specialist = self.roster.specialist(specialist_id) \
            if specialist_id else None
        if specialist is None:
            return "specialist %r is not known to the organization" \
                % (specialist_id,)
        if specialist["status"] != "ACTIVE":
            return "specialist %s is withdrawn from the catalog" \
                % specialist_id
        offered = _offered_role(specialist, capability, role)
        if offered is None:
            return "specialist %s does not offer %s/%s" \
                % (specialist_id, capability, role)
        if specialist_id in set(independent_of or ()):
            return ("specialist %s did the work under review; a review is"
                    " staffed independently of its subject"
                    % specialist_id)
        if self.roster.released_for_cause(specialist_id, capability,
                                          offered):
            return ("specialist %s was released for cause from %s/%s and"
                    " is not selected for it again"
                    % (specialist_id, capability, offered))
        if not continuity and self.roster.standing(
                specialist_id, capability, offered) != OUTSIDE:
            return ("specialist %s holds a retained Taskforce seat for"
                    " %s/%s: request the capability from the Taskforce"
                    " instead of selecting it as an outside specialist"
                    % (specialist_id, capability, offered))
        return None

    def _directive(self, directive):
        directive = dict(directive or {})
        mode = directive.get("mode") or "taskforce"
        if mode not in STAFFING_MODES:
            raise WorkforceError("staffing mode %r is not one of %r"
                                 % (mode, STAFFING_MODES))
        return (mode, directive.get("previous_specialist_id"),
                directive.get("specialist_id"))

    def resolve(self, capability, role=None, environment=None,
                subsystem=None, directive=None, independent_of=None):
        """One staffing request -> one selection, or None when nobody can
        be staffed right now (the caller waits or asks the Project
        Manager; nothing is invented).

        With no ``directive`` this is a CAPABILITY REQUEST: the Taskforce
        resolves an available retained seat and never an outside
        specialist. ``directive`` carries the Project Manager's staffing
        word: {"mode": one of STAFFING_MODES, "specialist_id": the named
        outside specialist, "previous_specialist_id": who did the last
        attempt}.

        ``independent_of`` names specialists that may NOT be staffed —
        whoever did the work a review examines. Independence is not a
        preference: it binds the Taskforce's resolution and the Project
        Manager's explicit selection alike, and with nobody else
        available the review waits."""
        if capability not in CAPABILITIES:
            raise WorkforceError(
                "capability %r is not in the registered vocabulary"
                % (capability,))
        mode, previous, named = self._directive(directive)
        request = {"capability": capability, "role": role,
                   "environment": environment, "subsystem": subsystem}
        independent = sorted(set(independent_of or []))
        reason = {"request": request,
                  "directive": {"mode": mode, "specialist_id": named,
                                "previous_specialist_id": previous}
                  if mode != "taskforce" else None}
        if independent:
            reason["independent_of"] = independent

        if mode in PM_SELECTED_MODES:
            target = named if mode == "outside_specialist" else previous
            if self.check_selection(
                    target, capability, role, independent,
                    continuity=(mode == "same_member")) is not None:
                return None
            specialist = self.roster.specialist(target)
            if not self.roster.specialist_available(specialist):
                return None  # the named specialist is busy: the work waits
            offered = _offered_role(specialist, capability, role)
            seat = self.roster.seat_for(target, capability, offered)
            through_seat = seat is not None and \
                seat["status"] in MEMBER_STATES
            reason["path"] = "RETAINED_SEAT" if through_seat \
                else "OUTSIDE_SPECIALIST"
            reason["decided_by"] = "PROJECT_MANAGER"
            reason["chosen"] = {
                "specialist_id": target, "handle": specialist["handle"],
                "seat_id": seat["seat_id"] if through_seat else None}
            # What the Project Manager chose among, on the record.
            reason["outside_candidates"] = self.outside_candidates(
                capability, role, exclude=independent)
            return {"specialist": specialist, "capability": capability,
                    "role": offered, "reason": reason,
                    "decided_by": "PROJECT_MANAGER"}

        exclude = set(independent)
        if mode == "different_member" and previous:
            exclude.add(previous)
        retained = self._retained_candidates(capability, role, environment,
                                             subsystem, exclude)
        choice = next((c for c in retained if c["available"]), None)
        reason["retained_ranked"] = retained
        if choice is None:
            return None
        reason["path"] = "RETAINED_SEAT"
        reason["decided_by"] = "TASKFORCE"
        reason["chosen"] = {"seat_id": choice["seat_id"],
                            "specialist_id": choice["specialist_id"],
                            "handle": choice["handle"]}
        # What the project would have needed WITHOUT this membership: the
        # outside specialist a Project Manager would have had to select.
        alternative = next(
            (c for c in self.outside_candidates(capability, role, exclude)
             if c["eligible"] and c["available"]), None)
        reason["outside_alternative"] = (
            {"specialist_id": alternative["specialist_id"],
             "handle": alternative["handle"]} if alternative else None)
        specialist = self.roster.specialist(choice["specialist_id"])
        return {"specialist": specialist, "capability": capability,
                "role": choice["role"], "reason": reason,
                "decided_by": "TASKFORCE"}

    def explain_unavailable(self, capability, role=None, directive=None,
                            independent_of=None):
        """Why a request cannot be staffed now, and what the Project
        Manager could do about it. ``cause`` is one of:

            no_retained_seat          no member holds this capability/role
            retained_seats_prohibited members hold it, none may be used
                                      (independence, or the last one is
                                      excluded)
            retained_seat_busy        a member holds it and is occupied
            specialist_busy           the named specialist is occupied
            selection_refused         the named specialist may never do
                                      this work (``refused`` says why)
        """
        mode, previous, named = self._directive(directive)
        independent = sorted(set(independent_of or []))
        out = {"capability": capability, "role": role, "mode": mode,
               "independent_of": independent}
        if mode in PM_SELECTED_MODES:
            target = named if mode == "outside_specialist" else previous
            out["specialist_id"] = target
            refusal = self.check_selection(
                target, capability, role, independent,
                continuity=(mode == "same_member"))
            if refusal is not None:
                out["cause"] = "selection_refused"
                out["refused"] = refusal
            else:
                out["cause"] = "specialist_busy"
            return out
        exclude = set(independent)
        if mode == "different_member" and previous:
            exclude.add(previous)
        retained = self._retained_candidates(capability, role, None, None,
                                             exclude)
        out["retained"] = retained
        out["outside_candidates"] = self.outside_candidates(
            capability, role, exclude)
        if retained:
            out["cause"] = "retained_seat_busy"
        elif self._retained_candidates(capability, role, None, None, set()):
            out["cause"] = "retained_seats_prohibited"
        else:
            out["cause"] = "no_retained_seat"
        return out
