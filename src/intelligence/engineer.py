"""The Engineer service: snapshot -> reasoning -> typed proposals
-> compiled candidates -> Registry admission -> recorded outcomes.

The Engineer is Autobot's technical problem-solving intelligence: it
investigates problems, develops solutions, weighs tradeoffs and
determines the technical work a solution requires. When realizing a
solution needs coordinated work, the Engineer assumes the PROJECT MANAGER
role for that project: it opens the project, lays out its workstreams and
their dependencies, and composes a temporary team — retained Taskforce
members (it asks the Taskforce for a capability; the Taskforce resolves a
member) and outside specialists (it selects them explicitly, for this
project). Both roles speak through the same seam and the same typed
proposals; ``proposals.ACTING_AS`` says which role each class belongs to.

Three things stay separate throughout:

    the WORK        a TaskContractRecord: frozen, and independent of
                    provider, model and specialist identity
    the PROJECT     temporary: workstreams, project assignments, a team
                    that is dissolved when the project closes
    the TASKFORCE   persistent: membership and retained seats, earned only
                    through an explicit, evidence-backed retention decision

The service owns the CYCLE MECHANICS (cap-13 batch proposal, inside the
delegation you granted) and the PRINCIPAL HANDOFF: the planner authors
draft content (cap-07), this deterministic service submits it as the
authority service boundary (``SUBMITTING_PRINCIPAL``), and the Registry
alone freezes (cap-08) — the registered forbidden edge planner->registry
never exists. The REASONING lives behind the ``EngineerPolicy`` seam,
which receives a ``PlanningView`` — plain data, no database handle, no
Registry, no Sequencer — and returns typed proposals. Deterministic rule
policies drive the tests; the production policy
(``policy.ReasoningEngineerPolicy``) reasons with a model behind the same
seam. The policy CANNOT mutate authority even in error: everything it
touches is a value object.

What the Engineer decides, and what it does not. It determines what
engineering work exists and why, how it decomposes, what capability each
piece needs, what depends on what and which findings need follow-up. As
Project Manager it decides who is on the project — a capability request
the Taskforce satisfies with a retained member, or an outside specialist
it names — and, as a workforce decision, whether a specialist's
demonstrated work earns it Taskforce membership. It never selects a
provider or a model (routing is resolved downstream of staffing), never
puts a specialist into a work requirement (staffing is not contract
identity), never certifies its own conclusions (the verifiers do), and
never mutates authority (the Registry and Sequencer do).

Reasoning is exactly-once per stimulus batch. The policy's proposals are
made durable BEFORE they are disposed (``cycle_reasoning``), keyed by the
identity of what woke the planner; a cycle interrupted after that point
is REPLAYED from the record instead of reasoned again, so a model-backed
policy cannot turn one stimulus into two different batches of work.

The budget. Every objective carries an engineering-work budget (cap-47:
the limits are yours): contracts created, attempts launched, outside
specialists sourced, repairs, retention decisions. Usage is derived from
durable rows; a proposal past a limit is recorded as refused with the
reason, and the planner is told.

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

from . import query
from .compiler import compile_proposal, evidence_requirements_for
from .events import pump_authority_events
from .prompts import SPECIALIST_PROMPT_INPUT, compose_specialist_prompt
from .proposals import (
    CONTRACT_CLASSES,
    SEAT_CLASSES,
    WORKFORCE_CLASSES,
    WORKSTREAM_KEY_RE,
    ProposalError,
    proposal_identity,
    validate_proposal,
)
from .proposals_util import canonical_json_digest
from .workforce import TaskforceSeatResolver

# The id of the standing project-manager staffing rule (``EngineerPolicy
# .staff``'s default), recorded in the provenance of every decision it
# makes.
STANDING_RULE_ID = "standing-outside-rule/v1"

# Sequence states in which a workstream is waiting to start an attempt —
# the only states in which a project assignment can be decided for it.
WAITING_TO_START = ("DECLARED", "BLOCKED", "READY")

# The note prefix of a proposal refused by the objective budget. Unlike a
# Registry rejection it is not final: once you raise the limit, the same
# engineering intent may be proposed again.
BUDGET_NOTE = "budget: "


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
    """Everything the Engineer reasons over, as plain data.

    ``authority`` is the project_state() snapshot (authoritative state);
    ``evidence`` holds content-address references only; ``reasoning`` is
    the plan store's own state. The three information classes stay
    separate here by construction.

    ``taskforce`` is the persistent organization (members, retained seats
    and their availability, inactive seats, the outside specialists a
    project may draw on, available retained capabilities, active
    assignments, recent demonstrations, the specialists whose verified
    work awaits a retention decision); ``workforce_events`` are the
    workforce changes that woke this cycle; ``budget`` is each
    objective's engineering-work budget and usage; ``engineering`` is the
    per-objective artifact projection (workstreams, dependencies,
    accepted results, failed assertions, open questions, decisions
    taken). ``projects`` is the Engineer's project
    state, per objective: the current project and its solution, its
    workstreams and their dependencies, the project assignments and the
    temporary team, and the workstreams that wait for a staffing
    decision (with the outside specialists that could be selected). All
    of it is plain data built by the query layer — the policy still holds
    no handle of any kind."""

    def __init__(self, authority, snapshot, events, findings, objectives,
                 open_proposals, workstreams, taskforce=None,
                 workforce_events=None, budget=None, engineering=None,
                 projects=None):
        self.authority = authority
        self.snapshot = snapshot
        self.events = events
        self.findings = findings
        self.objectives = objectives
        self.open_proposals = open_proposals
        self.workstreams = workstreams
        self.taskforce = taskforce or {}
        self.workforce_events = list(workforce_events or [])
        self.budget = budget or {}
        self.engineering = engineering or {}
        self.projects = projects or {}


def standing_staffing_rule(view, proposals=()):
    """The standing project-manager staffing rule.

    For every workstream the Taskforce cannot staff from a retained seat
    (``PlanningView.projects[...]["needs_staffing"]``) it selects the first
    ELIGIBLE outside specialist in catalog order, free ones first, and
    returns that selection as an explicit ASSIGN_SPECIALIST proposal. It
    is a Project Manager decision like any other — typed, recorded, bound
    by the same constraints (a review stays independent of its subject) —
    made by a declared rule instead of by reasoning. Deterministic
    policies and the operator's single-task command use it; a reasoning
    policy selects for itself.

    A workstream the batch already staffs is left alone."""
    decided = set()
    for proposal in proposals or ():
        if proposal.get("class") in SEAT_CLASSES \
                and proposal.get("staffing"):
            decided.add(proposal.get("sequence_id"))
    out = []
    chosen = {}
    for objective_id in sorted(view.projects):
        project = view.projects[objective_id] or {}
        for need in project.get("needs_staffing", []):
            if need["sequence_id"] in decided:
                continue
            candidates = sorted(
                (c for c in need["outside_candidates"] if c["eligible"]),
                key=lambda c: (c["busy"] + chosen.get(c["specialist_id"],
                                                      0), c["position"]))
            if not candidates:
                continue  # nobody may be selected: the work waits, visibly
            pick = candidates[0]
            chosen[pick["specialist_id"]] = chosen.get(
                pick["specialist_id"], 0) + 1
            out.append({
                "class": "ASSIGN_SPECIALIST",
                "objective_id": objective_id,
                "rationale": "standing project-manager rule: no retained"
                             " Taskforce seat can take %s/%s (%s); the"
                             " first eligible outside specialist in"
                             " catalog order is selected for this project"
                             % (need["capability"], need["role"],
                                need["cause"]),
                "sequence_id": need["sequence_id"],
                "capability": need["capability"],
                "attempt_ordinal": need["attempt_ordinal"],
                "staffing": {"mode": "outside_specialist",
                             "specialist_id": pick["specialist_id"]},
                "snapshot": {"entries": view.snapshot["entries"],
                             "head_hash": view.snapshot["head_hash"]},
                "provenance": {"findings": [], "events": [],
                               "workforce": {"rule": STANDING_RULE_ID}},
            })
    return out


class EngineerPolicy:
    """The reasoning seam. Implementations return typed proposals for one
    PlanningView; they hold no authority handles.

    The two methods mirror the Engineer's two roles. ``plan`` is the whole
    answer of a reasoning policy — technical work and project-management
    decisions alike. ``staff`` is where a policy that does not reason
    about staffing gets its Project Manager decisions from: by default the
    standing rule."""

    def plan(self, view):
        raise NotImplementedError

    def staff(self, view, proposals=()):
        return standing_staffing_rule(view, proposals)


# The name this seam carried before the organizational model was aligned
# (the Engineer was called the Chief Engineer). Kept for existing callers.
ChiefEngineerPolicy = EngineerPolicy


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


class EngineerService:
    def __init__(self, plan, registry, environment, clock, policy,
                 max_proposals_per_cycle=16, store=None, roster=None,
                 repository_context=None, evidence=None):
        """``store`` (the content store) turns on prompt freezing: every
        contract this service submits declares its composed specialist
        prompt as a specification input. ``roster`` is the Taskforce
        roster the workforce view is read from and workforce decisions
        are applied to; without one the Engineer sees no workforce and may
        make no staffing or workforce decision. ``evidence`` is the
        content store as a READ source for the view: with it, a failed
        attempt carries what the provider itself reported."""
        self.plan = plan
        self.registry = registry
        self.environment = environment
        self.clock = clock
        self.policy = policy
        self.max_proposals_per_cycle = max_proposals_per_cycle
        self.store = store
        self.evidence = evidence if evidence is not None else store
        self.roster = roster
        # Read-only over the roster: used to CHECK a Project Manager's
        # explicit selection, never to make one.
        self.resolver = TaskforceSeatResolver(roster) if roster else None
        self.repository_context = repository_context

    # ------------------------------------------------------------- cycle

    def build_view(self, ro_conn, project_state_fn, events, findings,
                   objectives, workforce_events):
        class _View(object):
            pass

        ro = _View()
        ro.conn = ro_conn
        authority = project_state_fn(ro)
        snapshot = {"entries": authority["journal"]["entries"],
                    "head_hash": authority["journal"]["head_hash"]}
        budget = {}
        for objective in objectives:
            objective_id = objective["objective_id"]
            budget[objective_id] = {
                "limits": self.plan.budget_limits(objective_id),
                "used": self.plan.budget_usage(objective_id)}
        return PlanningView(
            authority=authority, snapshot=snapshot, events=events,
            findings=findings, objectives=objectives,
            open_proposals=self.plan.proposals_by_status(
                "WAITING", "QUEUED_FOR_YOU", "EVALUATED", "SUBMITTED"),
            workstreams=_workstreams(authority),
            taskforce=self.roster.view() if self.roster else None,
            workforce_events=workforce_events,
            budget=budget,
            engineering=query.engineering_view(
                self.plan, ro_conn, objectives),
            projects=query.project_views(
                self.plan, ro_conn, objectives, self.roster,
                store=self.evidence))

    def run_cycle(self, ro_conn, project_state_fn, now):
        """One planning cycle over the read-only authority view. Returns
        a report; opens no cycle when nothing new wakes the planner."""
        pumped = pump_authority_events(ro_conn, self.plan)
        events = self.plan.unconsumed_events()
        findings = self.plan.unconsumed_findings()
        workforce_events = self.plan.unconsumed_workforce_events()
        objectives = self.plan.active_objectives()
        new_objectives = [o for o in objectives
                          if o["consumed_in_cycle"] is None]
        if not (events or findings or new_objectives or workforce_events):
            return {"cycle_id": None, "pumped": pumped, "proposals": []}

        view = self.build_view(ro_conn, project_state_fn, events, findings,
                               objectives, workforce_events)
        snapshot = view.snapshot
        # The identity of what woke the planner: exactly this batch of
        # authority events, findings, workforce events and new objectives.
        input_digest = canonical_json_digest({
            "events": [e["journal_seq"] for e in events],
            "findings": sorted(f["finding_id"] for f in findings),
            "workforce": [e["event_id"] for e in workforce_events],
            "objectives": sorted(o["objective_id"]
                                 for o in new_objectives)})
        interrupted = self.plan.interrupted_reasoning(input_digest)
        cycle_id = self.plan.open_cycle(now, snapshot["entries"],
                                        snapshot["head_hash"])
        policy_id = getattr(self.policy, "policy_id",
                            type(self.policy).__name__)
        if interrupted is not None:
            # A cycle over exactly this batch already reasoned and was
            # interrupted before it closed: replay its recorded
            # proposals. The policy is not asked again.
            proposals = json.loads(interrupted["proposals"])
            exchange = json.loads(interrupted["exchange"]) \
                if interrupted["exchange"] else None
            replayed_from = interrupted["cycle_id"]
            policy_id = interrupted["policy_id"]
        else:
            if hasattr(self.policy, "last_exchange"):
                self.policy.last_exchange = None
            proposals = self.policy.plan(view)
            if not isinstance(proposals, list):
                raise ProposalError(
                    "the policy must return a list of proposals")
            # The Project Manager's staffing decisions for workstreams the
            # Taskforce cannot staff from a retained seat: the policy's
            # own (``staff``), or the standing rule for a policy that has
            # none. Either way they are typed proposals like the rest.
            staff = getattr(self.policy, "staff", None)
            staffing = staff(view, proposals) if staff is not None \
                else standing_staffing_rule(view, proposals)
            proposals = proposals + list(staffing or [])
            exchange = getattr(self.policy, "last_exchange", None)
            replayed_from = None
        if not isinstance(proposals, list):
            raise ProposalError("the policy must return a list of proposals")
        if len(proposals) > self.max_proposals_per_cycle:
            raise PlanningBudget(
                "%d proposals exceed the per-cycle budget %d"
                % (len(proposals), self.max_proposals_per_cycle))
        known = {o["objective_id"] for o in objectives}
        for proposal in proposals:
            validate_proposal(proposal)
            if proposal["objective_id"] not in known:
                raise ProposalError(
                    "proposal binds unknown objective %r"
                    % (proposal["objective_id"],))
        # Durable BEFORE disposal: from here on this stimulus batch has
        # one answer, whatever happens to the process.
        self.plan.record_reasoning(cycle_id, input_digest, policy_id,
                                   proposals, exchange, now)
        if replayed_from is not None:
            self.plan.supersede_cycle(replayed_from, cycle_id, now)

        report = []
        for proposal in proposals:
            try:
                report.extend(
                    self._dispose(proposal, cycle_id, ro_conn, now))
            except ProposalError as exc:
                # A well-formed proposal that cannot be compiled or
                # applied is refused and recorded with the reason; it
                # never takes the cycle down — this stimulus batch
                # already has its one recorded answer.
                refused_id = "prop:" + canonical_json_digest(
                    {"refused": json.dumps(proposal, sort_keys=True,
                                           default=str)})
                report.append(self._reject_decision(
                    refused_id, cycle_id, proposal, str(exc)[:2000], now,
                    "proposal_refused"))

        with self.plan.transaction() as cur:
            self.plan.consume_events(
                cur, [e["journal_seq"] for e in events], cycle_id)
            self.plan.consume_findings(
                cur, [f["finding_id"] for f in findings], cycle_id)
            self.plan.consume_workforce_events(
                cur, [e["event_id"] for e in workforce_events], cycle_id)
            self.plan.consume_objectives(
                cur, [o["objective_id"] for o in new_objectives], cycle_id)
        self.plan.close_cycle(cycle_id, now,
                              note="%d proposals" % len(report))
        return {"cycle_id": cycle_id, "pumped": pumped,
                "proposals": report, "snapshot": snapshot,
                "replayed_from": replayed_from}

    # ---------------------------------------------------- prompt freezing

    def _finding_summaries(self, finding_ids):
        out = []
        for finding_id in finding_ids:
            row = self.plan.conn.execute(
                "SELECT * FROM findings WHERE finding_id = ?",
                (finding_id,)).fetchone()
            if row is None:
                continue
            out.append({
                "finding_id": row["finding_id"],
                "finding_class": row["finding_class"],
                "affected_component": row["affected_component"],
                "statement": row["statement"] or "",
                "significance": row["significance"],
                "suggested_follow_up": row["suggested_follow_up"] or ""})
        return out

    def _freeze_prompt(self, proposal):
        """Compose the complete specialist prompt for one contract-class
        proposal, put its exact bytes in the content store and bind their
        address as the contract's declared specification input. Pure over
        the proposal: the same engineering intent yields the same bytes,
        so contract identity — and with it proposal identity — is stable
        across re-derivation. A proposal that already declares its
        specification inputs is left exactly as authored."""
        if self.store is None:
            return proposal
        requirement = proposal["requirement"]
        if "specification_inputs" in requirement:
            return proposal
        read_only = proposal["class"] in ("INVESTIGATE", "REQUEST_REVIEW") \
            or requirement.get("finalization_class") == "READ_ONLY"
        fin_class = "READ_ONLY" if read_only else "SOURCE_MODIFYING"
        scope = requirement.get(
            "allowed_scope", self.environment.defaults["allowed_scope"])
        objective = self.plan.objective(proposal["objective_id"]) or {}
        prompt = compose_specialist_prompt(
            requirement["guidance"], fin_class,
            allowed_paths=scope.get("paths", []),
            objective=objective.get("text"),
            task=requirement["objective"],
            predecessor_findings=self._finding_summaries(
                proposal.get("provenance", {}).get("findings", [])),
            repository_context=self.repository_context,
            governed="COMMAND_GOVERNANCE" in evidence_requirements_for(
                self.environment, fin_class),
            # Work with frozen predecessors (or a record it reviews) is
            # told where their accepted results will be.
            predecessor_inputs=bool(requirement.get("predecessors")
                                    or proposal.get("review_of")),
            # A review says whether it examined the work (D-TF-32).
            review=proposal["class"] == "REQUEST_REVIEW")
        address = self.store.put(prompt.encode("utf-8"))
        bound = dict(proposal)
        bound["requirement"] = dict(requirement)
        bound["requirement"]["specification_inputs"] = {
            "name_to_exact_byte_sha256": {SPECIALIST_PROMPT_INPUT: address}}
        return bound

    # ------------------------------------------------------------- budget

    def _budget_refusal(self, proposal, dimension):
        """None when the objective has headroom in ``dimension``, else
        the refusal note."""
        objective_id = proposal["objective_id"]
        if self.plan.budget_headroom(objective_id, dimension) > 0:
            return None
        limits = self.plan.budget_limits(objective_id)
        used = self.plan.budget_usage(objective_id)
        return "%sobjective %s has used %d of %d %s" % (
            BUDGET_NOTE, objective_id, used[dimension],
            limits["max_" + dimension], dimension)

    def _refuse_for_budget(self, proposal_id, cycle_id, proposal, note,
                           dimension, now, **insert):
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal,
                                  now, **insert)
            self.plan.emit_workforce_event(
                cur, "budget:%s:%s:%s" % (proposal["objective_id"],
                                          dimension, proposal_id),
                "budget_exhausted", proposal["objective_id"],
                {"dimension": dimension, "proposal_id": proposal_id,
                 "class": proposal["class"]}, now)
        self.plan.set_proposal_status(proposal_id, "REJECTED", now,
                                      note=note)
        return {"proposal_id": proposal_id, "class": proposal["class"],
                "status": "REJECTED", "note": note}

    # ----------------------------------------------------------- dispose

    def _insert_proposal(self, cur, proposal_id, cycle_id, proposal, now,
                         candidate=None, contract_digest=None):
        effective_cycle = cycle_id
        if effective_cycle is None and \
                hasattr(self.plan, "orchestration_sentinel_cycle"):
            effective_cycle = self.plan.orchestration_sentinel_cycle()
        origin_kind = proposal.get("origin_kind")
        origin_id = proposal.get("origin_id")
        if origin_kind is None and cycle_id is not None:
            origin_kind = "ENGINEER_CYCLE"
            origin_id = str(cycle_id)
        cur.execute(
            "INSERT OR IGNORE INTO proposals (proposal_id, cycle_id,"
            " class, objective_id, status, rationale, body, candidate,"
            " contract_digest, snapshot_entries, snapshot_head,"
            " created_utc, origin_kind, origin_id)"
            " VALUES (?, ?, ?, ?, 'EVALUATED', ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?)",
            (proposal_id, effective_cycle, proposal["class"],
             proposal["objective_id"], proposal["rationale"],
             json.dumps(proposal, sort_keys=True),
             json.dumps(candidate, sort_keys=True) if candidate else None,
             contract_digest, proposal["snapshot"]["entries"],
             proposal["snapshot"].get("head_hash"), now,
             origin_kind, origin_id))
        return cur.rowcount == 1

    def _dispose(self, proposal, cycle_id, ro_conn, now):
        cls = proposal["class"]
        if cls == "DECOMPOSE_TASK":
            return self._dispose_decompose(proposal, cycle_id, ro_conn, now)
        if cls in CONTRACT_CLASSES:
            return [self._dispose_contract(proposal, cycle_id, ro_conn, now)]
        if cls == "OPEN_PROJECT":
            return [self._dispose_project(proposal, cycle_id, now)]
        if cls in SEAT_CLASSES:
            return [self._dispose_seat(proposal, cycle_id, ro_conn, now)]
        if cls in WORKFORCE_CLASSES:
            return [self._dispose_workforce(proposal, cycle_id, now)]
        if cls == "WAIT_FOR_AUTHORITY":
            proposal_id = proposal_identity(proposal)
            with self.plan.transaction() as cur:
                self._insert_proposal(cur, proposal_id, cycle_id, proposal,
                                      now)
            self.plan.set_proposal_status(proposal_id, "WAITING", now)
            return [{"proposal_id": proposal_id, "class": cls,
                     "status": "WAITING"}]
        # PROJECT_COMPLETE: close the project and the objective it
        # realized, and retire the objective's open authority waits (their
        # subjects are done or moot with it). Closing the project
        # dissolves its team — temporary participation ends here — while
        # Taskforce membership and seats are not project state and stay.
        proposal_id = proposal_identity(proposal)
        project = self.plan.active_project(proposal["objective_id"])
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal, now)
            cur.execute(
                "UPDATE proposals SET status = 'CLOSED', decided_utc = ?"
                " WHERE objective_id = ? AND status = 'WAITING'",
                (now, proposal["objective_id"]))
        self.plan.complete_objective(
            proposal["objective_id"], now, project_status="COMPLETED",
            closed_by=proposal_id, reason="project_completed")
        self.plan.set_proposal_status(proposal_id, "APPLIED", now)
        return [{"proposal_id": proposal_id, "class": cls,
                 "status": "APPLIED",
                 "project_id": project["project_id"] if project else None}]

    # ----------------------------------------------------------- projects

    def _dispose_project(self, proposal, cycle_id, now):
        """The Engineer assumes the Project Manager role: open the project
        that realizes its solution to the objective. One project realizes
        an objective at a time; a project the service opened implicitly
        (work was proposed before any project) is adopted."""
        proposal_id = proposal_identity(proposal)
        if self.plan.proposal(proposal_id) is not None:
            existing = self.plan.proposal(proposal_id)
            return {"proposal_id": proposal_id, "class": proposal["class"],
                    "status": existing["status"], "converged": True}
        spec = proposal["project"]
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal, now)
            project, created = self.plan.open_project(
                cur, proposal["objective_id"], spec["title"],
                spec["solution"], "PROJECT_MANAGER", proposal_id, now)
            adopted = (not created) and self.plan.adopt_project(
                cur, project["project_id"], spec["title"],
                spec["solution"], proposal_id)
        if created or adopted:
            self.plan.set_proposal_status(proposal_id, "APPLIED", now,
                                          note=project["project_id"])
            return {"proposal_id": proposal_id, "class": proposal["class"],
                    "status": "APPLIED",
                    "project_id": project["project_id"]}
        note = ("objective %s already has the active project %s; one"
                " project realizes an objective at a time"
                % (proposal["objective_id"], project["project_id"]))
        self.plan.set_proposal_status(proposal_id, "REJECTED", now,
                                      note=note)
        return {"proposal_id": proposal_id, "class": proposal["class"],
                "status": "REJECTED", "note": note}

    def _bind_workstream(self, cur, proposal_id, proposal, now):
        """Every piece of work belongs to the objective's project: record
        the workstream this proposal creates. When work is proposed before
        any project was opened, the service opens the project implicitly
        (a policy that never assumes the Project Manager role still gets
        its work organized)."""
        objective = self.plan.objective(proposal["objective_id"]) or {}
        title = (objective.get("text") or proposal["objective_id"])[:160]
        project, _created = self.plan.open_project(
            cur, proposal["objective_id"], title, None, "IMPLICIT",
            proposal_id, now)
        return self.plan.add_workstream(
            cur, project["project_id"], proposal.get("workstream"),
            proposal_id, now)

    def _dispose_contract(self, proposal, cycle_id, ro_conn, now,
                          candidate=None):
        if candidate is None:
            proposal = self._freeze_prompt(proposal)
            candidate = compile_proposal(proposal, self.environment,
                                         self.clock)
        _rd, cd, _rh = compute_tiers(candidate)
        proposal_id = proposal_identity(proposal, contract_digest=cd)
        existing = self.plan.proposal(proposal_id)
        budget_refused = existing is not None and \
            existing["status"] == "REJECTED" and \
            (existing["note"] or "").startswith(BUDGET_NOTE)
        if existing is not None and not budget_refused and \
                existing["status"] in ("ADMITTED", "DUPLICATE",
                                       "QUEUED_FOR_YOU", "REJECTED"):
            return {"proposal_id": proposal_id, "class": proposal["class"],
                    "status": existing["status"], "converged": True}
        if existing is None or budget_refused:
            # A NEW contract spends the objective's budget; an interrupted
            # submission being reconciled already holds its row and is
            # never refused here.
            refusal = self._budget_refusal(proposal, "contracts")
            if refusal is not None:
                return self._refuse_for_budget(
                    proposal_id, cycle_id, proposal, refusal, "contracts",
                    now, candidate=candidate, contract_digest=cd)
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal,
                                  now, candidate=candidate,
                                  contract_digest=cd)
            workstream = self._bind_workstream(cur, proposal_id, proposal,
                                               now)
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
        if budget_refused and "note" not in fields:
            fields["note"] = "admitted after a budget grant"
        self.plan.set_proposal_status(proposal_id, status, now, **fields)
        return {"proposal_id": proposal_id, "class": proposal["class"],
                "status": status,
                "sequence_id": fields.get("sequence_id"),
                "project_id": workstream["project_id"],
                "workstream": workstream["workstream_key"]}

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
            reviewed = part.get("review_of_part")
            edges = [dict(e) for e in requirement.get("predecessors", [])]
            for dep in part.get("depends_on_parts", []):
                if dep == reviewed:
                    continue  # the review edge below already binds it
                edges.append({
                    "edge_id": "on-" + dep, "edge_kind": "RECORD",
                    "target": digests[dep],
                    "observation_requirement": "ACCEPTANCE"})
            requirement["predecessors"] = edges
            child = {
                "class": "REQUEST_REVIEW" if reviewed else "CREATE_TASK",
                "objective_id": proposal["objective_id"],
                "rationale": "%s [decomposed part %s]"
                             % (proposal["rationale"], part_id),
                "snapshot": proposal["snapshot"],
                "provenance": proposal.get("provenance", {}),
                "requirement": requirement,
            }
            if reviewed:
                # A review part is a REQUEST_REVIEW of the earlier part's
                # frozen record: READ_ONLY, blocked on that record's
                # acceptance, and independent of whoever did the work.
                requirement.pop("finalization_class", None)
                child["review_of"] = {"record_hash": digests[reviewed]}
            if WORKSTREAM_KEY_RE.match(part_id):
                # The part is the workstream: its id is the key a project
                # assignment in the same batch can name.
                child["workstream"] = part_id
            validate_proposal(child)
            child = self._freeze_prompt(child)
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

    def _resolve_workstream(self, proposal):
        """A next-attempt proposal may name its workstream by the Project
        Manager's key (the work may have been proposed in this same
        batch): resolve it to the sequence. Returns (proposal, problem)."""
        if proposal.get("sequence_id"):
            return proposal, None
        key = proposal.get("workstream")
        project = self.plan.active_project(proposal["objective_id"])
        workstream = self.plan.workstream_by_key(
            project["project_id"], key) if project else None
        if workstream is None:
            return proposal, ("workstream %r is not a workstream of this"
                              " objective's project" % (key,))
        origin = self.plan.proposal(workstream["proposal_id"]) or {}
        if not origin.get("sequence_id"):
            return proposal, ("workstream %r has no admitted work to staff"
                              " (its proposal is %s)"
                              % (key, origin.get("status")))
        resolved = dict(proposal)
        resolved["sequence_id"] = origin["sequence_id"]
        return resolved, None

    def _reject_decision(self, proposal_id, cycle_id, proposal, note, now,
                         event_kind=None):
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal, now)
            if event_kind:
                # The Engineer is told: a refused decision is a stimulus,
                # so the next cycle sees it and decides again.
                self.plan.emit_workforce_event(
                    cur, "%s:%s" % (event_kind, proposal_id), event_kind,
                    proposal.get("sequence_id") or proposal["objective_id"],
                    {"proposal_id": proposal_id, "class": proposal["class"],
                     "staffing": proposal.get("staffing"), "note": note},
                    now)
        fields = {"note": note}
        if proposal.get("sequence_id"):
            fields["sequence_id"] = proposal["sequence_id"]
        self.plan.set_proposal_status(proposal_id, "REJECTED", now,
                                      **fields)
        return {"proposal_id": proposal_id, "class": proposal["class"],
                "status": "REJECTED", "note": note}

    def _pending_authorization(self, sequence_id):
        """The Engineer's word already given for a workstream's next
        attempt and not yet used, or None. There is at most one: a
        REPAIR_TASK for an attempt that is already ordered is refused."""
        return self.plan.open_authorization(sequence_id)

    def _dispose_seat(self, proposal, cycle_id, ro_conn, now):
        """REPAIR_TASK authorizes another attempt of existing work;
        ASSIGN_SPECIALIST is the Project Manager's staffing decision for a
        workstream's next attempt (and, for work that already ran, the
        authorization of that attempt). Either may carry a staffing word.

        The staffing word is recorded as an assignment decision the
        Taskforce pump applies when the work is ready. It is checked HERE
        against everything that binds an explicit selection — the
        specialist exists and offers the work, a review stays independent
        of its subject, the objective's outside-specialist budget — and a
        selection that fails is recorded REJECTED with the reason. Nothing
        in this path reaches the TaskContractRecord, which was frozen
        without any of it."""
        cls = proposal["class"]
        proposal, problem = self._resolve_workstream(proposal)
        if problem is not None:
            proposal_id = "prop:" + canonical_json_digest(
                {"class": cls, "objective_id": proposal["objective_id"],
                 "workstream": proposal.get("workstream"),
                 "staffing": proposal.get("staffing")})
            return self._reject_decision(proposal_id, cycle_id, proposal,
                                         problem, now, "assignment_refused")
        proposal_id = proposal_identity(proposal)
        existing = self.plan.proposal(proposal_id)
        budget_refused = existing is not None and \
            existing["status"] == "REJECTED" and \
            (existing["note"] or "").startswith(BUDGET_NOTE)
        if existing is not None and not budget_refused:
            return {"proposal_id": proposal_id, "class": cls,
                    "status": existing["status"], "converged": True}
        sequence_id = proposal["sequence_id"]
        staffing = proposal.get("staffing")
        mode = (staffing or {}).get("mode")
        work = query.work_requirement(self.plan, sequence_id) or {}
        capability = proposal.get("capability") or work.get("capability")
        sequence = ro_conn.execute(
            "SELECT state, current_attempt_id FROM sequences WHERE"
            " sequence_id = ?", (sequence_id,)).fetchone() \
            if ro_conn is not None else None
        attempts = ro_conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
            (sequence_id,)).fetchone()["n"] if ro_conn is not None else 0
        ordinal = attempts + 1
        if cls == "ASSIGN_SPECIALIST" and ro_conn is not None:
            refusal = None
            if sequence is None:
                refusal = "sequence %s is not known to authority" \
                    % sequence_id
            elif sequence["state"] not in WAITING_TO_START \
                    or sequence["current_attempt_id"] is not None:
                refusal = ("workstream %s is %s: a project assignment"
                           " staffs work that is waiting to start"
                           % (sequence_id, sequence["state"]))
            elif self.roster is not None:
                staffed = self.roster.assignment_for(sequence_id, ordinal)
                if staffed is not None and staffed["status"] != "ABANDONED":
                    refusal = ("attempt %d of %s is already staffed"
                               " (assignment %s)"
                               % (ordinal, sequence_id,
                                  staffed["assignment_id"]))
            if refusal is not None:
                return self._reject_decision(
                    proposal_id, cycle_id, proposal, refusal, now,
                    "assignment_refused")
        if mode == "outside_specialist":
            if self.resolver is None:
                return self._reject_decision(
                    proposal_id, cycle_id, proposal,
                    "no Taskforce roster is attached to this service",
                    now)
            refusal = self.resolver.check_selection(
                staffing["specialist_id"], capability, work.get("role"),
                query.review_authors(self.plan, ro_conn, work))
            if refusal is not None:
                return self._reject_decision(
                    proposal_id, cycle_id, proposal, refusal, now,
                    "assignment_refused")
            refusal = self._budget_refusal(proposal, "outside_specialists")
            if refusal is not None:
                return self._refuse_for_budget(
                    proposal_id, cycle_id, proposal, refusal,
                    "outside_specialists", now)
        if (staffing or {}).get("escalation"):
            # The ask is recorded with the assignment and resolved by
            # routing. Where the organization declares no escalation
            # route there is nothing to resolve it to, and saying so now
            # is better than silently running the work at standard depth.
            routing = getattr(getattr(self.roster, "pool", None),
                              "routing", None)
            if routing is None or routing.escalation is None:
                return self._reject_decision(
                    proposal_id, cycle_id, proposal,
                    "this organization declares no escalation route: the"
                    " assignment can only run at standard execution depth",
                    now, "assignment_refused")
        if cls == "REPAIR_TASK":
            already = self._pending_authorization(sequence_id)
            if already is not None:
                # One word per attempt. The next attempt of this
                # workstream is already authorized; a second
                # authorization would outlive it and start a later
                # attempt nobody ordered. Changing WHO does the pending
                # attempt is a staffing decision, not another order.
                return self._reject_decision(
                    proposal_id, cycle_id, proposal,
                    "the next attempt of %s is already authorized (by %s);"
                    " to change who does it, decide the assignment"
                    " (ASSIGN_SPECIALIST)"
                    % (sequence_id, already["authorized_by_proposal"]),
                    now)
            refusal = self._budget_refusal(proposal, "repairs")
            if refusal is not None:
                return self._refuse_for_budget(
                    proposal_id, cycle_id, proposal, refusal, "repairs",
                    now)
        # Work that already ran needs the Engineer's word to run again
        # (failures never re-execute themselves). REPAIR_TASK always is
        # that word; ASSIGN_SPECIALIST is when none is open yet. A first
        # attempt needs none: its work proposal authorized it.
        authorizes = cls == "REPAIR_TASK" or (
            attempts >= 1 and self.plan.open_authorization(sequence_id)
            is None)
        seat_id = "st_" + canonical_json_digest(
            {"proposal": proposal_id})[:32] if authorizes else None
        project = self.plan.workstream_of_sequence(sequence_id) or {}
        with self.plan.transaction() as cur:
            self._insert_proposal(cur, proposal_id, cycle_id, proposal, now)
            if authorizes:
                cur.execute(
                    "INSERT OR IGNORE INTO seats (seat_id, sequence_id,"
                    " role, capability, provider, cycle_id,"
                    " authorized_by_proposal, status, created_utc,"
                    " updated_utc) VALUES (?, ?, ?, ?, 'unassigned', ?, ?,"
                    " 'AUTHORIZED', ?, ?)",
                    (seat_id, sequence_id, proposal.get("role", ""),
                     capability, cycle_id, proposal_id, now, now))
            if staffing:
                self.plan.record_assignment_decision(
                    cur, proposal_id, proposal["objective_id"],
                    project.get("project_id"), sequence_id, ordinal, mode,
                    staffing.get("specialist_id"), now,
                    escalation=staffing.get("escalation"))
        self.plan.set_proposal_status(proposal_id, "APPLIED", now,
                                      sequence_id=sequence_id,
                                      note=seat_id)
        report = {"proposal_id": proposal_id, "class": cls,
                  "status": "APPLIED", "sequence_id": sequence_id}
        if seat_id:
            report["seat_id"] = seat_id
        if staffing:
            report["staffing"] = dict(staffing)
        return report

    def _dispose_workforce(self, proposal, cycle_id, now):
        """Apply one RETAIN_SPECIALIST / RELEASE_SPECIALIST decision to
        Taskforce state. The roster validates it against the demonstration
        records (retention must bind QUALIFYING demonstrations of exactly
        that specialist, capability and role); an unsupported decision is
        recorded REJECTED with the reason. Retention is what creates
        TaskforceMembership and the retained seat; releasing an outside
        specialist ends its participation in the project and changes no
        membership. No Registry or Sequencer call exists on this path: a
        workforce decision cannot touch authority."""
        proposal_id = proposal_identity(proposal)
        existing = self.plan.proposal(proposal_id)
        budget_refused = existing is not None and \
            existing["status"] == "REJECTED" and \
            (existing["note"] or "").startswith(BUDGET_NOTE)
        if existing is not None and not budget_refused and \
                existing["status"] == "REJECTED":
            return {"proposal_id": proposal_id, "class": proposal["class"],
                    "status": existing["status"], "converged": True}
        if existing is None or budget_refused:
            refusal = self._budget_refusal(proposal, "retention_decisions")
            if refusal is not None:
                return self._refuse_for_budget(
                    proposal_id, cycle_id, proposal, refusal,
                    "retention_decisions", now)
            with self.plan.transaction() as cur:
                self._insert_proposal(cur, proposal_id, cycle_id, proposal,
                                      now)
        # A decision already on record is applied again, not skipped: the
        # roster converges on its own decision identity (the specialist,
        # the capability and the work it considered), so the same
        # decision is one row — and the same words about NEW work are a
        # new decision.
        if self.roster is None:
            result = {"status": "REJECTED",
                      "note": "no Taskforce roster is attached to this"
                              " planning service"}
        elif proposal["class"] == "RETAIN_SPECIALIST":
            result = self.roster.retain(
                proposal["specialist_id"], proposal["capability"],
                proposal["role"], proposal["demonstrations"],
                proposal["rationale"], now, proposal_id=proposal_id,
                objective_id=proposal["objective_id"])
        else:
            project = self.plan.active_project(proposal["objective_id"])
            result = self.roster.release(
                proposal["specialist_id"], proposal["capability"],
                proposal["role"], proposal["reason"],
                proposal["rationale"], now, proposal_id=proposal_id,
                objective_id=proposal["objective_id"],
                disposition=proposal.get("disposition", "RELEASE"),
                project_id=project["project_id"] if project else None)
        status = "APPLIED" if result["status"] in ("APPLIED", "CONVERGED") \
            else "REJECTED"
        self.plan.set_proposal_status(
            proposal_id, status, now,
            note=result.get("decision_id") or result.get("note"))
        seat = result.get("seat") or {}
        report = {"proposal_id": proposal_id, "class": proposal["class"],
                  "status": status,
                  "specialist_id": proposal["specialist_id"],
                  "capability": proposal["capability"],
                  "role": proposal["role"],
                  "seat_id": seat.get("seat_id")}
        if status == "REJECTED":
            report["note"] = result.get("note")
        else:
            report["decision_id"] = result.get("decision_id")
            if "membership_created" in result:
                report["membership_created"] = result["membership_created"]
            if "membership_changed" in result:
                report["membership_changed"] = result["membership_changed"]
        return report


# The name this service carried before the organizational model was
# aligned. Kept for existing callers.
ChiefEngineerService = EngineerService
