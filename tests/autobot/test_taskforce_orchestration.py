"""Project orchestration over the real authority runtime.

Every test here drives the full plane — Engineer service, Registry
admission, project assignment, the Sequencer's attempt and sequence
machines, the Collector, the verifier seam, acceptance and release —
with rule policies and a scripted provider. Nothing is asserted about
the organization that the ledger does not also show.

Arms: the outside specialist -> verified work -> demonstration ->
explicit retention -> member reused through the Taskforce loop;
successful work that is released instead; the retained seat's effect on
later staffing (and its absence without a decision); multi-workstream
concurrency and dependency release with findings feeding follow-up;
repair staffing; restart convergence across every stage; authority
separation; the waiting production loop and assignment supervision; the
objective budget; exactly-once reasoning; frozen prompts; and the
engineering query layer.
"""

import json
import subprocess
import unittest
from datetime import timedelta

import support
from autobot import project_state
from autobot.models import instant
from intelligence import (
    ChiefEngineerPolicy,
    ChiefEngineerService,
    ContinuousDriver,
    PlanStore,
    SpecialistPool,
    query,
)
from intelligence import prompts
from intelligence.planstore import readonly_view
from intelligence.workforce import specialist_identity
from test_autonomous_pilot import (
    PilotTestCase,
    QueueRunner,
    ScriptedTransport,
    WITNESSES,
    base_fields,
    result,
    roles,
)

FOLLOW_UP = "Investigate the follow-up the diagnosis surfaced."


def investigate(view, objective_id, name, capability="research",
                role="investigator", findings=None, **requirement):
    p = base_fields(view, objective_id)
    body = {"objective": "Investigate workstream %s." % name,
            "capability": capability, "roles": roles(role),
            "guidance": "Investigate %s only." % name}
    body.update(requirement)
    p.update({"class": "INVESTIGATE",
              "rationale": "the objective needs workstream " + name,
              "requirement": body})
    if findings:
        p["provenance"] = {"findings": list(findings), "events": []}
    return p


def retain(view, objective_id, candidate):
    """The explicit workforce decision: retain an outside specialist on
    the strength of its qualifying demonstrations."""
    p = base_fields(view, objective_id)
    p.update({"class": "RETAIN_SPECIALIST",
              "rationale": "verified work; the capability is needed again",
              "specialist_id": candidate["specialist_id"],
              "capability": candidate["capability"],
              "role": candidate["role"],
              "demonstrations":
                  list(candidate["qualifying_demonstrations"])})
    return p


def release(view, objective_id, candidate, reason="task_only"):
    p = base_fields(view, objective_id)
    p.update({"class": "RELEASE_SPECIALIST",
              "rationale": "the capability was needed once",
              "specialist_id": candidate["specialist_id"],
              "capability": candidate["capability"],
              "role": candidate["role"], "reason": reason,
              "demonstrations":
                  list(candidate["qualifying_demonstrations"])})
    return p


def assign(view, objective_id, sequence_id, handle, **extra):
    """The Project Manager explicitly selects an outside specialist for
    one workstream."""
    p = base_fields(view, objective_id)
    p.update({"class": "ASSIGN_SPECIALIST",
              "rationale": "this project needs %s on this workstream"
                           % handle,
              "sequence_id": sequence_id,
              "staffing": {"mode": "outside_specialist",
                           "specialist_id": specialist_identity(handle)}})
    p.update(extra)
    return p


def complete(view, objective_id):
    p = base_fields(view, objective_id)
    p.update({"class": "PROJECT_COMPLETE",
              "rationale": "every workstream is COMPLETED"})
    return p


class TwoTaskPolicy(ChiefEngineerPolicy):
    """Work A; then — deciding about the specialist that did it — work B
    needing the SAME capability; then completion. Stateless: every
    decision is read from the view, so a restarted planner continues."""

    def __init__(self, decision="retain"):
        self.decision = decision
        self.views = []

    def plan(self, view):
        self.views.append(view)
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        sequences = view.authority["sequences"]
        if not sequences:
            return [investigate(view, oid, "A")]
        proposals = []
        for candidate in view.taskforce["retention_candidates"]:
            if self.decision == "retain":
                proposals.append(retain(view, oid, candidate))
            elif self.decision == "release":
                proposals.append(release(view, oid, candidate))
        done = all(s["state"] == "COMPLETED" for s in sequences)
        if len(sequences) == 1 and done:
            proposals.append(investigate(view, oid, "B"))
        if len(sequences) == 2 and done and not proposals:
            proposals.append(complete(view, oid))
        return proposals


class WorkforceTestCase(PilotTestCase):
    HANDLES = ("alpha", "beta")

    def setUp(self):
        PilotTestCase.setUp(self)
        self.pool = SpecialistPool()
        for handle in self.HANDLES:
            self.pool.register("research", "investigator", "fake",
                               handle=handle)
        self.objective_id = "obj-workforce"

    def _objective(self, text="Deliver the workforce objective."):
        self.plan.add_objective(self.objective_id, text, "you",
                                instant(self.clock.now()))

    def _driver(self, policy, runner=None, holder="chief-engineer",
                **kwargs):
        if runner is not None and getattr(runner, "sequencer", None) is None:
            runner.sequencer = self.rt.sequencer
        kwargs.setdefault("repo_path", self.repo)
        return ContinuousDriver(
            self.plan, self.rt.registry, self.rt.sequencer, self.rt.store,
            self.env, self.clock, policy, self.transport, runner, self.pool,
            self.dir / "autobot.db",
            repository_context="the pilot repository, branch main",
            holder=holder, **kwargs)

    def _restart(self, policy, holder, runner=None, **kwargs):
        """Kill the whole intelligence plane and start a new one."""
        if self.driver is not None:
            try:
                self.driver.close()
            except Exception:
                pass
        self.plan.close()
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.driver = self._driver(policy, runner=runner, holder=holder,
                                   **kwargs)
        return self.driver

    def _rows(self, sql, args=()):
        return [dict(r) for r in self.plan.conn.execute(sql,
                                                        args).fetchall()]

    def _assignments(self):
        return self._rows("SELECT * FROM project_assignments ORDER BY"
                          " selected_utc, rowid")

    def _seats(self):
        return self._rows("SELECT * FROM taskforce_seats ORDER BY"
                          " created_utc, rowid")

    def _memberships(self):
        return self._rows("SELECT * FROM taskforce_memberships ORDER BY"
                          " joined_utc, rowid")

    def _projects(self):
        return self._rows("SELECT * FROM projects ORDER BY opened_utc,"
                          " rowid")

    def _team(self, project_id=None):
        if project_id is None:
            return self._rows("SELECT * FROM project_team ORDER BY"
                              " joined_utc, rowid")
        return self._rows("SELECT * FROM project_team WHERE project_id = ?"
                          " ORDER BY joined_utc, rowid", (project_id,))

    def _candidates(self):
        return self.driver.taskforce.roster.retention_candidates()

    def _demonstrations(self):
        return self._rows("SELECT * FROM capability_demonstrations ORDER"
                          " BY recorded_utc, rowid")

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def _handle(self, specialist_id):
        row = self.plan.conn.execute(
            "SELECT handle FROM specialists WHERE specialist_id = ?",
            (specialist_id,)).fetchone()
        return row["handle"]


# ============================================================ the loop

class RetentionAndReuseTests(WorkforceTestCase):
    def test_outside_specialist_is_retained_then_reused(self):
        """The central proof. A needs research and nobody is retained:
        the Taskforce resolves nobody, and an outside specialist is
        selected for the project (here by the standing project-manager
        rule) — which creates NO membership. A is verified and accepted
        by authority; that becomes a capability demonstration; the
        specialist is still outside. The Engineer explicitly retains:
        membership and a retained seat come to exist. B needs the same
        capability and names nobody: the Taskforce resolves the retained
        member, under a NEW authority-bound attempt, which is verified in
        turn. The project completes: its team is dissolved, the
        membership persists."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"))
        final = self.driver.run_until_stable()

        states = self._states()
        self.assertEqual(sorted(states.values()),
                         ["COMPLETED", "COMPLETED"])
        self.assertEqual(final["active_objectives"], [])

        first, second = self._assignments()
        alpha = specialist_identity("alpha")
        seat = self._seats()[0]
        # A: no retained seat -> an outside specialist the Project
        # Manager selected, as an outside specialist.
        self.assertEqual(first["specialist_id"], alpha)
        self.assertEqual(first["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(first["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(first["standing_at_assignment"], "OUTSIDE")
        self.assertIsNone(first["seat_id"])
        decision = self.plan.proposal(first["assignment_decision"])
        self.assertEqual((decision["class"], decision["status"]),
                         ("ASSIGN_SPECIALIST", "APPLIED"))
        body = json.loads(decision["body"])
        self.assertEqual(body["staffing"], {
            "mode": "outside_specialist", "specialist_id": alpha})
        self.assertEqual(body["provenance"]["workforce"]["rule"],
                         "standing-outside-rule/v1")
        # B: the retained member, resolved BY THE TASKFORCE from a
        # capability request that named nobody.
        self.assertEqual(second["specialist_id"], alpha)
        self.assertEqual(second["seat_id"], seat["seat_id"])
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["decided_by"], "TASKFORCE")
        self.assertEqual(second["standing_at_assignment"], "RETAINED")
        self.assertIsNone(second["assignment_decision"])
        reason = json.loads(second["selected_reason"])
        self.assertIsNone(reason["directive"])
        self.assertEqual(reason["outside_alternative"]["handle"], "beta")
        self.assertEqual(reason["chosen"]["seat_id"], seat["seat_id"])
        self.assertEqual(reason["retained_ranked"][0]["qualifying"], 1)

        # Membership and the seat exist because of the retention decision
        # and nothing else.
        self.assertEqual(seat["status"], "RETAINED")
        history = self._rows(
            "SELECT from_status, to_status, cause FROM seat_history WHERE"
            " seat_id = ? ORDER BY history_id", (seat["seat_id"],))
        self.assertEqual(
            [(h["from_status"], h["to_status"], h["cause"])
             for h in history],
            [("OUTSIDE", "RETAINED", "retention_decision")])
        membership = self._memberships()[0]
        self.assertEqual((membership["specialist_id"],
                          membership["status"]), (alpha, "ACTIVE"))
        self.assertEqual(seat["membership_id"],
                         membership["membership_id"])
        # ... which was taken AFTER the first assignment settled.
        self.assertGreaterEqual(membership["joined_utc"],
                                first["settled_utc"])

        # The project was temporary: completed, its team dissolved.
        project = self._projects()[0]
        self.assertEqual(project["status"], "COMPLETED")
        self.assertEqual(
            [(m["specialist_id"], m["joined_as"], m["status"],
              m["end_reason"]) for m in self._team(project["project_id"])],
            [(alpha, "OUTSIDE_SPECIALIST", "RELEASED",
              "project_completed")])
        self.assertTrue(self.driver.taskforce.roster.is_member(alpha))

        # Two demonstrations, each REFERENCING what authority admitted.
        demos = self._demonstrations()
        self.assertEqual([d["outcome"] for d in demos],
                         ["QUALIFYING", "QUALIFYING"])
        self.assertEqual([(d["standing"], d["seat_id"]) for d in demos],
                         [("OUTSIDE", None),
                          ("RETAINED", seat["seat_id"])])
        for demo, assignment in zip(demos, (first, second)):
            self.assertEqual(demo["attempt_id"], assignment["attempt_id"])
            self.assertEqual(demo["call_id"], assignment["call_id"])
            criteria = json.loads(demo["criteria"])
            for name in ("task_completed", "candidate_accepted",
                         "verification_passed", "scope_respected",
                         "governance_satisfied", "repair_coherent",
                         "outcome_useful"):
                self.assertTrue(criteria[name], name)
            ledger = {(r["assertion_id"], r["receipt_sha256"]) for r in
                      self.rt.db.conn.execute(
                          "SELECT assertion_id, receipt_sha256 FROM"
                          " attempt_verifications WHERE attempt_id = ?",
                          (demo["attempt_id"],)).fetchall()}
            cited = {(r["assertion_id"], r["receipt_sha256"])
                     for r in json.loads(demo["verification_refs"])}
            self.assertEqual(cited, ledger)
            self.assertTrue(ledger)
            record = self.rt.db.conn.execute(
                "SELECT record_hash, contract_digest FROM sequences WHERE"
                " sequence_id = ?", (demo["sequence_id"],)).fetchone()
            self.assertEqual((demo["record_hash"],
                              demo["contract_digest"]),
                             (record["record_hash"],
                              record["contract_digest"]))
            refs = json.loads(demo["evidence_refs"])
            for key in ("execution_receipt", "candidate_receipt",
                        "acceptance", "release"):
                self.assertTrue(refs[key].startswith("sha256:"), key)

        # The retention decision binds the first demonstration.
        decision = self._rows("SELECT * FROM retention_decisions")[0]
        self.assertEqual(decision["kind"], "RETAIN")
        self.assertEqual(json.loads(decision["demonstration_ids"]),
                         [demos[0]["demonstration_id"]])
        proposal = self.plan.proposal(decision["proposal_id"])
        self.assertEqual((proposal["class"], proposal["status"]),
                         ("RETAIN_SPECIALIST", "APPLIED"))

        # The retained member's second run is a NEW authority-bound
        # attempt: its own AttemptContract, its own call, its own
        # manifest — nothing carried over from the first.
        attempts = [dict(r) for r in self.rt.db.conn.execute(
            "SELECT a.attempt_id, a.sequence_id, a.state,"
            " a.attempt_contract_hash, c.call_id FROM attempts a JOIN"
            " attempt_calls c ON c.attempt_id = a.attempt_id ORDER BY"
            " a.rowid").fetchall()]
        self.assertEqual(len(attempts), 2)
        self.assertEqual({a["state"] for a in attempts}, {"VERIFIED"})
        self.assertEqual(len({a["attempt_contract_hash"]
                              for a in attempts}), 2)
        self.assertEqual(len({a["call_id"] for a in attempts}), 2)
        self.assertEqual(len({a["sequence_id"] for a in attempts}), 2)
        manifests = [l["manifest_address"]
                     for l in self.transport.launch_log]
        self.assertEqual(len(set(manifests)), 2)
        for address, attempt in zip(manifests, attempts):
            manifest = json.loads(self.rt.store.get(address))
            self.assertEqual(manifest["attempt_id"], attempt["attempt_id"])
            self.assertEqual(
                manifest["provider_assignment"]["selected_by"],
                "agent_factory")
        self.assertTrue(self.driver.step()["quiet"])

    def test_provider_replacement_changes_no_contract_identity(self):
        """Activity stable, execution implementation replaceable. The
        retained member is rerouted to a different provider and model
        between A and B: the specialist and its seat are the same, B
        executes on the new route — and no TaskContractRecord identity
        moves, because no frozen record ever named a provider."""
        from contract_library import compute_tiers
        from intelligence import compile_proposal
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        for _ in range(30):
            self.driver.step()
            seats = self._seats()
            if seats and seats[0]["status"] == "RETAINED":
                break
        seat = self._seats()[0]
        first = self._assignments()[0]
        proposal = self._rows(
            "SELECT * FROM proposals WHERE sequence_id = ?",
            (first["sequence_id"],))[0]
        frozen_digest = self.rt.db.conn.execute(
            "SELECT contract_digest FROM sequences WHERE sequence_id = ?",
            (first["sequence_id"],)).fetchone()["contract_digest"]

        self.pool.reroute("alpha", provider="other-provider", model="m9")
        self.driver.run_until_stable()

        first, second = self._assignments()
        self.assertEqual(second["specialist_id"], first["specialist_id"])
        self.assertEqual(second["seat_id"], seat["seat_id"])
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual((first["provider"], first["model"]),
                         ("fake", None))
        self.assertEqual((second["provider"], second["model"]),
                         ("other-provider", "m9"))
        manifests = [json.loads(self.rt.store.get(l["manifest_address"]))
                     for l in self.transport.launch_log]
        self.assertEqual(
            [m["provider_assignment"]["provider"] for m in manifests],
            ["fake", "other-provider"])
        self.assertEqual(manifests[1]["provider_assignment"]["model"],
                         "m9")
        # The same engineering requirement, compiled again AFTER the
        # reroute, is the same contract: identity never saw the route.
        recompiled = compile_proposal(json.loads(proposal["body"]),
                                      self.env, self.clock)
        _rd, contract_digest, _rh = compute_tiers(recompiled)
        self.assertEqual(contract_digest, frozen_digest)
        for row in self.rt.db.conn.execute(
                "SELECT content_address FROM records").fetchall():
            payload = self.rt.store.get(
                row["content_address"]).decode("utf-8")
            self.assertNotIn("other-provider", payload)
            self.assertNotIn("m9", payload)
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        specialist = self.plan.conn.execute(
            "SELECT provider, model, first_seen_utc FROM specialists"
            " WHERE specialist_id = ?",
            (first["specialist_id"],)).fetchone()
        self.assertEqual((specialist["provider"], specialist["model"]),
                         ("other-provider", "m9"))

    def test_without_a_decision_the_specialist_stays_outside(self):
        """The control arm: identical work, but the Engineer never
        decides about the specialist. Both tasks are verified and
        accepted, both are recorded as QUALIFYING demonstrations — and
        there is no membership, no seat, and nothing for the Taskforce to
        resolve: B is an outside assignment again. Retention, not prior
        success, is what changed the first test's staffing."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        first, second = self._assignments()
        for assignment in (first, second):
            self.assertEqual(assignment["selection_path"],
                             "OUTSIDE_SPECIALIST")
            self.assertEqual(assignment["decided_by"], "PROJECT_MANAGER")
            self.assertEqual(assignment["standing_at_assignment"],
                             "OUTSIDE")
            self.assertIsNone(assignment["seat_id"])
            self.assertEqual(assignment["outcome"], "VERIFIED_ACCEPTED")
        self.assertEqual([d["outcome"] for d in self._demonstrations()],
                         ["QUALIFYING", "QUALIFYING"])
        self.assertEqual(self._seats(), [])
        self.assertEqual(self._memberships(), [])
        self.assertEqual(self._rows("SELECT * FROM retention_decisions"),
                         [])
        # The verified work is on record as a candidacy, which is not a
        # standing; completing the project did not turn it into one.
        candidate = self._candidates()[0]
        self.assertEqual(len(candidate["qualifying_demonstrations"]), 2)
        self.assertEqual(self._projects()[0]["status"], "COMPLETED")
        self.assertEqual({m["status"] for m in self._team()}, {"RELEASED"})

    def test_successful_specialist_can_be_released(self):
        """Successful execution does not create membership: the
        specialist's work is verified, accepted and recorded as a
        QUALIFYING demonstration — and the Engineer releases it as
        task-only. The release changes no Taskforce membership (there is
        none to change); it ends the specialist's participation in the
        project."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("release"),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        first, second = self._assignments()
        self.assertEqual(first["outcome"], "VERIFIED_ACCEPTED")
        self.assertEqual(first["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(second["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(self._demonstrations()[0]["outcome"],
                         "QUALIFYING")
        decisions = self._rows("SELECT kind, reason, seat_id, project_id"
                               " FROM retention_decisions ORDER BY"
                               " decided_utc, rowid")
        project = self._projects()[0]
        self.assertEqual(
            [(d["kind"], d["reason"], d["seat_id"], d["project_id"])
             for d in decisions],
            [("RELEASE_OUTSIDE", "task_only", None,
              project["project_id"])] * 2)
        self.assertEqual(self._seats(), [])
        self.assertEqual(self._memberships(), [])
        self.assertEqual(self._candidates(), [])
        report = query.taskforce_report(self.plan)
        self.assertEqual(report["members"], [])
        self.assertEqual(report["retained_members"], [])
        self.assertEqual(set(self._states().values()), {"COMPLETED"})

    def test_the_project_manager_selects_a_specific_outside_specialist(
            self):
        """The explicit path, end to end: the Engineer, as Project
        Manager, names the outside specialist for a workstream — not the
        first in the catalog — and that specialist executes it. The
        frozen TaskContractRecord names no specialist: the decision lives
        in the project assignment."""
        beta = specialist_identity("beta")

        class SelectingPolicy(ChiefEngineerPolicy):
            def plan(self, view):
                if not view.objectives:
                    return []
                oid = view.objectives[0]["objective_id"]
                sequences = view.authority["sequences"]
                if not sequences:
                    return [investigate(view, oid, "A")]
                needs = view.projects[oid]["needs_staffing"]
                if needs:
                    return [assign(view, oid, needs[0]["sequence_id"],
                                   "beta")]
                if all(s["state"] == "COMPLETED" for s in sequences):
                    return [complete(view, oid)]
                return []

        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        self.driver = self._driver(SelectingPolicy(), runner=QueueRunner())
        self.driver.run_until_stable()
        assignment = self._assignments()[0]
        self.assertEqual(assignment["specialist_id"], beta)
        self.assertEqual(assignment["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(assignment["decided_by"], "PROJECT_MANAGER")
        decision = self.plan.proposal(assignment["assignment_decision"])
        self.assertEqual(decision["class"], "ASSIGN_SPECIALIST")
        self.assertNotIn("workforce", json.loads(decision["body"])[
            "provenance"])  # the policy's own decision, not the rule's
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        for row in self.rt.db.conn.execute(
                "SELECT content_address FROM records").fetchall():
            payload = self.rt.store.get(
                row["content_address"]).decode("utf-8")
            self.assertNotIn(beta, payload)
            self.assertNotIn("beta", payload)
        self.assertEqual(self._memberships(), [])

    def test_workforce_decisions_never_touch_authority(self):
        """A retention is applied to Taskforce state only: the cycle that
        disposes it submits nothing to the Registry and appends nothing
        to the authority journal."""
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        policy = TwoTaskPolicy("none")
        self.driver = self._driver(policy, runner=QueueRunner())
        for _ in range(12):
            self.driver.step()
            if self._candidates():
                break
        review = self._candidates()[0]
        self.assertEqual(self._memberships(), [])
        calls = []
        real = self.rt.registry

        class SpyRegistry(object):
            def admit_payload(self, payload, submitted_by):
                calls.append(submitted_by)
                return real.admit_payload(payload,
                                          submitted_by=submitted_by)

        class OnlyRetain(ChiefEngineerPolicy):
            def plan(self, view):
                return [retain(view, view.objectives[0]["objective_id"],
                               review)]

        ro = readonly_view(self.dir / "autobot.db")
        try:
            # Wake the planner with a workforce event of its own making.
            with self.plan.transaction() as cur:
                self.plan.emit_workforce_event(
                    cur, "test:wake", "demonstration_recorded",
                    review["specialist_id"], None,
                    instant(self.clock.now()))
            service = ChiefEngineerService(
                self.plan, SpyRegistry(), self.env, self.clock,
                OnlyRetain(), roster=self.driver.taskforce.roster)
            before = self._journal_count()
            report = service.run_cycle(ro, project_state,
                                       instant(self.clock.now()))
        finally:
            ro.close()
        self.assertEqual(report["proposals"][0]["status"], "APPLIED")
        self.assertTrue(report["proposals"][0]["membership_created"])
        self.assertEqual(calls, [])
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self._seats()[0]["status"], "RETAINED")
        self.assertEqual(self._memberships()[0]["status"], "ACTIVE")


# =================================================== multi-workstream

class OrganizationPolicy(ChiefEngineerPolicy):
    """Three workstreams from one decomposition — A diagnoses, B
    implements, C reviews B — then follow-up from A's finding, retention
    of the specialist that produced it, and completion."""

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        sequences = view.authority["sequences"]
        if not sequences:
            p = base_fields(view, oid)
            p.update({
                "class": "DECOMPOSE_TASK",
                "rationale": "diagnose, implement, then review the"
                             " implementation",
                "parts": [
                    {"part_id": "a-diagnose", "requirement": {
                        "objective": "Diagnose the subsystem.",
                        "finalization_class": "READ_ONLY",
                        "capability": "research",
                        "roles": roles("investigator"),
                        "subsystem": "pilot.subsystem",
                        "guidance": "Diagnose the subsystem; report."}},
                    {"part_id": "b-impl", "requirement": {
                        "objective": "Implement the change.",
                        "finalization_class": "SOURCE_MODIFYING",
                        "capability": "software_implementation",
                        "roles": roles("builder"),
                        "guidance": "Implement the change."}},
                    {"part_id": "c-review",
                     "depends_on_parts": ["b-impl"],
                     "requirement": {
                        "objective": "Review the landed change.",
                        "finalization_class": "READ_ONLY",
                        "capability": "verification",
                        "roles": roles("reviewer"),
                        "guidance": "Review the landed change."}},
                ]})
            return [p]
        proposals = []
        for candidate in view.taskforce["retention_candidates"]:
            if candidate["capability"] == "research":
                proposals.append(retain(view, oid, candidate))
            else:
                proposals.append(release(view, oid, candidate))
        for finding in view.findings:
            if finding["finding_class"] == "newly_discovered_work":
                proposals.append(investigate(
                    view, oid, "D-follow-up",
                    findings=[finding["finding_id"]],
                    guidance=finding["suggested_follow_up"],
                    subsystem="pilot.subsystem"))
        permits = {row["sequence_id"]: row
                   for row in view.authority["permits"]}
        for seq_id in view.workstreams["finalization_queue"]:
            if seq_id not in permits:
                p = base_fields(view, oid)
                p.update({"class": "WAIT_FOR_AUTHORITY",
                          "rationale": "finalization needs your permit",
                          "reason": "publication permit required (cap-36)",
                          "subject_id": seq_id,
                          "provenance": {"findings": [], "events": [
                              e["journal_seq"] for e in view.events
                              if e["subject_id"] == seq_id]}})
                proposals.append(p)
        if not proposals and len(sequences) == 4 and all(
                s["state"] == "COMPLETED" for s in sequences):
            proposals.append(complete(view, oid))
        return proposals


class MultiWorkstreamTests(WorkforceTestCase):
    def setUp(self):
        WorkforceTestCase.setUp(self)
        self.pool = SpecialistPool()
        self.pool.register("research", "investigator", "fake",
                           handle="alpha")
        self.pool.register("software_implementation", "builder", "fake",
                           handle="beta")
        self.pool.register("verification", "reviewer", "fake",
                           handle="gamma")

    def test_concurrent_workstreams_dependencies_and_follow_up(self):
        """The autonomous loop, whole: objective -> reasoning ->
        decomposition into a project's workstreams -> a team of outside
        specialists selected for the project -> concurrent execution ->
        evidence -> verification -> a finding -> re-reasoning -> the plan
        adjusted with follow-up work -> acceptance -> an explicit
        retention (the follow-up is then resolved by the Taskforce to the
        retained member) -> completion -> a stable quiet state, the team
        dissolved and the membership kept."""
        self.transport.scripts = {
            "investigator": [
                result(findings=[{
                    "finding_class": "newly_discovered_work",
                    "affected_component": "pilot.subsystem",
                    "statement": "The diagnosis found one more gap.",
                    "evidence_refs": [], "significance": "high",
                    "confidence": "high",
                    "relation_to_requirement": "within",
                    "suggested_follow_up": FOLLOW_UP}]),
                result()],
            "builder": [result(artifact=json.dumps(
                {"target_ref": "refs/heads/main",
                 "expected_old": self.sha_old,
                 "new_value": self.sha_new}).encode("utf-8"))],
            "reviewer": [result()],
        }
        self._objective("Diagnose, change and review the subsystem.")
        self.driver = self._driver(OrganizationPolicy(),
                                   runner=QueueRunner())
        reports = []
        for _ in range(40):
            report = self.driver.step()
            reports.append(report)
            if self.plan.proposals_by_status("WAITING"):
                break
        # A and B are independent: staffed in the SAME sweep, onto two
        # different specialists; C is not staffed (BLOCKED on B).
        concurrent = [r["sweep"]["allocated"] for r in reports
                      if len(r["sweep"]["allocated"]) >= 2]
        self.assertEqual(len(concurrent), 1)
        self.assertEqual(
            sorted(self._handle(a["specialist_id"])
                   for a in concurrent[0]), ["alpha", "beta"])
        b_id, _b_record, _ = self._sequence_by_objective(
            "Implement the change")
        c_id, _c_record, _ = self._sequence_by_objective(
            "Review the landed change")
        self.assertEqual(self._states()[b_id], "VERIFIED")
        self.assertEqual(self._states()[c_id], "BLOCKED")
        self.assertEqual(
            [a for a in self._assignments() if a["sequence_id"] == c_id],
            [])

        # YOUR word: the publication permit.
        self.rt.sequencer.grant_permit(b_id, permitted_uses=1)
        final = self.driver.run_until_stable()
        states = self._states()
        self.assertEqual(len(states), 4)
        self.assertEqual(set(states.values()), {"COMPLETED"})
        self.assertEqual(final["active_objectives"], [])
        head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "refs/heads/main"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, self.sha_new)

        # Dependency ordering came from AUTHORITY: C was BLOCKED (t-s01)
        # and became READY only through the released edge (t-s02), and
        # was staffed only after B's release.
        c_rules = self._rules_for(c_id)
        self.assertLess(c_rules.index("t-s01"), c_rules.index("t-s02"))
        released = self.rt.db.conn.execute(
            "SELECT released_utc FROM released_edges WHERE"
            " predecessor_sequence_id = ? LIMIT 1", (b_id,)).fetchone()
        c_assignment = [a for a in self._assignments()
                        if a["sequence_id"] == c_id][0]
        self.assertGreaterEqual(c_assignment["selected_utc"],
                                released["released_utc"])

        # The finding fed the Engineer: D exists because of it...
        d_id, _d_record, d_payload = self._sequence_by_objective(
            "Investigate workstream D-follow-up")
        finding = self.plan.conn.execute(
            "SELECT * FROM findings").fetchone()
        consequences = query.finding_consequences(
            self.plan, finding["finding_id"])
        self.assertEqual([c["sequence_id"] for c in consequences], [d_id])
        # ... and workforce learning staffed it: alpha was retained on
        # the diagnosis and is selected for the follow-up as a member.
        d_assignment = [a for a in self._assignments()
                        if a["sequence_id"] == d_id][0]
        self.assertEqual(self._handle(d_assignment["specialist_id"]),
                         "alpha")
        self.assertEqual(d_assignment["selection_path"], "RETAINED_SEAT")
        self.assertEqual(json.loads(d_assignment["selected_reason"])
                         ["retained_ranked"][0]["system_experience"], 1)

        by_handle = {}
        for assignment in self._assignments():
            by_handle.setdefault(
                self._handle(assignment["specialist_id"]), []).append(
                    assignment["selection_path"])
        self.assertEqual(by_handle, {
            "alpha": ["OUTSIDE_SPECIALIST", "RETAINED_SEAT"],
            "beta": ["OUTSIDE_SPECIALIST"],
            "gamma": ["OUTSIDE_SPECIALIST"]})
        # Three specialists took part in the project. ONE is a Taskforce
        # member — the one explicitly retained; the other two were let go
        # and no membership or seat was ever created for them.
        seats = {self._handle(s["specialist_id"]): s["status"]
                 for s in self._seats()}
        self.assertEqual(seats, {"alpha": "RETAINED"})
        self.assertEqual(
            [self._handle(m["specialist_id"]) for m in
             self._memberships()], ["alpha"])
        decisions = sorted(
            (self._handle(d["specialist_id"]), d["kind"]) for d in
            self._rows("SELECT * FROM retention_decisions"))
        self.assertEqual(decisions, [("alpha", "RETAIN"),
                                     ("beta", "RELEASE_OUTSIDE"),
                                     ("gamma", "RELEASE_OUTSIDE")])
        project = self._projects()[0]
        self.assertEqual(project["status"], "COMPLETED")
        team = {self._handle(m["specialist_id"]): m
                for m in self._team(project["project_id"])}
        self.assertEqual(sorted(team), ["alpha", "beta", "gamma"])
        self.assertEqual({m["joined_as"] for m in team.values()},
                         {"OUTSIDE_SPECIALIST"})
        self.assertEqual({m["status"] for m in team.values()},
                         {"RELEASED"})
        self.assertEqual(
            sorted(w["workstream_key"] for w in self._rows(
                "SELECT workstream_key FROM project_workstreams")),
            ["a-diagnose", "b-impl", "c-review", "w4"])
        # Every specialist — the retained one included — went through
        # the same lifecycle: a verified attempt per sequence.
        for assignment in self._assignments():
            verifications = self.rt.db.conn.execute(
                "SELECT COUNT(*) AS n FROM attempt_verifications WHERE"
                " attempt_id = ? AND outcome = 'PASS'",
                (assignment["attempt_id"],)).fetchone()["n"]
            self.assertGreaterEqual(verifications, 2)
        self.assertTrue(self.driver.step()["quiet"])

        graph = query.artifact_graph(
            self.plan, self.driver.ro, self.objective_id)
        kinds = {n["kind"] for n in graph["nodes"]}
        for kind in ("objective", "project", "workstream", "decision",
                     "task", "record", "assignment", "specialist",
                     "attempt", "candidate", "verification",
                     "accepted_result", "finding", "demonstration",
                     "membership", "seat"):
            self.assertIn(kind, kinds)
        relations = {e["relation"] for e in graph["edges"]}
        for relation in ("realized_by", "has_workstream", "is_work",
                         "takes_part_in", "decided", "created",
                         "frozen_as", "staffed_by", "assigns",
                         "resolved_to", "executes_as", "verified_by",
                         "accepted_as", "reported", "informed",
                         "demonstrated", "bound_by_retain",
                         "member_through", "holds_seat"):
            self.assertIn(relation, relations)
        self.assertEqual(
            [e["released"] for e in query.dependencies(
                self.plan, self.driver.ro, self.objective_id)], [True])


# ===================================================== repair staffing

class RepairStaffingPolicy(ChiefEngineerPolicy):
    def __init__(self, mode, specialist=None):
        self.mode = mode
        self.staffing = {"mode": mode}
        if specialist is not None:
            self.staffing["specialist_id"] = specialist_identity(specialist)

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        if not view.authority["sequences"]:
            return [investigate(view, oid, "R")]
        proposals = []
        for event in view.events:
            if event["event_kind"] != "repair_ready":
                continue
            p = base_fields(view, oid)
            p.update({
                "class": "REPAIR_TASK",
                "rationale": "verification failed inside the frozen"
                             " repair budget",
                "sequence_id": event["subject_id"],
                "capability": "research",
                "role": "investigator",
                "guidance": "Repair the failed assertion.",
                "staffing": dict(self.staffing),
                "provenance": {"findings": [],
                               "events": [event["journal_seq"]]}})
            proposals.append(p)
        sequences = view.authority["sequences"]
        if not proposals and all(s["state"] == "COMPLETED"
                                 for s in sequences):
            proposals.append(complete(view, oid))
        return proposals


class RepairOrchestrationTests(WorkforceTestCase):
    def _run(self, mode, specialist=None):
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(RepairStaffingPolicy(mode, specialist),
                                   runner=QueueRunner({"a2": ["FAIL"]}))
        self.driver.run_until_stable()
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        return self._assignments()

    def _qualifying_by_handle(self):
        return {c["handle"]: len(c["qualifying_demonstrations"])
                for c in self._candidates()}

    def test_same_member_repairs_its_own_work(self):
        first, second = self._run("same_member")
        self.assertEqual(second["specialist_id"], first["specialist_id"])
        self.assertEqual(second["attempt_ordinal"], 2)
        self.assertNotEqual(second["attempt_id"], first["attempt_id"])
        self.assertEqual(second["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(json.loads(second["selected_reason"])
                         ["directive"]["mode"], "same_member")
        outcomes = [(d["outcome"], json.loads(d["criteria"])["settled_as"])
                    for d in self._demonstrations()]
        self.assertEqual(outcomes, [
            ("NOT_QUALIFYING", "VERIFICATION_FAILED"),
            ("QUALIFYING", "VERIFIED_ACCEPTED")])
        # The repair that verified is the specialist's qualifying work —
        # and it is still an outside specialist.
        self.assertEqual(self._qualifying_by_handle(), {"alpha": 1})
        self.assertEqual(self._seats(), [])
        self.assertEqual(self._memberships(), [])
        repair = self._rows("SELECT proposal_id FROM proposals WHERE class"
                            " = 'REPAIR_TASK'")[0]["proposal_id"]
        self.assertEqual(second["authorized_by_proposal"], repair)
        self.assertEqual(second["assignment_decision"], repair)

    def test_a_different_member_repairs(self):
        """'A different member' is a capability request the Taskforce
        cannot satisfy here (nobody is retained), so the Project Manager
        selects — and the specialist that failed is excluded."""
        first, second = self._run("different_member")
        self.assertEqual(self._handle(first["specialist_id"]), "alpha")
        self.assertEqual(self._handle(second["specialist_id"]), "beta")
        self.assertEqual(second["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(second["decided_by"], "PROJECT_MANAGER")
        # The repair is authorized by the Engineer's REPAIR_TASK and
        # staffed by the Project Manager's later decision.
        repair = self._rows("SELECT proposal_id FROM proposals WHERE class"
                            " = 'REPAIR_TASK'")[0]["proposal_id"]
        self.assertEqual(second["authorized_by_proposal"], repair)
        decision = self.plan.proposal(second["assignment_decision"])
        self.assertEqual(decision["class"], "ASSIGN_SPECIALIST")
        self.assertEqual(
            [(d["mode"], d["status"]) for d in
             self.plan.assignment_decisions(
                 sequence_id=second["sequence_id"])][-2:],
            [("different_member", "SUPERSEDED"),
             ("outside_specialist", "APPLIED")])
        # The failed first attempt earned nothing; the repairer's work
        # qualifies.
        self.assertEqual(self._qualifying_by_handle(), {"beta": 1})
        self.assertEqual(self._seats(), [])

    def test_an_outside_specialist_joins_for_the_repair(self):
        first, second = self._run("outside_specialist", "beta")
        self.assertNotEqual(second["specialist_id"],
                            first["specialist_id"])
        self.assertEqual(self._handle(second["specialist_id"]), "beta")
        self.assertEqual(second["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(second["decided_by"], "PROJECT_MANAGER")
        reason = json.loads(second["selected_reason"])
        self.assertEqual(reason["directive"]["previous_specialist_id"],
                         first["specialist_id"])
        self.assertEqual(reason["directive"]["specialist_id"],
                         second["specialist_id"])
        team = self._team()
        self.assertEqual(len(team), 2)  # both took part in the project


# ============================================== provider-failed attempts

def provider_failure(error="usage window exhausted"):
    """What a transport collects when the PROVIDER fails the session
    before the specialist does any work: a failed outcome and the
    provider's own result record (the obj-11 witness)."""
    reported = json.dumps(
        {"outcome": "failed", "error": error, "exit_code": 1,
         "diagnostics": {"phase": "execution"}},
        sort_keys=True).encode("utf-8")
    return {"witness_files": [("obj-11", "autoswarm", reported)],
            "outcome": "failed", "findings": []}


class RetryPolicy(ChiefEngineerPolicy):
    """One investigation. When authority says the work is READY again
    after a failed attempt, the Engineer orders another attempt — or,
    with ``repair`` off, says nothing."""

    def __init__(self, mode="same_member", repair=True, twice=False):
        self.mode = mode
        self.repair = repair
        self.twice = twice

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        if not view.authority["sequences"]:
            return [investigate(view, oid, "R")]
        proposals = []
        for event in view.events:
            if event["event_kind"] != "retry_ready" or not self.repair:
                continue
            modes = [self.mode] + (["taskforce"] if self.twice else [])
            for mode in modes:
                p = base_fields(view, oid)
                p.update({
                    "class": "REPAIR_TASK",
                    "rationale": "the provider failed the session; the"
                                 " work is ready again",
                    "sequence_id": event["subject_id"],
                    "capability": "research",
                    "role": "investigator",
                    "guidance": "Run the investigation again.",
                    "staffing": {"mode": mode},
                    "provenance": {"findings": [],
                                   "events": [event["journal_seq"]]}})
                proposals.append(p)
        sequences = view.authority["sequences"]
        if not proposals and all(s["state"] == "COMPLETED"
                                 for s in sequences):
            proposals.append(complete(view, oid))
        return proposals


class ProviderFailureTests(WorkforceTestCase):
    """An attempt the PROVIDER failed (a usage window exhausted, a session
    that died) is genuine history and recoverable work — never a dead
    end, never a silent re-execution, and never accepted state."""

    def _run(self, script, **policy):
        self.transport.scripts = {"investigator": script}
        self._objective()
        self.driver = self._driver(RetryPolicy(**policy),
                                   runner=QueueRunner())
        return self.driver.run_until_stable()

    def _attempt_states(self):
        return [r["state"] for r in self.driver.ro.execute(
            "SELECT state FROM attempts ORDER BY rowid").fetchall()]

    def _planning_kinds(self, kind):
        return [r for r in self._rows(
            "SELECT * FROM planning_events WHERE event_kind = ?", (kind,))]

    def _workstream(self):
        project_id = self._projects()[0]["project_id"]
        return query.project_state(
            self.plan, self.driver.ro, project_id,
            store=self.rt.store)["workstreams"][0]

    def test_a_failed_attempt_is_recovered_on_the_engineers_word(self):
        """The provider fails the first session. Authority records the
        attempt FAILED and accepts nothing; the sequence returns to READY
        through the registered retry arc (t-s08); the Engineer orders
        another attempt; the SAME workstream completes on a second
        AttemptContract. The failed attempt stays in the history with
        what the provider itself reported."""
        final = self._run([provider_failure("usage window exhausted"),
                           result()])
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(list(self._states().values()), ["COMPLETED"])
        self.assertEqual(self._attempt_states(), ["FAILED", "VERIFIED"])
        self.assertEqual(len(self._planning_kinds("retry_ready")), 1)
        first, second = self._assignments()
        self.assertEqual((first["outcome"], second["outcome"]),
                         ("ATTEMPT_FAILED", "VERIFIED_ACCEPTED"))
        self.assertEqual(second["sequence_id"], first["sequence_id"])
        self.assertEqual(second["attempt_ordinal"], 2)
        self.assertNotEqual(second["attempt_id"], first["attempt_id"])
        self.assertNotEqual(second["call_id"], first["call_id"])
        self.assertEqual(second["specialist_id"], first["specialist_id"])
        repair = self._rows("SELECT proposal_id FROM proposals WHERE class"
                            " = 'REPAIR_TASK'")[0]["proposal_id"]
        self.assertEqual(second["authorized_by_proposal"], repair)
        # The failed attempt is a record that says WHY, in the provider's
        # own words — and it earned nothing.
        failed, verified = self._demonstrations()
        criteria = json.loads(failed["criteria"])
        self.assertEqual(failed["outcome"], "NOT_QUALIFYING")
        self.assertEqual(criteria["settled_as"], "ATTEMPT_FAILED")
        reported = criteria["observations"]["provider_report"]
        self.assertEqual(reported["error"], "usage window exhausted")
        self.assertEqual(reported["phase"], "execution")
        self.assertEqual(verified["outcome"], "QUALIFYING")
        self.assertNotIn("provider_report",
                         json.loads(verified["criteria"])["observations"])
        told = [json.loads(e["facts"]) for e in self._rows(
            "SELECT facts FROM workforce_events WHERE event_kind ="
            " 'demonstration_recorded' ORDER BY event_id")]
        self.assertEqual(told[0]["provider_error"],
                         "usage window exhausted")
        self.assertEqual(told[0]["settled_as"], "ATTEMPT_FAILED")
        self.assertNotIn("provider_error", told[1])
        # The project keeps the failed attempt beside the work.
        workstream = self._workstream()
        self.assertEqual(workstream["state"], "COMPLETED")
        self.assertEqual(workstream["attempts"], 2)
        self.assertEqual(
            [(a["attempt_id"], a["state"],
              a["provider_report"]["error"])
             for a in workstream["failed_attempts"]],
            [(first["attempt_id"], "FAILED", "usage window exhausted")])
        self.assertEqual(self._seats(), [])  # and nobody was retained

    def test_a_failed_attempt_never_re_executes_itself(self):
        """Returning to READY starts nothing: with no word from the
        Engineer the workstream waits, visibly, with one attempt."""
        final = self._run([provider_failure(), result()], repair=False)
        self.assertEqual(len(final["active_objectives"]), 1)
        self.assertEqual(list(self._states().values()), ["READY"])
        self.assertEqual(self._attempt_states(), ["FAILED"])
        self.assertEqual(len(self._assignments()), 1)
        self.assertEqual(len(self._planning_kinds("retry_ready")), 1)
        self.assertEqual(len(self.transport.scripts["investigator"]), 1)

    def test_one_word_authorizes_one_attempt(self):
        """Ordering the same pending attempt twice is refused: a second
        authorization would outlive the attempt and start a later one
        nobody ordered. The work runs once more, on the first word."""
        final = self._run([provider_failure(), result()], twice=True)
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(self._attempt_states(), ["FAILED", "VERIFIED"])
        repairs = self._rows(
            "SELECT status, note FROM proposals WHERE class ="
            " 'REPAIR_TASK' ORDER BY created_utc, rowid")
        self.assertEqual([r["status"] for r in repairs],
                         ["APPLIED", "REJECTED"])
        self.assertIn("already authorized", repairs[1]["note"])
        self.assertEqual(len(self._rows(
            "SELECT * FROM seats WHERE authorized_by_proposal IS NOT"
            " NULL")), 1)
        self.assertEqual(
            self.plan.budget_usage(self.objective_id)["repairs"], 1)
        self.assertIsNone(self.plan.open_authorization(
            self._assignments()[0]["sequence_id"]))
        second = self._assignments()[1]
        self.assertEqual(json.loads(second["selected_reason"])
                         ["directive"]["mode"], "same_member")

    def test_the_frozen_retry_budget_bounds_recovery(self):
        """max_retries 2: three attempts and no more. When the provider
        fails the third, the Sequencer refuses the retry arc, the
        sequence stays where it is and the Engineer is told once."""
        self._run([provider_failure(), provider_failure(),
                   provider_failure()])
        self.assertEqual(self._attempt_states(),
                         ["FAILED", "FAILED", "FAILED"])
        self.assertEqual(list(self._states().values()), ["EXECUTING"])
        self.assertEqual(len(self._planning_kinds("retry_ready")), 2)
        exhausted = self._rows(
            "SELECT * FROM workforce_events WHERE event_kind ="
            " 'retry_exhausted'")
        self.assertEqual(len(exhausted), 1)
        self.assertIn("retry budget exhausted",
                      json.loads(exhausted[0]["facts"])["reason"])
        self.assertEqual(len(self._workstream()["failed_attempts"]), 3)
        # Looking again changes nothing and tells nobody twice.
        for _ in range(3):
            self.driver.step()
        self.assertEqual(len(self._rows(
            "SELECT * FROM workforce_events WHERE event_kind ="
            " 'retry_exhausted'")), 1)


# ====================================================== restart recovery

class _Boom(Exception):
    pass


def _fail_once(obj, name):
    """Make ``obj.name`` raise the first time it is called."""
    real = getattr(obj, name)
    state = {"failed": False}

    def wrapper(*args, **kwargs):
        if not state["failed"]:
            state["failed"] = True
            raise _Boom(name)
        return real(*args, **kwargs)

    setattr(obj, name, wrapper)
    return lambda: setattr(obj, name, real)


class RestartRecoveryTests(WorkforceTestCase):
    def _step_until(self, predicate, limit=30):
        for _ in range(limit):
            self.driver.step()
            if predicate():
                return
        raise AssertionError("condition not reached in %d steps" % limit)

    def _one_of_each(self):
        self.assertEqual(len(self._assignments()), 1)
        self.assertEqual(len(self._team()), 1)
        self.assertEqual(len(self._rows(
            "SELECT * FROM assignment_decisions WHERE status ="
            " 'APPLIED'")), 1)
        self.assertEqual(self._seats(), [])
        self.assertEqual(self._memberships(), [])
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempts").fetchone()["n"], 1)
        self.assertEqual(len(self.transport.sessions), 1)

    def test_restart_after_selection_before_the_attempt_exists(self):
        """outside specialist selected + project assignment written ->
        crash before authority minted the attempt -> restart: the
        staffing is re-derived onto the SAME rows."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        restore = _fail_once(self.rt.sequencer, "start_execution")
        with self.assertRaises(_Boom):
            for _ in range(8):
                self.driver.step()
        restore()
        selected = self._assignments()
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["status"], "SELECTED")
        self.assertIsNone(selected[0]["attempt_id"])
        self.assertEqual(selected[0]["selection_path"],
                         "OUTSIDE_SPECIALIST")
        self.assertEqual(len(self._team()), 1)
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempts").fetchone()["n"], 0)

        self._restart(TwoTaskPolicy("none"), "chief-b",
                      runner=QueueRunner())
        self._step_until(lambda: self._assignments()[0]["status"]
                         != "SELECTED")
        self._one_of_each()
        self.assertEqual(self._assignments()[0]["assignment_id"],
                         selected[0]["assignment_id"])
        # One staffing decision, applied once: the restart decided
        # nothing again and joined nobody to the team twice.
        self.assertEqual(len(self._rows(
            "SELECT * FROM proposals WHERE class ="
            " 'ASSIGN_SPECIALIST'")), 1)

    def test_restart_between_dispatch_and_launch(self):
        """The attempt is DISPATCHED (call_id minted) but the session was
        never handed to the provider -> restart relaunches on the SAME
        call_id, exactly once."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        restore = _fail_once(self.transport, "launch_manifest")
        with self.assertRaises(_Boom):
            for _ in range(8):
                self.driver.step()
        restore()
        row = self._assignments()[0]
        self.assertEqual(row["status"], "DISPATCHED")
        self.assertEqual(self.transport.sessions, {})
        self._restart(TwoTaskPolicy("none"), "chief-b",
                      runner=QueueRunner())
        self._step_until(lambda: self.transport.sessions)
        self._one_of_each()
        self.assertEqual(list(self.transport.sessions),
                         [row["call_id"]])
        self.driver.run_until_stable()
        self.assertEqual(set(self._states().values()), {"COMPLETED"})

    def test_restart_while_the_specialist_is_executing(self):
        self.transport.scripts = {"investigator": []}  # still running
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        self._step_until(lambda: self._assignments()
                         and self._assignments()[0]["status"]
                         == "EXECUTING")
        launches = len(self.transport.launch_log)
        self._restart(TwoTaskPolicy("none"), "chief-b",
                      runner=QueueRunner())
        self.transport.scripts = {"investigator": [result(), result()]}
        self.driver.run_until_stable()
        first = self._assignments()[0]
        self.assertEqual(first["outcome"], "VERIFIED_ACCEPTED")
        # Two sequences, two launches in all: A was never relaunched.
        self.assertEqual(len(self._assignments()), 2)
        self.assertEqual(len(self.transport.launch_log), launches + 1)

    def test_restart_after_verification_before_the_demonstration(self):
        """Authority accepted the work but the plane died before the
        demonstration was recorded -> restart records it exactly once
        from the ledger, and retention proceeds on it."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        restore = _fail_once(self.driver.taskforce.recorder,
                             "_record_demonstration")
        with self.assertRaises(_Boom):
            for _ in range(20):
                self.driver.step()
        restore()
        self.assertEqual(self._demonstrations(), [])
        self.assertEqual(list(self._states().values()), ["COMPLETED"])
        self._restart(TwoTaskPolicy("retain"), "chief-b",
                      runner=QueueRunner())
        self.driver.run_until_stable()
        demos = self._demonstrations()
        self.assertEqual(len(demos), 2)
        self.assertEqual(len({d["assignment_id"] for d in demos}), 2)
        self.assertEqual(self._seats()[0]["status"], "RETAINED")
        self.assertEqual(len(self._memberships()), 1)
        self.assertEqual(self._assignments()[1]["selection_path"],
                         "RETAINED_SEAT")

    def test_restart_with_the_retention_proposal_stored(self):
        """The Engineer decided to retain; the decision's reasoning is
        durable; the plane died before it was applied -> the restarted
        plane REPLAYS the recorded proposals (the policy is not asked
        again) and retains exactly once: one membership, one seat."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        restore = _fail_once(self.driver.taskforce.roster, "retain")
        with self.assertRaises(_Boom):
            for _ in range(20):
                self.driver.step()
        restore()
        stored = self._rows("SELECT * FROM proposals WHERE class ="
                            " 'RETAIN_SPECIALIST'")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "EVALUATED")
        self.assertEqual(self._seats(), [])
        self.assertEqual(self._memberships(), [])

        class Poisoned(TwoTaskPolicy):
            def plan(self, view):
                if view.taskforce["retention_candidates"]:
                    raise AssertionError(
                        "the interrupted batch must be replayed, not"
                        " reasoned again")
                return TwoTaskPolicy.plan(self, view)

        self._restart(Poisoned("retain"), "chief-b", runner=QueueRunner())
        first = self.driver.step()
        self.assertIsNotNone(first["planning"]["replayed_from"])
        self.driver.run_until_stable()
        decisions = self._rows("SELECT * FROM retention_decisions WHERE"
                               " kind = 'RETAIN'")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(self._seats()[0]["status"], "RETAINED")
        self.assertEqual(len(self._seats()), 1)
        self.assertEqual(len(self._memberships()), 1)
        self.assertEqual(len(self._rows(
            "SELECT * FROM proposals WHERE class = 'RETAIN_SPECIALIST'")),
            1)
        self.assertEqual(self._rows(
            "SELECT status FROM cycles WHERE status = 'OPEN'"), [])
        self.assertEqual(len(self._assignments()), 2)
        self.assertEqual(self._assignments()[1]["selection_path"],
                         "RETAINED_SEAT")

    def test_restart_with_a_retained_seat_before_its_second_assignment(
            self):
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        self._step_until(
            lambda: self._seats()
            and self._seats()[0]["status"] == "RETAINED"
            and len(self._states()) == 2)
        self.assertEqual(len(self._assignments()), 1)
        self._restart(TwoTaskPolicy("retain"), "chief-b",
                      runner=QueueRunner())
        self.driver.run_until_stable()
        first, second = self._assignments()
        self.assertIsNone(first["seat_id"])
        self.assertEqual(second["seat_id"], self._seats()[0]["seat_id"])
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["decided_by"], "TASKFORCE")
        self.assertEqual(len(self._seats()), 1)
        self.assertEqual(len(self._memberships()), 1)
        self.assertEqual(len(self._rows(
            "SELECT * FROM retention_decisions")), 1)
        self.assertEqual(set(self._states().values()), {"COMPLETED"})


# ================================================== authority separation

class EstablishedSeatPolicy(ChiefEngineerPolicy):
    """Two READ_ONLY builds (retaining after the first), then a
    SOURCE_MODIFYING build for the now-established member."""

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        sequences = view.authority["sequences"]
        proposals = [retain(view, oid, candidate) for candidate in
                     view.taskforce["retention_candidates"]]
        done = [s for s in sequences if s["state"] == "COMPLETED"]
        if len(sequences) == len(done) and len(sequences) < 2:
            proposals.append(investigate(
                view, oid, "RO-%d" % len(sequences),
                capability="software_implementation", role="builder"))
        elif len(sequences) == 2 and len(done) == 2:
            p = base_fields(view, oid)
            p.update({
                "class": "CREATE_TASK",
                "rationale": "the change itself",
                "requirement": {
                    "objective": "Land the change.",
                    "finalization_class": "SOURCE_MODIFYING",
                    "capability": "software_implementation",
                    "roles": roles("builder"),
                    "guidance": "Land the change."}})
            proposals.append(p)
        return proposals


class AuthoritySeparationTests(WorkforceTestCase):
    def test_retained_member_fails_verification_like_anyone(self):
        """A retained member's work is verified by the same registered
        path: when verification fails, the attempt is REJECTED, nothing
        is accepted, and the demonstration is NOT_QUALIFYING — whatever
        the specialist's own report claims. The Taskforce cannot certify
        its own member, and membership is not a verification bypass."""
        self.transport.scripts = {"investigator": [
            result(), result(artifact=b"ALL CHECKS PASSED. Ship it.\n")]}
        self._objective()
        # a2 passes for A, FAILS for the retained member's B.
        self.driver = self._driver(
            TwoTaskPolicy("retain"),
            runner=QueueRunner({"a2": ["PASS", "FAIL"]}))
        self.driver.run_until_stable()
        first, second = self._assignments()
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["outcome"], "VERIFICATION_FAILED")
        attempt = self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (second["attempt_id"],)).fetchone()
        self.assertEqual(attempt["state"], "REJECTED")
        b_state = self._states()[second["sequence_id"]]
        self.assertNotIn(b_state, ("ACCEPTED", "DEPENDENCIES_RELEASED",
                                   "COMPLETED"))
        demo = self._demonstrations()[1]
        self.assertEqual(demo["outcome"], "NOT_QUALIFYING")
        criteria = json.loads(demo["criteria"])
        self.assertFalse(criteria["verification_passed"])
        self.assertFalse(criteria["task_completed"])
        # The failure is on the member's record; membership did not hide
        # it and the seat did not turn it into an acceptance.
        view = self.driver.taskforce.roster.seat_view(self._seats()[0])
        self.assertEqual(view["performance"]["not_qualifying"], 1)
        self.assertEqual(view["performance"]["qualifying"], 1)
        self.assertEqual(len(self.rt.db.conn.execute(
            "SELECT 1 FROM released_edges WHERE predecessor_sequence_id"
            " = ?", (second["sequence_id"],)).fetchall()), 0)

    def test_established_seat_does_not_bypass_the_publication_permit(self):
        """Even an ESTABLISHED member's verified SOURCE_MODIFYING work
        stops at YOUR permit (cap-36): the ref does not move until you
        grant it."""
        self.pool = SpecialistPool()
        self.pool.register("software_implementation", "builder", "fake",
                           handle="alpha")
        effect = json.dumps({"target_ref": "refs/heads/main",
                             "expected_old": self.sha_old,
                             "new_value": self.sha_new}).encode("utf-8")
        self.transport.scripts = {"builder": [
            result(), result(), result(artifact=effect)]}
        self._objective()
        self.driver = self._driver(EstablishedSeatPolicy(),
                                   runner=QueueRunner(),
                                   established_threshold=2)
        self.driver.run_until_stable()
        seat = self._seats()[0]
        self.assertEqual(seat["status"], "ESTABLISHED")
        third = self._assignments()[2]
        self.assertEqual(third["standing_at_assignment"], "ESTABLISHED")
        self.assertEqual(third["decided_by"], "TASKFORCE")
        sm_id = third["sequence_id"]
        self.assertEqual(self._states()[sm_id], "VERIFIED")

        def head():
            return subprocess.run(
                ["git", "-C", str(self.repo), "rev-parse",
                 "refs/heads/main"], capture_output=True,
                text=True).stdout.strip()

        self.assertEqual(head(), self.sha_old)
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM effect_intents").fetchone()["n"], 0)
        self.rt.sequencer.grant_permit(sm_id, permitted_uses=1)
        self.driver.run_until_stable()
        self.assertEqual(self._states()[sm_id], "COMPLETED")
        self.assertEqual(head(), self.sha_new)

    def test_every_assignment_stands_on_a_frozen_contract(self):
        """Membership bypasses no TaskContractRecord: every assignment —
        outside or retained — names a sequence authority admitted, an
        attempt authority minted, and a manifest built from that ledger
        state."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        for assignment in self._assignments():
            sequence = self.rt.db.conn.execute(
                "SELECT s.record_hash, r.task_contract_id FROM sequences s"
                " JOIN records r ON r.record_hash = s.record_hash WHERE"
                " s.sequence_id = ?", (assignment["sequence_id"],)
            ).fetchone()
            self.assertIsNotNone(sequence)
            attempt = self.rt.db.conn.execute(
                "SELECT a.attempt_contract_hash, o.kind FROM attempts a"
                " JOIN objects o ON o.object_hash ="
                " a.attempt_contract_hash WHERE a.attempt_id = ?",
                (assignment["attempt_id"],)).fetchone()
            self.assertEqual(attempt["kind"], "AttemptContract")
            admission = self.rt.db.conn.execute(
                "SELECT submitted_by FROM admissions WHERE record_hash ="
                " ?", (sequence["record_hash"],)).fetchone()
            self.assertEqual(admission["submitted_by"], "autobot_service")
            receipt = self.rt.db.conn.execute(
                "SELECT completeness FROM attempt_receipts WHERE"
                " attempt_id = ? ORDER BY rowid DESC LIMIT 1",
                (assignment["attempt_id"],)).fetchone()
            self.assertEqual(receipt["completeness"], "COMPLETE")

    def test_the_policy_view_carries_workforce_state_and_no_handle(self):
        """The Engineer reasons over its project and the organization as
        plain data: the current project (workstreams, dependencies,
        assignments, team, what waits for a staffing decision), the
        Taskforce's members and retained seats, the outside specialists,
        demonstrations, workforce events and the objective budget — all
        JSON, with no handle anywhere."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        policy = TwoTaskPolicy("retain")
        self.driver = self._driver(policy, runner=QueueRunner())
        self.driver.run_until_stable()
        first = policy.views[0]
        self.assertEqual(first.taskforce["members"], [])
        self.assertEqual(first.taskforce["retained_seats"], [])
        self.assertEqual(
            sorted(s["handle"] for s in
                   first.taskforce["outside_specialists"]),
            ["alpha", "beta"])
        self.assertEqual(
            first.taskforce["available_capabilities"]["research"],
            {"retained_seats": 0, "retained_available": 0,
             "outside_specialists": 2})
        self.assertIsNone(first.projects[self.objective_id]["project_id"])
        # The work exists, is READY, and no retained seat can take it:
        # the view says it waits for the Project Manager, and names who
        # could be selected.
        waiting = [v for v in policy.views if v.projects[
            self.objective_id]["needs_staffing"]][0]
        need = waiting.projects[self.objective_id]["needs_staffing"][0]
        self.assertEqual((need["capability"], need["role"], need["cause"],
                          need["attempt_ordinal"]),
                         ("research", "investigator", "no_retained_seat",
                          1))
        self.assertEqual([c["handle"] for c in need["outside_candidates"]
                          if c["eligible"]], ["alpha", "beta"])
        deciding = [v for v in policy.views
                    if v.taskforce["retention_candidates"]][0]
        kinds = [e["event_kind"] for e in deciding.workforce_events]
        self.assertIn("demonstration_recorded", kinds)
        self.assertEqual(deciding.taskforce["members"], [])
        last = policy.views[-1]
        project = last.projects[self.objective_id]
        self.assertEqual(project["status"], "ACTIVE")
        self.assertEqual([w["state"] for w in project["workstreams"]],
                         ["COMPLETED", "COMPLETED"])
        self.assertEqual(
            [(a["selection_path"], a["decided_by"])
             for w in project["workstreams"] for a in w["assignments"]],
            [("OUTSIDE_SPECIALIST", "PROJECT_MANAGER"),
             ("RETAINED_SEAT", "TASKFORCE")])
        self.assertEqual(
            [(m["joined_as"], m["participation"], m["taskforce_member"])
             for m in project["team"]],
            [("OUTSIDE_SPECIALIST", "ACTIVE", True)])
        self.assertEqual(project["needs_staffing"], [])
        self.assertEqual(len(last.taskforce["members"]), 1)
        self.assertEqual(len(last.taskforce["retained_seats"]), 1)
        self.assertEqual(
            last.taskforce["available_capabilities"]["research"]
            ["retained_available"], 1)
        self.assertEqual(len(last.taskforce["recent_demonstrations"]), 2)
        self.assertEqual(
            last.budget[self.objective_id]["used"]["contracts"], 2)
        self.assertEqual(
            last.budget[self.objective_id]["used"]["retention_decisions"],
            1)
        workstreams = last.engineering[self.objective_id]["workstreams"]
        self.assertEqual([w["state"] for w in workstreams],
                         ["COMPLETED", "COMPLETED"])
        self.assertEqual(
            [w["staffing"][0]["selection_path"] for w in workstreams],
            ["OUTSIDE_SPECIALIST", "RETAINED_SEAT"])
        self.assertEqual(last.taskforce["retention_candidates"], [])
        for view in policy.views:
            for name in ("conn", "plan", "registry", "sequencer", "store",
                         "roster"):
                self.assertFalse(hasattr(view, name), name)
            json.dumps([view.taskforce, view.workforce_events,
                        view.budget, view.engineering, view.projects])


# ================================================= waiting + supervision

class AskingTransport(ScriptedTransport):
    """A provider whose running sessions raise permission requests."""

    def __init__(self):
        ScriptedTransport.__init__(self)
        self.requests = {}
        self.decisions = []

    def pending_requests(self, call_id):
        return [r for r in self.requests.get(call_id, [])
                if r["request_id"] not in
                {d["request_id"] for d in self.decisions}]

    def decide_request(self, call_id, request_id, decision, reason=""):
        self.decisions.append({"call_id": call_id,
                               "request_id": request_id,
                               "decision": decision, "reason": reason})


def _request(request_id, tool, workspace, **tool_input):
    return {"kind": "approval_required", "request_id": request_id,
            "workspace": str(workspace),
            "native_request": {"subtype": "can_use_tool",
                               "tool_name": tool, "input": tool_input}}


class WaitingLoopTests(WorkforceTestCase):
    def setUp(self):
        WorkforceTestCase.setUp(self)
        self.transport = AskingTransport()

    def test_the_loop_waits_for_specialists_and_answers_them(self):
        """A quiet step while a specialist is still executing is a WAIT:
        the production loop sleeps, looks again, answers the specialist's
        permission requests by the assignment policy, collects the result
        when it arrives, and only then reports a stable quiet state."""
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner(),
                                   gate_root=self.dir / "gate")
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            self.clock.advance(seconds)
            running = [a for a in self._assignments()
                       if a["status"] in ("DISPATCHED", "EXECUTING")]
            if len(sleeps) == 1 and running:
                call_id = running[0]["call_id"]
                self.transport.requests[call_id] = [
                    _request("approval_" + "1" * 32, "Read", self.repo,
                             file_path=str(self.repo / "README.md")),
                    _request("approval_" + "2" * 32, "Write", self.repo,
                             file_path=str(self.repo / "notes.txt"),
                             content="scratch"),
                    {"kind": "interaction_required",
                     "request_id": "approval_" + "3" * 32,
                     "workspace": str(self.repo), "native_request": {}},
                ]
            if len(sleeps) == 3:
                self.transport.scripts = {
                    "investigator": [result(), result()]}

        final = self.driver.run(max_wall_seconds=600, poll_seconds=5,
                                sleep=sleep)
        self.assertTrue(final["stable"])
        self.assertFalse(final["timed_out"])
        self.assertGreaterEqual(final["waits"], 3)
        self.assertEqual(final["in_flight"], [])
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        decided = {d["request_id"][-1]: d["decision"]
                   for d in self.transport.decisions}
        # The read is inside the environment; the write is not granted
        # (the frozen scope here is the chapter-1 tree); the native
        # question is never answered.
        self.assertEqual(decided, {"1": "allow", "2": "deny"})
        approvals = self._rows(
            "SELECT tool_name, decision, assignment_id FROM"
            " assignment_approvals ORDER BY request_id")
        self.assertEqual([(a["tool_name"], a["decision"])
                          for a in approvals],
                         [("Read", "allow"), ("Write", "deny")])
        first = self._assignments()[0]
        self.assertEqual({a["assignment_id"] for a in approvals},
                         {first["assignment_id"]})
        observations = json.loads(
            self._demonstrations()[0]["criteria"])["observations"]
        self.assertEqual((observations["requests_allowed"],
                          observations["requests_denied"]), (1, 1))

    def test_the_wall_bound_ends_a_wait_and_a_later_run_resumes(self):
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())

        def sleep(seconds):
            self.clock.advance(seconds)

        waiting = self.driver.run(max_wall_seconds=20, poll_seconds=5,
                                  sleep=sleep)
        self.assertFalse(waiting["stable"])
        self.assertTrue(waiting["timed_out"])
        self.assertEqual(len(waiting["in_flight"]), 1)
        self.assertEqual(len(self._assignments()), 1)
        self.transport.scripts = {"investigator": [result(), result()]}
        final = self.driver.run(max_wall_seconds=600, poll_seconds=5,
                                sleep=sleep)
        self.assertTrue(final["stable"])
        self.assertEqual(len(self.transport.launch_log), 2)  # A once, B
        self.assertEqual(set(self._states().values()), {"COMPLETED"})

    def test_a_long_wait_keeps_the_authority_lease(self):
        """A specialist that takes longer than the Sequencer's instance
        lease does not cost the loop its authority: the lease is renewed
        when less than half of it remains — a few journaled renewals, not
        one per look."""
        self.rt.sequencer.acquire_lease(ttl_seconds=100)
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner(), lease_seconds=100)
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            self.clock.advance(seconds)
            if len(sleeps) == 6:  # 180 s: well past the 100 s lease
                self.transport.scripts = {
                    "investigator": [result(), result()]}

        final = self.driver.run(max_wall_seconds=3600, poll_seconds=30,
                                sleep=sleep)
        self.assertTrue(final["stable"])
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        renewals = [1 for row in self.rt.db.conn.execute(
            "SELECT payload FROM transition_journal").fetchall()
            if json.loads(row["payload"]).get("rule") == "ch2:lease-renew"]
        self.assertGreaterEqual(len(renewals), 2)
        self.assertLessEqual(len(renewals), 6)

    def test_run_until_stable_reports_what_is_in_flight(self):
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        report = self.driver.run_until_stable()
        self.assertEqual(len(report["in_flight"]), 1)
        self.assertEqual(self._assignments()[0]["status"], "EXECUTING")


# ============================================================== budget

class ManyTasksPolicy(ChiefEngineerPolicy):
    def __init__(self, names):
        self.names = names

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        existing = len(view.authority["sequences"])
        return [investigate(view, oid, name)
                for name in self.names[existing:]]


class BudgetTests(WorkforceTestCase):
    def test_contract_budget_refuses_and_a_grant_reopens(self):
        """The objective budget is enforced at the planning boundary: a
        contract past the limit is recorded REFUSED with the reason and
        the planner is told; once you raise the limit the same
        engineering intent is admitted."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        now = instant(self.clock.now())
        self.plan.set_budget(self.objective_id, now, max_contracts=1)
        self.driver = self._driver(ManyTasksPolicy(["one", "two"]),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        self.assertEqual(len(self._states()), 1)
        refused = self._rows(
            "SELECT * FROM proposals WHERE status = 'REJECTED'")
        self.assertEqual(len(refused), 1)
        self.assertTrue(refused[0]["note"].startswith("budget: "))
        self.assertIn("1 of 1 contracts", refused[0]["note"])
        events = self._rows("SELECT * FROM workforce_events WHERE"
                            " event_kind = 'budget_exhausted'")
        self.assertEqual(len(events), 1)
        self.assertEqual(self.plan.budget_usage(self.objective_id)
                         ["contracts"], 1)

        self.plan.set_budget(self.objective_id, now, max_contracts=2)
        with self.plan.transaction() as cur:
            self.plan.emit_workforce_event(
                cur, "test:budget-granted", "budget_granted",
                self.objective_id, None, now)
        self.driver.run_until_stable()
        self.assertEqual(len(self._states()), 2)
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        self.assertEqual(self._rows(
            "SELECT * FROM proposals WHERE status = 'REJECTED'"), [])

    def test_attempt_and_outside_budgets_stop_staffing_visibly(self):
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        now = instant(self.clock.now())
        self.plan.set_budget(self.objective_id, now, max_attempts=1)
        self.driver = self._driver(ManyTasksPolicy(["one", "two"]),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        self.assertEqual(len(self._assignments()), 1)
        self.assertEqual(sorted(self._states().values()),
                         ["COMPLETED", "READY"])
        kinds = [e["event_kind"] for e in self._rows(
            "SELECT event_kind FROM workforce_events WHERE event_kind ="
            " 'budget_exhausted'")]
        self.assertEqual(kinds, ["budget_exhausted"])
        self.plan.set_budget(self.objective_id, now, max_attempts=2,
                             max_outside_specialists=1)
        self.driver.run_until_stable()
        # Attempts now allow it, but the second task would need a second
        # OUTSIDE specialist and that budget is spent.
        self.assertEqual(len(self._assignments()), 1)
        self.plan.set_budget(self.objective_id, now, max_attempts=2)
        self.driver.run_until_stable()
        self.assertEqual(len(self._assignments()), 2)
        self.assertEqual(set(self._states().values()), {"COMPLETED"})


# ======================================================= frozen prompts

class EchoTransport(ScriptedTransport):
    """A provider that records the prompt it was launched on and reports
    it back in its own task record, as AutoSwarm's task.json does."""

    def __init__(self, store):
        ScriptedTransport.__init__(self)
        self.store = store
        self.prompts = {}

    def launch_manifest(self, manifest_address, manifest, *,
                        manifest_bytes=None):
        projection = json.loads(self.store.get(
            manifest["projection_address"]))
        self.prompts[manifest["call_id"]] = \
            projection["engineering_context"]["guidance"]
        return ScriptedTransport.launch_manifest(
            self, manifest_address, manifest,
            manifest_bytes=manifest_bytes)

    def collect(self, call_id):
        scripted = ScriptedTransport.collect(self, call_id)
        if scripted is None:
            return None
        scripted = dict(scripted)
        scripted["witness_files"] = [
            ("obj-11", "autoswarm", json.dumps(
                {"prompt": self.prompts[call_id]},
                sort_keys=True).encode("utf-8"))] + [
            w for w in WITNESSES if w[0] != "obj-11"]
        return scripted


class FrozenPromptTests(WorkforceTestCase):
    def setUp(self):
        WorkforceTestCase.setUp(self)
        self.transport = EchoTransport(self.rt.store)

    def test_the_specialist_is_launched_on_the_frozen_prompt(self):
        """With prompt freezing on, the composed specialist prompt is a
        declared specification input of the frozen record, the specialist
        is launched on exactly those bytes, and the production
        role-transform check (INV-49) passes on them — for the first
        attempt AND for the repair, which executes the same frozen
        requirement."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective("Audit the roster.")
        # The PRODUCTION verifier runner: the role-transform check really
        # executes the registered transform over the declared input and
        # compares it with what the provider says it received.
        self.driver = self._driver(RepairStaffingPolicy("same_member"),
                                   runner=None, freeze_prompts=True)
        self.driver.run_until_stable()
        states = self._states()
        self.assertEqual(set(states.values()), {"COMPLETED"})
        sequence_id = list(states)[0]
        record_hash = self.rt.db.conn.execute(
            "SELECT record_hash FROM sequences WHERE sequence_id = ?",
            (sequence_id,)).fetchone()["record_hash"]
        address = self.rt.db.conn.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (record_hash,)).fetchone()["content_address"]
        record = json.loads(self.rt.store.get(address))
        inputs = record["specification_inputs"][
            "name_to_exact_byte_sha256"]
        self.assertEqual(list(inputs), [prompts.SPECIALIST_PROMPT_INPUT])
        frozen = self.rt.store.get(
            inputs[prompts.SPECIALIST_PROMPT_INPUT]).decode("utf-8")
        launched = list(self.transport.prompts.values())
        self.assertEqual(launched, [frozen])
        self.assertTrue(frozen.startswith("Investigate R only."))
        self.assertIn("Project objective: Audit the roster.", frozen)
        # This environment's contracts require command governance, so the
        # composed prompt carries the governed-shell note.
        self.assertIn("COMMAND GOVERNANCE", frozen)
        self.assertIn(prompts.FINDINGS_BEGIN, frozen)
        check = self.rt.db.conn.execute(
            "SELECT outcome FROM attempt_verifications WHERE assertion_id"
            " LIKE 'role-transform/%'").fetchone()
        self.assertEqual(check["outcome"], "PASS")

    def test_a_repair_executes_the_same_frozen_prompt(self):
        """A repair attempt is another attempt of the SAME frozen
        requirement: the planner's repair note is its rationale, and the
        specialist is launched on the identical frozen prompt (the
        admission seam re-derives the observed prompt identity from the
        provider's own task record, so anything else would fail
        INV-49)."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective("Audit the roster.")
        self.driver = self._driver(RepairStaffingPolicy("same_member"),
                                   runner=QueueRunner({"a2": ["FAIL"]}),
                                   freeze_prompts=True)
        self.driver.run_until_stable()
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        launched = list(self.transport.prompts.values())
        self.assertEqual(len(launched), 2)
        self.assertEqual(launched[0], launched[1])
        self.assertNotIn("Repair the failed assertion", launched[1])
        checks = [r["outcome"] for r in self.rt.db.conn.execute(
            "SELECT outcome FROM attempt_verifications WHERE assertion_id"
            " LIKE 'role-transform/%' ORDER BY rowid").fetchall()]
        self.assertEqual(checks, ["PASS", "PASS"])


# ============================================ isolation and independence

class ReviewPolicy(ChiefEngineerPolicy):
    """A, then an independent review of A's accepted record."""

    def plan(self, view):
        if not view.objectives:
            return []
        oid = view.objectives[0]["objective_id"]
        sequences = view.authority["sequences"]
        if not sequences:
            return [investigate(view, oid, "A")]
        if len(sequences) == 1 and sequences[0]["state"] == "COMPLETED":
            p = base_fields(view, oid)
            p.update({
                "class": "REQUEST_REVIEW",
                "rationale": "the investigation needs an independent"
                             " review",
                "review_of": {"record_hash": sequences[0]["record_hash"]},
                "requirement": {
                    "objective": "Review investigation A.",
                    "capability": "verification",
                    "roles": roles("reviewer"),
                    "guidance": "Review the accepted investigation."}})
            return [p]
        return []


class ReleasingTransport(ScriptedTransport):
    def __init__(self):
        ScriptedTransport.__init__(self)
        self.released = []

    def release_workspace(self, call_id):
        self.released.append(call_id)
        return True


class IsolationAndIndependenceTests(WorkforceTestCase):
    def test_a_review_is_staffed_independently_of_its_subject(self):
        """alpha did the work under review; alpha also offers review and
        comes first in the catalog. The review still goes to beta: the
        author of a result does not review it."""
        self.pool = SpecialistPool()
        self.pool.register("research", "investigator", "fake",
                           handle="alpha")
        self.pool.register("verification", "reviewer", "fake",
                           handle="alpha")
        self.pool.register("verification", "reviewer", "fake",
                           handle="beta")
        self.transport.scripts = {"investigator": [result()],
                                  "reviewer": [result()]}
        self._objective()
        self.driver = self._driver(ReviewPolicy(), runner=QueueRunner())
        self.driver.run_until_stable()
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        work, review = self._assignments()
        self.assertEqual(self._handle(work["specialist_id"]), "alpha")
        self.assertEqual(self._handle(review["specialist_id"]), "beta")
        reason = json.loads(review["selected_reason"])
        self.assertEqual(reason["independent_of"],
                         [work["specialist_id"]])
        # The candidates on record show WHY: the author was prohibited.
        candidates = {c["handle"]: c for c in reason["outside_candidates"]}
        self.assertTrue(candidates["alpha"]["prohibited"])
        self.assertFalse(candidates["alpha"]["eligible"])
        self.assertTrue(candidates["beta"]["eligible"])

    def test_the_project_manager_may_not_assign_the_author_to_its_review(
            self):
        """An explicit selection is bound by independence too: the
        Project Manager naming the author for the review of its own work
        is REFUSED with the reason, and told so; selecting someone else
        then staffs the review."""
        alpha = specialist_identity("alpha")
        attempts = []

        class InsistentPolicy(ReviewPolicy):
            def plan(self, view):
                proposals = ReviewPolicy.plan(self, view)
                if not view.objectives:
                    return proposals
                oid = view.objectives[0]["objective_id"]
                for need in view.projects[oid]["needs_staffing"]:
                    if need["capability"] != "verification":
                        continue
                    handle = "alpha" if not attempts else "beta"
                    attempts.append(handle)
                    proposals.append(assign(view, oid, need["sequence_id"],
                                            handle))
                return proposals

        self.pool = SpecialistPool()
        self.pool.register("research", "investigator", "fake",
                           handle="alpha")
        self.pool.register("verification", "reviewer", "fake",
                           handle="alpha")
        self.pool.register("verification", "reviewer", "fake",
                           handle="beta")
        self.transport.scripts = {"investigator": [result()],
                                  "reviewer": [result()]}
        self._objective()
        self.driver = self._driver(InsistentPolicy(), runner=QueueRunner())
        self.driver.run_until_stable()
        self.assertEqual(attempts, ["alpha", "beta"])
        refused = self._rows(
            "SELECT * FROM proposals WHERE class = 'ASSIGN_SPECIALIST' AND"
            " status = 'REJECTED'")
        self.assertEqual(len(refused), 1)
        self.assertEqual(json.loads(refused[0]["body"])["staffing"]
                         ["specialist_id"], alpha)
        self.assertIn("independently of its subject", refused[0]["note"])
        events = self._rows("SELECT * FROM workforce_events WHERE"
                            " event_kind = 'assignment_refused'")
        self.assertEqual(len(events), 1)
        work, review = self._assignments()
        self.assertEqual(self._handle(review["specialist_id"]), "beta")
        self.assertEqual(set(self._states().values()), {"COMPLETED"})

    def test_independence_is_not_traded_for_availability(self):
        """Only the author offers review: the review WAITS, visibly. It
        is never handed back to the author — not by the Taskforce and not
        by the standing project-manager rule, which finds nobody
        eligible."""
        self.pool = SpecialistPool()
        self.pool.register("research", "investigator", "fake",
                           handle="alpha")
        self.pool.register("verification", "reviewer", "fake",
                           handle="alpha")
        self.transport.scripts = {"investigator": [result()],
                                  "reviewer": [result()]}
        self._objective()
        self.driver = self._driver(ReviewPolicy(), runner=QueueRunner())
        self.driver.run_until_stable()
        self.assertEqual(len(self._assignments()), 1)
        self.assertEqual(sorted(self._states().values()),
                         ["COMPLETED", "READY"])
        waits = self._rows("SELECT facts FROM workforce_events WHERE"
                           " event_kind = 'staffing_unavailable'")
        self.assertEqual(len(waits), 1)
        facts = json.loads(waits[0]["facts"])
        self.assertEqual(facts["independent_of"],
                         [specialist_identity("alpha")])
        self.assertEqual(facts["cause"], "no_retained_seat")
        self.assertEqual([c["eligible"] for c in
                          facts["outside_candidates"]], [False])
        # Exactly one staffing decision exists — the investigation's. The
        # standing rule proposed nobody for the review, not even to be
        # refused.
        decisions = self._rows(
            "SELECT status FROM proposals WHERE class ="
            " 'ASSIGN_SPECIALIST'")
        self.assertEqual([d["status"] for d in decisions], ["APPLIED"])

    def test_read_only_work_reads_a_frozen_revision_when_isolated(self):
        """With isolation on, a READ_ONLY specialist's manifest carries
        the canonical ref's value at dispatch — the revision authority
        has accepted so far — exactly as a SOURCE_MODIFYING one does.
        Without it the manifest carries none."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner(),
                                   isolate_read_only=True)
        self.driver.run_until_stable()
        for launch in self.transport.launch_log:
            manifest = json.loads(self.rt.store.get(
                launch["manifest_address"]))
            self.assertEqual(manifest["constraints"]["finalization_class"],
                             "READ_ONLY")
            self.assertEqual(manifest["constraints"]["source_revision"],
                             self.sha_old)

    def test_shared_checkout_is_the_default_for_read_only(self):
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        self.driver.step()
        self.driver.step()
        manifest = json.loads(self.rt.store.get(
            self.transport.launch_log[0]["manifest_address"]))
        self.assertNotIn("source_revision", manifest["constraints"])

    def test_a_settled_assignments_workspace_is_given_back(self):
        self.transport = ReleasingTransport()
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        calls = [a["call_id"] for a in self._assignments()]
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.transport.released, calls)


# ========================================================== query layer

class QueryLayerTests(WorkforceTestCase):
    def test_engineering_information_is_queryable(self):
        self.transport.scripts = {"investigator": [
            result(findings=[{
                "finding_class": "documentation_mismatch",
                "affected_component": "pilot.subsystem",
                "statement": "The document omits one state.",
                "evidence_refs": ["docs/x.md:3"], "significance": "low",
                "confidence": "high",
                "relation_to_requirement": "within"}]),
            result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        ro = self.driver.ro
        alpha = specialist_identity("alpha")
        first, second = self._assignments()
        seat_id = self._seats()[0]["seat_id"]
        project_id = self._projects()[0]["project_id"]

        state = query.objective_state(self.plan, ro, self.objective_id)
        self.assertEqual(state["status"], "COMPLETE")
        self.assertEqual(state["task_states"], {"COMPLETED": 2})
        self.assertEqual(len(state["accepted_results"]), 2)
        # Every decision, with the role the Engineer made it in.
        self.assertEqual(
            [(d["class"], d["acting_as"]) for d in state["decisions"]],
            [("INVESTIGATE", "ENGINEER"),
             ("ASSIGN_SPECIALIST", "PROJECT_MANAGER"),
             ("RETAIN_SPECIALIST", "PROJECT_MANAGER"),
             ("INVESTIGATE", "ENGINEER"),
             ("PROJECT_COMPLETE", "PROJECT_MANAGER")])
        # The project: temporary state, kept apart from the Taskforce's.
        project = query.project_state(self.plan, ro, project_id)
        self.assertEqual(state["projects"], [project])
        self.assertEqual(project["status"], "COMPLETED")
        self.assertEqual([w["state"] for w in project["workstreams"]],
                         ["COMPLETED", "COMPLETED"])
        self.assertEqual(project["active_team"], [])
        self.assertEqual(project["open_assignments"], [])
        self.assertEqual(
            [(m["specialist_id"], m["participation"], m["end_reason"],
              m["taskforce_member"]) for m in project["team"]],
            [(alpha, "RELEASED", "project_completed", True)])
        self.assertEqual(query.projects_report(self.plan, ro), [project])
        self.assertEqual(len(state["findings"]), 1)
        self.assertEqual(state["findings"][0]["specialist_id"], alpha)
        self.assertEqual(state["budget"]["used"]["attempts"], 2)

        # Which specialists have demonstrated research?
        capability = query.by_capability(self.plan, "research")
        self.assertEqual(capability["demonstrated_by"], [alpha])
        self.assertEqual([s["handle"] for s in capability["retained"]],
                         ["alpha"])
        # What evidence justified retaining this specialist?
        why = query.why_retained(self.plan, seat_id)
        self.assertEqual(why["membership_status"], "ACTIVE")
        retained = [d for d in why["decisions"] if d["kind"] == "RETAIN"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(why["membership_joined_by_decision"],
                         retained[0]["decision_id"])
        self.assertEqual(
            retained[0]["demonstrations"][0]["sequence_id"],
            first["sequence_id"])
        self.assertTrue(
            retained[0]["demonstrations"][0]["verification_refs"])
        # Why was this specialist selected?
        selected = query.why_selected(self.plan, second["assignment_id"])
        self.assertEqual(selected["selected_reason"]["path"],
                         "RETAINED_SEAT")
        self.assertEqual(selected["decided_by"], "TASKFORCE")
        outside = query.why_selected(self.plan, first["assignment_id"])
        self.assertEqual(outside["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(outside["assignment_decision"],
                         first["assignment_decision"])
        self.assertIn("standing project-manager rule",
                      outside["assignment_decision_rationale"])
        # Why does this task exist?
        created = query.why_created(self.plan, second["sequence_id"])
        self.assertEqual(created[0]["class"], "INVESTIGATE")
        self.assertIsNotNone(created[0]["snapshot"]["entries"])
        self.assertEqual(created[0]["policy_id"], "TwoTaskPolicy")

        specialist = query.by_specialist(self.plan, alpha)
        self.assertEqual(len(specialist["assignments"]), 2)
        self.assertEqual(len(specialist["demonstrations"]), 2)
        self.assertEqual(len(specialist["findings"]), 1)
        self.assertTrue(specialist["taskforce_member"])
        self.assertEqual([p["project_id"] for p in specialist["projects"]],
                         [project_id])
        seat = query.by_seat(self.plan, seat_id)
        self.assertEqual(seat["status"], "RETAINED")
        self.assertEqual(seat["membership"]["status"], "ACTIVE")
        self.assertEqual(seat["demonstrated_environments"],
                         ["autobot/READ_ONLY"])
        self.assertEqual(len(seat["history"]), 1)
        self.assertEqual(len(seat["demonstrations"]), 2)
        sequence = query.by_sequence(self.plan, ro, first["sequence_id"])
        self.assertEqual(sequence["state"], "COMPLETED")
        self.assertEqual(len(sequence["findings"]), 1)
        attempt = query.by_attempt(self.plan, ro, first["attempt_id"])
        self.assertEqual(attempt["assignment"]["specialist_id"], alpha)
        self.assertTrue(attempt["verifications"])
        passed = query.by_verification_outcome(self.plan, ro, "PASS")
        self.assertEqual({p["specialist_id"] for p in passed}, {alpha})
        self.assertEqual(
            query.by_verification_outcome(self.plan, ro, "FAIL"), [])
        subsystem = query.by_subsystem(self.plan, ro, "pilot.subsystem")
        self.assertEqual(len(subsystem["findings"]), 1)

        report = query.taskforce_report(self.plan)
        self.assertEqual([m["specialist_id"] for m in report["members"]],
                         [alpha])
        self.assertEqual(len(report["retained_members"]), 1)
        self.assertEqual(len(report["assignment_history"]), 2)
        self.assertEqual(report["why_retained"][0]["seat_id"], seat_id)
        self.assertEqual(
            [s["handle"] for s in report["outside_specialists"]],
            ["beta"])
        self.assertEqual(report["retention_candidates"], [])
        self.assertEqual([d["kind"] for d in report["workforce_decisions"]],
                         ["RETAIN"])
        # The persistent report holds no project state.
        self.assertNotIn("projects", report)
        json.dumps([state, capability, why, specialist, seat, sequence,
                    attempt, passed, subsystem, report, project],
                   default=str)


class CliReportingTests(WorkforceTestCase):
    """The reporting commands read the stores directly: no lease, no
    driver, safe beside a running organization."""

    def _cli(self, command, **namespace):
        import argparse
        import contextlib
        import io
        from intelligence import cli
        out, err = io.StringIO(), io.StringIO()
        args = argparse.Namespace(root=self.dir, **namespace)
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = getattr(cli, command)(args)
        return code, out.getvalue(), err.getvalue()

    def test_reports_and_queries_from_the_command_line(self):
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("retain"),
                                   runner=QueueRunner())
        self.driver.run_until_stable()
        first = self._assignments()[0]
        seat_id = self._seats()[0]["seat_id"]
        project_id = self._projects()[0]["project_id"]

        code, out, _err = self._cli("cmd_taskforce", json=True)
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(len(report["retained_members"]), 1)
        self.assertEqual(report["why_retained"][0]["seat_id"], seat_id)
        code, out, _err = self._cli("cmd_taskforce", json=False)
        self.assertIn("Taskforce: 1 member(s) holding 1 retained seat(s)",
                      out)
        self.assertIn("why each retained member holds its seat", out)
        self.assertIn("RETAINED_SEAT", out)
        self.assertIn("decided by PROJECT_MANAGER", out)
        self.assertIn("projects and their teams", out)
        self.assertIn("joined as OUTSIDE_SPECIALIST, participation RELEASED"
                      " (project_completed); Taskforce member now: yes",
                      out)

        for kind, value, probe in (
                ("capability", "research", "demonstrated_by"),
                ("project", project_id, "team"),
                ("seat", seat_id, "history"),
                ("specialist", first["specialist_id"], "demonstrations"),
                ("sequence", first["sequence_id"], "assignments"),
                ("attempt", first["attempt_id"], "verifications"),
                ("objective", self.objective_id, "accepted_results"),
                ("graph", self.objective_id, "edges"),
                ("why-selected", first["assignment_id"],
                 "selected_reason"),
                ("why-retained", seat_id, "decisions"),
                ("taskforce", None, "assignment_history")):
            code, out, _err = self._cli("cmd_query", kind=kind,
                                        value=value)
            self.assertEqual(code, 0, kind)
            self.assertIn(probe, json.loads(out), kind)
        for kind, value in (("dependencies", self.objective_id),
                            ("projects", None),
                            ("accepted", self.objective_id),
                            ("verification-outcome", "PASS"),
                            ("why-created", first["sequence_id"])):
            code, out, _err = self._cli("cmd_query", kind=kind,
                                        value=value)
            self.assertEqual(code, 0, kind)
            self.assertIsInstance(json.loads(out), list, kind)
        code, _out, _err = self._cli("cmd_query", kind="seat",
                                     value="ts_nope")
        self.assertEqual(code, 1)

        code, out, _err = self._cli("cmd_status")
        self.assertEqual(code, 0)
        self.assertIn("no active objectives", out)
        self.assertIn("completed sequences: 2", out)
        code, out, _err = self._cli("cmd_objectives", close=None)
        self.assertIn("COMPLETE", out)
        self.assertIn(self.objective_id, out)

    def test_an_objective_closes_only_when_its_work_is_completed(self):
        self.transport.scripts = {"investigator": []}  # A keeps running
        self._objective()
        self.driver = self._driver(TwoTaskPolicy("none"),
                                   runner=QueueRunner())
        for _ in range(6):
            self.driver.step()
        self.driver.close()
        self.driver = None
        code, _out, err = self._cli("cmd_objectives",
                                    close=self.objective_id)
        self.assertEqual(code, 1)
        self.assertIn("are not COMPLETED", err)
        self.assertEqual(self.plan.objective(self.objective_id)["status"],
                         "ACTIVE")
        code, _out, err = self._cli("cmd_objectives", close="obj-nope")
        self.assertEqual(code, 1)
        # An objective with nothing outstanding is yours to close.
        self.plan.add_objective("obj-idle", "Nothing was ever needed.",
                                "you", instant(self.clock.now()))
        code, out, _err = self._cli("cmd_objectives", close="obj-idle")
        self.assertEqual(code, 0)
        self.assertIn("closed: obj-idle", out)
        self.assertEqual(self.plan.objective("obj-idle")["status"],
                         "COMPLETE")


if __name__ == "__main__":
    unittest.main()
