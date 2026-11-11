"""The intelligence-plane conformance suite (the CE phase).

Pins the boundary and the mechanics: the proposal compiler emits
canonical, admissible candidates whose identity converges at the
Registry; planning events derive exactly once per durable transition;
cycles wake only on news and never re-propose after restart; the policy
seam receives plain data and no authority handle; the plane's authority
reads are physically read-only; findings are information, never work;
provider identity stays out of contract identity; one driver reasons at
a time; and the expanded PromptProjection carries the approved context
bundle reproducibly.
"""

import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import support
from autobot import project_state
from autobot.models import ConflictError, instant
from autobot.projection import project_prompt
from intelligence import (
    AgentFactory,
    ChiefEngineerPolicy,
    ChiefEngineerService,
    PlanStore,
    PlanningView,
    admit_finding,
    compile_proposal,
    proposal_identity,
    pump_authority_events,
    readonly_view,
    validate_proposal,
)
from intelligence.chief import SUBMITTING_PRINCIPAL, PlanningBudget
from intelligence.planstore import DriverBusy
from intelligence.proposals import ProposalError
from intelligence.roles import AgentFactoryError

GUIDANCE = "Work strictly inside the frozen requirement's scope."


def requirement(objective_text, fin="SOURCE_MODIFYING",
                capability="software_implementation", roles=None, **extra):
    req = {"objective": objective_text,
           "finalization_class": fin,
           "capability": capability,
           "roles": roles or {"builder": {"required": True,
                                          "success_outcomes": ["success"]}},
           "guidance": GUIDANCE}
    req.update(extra)
    return req


def make_proposal(cls="CREATE_TASK", objective_id="obj-1", req=None,
                  **extra):
    proposal = {"class": cls, "objective_id": objective_id,
                "rationale": "the objective needs this step next",
                "snapshot": {"entries": 0, "head_hash": None},
                "provenance": {"findings": [], "events": []}}
    if req is not None:
        proposal["requirement"] = req
    proposal.update(extra)
    return proposal


class ListPolicy(ChiefEngineerPolicy):
    """Returns a scripted list once, then nothing; records its views."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.views = []

    def plan(self, view):
        self.views.append(view)
        if self.batches:
            return self.batches.pop(0)
        return []


class IntelligenceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)
        self.env = support.intelligence_environment()
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.ro = readonly_view(self.dir / "autobot.db")

    def tearDown(self):
        self.ro.close()
        self.plan.close()
        self.rt.db.close()
        self.tmp.cleanup()

    def _now(self):
        return instant(self.clock.now())

    def _service(self, policy, **kw):
        return ChiefEngineerService(self.plan, self.rt.registry, self.env,
                                    self.clock, policy, **kw)

    def _sequence_count(self):
        return self.ro.execute(
            "SELECT COUNT(*) AS n FROM sequences").fetchone()["n"]


class CompilerTests(IntelligenceTestCase):
    def test_candidates_admit_and_identity_converges(self):
        """A compiled candidate freezes through the real Registry; a
        re-derived candidate differs only in record-tier values, so its
        contract identity is THE SAME and resubmission converges as the
        designed DUPLICATE (obj-32). Provider identity never appears in
        the payload."""
        p = make_proposal(req=requirement("Build the pilot subsystem."))
        candidate = compile_proposal(p, self.env, self.clock)
        self.assertNotIn("provider", json.dumps(candidate))
        self.assertNotIn("model", json.dumps(candidate))
        outcome = self.rt.registry.admit_payload(
            candidate, submitted_by="autobot_service")
        self.assertEqual(outcome["outcome"], "NEW_RECORD")
        again = compile_proposal(p, self.env, self.clock)
        self.assertNotEqual(again["task_contract_id"],
                            candidate["task_contract_id"])
        dup = self.rt.registry.admit_payload(
            again, submitted_by="autobot_service")
        self.assertEqual(dup["outcome"], "DUPLICATE")
        self.assertEqual(
            proposal_identity(p, outcome["contract_digest"]),
            "prop:" + outcome["contract_digest"])

    def test_read_only_shape_per_d54(self):
        """INVESTIGATE compiles READ_ONLY: no target_ref, SEM-05 derives
        [] requirements, and the delegation's RO floor is empty."""
        p = make_proposal(cls="INVESTIGATE",
                          req=requirement("Investigate the subsystem.",
                                          fin=None, capability="research"))
        del p["requirement"]["finalization_class"]
        candidate = compile_proposal(p, self.env, self.clock)
        self.assertEqual(candidate["finalization_class"], "READ_ONLY")
        self.assertNotIn("target_ref", candidate["source_domain"])
        self.assertEqual(candidate["finalization_requirements"], [])
        self.assertEqual(
            candidate["resolved_bindings"]["mandatory_floor"]
            ["mandatory_assertion_ids"], [])
        outcome = self.rt.registry.admit_payload(
            candidate, submitted_by="autobot_service")
        self.assertEqual(outcome["outcome"], "NEW_RECORD")

    def test_out_of_scope_queues_for_you(self):
        """A proposal whose scope exceeds the delegation is not an
        error and not authority: it QUEUES FOR YOU (SEM-11/t-c04), and
        the proposal row shows the operator boundary."""
        req = requirement("Touch the whole tree.")
        req["allowed_scope"] = {"paths": ["somewhere/else"],
                                "artifact_paths": []}
        self.plan.add_objective("obj-1", "pilot", "you", self._now())
        policy = ListPolicy([[make_proposal(req=req)]])
        svc = self._service(policy)
        report = svc.run_cycle(self.ro, project_state, self._now())
        self.assertEqual(report["proposals"][0]["status"], "QUEUED_FOR_YOU")
        row = self.plan.proposal(report["proposals"][0]["proposal_id"])
        self.assertEqual(row["status"], "QUEUED_FOR_YOU")
        self.assertEqual(self._sequence_count(), 0)

    def test_decompose_forward_dependency_refused(self):
        part = {"part_id": "b", "requirement": requirement("Part B."),
                "depends_on_parts": ["c"]}
        proposal = make_proposal(cls="DECOMPOSE_TASK", parts=[part])
        with self.assertRaises(ProposalError):
            validate_proposal(proposal)
        with self.assertRaises(ProposalError):
            proposal_identity(make_proposal(req=requirement("x.")))


class EventTests(IntelligenceTestCase):
    def test_one_transition_one_event_across_restart(self):
        """One durable transition yields exactly one planning event,
        across repeated pumps AND a plan-store restart (journal-seq
        identity plus the watermark)."""
        pump_authority_events(self.ro, self.plan)  # drain the seed graph
        candidate = compile_proposal(
            make_proposal(req=requirement("Event source.")),
            self.env, self.clock)
        admitted = self.rt.registry.admit_payload(
            candidate, submitted_by="autobot_service")
        self.rt.sequencer.mark_ready(admitted["sequence_id"])
        first = pump_authority_events(self.ro, self.plan)
        self.assertEqual(first["events"], 2)  # contract_admitted, work_ready
        second = pump_authority_events(self.ro, self.plan)
        self.assertEqual(second["events"], 0)
        self.plan.close()
        self.plan = PlanStore(self.dir / "intelligence.db")
        third = pump_authority_events(self.ro, self.plan)
        self.assertEqual(third["events"], 0)
        kinds = [e["event_kind"] for e in self.plan.unconsumed_events()]
        self.assertEqual(kinds[-2:], ["contract_admitted", "work_ready"])

    def test_cycle_wakes_only_on_news(self):
        policy = ListPolicy([[], []])
        svc = self._service(policy)
        self.plan.add_objective("obj-1", "pilot", "you", self._now())
        report = svc.run_cycle(self.ro, project_state, self._now())
        self.assertIsNotNone(report["cycle_id"])
        view = policy.views[0]
        self.assertIsInstance(view, PlanningView)
        self.assertFalse(hasattr(view, "conn"))
        self.assertFalse(hasattr(view, "registry"))
        quiet = svc.run_cycle(self.ro, project_state, self._now())
        self.assertIsNone(quiet["cycle_id"])


class ProposalLifecycleTests(IntelligenceTestCase):
    def test_restart_does_not_duplicate_proposals(self):
        """The same engineering intent re-derived by a fresh service
        after restart converges on the admitted proposal row: the ledger
        sees ONE admission."""
        proposal = make_proposal(req=requirement("Build once."))
        self.plan.add_objective("obj-1", "pilot", "you", self._now())
        svc = self._service(ListPolicy([[proposal]]))
        report = svc.run_cycle(self.ro, project_state, self._now())
        self.assertEqual(report["proposals"][0]["status"], "ADMITTED")
        self.assertEqual(self._sequence_count(), 1)
        admissions = self.ro.execute(
            "SELECT COUNT(*) AS n FROM admissions").fetchone()["n"]

        self.plan.close()
        self.plan = PlanStore(self.dir / "intelligence.db")
        svc2 = self._service(ListPolicy([[proposal]]))
        # Wake the planner with an intelligence-plane event.
        admit_finding(self.plan, {
            "attempt_id": "at_" + "0" * 32, "sequence_id": "sq_" + "0" * 32,
            "role": "investigator", "finding_class": "verification_gap",
            "affected_component": "pilot", "significance": "low",
            "confidence": "high", "relation_to_requirement": "adjacent",
        }, self._now())
        report2 = svc2.run_cycle(self.ro, project_state, self._now())
        self.assertTrue(report2["proposals"][0].get("converged"))
        self.assertEqual(self._sequence_count(), 1)
        self.assertEqual(self.ro.execute(
            "SELECT COUNT(*) AS n FROM admissions").fetchone()["n"],
            admissions)

    def test_interrupted_submission_recovers(self):
        """F-CE-3: a crash between the durable proposal row and the
        Registry outcome reconciles on the next cycle — the stored
        candidate bytes are resubmitted and the ledger sees ONE record."""
        proposal = make_proposal(req=requirement("Build exactly once."))
        self.plan.add_objective("obj-1", "pilot", "you", self._now())

        class Boom(Exception):
            pass

        class FailingRegistry(object):
            def admit_payload(self, payload, submitted_by):
                raise Boom()

        svc = ChiefEngineerService(self.plan, FailingRegistry(), self.env,
                                   self.clock, ListPolicy([[proposal]]))
        with self.assertRaises(Boom):
            svc.run_cycle(self.ro, project_state, self._now())
        interrupted = self.plan.proposals_by_status("SUBMITTED")
        self.assertEqual(len(interrupted), 1)
        self.assertEqual(self._sequence_count(), 0)

        # The restarted planner re-derives the same intent; the events
        # of the interrupted cycle are still unconsumed, so it wakes
        # without new stimulus and resubmits the DURABLE candidate.
        svc2 = self._service(ListPolicy([[proposal]]))
        report = svc2.run_cycle(self.ro, project_state, self._now())
        self.assertIn("ADMITTED",
                      [p["status"] for p in report["proposals"]])
        self.assertEqual(self._sequence_count(), 1)
        admitted = self.plan.proposals_by_status("ADMITTED")[0]
        self.assertEqual(
            json.loads(admitted["candidate"])["task_contract_id"],
            json.loads(interrupted[0]["candidate"])["task_contract_id"])

    def test_findings_are_information_not_work(self):
        admit_finding(self.plan, {
            "attempt_id": "at_" + "1" * 32, "sequence_id": "sq_" + "1" * 32,
            "role": "investigator", "finding_class": "newly_discovered_work",
            "affected_component": "pilot", "significance": "high",
            "confidence": "high", "relation_to_requirement": "beyond",
            "suggested_follow_up": "Someone COULD build this.",
        }, self._now())
        self.plan.add_objective("obj-1", "pilot", "you", self._now())
        svc = self._service(ListPolicy([[]]))
        report = svc.run_cycle(self.ro, project_state, self._now())
        self.assertIsNotNone(report["cycle_id"])
        self.assertEqual(report["proposals"], [])
        self.assertEqual(self._sequence_count(), 0)
        self.assertEqual(self.plan.unconsumed_findings(), [])

    def test_planning_budget_guard(self):
        self.plan.add_objective("obj-1", "pilot", "you", self._now())
        waits = [make_proposal(cls="WAIT_FOR_AUTHORITY",
                               reason="r%d" % i, subject_id="s%d" % i)
                 for i in range(3)]
        svc = self._service(ListPolicy([waits]), max_proposals_per_cycle=2)
        with self.assertRaises(PlanningBudget):
            svc.run_cycle(self.ro, project_state, self._now())


class BoundaryTests(IntelligenceTestCase):
    def test_authority_reads_are_physically_read_only(self):
        """The plane's own handle on autobot.db cannot write: the Chief
        Engineer and Taskforce mutate authority only through the public
        Registry/Sequencer APIs (D-63; cap-43/cap-51 forbiddances)."""
        with self.assertRaises(sqlite3.OperationalError):
            self.ro.execute("UPDATE sequences SET state = 'ACCEPTED'")
        with self.assertRaises(sqlite3.OperationalError):
            self.ro.execute(
                "INSERT INTO objects (object_hash, kind, content_address,"
                " minted_utc) VALUES ('x', 'y', 'z', 'w')")

    def test_driver_lease_is_exclusive(self):
        t0 = self.clock.now()
        self.plan.acquire_driver_lease(
            "chief-a", instant(t0), instant(t0 + timedelta(hours=1)))
        with self.assertRaises(DriverBusy):
            self.plan.acquire_driver_lease(
                "chief-b", instant(t0), instant(t0 + timedelta(hours=1)))
        later = t0 + timedelta(hours=2)
        self.plan.acquire_driver_lease(
            "chief-b", instant(later), instant(later + timedelta(hours=1)))

    def test_agent_factory_owns_seat_selection(self):
        factory = AgentFactory()
        factory.register("software_implementation", "builder", "autoswarm",
                         model="claude")
        factory.register("software_implementation", "builder", "fake",
                         model=None)
        first = factory.select("software_implementation")
        self.assertEqual(first["provider"], "autoswarm")
        self.assertEqual(first["selected_by"], "agent_factory")
        narrowed = factory.select("software_implementation",
                                  providers=["fake"])
        self.assertEqual(narrowed["provider"], "fake")
        with self.assertRaises(AgentFactoryError):
            factory.select("security_review")

    def test_planner_authors_and_the_service_submits(self):
        """The CORR-15 principal handoff, adversarially pinned: the
        frozen payload is AUTHORED by the planner (cap-07), but no
        Registry call executes as the planner principal — the
        deterministic service submits over the autobot_service boundary
        edge, and the ledger records that identity (cap-08 stays the
        Registry's)."""
        captured = []
        real = self.rt.registry

        class SpyRegistry(object):
            def admit_payload(self, payload, submitted_by):
                captured.append((submitted_by, payload["authored_by"]))
                return real.admit_payload(payload,
                                          submitted_by=submitted_by)

        self.plan.add_objective("obj-1", "pilot", "you", self._now())
        svc = ChiefEngineerService(
            self.plan, SpyRegistry(), self.env, self.clock,
            ListPolicy([[make_proposal(
                req=requirement("Build under the handoff."))]]))
        report = svc.run_cycle(self.ro, project_state, self._now())
        self.assertEqual(report["proposals"][0]["status"], "ADMITTED")
        self.assertEqual(captured,
                         [(SUBMITTING_PRINCIPAL, "planner")])
        self.assertNotEqual(SUBMITTING_PRINCIPAL, "planner")
        row = self.ro.execute(
            "SELECT submitted_by FROM admissions ORDER BY admission_id"
            " DESC LIMIT 1").fetchone()
        self.assertEqual(row["submitted_by"], "autobot_service")


class ProjectionBundleTests(IntelligenceTestCase):
    def _identity(self):
        return {"sequence_id": "sq_" + "2" * 32,
                "attempt_id": "at_" + "2" * 32, "fence": 1,
                "attempt_contract_hash": "sha256:" + "2" * 64}

    def test_context_bundle_sections_and_reproducibility(self):
        record = support.local_record_payload()
        base_ctx = {"author": "planner", "guidance": GUIDANCE}
        data1, addr1 = project_prompt(self.rt.store, record, "builder",
                                      self._identity(), base_ctx)
        artifact = json.loads(data1.decode("utf-8"))
        self.assertEqual(artifact["engineering_context"],
                         {"provenance": "chief-engineer-guidance",
                          "author": "planner", "guidance": GUIDANCE})
        self.assertEqual(
            artifact["completion_contract"]["role_policy"],
            record["completion_policies"]["builder"])
        full_ctx = dict(base_ctx)
        full_ctx["predecessor_findings"] = [
            {"finding_id": "fnd:abc", "finding_class": "verification_gap",
             "suggested_follow_up": "close the gap"}]
        full_ctx["context_refs"] = ["sha256:" + "3" * 64]
        full_ctx["repository_context"] = "swarm-coordinator, branch main"
        full_ctx["expected_outputs"] = "artifacts and findings"
        data2, addr2 = project_prompt(self.rt.store, record, "builder",
                                      self._identity(), full_ctx)
        data3, addr3 = project_prompt(self.rt.store, record, "builder",
                                      self._identity(), full_ctx)
        self.assertEqual((data2, addr2), (data3, addr3))
        self.assertNotEqual(addr1, addr2)
        bundled = json.loads(data2.decode("utf-8"))["engineering_context"]
        self.assertEqual(bundled["predecessor_findings"][0]["finding_id"],
                         "fnd:abc")
        self.assertEqual(bundled["repository_context"],
                         "swarm-coordinator, branch main")
        with self.assertRaises(ConflictError):
            bad = dict(base_ctx, predecessor_findings=[{"no_id": True}])
            project_prompt(self.rt.store, record, "builder",
                           self._identity(), bad)


if __name__ == "__main__":
    unittest.main()
