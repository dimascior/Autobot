"""The Chief Engineer service: snapshot -> reasoning -> typed proposals
-> compiled candidates -> Registry admission -> recorded outcomes.

The service owns the CYCLE MECHANICS (cap-13 batch proposal, inside the
delegation you granted) and the PRINCIPAL HANDOFF: the planner authors
draft content (cap-07), this deterministic service submits it as the
authority service boundary (``SUBMITTING_PRINCIPAL``), and the Registry
alone freezes (cap-08) — the registered forbidden edge planner->registry
never exists. The REASONING lives behind the ``ChiefEngineerPolicy``
seam, which receives a ``PlanningView`` — plain data, no database
handle, no Registry, no Sequencer — and returns typed proposals. A
deterministic rule policy drives the tests; an LLM-backed policy is a
later provider behind the same seam (the Transport/G2 precedent). The policy CANNOT mutate authority even in error: everything
it touches is a value object.

Restart and idempotency: proposal identity is deterministic (the
candidate's contract_digest for contract classes), the proposal row is
durable BEFORE submission, and an interrupted submission reconciles on
the next cycle — a resubmission converges at the Registry as DUPLICATE
(obj-32) and the service binds the proposal to the live sequence it
finds for that contract_digest. The same durable state change never
yields a second proposal, because planning events are keyed by journal
seq and consumed exactly once.
"""

import json

from contract_library import compute_tiers
from contract_library.grammars import NAME_ID_RE

from .compiler import compile_proposal
from .events import pump_authority_events
from .proposals import (
    CONTRACT_CLASSES,
    SEAT_CLASSES,
    ProposalError,
    proposal_identity,
    validate_proposal,
)
from .proposals_util import canonical_json_digest


# The principal handoff (CORR-15; the registered forbidden edge
# "planner -> registry: the Planner authors drafts; only the Registry
# freezes"). The planner AUTHORS the candidate's content — that is
# cap-07, and the frozen payload says so (authored_by: "planner") —
# but no Registry call ever executes as the planner principal: this
# DETERMINISTIC service submits the draft over the authority service
# boundary, the registered autobot_service -> registry edge
# ("authenticated freeze requests"), and the Registry alone decides
# admission and freeze (cap-08, which forbids the planner). Authorship
# and submission identity are therefore distinct in the ledger: the
# admission row's submitted_by is the service boundary, the record's
# authored_by is the planner.
SUBMITTING_PRINCIPAL = "autobot_service"


class PlanningBudget(Exception):
    """The policy exceeded the per-cycle proposal budget (runaway guard)."""


class PlanningView:
    """Everything the Chief Engineer reasons over, as plain data.

    ``authority`` is the project_state() snapshot (authoritative state);
    ``evidence`` holds content-address references only; ``reasoning`` is
    the plan store's own state. The three information classes stay
    separate here by construction."""

    def __init__(self, authority, snapshot, events, findings, objectives,
                 open_proposals, workstreams):
        self.authority = authority
        self.snapshot = snapshot
        self.events = events
        self.findings = findings
        self.objectives = objectives
        self.open_proposals = open_proposals
        self.workstreams = workstreams


class ChiefEngineerPolicy:
    """The reasoning seam. Implementations return a list of typed
    proposals for one PlanningView; they hold no authority handles."""

    def plan(self, view):
        raise NotImplementedError


def _workstreams(authority):
    obligations = authority["obligations"]
    return {
        "ready": list(obligations["ready_work"]),
        "blocked": list(obligations["blocked_work"]),
        "halted": list(obligations["halted_sequences"]),
        "verification_queue": [e["attempt_id"] for e in
                               obligations["awaiting_verification"]],
        "finalization_queue": list(
            obligations["verified_awaiting_finalization"]),
        "acceptance_queue": list(
            obligations["committed_awaiting_acceptance"]),
        "release_queue": list(obligations["accepted_awaiting_release"]),
        "completed": list(obligations["completed"]),
        "released": list(obligations["dependencies_released"]),
        "in_doubt": list(obligations["intents_in_doubt"]),
        "queued_admissions": list(obligations["queued_admissions"]),
    }


class ChiefEngineerService:
    def __init__(self, plan, registry, environment, clock, policy,
                 max_proposals_per_cycle=16):
        self.plan = plan
        self.registry = registry
        self.environment = environment
        self.clock = clock
        self.policy = policy
        self.max_proposals_per_cycle = max_proposals_per_cycle

    # ------------------------------------------------------------- cycle

    def run_cycle(self, ro_conn, project_state_fn, now):
        """One planning cycle over the read-only authority view. Returns
        a report; opens no cycle when nothing new wakes the planner."""
        pumped = pump_authority_events(ro_conn, self.plan)
        events = self.plan.unconsumed_events()
        findings = self.plan.unconsumed_findings()
        objectives = self.plan.active_objectives()
        new_objectives = [o for o in objectives
                          if o["consumed_in_cycle"] is None]
        if not (events or findings or new_objectives):
            return {"cycle_id": None, "pumped": pumped, "proposals": []}

        class _View(object):
            pass

        ro = _View()
        ro.conn = ro_conn
        authority = project_state_fn(ro)
        snapshot = {"entries": authority["journal"]["entries"],
                    "head_hash": authority["journal"]["head_hash"]}
        cycle_id = self.plan.open_cycle(now, snapshot["entries"],
                                        snapshot["head_hash"])
        view = PlanningView(
            authority=authority, snapshot=snapshot, events=events,
            findings=findings, objectives=objectives,
            open_proposals=self.plan.proposals_by_status(
                "WAITING", "QUEUED_FOR_YOU", "EVALUATED", "SUBMITTED"),
            workstreams=_workstreams(authority))
        proposals = self.policy.plan(view)
        if not isinstance(proposals, list):
            raise ProposalError("the policy must return a list of proposals")
        if len(proposals) > self.max_proposals_per_cycle:
            raise PlanningBudget(
                "%d proposals exceed the per-cycle budget %d"
                % (len(proposals), self.max_proposals_per_cycle))

        known = {o["objective_id"] for o in objectives}
        report = []
        for proposal in proposals:
            validate_proposal(proposal)
            if proposal["objective_id"] not in known:
                raise ProposalError(
                    "proposal binds unknown objective %r"
                    % (proposal["objective_id"],))
            report.extend(self._dispose(proposal, cycle_id, ro_conn, now))

        with self.plan.transaction() as cur:
            self.plan.consume_events(
                cur, [e["journal_seq"] for e in events], cycle_id)
            self.plan.consume_findings(
                cur, [f["finding_id"] for f in findings], cycle_id)
            self.plan.consume_objectives(
                cur, [o["objective_id"] for o in new_objectives], cycle_id)
        self.plan.close_cycle(cycle_id, now,
                              note="%d proposals" % len(report))
        return {"cycle_id": cycle_id, "pumped": pumped,
                "proposals": report, "snapshot": snapshot}

    # ----------------------------------------------------------- dispose

    def _insert_proposal(self, cur, proposal_id, cycle_id, proposal, now,
                         candidate=None, contract_digest=None):
        cur.execute(
            "INSERT OR IGNORE INTO proposals (proposal_id, cycle_id,"
            " class, objective_id, status, rationale, body, candidate,"
            " contract_digest, snapshot_entries, snapshot_head,"
            " created_utc) VALUES (?, ?, ?, ?, 'EVALUATED', ?, ?, ?, ?,"
            " ?, ?, ?)",
            (proposal_id, cycle_id, proposal["class"],
             proposal["objective_id"], proposal["rationale"],
             json.dumps(proposal, sort_keys=True),
             json.dumps(candidate, sort_keys=True) if candidate else None,
             contract_digest, proposal["snapshot"]["entries"],
             proposal["snapshot"].get("head_hash"), now))
        return cur.rowcount == 1

    def _dispose(self, proposal, cycle_id, ro_conn, now):
        cls = proposal["class"]
        if cls == "DECOMPOSE_TASK":
            return self._dispose_decompose(proposal, cycle_id, ro_conn, now)
        if cls in CONTRACT_CLASSES:
            return [self._dispose_contract(proposal, cycle_id, ro_conn, now)]
        if cls in SEAT_CLASSES:
            return [self._dispose_seat(proposal, cycle_id, now)]
        if cls == "WAIT_FOR_AUTHORITY":
            proposal_id = proposal_identity(proposal)
            with self.plan.transaction() as cur:
                self._insert_proposal(cur, proposal_id, cycle_id, proposal,
                                      now)
            self.plan.set_proposal_status(proposal_id, "WAITING", now)
            return [{"proposal_id": proposal_id, "class": cls,
                     "status": "WAITING"}]
        # PROJECT_COMPLETE: close the objective and retire its open
        # authority waits (their subjects are done or moot with it).
        proposal_id = proposal_identity(proposal)
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal, now)
            cur.execute(
                "UPDATE proposals SET status = 'CLOSED', decided_utc = ?"
                " WHERE objective_id = ? AND status = 'WAITING'",
                (now, proposal["objective_id"]))
        self.plan.complete_objective(proposal["objective_id"], now)
        self.plan.set_proposal_status(proposal_id, "APPLIED", now)
        return [{"proposal_id": proposal_id, "class": cls,
                 "status": "APPLIED"}]

    def _dispose_contract(self, proposal, cycle_id, ro_conn, now,
                          candidate=None):
        if candidate is None:
            candidate = compile_proposal(proposal, self.environment,
                                         self.clock)
        _rd, cd, _rh = compute_tiers(candidate)
        proposal_id = proposal_identity(proposal, contract_digest=cd)
        existing = self.plan.proposal(proposal_id)
        if existing is not None and existing["status"] in (
                "ADMITTED", "DUPLICATE", "QUEUED_FOR_YOU", "REJECTED"):
            return {"proposal_id": proposal_id, "class": proposal["class"],
                    "status": existing["status"], "converged": True}
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal,
                                  now, candidate=candidate,
                                  contract_digest=cd)
        if existing is not None and existing["candidate"]:
            # A durable candidate from an interrupted cycle is resubmitted
            # byte-for-byte: identical identity, Registry convergence.
            candidate = json.loads(existing["candidate"])
        self.plan.set_proposal_status(proposal_id, "SUBMITTED", now)
        outcome = self.registry.admit_payload(
            candidate, submitted_by=SUBMITTING_PRINCIPAL)
        status = {
            "NEW_RECORD": "ADMITTED",
            "DUPLICATE": "ADMITTED",  # converged on the existing work
            "QUEUED_FOR_YOU": "QUEUED_FOR_YOU",
            "REJECTED": "REJECTED",
        }[outcome["outcome"]]
        fields = {"admission_id": outcome.get("admission_id"),
                  "task_contract_id": candidate["task_contract_id"]}
        if outcome["outcome"] == "NEW_RECORD":
            fields["sequence_id"] = outcome["sequence_id"]
        elif outcome["outcome"] == "DUPLICATE":
            row = ro_conn.execute(
                "SELECT sequence_id FROM sequences WHERE contract_digest"
                " = ? ORDER BY created_utc LIMIT 1", (cd,)).fetchone()
            if row is not None:
                fields["sequence_id"] = row["sequence_id"]
            fields["note"] = "converged as Registry DUPLICATE (obj-32)"
        elif outcome["outcome"] == "REJECTED":
            fields["note"] = json.dumps(outcome.get("issues", []))[:2000]
        self.plan.set_proposal_status(proposal_id, status, now, **fields)
        return {"proposal_id": proposal_id, "class": proposal["class"],
                "status": status,
                "sequence_id": fields.get("sequence_id")}

    def _dispose_decompose(self, proposal, cycle_id, ro_conn, now):
        results = []
        digests = {}
        for part in proposal["parts"]:
            part_id = part["part_id"]
            if not NAME_ID_RE.match(part_id):
                raise ProposalError(
                    "part_id %r is not a name id (edge ids derive from"
                    " it)" % (part_id,))
            requirement = dict(part["requirement"])
            edges = [dict(e) for e in requirement.get("predecessors", [])]
            for dep in part.get("depends_on_parts", []):
                edges.append({
                    "edge_id": "on-" + dep, "edge_kind": "RECORD",
                    "target": digests[dep],
                    "observation_requirement": "ACCEPTANCE"})
            requirement["predecessors"] = edges
            child = {
                "class": "CREATE_TASK",
                "objective_id": proposal["objective_id"],
                "rationale": "%s [decomposed part %s]"
                             % (proposal["rationale"], part_id),
                "snapshot": proposal["snapshot"],
                "provenance": proposal.get("provenance", {}),
                "requirement": requirement,
            }
            validate_proposal(child)
            candidate = compile_proposal(child, self.environment, self.clock)
            _rd, _cd, rh = compute_tiers(candidate)
            digests[part_id] = rh
            results.append(self._dispose_contract(
                child, cycle_id, ro_conn, now, candidate=candidate))
        marker_id = "prop:" + canonical_json_digest(
            {"class": "DECOMPOSE_TASK",
             "children": sorted(r["proposal_id"] for r in results)})
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, marker_id, cycle_id, proposal, now)
        self.plan.set_proposal_status(
            marker_id, "APPLIED", now,
            note=json.dumps([r["proposal_id"] for r in results]))
        results.append({"proposal_id": marker_id, "class": "DECOMPOSE_TASK",
                        "status": "APPLIED"})
        return results

    def _dispose_seat(self, proposal, cycle_id, now):
        proposal_id = proposal_identity(proposal)
        existing = self.plan.proposal(proposal_id)
        if existing is not None:
            return {"proposal_id": proposal_id, "class": proposal["class"],
                    "status": existing["status"], "converged": True}
        seat_id = "st_" + canonical_json_digest(
            {"proposal": proposal_id})[:32]
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal, now)
            cur.execute(
                "INSERT OR IGNORE INTO seats (seat_id, sequence_id, role,"
                " capability, provider, cycle_id, authorized_by_proposal,"
                " status, created_utc, updated_utc)"
                " VALUES (?, ?, ?, ?, 'unassigned', ?, ?, 'AUTHORIZED',"
                " ?, ?)",
                (seat_id, proposal["sequence_id"],
                 proposal.get("role", ""), proposal["capability"],
                 cycle_id, proposal_id, now, now))
        self.plan.set_proposal_status(proposal_id, "APPLIED", now,
                                      sequence_id=proposal["sequence_id"],
                                      note=seat_id)
        return {"proposal_id": proposal_id, "class": proposal["class"],
                "status": "APPLIED", "seat_id": seat_id}
