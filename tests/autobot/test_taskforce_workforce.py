"""The Taskforce and the project team, at the roster's own surface.

Pins the organization's deterministic rules without any provider: the
specialist catalog and stable specialist identity; the two staffing paths
(a capability request the Taskforce resolves to a retained member, an
outside specialist the Project Manager selects); that an assignment
creates no membership and neither does a demonstration; that an explicit
retention decision is the only thing that does; the seat transition
table; release with and without a membership change; the temporary
project team; the established threshold; provider replacement; what a
proposal may and may not name; the supervision policy, findings
extraction, prompt composition, and the plan-store migration that carries
a v1 store forward untouched.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import support
from intelligence import (
    ACTING_AS,
    PROPOSAL_CLASSES,
    PlanStore,
    SpecialistPool,
    TaskforceRoster,
    TaskforceSeatResolver,
)
from intelligence import prompts, supervision
from intelligence.findings import admit_finding, extract_findings
from intelligence.planstore import (
    _DDL_V1,
    DEFAULT_BUDGET,
    PLAN_SCHEMA_VERSION,
    PlanStoreTooNew,
)
from intelligence.workforce import (
    OUTSIDE,
    RELEASE_REASONS,
    SEAT_TRANSITIONS,
    WorkforceError,
    assignment_identity,
    membership_identity,
    seat_identity,
    specialist_identity,
)

T0 = "2026-11-09T12:00:00.000Z"
T1 = "2026-11-09T12:05:00.000Z"
T2 = "2026-11-09T12:10:00.000Z"
T3 = "2026-11-09T12:15:00.000Z"


class RosterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.pool = SpecialistPool()
        for handle in ("alpha", "beta"):
            self.pool.register("research", "investigator", "fake",
                               model="m1", handle=handle)
        self.pool.register("verification", "reviewer", "fake",
                           model="m1", handle="beta")
        self.roster = TaskforceRoster(self.plan, self.pool,
                                      established_threshold=2)
        self.roster.sync_pool(T0)
        self.resolver = TaskforceSeatResolver(self.roster)
        self.alpha = specialist_identity("alpha")
        self.beta = specialist_identity("beta")
        self.plan.add_objective("obj-1", "Objective one.", "you", T0)
        with self.plan.transaction() as cur:
            project, _created = self.plan.open_project(
                cur, "obj-1", "Project one", "the solution",
                "PROJECT_MANAGER", "prop:open", T0)
        self.project_id = project["project_id"]
        self._sequence = 0

    def tearDown(self):
        self.plan.close()
        self.tmp.cleanup()

    def _assign(self, selection, sequence_id=None, ordinal=1, now=T0):
        if selection is None:
            return None
        if sequence_id is None:
            self._sequence += 1
            sequence_id = "sq_%032x" % self._sequence
        row, _created = self.roster.open_assignment(
            sequence_id, ordinal, selection, "obj-1", "prop:test", now,
            project_id=self.project_id)
        return row

    def _select(self, handle, capability="research", role="investigator",
                sequence_id=None, ordinal=1, now=T0, **resolve):
        """The Project Manager explicitly selects an outside specialist."""
        return self._assign(self.resolver.resolve(
            capability, role=role, directive={
                "mode": "outside_specialist",
                "specialist_id": specialist_identity(handle)}, **resolve),
            sequence_id, ordinal, now)

    def _request(self, capability="research", role="investigator",
                 sequence_id=None, ordinal=1, now=T0, **resolve):
        """A capability request: the Taskforce resolves a retained
        member, or nobody."""
        return self._assign(
            self.resolver.resolve(capability, role=role, **resolve),
            sequence_id, ordinal, now)

    def _conclude(self, assignment, outcome="QUALIFYING", now=T1,
                  environment="autobot/READ_ONLY", domains=()):
        """Stand in for the demonstration recorder: record the verified
        conclusion of one assignment exactly as it does."""
        from intelligence.demonstrations import demonstration_identity
        demonstration_id = demonstration_identity(
            assignment["assignment_id"])
        with self.plan.transaction() as cur:
            cur.execute(
                "INSERT INTO capability_demonstrations (demonstration_id,"
                " specialist_id, seat_id, assignment_id, project_id,"
                " standing, capability, role, environment, system_domains,"
                " sequence_id, attempt_id, call_id, task_contract_id,"
                " record_hash, contract_digest, verification_refs,"
                " evidence_refs, criteria, outcome, observed_findings,"
                " recorded_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, '[]', '{}', '{}', ?, '[]', ?)",
                (demonstration_id, assignment["specialist_id"],
                 assignment["seat_id"], assignment["assignment_id"],
                 assignment["project_id"],
                 assignment["standing_at_assignment"],
                 assignment["capability"], assignment["role"], environment,
                 json.dumps(list(domains)), assignment["sequence_id"],
                 "at_" + assignment["assignment_id"][4:], None,
                 "tc_" + "0" * 32, "sha256:" + "1" * 64,
                 "sha256:" + "2" * 64, outcome, now))
            self.roster.set_assignment_status(
                cur, assignment["assignment_id"], "SETTLED", now,
                outcome="VERIFIED_ACCEPTED" if outcome == "QUALIFYING"
                else "VERIFICATION_FAILED")
            if outcome == "QUALIFYING" and assignment["seat_id"]:
                seat = dict(cur.execute(
                    "SELECT * FROM taskforce_seats WHERE seat_id = ?",
                    (assignment["seat_id"],)).fetchone())
                self.roster._maybe_establish(cur, seat, now)
        return demonstration_id

    def _retain(self, assignment, demonstrations, now=T2, **extra):
        return self.roster.retain(
            assignment["specialist_id"], assignment["capability"],
            assignment["role"], demonstrations, "needed again", now,
            **extra)

    def _seat(self, assignment):
        return self.roster.seat_for(assignment["specialist_id"],
                                    assignment["capability"],
                                    assignment["role"])

    def _retained(self, handle="alpha", capability="research",
                  role="investigator"):
        """An outside specialist does verified work and is retained: the
        only way a member comes to exist."""
        row = self._select(handle, capability, role)
        demo = self._conclude(row)
        result = self._retain(row, [demo])
        self.assertEqual(result["status"], "APPLIED")
        return row, demo, result


class PoolAndIdentityTests(RosterTestCase):
    def test_specialist_identity_is_the_handle_alone(self):
        """One worker, one stable identity: it is a function of the
        handle, so nothing about how the worker is executed is in it."""
        alpha = self.pool.profile("alpha")
        self.assertEqual(alpha["specialist_id"], self.alpha)
        row = self.roster.specialist(self.alpha)
        self.assertEqual((row["provider"], row["model"], row["status"]),
                         ("fake", "m1", "ACTIVE"))
        self.assertEqual(row["first_seen_utc"], T0)
        # The identity is NOT a call, attempt or contract identity.
        self.assertTrue(row["specialist_id"].startswith("sp_"))

    def test_one_worker_may_offer_several_capabilities(self):
        beta = self.pool.profile("beta")
        self.assertEqual(sorted(beta["offers"]),
                         [["research", "investigator"],
                          ["verification", "reviewer"]])
        self.assertTrue(self.pool.can_source("verification", "reviewer"))
        self.assertFalse(self.pool.can_source("verification", "builder"))
        self.assertFalse(self.pool.can_source("security_review"))

    def test_a_handle_names_one_worker(self):
        with self.assertRaises(WorkforceError):
            self.pool.register("research", "investigator", "other",
                               handle="alpha")
        with self.assertRaises(WorkforceError):
            self.pool.register("not_a_capability", "builder", "fake")

    def test_pool_mirror_is_idempotent_and_withdraws(self):
        self.assertEqual(self.roster.sync_pool(T1), 0)  # nothing changed
        smaller = SpecialistPool()
        smaller.register("research", "investigator", "fake", model="m1",
                         handle="alpha")
        roster = TaskforceRoster(self.plan, smaller)
        self.assertEqual(roster.sync_pool(T1), 1)
        self.assertEqual(roster.specialist(self.beta)["status"],
                         "WITHDRAWN")
        # A withdrawn worker cannot be selected; its row and history stay.
        resolver = TaskforceSeatResolver(roster)
        self.assertIn("withdrawn", resolver.check_selection(
            self.beta, "verification", "reviewer"))
        self.assertIsNone(resolver.resolve(
            "verification", role="reviewer", directive={
                "mode": "outside_specialist", "specialist_id": self.beta}))


class StaffingPathTests(RosterTestCase):
    def test_the_taskforce_never_goes_outside_for_a_capability_request(self):
        """Nobody is retained: a capability request resolves NOBODY. The
        Taskforce resolves retained members only; it says what a Project
        Manager could select instead."""
        self.assertIsNone(self.resolver.resolve("research",
                                                role="investigator"))
        why = self.resolver.explain_unavailable("research", "investigator")
        self.assertEqual(why["cause"], "no_retained_seat")
        self.assertEqual(why["retained"], [])
        self.assertEqual(
            [(c["handle"], c["eligible"], c["available"])
             for c in why["outside_candidates"]],
            [("alpha", True, True), ("beta", True, True)])
        self.assertEqual(self.roster.assignments(), [])

    def test_the_project_manager_selects_an_outside_specialist(self):
        """The explicit path: the named specialist gets a project
        assignment — and nothing else. No membership, no seat, no
        provisional standing of any kind comes into existence."""
        row = self._select("beta")
        self.assertEqual(row["specialist_id"], self.beta)
        self.assertEqual(row["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(row["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(row["standing_at_assignment"], OUTSIDE)
        self.assertIsNone(row["seat_id"])
        self.assertEqual(row["project_id"], self.project_id)
        self.assertEqual(row["status"], "SELECTED")
        self.assertIsNone(row["attempt_id"])  # nothing launched yet
        reason = json.loads(row["selected_reason"])
        self.assertEqual(reason["path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(reason["directive"]["specialist_id"], self.beta)
        self.assertEqual(
            [c["handle"] for c in reason["outside_candidates"]],
            ["alpha", "beta"])  # what was chosen among, on the record
        # Project participation, and ONLY project participation.
        self.assertEqual(self.roster.memberships(), [])
        self.assertEqual(self.roster.seats(), [])
        self.assertFalse(self.roster.is_member(self.beta))
        self.assertEqual(self.roster.standing(
            self.beta, "research", "investigator"), OUTSIDE)
        team = self.roster.team(self.project_id)
        self.assertEqual(
            [(m["specialist_id"], m["joined_as"], m["status"])
             for m in team],
            [(self.beta, "OUTSIDE_SPECIALIST", "ACTIVE")])
        events = [e["event_kind"] for e in
                  self.plan.unconsumed_workforce_events()]
        self.assertEqual(events, ["outside_specialist_assigned"])

    def test_assignment_is_idempotent_on_the_attempt_it_staffs(self):
        """Re-deriving the staffing for the same attempt returns the row
        already written: one assignment, one team row."""
        first = self._select("alpha", sequence_id="sq_" + "a" * 32)
        selection = self.resolver.resolve(
            "research", role="investigator", directive={
                "mode": "outside_specialist", "specialist_id": self.beta})
        again, created = self.roster.open_assignment(
            "sq_" + "a" * 32, 1, selection, "obj-1", "prop:test", T1,
            project_id=self.project_id)
        self.assertFalse(created)
        self.assertEqual(again["assignment_id"], first["assignment_id"])
        self.assertEqual(again["specialist_id"], self.alpha)
        self.assertEqual(first["assignment_id"],
                         assignment_identity("sq_" + "a" * 32, 1))
        self.assertEqual(len(self.roster.assignments()), 1)
        self.assertEqual(len(self.roster.team(self.project_id)), 1)

    def test_a_busy_specialist_is_never_dispatched_twice(self):
        """One execution at a time: while alpha executes, selecting it
        again WAITS (nothing is invented); another specialist can be
        selected meanwhile."""
        first = self._select("alpha")
        self.assertIsNone(self._select("alpha"))
        why = self.resolver.explain_unavailable(
            "research", "investigator", directive={
                "mode": "outside_specialist", "specialist_id": self.alpha})
        self.assertEqual(why["cause"], "specialist_busy")
        second = self._select("beta")
        self.assertEqual(second["specialist_id"], self.beta)
        candidates = self.resolver.outside_candidates("research",
                                                      "investigator")
        self.assertEqual([c["available"] for c in candidates],
                         [False, False])
        # Once alpha's execution is over (collected), it is free again.
        with self.plan.transaction() as cur:
            self.roster.set_assignment_status(
                cur, first["assignment_id"], "COLLECTED", T1)
        third = self._select("alpha")
        self.assertEqual(third["specialist_id"], self.alpha)

    def test_an_explicit_selection_must_be_possible(self):
        """The Project Manager's selection is bound by what is true: the
        specialist exists and offers exactly this capability and role."""
        check = self.resolver.check_selection
        self.assertIsNone(check(self.beta, "verification", "reviewer"))
        self.assertIn("not known", check("sp_nobody", "research",
                                         "investigator"))
        self.assertIn("does not offer", check(self.alpha, "verification",
                                              "reviewer"))
        self.assertIn("does not offer", check(self.beta, "verification",
                                              "builder"))
        self.assertIsNone(self._select("alpha", "verification", "reviewer"))
        self.assertIsNone(self._select("beta", "documentation", "builder"))
        why = self.resolver.explain_unavailable(
            "verification", "reviewer", directive={
                "mode": "outside_specialist", "specialist_id": self.alpha})
        self.assertEqual(why["cause"], "selection_refused")
        self.assertIn("does not offer", why["refused"])
        with self.assertRaises(WorkforceError):
            self.resolver.resolve("research", directive={"mode": "anyone"})

    def test_a_review_stays_independent_of_its_subject(self):
        """Independence binds both paths: the excluded specialist is not
        resolved by the Taskforce and may not be selected by the Project
        Manager."""
        self._retained("alpha")
        self.assertIsNone(self.resolver.resolve(
            "research", role="investigator", independent_of=[self.alpha]))
        why = self.resolver.explain_unavailable(
            "research", "investigator", independent_of=[self.alpha])
        self.assertEqual(why["cause"], "retained_seats_prohibited")
        self.assertIn("independently", self.resolver.check_selection(
            self.beta, "research", "investigator",
            independent_of=[self.beta]))
        self.assertIsNone(self._select("beta", independent_of=[self.beta]))
        prohibited = [c for c in self.resolver.outside_candidates(
            "research", "investigator", exclude=[self.beta])
            if c["specialist_id"] == self.beta][0]
        self.assertTrue(prohibited["prohibited"])
        self.assertFalse(prohibited["eligible"])


class MembershipSeparationTests(RosterTestCase):
    def test_verified_work_alone_never_creates_membership(self):
        """An outside specialist does verified, QUALIFYING work. A
        capability demonstration is recorded — and the specialist is
        exactly as outside as before. It is a retention CANDIDATE, which
        is not a standing."""
        row = self._select("alpha")
        demo = self._conclude(row)
        recorded = self.roster.demonstration(demo)
        self.assertEqual(recorded["outcome"], "QUALIFYING")
        self.assertEqual(recorded["standing"], OUTSIDE)
        self.assertIsNone(recorded["seat_id"])
        self.assertEqual(self.roster.memberships(), [])
        self.assertEqual(self.roster.seats(), [])
        self.assertEqual(self.roster.standing(
            self.alpha, "research", "investigator"), OUTSIDE)
        view = self.roster.view()
        self.assertEqual(view["members"], [])
        self.assertEqual(view["retained_seats"], [])
        self.assertEqual(
            [(c["specialist_id"], c["capability"], c["role"],
              c["qualifying_demonstrations"])
             for c in view["retention_candidates"]],
            [(self.alpha, "research", "investigator", [demo])])
        self.assertIn(self.alpha, [s["specialist_id"]
                                   for s in view["outside_specialists"]])

    def test_however_often_it_works_it_stays_outside_without_a_decision(
            self):
        """Successful project execution never implies retention: three
        verified assignments later there is still no membership."""
        demos = []
        for _ in range(3):
            row = self._select("alpha")
            self.assertEqual(row["standing_at_assignment"], OUTSIDE)
            self.assertEqual(row["selection_path"], "OUTSIDE_SPECIALIST")
            demos.append(self._conclude(row))
        self.assertEqual(self.roster.memberships(), [])
        self.assertEqual(self.roster.seats(), [])
        # And the Taskforce still resolves nobody for the capability.
        self.assertIsNone(self.resolver.resolve("research",
                                                role="investigator"))
        candidate = self.roster.retention_candidates()[0]
        self.assertEqual(candidate["qualifying_demonstrations"], demos)

    def test_a_member_is_outside_for_work_it_holds_no_seat_for(self):
        """Membership is the specialist's; a seat is one capability and
        role. beta, retained as a reviewer, is still an outside
        specialist for research."""
        self._retained("beta", "verification", "reviewer")
        self.assertTrue(self.roster.is_member(self.beta))
        self.assertEqual(self.roster.standing(
            self.beta, "research", "investigator"), OUTSIDE)
        self.assertIsNone(self.resolver.resolve("research",
                                                role="investigator"))
        row = self._select("beta")
        self.assertEqual(row["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(row["standing_at_assignment"], OUTSIDE)
        self.assertIsNone(row["seat_id"])
        candidate = [c for c in self.resolver.outside_candidates(
            "research", "investigator")
            if c["specialist_id"] == self.beta][0]
        self.assertTrue(candidate["is_member"])


class RetentionTests(RosterTestCase):
    def test_retention_creates_membership_and_the_retained_seat(self):
        """outside specialist -> verified work -> demonstration ->
        RETAIN -> TaskforceMembership -> retained seat."""
        row = self._select("alpha")
        demo = self._conclude(row)
        result = self._retain(row, [demo], proposal_id="prop:r1",
                              objective_id="obj-1")
        self.assertEqual(result["status"], "APPLIED")
        self.assertTrue(result["membership_created"])
        membership = self.roster.membership(self.alpha)
        self.assertEqual(membership["membership_id"],
                         membership_identity(self.alpha))
        self.assertEqual(membership["status"], "ACTIVE")
        self.assertEqual(membership["joined_utc"], T2)
        self.assertEqual(membership["joined_by_decision"],
                         result["decision_id"])
        seat = self._seat(row)
        self.assertEqual(seat["seat_id"], seat_identity(
            self.alpha, "research", "investigator"))
        self.assertEqual(seat["membership_id"],
                         membership["membership_id"])
        self.assertEqual(seat["status"], "RETAINED")
        self.assertEqual(seat["retained_since_utc"], T2)
        self.assertEqual(
            [(h["from_status"], h["to_status"], h["cause"],
              h["decision_id"]) for h in self.roster.history(
                  seat["seat_id"])],
            [(OUTSIDE, "RETAINED", "retention_decision",
              result["decision_id"])])
        decision = self.roster.decisions(seat_id=seat["seat_id"])[0]
        self.assertEqual(decision["kind"], "RETAIN")
        self.assertEqual(json.loads(decision["demonstration_ids"]), [demo])
        self.assertEqual(decision["decided_by"], "planner")
        self.assertEqual(self.roster.retention_candidates(), [])
        # Re-deciding converges; it never retains twice.
        again = self._retain(row, [demo], proposal_id="prop:r1")
        self.assertEqual(again["status"], "CONVERGED")
        self.assertEqual(len(self.roster.decisions(
            seat_id=seat["seat_id"])), 1)
        self.assertEqual(len(self.roster.memberships()), 1)

    def test_retention_is_refused_without_verified_evidence(self):
        row = self._select("alpha")
        refused = self._retain(row, [])
        self.assertEqual(refused["status"], "REJECTED")
        failed = self._conclude(row, outcome="NOT_QUALIFYING")
        refused = self._retain(row, [failed])
        self.assertEqual(refused["status"], "REJECTED")
        self.assertIn("NOT_QUALIFYING", refused["note"])
        self.assertEqual(self.roster.memberships(), [])
        self.assertEqual(self.roster.seats(), [])
        self.assertEqual(self.roster.retention_candidates(), [])

    def test_retention_cannot_borrow_other_work(self):
        mine = self._select("alpha")
        self._conclude(mine)
        theirs = self._select("beta")
        their_demo = self._conclude(theirs)
        refused = self._retain(mine, [their_demo])
        self.assertEqual(refused["status"], "REJECTED")
        self.assertIn("not this specialist", refused["note"])
        missing = self._retain(mine, ["dem:nope"])
        self.assertEqual(missing["status"], "REJECTED")
        # The same specialist's work in ANOTHER capability does not
        # support this seat either.
        review = self._select("beta", "verification", "reviewer")
        review_demo = self._conclude(review)
        refused = self.roster.retain(
            self.beta, "research", "investigator", [review_demo],
            "borrowed from another capability", T2)
        self.assertEqual(refused["status"], "REJECTED")
        self.assertEqual(self.roster.memberships(), [])

    def test_a_retained_member_is_resolved_for_a_later_request(self):
        """The central rule: once alpha is RETAINED for the capability,
        a later capability request is resolved to alpha by the Taskforce
        — and the record names the outside specialist a Project Manager
        would have had to select without the membership."""
        first, _demo, _result = self._retained("alpha")
        second = self._request(environment="autobot/READ_ONLY", now=T2)
        seat = self._seat(first)
        self.assertEqual(second["specialist_id"], self.alpha)
        self.assertEqual(second["seat_id"], seat["seat_id"])
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["decided_by"], "TASKFORCE")
        self.assertEqual(second["standing_at_assignment"], "RETAINED")
        reason = json.loads(second["selected_reason"])
        self.assertEqual(reason["path"], "RETAINED_SEAT")
        self.assertIsNone(reason["directive"])
        self.assertEqual(reason["outside_alternative"]["handle"], "beta")
        self.assertEqual(reason["retained_ranked"][0]["qualifying"], 1)
        self.assertEqual(
            reason["retained_ranked"][0]["environment_experience"], 1)
        events = [e["event_kind"] for e in
                  self.plan.unconsumed_workforce_events()]
        self.assertIn("seat_retained", events)
        self.assertEqual(events[-1], "retained_seat_assigned")
        self.assertEqual(self.roster.seat(seat["seat_id"])[
            "last_assignment_id"], second["assignment_id"])

    def test_a_busy_retained_seat_makes_the_request_wait(self):
        """The Taskforce does not substitute an outside specialist for a
        busy member: the request waits, and going outside is the Project
        Manager's explicit decision."""
        self._retained("alpha")
        busy = self._request(now=T2)
        self.assertEqual(busy["selection_path"], "RETAINED_SEAT")
        self.assertIsNone(self._request(now=T2))
        why = self.resolver.explain_unavailable("research", "investigator")
        self.assertEqual(why["cause"], "retained_seat_busy")
        self.assertFalse(why["retained"][0]["available"])
        other = self._select("beta", now=T2)
        self.assertEqual(other["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(other["decided_by"], "PROJECT_MANAGER")
        # A retained member is not selectable AS an outside specialist.
        self.assertIn("retained Taskforce seat",
                      self.resolver.check_selection(
                          self.alpha, "research", "investigator"))


class ReleaseTests(RosterTestCase):
    def test_releasing_an_outside_specialist_changes_no_membership(self):
        """The specialist leaves the project after its task: a decision
        is recorded, its participation ends, and no membership or seat
        row is created or changed. History stays."""
        row = self._select("alpha")
        demo = self._conclude(row)
        result = self.roster.release(
            self.alpha, "research", "investigator", "task_only",
            "the capability was needed once", T2, proposal_id="prop:x",
            project_id=self.project_id)
        self.assertEqual(result["status"], "APPLIED")
        self.assertFalse(result["membership_changed"])
        self.assertIsNone(result["seat"])
        decision = self.roster.decisions(specialist_id=self.alpha)[0]
        self.assertEqual(decision["kind"], "RELEASE_OUTSIDE")
        self.assertIsNone(decision["seat_id"])
        self.assertEqual(decision["project_id"], self.project_id)
        self.assertEqual(self.roster.memberships(), [])
        self.assertEqual(self.roster.seats(), [])
        member = self.roster.team(self.project_id)[0]
        self.assertEqual(member["status"], "RELEASED")
        self.assertEqual(member["end_reason"],
                         "released_by_project_manager:task_only")
        self.assertIsNotNone(self.roster.demonstration(demo))
        self.assertEqual(len(self.roster.assignments(
            specialist_id=self.alpha)), 1)
        # The decision considered that work: it is no longer pending.
        self.assertEqual(self.roster.retention_candidates(), [])
        # Released for an organizational reason: it may be selected for a
        # project again, and rejoins the team.
        again = self._select("alpha", now=T2)
        self.assertEqual(again["standing_at_assignment"], OUTSIDE)
        self.assertEqual(self.roster.team(self.project_id)[0]["status"],
                         "ACTIVE")
        self.assertEqual(
            self.roster.release(self.alpha, "research", "investigator",
                                "capacity", "x", T2,
                                disposition="DEACTIVATE")["status"],
            "REJECTED")  # an outside specialist holds no seat

    def test_release_for_cause_stops_selection(self):
        row = self._select("alpha")
        self._conclude(row, outcome="NOT_QUALIFYING")
        self.roster.release(self.alpha, "research", "investigator",
                            "poor_performance", "verification failed", T2)
        self.assertIn("released for cause", self.resolver.check_selection(
            self.alpha, "research", "investigator"))
        self.assertIsNone(self._select("alpha", now=T2))
        candidates = {c["handle"]: c for c in
                      self.resolver.outside_candidates("research",
                                                       "investigator")}
        self.assertTrue(candidates["alpha"]["released_for_cause"])
        self.assertFalse(candidates["alpha"]["eligible"])
        self.assertTrue(candidates["beta"]["eligible"])

    def test_release_validation(self):
        row = self._select("alpha")
        bad = self.roster.release(self.alpha, "research", "investigator",
                                  "because", "x", T2)
        self.assertEqual(bad["status"], "REJECTED")
        self.assertIn("because", bad["note"])
        executing = self.roster.release(
            self.alpha, "research", "investigator", "capacity", "x", T2)
        self.assertEqual(executing["status"], "REJECTED")
        self.assertIn("still executing", executing["note"])
        self.assertEqual(row["status"], "SELECTED")
        self.assertEqual(sorted(RELEASE_REASONS),
                         sorted(["task_only", "obsolete_capability",
                                 "poor_performance", "environment_mismatch",
                                 "capacity"]))

    def test_releasing_a_member_ends_the_seat_and_the_membership(self):
        row, demo, _result = self._retained("alpha")
        seat = self._seat(row)
        result = self.roster.release(
            self.alpha, "research", "investigator", "obsolete_capability",
            "the organization no longer does this", T3)
        self.assertEqual(result["status"], "APPLIED")
        self.assertTrue(result["membership_changed"])
        self.assertEqual(self.roster.seat(seat["seat_id"])["status"],
                         "RELEASED")
        membership = self.roster.membership(self.alpha)
        self.assertEqual(membership["status"], "RELEASED")
        self.assertEqual(membership["ended_utc"], T3)
        self.assertFalse(self.roster.is_member(self.alpha))
        self.assertEqual(
            [h["to_status"] for h in self.roster.history(seat["seat_id"])],
            ["RETAINED", "RELEASED"])
        self.assertIsNone(self.resolver.resolve("research",
                                                role="investigator"))
        self.assertIsNotNone(self.roster.demonstration(demo))

    def test_deactivation_is_reversible(self):
        row, demo, _result = self._retained("alpha")
        seat = self._seat(row)
        off = self.roster.release(
            self.alpha, "research", "investigator", "capacity",
            "not needed this quarter", T3, disposition="DEACTIVATE")
        self.assertEqual(off["status"], "APPLIED")
        self.assertEqual(self.roster.seat(seat["seat_id"])["status"],
                         "INACTIVE")
        self.assertEqual(self.roster.membership(self.alpha)["status"],
                         "INACTIVE")
        # An inactive seat is not resolved by the Taskforce.
        self.assertIsNone(self.resolver.resolve("research",
                                                role="investigator"))
        back = self._retain(row, [demo], now=T3)
        self.assertEqual(back["status"], "APPLIED")
        self.assertFalse(back["membership_created"])
        restored = self.roster.seat(seat["seat_id"])
        self.assertEqual(restored["status"], "RETAINED")
        self.assertEqual(restored["retained_since_utc"], T2)  # seniority
        self.assertEqual(self.roster.membership(self.alpha)["status"],
                         "ACTIVE")


class ProjectTeamTests(RosterTestCase):
    def test_a_project_team_is_temporary_and_membership_is_not(self):
        """A project uses a retained member and an outside specialist.
        When it completes, every participation ends — and the Taskforce
        is exactly what it was."""
        self._retained("alpha")
        member = self._request(now=T2)
        outsider = self._select("beta", now=T2)
        self._conclude(member, now=T2)
        self._conclude(outsider, now=T2)
        team = {m["specialist_id"]: m for m in
                self.roster.team(self.project_id)}
        self.assertEqual(team[self.alpha]["joined_as"],
                         "OUTSIDE_SPECIALIST")  # how it FIRST joined
        self.assertEqual(team[self.beta]["joined_as"], "OUTSIDE_SPECIALIST")
        self.assertEqual({m["status"] for m in team.values()}, {"ACTIVE"})
        self.plan.complete_objective(
            "obj-1", T3, project_status="COMPLETED", closed_by="prop:done",
            reason="project_completed")
        project = self.plan.project(self.project_id)
        self.assertEqual((project["status"], project["closed_by"]),
                         ("COMPLETED", "prop:done"))
        self.assertIsNone(self.plan.active_project("obj-1"))
        for row in self.roster.team(self.project_id):
            self.assertEqual((row["status"], row["end_reason"],
                              row["ended_utc"]),
                             ("RELEASED", "project_completed", T3))
        self.assertEqual(self.roster.assignments(
            statuses=("SELECTED", "DISPATCHED", "EXECUTING", "COLLECTED"),
            project_id=self.project_id), [])
        self.assertEqual(len(self.roster.assignments(
            project_id=self.project_id)), 3)  # history stays
        # The persistent side is untouched.
        self.assertTrue(self.roster.is_member(self.alpha))
        self.assertEqual(self._seat(member)["status"], "ESTABLISHED")
        self.assertFalse(self.roster.is_member(self.beta))
        self.assertEqual(self.roster.membership(self.alpha)["status"],
                         "ACTIVE")

    def test_a_retained_member_joins_a_new_project_as_a_member(self):
        self._retained("alpha")
        self.plan.complete_objective("obj-1", T2)
        self.plan.add_objective("obj-2", "Objective two.", "you", T2)
        with self.plan.transaction() as cur:
            project, created = self.plan.open_project(
                cur, "obj-2", "Project two", "another solution",
                "PROJECT_MANAGER", "prop:open2", T2)
        self.assertTrue(created)
        selection = self.resolver.resolve("research", role="investigator")
        row, _created = self.roster.open_assignment(
            "sq_" + "c" * 32, 1, selection, "obj-2", "prop:w", T2,
            project_id=project["project_id"])
        self.assertEqual(row["selection_path"], "RETAINED_SEAT")
        self.assertEqual(
            [(m["specialist_id"], m["joined_as"])
             for m in self.roster.team(project["project_id"])],
            [(self.alpha, "RETAINED_MEMBER")])

    def test_one_project_realizes_an_objective_at_a_time(self):
        with self.plan.transaction() as cur:
            same, created = self.plan.open_project(
                cur, "obj-1", "Another title", "x", "PROJECT_MANAGER",
                "prop:other", T1)
        self.assertFalse(created)
        self.assertEqual(same["project_id"], self.project_id)
        self.assertEqual(same["title"], "Project one")
        with self.plan.transaction() as cur:
            first = self.plan.add_workstream(
                cur, self.project_id, "audit", "prop:w1", T1)
            again = self.plan.add_workstream(
                cur, self.project_id, "audit", "prop:w1", T1)
            clash = self.plan.add_workstream(
                cur, self.project_id, "audit", "prop:w2", T1)
            unnamed = self.plan.add_workstream(
                cur, self.project_id, None, "prop:w3", T1)
        self.assertEqual(first["workstream_id"], again["workstream_id"])
        self.assertNotEqual(clash["workstream_key"], "audit")
        self.assertTrue(unnamed["workstream_key"])
        self.assertEqual(len(self.plan.workstreams(self.project_id)), 3)

    def test_the_latest_staffing_decision_wins(self):
        sequence_id = "sq_" + "d" * 32
        with self.plan.transaction() as cur:
            self.plan.record_assignment_decision(
                cur, "prop:d1", "obj-1", self.project_id, sequence_id, 1,
                "outside_specialist", self.alpha, T0)
            self.plan.record_assignment_decision(
                cur, "prop:d2", "obj-1", self.project_id, sequence_id, 1,
                "outside_specialist", self.beta, T1)
        open_decision = self.plan.open_staffing_decision(sequence_id)
        self.assertEqual(open_decision["decision_id"], "prop:d2")
        self.assertEqual(
            [(d["decision_id"], d["status"]) for d in
             self.plan.assignment_decisions(sequence_id=sequence_id)],
            [("prop:d1", "SUPERSEDED"), ("prop:d2", "OPEN")])
        selection = self.resolver.resolve(
            "research", role="investigator", directive={
                "mode": "outside_specialist", "specialist_id": self.beta})
        row, _created = self.roster.open_assignment(
            sequence_id, 1, selection, "obj-1", "prop:w", T2,
            project_id=self.project_id, assignment_decision="prop:d2")
        self.assertEqual(row["assignment_decision"], "prop:d2")
        applied = self.plan.assignment_decisions(sequence_id=sequence_id)[1]
        self.assertEqual((applied["status"], applied["assignment_id"]),
                         ("APPLIED", row["assignment_id"]))
        self.assertIsNone(self.plan.open_staffing_decision(sequence_id))


class SeatTransitionTests(RosterTestCase):
    def test_only_registered_transitions_exist(self):
        """A seat begins with a retention decision. No transition is
        caused by an assignment or by a demonstration."""
        self.assertEqual(
            sorted(set(SEAT_TRANSITIONS.values())),
            ["deactivation_decision", "established_threshold",
             "release_decision", "retention_decision"])
        self.assertEqual(
            sorted(to for (start, to) in SEAT_TRANSITIONS
                   if start == OUTSIDE), ["RETAINED"])
        self.assertNotIn((OUTSIDE, "ESTABLISHED"), SEAT_TRANSITIONS)
        row, _demo, _result = self._retained("alpha")
        seat = self._seat(row)
        with self.plan.transaction() as cur:
            with self.assertRaises(WorkforceError):
                self.roster._transition(cur, seat, "RETAINED", T3)
        released = dict(seat, status="RELEASED")
        with self.plan.transaction() as cur:
            for target in ("ESTABLISHED", "INACTIVE"):
                with self.assertRaises(WorkforceError):
                    self.roster._transition(cur, released, target, T3)
        self.assertEqual(self._seat(row)["status"], "RETAINED")

    def test_established_needs_repeated_independent_verified_work(self):
        """threshold 2: the work that earned the seat counts once; the
        second INDEPENDENT qualifying demonstration establishes it —
        deterministically, with the supporting demonstrations on the
        decision."""
        first, demo1, _result = self._retained("alpha")
        seat = self._seat(first)
        self.assertEqual(seat["status"], "RETAINED")
        second = self._request(now=T2)
        self.assertEqual(second["seat_id"], seat["seat_id"])
        demo2 = self._conclude(second, now=T2)
        self.assertEqual(self._seat(first)["status"], "ESTABLISHED")
        decision = [d for d in self.roster.decisions(
            seat_id=seat["seat_id"]) if d["kind"] == "ESTABLISH"][0]
        self.assertEqual(decision["decided_by"], "taskforce")
        self.assertEqual(sorted(json.loads(decision["demonstration_ids"])),
                         sorted([demo1, demo2]))
        self.assertEqual(self.roster.membership(self.alpha)["status"],
                         "ACTIVE")
        third = self._request(now=T2)
        self.assertEqual(third["standing_at_assignment"], "ESTABLISHED")
        reason = json.loads(third["selected_reason"])
        self.assertEqual(reason["retained_ranked"][0]["status"],
                         "ESTABLISHED")

    def test_established_threshold_must_mean_repeated(self):
        with self.assertRaises(WorkforceError):
            TaskforceRoster(self.plan, self.pool, established_threshold=1)

    def test_ranking_prefers_environment_and_system_experience(self):
        """Two retained seats for the same capability: the one with
        verified experience in the requested environment and subsystem
        ranks first; ties break deterministically."""
        rows = [self._select("alpha"), self._select("beta")]
        demos = [
            self._conclude(rows[0], environment="autobot/READ_ONLY",
                           domains=["intelligence.workforce"]),
            self._conclude(rows[1], environment="autobot/SOURCE_MODIFYING",
                           domains=["docs"])]
        for row, demo in zip(rows, demos):
            self.assertEqual(self._retain(row, [demo])["status"], "APPLIED")
        sm = self.resolver.resolve(
            "research", role="investigator",
            environment="autobot/SOURCE_MODIFYING", subsystem="docs")
        self.assertEqual(sm["specialist"]["specialist_id"], self.beta)
        ro = self.resolver.resolve(
            "research", role="investigator",
            environment="autobot/READ_ONLY",
            subsystem="intelligence.workforce")
        self.assertEqual(ro["specialist"]["specialist_id"], self.alpha)
        ranked = ro["reason"]["retained_ranked"]
        self.assertEqual([r["system_experience"] for r in ranked], [1, 0])


class ProviderReplacementTests(RosterTestCase):
    def test_rerouting_a_worker_changes_no_identity(self):
        """The activity is stable, the execution implementation is
        replaceable: reroute a RETAINED member to another provider and
        model — same specialist, same membership, same seat; only the
        next assignment's route differs."""
        first, _demo, _result = self._retained("alpha")
        seat = self._seat(first)
        before = self.roster.assignment_view(first)
        self.assertEqual((before["provider"], before["model"]),
                         ("fake", "m1"))
        self.pool.reroute("alpha", provider="other-provider", model="m9")
        self.assertEqual(self.roster.sync_pool(T2), 1)
        specialist = self.roster.specialist(self.alpha)
        self.assertEqual((specialist["provider"], specialist["model"]),
                         ("other-provider", "m9"))
        self.assertEqual(specialist["first_seen_utc"], T0)
        self.assertEqual(self._seat(first)["status"], "RETAINED")
        self.assertEqual(self.roster.membership(self.alpha)[
            "membership_id"], seat["membership_id"])
        second = self._request(now=T2)
        self.assertEqual(second["seat_id"], seat["seat_id"])
        self.assertEqual(second["specialist_id"], self.alpha)
        self.assertEqual((second["provider"], second["model"]),
                         ("other-provider", "m9"))
        view = self.roster.assignment_view(second)
        # The registered cap-14 principal stays the wire identity of the
        # staffing function; it is not an actor.
        self.assertEqual(view["selected_by"], "agent_factory")
        self.assertEqual(view["staffed_by"], "taskforce")
        # The settled first assignment still says what it ran on.
        self.assertEqual(
            self.roster.assignment(first["assignment_id"])["provider"],
            "fake")


class StaffingDirectiveTests(RosterTestCase):
    def test_same_member_different_member_outside(self):
        self._retained("alpha")
        self._retained("beta")
        first = self._request(sequence_id="sq_" + "b" * 32, now=T2)
        with self.plan.transaction() as cur:
            self.roster.set_assignment_status(
                cur, first["assignment_id"], "SETTLED", T2,
                outcome="VERIFICATION_FAILED")
        previous = first["specialist_id"]
        same = self.resolver.resolve(
            "research", role="investigator",
            directive={"mode": "same_member",
                       "previous_specialist_id": previous})
        self.assertEqual(same["specialist"]["specialist_id"], previous)
        self.assertEqual(same["reason"]["directive"]["mode"], "same_member")
        self.assertEqual(same["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(same["reason"]["path"], "RETAINED_SEAT")
        different = self.resolver.resolve(
            "research", role="investigator",
            directive={"mode": "different_member",
                       "previous_specialist_id": previous})
        self.assertNotEqual(different["specialist"]["specialist_id"],
                            previous)
        self.assertEqual(different["decided_by"], "TASKFORCE")
        # Naming nobody selects nobody: only an explicit specialist does.
        self.assertIsNone(self.resolver.resolve(
            "research", role="investigator",
            directive={"mode": "outside_specialist"}))
        # A directive nobody can satisfy waits; it is never widened.
        self.assertIsNone(self.resolver.resolve(
            "verification", role="reviewer",
            directive={"mode": "different_member",
                       "previous_specialist_id": self.beta}))

    def test_continuity_keeps_an_outside_specialist_outside(self):
        """same_member with an outside specialist: the same person does
        the next attempt, and is still outside."""
        first = self._select("alpha", sequence_id="sq_" + "e" * 32)
        with self.plan.transaction() as cur:
            self.roster.set_assignment_status(
                cur, first["assignment_id"], "SETTLED", T1,
                outcome="VERIFICATION_FAILED")
        again = self._assign(self.resolver.resolve(
            "research", role="investigator",
            directive={"mode": "same_member",
                       "previous_specialist_id": self.alpha}),
            "sq_" + "e" * 32, 2, T1)
        self.assertEqual(again["specialist_id"], self.alpha)
        self.assertEqual(again["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(self.roster.memberships(), [])


class ProposalBoundaryTests(unittest.TestCase):
    """What a typed proposal may and may not name."""

    def _base(self, cls, **extra):
        proposal = {"class": cls, "objective_id": "obj-1",
                    "rationale": "why", "snapshot": {"entries": 0},
                    "provenance": {"findings": [], "events": []}}
        proposal.update(extra)
        return proposal

    def _requirement(self, **extra):
        requirement = {
            "objective": "Do the work.", "capability": "research",
            "roles": {"investigator": {"required": True,
                                       "success_outcomes": ["success"]}},
            "guidance": "Do it."}
        requirement.update(extra)
        return requirement

    def test_a_work_requirement_names_no_provider_model_or_specialist(self):
        """The requirement is what gets frozen into the
        TaskContractRecord: it names a capability and a role, and a
        proposal that puts a provider, a model, a specialist or any
        staffing into it — or beside it — is not well-formed."""
        from intelligence import validate_proposal
        from intelligence.proposals import ProposalError
        validate_proposal(self._base("INVESTIGATE", workstream="audit",
                                     requirement=self._requirement()))
        for field in ("provider", "model", "specialist_id", "specialist",
                      "seat_id", "handle"):
            with self.assertRaises(ProposalError):
                validate_proposal(self._base(
                    "INVESTIGATE",
                    requirement=self._requirement(**{field: "x"})))
            with self.assertRaises(ProposalError):
                validate_proposal(self._base(
                    "CREATE_TASK", requirement=self._requirement(
                        finalization_class="READ_ONLY"), **{field: "x"}))
        for cls in ("INVESTIGATE", "CREATE_TASK"):
            with self.assertRaises(ProposalError):
                validate_proposal(self._base(
                    cls, requirement=self._requirement(
                        finalization_class="READ_ONLY"),
                    staffing={"mode": "outside_specialist",
                              "specialist_id": "sp_x"}))
        with self.assertRaises(ProposalError):
            validate_proposal(self._base(
                "INVESTIGATE", requirement=self._requirement(
                    staffing={"mode": "taskforce"})))
        with self.assertRaises(ProposalError):
            validate_proposal(self._base(
                "DECOMPOSE_TASK", parts=[{
                    "part_id": "a", "requirement": self._requirement(
                        finalization_class="READ_ONLY", model="opus")}]))
        with self.assertRaises(ProposalError):
            validate_proposal(self._base(
                "DECOMPOSE_TASK", parts=[{
                    "part_id": "a", "specialist_id": "sp_x",
                    "requirement": self._requirement(
                        finalization_class="READ_ONLY")}]))

    def test_a_work_graph_may_include_its_own_independent_review(self):
        """A decomposition lays out the work and a review of it at once.
        The review names an EARLIER part and is READ_ONLY by
        construction."""
        from intelligence import validate_proposal
        from intelligence.proposals import ProposalError
        work = {"part_id": "work", "requirement": self._requirement(
            finalization_class="SOURCE_MODIFYING")}
        review = {"part_id": "review", "review_of_part": "work",
                  "requirement": self._requirement(
                      capability="verification")}
        validate_proposal(self._base("DECOMPOSE_TASK",
                                     parts=[work, review]))
        for broken in (
                [review, work],  # reviews a later part
                [work, dict(review, review_of_part="nothing")],
                [work, dict(review, review_of_part="review")],  # itself
                [work, dict(review, requirement=self._requirement(
                    finalization_class="SOURCE_MODIFYING"))],
                [work, dict(review, specialist_id="sp_x")]):
            with self.assertRaises(ProposalError):
                validate_proposal(self._base("DECOMPOSE_TASK",
                                             parts=broken))

    def test_a_project_assignment_names_a_specialist_never_a_route(self):
        """Staffing is not contract identity: ASSIGN_SPECIALIST may name
        the specialist the Project Manager selects. It still never names
        a provider or a model — routing is resolved downstream."""
        from intelligence import proposal_identity, validate_proposal
        from intelligence.proposals import ProposalError
        assign = self._base(
            "ASSIGN_SPECIALIST", sequence_id="sq_x",
            staffing={"mode": "outside_specialist",
                      "specialist_id": "sp_x"})
        validate_proposal(assign)
        validate_proposal(self._base(
            "ASSIGN_SPECIALIST", workstream="audit",
            staffing={"mode": "taskforce"}))
        for staffing in (
                {"mode": "the-best-one"},
                {"mode": "outside_specialist"},  # names nobody
                {"mode": "taskforce", "specialist_id": "sp_x"},
                {"mode": "same_member", "specialist_id": "sp_x"},
                {"mode": "outside_specialist", "specialist_id": "sp_x",
                 "model": "opus"},
                {"mode": "outside_specialist", "specialist_id": "sp_x",
                 "provider": "claude-code"}):
            with self.assertRaises(ProposalError):
                validate_proposal(dict(assign, staffing=staffing))
        for field in ("provider", "model"):
            with self.assertRaises(ProposalError):
                validate_proposal(dict(assign, **{field: "x"}))
        with self.assertRaises(ProposalError):  # staffs no workstream
            validate_proposal(self._base(
                "ASSIGN_SPECIALIST", staffing={"mode": "taskforce"}))
        with self.assertRaises(ProposalError):  # decides nothing
            validate_proposal(self._base("ASSIGN_SPECIALIST",
                                         sequence_id="sq_x"))
        # A repair may carry the same staffing word.
        for staffing in ({"mode": "different_member"},
                         {"mode": "same_member"},
                         {"mode": "outside_specialist",
                          "specialist_id": "sp_y"}):
            validate_proposal(self._base(
                "REPAIR_TASK", sequence_id="sq_x", capability="research",
                guidance="repair", staffing=staffing))
        # A different selection is a different decision.
        self.assertNotEqual(
            proposal_identity(assign),
            proposal_identity(dict(assign, staffing={
                "mode": "outside_specialist", "specialist_id": "sp_y"})))
        self.assertEqual(proposal_identity(assign),
                         proposal_identity(dict(assign, rationale="x")))

    def test_workforce_decisions_are_well_formed_or_refused(self):
        from intelligence import proposal_identity, validate_proposal
        from intelligence.proposals import ProposalError
        retain = self._base("RETAIN_SPECIALIST", specialist_id="sp_a",
                            capability="research", role="investigator",
                            demonstrations=["dem:1"])
        validate_proposal(retain)
        for broken in (dict(retain, demonstrations=[]),
                       dict(retain, role=""),
                       dict(retain, capability="not_a_capability"),
                       dict(retain, model="opus"),
                       {k: v for k, v in retain.items()
                        if k != "specialist_id"}):
            with self.assertRaises(ProposalError):
                validate_proposal(broken)
        release = self._base("RELEASE_SPECIALIST", specialist_id="sp_a",
                             capability="research", role="investigator",
                             reason="task_only")
        validate_proposal(release)
        with self.assertRaises(ProposalError):
            validate_proposal(dict(release, reason="no longer liked"))
        with self.assertRaises(ProposalError):
            validate_proposal(dict(release, disposition="DELETE"))
        # The decision IS its identity: re-derived, it converges.
        self.assertEqual(proposal_identity(retain),
                         proposal_identity(dict(retain, rationale="x")))
        self.assertNotEqual(
            proposal_identity(retain),
            proposal_identity(dict(retain, demonstrations=["dem:2"])))
        self.assertNotEqual(
            proposal_identity(release),
            proposal_identity(dict(release, disposition="DEACTIVATE")))

    def test_a_project_states_the_solution_it_realizes(self):
        from intelligence import proposal_identity, validate_proposal
        from intelligence.proposals import ProposalError
        opened = self._base("OPEN_PROJECT", project={
            "title": "Audit", "solution": "Two independent lines."})
        validate_proposal(opened)
        for project in ({"title": "Audit"}, {"solution": "x"},
                        {"title": "Audit", "solution": ""},
                        {"title": "Audit", "solution": "x",
                         "provider": "claude-code"}, None):
            with self.assertRaises(ProposalError):
                validate_proposal(self._base("OPEN_PROJECT",
                                             project=project))
        self.assertNotEqual(
            proposal_identity(opened),
            proposal_identity(dict(opened, project={
                "title": "Audit", "solution": "Another approach."})))

    def test_each_class_belongs_to_one_of_the_engineers_two_roles(self):
        """Technical work is the Engineer's; the project, its team and
        the workforce decisions are the Project Manager's."""
        self.assertEqual(sorted(ACTING_AS), sorted(PROPOSAL_CLASSES))
        for cls in ("CREATE_TASK", "DECOMPOSE_TASK", "INVESTIGATE",
                    "REQUEST_REVIEW", "REPAIR_TASK", "WAIT_FOR_AUTHORITY"):
            self.assertEqual(ACTING_AS[cls], "ENGINEER")
        for cls in ("OPEN_PROJECT", "ASSIGN_SPECIALIST",
                    "RETAIN_SPECIALIST", "RELEASE_SPECIALIST",
                    "PROJECT_COMPLETE"):
            self.assertEqual(ACTING_AS[cls], "PROJECT_MANAGER")


class SupervisionPolicyTests(unittest.TestCase):
    """The deterministic answers to a specialist's permission requests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workspace = root / "worktree"
        self.gate = root / "gate"
        self.repo = root / "repo"
        for path in (self.workspace / "docs", self.gate / "pending",
                     self.repo / "src"):
            path.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _context(self, fin="READ_ONLY", evidence=("EXECUTION_LIFECYCLE",),
                 scope=("docs/guide.md",)):
        record = {"finalization_class": fin,
                  "evidence_requirements": list(evidence),
                  "allowed_scope": {"paths": list(scope)}}
        return supervision.assignment_context(
            record, self.workspace, gate_root=self.gate,
            repo_path=self.repo)

    def _request(self, tool, **tool_input):
        return {"kind": "approval_required", "request_id": "approval_x",
                "native_request": {"subtype": "can_use_tool",
                                   "tool_name": tool, "input": tool_input}}

    def _decide(self, context, tool, **tool_input):
        return supervision.decide(self._request(tool, **tool_input),
                                  context)["decision"]

    def test_read_only_assignment_gets_reading_tools_only(self):
        ctx = self._context()
        self.assertFalse(ctx["shell_allowed"])
        self.assertEqual(self._decide(
            ctx, "Read", file_path=str(self.repo / "src" / "a.py")),
            "allow")
        self.assertEqual(self._decide(ctx, "Grep", pattern="x"), "allow")
        self.assertEqual(self._decide(ctx, "Bash", command="git log"),
                         "deny")
        self.assertEqual(self._decide(
            ctx, "Write", file_path=str(self.repo / "src" / "a.py"),
            content="x"), "deny")
        # No shell means no gate handshake to write either.
        self.assertEqual(self._decide(
            ctx, "Write",
            file_path=str(self.gate / "pending" / "a.gate.json"),
            content="{}"), "deny")
        self.assertEqual(self._decide(
            ctx, "Read", file_path=str(Path(self.tmp.name).parent
                                       / "elsewhere.txt")), "deny")

    def test_governed_read_only_gets_a_governed_shell(self):
        ctx = self._context(evidence=("COMMAND_GOVERNANCE",
                                      "EXECUTION_LIFECYCLE"))
        self.assertTrue(ctx["shell_allowed"])
        self.assertEqual(self._decide(ctx, "PowerShell", command="git log"),
                         "allow")
        self.assertEqual(self._decide(
            ctx, "Write",
            file_path=str(self.gate / "pending" / "git-log.gate.json"),
            content="{}"), "allow")
        self.assertEqual(self._decide(
            ctx, "Edit",
            file_path=str(self.repo / "src" / "a.py"), old_string="a",
            new_string="b"), "deny")

    def test_source_modifying_writes_stay_inside_the_frozen_scope(self):
        ctx = self._context(fin="SOURCE_MODIFYING",
                            evidence=("COMMAND_GOVERNANCE",
                                      "EXECUTION_LIFECYCLE"))
        inside = self.workspace / "docs" / "guide.md"
        self.assertEqual(self._decide(ctx, "Write", file_path=str(inside),
                                      content="x"), "allow")
        self.assertEqual(self._decide(
            ctx, "Edit", file_path=str(self.workspace / "docs" / "b.md"),
            old_string="a", new_string="b"), "deny")
        # The same file in the MAIN checkout is not the isolated worktree.
        self.assertEqual(self._decide(
            ctx, "Write", file_path=str(self.repo / "docs" / "guide.md"),
            content="x"), "deny")
        self.assertEqual(self._decide(
            ctx, "Write",
            file_path=str(self.gate / "pending" / "x.gate.json"),
            content="{}"), "allow")
        self.assertEqual(self._decide(
            ctx, "Write",
            file_path=str(self.gate / "pending" / "sub" / "x.gate.json"),
            content="{}"), "deny")
        self.assertEqual(self._decide(
            ctx, "Write", file_path=str(self.gate / "hooks" / "h.ps1"),
            content="x"), "deny")
        self.assertEqual(self._decide(ctx, "Bash", command="git add ."),
                         "allow")

    def test_unknown_requests_fail_closed(self):
        ctx = self._context(fin="SOURCE_MODIFYING")
        self.assertEqual(self._decide(ctx, "WebFetch", url="http://x"),
                         "deny")
        self.assertEqual(self._decide(ctx, "Agent", prompt="x"), "deny")
        odd = supervision.decide(
            {"kind": "approval_required",
             "native_request": {"subtype": "something_else"}}, ctx)
        self.assertEqual(odd["decision"], "deny")
        question = supervision.decide(
            {"kind": "interaction_required", "native_request": {}}, ctx)
        self.assertIsNone(question["decision"])  # never invents an answer


class FindingsAndPromptTests(unittest.TestCase):
    def test_findings_block_is_parsed_strictly(self):
        text = ("Summary of the work.\n" + prompts.FINDINGS_BEGIN + "\n"
                + json.dumps([{"finding_class": "documentation_mismatch",
                               "affected_component": "docs/x.md",
                               "statement": "The table omits RELEASED.",
                               "significance": "medium",
                               "confidence": "high",
                               "relation_to_requirement": "within",
                               "suggested_follow_up": "",
                               "evidence_refs": ["docs/x.md:12"]}])
                + "\n" + prompts.FINDINGS_END + "\n")
        found, problem = extract_findings(text)
        self.assertIsNone(problem)
        self.assertEqual(len(found), 1)
        self.assertNotIn("suggested_follow_up", found[0])  # "" = absent
        self.assertEqual(extract_findings("no block here"), ([], None))
        self.assertEqual(extract_findings(None), ([], None))
        broken, problem = extract_findings(
            prompts.FINDINGS_BEGIN + "\nnot json\n" + prompts.FINDINGS_END)
        self.assertEqual(broken, [])
        self.assertIn("not JSON", problem)
        _none, problem = extract_findings(prompts.FINDINGS_BEGIN + " [")
        self.assertIn("no end marker", problem)
        _none, problem = extract_findings(
            prompts.FINDINGS_BEGIN + ' {"a": 1} ' + prompts.FINDINGS_END)
        self.assertIn("not a JSON array", problem)
        fenced, problem = extract_findings(
            prompts.FINDINGS_BEGIN + "\n```json\n[]\n```\n"
            + prompts.FINDINGS_END)
        self.assertEqual((fenced, problem), ([], None))

    def test_statement_is_admitted_and_part_of_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = PlanStore(Path(tmp) / "intelligence.db")
            base = {"attempt_id": "at_" + "1" * 32,
                    "sequence_id": "sq_" + "1" * 32, "role": "investigator",
                    "finding_class": "verification_gap",
                    "affected_component": "pilot", "significance": "low",
                    "confidence": "high",
                    "relation_to_requirement": "adjacent"}
            plain = admit_finding(plan, dict(base), T0)
            stated = admit_finding(
                plan, dict(base, statement="The gap is real."), T0)
            self.assertNotEqual(plain, stated)
            row = plan.conn.execute(
                "SELECT statement FROM findings WHERE finding_id = ?",
                (stated,)).fetchone()
            self.assertEqual(row["statement"], "The gap is real.")
            self.assertEqual(admit_finding(plan, dict(base), T1), plain)
            plan.close()

    def test_prompt_composition_is_deterministic_and_class_shaped(self):
        ro = prompts.compose_specialist_prompt(
            "Inspect the roster.", "READ_ONLY", objective="Audit it.",
            task="Inspect the roster transitions.",
            predecessor_findings=[{"finding_id": "fnd:1"}])
        self.assertEqual(ro, prompts.compose_specialist_prompt(
            "Inspect the roster.", "READ_ONLY", objective="Audit it.",
            task="Inspect the roster transitions.",
            predecessor_findings=[{"finding_id": "fnd:1"}]))
        self.assertTrue(ro.startswith("Inspect the roster."))
        self.assertIn("READ_ONLY EXECUTION CONTRACT", ro)
        self.assertIn(prompts.FINDINGS_BEGIN, ro)
        self.assertIn("fnd:1", ro)
        self.assertNotIn("SOURCE_MODIFYING EXECUTION CONTRACT", ro)
        sm = prompts.compose_specialist_prompt(
            "Write the glossary.", "SOURCE_MODIFYING",
            allowed_paths=["docs/b.md", "docs/a.md"])
        self.assertIn("['docs/a.md', 'docs/b.md']", sm)
        self.assertTrue(sm.rstrip().endswith("--- END CONTRACT ---"))
        self.assertLess(sm.index(prompts.FINDINGS_BEGIN),
                        sm.index("SOURCE_MODIFYING EXECUTION CONTRACT"))
        governed = prompts.compose_specialist_prompt(
            "Inspect.", "READ_ONLY", governed=True)
        self.assertIn("COMMAND GOVERNANCE", governed)
        self.assertNotIn("READ_ONLY EXECUTION CONTRACT", governed)
        with self.assertRaises(ValueError):
            prompts.compose_specialist_prompt("  ", "READ_ONLY")


class PlanStoreMigrationTests(unittest.TestCase):
    def test_v1_store_migrates_forward_with_every_row_preserved(self):
        """An intelligence.db written by the previous schema opens under
        this one: its objectives, cycles, proposals, findings and
        execution slots are untouched, the workforce tables exist, and
        the version is recorded. Authority is not involved at all."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intelligence.db"
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value"
                         " TEXT NOT NULL)")
            for statement in _DDL_V1:
                conn.execute(statement)
            conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
            conn.execute("INSERT INTO meta VALUES ('authority_watermark',"
                         " '41')")
            conn.execute(
                "INSERT INTO objectives (objective_id, text, authored_by,"
                " status, created_utc) VALUES ('obj-old', 'Old objective',"
                " 'you', 'COMPLETE', ?)", (T0,))
            conn.execute(
                "INSERT INTO cycles (started_utc, snapshot_entries,"
                " status) VALUES (?, 3, 'CLOSED')", (T0,))
            conn.execute(
                "INSERT INTO proposals (proposal_id, cycle_id, class,"
                " objective_id, status, rationale, body, snapshot_entries,"
                " created_utc) VALUES ('prop:old', 1, 'INVESTIGATE',"
                " 'obj-old', 'ADMITTED', 'why', '{}', 3, ?)", (T0,))
            conn.execute(
                "INSERT INTO findings (finding_id, attempt_id, sequence_id,"
                " role, finding_class, affected_component, evidence_refs,"
                " significance, confidence, relation_to_requirement,"
                " admitted_utc) VALUES ('fnd:old', 'at_x', 'sq_x',"
                " 'builder', 'verification_gap', 'c', '[]', 'low', 'high',"
                " 'within', ?)", (T0,))
            conn.execute(
                "INSERT INTO seats (seat_id, sequence_id, role, capability,"
                " provider, selected_by, status, created_utc, updated_utc)"
                " VALUES ('st_old', 'sq_x', 'builder', 'research',"
                " 'claude-code', 'agent_factory', 'COLLECTED', ?, ?)",
                (T0, T0))
            conn.commit()
            conn.close()

            plan = PlanStore(path)
            try:
                version = plan.conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()["value"]
                self.assertEqual(int(version), PLAN_SCHEMA_VERSION)
                self.assertEqual(plan.watermark(), 41)
                self.assertEqual(plan.objective("obj-old")["text"],
                                 "Old objective")
                self.assertIsNone(plan.objective("obj-old")["constraints"])
                self.assertEqual(plan.proposal("prop:old")["status"],
                                 "ADMITTED")
                finding = plan.conn.execute(
                    "SELECT * FROM findings").fetchone()
                self.assertEqual(finding["finding_id"], "fnd:old")
                self.assertIsNone(finding["statement"])
                slot = plan.conn.execute("SELECT * FROM seats").fetchone()
                # The frozen cap-14 principal on a historical slot stays.
                self.assertEqual(slot["selected_by"], "agent_factory")
                self.assertIsNone(slot["assignment_id"])
                tables = {r["name"] for r in plan.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")}
                for name in ("specialists", "taskforce_memberships",
                             "taskforce_seats", "seat_history",
                             "projects", "project_workstreams",
                             "project_team", "assignment_decisions",
                             "project_assignments",
                             "capability_demonstrations",
                             "retention_decisions", "workforce_events",
                             "assignment_approvals", "objective_budgets",
                             "cycle_reasoning"):
                    self.assertIn(name, tables)
                # The migration created an organization with nobody in
                # it: no member, no seat, no project.
                for name in ("taskforce_memberships", "taskforce_seats",
                             "projects", "project_assignments"):
                    self.assertEqual(plan.conn.execute(
                        "SELECT COUNT(*) AS n FROM %s" % name
                    ).fetchone()["n"], 0)
                self.assertEqual(plan.budget_limits("obj-old"),
                                 DEFAULT_BUDGET)
            finally:
                plan.close()
            # Re-opening is a no-op, and a NEWER store is refused.
            PlanStore(path).close()
            conn = sqlite3.connect(str(path))
            conn.execute("UPDATE meta SET value = '99' WHERE key ="
                         " 'schema_version'")
            conn.commit()
            conn.close()
            with self.assertRaises(PlanStoreTooNew):
                PlanStore(path)

    def test_budget_usage_is_derived_and_limits_are_yours(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = PlanStore(Path(tmp) / "intelligence.db")
            limits = plan.set_budget("obj-1", T0, max_contracts=2)
            self.assertEqual(limits["max_contracts"], 2)
            self.assertEqual(limits["max_attempts"],
                             DEFAULT_BUDGET["max_attempts"])
            self.assertEqual(plan.budget_usage("obj-1")["contracts"], 0)
            self.assertEqual(plan.budget_headroom("obj-1", "contracts"), 2)
            with self.assertRaises(ValueError):
                plan.set_budget("obj-1", T0, max_everything=1)
            with self.assertRaises(ValueError):
                plan.set_budget("obj-1", T0, max_contracts=-1)
            plan.close()


if __name__ == "__main__":
    unittest.main()
