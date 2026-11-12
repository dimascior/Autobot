"""The end-to-end autonomous pilot (the CE phase's §15/§16 proof).

Three contracts driven by an actual Chief Engineer reasoning cycle —
no scripted call order, only rules over authoritative state:

  A (INVESTIGATE, READ_ONLY): a specialist investigates and returns a
  structured finding. B (implementation, SOURCE_MODIFYING): proposed by
  the planner FROM the finding's content, executes over the real local
  Git CAS, and pauses visibly at YOUR cap-36 permit boundary — where the
  pilot also restarts the whole intelligence plane. C (review,
  READ_ONLY): decomposed alongside B, BLOCKED on B's released record
  edge the entire way, READY only through t-s02, then allocated and
  completed. PROJECT_COMPLETE closes the loop and the driver reaches a
  stable no-action state.

Conformance arms in this file: restart without duplicate proposals,
contracts or launches; the Sequencer-minted call identity at the
provider; projection binding (frozen record + exact attempt + the
planner's finding bundle); verification failure -> repair proposal ->
informed second attempt; re-execution gated on the planner's word;
concurrent allocation of independent READY contracts; and the ledger as
the only planning input.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import support
from autobot import project_state
from autobot.models import instant
from fake_transport import FakeTransport
from fake_verifier import FakeVerifierRunner, receipt_bytes
from intelligence import (
    AgentFactory,
    ChiefEngineerPolicy,
    ContinuousDriver,
    PlanStore,
)

# The frozen record family requires COMMAND_GOVERNANCE +
# EXECUTION_LIFECYCLE (va_record evidence_requirements), so a COMPLETE
# obj-15 classification needs obj-12 + obj-11 admitted (C3-M1); the
# transcript rides along as extra admitted evidence.
WITNESSES = [("obj-11", "autoswarm", b"provider session record\n"),
             ("obj-12", "helios", b"command governance log\n"),
             ("obj-13", "agent_clis", b"session transcript\n")]
FOLLOW_UP = ("Advance refs/heads/main to the prepared commit so the"
             " pilot subsystem lands.")


class ScriptedTransport(FakeTransport):
    """Role-keyed collect scripts over the honest Transport contract."""

    def __init__(self):
        FakeTransport.__init__(self)
        self.scripts = {}

    def collect(self, call_id):
        role = self.sessions[call_id]["record"]["role"]
        queue = self.scripts.get(role)
        if not queue:
            return None
        return queue.pop(0)


class QueueRunner(FakeVerifierRunner):
    """Per-assertion outcome queues; PASS once a queue drains."""

    def __init__(self, queues=None):
        FakeVerifierRunner.__init__(self)
        self.queues = {k: list(v) for k, v in (queues or {}).items()}

    def run_assertion(self, assertion, registration, context):
        queue = self.queues.get(assertion["assertion_id"])
        outcome = queue.pop(0) if queue else "PASS"
        return outcome, receipt_bytes(
            assertion["assertion_id"], assertion.get("verifier_id"),
            outcome, context["attempt_id"])


def result(artifact=b"engineering notes\n", findings=None):
    return {"witness_files": list(WITNESSES), "outcome": "success",
            "artifact_bytes": artifact, "findings": findings or []}


def base_fields(view, objective_id):
    return {"objective_id": objective_id,
            "snapshot": {"entries": view.snapshot["entries"],
                         "head_hash": view.snapshot["head_hash"]},
            "provenance": {"findings": [], "events": []}}


def roles(name):
    return {name: {"required": True, "success_outcomes": ["success"]}}


class PilotPolicy(ChiefEngineerPolicy):
    """Deterministic engineering rules over authoritative state — the
    reasoning seam an LLM-backed policy later fills."""

    def plan(self, view):
        if not view.objectives:
            return []
        objective = view.objectives[0]
        proposals = []
        sequences = view.authority["sequences"]

        if not sequences:
            p = base_fields(view, objective["objective_id"])
            p.update({
                "class": "INVESTIGATE",
                "rationale": "no work exists yet: the objective needs an"
                             " engineering investigation first",
                "requirement": {
                    "objective": "Investigate the pilot subsystem and"
                                 " report buildable work.",
                    "capability": "research",
                    "roles": roles("investigator"),
                    "guidance": "Investigate the subsystem named by the"
                                " objective; return findings, not"
                                " changes."}})
            return [p]

        for finding in view.findings:
            if finding["finding_class"] != "newly_discovered_work":
                continue
            follow_up = finding["suggested_follow_up"] or ""
            p = base_fields(view, objective["objective_id"])
            p.update({
                "class": "DECOMPOSE_TASK",
                "rationale": "the investigation surfaced one buildable"
                             " change and its review",
                "provenance": {"findings": [finding["finding_id"]],
                               "events": []},
                "parts": [
                    {"part_id": "b-impl", "requirement": {
                        "objective": "Implement the investigator's"
                                     " follow-up: " + follow_up,
                        "finalization_class": "SOURCE_MODIFYING",
                        "capability": "software_implementation",
                        "roles": roles("builder"),
                        "guidance": "Apply exactly the follow-up the"
                                    " investigation recorded: "
                                    + follow_up}},
                    {"part_id": "c-review",
                     "depends_on_parts": ["b-impl"],
                     "requirement": {
                        "objective": "Review the landed implementation"
                                     " of the pilot follow-up.",
                        "finalization_class": "READ_ONLY",
                        "capability": "verification",
                        "roles": roles("reviewer"),
                        "guidance": "Review the released implementation"
                                    " against the investigator's"
                                    " finding."}},
                ]})
            proposals.append(p)

        permits = {row["sequence_id"]: row
                   for row in view.authority["permits"]}
        for seq_id in view.workstreams["finalization_queue"]:
            permit = permits.get(seq_id)
            headroom = permit is not None and (
                permit["consumed_uses"] + permit["reserved_uses"]
                < permit["permitted_uses"])
            if not headroom:
                p = base_fields(view, objective["objective_id"])
                p.update({
                    "class": "WAIT_FOR_AUTHORITY",
                    "rationale": "finalization needs a publication"
                                 " permit only you grant",
                    "reason": "publication permit required (cap-36)",
                    "subject_id": seq_id,
                    "provenance": {"findings": [], "events": [
                        e["journal_seq"] for e in view.events
                        if e["subject_id"] == seq_id]}})
                proposals.append(p)

        # Completion is declared only by a cycle that proposes NOTHING
        # else: a batch that still creates or waits on work is not a
        # completed project (the F-CE-1 premature-completion guard).
        if not proposals and sequences and all(
                s["state"] == "COMPLETED" for s in sequences):
            p = base_fields(view, objective["objective_id"])
            p.update({
                "class": "PROJECT_COMPLETE",
                "rationale": "every sequence is COMPLETED and nothing"
                             " actionable remains"})
            proposals.append(p)
        return proposals


class RepairPolicy(ChiefEngineerPolicy):
    """Creates one contract, then repairs on the registered t-s11 signal."""

    def __init__(self, repair=True):
        self.repair = repair

    def plan(self, view):
        if not view.objectives:
            return []
        objective = view.objectives[0]
        proposals = []
        if not view.authority["sequences"]:
            p = base_fields(view, objective["objective_id"])
            p.update({
                "class": "CREATE_TASK",
                "rationale": "the objective's one implementation step",
                "requirement": {
                    "objective": "Build the gadget to its assertions.",
                    "finalization_class": "READ_ONLY",
                    "capability": "software_implementation",
                    "roles": roles("builder"),
                    "guidance": "Build the gadget; verification decides."}})
            return [p]
        if self.repair:
            for event in view.events:
                if event["event_kind"] != "repair_ready":
                    continue
                facts = json.loads(event["facts"] or "{}")
                failed = ",".join(facts.get("failed_assertion_ids", []))
                p = base_fields(view, objective["objective_id"])
                p.update({
                    "class": "REPAIR_TASK",
                    "rationale": "verification failed inside the frozen"
                                 " repair budget; an informed repair"
                                 " attempt is warranted",
                    "sequence_id": event["subject_id"],
                    "capability": "software_implementation",
                    "guidance": "Repair the failed assertions: " + failed,
                    "provenance": {"findings": [],
                                   "events": [event["journal_seq"]]}})
                proposals.append(p)
        sequences = view.authority["sequences"]
        if not proposals and sequences and all(
                s["state"] == "COMPLETED" for s in sequences):
            p = base_fields(view, objective["objective_id"])
            p.update({"class": "PROJECT_COMPLETE",
                      "rationale": "the gadget is done"})
            proposals.append(p)
        return proposals


class TwinPolicy(ChiefEngineerPolicy):
    """Two independent investigations in one batch (concurrency arm)."""

    def plan(self, view):
        if not view.objectives or view.authority["sequences"]:
            return []
        objective = view.objectives[0]
        out = []
        for name in ("alpha", "beta"):
            p = base_fields(view, objective["objective_id"])
            p.update({
                "class": "INVESTIGATE",
                "rationale": "independent workstream " + name,
                "requirement": {
                    "objective": "Investigate workstream %s." % name,
                    "capability": "research",
                    "roles": roles("investigator"),
                    "guidance": "Investigate %s only." % name}})
            out.append(p)
        return out


class PilotTestCase(unittest.TestCase):
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
        self.repo = self.dir / "repo"
        self.sha_old, self.sha_new = support.init_git_repo(self.repo)
        self.transport = ScriptedTransport()
        self.factory = AgentFactory()
        for capability, role in (("research", "investigator"),
                                 ("software_implementation", "builder"),
                                 ("verification", "reviewer")):
            self.factory.register(capability, role, "fake")
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.driver = None

    def tearDown(self):
        if self.driver is not None:
            self.driver.close()
        self.plan.close()
        self.rt.db.close()
        self.tmp.cleanup()

    def _driver(self, policy, runner=None, holder="chief-engineer"):
        return ContinuousDriver(
            self.plan, self.rt.registry, self.rt.sequencer, self.rt.store,
            self.env, self.clock, policy, self.transport,
            runner or FakeVerifierRunner(), self.factory,
            self.dir / "autobot.db", repo_path=self.repo,
            repository_context="the pilot repository, branch main",
            holder=holder)

    def _states(self):
        return {r["sequence_id"]: r["state"] for r in
                self.rt.db.conn.execute(
                    "SELECT sequence_id, state FROM sequences").fetchall()}

    def _rules_for(self, sequence_id):
        rules = []
        for row in self.rt.db.conn.execute(
                "SELECT payload FROM transition_journal ORDER BY seq"
        ).fetchall():
            payload = json.loads(row["payload"])
            if payload.get("subject_id") == sequence_id:
                rules.append(payload["rule"])
        return rules

    def _sequence_by_objective(self, marker):
        for row in self.rt.db.conn.execute(
                "SELECT s.sequence_id, s.record_hash, r.content_address"
                " FROM sequences s JOIN records r ON r.record_hash ="
                " s.record_hash").fetchall():
            payload = json.loads(
                self.rt.store.get(row["content_address"]).decode("utf-8"))
            if marker in payload["objective"]:
                return row["sequence_id"], row["record_hash"], payload
        raise AssertionError("no sequence with %r in its objective" % marker)


class AutonomousPilotTests(PilotTestCase):
    def test_three_contract_loop_with_restart_and_permit_boundary(self):
        self.transport.scripts = {
            "investigator": [result(findings=[{
                "finding_class": "newly_discovered_work",
                "affected_component": "pilot-subsystem",
                "evidence_refs": [],
                "significance": "high",
                "confidence": "high",
                "relation_to_requirement": "within",
                "suggested_follow_up": FOLLOW_UP}])],
            "builder": [result(artifact=json.dumps(
                {"target_ref": "refs/heads/main",
                 "expected_old": self.sha_old,
                 "new_value": self.sha_new}).encode("utf-8"))],
            "reviewer": [result()],
        }
        self.plan.add_objective(
            "obj-pilot", "Deliver the pilot subsystem end to end.",
            "you", instant(self.clock.now()))
        self.driver = self._driver(PilotPolicy())
        first = self.driver.run_until_stable()

        # The permit boundary: B is VERIFIED and waiting on YOU; A is
        # COMPLETED through the RO acceptance arc; C sits BLOCKED on B.
        b_id, b_record, b_payload = self._sequence_by_objective(
            "Implement the investigator's follow-up")
        a_id, _a_record, _ = self._sequence_by_objective(
            "Investigate the pilot subsystem")
        c_id, _c_record, _ = self._sequence_by_objective(
            "Review the landed implementation")
        states = self._states()
        self.assertEqual((states[a_id], states[b_id], states[c_id]),
                         ("COMPLETED", "VERIFIED", "BLOCKED"))
        debug = "proposals=%r events=%r" % (
            [(r["class"], r["status"]) for r in self.plan.conn.execute(
                "SELECT class, status FROM proposals").fetchall()],
            [(e["event_kind"], e["consumed_in_cycle"]) for e in
             self.plan.conn.execute(
                 "SELECT event_kind, consumed_in_cycle FROM"
                 " planning_events ORDER BY journal_seq").fetchall()])
        self.assertEqual(len(first["waiting"]), 1, debug)
        self.assertEqual(first["waiting"][0]["class"], "WAIT_FOR_AUTHORITY")
        # The planner reasoned FROM the finding: B's frozen requirement
        # carries the investigator's follow-up text.
        self.assertIn(FOLLOW_UP, b_payload["objective"])

        # Restart the whole intelligence plane at the boundary.
        self.driver.close()
        self.plan.close()
        sequences_before = len(states)
        admissions_before = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM admissions").fetchone()["n"]
        launches_before = len(self.transport.launch_log)
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.driver = self._driver(PilotPolicy(), holder="chief-restarted")

        # YOUR word: the publication permit (cap-36).
        self.rt.sequencer.grant_permit(b_id, permitted_uses=3)
        final = self.driver.run_until_stable()

        states = self._states()
        self.assertEqual(set(states.values()), {"COMPLETED"})
        self.assertEqual(len(states), 3)
        head = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "refs/heads/main"],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, self.sha_new)

        # Restart duplicated nothing: same sequences, no new admissions
        # beyond C-review's convergence-free state, no relaunches.
        self.assertEqual(len(self._states()), sequences_before)
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM admissions").fetchone()["n"],
            admissions_before)
        self.assertEqual(
            len(self.transport.launch_log) - launches_before, 1)  # only C

        # C was BLOCKED first (t-s01) and READY only through t-s02.
        c_rules = self._rules_for(c_id)
        self.assertIn("t-s01", c_rules)
        self.assertIn("t-s02", c_rules)
        self.assertLess(c_rules.index("t-s01"), c_rules.index("t-s02"))

        # Execution identity: every provider launch used the
        # Sequencer-minted call_id, exactly once each.
        ledger_calls = [r["call_id"] for r in self.rt.db.conn.execute(
            "SELECT call_id FROM attempt_calls ORDER BY call_id")]
        launched = sorted(l["call_id"] for l in self.transport.launch_log)
        self.assertEqual(launched, sorted(ledger_calls))
        self.assertEqual(len(ledger_calls), 3)

        # The finding is traceable to A's attempt, and B's projection
        # carries the planner-approved bundle bound to B's exact attempt.
        finding = self.plan.conn.execute(
            "SELECT * FROM findings").fetchone()
        a_attempt = self.rt.db.conn.execute(
            "SELECT attempt_id FROM attempts WHERE sequence_id = ?",
            (a_id,)).fetchone()["attempt_id"]
        self.assertEqual(finding["attempt_id"], a_attempt)
        b_call = self.rt.db.conn.execute(
            "SELECT * FROM attempt_calls c JOIN attempts a ON"
            " a.attempt_id = c.attempt_id WHERE a.sequence_id = ?",
            (b_id,)).fetchone()
        projection = json.loads(self.rt.store.get(
            b_call["projection_address"]).decode("utf-8"))
        self.assertEqual(projection["record"]["record_hash"], b_record)
        self.assertEqual(projection["attempt"]["attempt_id"],
                         b_call["attempt_id"])
        self.assertEqual(
            projection["engineering_context"]["predecessor_findings"][0]
            ["finding_id"], finding["finding_id"])
        self.assertIn(FOLLOW_UP,
                      projection["engineering_context"]["guidance"])

        # Stable no-action state: the objective is closed, waits are
        # retired, and one more step is quiet.
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(final["waiting"], [])
        self.assertTrue(self.driver.step()["quiet"])

    def test_verification_failure_creates_repair_proposal(self):
        self.transport.scripts = {"builder": [result(), result()]}
        self.plan.add_objective("obj-gadget", "Build the gadget.", "you",
                                instant(self.clock.now()))
        runner = QueueRunner({"a2": ["FAIL"]})
        self.driver = self._driver(RepairPolicy(), runner=runner)
        final = self.driver.run_until_stable()
        states = self._states()
        self.assertEqual(list(states.values()), ["COMPLETED"])
        seq_id = list(states)[0]
        attempts = self.rt.db.conn.execute(
            "SELECT attempt_id, state FROM attempts WHERE sequence_id = ?"
            " ORDER BY rowid", (seq_id,)).fetchall()
        self.assertEqual(len(attempts), 2)
        rules = self._rules_for(seq_id)
        self.assertIn("t-s11", rules)
        repair_rows = [p for p in self.plan.conn.execute(
            "SELECT * FROM proposals WHERE class = 'REPAIR_TASK'"
        ).fetchall()]
        self.assertEqual(len(repair_rows), 1)
        self.assertEqual(repair_rows[0]["status"], "APPLIED")
        # The second (repair) attempt's projection carries the repair
        # guidance naming the failed assertion.
        second = self.rt.db.conn.execute(
            "SELECT c.projection_address FROM attempt_calls c WHERE"
            " c.attempt_id = ?", (attempts[-1]["attempt_id"],)).fetchone()
        projection = json.loads(self.rt.store.get(
            second["projection_address"]).decode("utf-8"))
        self.assertIn("a2", projection["engineering_context"]["guidance"])
        self.assertEqual(final["active_objectives"], [])

    def test_failure_never_reexecutes_without_the_planners_word(self):
        self.transport.scripts = {"builder": [result(), result()]}
        self.plan.add_objective("obj-gadget", "Build the gadget.", "you",
                                instant(self.clock.now()))
        runner = QueueRunner({"a2": ["FAIL"]})
        self.driver = self._driver(RepairPolicy(repair=False),
                                   runner=runner)
        self.driver.run_until_stable()
        states = self._states()
        seq_id = list(states)[0]
        self.assertEqual(states[seq_id], "READY")  # t-s11 returned it
        attempts = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
            (seq_id,)).fetchone()["n"]
        self.assertEqual(attempts, 1)

    def test_seat_selection_belongs_to_the_agent_factory(self):
        """cap-14, adversarially pinned: the Taskforce asks for WHAT it
        needs (capability, role) and dictates no provider or model; the
        Agent Factory alone answers WHICH seat, and the persisted seat
        records that principal."""
        requests = []

        class SpyFactory(AgentFactory):
            def select(self, capability, role=None, providers=None):
                requests.append({"capability": capability, "role": role,
                                 "providers": providers})
                return AgentFactory.select(self, capability, role=role,
                                           providers=providers)

        self.factory = SpyFactory()
        self.factory.register("software_implementation", "builder", "fake")
        self.transport.scripts = {"builder": [result()]}
        self.plan.add_objective("obj-gadget", "Build the gadget.", "you",
                                instant(self.clock.now()))
        self.driver = self._driver(RepairPolicy())
        self.driver.run_until_stable()
        self.assertEqual(requests, [{"capability":
                                     "software_implementation",
                                     "role": "builder",
                                     "providers": None}])
        seat = self.plan.conn.execute(
            "SELECT provider, model, selected_by FROM seats").fetchone()
        self.assertEqual((seat["provider"], seat["selected_by"]),
                         ("fake", "agent_factory"))

    def test_independent_ready_contracts_allocate_concurrently(self):
        self.transport.scripts = {
            "investigator": [result(), result()],
        }
        self.plan.add_objective("obj-twin", "Investigate both streams.",
                                "you", instant(self.clock.now()))
        self.driver = self._driver(TwinPolicy())
        reports = [self.driver.step()]  # cycle admits both
        allocated = []
        for _ in range(6):
            report = self.driver.step()
            reports.append(report)
            if report["sweep"]["allocated"]:
                allocated = report["sweep"]["allocated"]
                break
        self.assertEqual(len(allocated), 2)
        launched = [l["call_id"] for l in self.transport.launch_log]
        self.assertEqual(sorted(launched),
                         sorted(a["call_id"] for a in allocated))


class MissingResultTests(PilotTestCase):
    def test_missing_result_manufactures_no_state(self):
        """The operator's pre-commit correction (D-C3-4): the absence of a
        terminal provider result is NOT an observation. The attempt is
        RUNNING because of the POSITIVE t-a03 lifecycle witness (the
        adopted session record), and a collect sweep that observes no
        terminal result changes NOTHING — no transition, no journal
        entry, no receipt. When the terminal observation later arrives,
        the arc proceeds because of THAT observation — absence of an
        authoritative observation is never an observation of the
        opposite state."""
        from autobot.transport import reconcile_dispatched
        from intelligence import Taskforce, readonly_view
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Missing result arm"),
            submitted_by="autobot_service")
        seq = self.rt.sequencer
        seq.mark_ready(admitted["sequence_id"])
        info = seq.start_execution(admitted["sequence_id"])
        seq.reserve_attempt(info["attempt_id"],
                            expected_fence=info["fence"])
        projection = self.rt.store.put(b"projection for the missing arm\n")
        call_id = seq.dispatch_attempt(
            info["attempt_id"], "builder", "fake", projection)["call_id"]
        self.transport.launch(projection, "fake", "builder", call_id)
        report = reconcile_dispatched(seq, self.transport)
        self.assertEqual(report[0]["action"], "adopted")
        # RUNNING rests on the positive adopted-session witness (t-a03):
        rules = self._rules_for(info["attempt_id"])
        self.assertIn("t-a03", rules)
        ro = readonly_view(str(self.dir / "autobot.db"))
        try:
            taskforce = Taskforce(
                self.plan, seq, self.rt.store, self.transport,
                FakeVerifierRunner(), self.factory, self.clock, ro)
            self.transport.scripts = {"builder": []}  # no terminal result
            before = self.rt.db.conn.execute(
                "SELECT COUNT(*) AS n FROM transition_journal"
            ).fetchone()["n"]
            swept = taskforce.collect_running(instant(self.clock.now()))
            self.assertEqual(swept, [])
            state = self.rt.db.conn.execute(
                "SELECT state FROM attempts WHERE attempt_id = ?",
                (info["attempt_id"],)).fetchone()["state"]
            self.assertEqual(state, "RUNNING")  # unchanged, not inferred
            self.assertEqual(
                self.rt.db.conn.execute(
                    "SELECT COUNT(*) AS n FROM transition_journal"
                ).fetchone()["n"], before)
            # the terminal observation arrives: the arc proceeds on IT
            self.transport.scripts = {"builder": [result()]}
            taskforce.collect_running(instant(self.clock.now()))
            state = self.rt.db.conn.execute(
                "SELECT state FROM attempts WHERE attempt_id = ?",
                (info["attempt_id"],)).fetchone()["state"]
            self.assertEqual(state, "EVIDENCE_COMPLETE")
        finally:
            ro.close()
