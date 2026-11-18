"""The Taskforce pump: staffing project work and carrying it through
execution.

Two responsibilities live in the Taskforce, and they are different in
kind:

- THE PERSISTENT ORGANIZATION (``workforce``): retained membership,
  retained seats, capability history, demonstrated environment
  competence, availability, organizational learning, and the resolution
  of retained capabilities.
- EXECUTION (this module): staffing READY work, projecting the frozen
  contract into a specialist task (obj-10), launching provider sessions
  through the Transport contract (AutoSwarm is the substrate),
  supervising what a running specialist asks permission for, collecting
  producer-original witnesses and artifacts, admitting findings, and
  walking each attempt through the Sequencer's registered transitions.

It holds no authority of its own (D-63, cap-51 forbids it transitions):
every state change below goes through a public Sequencer API, which
enforces lease, fence, budgets and the frozen rows regardless of what
this code asks for. A retained or established seat changes WHO is staffed
and nothing else — the same TaskContractRecord, AttemptContract,
ExecutionManifest, Collector, Verifier and Sequencer stand in front of
every specialist.

Staffing. The work requirement names a capability and a role and nothing
else: no provider, no model, no specialist — it is frozen into the
TaskContractRecord. WHO executes one attempt is a ProjectAssignment, and
it comes about on one of two paths:

    capability request      the Taskforce resolves an available retained
                            member
    explicit selection      the Engineer, as Project Manager, names an
                            outside specialist for this project

The pump applies whichever the Project Manager decided (an assignment
decision recorded by ``ASSIGN_SPECIALIST``), and treats work with no
decision as a capability request. The Taskforce NEVER brings an outside
specialist onto a project itself: when no retained seat can take the
work, the pump says so (``staffing_unavailable``) and the work waits for
the Project Manager. Provider and model are resolved from the assigned
specialist's route, downstream of all of this. The assignment and its
reasons are persisted BEFORE anything launches, and assigning anyone
creates no Taskforce membership.

Allocation discipline (the Engineer's gate): a sequence's FIRST attempt
is authorized by its originating admitted proposal; every LATER attempt
(a repair after t-s11, a retry after t-s08) requires an explicit
authorization minted by a REPAIR_TASK/ASSIGN_SPECIALIST proposal —
failures never re-execute themselves, and findings never become work
without the Engineer's say.

The frozen prompt. When a contract declares its specialist prompt as a
specification input, the specialist is launched on exactly those bytes —
first attempt or repair. A repair executes the SAME frozen requirement;
different instructions are a different contract.

Authority state is the only truth the pump trusts: assignments and
execution slots are plan-store bookkeeping reconciled FROM ledger facts,
never the other way around. Allocation is staged and resumable, so a
restart anywhere between "specialist selected" and "session launched"
continues from the durable stage it reached without a second membership,
a second assignment or a second launch.
"""

import json
import subprocess

from autobot.collector import Collector
from autobot.execution_boundary import launch_with_manifest
from autobot.git_adapter import mint_candidate_receipt
from autobot.git_executor import execute_local_cas
from autobot.models import ConflictError
from autobot.projection import project_prompt
from autobot.transport import normalize_outcome, reconcile_dispatched
from autobot.verifier_runner import ProductionVerifierRunner

from . import query, supervision
from .demonstrations import DemonstrationRecorder
from .findings import FindingError, admit_finding, extract_findings
from .prompts import frozen_specialist_prompt
from .proposals_util import canonical_json_digest
from .workforce import (
    MEMBER_STATES,
    SpecialistPool,
    TaskforceRoster,
    TaskforceSeatResolver,
    assignment_identity,
)


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class Taskforce:
    def __init__(self, plan, sequencer, store, transport, runner=None,
                 pool=None, clock=None, ro_conn=None, repo_path=None,
                 repository_context=None, collector=None,
                 established_threshold=3, gate_root=None,
                 isolate_read_only=False):
        self.plan = plan
        # Whether READ_ONLY specialists also work from a frozen source
        # revision in an isolated worktree (the production setting).
        self.isolate_read_only = isolate_read_only
        self.sequencer = sequencer
        self.store = store
        self.transport = transport
        # The registered verifier_runner seat (cap-35): the PRODUCTION
        # runner by default (C4-M1) — it executes exact registered
        # closures and mints canonical obj-19/obj-20; the Taskforce only
        # triggers it and applies the registered transitions on the
        # admitted rows (task_force is on cap-35's forbidden list).
        self.runner = runner or ProductionVerifierRunner(sequencer, store)
        # The organization: the specialist catalog, the durable roster
        # (memberships, seats, project assignments, teams), the resolver
        # of retained capabilities, and the demonstration recorder.
        self.pool = pool if pool is not None else SpecialistPool()
        self.roster = TaskforceRoster(
            plan, self.pool, established_threshold=established_threshold)
        self.resolver = TaskforceSeatResolver(self.roster)
        self.recorder = DemonstrationRecorder(plan, self.roster, ro_conn,
                                              store)
        self.clock = clock
        self.ro = ro_conn
        self.repo_path = repo_path
        self.repository_context = repository_context
        self.gate_root = gate_root if gate_root is not None else getattr(
            transport, "_gate_root", None)
        # The registered collector seat (cap-30): the Taskforce ASKS it to
        # collect; it never classifies, mints or submits a receipt itself
        # (task_force is on cap-30's forbidden list).
        self.collector = collector or Collector(sequencer, store)
        # Calls this process has already handed to the transport: a
        # DISPATCHED attempt is relaunched only across a restart.
        self._launched = set()

    # ------------------------------------------------------------- reads

    def _sequences_in(self, states):
        marks = ",".join("?" for _ in states)
        return [dict(r) for r in self.ro.execute(
            "SELECT * FROM sequences WHERE state IN (%s)"
            " ORDER BY sequence_id" % marks, tuple(states)).fetchall()]

    def _attempts_in(self, states):
        marks = ",".join("?" for _ in states)
        return [dict(r) for r in self.ro.execute(
            "SELECT a.*, c.role, c.provider, c.call_id,"
            " c.projection_address FROM attempts a LEFT JOIN attempt_calls"
            " c ON c.attempt_id = a.attempt_id WHERE a.state IN (%s)"
            " ORDER BY a.attempt_id" % marks, tuple(states)).fetchall()]

    def _sequence(self, sequence_id):
        row = self.ro.execute(
            "SELECT * FROM sequences WHERE sequence_id = ?",
            (sequence_id,)).fetchone()
        return dict(row) if row else None

    def _record_payload(self, record_hash):
        row = self.ro.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (record_hash,)).fetchone()
        return json.loads(self.store.get(
            row["content_address"]).decode("utf-8"))

    def _sequence_attempts(self, sequence_id):
        return [dict(r) for r in self.ro.execute(
            "SELECT a.*, c.role, c.provider, c.call_id,"
            " c.projection_address FROM attempts a LEFT JOIN attempt_calls"
            " c ON c.attempt_id = a.attempt_id WHERE a.sequence_id = ?"
            " ORDER BY a.rowid", (sequence_id,)).fetchall()]

    def _attempt_count(self, sequence_id):
        return self.ro.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
            (sequence_id,)).fetchone()["n"]

    def _permit_available(self, sequence_id):
        row = self.ro.execute(
            "SELECT permitted_uses, consumed_uses, reserved_uses"
            " FROM permit_state WHERE sequence_id = ?",
            (sequence_id,)).fetchone()
        return row is not None and \
            row["consumed_uses"] + row["reserved_uses"] < row["permitted_uses"]

    def _originating_proposal(self, sequence_id):
        row = self.plan.conn.execute(
            "SELECT * FROM proposals WHERE sequence_id = ? AND status ="
            " 'ADMITTED' ORDER BY created_utc LIMIT 1",
            (sequence_id,)).fetchone()
        return dict(row) if row else None

    def _provenance_findings(self, proposal_body):
        ids = proposal_body.get("provenance", {}).get("findings", [])
        out = []
        for finding_id in ids:
            row = self.plan.conn.execute(
                "SELECT * FROM findings WHERE finding_id = ?",
                (finding_id,)).fetchone()
            if row is not None:
                out.append({
                    "finding_id": row["finding_id"],
                    "finding_class": row["finding_class"],
                    "affected_component": row["affected_component"],
                    "significance": row["significance"],
                    "suggested_follow_up": row["suggested_follow_up"] or "",
                    "evidence_refs": _loads(row["evidence_refs"], []),
                })
        return out

    def in_flight(self):
        """Attempts whose outcome is still coming from OUTSIDE this
        process: a session being launched or running, or a frozen
        candidate inside its evidence window. While any exist the loop is
        waiting, not stable."""
        flying = [a["attempt_id"] for a in self._attempts_in(
            ["DISPATCHED", "RUNNING"])]
        for att in self._attempts_in(["CANDIDATE_FROZEN"]):
            seq = self._sequence(att["sequence_id"])
            if seq is not None and seq["state"] == "CANDIDATE_FROZEN":
                flying.append(att["attempt_id"])
        return flying

    # ---------------------------------------------------------- staffing

    def _slot_for_assignment(self, sequence_id, assignment_id):
        row = self.plan.conn.execute(
            "SELECT * FROM seats WHERE sequence_id = ? AND assignment_id"
            " = ?", (sequence_id, assignment_id)).fetchone()
        return dict(row) if row else None

    def _staffing_request(self, seq_row, ordinal):
        """The engineering ask for one attempt of one sequence — WHAT is
        needed, with the Engineer gate applied — or None when this
        attempt may not be staffed (no planner word, or not this plane's
        work)."""
        sequence_id = seq_row["sequence_id"]
        first = ordinal == 1
        authorization = self.plan.open_authorization(sequence_id)
        if authorization is None and not first:
            # The authorization may already be bound to this attempt's
            # execution slot (a restart after the bind).
            slot = self._slot_for_assignment(
                sequence_id, assignment_identity(sequence_id, ordinal))
            if slot is not None and slot["authorized_by_proposal"]:
                authorization = slot
        if not first and authorization is None:
            return None  # re-execution waits for the planner's word
        work = query.work_requirement(self.plan, sequence_id)
        origin = work["proposal"] if work else None
        origin_body = work["body"] if work else {}
        origin_requirement = origin_body.get("requirement", {})
        if authorization is not None:
            auth_proposal = self.plan.proposal(
                authorization["authorized_by_proposal"]) or {}
            body = _loads(auth_proposal.get("body"), {})
            guidance = body.get("guidance", "") or body.get(
                "requirement", {}).get("guidance", "") or \
                origin_requirement.get("guidance", "")
            capability = authorization["capability"]
            # The role is the one the work froze, unless the
            # authorization names another.
            role = authorization["role"] or (work or {}).get("role")
            findings = self._provenance_findings(body)
            authorized_by = authorization["authorized_by_proposal"]
        else:
            if origin is None:
                return None  # no admitted proposal: not this plane's work
            body = origin_body
            guidance = origin_requirement.get("guidance", "")
            capability = work["capability"]
            role = work["role"]
            findings = self._provenance_findings(body)
            authorized_by = origin["proposal_id"]
        if not capability or not guidance:
            return None
        # The Project Manager's staffing decision for this workstream's
        # next attempt, when there is one; otherwise the work is a
        # capability request to the Taskforce.
        decision = self.plan.open_staffing_decision(sequence_id)
        workstream = self.plan.workstream_of_sequence(sequence_id) or {}
        return {
            "capability": capability, "role": role, "guidance": guidance,
            "findings": findings,
            "staffing_mode": decision["mode"] if decision else "taskforce",
            "staffing_specialist":
                decision["specialist_id"] if decision else None,
            "staffing_decision":
                decision["decision_id"] if decision else None,
            "authorization": authorization,
            "authorized_by": authorized_by,
            "objective_id": origin["objective_id"] if origin else None,
            "project_id": workstream.get("project_id"),
            "workstream_id": workstream.get("workstream_id"),
            "subsystem": origin_requirement.get("subsystem"),
            "independent_of": query.review_authors(self.plan, self.ro,
                                                   work),
        }

    def _canonical_ref(self, record):
        """The repository's canonical ref for one frozen record: its own
        target_ref when it has one (SOURCE_MODIFYING), otherwise the bound
        RepositoryAuthorityDeclaration's."""
        target = record.get("source_domain", {}).get("target_ref")
        if target:
            return target
        declaration_hash = record.get("resolved_bindings", {}).get(
            "repository_authority_declaration")
        if declaration_hash:
            row = self.ro.execute(
                "SELECT content_address FROM objects WHERE object_hash = ?",
                (declaration_hash,)).fetchone()
            if row is not None:
                payload = json.loads(self.store.get(
                    row["content_address"]).decode("utf-8"))
                if payload.get("canonical_ref"):
                    return payload["canonical_ref"]
        return "refs/heads/main"

    def _emit(self, key, kind, subject, facts, now):
        with self.plan.transaction() as cur:
            return self.plan.emit_workforce_event(cur, key, kind, subject,
                                                  facts, now)

    def _source_revision(self, record):
        """The frozen source revision a specialist works from: the
        canonical ref's value now, read from Git (cap-42). Always for
        SOURCE_MODIFYING work; for READ_ONLY work when the Taskforce
        isolates it — a reader given a frozen revision sees exactly what
        authority has accepted so far (a review dispatched after a change
        landed reads the LANDED change), independent of whatever a shared
        checkout's files happen to hold."""
        if not self.repo_path:
            return None
        if record["finalization_class"] != "SOURCE_MODIFYING" \
                and not self.isolate_read_only:
            return None
        proc = subprocess.run(
            ["git", "-C", str(self.repo_path), "rev-parse",
             self._canonical_ref(record)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def _staffing_wait(self, sequence_id, ordinal, request, why, now):
        """Nobody could be staffed: make the wait visible, and — when the
        Project Manager's selection can never be honored — close that
        decision so a new one can be made."""
        cause = why["cause"]
        wait_key = "%s:%d" % (sequence_id, ordinal)
        if cause == "specialist_busy":
            return  # the selected specialist is occupied; the work waits
        if cause == "selection_refused":
            decision_id = request["staffing_decision"]
            with self.plan.transaction() as cur:
                if decision_id:
                    self.plan.resolve_assignment_decision(
                        cur, decision_id, "REFUSED", now,
                        note=why["refused"])
                self.plan.emit_workforce_event(
                    cur, "assignment_refused:%s:%s" % (wait_key,
                                                       decision_id),
                    "assignment_refused", sequence_id,
                    {"capability": request["capability"],
                     "role": request["role"],
                     "staffing_mode": why["mode"],
                     "specialist_id": why.get("specialist_id"),
                     "decision_id": decision_id,
                     "note": why["refused"]}, now)
            return
        # A capability request no retained seat can take: the Taskforce
        # does not go outside for it. The Project Manager is told, with
        # the outside specialists it could select.
        self._emit(
            "staffing_unavailable:" + wait_key, "staffing_unavailable",
            sequence_id,
            {"capability": request["capability"],
             "role": request["role"],
             "staffing_mode": why["mode"],
             "cause": cause,
             "attempt_ordinal": ordinal,
             "independent_of": request["independent_of"],
             "retained_seats": [
                 {"seat_id": c["seat_id"],
                  "specialist_id": c["specialist_id"],
                  "available": c["available"]}
                 for c in why.get("retained", [])],
             "outside_candidates": [
                 {"specialist_id": c["specialist_id"],
                  "handle": c["handle"], "eligible": c["eligible"],
                  "busy": c["busy"]}
                 for c in why.get("outside_candidates", [])]}, now)

    def _staff(self, seq_row, ordinal, request, now):
        """Resolve WHO executes one attempt and persist the
        ProjectAssignment. The Project Manager's decision is applied when
        there is one; otherwise the work is a capability request and the
        Taskforce resolves a retained member. Returns the assignment row,
        or None for a visible wait (no retained seat can take it, the
        selected specialist is busy or may not do it, or the objective's
        budget is spent)."""
        sequence_id = seq_row["sequence_id"]
        objective_id = request["objective_id"]
        wait_key = "%s:%d" % (sequence_id, ordinal)
        if objective_id and self.plan.budget_headroom(
                objective_id, "attempts") <= 0:
            self._emit("budget:%s:attempts:%s" % (objective_id, wait_key),
                       "budget_exhausted", objective_id,
                       {"dimension": "attempts",
                        "sequence_id": sequence_id}, now)
            return None
        record = self._record_payload(seq_row["record_hash"])
        environment = "%s/%s" % (record["source_domain"]["repository_id"],
                                 record["finalization_class"])
        directive = None
        if request["staffing_mode"] != "taskforce":
            previous = self.roster.assignment_for(sequence_id, ordinal - 1) \
                if ordinal > 1 else None
            directive = {
                "mode": request["staffing_mode"],
                "specialist_id": request["staffing_specialist"],
                "previous_specialist_id":
                    previous["specialist_id"] if previous else None}
        selection = self.resolver.resolve(
            request["capability"], role=request["role"],
            environment=environment, subsystem=request["subsystem"],
            directive=directive,
            independent_of=request["independent_of"])
        if selection is None:
            self._staffing_wait(
                sequence_id, ordinal, request,
                self.resolver.explain_unavailable(
                    request["capability"], request["role"], directive,
                    request["independent_of"]), now)
            return None
        if selection["reason"].get("path") == "OUTSIDE_SPECIALIST" \
                and objective_id and self.plan.budget_headroom(
                    objective_id, "outside_specialists") <= 0:
            self._emit(
                "budget:%s:outside_specialists:%s"
                % (objective_id, wait_key), "budget_exhausted",
                objective_id, {"dimension": "outside_specialists",
                               "sequence_id": sequence_id}, now)
            return None
        execution = {}
        source_revision = self._source_revision(record)
        if source_revision:
            execution["source_revision"] = source_revision
        assignment, _created = self.roster.open_assignment(
            sequence_id, ordinal, selection, objective_id,
            request["authorized_by"], now, execution=execution or None,
            project_id=request["project_id"],
            workstream_id=request["workstream_id"],
            assignment_decision=request["staffing_decision"])
        return assignment

    # -------------------------------------------------------- allocation

    def _bind_slot(self, seq_row, assignment, view, request, attempt_id,
                   call_id, projection, now):
        """The execution slot (the v1 ``seats`` row the authority arcs
        read the artifact from) and the assignment's authority
        references — both idempotent."""
        authorization = request["authorization"] or {}
        slot_id = authorization.get("seat_id") or \
            "st_" + canonical_json_digest({"call": call_id})[:32]
        with self.plan.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO seats (seat_id, sequence_id, role,"
                " capability, provider, status, created_utc, updated_utc)"
                " VALUES (?, ?, ?, ?, ?, 'AUTHORIZED', ?, ?)",
                (slot_id, seq_row["sequence_id"], view["role"],
                 view["capability"], view["provider"], now, now))
            cur.execute(
                "UPDATE seats SET attempt_id = ?, call_id = ?, role = ?,"
                " provider = ?, model = ?, selected_by = ?,"
                " projection_address = ?, assignment_id = ?,"
                " status = CASE WHEN status = 'AUTHORIZED' THEN"
                " 'DISPATCHED' ELSE status END, updated_utc = ?"
                " WHERE seat_id = ?",
                (attempt_id, call_id, view["role"], view["provider"],
                 view["model"], view["selected_by"], projection,
                 assignment["assignment_id"], now, slot_id))
        self.roster.bind_execution(
            assignment["assignment_id"], attempt_id, call_id, now,
            execution={"projection_address": projection})
        return slot_id

    def _drive(self, seq_row, assignment, request, ordinal, now):
        """Carry one staffed attempt from whatever durable stage it has
        reached to a launched session: create the attempt (t-s06), reserve
        it (t-a01), project and dispatch (t-a02), bind the references,
        launch through the manifest. Every stage is read back from the
        ledger first, so re-entering after a crash repeats nothing."""
        sequence_id = seq_row["sequence_id"]
        view = self.roster.assignment_view(assignment)
        record = self._record_payload(seq_row["record_hash"])
        attempts = self._sequence_attempts(sequence_id)
        att = attempts[ordinal - 1] if len(attempts) >= ordinal else None
        if att is None:
            try:
                info = self.sequencer.start_execution(sequence_id)
            except ConflictError as exc:
                # Authority refused the attempt (e.g. the frozen retry
                # budget): the staffing is abandoned, visibly.
                with self.plan.transaction() as cur:
                    self.roster.set_assignment_status(
                        cur, assignment["assignment_id"], "ABANDONED", now,
                        outcome="START_REFUSED: %s" % exc)
                    self.plan.emit_workforce_event(
                        cur, "start_refused:" + assignment["assignment_id"],
                        "attempt_refused", sequence_id,
                        {"reason": str(exc)}, now)
                return None
            attempt_id, fence = info["attempt_id"], info["fence"]
            contract_hash = info["attempt_contract_hash"]
            state = "DECLARED"
            call_id = projection = None
        else:
            attempt_id, fence = att["attempt_id"], att["fence"]
            contract_hash = att["attempt_contract_hash"]
            state = att["state"]
            call_id, projection = att["call_id"], att["projection_address"]
        if state == "DECLARED":
            self.sequencer.reserve_attempt(attempt_id, expected_fence=fence)
            state = "RESERVED"
        if state == "RESERVED":
            identity = {"sequence_id": sequence_id,
                        "attempt_id": attempt_id, "fence": fence,
                        "attempt_contract_hash": contract_hash}
            # The specialist is launched on the FROZEN prompt whenever
            # the contract declares one (INV-49 holds by construction);
            # otherwise on the planner's guidance for this attempt.
            guidance = frozen_specialist_prompt(record, self.store) \
                or request["guidance"]
            context = {"author": "planner", "guidance": guidance,
                       "expected_outputs": view["expected_output"]}
            if request["findings"]:
                context["predecessor_findings"] = request["findings"]
            if self.repository_context:
                context["repository_context"] = self.repository_context
            _data, projection = project_prompt(
                self.store, record, view["role"], identity, context)
            dispatched = self.sequencer.dispatch_attempt(
                attempt_id, view["role"], view["provider"], projection)
            call_id = dispatched["call_id"]
            state = "DISPATCHED"
        if state != "DISPATCHED":
            return None  # already past the launch: nothing to drive
        slot_id = self._bind_slot(seq_row, assignment, view, request,
                                  attempt_id, call_id, projection, now)
        if call_id not in self._launched:
            launch = dict(view)
            source_revision = view["execution"].get("source_revision")
            if source_revision:
                launch["source_revision"] = source_revision
            self._launched.add(call_id)
            launch_with_manifest(
                self.sequencer, self.transport, attempt_id, launch,
                view["capability"])
        return {"sequence_id": sequence_id, "attempt_id": attempt_id,
                "call_id": call_id, "seat_id": slot_id,
                "role": view["role"], "provider": view["provider"],
                "assignment_id": assignment["assignment_id"],
                "taskforce_seat_id": assignment["seat_id"],
                "specialist_id": assignment["specialist_id"],
                "selection_path": assignment["selection_path"]}

    def allocate_ready(self, now):
        """Breadth-first: every READY sequence staffable under the
        Engineer's gate is staffed BEFORE any later stage runs, so
        independent workstreams run concurrently at the provider. A
        specialist is never dispatched into two executions at once: a
        busy specialist is unavailable on both staffing paths, and
        dependency readiness is authority's (only READY work is ever
        staffed)."""
        allocated = []
        for seq_row in self._sequences_in(["READY"]):
            if seq_row["current_attempt_id"] is not None:
                continue
            ordinal = self._attempt_count(seq_row["sequence_id"]) + 1
            request = self._staffing_request(seq_row, ordinal)
            if request is None:
                continue
            assignment = self.roster.assignment_for(
                seq_row["sequence_id"], ordinal)
            if assignment is not None and \
                    assignment["status"] == "ABANDONED":
                continue  # authority refused this attempt; the planner knows
            if assignment is None:
                assignment = self._staff(seq_row, ordinal, request, now)
                if assignment is None:
                    continue  # a visible wait, never an invented seat
            entry = self._drive(seq_row, assignment, request, ordinal, now)
            if entry is not None:
                allocated.append(entry)
        return allocated

    def resume_interrupted(self, now):
        """Continue allocations a previous process left between stages:
        an attempt created but not reserved, reserved but not dispatched,
        or dispatched but never handed to the transport. Only attempts
        this plane staffed (an assignment row exists) are touched."""
        resumed = []
        for att in self._attempts_in(["DECLARED", "RESERVED",
                                      "DISPATCHED"]):
            sequence_id = att["sequence_id"]
            attempts = self._sequence_attempts(sequence_id)
            ordinal = [a["attempt_id"] for a in attempts].index(
                att["attempt_id"]) + 1
            assignment = self.roster.assignment_for(sequence_id, ordinal)
            if assignment is None or assignment["status"] not in (
                    "SELECTED", "DISPATCHED"):
                continue
            if att["state"] == "DISPATCHED":
                call_id = att["call_id"]
                if call_id in self._launched:
                    continue
                if self.transport.find_session(call_id) is not None:
                    continue  # reconcile adopts it
                if self.transport.positive_launch_failure(call_id) \
                        is not None:
                    continue  # reconcile fails it (t-a10)
            seq_row = self._sequence(sequence_id)
            request = self._staffing_request(seq_row, ordinal)
            if request is None:
                continue
            entry = self._drive(seq_row, assignment, request, ordinal, now)
            if entry is not None:
                resumed.append(entry)
        return resumed

    # ------------------------------------------------------- supervision

    def supervise_running(self, now):
        """Answer what running specialists are asking permission for,
        with the deterministic assignment policy (``supervision``). Each
        decision is recorded beside the assignment; a request that is not
        a permission (a native question) is left unanswered."""
        pending = getattr(self.transport, "pending_requests", None)
        if pending is None:
            return []
        decided = []
        for att in self._attempts_in(["DISPATCHED", "RUNNING"]):
            call_id = att["call_id"]
            if not call_id:
                continue
            requests = pending(call_id)
            if not requests:
                continue
            seq_row = self._sequence(att["sequence_id"])
            record = self._record_payload(seq_row["record_hash"])
            assignment = self.roster.assignment_by_call(call_id)
            for request in requests:
                request_id = request.get("request_id")
                if self.plan.conn.execute(
                        "SELECT 1 FROM assignment_approvals WHERE"
                        " request_id = ?", (request_id,)).fetchone():
                    continue
                context = supervision.assignment_context(
                    record, request.get("workspace"),
                    gate_root=self.gate_root, repo_path=self.repo_path)
                verdict = supervision.decide(request, context)
                if verdict["decision"] is None:
                    continue
                try:
                    self.transport.decide_request(
                        call_id, request_id, verdict["decision"],
                        verdict["reason"])
                except RuntimeError:
                    continue  # no longer pending; nothing was decided
                subject = verdict["subject"]
                with self.plan.transaction() as cur:
                    cur.execute(
                        "INSERT OR IGNORE INTO assignment_approvals"
                        " (request_id, assignment_id, call_id, tool_name,"
                        " subject, decision, reason, decided_utc)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (request_id,
                         assignment["assignment_id"] if assignment
                         else None, call_id, verdict["tool"],
                         subject[:500] if isinstance(subject, str)
                         else None, verdict["decision"],
                         verdict["reason"], now))
                decided.append({"call_id": call_id,
                                "request_id": request_id,
                                "tool": verdict["tool"],
                                "decision": verdict["decision"]})
        return decided

    # -------------------------------------------------------- collection

    def _witness_address_for_finish(self, admitted):
        for kind in ("obj-13", "obj-11", "obj-12", "obj-14"):
            if kind in admitted:
                return admitted[kind]
        return None

    def _admit_findings(self, att, result, now):
        """Admit the specialist's structured findings: the transport's
        own list when it supplies one, otherwise the findings block of
        the final response. A malformed finding is dropped, never
        repaired and never fatal — engineering information is admitted
        as reported or not at all."""
        reported = result.get("findings")
        if reported is None:
            artifact = result.get("artifact_bytes")
            text = artifact.decode("utf-8", "replace") \
                if isinstance(artifact, bytes) else None
            reported, _problem = extract_findings(text)
        admitted = []
        for finding in reported or []:
            if not isinstance(finding, dict):
                continue
            merged = dict(finding)
            merged.setdefault("attempt_id", att["attempt_id"])
            merged.setdefault("sequence_id", att["sequence_id"])
            merged.setdefault("call_id", att["call_id"])
            merged.setdefault("role", att["role"])
            try:
                admitted.append(admit_finding(self.plan, merged, now))
            except FindingError:
                continue
        return admitted

    def collect_running(self, now):
        """For each RUNNING attempt: collect producer originals, admit
        witnesses, admit findings, finish per the dispatched role, freeze
        the candidate and complete evidence (t-a04..t-a06)."""
        collected = []
        for att in self._attempts_in(["RUNNING"]):
            result = self.transport.collect(att["call_id"])
            if result is None:
                # No terminal provider outcome observed. The attempt's
                # RUNNING state rests on the POSITIVE t-a03 lifecycle
                # witness, never on this absence; nothing transitions,
                # and staleness remains t-a12's (D-C3-4).
                continue
            admitted = {}
            for kind, producer, data in result.get("witness_files", []):
                entry = self.sequencer.admit_witness(
                    att["attempt_id"], kind, producer, data)
                admitted[kind] = entry["content_address"]
            self._admit_findings(att, result, now)
            finish_witness = self._witness_address_for_finish(admitted)
            # D-C3-4: the provider reports; the frozen mapping normalizes
            # (completed -> success); the frozen role policy decides.
            # A missing or empty outcome from the transport is never
            # promoted to success — it normalizes to "" which is not in
            # any frozen success_outcomes list.
            outcome = normalize_outcome(result.get("outcome") or "")
            state = self.sequencer.finish_attempt(
                att["attempt_id"], att["role"], outcome, finish_witness)
            entry = {"attempt_id": att["attempt_id"],
                     "sequence_id": att["sequence_id"],
                     "outcome": outcome, "state": state}
            if state == "FINISHED":
                artifact = result.get("artifact_bytes")
                if artifact is not None:
                    admitted_artifact = self.sequencer.admit_artifact(
                        att["attempt_id"],
                        result.get("artifact_path",
                                   "artifact/engineering-notes"),
                        artifact)
                    with self.plan.transaction() as cur:
                        cur.execute(
                            "UPDATE seats SET artifact_address = ?,"
                            " status = 'COLLECTED', updated_utc = ?"
                            " WHERE call_id = ?",
                            (admitted_artifact["content_address"], now,
                             att["call_id"]))
                else:
                    with self.plan.transaction() as cur:
                        cur.execute(
                            "UPDATE seats SET status = 'COLLECTED',"
                            " updated_utc = ? WHERE call_id = ?",
                            (now, att["call_id"]))
                # C4-M1 (cap-32): the git_adapter mints the REAL obj-16.
                # SOURCE_MODIFYING work binds the candidate tree — the
                # tree of the specialist's PROPOSED new value, read from
                # Git (cap-42), never "whatever the workspace holds";
                # READ_ONLY work is the artifact/observation set. An SM
                # attempt with no capturable tree stays FINISHED, a
                # visible wait for the planner, never a fabricated
                # candidate.
                seq = self.ro.execute(
                    "SELECT fence, record_hash FROM sequences"
                    " WHERE sequence_id = ?",
                    (att["sequence_id"],)).fetchone()
                record = self._record_payload(seq["record_hash"])
                if record["finalization_class"] == "SOURCE_MODIFYING":
                    text = artifact.decode("utf-8", "replace") \
                        if artifact is not None else ""
                    effect = _loads(text, None)
                    if not isinstance(effect, dict):
                        import re
                        m = re.search(
                            r'\{[^{}]*"target_ref"[^{}]*"new_value"[^{}]*\}',
                            text, re.DOTALL)
                        if m:
                            effect = _loads(m.group(), {})
                        else:
                            effect = {}
                    treeish = effect.get("new_value") \
                        if isinstance(effect, dict) else None
                    if self.repo_path is None or not isinstance(
                            treeish, str) or not treeish:
                        entry["candidate"] = "BLOCKED_NO_CANDIDATE_TREE"
                        collected.append(entry)
                        continue
                    expected_old = effect.get("expected_old")
                    if expected_old and isinstance(expected_old, str):
                        from autobot.git_adapter import (
                            changed_paths, verify_mutation_scope)
                        paths = changed_paths(
                            self.repo_path, expected_old, treeish)
                        scope = record.get("allowed_scope", {}).get(
                            "paths", [])
                        unauthorized = verify_mutation_scope(paths, scope)
                        if unauthorized:
                            entry["candidate"] = (
                                "BLOCKED_SCOPE_VIOLATION: %s"
                                % unauthorized)
                            collected.append(entry)
                            continue
                    minted = mint_candidate_receipt(
                        self.sequencer, self.store, att["attempt_id"],
                        repo_path=self.repo_path, treeish=treeish,
                        source_revision=expected_old)
                else:
                    minted = mint_candidate_receipt(
                        self.sequencer, self.store, att["attempt_id"])
                self.sequencer.freeze_candidate(
                    att["attempt_id"], seq["fence"],
                    minted["candidate_address"])
                entry["candidate_address"] = minted["candidate_address"]
            collected.append(entry)
        collected += self.settle_frozen_evidence(now)
        return collected

    def settle_frozen_evidence(self, now):
        """The C3-M1 evidence pass, for EVERY attempt in CANDIDATE_FROZEN —
        frozen this sweep or inherited across a restart: ask the registered
        Collector (cap-30) to derive-and-admit the obj-15 classification,
        then act on the ADMITTED facts alone. COMPLETE completes the
        evidence (t-a06/t-s09); INTEGRITY_FAILED, or INCOMPLETE whose
        derived bound has elapsed, fails the candidate (t-a13/t-s45);
        INCOMPLETE inside its window stays frozen for a later sweep —
        unchanged facts re-admit idempotently and report nothing, so a
        waiting attempt is quiet, not busy. The Taskforce never sets,
        forwards or interprets a classification of its own (F-CE-4
        closed)."""
        settled = []
        for att in self._attempts_in(["CANDIDATE_FROZEN"]):
            seq = self.ro.execute(
                "SELECT state, fence FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            if seq["state"] != "CANDIDATE_FROZEN":
                continue  # the sequence arc has already moved (e.g. HALTED)
            outcome = self.collector.collect_and_admit(att["attempt_id"])
            entry = {"attempt_id": att["attempt_id"],
                     "sequence_id": att["sequence_id"],
                     "evidence": outcome["completeness"]}
            if outcome["completeness"] == "COMPLETE":
                self.sequencer.complete_evidence(
                    att["attempt_id"], seq["fence"],
                    outcome["receipt_address"])
                settled.append(entry)
            elif (outcome["completeness"] == "INTEGRITY_FAILED"
                    or outcome["evidence_complete_by_utc"] <= now):
                self.sequencer.fail_candidate(
                    att["attempt_id"], seq["fence"],
                    outcome["receipt_address"],
                    reason="collector classification %s"
                           % outcome["completeness"])
                settled.append(entry)
            elif not outcome["duplicate"]:
                settled.append(entry)  # a NEW classification is news
        return settled

    # ------------------------------------------------------ verification

    def verify_evidence_complete(self):
        """The production verification trigger (C4-M1): the registered
        verifier_runner seat executes the exact registered closures and
        submits canonical obj-19/obj-20 through the admission seam; the
        Taskforce only applies the registered transitions on the admitted
        rows. An environment mismatch is a HALT-grade authority problem
        (t-s12 env_mismatch) — never converted into a failed assertion —
        and an INCONCLUSIVE member routes to t-s12's unknown arm. An
        integrity stop (IntegrityHalt: missing closure leaf, forged
        registration) PROPAGATES: processing stops with no transition
        committed, and the durable t-s12 bound_artifact_unavailable is the
        operator-visible integration decision, exactly the M4 seam
        discipline.

        Who did the work makes no difference here: a retained or
        established member's attempt is verified by exactly this path."""
        verified = []
        for att in self._attempts_in(["EVIDENCE_COMPLETE"]):
            seq = self.ro.execute(
                "SELECT * FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            if seq["state"] != "VERIFYING":
                continue  # already halted or moved by another arc
            record = self._record_payload(seq["record_hash"])
            assertions = record.get("acceptance_assertions", {}).get(
                "ids_and_environment_constraints", [])
            if any(a.get("mode") == "HUMAN_ATTESTED" for a in assertions):
                continue  # the attestation path is yours (cap-44)
            try:
                outcome = self.runner.verify_attempt(att["attempt_id"])
            except ConflictError as exc:
                if "env_mismatch" in str(exc):
                    self.sequencer.halt_verification(
                        att["sequence_id"], "env_mismatch")
                    verified.append({"attempt_id": att["attempt_id"],
                                     "result": "HALTED"})
                    continue
                raise
            if outcome["inconclusive"]:
                self.sequencer.halt_verification(
                    att["sequence_id"], "unknown")
                verified.append({"attempt_id": att["attempt_id"],
                                 "result": "HALTED"})
                continue
            result = self.sequencer.conclude_verification(
                att["attempt_id"], att["fence"])
            verified.append({"attempt_id": att["attempt_id"],
                             "result": result})
        return verified

    # ------------------------------------------- finalization/acceptance

    def advance_authority_arcs(self):
        """The deterministic registered arcs past VERIFIED: finalization
        (SL, inside your permit grant), acceptance, release, completion,
        and readiness re-evaluation. Each is a registered transition the
        Sequencer alone applies; a missing permit or attestation leaves
        the sequence visibly waiting for YOUR registered authority —
        whoever did the work, retained member or not."""
        report = {"finalized": [], "accepted": [], "released": [],
                  "completed": [], "ready": [], "blocked": []}
        for row in self._sequences_in(["DECLARED"]):
            state = self.sequencer.mark_ready(row["sequence_id"])
            report["ready" if state == "READY" else "blocked"].append(
                row["sequence_id"])
        for row in self._sequences_in(["BLOCKED"]):
            result = self.sequencer.reevaluate_blocked(row["sequence_id"])
            if result["state"] == "READY":
                report["ready"].append(row["sequence_id"])
        for row in self._sequences_in(["VERIFIED"]):
            record = self._record_payload(row["record_hash"])
            # Latest by INSERTION order (rowid), never by minted id:
            # after a t-s18 stale-base arc two VERIFIED attempts can
            # exist, and only the newest carries the current fence
            # (F-CE-5; the F-M5-1 lesson again).
            att = self.ro.execute(
                "SELECT attempt_id, fence FROM attempts WHERE sequence_id"
                " = ? AND state = 'VERIFIED' ORDER BY rowid DESC"
                " LIMIT 1", (row["sequence_id"],)).fetchone()
            if record["finalization_class"] == "READ_ONLY":
                self.sequencer.accept_verified(row["sequence_id"],
                                               att["fence"])
                report["accepted"].append(row["sequence_id"])
                continue
            if not self._permit_available(row["sequence_id"]):
                continue  # cap-36: publication permits are yours
            if self.repo_path is None:
                continue
            seat = self.plan.conn.execute(
                "SELECT artifact_address FROM seats WHERE attempt_id = ?",
                (att["attempt_id"],)).fetchone()
            if seat is None or not seat["artifact_address"]:
                continue
            text = self.store.get(
                seat["artifact_address"]).decode("utf-8")
            effect = _loads(text, None)
            if not isinstance(effect, dict):
                import re
                m = re.search(
                    r'\{[^{}]*"target_ref"[^{}]*"new_value"[^{}]*\}',
                    text, re.DOTALL)
                effect = _loads(m.group(), {}) if m else {}
            needed = ("target_ref", "expected_old", "new_value")
            if not all(isinstance(effect.get(k), str) for k in needed):
                continue  # the specialist proposed no executable effect
            minted = self.sequencer.begin_commit(
                row["sequence_id"], att["attempt_id"],
                effect["target_ref"], effect["expected_old"],
                effect["new_value"])
            outcome = execute_local_cas(self.sequencer,
                                        minted["intent_id"], self.repo_path)
            if outcome["outcome"] == "HAPPENED":
                self.sequencer.commit_committed(
                    row["sequence_id"], att["fence"], outcome["receipt"])
                report["finalized"].append(row["sequence_id"])
            # ABSENT arms (t-s18/t-s19) stay with the M5 surface; the
            # intent outcome is durable and the planner sees it.
        for row in self._sequences_in(["COMMITTED"]):
            self.sequencer.accept_commit(row["sequence_id"], row["fence"])
            report["accepted"].append(row["sequence_id"])
        for row in self._sequences_in(["ACCEPTED"]):
            self.sequencer.release_dependencies(row["sequence_id"])
            report["released"].append(row["sequence_id"])
        for row in self._sequences_in(["DEPENDENCIES_RELEASED"]):
            self.sequencer.complete_sequence(row["sequence_id"])
            report["completed"].append(row["sequence_id"])
        return report

    # ------------------------------------------------------- settlements

    def settle_assignments(self, now):
        """Reconcile assignments against the ledger and record a
        capability demonstration for each concluded one (the workforce
        learning step: verified performance becomes workforce
        evidence). A concluded assignment's isolated workspace is given
        back to the transport: by then its result is either accepted
        (and, for SOURCE_MODIFYING work, reachable from the canonical
        ref) or not going to be."""
        settled = self.recorder.settle(now)
        release = getattr(self.transport, "release_workspace", None)
        if release is not None:
            for entry in settled:
                assignment = self.roster.assignment(entry["assignment_id"])
                if assignment and assignment["call_id"]:
                    try:
                        entry["workspace_released"] = bool(
                            release(assignment["call_id"]))
                    except (OSError, RuntimeError, ValueError):
                        entry["workspace_released"] = False
        return settled

    # --------------------------------------------------------- the sweep

    def advance(self, now):
        """One breadth-first Taskforce sweep over authority state."""
        self.roster.sync_pool(now)
        adopted = reconcile_dispatched(self.sequencer, self.transport)
        resumed = self.resume_interrupted(now)
        allocated = self.allocate_ready(now)
        # Newly allocated seats are DISPATCHED: adopt their sessions now
        # (same registered path a restart would take).
        adopted += reconcile_dispatched(self.sequencer, self.transport)
        supervised = self.supervise_running(now)
        collected = self.collect_running(now)
        verified = self.verify_evidence_complete()
        arcs = self.advance_authority_arcs()
        settled = self.settle_assignments(now)
        return {"allocated": allocated + resumed,
                "adopted": [a for a in adopted if a["action"] == "adopted"],
                "supervised": supervised,
                "collected": collected, "verified": verified, "arcs": arcs,
                "settled": settled}


__all__ = ["Taskforce", "MEMBER_STATES"]
