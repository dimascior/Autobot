"""Architecture conformance: the organizational model, pinned.

    Engineer                 solves technical problems
    Engineer as Project      organizes a temporary team around a solution
      Manager
    Taskforce                maintains the persistent retained specialist
                             organization
    Outside specialists      may take part in a project without Taskforce
                             membership
    AutoSwarm                executes assignments
    Authority Plane          independently proves results

Each test below is one sentence of that model, run over the real
authority runtime (Registry, Sequencer, Collector, verifier seam) with
rule policies and a scripted provider. Six identities are kept apart
throughout:

    TaskContractRecord    frozen work authority
    Project               a temporary coordinated undertaking
    ProjectAssignment     temporary participation in one workstream
    TaskforceMembership   persistent organizational membership
    TaskforceSeat         a retained organizational capability and role
    AttemptContract       one authority-bound execution
"""

import json
import sqlite3
import unittest

import support
from autobot import project_state
from autobot.models import instant
from intelligence import (
    ChiefEngineerPolicy,
    EngineerPolicy,
    EngineerService,
    SpecialistPool,
    query,
    validate_proposal,
)
from intelligence.planstore import readonly_view
from intelligence.proposals import ProposalError
from intelligence.workforce import (
    MEMBER_STATES,
    SEAT_STATES,
    SEAT_TRANSITIONS,
    specialist_identity,
)
from test_autonomous_pilot import QueueRunner, base_fields, result, roles
from test_taskforce_orchestration import (
    TwoTaskPolicy,
    WorkforceTestCase,
    assign,
    complete,
    investigate,
    retain,
)

# What a TaskContractRecord may never carry, as a key anywhere in it.
FORBIDDEN_KEYS = ("provider", "model", "specialist", "specialist_id",
                  "seat_id", "handle", "staffing")
PROVIDER, MODEL = "provider-zeta", "model-omega"


def walk(value):
    """Every (key, value) pair anywhere inside a JSON value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            for pair in walk(item):
                yield pair
    elif isinstance(value, list):
        for item in value:
            for pair in walk(item):
                yield pair


class ConformanceTestCase(WorkforceTestCase):
    """Two workers with a route that is easy to recognize anywhere."""

    def setUp(self):
        WorkforceTestCase.setUp(self)
        self.pool = SpecialistPool()
        for handle in self.HANDLES:
            self.pool.register("research", "investigator", PROVIDER,
                               model=MODEL, handle=handle)
        self.alpha = specialist_identity("alpha")
        self.beta = specialist_identity("beta")

    def _frozen(self, kind):
        """Every admitted authority object of one kind, as payloads."""
        if kind == "TaskContractRecord":
            rows = self.rt.db.conn.execute(
                "SELECT content_address FROM records").fetchall()
        else:
            rows = self.rt.db.conn.execute(
                "SELECT content_address FROM objects WHERE kind = ?",
                (kind,)).fetchall()
        return [json.loads(self.rt.store.get(r["content_address"]))
                for r in rows]

    def _run(self, policy, runner=None, **kwargs):
        self._objective()
        self.driver = self._driver(policy, runner=runner or QueueRunner(),
                                   **kwargs)
        return self.driver.run_until_stable()

    def _retained_project(self):
        """A, done by an outside specialist, explicitly retained; B,
        requested from the Taskforce."""
        self.transport.scripts = {"investigator": [result(), result()]}
        return self._run(TwoTaskPolicy("retain"))

    def _assert_nothing_names(self, forbidden_key, values):
        records = self._frozen("TaskContractRecord")
        self.assertEqual(len(records), 2)
        for record in records:
            for key, item in walk(record):
                self.assertNotEqual(key, forbidden_key)
                if isinstance(item, str):
                    for value in values:
                        self.assertNotIn(value, item)

    def _refused_at_the_boundary(self, field):
        requirement = {
            "objective": "Do the work.", "capability": "research",
            "roles": roles("investigator"), "guidance": "Do it."}
        base = {"objective_id": "obj-1", "rationale": "why",
                "snapshot": {"entries": 0},
                "provenance": {"findings": [], "events": []}}
        validate_proposal(dict(base, **{"class": "INVESTIGATE",
                                        "requirement": requirement}))
        with self.assertRaises(ProposalError):
            validate_proposal(dict(base, **{
                "class": "INVESTIGATE",
                "requirement": dict(requirement, **{field: "x"})}))
        with self.assertRaises(ProposalError):
            validate_proposal(dict(base, **{
                "class": "INVESTIGATE", "requirement": requirement,
                field: "x"}))

    def _a_policy_that_tries_reaches_nothing(self, field):
        """A policy that puts the field into a work requirement is
        stopped before the Registry is asked anything."""

        class Smuggler(ChiefEngineerPolicy):
            def plan(self, view):
                p = investigate(view, view.objectives[0]["objective_id"],
                                "A")
                p["requirement"][field] = "x"
                return [p]

        self.plan.add_objective("obj-smuggle", "Try it.", "you",
                                instant(self.clock.now()))
        calls = []
        real = self.rt.registry

        class SpyRegistry(object):
            def admit_payload(self, payload, submitted_by):
                calls.append(payload)
                return real.admit_payload(payload,
                                          submitted_by=submitted_by)

        service = EngineerService(self.plan, SpyRegistry(), self.env,
                                  self.clock, Smuggler())
        ro = readonly_view(self.dir / "autobot.db")
        try:
            with self.assertRaises(ProposalError):
                service.run_cycle(ro, project_state,
                                  instant(self.clock.now()))
        finally:
            ro.close()
        self.assertEqual(calls, [])
        self.assertEqual(self.plan.conn.execute(
            "SELECT COUNT(*) AS n FROM proposals WHERE objective_id ="
            " 'obj-smuggle'").fetchone()["n"], 0)


# ================================================== the TaskContractRecord

class TaskContractIdentityTests(ConformanceTestCase):
    def test_a_task_contract_cannot_name_a_provider(self):
        self._refused_at_the_boundary("provider")
        self._retained_project()
        self._assert_nothing_names("provider", [PROVIDER])
        self._a_policy_that_tries_reaches_nothing("provider")

    def test_a_task_contract_cannot_name_a_model(self):
        self._refused_at_the_boundary("model")
        self._retained_project()
        self._assert_nothing_names("model", [MODEL])
        self._a_policy_that_tries_reaches_nothing("model")

    def test_a_task_contract_cannot_name_a_specialist(self):
        for field in ("specialist_id", "specialist", "seat_id", "handle",
                      "staffing"):
            self._refused_at_the_boundary(field)
        self._retained_project()
        seat_ids = [s["seat_id"] for s in self._seats()]
        for field in ("specialist", "specialist_id", "seat_id", "handle",
                      "staffing"):
            self._assert_nothing_names(
                field, [self.alpha, self.beta] + seat_ids)
        # Nor does any AttemptContract: one authority-bound execution is
        # bound to the work, not to who did it.
        attempts = self._frozen("AttemptContract")
        self.assertEqual(len(attempts), 2)
        for contract in attempts:
            text = json.dumps(contract)
            for value in [self.alpha, self.beta] + seat_ids:
                self.assertNotIn(value, text)
        self._a_policy_that_tries_reaches_nothing("specialist_id")

    def test_the_same_work_is_the_same_contract_whoever_does_it(self):
        """Two organizations staff the identical work requirement with
        different people on different routes: the contract digest is the
        same, because staffing was never part of it."""
        from contract_library import compute_tiers
        from intelligence import compile_proposal
        self.transport.scripts = {"investigator": [result()]}

        class Selecting(ChiefEngineerPolicy):
            def plan(self, view):
                oid = view.objectives[0]["objective_id"]
                if not view.authority["sequences"]:
                    return [investigate(view, oid, "A")]
                needs = view.projects[oid]["needs_staffing"]
                return [assign(view, oid, n["sequence_id"], "beta")
                        for n in needs]

        self._run(Selecting())
        assignment = self._assignments()[0]
        self.assertEqual(assignment["specialist_id"], self.beta)
        frozen = self.rt.db.conn.execute(
            "SELECT contract_digest FROM sequences").fetchone()[
                "contract_digest"]
        body = json.loads(self._rows(
            "SELECT body FROM proposals WHERE class = 'INVESTIGATE'")[0][
                "body"])
        self.pool.reroute("alpha", provider="another-provider",
                          model="another-model")
        recompiled = compile_proposal(body, self.env, self.clock)
        _rd, contract_digest, _rh = compute_tiers(recompiled)
        self.assertEqual(contract_digest, frozen)


# ==================================================== the two staffing paths

class StaffingPathConformanceTests(ConformanceTestCase):
    def test_the_project_manager_assigns_an_outside_specialist(self):
        """Engineer/PM -> explicitly selects an outside specialist -> a
        project-specific assignment. The Engineer opens the project,
        creates the workstream and names the specialist for it in one
        answer; the pump applies exactly that."""

        class ProjectManager(ChiefEngineerPolicy):
            def plan(self, view):
                if not view.objectives:
                    return []
                oid = view.objectives[0]["objective_id"]
                sequences = view.authority["sequences"]
                if not sequences:
                    opened = base_fields(view, oid)
                    opened.update({
                        "class": "OPEN_PROJECT",
                        "rationale": "the solution needs a specialist",
                        "project": {"title": "Audit",
                                    "solution": "One independent audit."}})
                    work = investigate(view, oid, "audit")
                    work["workstream"] = "audit"
                    staffing = assign(view, oid, None, "beta",
                                      workstream="audit")
                    del staffing["sequence_id"]
                    return [opened, work, staffing]
                if all(s["state"] == "COMPLETED" for s in sequences):
                    return [complete(view, oid)]
                return []

        self.transport.scripts = {"investigator": [result()]}
        final = self._run(ProjectManager())
        self.assertEqual(final["active_objectives"], [])
        assignment = self._assignments()[0]
        self.assertEqual(assignment["specialist_id"], self.beta)
        self.assertEqual(assignment["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(assignment["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(assignment["standing_at_assignment"], "OUTSIDE")
        project = self._projects()[0]
        self.assertEqual(project["opened_by"], "PROJECT_MANAGER")
        self.assertEqual(project["solution"], "One independent audit.")
        self.assertEqual(assignment["project_id"], project["project_id"])
        workstream = self._rows("SELECT * FROM project_workstreams")[0]
        self.assertEqual(workstream["workstream_key"], "audit")
        self.assertEqual(assignment["workstream_id"],
                         workstream["workstream_id"])
        decision = self._rows("SELECT * FROM assignment_decisions")[0]
        self.assertEqual(
            (decision["mode"], decision["specialist_id"],
             decision["status"], decision["assignment_id"]),
            ("outside_specialist", self.beta, "APPLIED",
             assignment["assignment_id"]))
        self.assertEqual(assignment["assignment_decision"],
                         decision["decision_id"])
        # Staffed before the Taskforce was ever asked: it was the Project
        # Manager's decision from the start.
        self.assertEqual(self._rows(
            "SELECT * FROM workforce_events WHERE event_kind ="
            " 'staffing_unavailable'"), [])
        # Provider and model were resolved from the specialist's route,
        # downstream of the decision — which named neither.
        self.assertEqual((assignment["provider"], assignment["model"]),
                         (PROVIDER, MODEL))
        self.assertNotIn(PROVIDER, json.dumps(self._rows(
            "SELECT body FROM proposals")))

    def test_a_capability_request_is_satisfied_by_a_retained_member(self):
        """Engineer/PM -> requests a capability from the Taskforce -> the
        Taskforce resolves a retained member. B's proposal names nobody,
        and no project-manager staffing decision exists for it."""
        self._retained_project()
        first, second = self._assignments()
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["decided_by"], "TASKFORCE")
        self.assertIsNone(second["assignment_decision"])
        self.assertEqual(self._rows(
            "SELECT * FROM assignment_decisions WHERE sequence_id = ?",
            (second["sequence_id"],)), [])
        body = json.loads(self.plan.proposal(
            second["authorized_by_proposal"])["body"])
        self.assertEqual(body["class"], "INVESTIGATE")
        self.assertNotIn(second["specialist_id"], json.dumps(body))
        reason = json.loads(second["selected_reason"])
        self.assertIsNone(reason["directive"])
        self.assertEqual(reason["decided_by"], "TASKFORCE")

    def test_the_taskforce_never_brings_an_outside_specialist(self):
        """With nobody retained and a policy that makes no staffing
        decision at all, the work WAITS: the Taskforce resolves retained
        members only."""

        class NoStaffing(ChiefEngineerPolicy):
            def plan(self, view):
                oid = view.objectives[0]["objective_id"]
                if not view.authority["sequences"]:
                    return [investigate(view, oid, "A")]
                return []

            def staff(self, view, proposals=()):
                return []  # the Project Manager decides nothing

        self.transport.scripts = {"investigator": [result()]}
        self._run(NoStaffing())
        self.assertEqual(self._assignments(), [])
        self.assertEqual(list(self._states().values()), ["READY"])
        waits = self._rows("SELECT facts FROM workforce_events WHERE"
                           " event_kind = 'staffing_unavailable'")
        self.assertEqual(len(waits), 1)
        facts = json.loads(waits[0]["facts"])
        self.assertEqual(facts["cause"], "no_retained_seat")
        self.assertEqual(sorted(c["handle"] for c in
                                facts["outside_candidates"]),
                         ["alpha", "beta"])
        self.assertEqual(self.transport.launch_log, [])


# ================================================ membership is not project

class MembershipConformanceTests(ConformanceTestCase):
    def test_an_outside_assignment_creates_no_taskforce_membership(self):
        """While the outside specialist is executing project work — and
        after — the Taskforce has no row about it at all."""
        self.transport.scripts = {"investigator": []}  # A keeps running
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        for _ in range(8):
            self.driver.step()
        assignment = self._assignments()[0]
        self.assertEqual(assignment["status"], "EXECUTING")
        self.assertEqual(assignment["selection_path"], "OUTSIDE_SPECIALIST")
        for table in ("taskforce_memberships", "taskforce_seats",
                      "seat_history"):
            self.assertEqual(self._rows("SELECT * FROM %s" % table), [],
                             table)
        self.assertEqual(
            [(m["joined_as"], m["status"]) for m in self._team()],
            [("OUTSIDE_SPECIALIST", "ACTIVE")])
        roster = self.driver.taskforce.roster
        self.assertFalse(roster.is_member(assignment["specialist_id"]))
        # The schema itself has no provisional membership to fall into.
        membership_states = ("PROVISIONAL", "QUALIFIED",
                             "PROVISIONAL_TASKFORCE_MEMBER")
        for state in membership_states:
            self.assertNotIn(state, SEAT_STATES)
            with self.assertRaises(sqlite3.IntegrityError):
                with self.plan.transaction() as cur:
                    cur.execute(
                        "INSERT INTO taskforce_memberships (membership_id,"
                        " specialist_id, status, joined_utc,"
                        " joined_by_decision, updated_utc)"
                        " VALUES ('tm_x', ?, ?, 't', 'd', 't')",
                        (assignment["specialist_id"], state))
        self.assertEqual(
            sorted(set(SEAT_TRANSITIONS.values())),
            ["deactivation_decision", "established_threshold",
             "release_decision", "retention_decision"])

    def test_verified_outside_work_creates_a_capability_demonstration(
            self):
        """The demonstration REFERENCES what authority admitted — the
        attempt, the call, the frozen record, the verification receipts,
        the execution and candidate receipts, the acceptance — and copies
        none of it."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._run(TwoTaskPolicy("none"))
        assignment = self._assignments()[0]
        demo = self._demonstrations()[0]
        self.assertEqual(demo["outcome"], "QUALIFYING")
        self.assertEqual(demo["standing"], "OUTSIDE")
        self.assertIsNone(demo["seat_id"])
        self.assertEqual(demo["specialist_id"], assignment["specialist_id"])
        self.assertEqual(demo["project_id"], assignment["project_id"])
        attempt = self.rt.db.conn.execute(
            "SELECT a.attempt_id, a.state, a.sequence_id, c.call_id FROM"
            " attempts a JOIN attempt_calls c ON c.attempt_id ="
            " a.attempt_id WHERE a.attempt_id = ?",
            (demo["attempt_id"],)).fetchone()
        self.assertEqual((attempt["state"], attempt["call_id"]),
                         ("VERIFIED", demo["call_id"]))
        sequence = self.rt.db.conn.execute(
            "SELECT s.record_hash, s.contract_digest, r.task_contract_id"
            " FROM sequences s JOIN records r ON r.record_hash ="
            " s.record_hash WHERE s.sequence_id = ?",
            (demo["sequence_id"],)).fetchone()
        self.assertEqual(
            (demo["record_hash"], demo["contract_digest"],
             demo["task_contract_id"]),
            (sequence["record_hash"], sequence["contract_digest"],
             sequence["task_contract_id"]))
        ledger = {(r["assertion_id"], r["receipt_sha256"]) for r in
                  self.rt.db.conn.execute(
                      "SELECT assertion_id, receipt_sha256 FROM"
                      " attempt_verifications WHERE attempt_id = ?",
                      (demo["attempt_id"],)).fetchall()}
        self.assertTrue(ledger)
        self.assertEqual(
            {(r["assertion_id"], r["receipt_sha256"])
             for r in json.loads(demo["verification_refs"])}, ledger)
        refs = json.loads(demo["evidence_refs"])
        receipt = self.rt.db.conn.execute(
            "SELECT content_sha256 FROM attempt_receipts WHERE attempt_id"
            " = ? ORDER BY rowid DESC LIMIT 1",
            (demo["attempt_id"],)).fetchone()
        self.assertEqual(refs["execution_receipt"],
                         receipt["content_sha256"])
        for key in ("candidate_receipt", "acceptance", "release"):
            self.assertTrue(refs[key].startswith("sha256:"), key)
        criteria = json.loads(demo["criteria"])
        for name in ("task_completed", "candidate_accepted",
                     "verification_passed", "scope_respected",
                     "governance_satisfied", "repair_coherent",
                     "outcome_useful"):
            self.assertTrue(criteria[name], name)

    def test_a_capability_demonstration_alone_creates_no_membership(self):
        """Two verified, accepted, QUALIFYING pieces of work and a
        completed project later: no membership. The specialist is a
        retention candidate, which is not a standing, and the Taskforce
        still resolves nobody for the capability."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._run(TwoTaskPolicy("none"))
        self.assertEqual([d["outcome"] for d in self._demonstrations()],
                         ["QUALIFYING", "QUALIFYING"])
        self.assertEqual(self._memberships(), [])
        self.assertEqual(self._seats(), [])
        roster = self.driver.taskforce.roster
        self.assertFalse(roster.is_member(self.alpha))
        self.assertIsNone(self.driver.taskforce.resolver.resolve(
            "research", role="investigator"))
        self.assertEqual(
            [(c["specialist_id"], len(c["qualifying_demonstrations"]))
             for c in self._candidates()], [(self.alpha, 2)])
        self.assertEqual({a["standing_at_assignment"]
                          for a in self._assignments()}, {"OUTSIDE"})

    def test_explicit_retention_creates_retained_membership(self):
        """outside specialist -> verified work -> CapabilityDemonstration
        -> RETAIN_SPECIALIST -> TaskforceMembership -> retained seat."""
        self._retained_project()
        membership = self._memberships()[0]
        seat = self._seats()[0]
        self.assertEqual((membership["specialist_id"],
                          membership["status"]), (self.alpha, "ACTIVE"))
        self.assertEqual((seat["membership_id"], seat["status"],
                          seat["capability"], seat["role"]),
                         (membership["membership_id"], "RETAINED",
                          "research", "investigator"))
        decision = self._rows(
            "SELECT * FROM retention_decisions WHERE decision_id = ?",
            (membership["joined_by_decision"],))[0]
        self.assertEqual((decision["kind"], decision["seat_id"]),
                         ("RETAIN", seat["seat_id"]))
        self.assertEqual(json.loads(decision["demonstration_ids"]),
                         [self._demonstrations()[0]["demonstration_id"]])
        proposal = self.plan.proposal(decision["proposal_id"])
        self.assertEqual((proposal["class"], proposal["status"]),
                         ("RETAIN_SPECIALIST", "APPLIED"))
        first = self._assignments()[0]
        # The membership began AFTER the outside work was settled: the
        # first assignment was, and stays, an outside one.
        self.assertEqual(first["standing_at_assignment"], "OUTSIDE")
        self.assertIsNone(first["seat_id"])
        self.assertGreaterEqual(membership["joined_utc"],
                                first["settled_utc"])

    def test_retention_without_qualifying_evidence_is_refused(self):
        """A retention that binds no verified, qualifying demonstration
        of that specialist creates nothing."""

        class Eager(ChiefEngineerPolicy):
            def plan(self, view):
                if not view.objectives:
                    return []
                oid = view.objectives[0]["objective_id"]
                if not view.authority["sequences"]:
                    return [investigate(view, oid, "A")]
                if view.taskforce["active_assignments"]:
                    # Retain it while it is still working: there is no
                    # verified work to bind.
                    busy = view.taskforce["active_assignments"][0]
                    return [retain(view, oid, {
                        "specialist_id": busy["specialist_id"],
                        "capability": busy["capability"],
                        "role": busy["role"],
                        "qualifying_demonstrations": ["dem:" + "0" * 64]})]
                return []

        self.transport.scripts = {"investigator": []}
        self._run(Eager())
        refused = self._rows("SELECT * FROM proposals WHERE class ="
                             " 'RETAIN_SPECIALIST'")
        self.assertEqual([p["status"] for p in refused], ["REJECTED"])
        self.assertIn("does not exist", refused[0]["note"])
        self.assertEqual(self._memberships(), [])
        self.assertEqual(self._seats(), [])

    def test_a_retained_member_is_later_resolved_by_the_taskforce(self):
        """The later task is a NEW authority-bound execution of a NEW
        frozen record, done by the SAME specialist — because the
        Taskforce resolved its retained seat, where without the
        membership a Project Manager would have had to select someone."""
        self._retained_project()
        first, second = self._assignments()
        seat = self._seats()[0]
        self.assertEqual(second["specialist_id"], first["specialist_id"])
        self.assertEqual(second["seat_id"], seat["seat_id"])
        self.assertEqual(second["standing_at_assignment"], "RETAINED")
        self.assertNotEqual(second["sequence_id"], first["sequence_id"])
        self.assertNotEqual(second["attempt_id"], first["attempt_id"])
        reason = json.loads(second["selected_reason"])
        self.assertEqual(reason["chosen"]["seat_id"], seat["seat_id"])
        self.assertEqual(reason["retained_ranked"][0]["qualifying"], 1)
        self.assertEqual(reason["outside_alternative"]["handle"], "beta")
        contracts = [r["attempt_contract_hash"] for r in
                     self.rt.db.conn.execute(
                         "SELECT attempt_contract_hash FROM attempts ORDER"
                         " BY rowid").fetchall()]
        self.assertEqual(len(set(contracts)), 2)
        self.assertEqual(seat["last_assignment_id"],
                         second["assignment_id"])


# ============================================ retention grants no authority

class AuthoritySeparationConformanceTests(ConformanceTestCase):
    def test_retained_status_does_not_weaken_authority_or_verification(
            self):
        """The retained member's work fails verification exactly as an
        outside specialist's would: the attempt is REJECTED, nothing is
        accepted or released, and the demonstration is NOT_QUALIFYING —
        whatever the specialist's own report claims."""
        self.transport.scripts = {"investigator": [
            result(), result(artifact=b"ALL CHECKS PASSED. Ship it.\n")]}
        self._run(TwoTaskPolicy("retain"),
                  runner=QueueRunner({"a2": ["PASS", "FAIL"]}))
        first, second = self._assignments()
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["outcome"], "VERIFICATION_FAILED")
        attempt = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (second["attempt_id"],)).fetchone()
        self.assertEqual(attempt["state"], "REJECTED")
        self.assertNotIn(self._states()[second["sequence_id"]],
                         ("ACCEPTED", "DEPENDENCIES_RELEASED",
                          "COMPLETED"))
        self.assertEqual(len(self.rt.db.conn.execute(
            "SELECT 1 FROM released_edges WHERE predecessor_sequence_id"
            " = ?", (second["sequence_id"],)).fetchall()), 0)
        demo = self._demonstrations()[1]
        self.assertEqual((demo["outcome"], demo["standing"]),
                         ("NOT_QUALIFYING", "RETAINED"))
        # Both attempts stood in front of the same objects: a frozen
        # record the service submitted, an AttemptContract authority
        # minted, a COMPLETE ExecutionReceipt, admitted verification rows.
        for assignment in (first, second):
            contract = self.rt.db.conn.execute(
                "SELECT o.kind FROM attempts a JOIN objects o ON"
                " o.object_hash = a.attempt_contract_hash WHERE"
                " a.attempt_id = ?", (assignment["attempt_id"],)).fetchone()
            self.assertEqual(contract["kind"], "AttemptContract")
            receipt = self.rt.db.conn.execute(
                "SELECT completeness FROM attempt_receipts WHERE"
                " attempt_id = ? ORDER BY rowid DESC LIMIT 1",
                (assignment["attempt_id"],)).fetchone()
            self.assertEqual(receipt["completeness"], "COMPLETE")
            self.assertGreaterEqual(self.rt.db.conn.execute(
                "SELECT COUNT(*) AS n FROM attempt_verifications WHERE"
                " attempt_id = ?", (assignment["attempt_id"],)
            ).fetchone()["n"], 2)

    def test_a_workforce_decision_touches_no_authority_state(self):
        """Retaining appends nothing to the authority journal and asks
        the Registry nothing: membership is Taskforce state."""
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        for _ in range(12):
            self.driver.step()
            if self._candidates():
                break
        candidate = self._candidates()[0]
        calls = []
        real = self.rt.registry

        class SpyRegistry(object):
            def admit_payload(self, payload, submitted_by):
                calls.append(submitted_by)
                return real.admit_payload(payload,
                                          submitted_by=submitted_by)

        class OnlyRetain(EngineerPolicy):
            def plan(self, view):
                return [retain(view, view.objectives[0]["objective_id"],
                               candidate)]

        with self.plan.transaction() as cur:
            self.plan.emit_workforce_event(
                cur, "test:wake", "demonstration_recorded",
                candidate["specialist_id"], None,
                instant(self.clock.now()))
        service = EngineerService(
            self.plan, SpyRegistry(), self.env, self.clock, OnlyRetain(),
            roster=self.driver.taskforce.roster)
        ro = readonly_view(self.dir / "autobot.db")
        try:
            before = self._journal_count()
            report = service.run_cycle(ro, project_state,
                                       instant(self.clock.now()))
        finally:
            ro.close()
        self.assertEqual(report["proposals"][0]["status"], "APPLIED")
        self.assertEqual(calls, [])
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(len(self._memberships()), 1)


# ==================================================== independence

class ReviewerConflictPolicy(ChiefEngineerPolicy):
    """A reviewer is retained; the same specialist then does research;
    that research is reviewed. The retained reviewer is the author."""

    def __init__(self):
        self.tried_author = False

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        project = view.projects[oid]
        streams = project["workstreams"]
        proposals = [retain(view, oid, c)
                     for c in view.taskforce["retention_candidates"]
                     if c["capability"] == "verification"]
        done = bool(streams) and all(w["state"] == "COMPLETED"
                                     for w in streams)
        if not streams:
            proposals.append(investigate(
                view, oid, "V0", capability="verification",
                role="reviewer"))
        elif len(streams) == 1 and done:
            proposals.append(investigate(view, oid, "A"))
        elif len(streams) == 2 and done:
            p = base_fields(view, oid)
            p.update({
                "class": "REQUEST_REVIEW",
                "rationale": "the research needs an independent review",
                "review_of": {"record_hash": streams[1]["record_hash"]},
                "requirement": {
                    "objective": "Review research A.",
                    "capability": "verification",
                    "roles": roles("reviewer"),
                    "guidance": "Review the accepted research."}})
            proposals.append(p)
        for need in project["needs_staffing"]:
            if need["independent_of"] and not self.tried_author:
                self.tried_author = True
                proposals.append(assign(view, oid, need["sequence_id"],
                                        "alpha"))
        if len(streams) == 3 and done and not proposals:
            proposals.append(complete(view, oid))
        return proposals


class IndependenceConformanceTests(ConformanceTestCase):
    def test_an_independent_reviewer_cannot_be_the_prohibited_specialist(
            self):
        """alpha holds the ONLY retained reviewer seat and is the author
        of the work under review. The Taskforce does not resolve it; the
        Project Manager naming it is refused; the review is done by
        someone else."""
        self.pool = SpecialistPool()
        self.pool.register("verification", "reviewer", PROVIDER,
                           handle="alpha")
        self.pool.register("research", "investigator", PROVIDER,
                           handle="alpha")
        self.pool.register("verification", "reviewer", PROVIDER,
                           handle="beta")
        self.transport.scripts = {"reviewer": [result(), result()],
                                  "investigator": [result()]}
        policy = ReviewerConflictPolicy()
        final = self._run(policy)
        self.assertEqual(final["active_objectives"], [])
        self.assertTrue(policy.tried_author)
        seed, work, review = self._assignments()
        self.assertEqual(self._handle(seed["specialist_id"]), "alpha")
        self.assertEqual(self._handle(work["specialist_id"]), "alpha")
        # alpha IS the retained reviewer ...
        seat = self._seats()[0]
        self.assertEqual((seat["specialist_id"], seat["capability"],
                          seat["status"]),
                         (self.alpha, "verification", "RETAINED"))
        # ... and the Taskforce did not resolve it for this review.
        waits = [json.loads(e["facts"]) for e in self._rows(
            "SELECT facts FROM workforce_events WHERE event_kind ="
            " 'staffing_unavailable' AND subject_id = ?",
            (review["sequence_id"],))]
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["cause"], "retained_seats_prohibited")
        self.assertEqual(waits[0]["independent_of"], [self.alpha])
        # The Project Manager's explicit selection of the author was
        # refused, with the reason.
        refused = self._rows(
            "SELECT * FROM proposals WHERE class = 'ASSIGN_SPECIALIST' AND"
            " status = 'REJECTED'")
        self.assertEqual(len(refused), 1)
        self.assertEqual(json.loads(refused[0]["body"])["staffing"]
                         ["specialist_id"], self.alpha)
        self.assertIn("independently of its subject", refused[0]["note"])
        # The review was done by the other reviewer.
        self.assertEqual(self._handle(review["specialist_id"]), "beta")
        self.assertEqual(review["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(json.loads(review["selected_reason"])[
            "independent_of"], [self.alpha])
        self.assertEqual(set(self._states().values()), {"COMPLETED"})


class WorkGraphPolicy(ChiefEngineerPolicy):
    """The work and its independent review, laid out in one decision."""

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        streams = view.projects[oid]["workstreams"]
        if not streams:
            p = base_fields(view, oid)
            p.update({
                "class": "DECOMPOSE_TASK",
                "rationale": "the research and its independent review",
                "parts": [
                    {"part_id": "work", "requirement": {
                        "objective": "Do the research.",
                        "finalization_class": "READ_ONLY",
                        "capability": "research",
                        "roles": roles("investigator"),
                        "guidance": "Research it."}},
                    {"part_id": "review", "review_of_part": "work",
                     "requirement": {
                         "objective": "Review the research.",
                         "capability": "verification",
                         "roles": roles("reviewer"),
                         "guidance": "Review the accepted research."}},
                ]})
            return [p]
        if all(w["state"] == "COMPLETED" for w in streams):
            return [complete(view, oid)]
        return []


class PreAssigningPolicy(WorkGraphPolicy):
    """Staffs the whole graph when it lays it out — and names the same
    specialist for the work and for its review."""

    def plan(self, view):
        proposals = WorkGraphPolicy.plan(self, view)
        if not view.objectives:
            return proposals
        oid = view.objectives[0]["objective_id"]
        if proposals and proposals[0]["class"] == "DECOMPOSE_TASK":
            for key in ("work", "review"):
                staffing = assign(view, oid, None, "alpha", workstream=key)
                del staffing["sequence_id"]
                proposals.append(staffing)
        for need in view.projects[oid]["needs_staffing"]:
            proposals.append(assign(view, oid, need["sequence_id"],
                                    "beta"))
        return proposals


class WorkGraphConformanceTests(ConformanceTestCase):
    def test_an_author_assigned_to_its_review_in_advance_is_refused(self):
        """When the review was staffed, nobody had done the work yet, so
        the selection looked fine. Independence is checked again when the
        review is launched: the author is refused, the decision is closed
        with the reason, and the Project Manager decides again."""
        self.pool = SpecialistPool()
        self.pool.register("verification", "reviewer", PROVIDER,
                           handle="alpha")
        self.pool.register("research", "investigator", PROVIDER,
                           handle="alpha")
        self.pool.register("verification", "reviewer", PROVIDER,
                           handle="beta")
        self.transport.scripts = {"investigator": [result()],
                                  "reviewer": [result()]}
        final = self._run(PreAssigningPolicy())
        self.assertEqual(final["active_objectives"], [])
        work, review = self._assignments()
        self.assertEqual(self._handle(work["specialist_id"]), "alpha")
        self.assertEqual(self._handle(review["specialist_id"]), "beta")
        decisions = self.plan.assignment_decisions(
            sequence_id=review["sequence_id"])
        self.assertEqual(
            [(d["specialist_id"], d["status"]) for d in decisions],
            [(self.alpha, "REFUSED"), (self.beta, "APPLIED")])
        self.assertIn("independently of its subject", decisions[0]["note"])
        events = self._rows("SELECT facts FROM workforce_events WHERE"
                            " event_kind = 'assignment_refused'")
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]["facts"])["specialist_id"],
                         self.alpha)
        # The work's own assignment was applied as decided.
        self.assertEqual(
            [(d["specialist_id"], d["status"]) for d in
             self.plan.assignment_decisions(
                 sequence_id=work["sequence_id"])],
            [(self.alpha, "APPLIED")])

    def test_a_review_declared_with_its_work_waits_and_stays_independent(
            self):
        """The Project Manager lays out the work graph at once. The
        review is BLOCKED by authority until the work is accepted, and
        only then staffed — independently of the specialist that did the
        work, although that specialist offers review and comes first in
        the catalog."""
        self.pool = SpecialistPool()
        self.pool.register("verification", "reviewer", PROVIDER,
                           handle="alpha")
        self.pool.register("research", "investigator", PROVIDER,
                           handle="alpha")
        self.pool.register("verification", "reviewer", PROVIDER,
                           handle="beta")
        self.transport.scripts = {"investigator": [], "reviewer": []}
        self._objective()
        self.driver = self._driver(WorkGraphPolicy(), runner=QueueRunner())
        for _ in range(8):
            self.driver.step()
        project = query.project_state(
            self.plan, self.driver.ro, self._projects()[0]["project_id"])
        streams = {w["workstream"]: w for w in project["workstreams"]}
        self.assertEqual(sorted(streams), ["review", "work"])
        self.assertEqual(streams["review"]["class"], "REQUEST_REVIEW")
        self.assertEqual(streams["review"]["finalization_class"],
                         "READ_ONLY")
        # The work is executing; the review is blocked on it and nobody
        # is assigned to it.
        self.assertEqual(streams["work"]["assignments"][0]["status"],
                         "EXECUTING")
        self.assertEqual(streams["review"]["state"], "BLOCKED")
        self.assertEqual(streams["review"]["assignments"], [])
        self.assertEqual(
            streams["review"]["depends_on"],
            [{"workstream": "work",
              "sequence_id": streams["work"]["sequence_id"],
              "released": False}])
        self.assertEqual(project["needs_staffing"]
                         if "needs_staffing" in project else [], [])

        self.transport.scripts = {"investigator": [result()],
                                  "reviewer": [result()]}
        final = self.driver.run_until_stable()
        self.assertEqual(final["active_objectives"], [])
        work, review = self._assignments()
        self.assertEqual(self._handle(work["specialist_id"]), "alpha")
        self.assertEqual(self._handle(review["specialist_id"]), "beta")
        self.assertEqual(json.loads(review["selected_reason"])[
            "independent_of"], [self.alpha])
        # Dependency ordering came from AUTHORITY: blocked (t-s01), then
        # released (t-s02), and staffed only after the release.
        rules = self._rules_for(review["sequence_id"])
        self.assertLess(rules.index("t-s01"), rules.index("t-s02"))
        released = self.rt.db.conn.execute(
            "SELECT released_utc FROM released_edges WHERE"
            " predecessor_sequence_id = ? LIMIT 1",
            (work["sequence_id"],)).fetchone()
        self.assertGreaterEqual(review["selected_utc"],
                                released["released_utc"])
        self.assertEqual(
            [d["released"] for d in query.dependencies(
                self.plan, self.driver.ro, self.objective_id)], [True])


# ===================================== the project ends; the Taskforce stays

class OneMoreTask(ChiefEngineerPolicy):
    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        streams = view.projects[oid]["workstreams"]
        if not streams:
            return [investigate(view, oid, "C")]
        if all(w["state"] == "COMPLETED" for w in streams):
            return [complete(view, oid)]
        return []


class ProjectLifetimeConformanceTests(ConformanceTestCase):
    def test_project_completion_settles_the_team_and_membership_persists(
            self):
        """Project participation is temporary; Taskforce membership is
        persistent. Completing the project ends every participation and
        leaves no open assignment state behind — and the member it
        produced is still a member, resolved by the Taskforce for the
        NEXT project."""
        self._retained_project()
        project = self._projects()[0]
        self.assertEqual(project["status"], "COMPLETED")
        self.assertIsNotNone(project["closed_utc"])
        self.assertIsNone(self.plan.active_project(self.objective_id))
        # Temporary state: all of it settled.
        self.assertEqual(
            [(m["status"], m["end_reason"])
             for m in self._team(project["project_id"])],
            [("RELEASED", "project_completed")])
        self.assertEqual({a["status"] for a in self._assignments()},
                         {"SETTLED"})
        self.assertEqual(self._rows(
            "SELECT * FROM assignment_decisions WHERE status = 'OPEN'"),
            [])
        self.assertEqual(self._rows(
            "SELECT * FROM seats WHERE status = 'AUTHORIZED'"), [])
        state = query.project_state(self.plan, self.driver.ro,
                                    project["project_id"])
        self.assertEqual(state["active_team"], [])
        self.assertEqual(state["open_assignments"], [])
        # Persistent state: untouched.
        membership = self._memberships()[0]
        seat = self._seats()[0]
        self.assertEqual((membership["status"], membership["ended_utc"]),
                         ("ACTIVE", None))
        self.assertEqual(seat["status"], "RETAINED")
        self.assertTrue(self.driver.taskforce.roster.is_member(self.alpha))

        # The next objective is a new project; the member joins it AS a
        # member, through the Taskforce.
        self.transport.scripts = {"investigator": [result()]}
        self.plan.add_objective("obj-next", "One more investigation.",
                                "you", instant(self.clock.now()))
        self.driver.engineer.policy = OneMoreTask()
        self.driver.run_until_stable()
        projects = self._projects()
        self.assertEqual(len(projects), 2)
        self.assertEqual(projects[1]["objective_id"], "obj-next")
        third = self._assignments()[2]
        self.assertEqual(third["project_id"], projects[1]["project_id"])
        self.assertEqual((third["specialist_id"], third["selection_path"],
                          third["decided_by"]),
                         (self.alpha, "RETAINED_SEAT", "TASKFORCE"))
        self.assertEqual(
            [(m["specialist_id"], m["joined_as"])
             for m in self._team(projects[1]["project_id"])],
            [(self.alpha, "RETAINED_MEMBER")])
        self.assertEqual(len(self._memberships()), 1)

    def test_the_six_identities_are_separate_records(self):
        """One executed, retained workstream shows all six — each its own
        identity, the two authority ones in the ledger, the other four in
        the intelligence store, and none standing in for another."""
        self._retained_project()
        assignment = self._assignments()[1]
        sequence = self.rt.db.conn.execute(
            "SELECT s.record_hash, r.task_contract_id FROM sequences s"
            " JOIN records r ON r.record_hash = s.record_hash WHERE"
            " s.sequence_id = ?", (assignment["sequence_id"],)).fetchone()
        attempt = self.rt.db.conn.execute(
            "SELECT attempt_contract_hash FROM attempts WHERE attempt_id"
            " = ?", (assignment["attempt_id"],)).fetchone()
        identities = {
            "TaskContractRecord": sequence["record_hash"],
            "Project": self._projects()[0]["project_id"],
            "ProjectAssignment": assignment["assignment_id"],
            "TaskforceMembership":
                self._memberships()[0]["membership_id"],
            "TaskforceSeat": self._seats()[0]["seat_id"],
            "AttemptContract": attempt["attempt_contract_hash"],
        }
        self.assertEqual(len(set(identities.values())), 6)
        for name, prefix in (("Project", "pj_"),
                             ("ProjectAssignment", "asg_"),
                             ("TaskforceMembership", "tm_"),
                             ("TaskforceSeat", "ts_")):
            self.assertTrue(identities[name].startswith(prefix), name)
        # The authority ledger holds no project, assignment, membership
        # or seat — and no table for any of them.
        tables = {r["name"] for r in self.rt.db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for name in ("projects", "project_assignments", "project_team",
                     "taskforce_memberships", "taskforce_seats"):
            self.assertNotIn(name, tables)
        ledger_text = json.dumps(
            self._frozen("TaskContractRecord")
            + self._frozen("AttemptContract"))
        for name in ("Project", "ProjectAssignment",
                     "TaskforceMembership", "TaskforceSeat"):
            self.assertNotIn(identities[name], ledger_text, name)
        # And the intelligence store holds no authority table.
        plan_tables = {r["name"] for r in self.plan.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        for name in ("sequences", "attempts", "records", "objects",
                     "attempt_verifications", "attempt_candidates",
                     "attempt_receipts", "transition_journal"):
            self.assertNotIn(name, plan_tables)
        self.assertEqual(sorted(MEMBER_STATES),
                         ["ESTABLISHED", "RETAINED"])


if __name__ == "__main__":
    unittest.main()
