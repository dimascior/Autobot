"""The production Engineer policy, with a scripted reasoner.

``ReasoningEngineerPolicy`` is the seam's production implementation:
PlanningView in, typed proposals out, a reasoning model in between. These
tests replace only the model — a deterministic "brain" that reads the
same projection a model would and answers in the same JSON — and run
everything else for real: projection rendering, answer parsing, identity
resolution, validation, the deterministic guards, disposal through the
Engineer service, project assignment and the authority runtime.

The brain answers in the Engineer's two roles, as a model does: it opens
the project and lays out the work, and it staffs — an explicit selection
of an outside specialist, or nothing (a capability request the Taskforce
resolves to a retained member).
"""

import json
import subprocess
import unittest

from autobot.models import instant
from intelligence import (
    ClaudeCliReasoner,
    ReasoningChiefEngineerPolicy,
    ReasoningEngineerPolicy,
)
from intelligence import policy as policy_module
from intelligence.policy import (
    POLICY_ID,
    ReasonerError,
    extract_json_object,
    render_projection,
)
from test_autonomous_pilot import QueueRunner, result
from test_taskforce_orchestration import WorkforceTestCase


def projection_of(prompt):
    """What a model would read: the PROJECTION JSON in the prompt."""
    marker = "\nPROJECTION\n"
    start = prompt.index(marker) + len(marker)
    value, _end = json.JSONDecoder().raw_decode(prompt[start:])
    return value


class Brain(object):
    """A deterministic stand-in for the reasoning model: it reads the
    projection and answers as the instructions ask. Not a mock of the
    policy — the policy is what is under test."""

    def __init__(self, decide):
        self.decide = decide
        self.prompts = []
        self.last_meta = {"session_id": "scripted"}

    def __call__(self, prompt):
        self.prompts.append(prompt)
        answer = self.decide(projection_of(prompt), len(self.prompts))
        if isinstance(answer, str):
            return answer
        return "Here is my decision.\n```json\n%s\n```" % json.dumps(answer)


def open_project(objective_id):
    return {"class": "OPEN_PROJECT", "objective_id": objective_id,
            "rationale": "the solution needs coordinated work",
            "project": {"title": "Investigate A, then B",
                        "solution": "Establish A first; B builds on what"
                                    " A finds, so it follows A."}}


def investigation(objective_id, name, key=None, **extra):
    requirement = {
        "objective": "Investigate workstream %s." % name,
        "capability": "research", "role": "investigator",
        "guidance": "Investigate %s only." % name}
    requirement.update(extra)
    proposal = {"class": "INVESTIGATE", "objective_id": objective_id,
                "rationale": "the objective needs workstream " + name,
                "requirement": requirement}
    if key is not None:
        proposal["workstream"] = key
    return proposal


def outside_offering(projection, capability="research",
                     role="investigator"):
    """The outside specialists the projection says could be selected."""
    return [s["specialist_id"]
            for s in projection["taskforce"]["outside_specialists"]
            if any((o["capability"], o["role"]) == (capability, role)
                   for o in s["offers"])]


def select(objective_id, key, specialist_id):
    return {"class": "ASSIGN_SPECIALIST", "objective_id": objective_id,
            "rationale": "no retained seat covers this workstream",
            "workstream": key,
            "staffing": {"mode": "outside_specialist",
                         "specialist_id": specialist_id}}


def two_task_brain(projection, _call):
    """Open the project and staff A with an outside specialist; then
    retain whoever did qualifying work and request B from the Taskforce;
    then complete."""
    objective = projection["objectives"][0]
    oid = objective["objective_id"]
    workstreams = objective["project"]["workstreams"]
    proposals = []
    for candidate in projection["taskforce"]["retention_candidates"]:
        proposals.append({
            "class": "RETAIN_SPECIALIST", "objective_id": oid,
            "rationale": "verified research; needed again for B",
            "specialist_id": candidate["specialist_id"],
            "capability": candidate["capability"],
            "role": candidate["role"]})  # demonstrations omitted
    done = [w for w in workstreams if w["state"] == "COMPLETED"]
    if not workstreams:
        proposals.append(open_project(oid))
        proposals.append(investigation(oid, "A", key="a"))
        proposals.append(select(oid, "a", outside_offering(projection)[0]))
    elif len(workstreams) == 1 and len(done) == 1:
        # No assignment: a capability request the Taskforce resolves.
        proposals.append(investigation(oid, "B", key="b"))
    elif len(workstreams) == 2 and len(done) == 2 and not proposals:
        proposals.append({"class": "PROJECT_COMPLETE", "objective_id": oid,
                          "rationale": "both workstreams are COMPLETED"})
    return {"reasoning": "scripted", "proposals": proposals}


def one_task_brain(close_with_retention):
    """One workstream by an outside specialist; then retain whoever did
    qualifying work — together with the completion, or before it."""

    def decide(projection, _call):
        objective = projection["objectives"][0]
        oid = objective["objective_id"]
        streams = objective["project"]["workstreams"]
        if not streams:
            return {"reasoning": "", "proposals": [
                open_project(oid), investigation(oid, "A", key="a"),
                select(oid, "a", outside_offering(projection)[0])]}
        proposals = [
            {"class": "RETAIN_SPECIALIST", "objective_id": oid,
             "rationale": "research is needed again",
             "specialist_id": c["specialist_id"],
             "capability": c["capability"], "role": c["role"]}
            for c in projection["taskforce"]["retention_candidates"]]
        done = all(w["state"] == "COMPLETED" for w in streams)
        if done and (close_with_retention or not proposals):
            proposals.append({
                "class": "PROJECT_COMPLETE", "objective_id": oid,
                "rationale": "the workstream is COMPLETED"})
        return {"reasoning": "", "proposals": proposals}

    return decide


class ReasonedLoopTests(WorkforceTestCase):
    def _policy(self, brain, **kwargs):
        return ReasoningEngineerPolicy(
            brain, context={"repository_id": "autobot"}, **kwargs)

    def test_a_closing_retention_may_accompany_completion(self):
        """A workforce decision is not work: retaining the specialist
        whose work the project used may be decided with the completion.
        The retention is applied first, then the project closes — its
        team dissolved, the new membership kept."""
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        brain = Brain(one_task_brain(close_with_retention=True))
        policy = self._policy(brain)
        self.driver = self._driver(policy, runner=QueueRunner())
        final = self.driver.run_until_stable()
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(len(brain.prompts), 2)
        reasoned = [exchange for exchange in (
            json.loads(c["exchange"]) for c in self._rows(
                "SELECT exchange FROM cycle_reasoning ORDER BY cycle_id"))
            if exchange["reasoned"]]
        self.assertEqual(len(reasoned), 2)
        # The answer that retained AND completed was usable as given.
        self.assertEqual(reasoned[-1]["attempts"][-1]["errors"], [])
        self.assertEqual(
            [(p["class"], p["status"]) for p in self._rows(
                "SELECT class, status FROM proposals WHERE class IN"
                " ('RETAIN_SPECIALIST', 'PROJECT_COMPLETE') ORDER BY"
                " created_utc, rowid")],
            [("RETAIN_SPECIALIST", "APPLIED"),
             ("PROJECT_COMPLETE", "APPLIED")])
        membership = self._memberships()[0]
        self.assertEqual(membership["status"], "ACTIVE")
        project = self._projects()[0]
        self.assertEqual(project["status"], "COMPLETED")
        self.assertGreaterEqual(project["closed_utc"],
                                membership["joined_utc"])
        self.assertEqual({m["status"] for m in self._team()}, {"RELEASED"})

    def test_a_finished_project_is_put_to_the_engineer_again(self):
        """The last decision was a retention, and what follows it is
        routine (the seat it created). Every workstream is COMPLETED and
        the objective is still open — that alone is something to decide,
        so the Engineer is asked and completes; the loop does not go
        quiet with a finished project left open."""
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        brain = Brain(one_task_brain(close_with_retention=False))
        self.driver = self._driver(self._policy(brain),
                                   runner=QueueRunner())
        final = self.driver.run_until_stable()
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(len(brain.prompts), 3)
        last_cycle = self._rows("SELECT MAX(cycle_id) AS n FROM"
                                " cycle_reasoning")[0]["n"]
        woke = [e["event_kind"] for e in self._rows(
            "SELECT event_kind FROM workforce_events WHERE"
            " consumed_in_cycle = ?", (last_cycle,))]
        self.assertEqual(woke, ["seat_retained"])  # routine, by itself
        self.assertEqual(self._projects()[0]["status"], "COMPLETED")
        self.assertEqual(len(self._memberships()), 1)

    def test_a_refused_proposal_does_not_keep_the_project_open(self):
        """A proposal the objective budget refused never became work: it
        has no sequence. It stays on record and the Engineer is told, but
        it is nothing to complete — the project closes on the work that
        was admitted instead of staying open forever."""
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        self.plan.set_budget(self.objective_id, instant(self.clock.now()),
                             max_contracts=1)

        def decide(projection, _call):
            objective = projection["objectives"][0]
            oid = objective["objective_id"]
            streams = objective["project"]["workstreams"]
            if not streams:
                return {"reasoning": "", "proposals": [
                    open_project(oid), investigation(oid, "A", key="a"),
                    select(oid, "a", outside_offering(projection)[0]),
                    investigation(oid, "B", key="b")]}  # past the budget
            if all(w["state"] == "COMPLETED" for w in streams):
                return {"reasoning": "", "proposals": [
                    {"class": "PROJECT_COMPLETE", "objective_id": oid,
                     "rationale": "the admitted work is COMPLETED"}]}
            return {"reasoning": "", "proposals": []}

        self.driver = self._driver(self._policy(Brain(decide)),
                                   runner=QueueRunner())
        final = self.driver.run_until_stable()
        refused = self._rows(
            "SELECT * FROM proposals WHERE status = 'REJECTED'")
        self.assertEqual([p["class"] for p in refused], ["INVESTIGATE"])
        self.assertTrue(refused[0]["note"].startswith("budget: "))
        self.assertIsNone(refused[0]["sequence_id"])
        self.assertEqual(list(self._states().values()), ["COMPLETED"])
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(self._projects()[0]["status"], "COMPLETED")

    def test_the_reasoned_loop_retains_and_reuses(self):
        """The whole organization under the PRODUCTION policy: the
        reasoner opens the project, decides A and explicitly selects an
        outside specialist for it; then — seeing that specialist's
        verified work in the projection — retains it and decides B,
        naming nobody; the Taskforce resolves B to the retained member;
        the reasoner declares completion. Routine progress batches cost
        no model call."""
        self.transport.scripts = {"investigator": [result(), result()]}
        self._objective("Investigate A, then B.")
        brain = Brain(two_task_brain)
        self.driver = self._driver(self._policy(brain),
                                   runner=QueueRunner())
        final = self.driver.run_until_stable()
        self.assertEqual(final["active_objectives"], [])
        self.assertEqual(set(self._states().values()), {"COMPLETED"})
        first, second = self._assignments()
        self.assertEqual(first["selection_path"], "OUTSIDE_SPECIALIST")
        self.assertEqual(first["decided_by"], "PROJECT_MANAGER")
        self.assertEqual(second["selection_path"], "RETAINED_SEAT")
        self.assertEqual(second["decided_by"], "TASKFORCE")
        self.assertEqual(second["specialist_id"], first["specialist_id"])
        # The project was opened by the Project Manager, with a solution.
        project = self._projects()[0]
        self.assertEqual((project["opened_by"], project["status"]),
                         ("PROJECT_MANAGER", "COMPLETED"))
        self.assertIn("B builds on what A finds", project["solution"])
        self.assertEqual(
            [w["workstream_key"] for w in self._rows(
                "SELECT workstream_key FROM project_workstreams ORDER BY"
                " created_utc, rowid")], ["a", "b"])
        # The retention bound its evidence even though the model omitted
        # the list: binding is mechanical, the decision was the model's.
        decision = self._rows("SELECT * FROM retention_decisions WHERE"
                              " kind = 'RETAIN'")[0]
        demo = self._demonstrations()[0]["demonstration_id"]
        self.assertEqual(json.loads(decision["demonstration_ids"]), [demo])
        proposal = self.plan.proposal(decision["proposal_id"])
        body = json.loads(proposal["body"])
        self.assertEqual(body["provenance"]["workforce"]["demonstrations"],
                         [demo])
        self.assertEqual(body["snapshot"]["entries"],
                         proposal["snapshot_entries"])

        cycles = self._rows("SELECT * FROM cycle_reasoning ORDER BY"
                            " cycle_id")
        self.assertEqual({c["policy_id"] for c in cycles}, {POLICY_ID})
        reasoned = [json.loads(c["exchange"]) for c in cycles]
        calls = [e for e in reasoned if e["reasoned"]]
        # Three decisions were needed (project + A + its assignment;
        # retain + B; complete); every other cycle was routine progress
        # answered without the model.
        self.assertEqual(len(brain.prompts), 3)
        self.assertEqual(len(calls), 3)
        self.assertGreater(len(cycles), len(calls))
        for exchange in calls:
            attempt = exchange["attempts"][-1]
            self.assertTrue(attempt["prompt_sha256"].startswith("sha256:"))
            self.assertTrue(attempt["response_sha256"].startswith(
                "sha256:"))
            self.assertEqual(attempt["errors"], [])
            self.assertEqual(attempt["meta"], {"session_id": "scripted"})
        # What the model was shown when it decided to retain.
        deciding = projection_of(brain.prompts[1])
        candidates = deciding["taskforce"]["retention_candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(deciding["taskforce"]["members"], [])
        self.assertEqual(
            deciding["taskforce"]["recent_demonstrations"][0]["outcome"],
            "QUALIFYING")
        kinds = [e["kind"] for e in
                 deciding["what_is_new"]["workforce_events"]]
        self.assertIn("demonstration_recorded", kinds)
        shown = deciding["objectives"][0]["project"]
        self.assertEqual(shown["workstreams"][0]["state"], "COMPLETED")
        self.assertEqual(shown["workstreams"][0]["workstream"], "a")
        self.assertEqual(
            [(m["joined_as"], m["taskforce_member"])
             for m in shown["team"]], [("OUTSIDE_SPECIALIST", False)])
        self.assertEqual(deciding["environment"],
                         {"repository_id": "autobot"})
        self.assertIn({"capability": "research", "role": "investigator"},
                      deciding["taskforce"]["staffable"])
        # Routing never reaches the Engineer: no handle, no provider, no
        # model — it selects people by identity and verified record.
        for prompt in brain.prompts:
            text = json.dumps(projection_of(prompt))
            for hidden in ('"handle"', '"provider"', '"model"', "alpha",
                           "beta", "fake"):
                self.assertNotIn(hidden, text)

    def test_the_model_staffs_what_the_taskforce_cannot(self):
        """Work proposed with no assignment is a capability request. No
        retained seat covers it, so the Taskforce can resolve nobody: the
        workstream stands under needs_staffing — with the outside
        specialists that could be selected — and that alone is something
        to decide. The model, as Project Manager, selects one."""
        self.transport.scripts = {"investigator": [result()]}
        self._objective()
        beta = None

        def decide(projection, _call):
            objective = projection["objectives"][0]
            oid = objective["objective_id"]
            project = objective["project"]
            if not project["workstreams"]:
                return {"reasoning": "request it", "proposals": [
                    open_project(oid), investigation(oid, "A", key="a")]}
            if project["needs_staffing"]:
                need = project["needs_staffing"][0]
                eligible = [c["specialist_id"]
                            for c in need["outside_candidates"]
                            if c["eligible"]]
                return {"reasoning": "nobody is retained", "proposals": [
                    select(oid, need["workstream"], eligible[-1])]}
            if all(w["state"] == "COMPLETED"
                   for w in project["workstreams"]):
                return {"reasoning": "done", "proposals": [{
                    "class": "PROJECT_COMPLETE", "objective_id": oid,
                    "rationale": "the workstream is COMPLETED"}]}
            return {"reasoning": "in progress", "proposals": []}

        brain = Brain(decide)
        self.driver = self._driver(self._policy(brain),
                                   runner=QueueRunner())
        final = self.driver.run_until_stable()
        self.assertEqual(final["active_objectives"], [])
        # Three model calls: the work; its staffing; completion. The
        # staffing was decided from the view — the pump never had to
        # report a wait.
        self.assertEqual(len(brain.prompts), 3)
        self.assertEqual(self._rows(
            "SELECT * FROM workforce_events WHERE event_kind ="
            " 'staffing_unavailable'"), [])
        assignment = self._assignments()[0]
        self.assertEqual(self._handle(assignment["specialist_id"]), "beta")
        self.assertEqual(assignment["decided_by"], "PROJECT_MANAGER")
        staffing = projection_of(brain.prompts[1])["objectives"][0][
            "project"]["needs_staffing"]
        self.assertEqual(
            (staffing[0]["capability"], staffing[0]["role"],
             staffing[0]["cause"]),
            ("research", "investigator", "no_retained_seat"))
        self.assertEqual(len(staffing[0]["outside_candidates"]), 2)
        self.assertNotIn("handle", staffing[0]["outside_candidates"][0])
        self.assertIsNone(beta)
        self.assertEqual(self._memberships(), [])

    def test_the_model_lays_out_work_and_its_review_in_one_decision(self):
        """One answer: the project, a decomposition whose second part is
        an independent review of the first, and the staffing of both. The
        review is a REQUEST_REVIEW of the first part's frozen record,
        blocked by authority until that work is accepted; its staffing
        decision waits with it."""
        self.transport.scripts = {"investigator": []}  # the work runs on
        self._objective()

        def decide(projection, _call):
            objective = projection["objectives"][0]
            oid = objective["objective_id"]
            if objective["project"]["workstreams"]:
                return {"reasoning": "in progress", "proposals": []}
            people = outside_offering(projection)
            return {"reasoning": "", "proposals": [
                select(oid, "review", people[1]),  # in any order
                open_project(oid),
                {"class": "DECOMPOSE_TASK", "objective_id": oid,
                 "rationale": "the work and its independent review",
                 "parts": [
                     {"part_id": "work", "requirement": {
                         "objective": "Do the research.",
                         "capability": "research", "role": "investigator",
                         "guidance": "Research it.",
                         "finalization_class": "READ_ONLY"}},
                     {"part_id": "review", "review_of_part": "work",
                      "requirement": {
                          "objective": "Review the research.",
                          "capability": "research",
                          "role": "investigator",
                          "guidance": "Review the accepted research."}}]},
                select(oid, "work", people[0])]}

        brain = Brain(decide)
        policy = self._policy(brain)
        self.driver = self._driver(policy, runner=QueueRunner())
        report = self.driver.step()
        self.assertEqual(policy.last_exchange["attempts"][-1]["errors"], [])
        self.assertEqual(
            [(p["class"], p["status"]) for p in
             report["planning"]["proposals"]],
            [("OPEN_PROJECT", "APPLIED"), ("CREATE_TASK", "ADMITTED"),
             ("REQUEST_REVIEW", "ADMITTED"), ("DECOMPOSE_TASK", "APPLIED"),
             ("ASSIGN_SPECIALIST", "APPLIED"),
             ("ASSIGN_SPECIALIST", "APPLIED")])
        for _ in range(5):
            self.driver.step()
        self.assertEqual(sorted(self._states().values()),
                         ["BLOCKED", "EXECUTING"])
        self.assertEqual(len(self._assignments()), 1)
        waiting = self._rows("SELECT * FROM assignment_decisions WHERE"
                             " status = 'OPEN'")
        self.assertEqual(len(waiting), 1)  # the review's, held with it
        self.assertEqual(len(brain.prompts), 1)

    def test_work_without_a_project_is_asked_about_once(self):
        """The Project Manager role is entered explicitly: an answer that
        proposes coordinated work without opening a project is sent back
        once. A model that still opens none has its work organized under
        an implicit project — nothing is lost, and the record says who
        opened it."""
        self.transport.scripts = {"investigator": []}
        self._objective()

        def decide(projection, call):
            oid = projection["objectives"][0]["objective_id"]
            if projection["objectives"][0]["project"]["workstreams"]:
                return {"reasoning": "wait", "proposals": []}
            proposals = [investigation(oid, "A", key="a"),
                         select(oid, "a", outside_offering(projection)[0])]
            if call == 2:
                proposals.append(open_project(oid))  # in any order
            return {"reasoning": "", "proposals": proposals}

        brain = Brain(decide)
        policy = self._policy(brain)
        self.driver = self._driver(policy, runner=QueueRunner())
        report = self.driver.step()
        self.assertEqual(len(brain.prompts), 2)
        self.assertIn("put OPEN_PROJECT first", brain.prompts[1])
        # Applied in dependency order whatever order the model wrote.
        self.assertEqual(
            [(p["class"], p["status"]) for p in
             report["planning"]["proposals"]],
            [("OPEN_PROJECT", "APPLIED"), ("INVESTIGATE", "ADMITTED"),
             ("ASSIGN_SPECIALIST", "APPLIED")])
        self.assertEqual(self._projects()[0]["opened_by"],
                         "PROJECT_MANAGER")

    def test_a_model_that_never_opens_a_project_still_gets_one(self):
        self.transport.scripts = {"investigator": []}
        self._objective()

        def decide(projection, _call):
            oid = projection["objectives"][0]["objective_id"]
            if projection["objectives"][0]["project"]["workstreams"]:
                return {"reasoning": "wait", "proposals": []}
            return {"reasoning": "", "proposals": [
                investigation(oid, "A", key="a")]}

        brain = Brain(decide)
        self.driver = self._driver(self._policy(brain),
                                   runner=QueueRunner())
        self.driver.step()
        self.assertEqual(len(brain.prompts), 2)
        project = self._projects()[0]
        self.assertEqual((project["opened_by"], project["status"]),
                         ("IMPLICIT", "ACTIVE"))
        self.assertEqual(len(self._states()), 1)

    def test_an_unusable_answer_is_retried_with_its_problems(self):
        self.transport.scripts = {"investigator": [result()]}
        self._objective()

        def decide(projection, call):
            oid = projection["objectives"][0]["objective_id"]
            if call == 1:
                return "I think we should investigate. No JSON, sorry."
            if projection["objectives"][0]["project"]["workstreams"]:
                return {"reasoning": "wait", "proposals": []}
            return {"reasoning": "ok", "proposals": [
                open_project(oid), investigation(oid, "A", key="a")]}

        brain = Brain(decide)
        policy = self._policy(brain)
        self.driver = self._driver(policy, runner=QueueRunner())
        self.driver.step()
        self.assertEqual(len(self._states()), 1)
        self.assertEqual(len(brain.prompts), 2)
        self.assertIn("YOUR PREVIOUS ANSWER WAS NOT USABLE",
                      brain.prompts[1])
        exchange = json.loads(self._rows(
            "SELECT exchange FROM cycle_reasoning")[0]["exchange"])
        self.assertEqual(len(exchange["attempts"]), 2)
        self.assertIn("not one JSON object",
                      exchange["attempts"][0]["errors"][0])
        self.assertEqual(exchange["attempts"][1]["errors"], [])

    def test_identities_the_view_does_not_contain_are_refused(self):
        """A model cannot conjure a capability nobody offers, a task that
        does not exist, a specialist that does not exist or does not
        offer the work — and it cannot put a provider, a model or a
        specialist into a work requirement, or a provider or a model into
        an assignment. Each such proposal is dropped with the reason, and
        only what validated is proposed."""
        self._objective()

        def decide(projection, _call):
            oid = projection["objectives"][0]["objective_id"]
            reviewer = "sp_" + "0" * 32
            return {"reasoning": "mixed", "proposals": [
                open_project(oid),
                investigation(oid, "bad-capability",
                              capability="security_review",
                              role="auditor"),
                investigation(oid, "bad-role", role="builder"),
                investigation(oid, "bad-dependency",
                              depends_on_sequences=["sq_" + "9" * 32]),
                investigation(oid, "names-a-model", model="opus"),
                investigation(oid, "names-a-specialist",
                              specialist_id=outside_offering(
                                  projection)[0]),
                {"class": "REPAIR_TASK", "objective_id": oid,
                 "rationale": "x", "sequence_id": "sq_" + "9" * 32,
                 "capability": "research", "guidance": "repair"},
                {"class": "RETAIN_SPECIALIST", "objective_id": oid,
                 "rationale": "x", "specialist_id": "sp_nope",
                 "capability": "research", "role": "investigator",
                 "demonstrations": ["dem:x"]},
                {"class": "CREATE_TASK", "objective_id": oid,
                 "rationale": "x", "requirement": {
                     "objective": "Change something.",
                     "capability": "research", "role": "investigator",
                     "guidance": "Change it.",
                     "finalization_class": "SOURCE_MODIFYING"}},
                {"class": "LAUNCH_MISSILES", "objective_id": oid,
                 "rationale": "x"},
                {"class": "INVESTIGATE", "objective_id": "obj-other",
                 "rationale": "x", "requirement": {}},
                investigation(oid, "good", key="good"),
                select(oid, "good", reviewer),
                {"class": "ASSIGN_SPECIALIST", "objective_id": oid,
                 "rationale": "x", "workstream": "good",
                 "staffing": {"mode": "outside_specialist",
                              "specialist_id": outside_offering(
                                  projection)[0],
                              "model": "opus"}},
            ]}

        brain = Brain(decide)
        policy = self._policy(brain, max_attempts=1, max_proposals=20)
        self.driver = self._driver(policy, runner=QueueRunner())
        report = self.driver.step()
        admitted = [p for p in report["planning"]["proposals"]]
        self.assertEqual([(p["class"], p["status"]) for p in admitted],
                         [("OPEN_PROJECT", "APPLIED"),
                          ("INVESTIGATE", "ADMITTED")])
        errors = policy.last_exchange["attempts"][0]["errors"]
        self.assertEqual(len(errors), 12)
        text = "\n".join(errors)
        for needle in ("security_review", "staffable pairs",
                       "is not a workstream already in this project",
                       "a work requirement names model",
                       "a work requirement names specialist_id",
                       "is not a workstream of this objective",
                       "is not known to the organization",
                       "allowed_paths", "LAUNCH_MISSILES",
                       "not an active objective",
                       "cannot be selected as an outside specialist",
                       "never a provider or a model"):
            self.assertIn(needle, text)
        # Nothing of it reached the frozen record.
        for row in self.rt.db.conn.execute(
                "SELECT content_address FROM records").fetchall():
            payload = self.rt.store.get(
                row["content_address"]).decode("utf-8")
            for word in ("opus", "specialist_id", "sp_"):
                self.assertNotIn(word, payload)

    def test_completion_is_guarded_deterministically(self):
        """F-CE-1 at the production seam: PROJECT_COMPLETE with work
        still open, or alongside other work, never leaves the policy."""
        self.transport.scripts = {"investigator": []}  # A keeps running
        self._objective()

        def decide(projection, call):
            oid = projection["objectives"][0]["objective_id"]
            done = {"class": "PROJECT_COMPLETE", "objective_id": oid,
                    "rationale": "looks finished to me"}
            if call == 1:
                return {"reasoning": "", "proposals": [
                    open_project(oid), investigation(oid, "A", key="a"),
                    select(oid, "a", outside_offering(projection)[0]),
                    done]}
            return {"reasoning": "", "proposals": [done]}

        brain = Brain(decide)
        policy = self._policy(brain, max_attempts=1)
        self.driver = self._driver(policy, runner=QueueRunner())
        first = self.driver.step()
        self.assertEqual(
            [p["class"] for p in first["planning"]["proposals"]],
            ["OPEN_PROJECT", "INVESTIGATE", "ASSIGN_SPECIALIST"])
        self.assertIn("proposes other work",
                      policy.last_exchange["attempts"][0]["errors"][0])
        # Wake it again while A is still executing.
        with self.plan.transaction() as cur:
            self.plan.emit_workforce_event(
                cur, "test:nudge", "staffing_unavailable", "x", None,
                instant(self.clock.now()))
        self.driver.step()
        self.driver.step()
        self.assertEqual(len(self.plan.active_objectives()), 1)
        self.assertEqual(self._projects()[0]["status"], "ACTIVE")
        errors = [e for cycle in self._rows(
            "SELECT exchange FROM cycle_reasoning ORDER BY cycle_id")
            for attempt in json.loads(cycle["exchange"])["attempts"]
            for e in attempt.get("errors", [])]
        self.assertTrue(any("are not COMPLETED" in e for e in errors))

    def test_a_failing_reasoner_proposes_nothing_and_breaks_nothing(self):
        self._objective()

        def broken(_prompt):
            raise ReasonerError("the model is unavailable")

        policy = self._policy(broken)
        self.driver = self._driver(policy, runner=QueueRunner())
        report = self.driver.step()
        self.assertEqual(report["planning"]["proposals"], [])
        self.assertEqual(self._states(), {})
        exchange = policy.last_exchange
        self.assertEqual(len(exchange["attempts"]), 2)
        self.assertIn("unavailable", exchange["attempts"][0]["error"])
        self.assertTrue(self.driver.step()["quiet"])

    def test_the_permit_boundary_is_reported_without_reasoning(self):
        """Verified SOURCE_MODIFYING work at your permit boundary is a
        protocol fact, not an engineering decision: the policy reports
        WAIT_FOR_AUTHORITY itself, once, with no model call."""
        self.pool.register("software_implementation", "builder", "fake",
                           handle="alpha")
        effect = json.dumps({"target_ref": "refs/heads/main",
                             "expected_old": self.sha_old,
                             "new_value": self.sha_new}).encode("utf-8")
        self.transport.scripts = {"builder": [result(artifact=effect)]}
        self._objective()

        def decide(projection, _call):
            objective = projection["objectives"][0]
            oid = objective["objective_id"]
            if objective["project"]["workstreams"]:
                return {"reasoning": "in progress", "proposals": []}
            builder = outside_offering(
                projection, "software_implementation", "builder")[0]
            return {"reasoning": "", "proposals": [
                open_project(oid),
                {"class": "CREATE_TASK", "objective_id": oid,
                 "rationale": "the change itself", "workstream": "change",
                 "requirement": {
                     "objective": "Land the change.",
                     "capability": "software_implementation",
                     "role": "builder", "guidance": "Land the change.",
                     "finalization_class": "SOURCE_MODIFYING",
                     "allowed_paths": ["docs/architecture/chapter1"]}},
                select(oid, "change", builder)]}

        brain = Brain(decide)
        self.driver = self._driver(self._policy(brain),
                                   runner=QueueRunner())
        final = self.driver.run_until_stable()
        waiting = [p for p in final["waiting"]
                   if p["class"] == "WAIT_FOR_AUTHORITY"]
        self.assertEqual(len(waiting), 1)
        sequence_id = list(self._states())[0]
        self.assertEqual(json.loads(waiting[0]["body"])["subject_id"],
                         sequence_id)
        self.assertEqual(self._states()[sequence_id], "VERIFIED")
        reflex = [json.loads(c["exchange"]) for c in self._rows(
            "SELECT exchange FROM cycle_reasoning ORDER BY cycle_id")
            if json.loads(c["exchange"])["reflex"]]
        self.assertEqual(len(reflex), 1)
        self.assertFalse(reflex[0]["reasoned"])
        calls_before = len(brain.prompts)
        self.rt.sequencer.grant_permit(sequence_id, permitted_uses=1)
        self.driver.run_until_stable()
        self.assertEqual(self._states()[sequence_id], "COMPLETED")
        # The landing needed one more decision at most (completion), and
        # no second wait was ever reported.
        self.assertLessEqual(len(brain.prompts) - calls_before, 2)
        self.assertEqual(len(self._rows(
            "SELECT * FROM proposals WHERE class = 'WAIT_FOR_AUTHORITY'")),
            1)


class PolicyUnitTests(unittest.TestCase):
    def test_json_object_extraction(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(
            extract_json_object('Sure.\n```json\n{"a": 1}\n```\nDone.'),
            {"a": 1})
        self.assertEqual(
            extract_json_object('prefix {"a": {"b": 2}} suffix'),
            {"a": {"b": 2}})
        self.assertIsNone(extract_json_object("no object here"))
        self.assertIsNone(extract_json_object("[1, 2]"))
        self.assertIsNone(extract_json_object(None))

    def test_the_cli_reasoner_runs_headless_with_no_tools(self):
        """The production reasoner is the subscription CLI in print mode
        with every tool disabled and no session persisted; the prompt
        travels on stdin; an error envelope is an error, never an
        answer."""
        calls = []
        real_run = subprocess.run

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            envelope = {"type": "result", "is_error": False,
                        "result": '{"proposals": []}',
                        "session_id": "s-1", "num_turns": 1}
            return subprocess.CompletedProcess(argv, 0,
                                               json.dumps(envelope), "")

        policy_module.subprocess.run = fake_run
        try:
            reasoner = ClaudeCliReasoner("planner-model", cli="claude-x")
            self.assertEqual(reasoner("THE PROMPT"), '{"proposals": []}')
            argv, kwargs = calls[0]
            self.assertEqual(argv[:2], ["claude-x", "-p"])
            self.assertEqual(argv[argv.index("--tools") + 1], "")
            self.assertEqual(argv[argv.index("--model") + 1],
                             "planner-model")
            self.assertEqual(argv[argv.index("--output-format") + 1],
                             "json")
            self.assertIn("--no-session-persistence", argv)
            self.assertNotIn("--dangerously-skip-permissions", argv)
            self.assertEqual(kwargs["input"], "THE PROMPT")
            self.assertEqual(reasoner.last_meta["session_id"], "s-1")

            def failing(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 1, "", "boom")

            policy_module.subprocess.run = failing
            with self.assertRaises(ReasonerError):
                reasoner("x")

            def error_envelope(argv, **kwargs):
                return subprocess.CompletedProcess(argv, 0, json.dumps(
                    {"is_error": True, "result": "rate limited"}), "")

            policy_module.subprocess.run = error_envelope
            with self.assertRaises(ReasonerError):
                reasoner("x")
        finally:
            policy_module.subprocess.run = real_run
        with self.assertRaises(ReasonerError):
            ClaudeCliReasoner("")

    def _view(self, **extra):
        from intelligence import PlanningView
        return PlanningView(
            authority={"sequences_by_state": {}, "obligations": {},
                       "permits": []},
            snapshot={"entries": 3, "head_hash": "sha256:" + "0" * 64},
            events=[{"journal_seq": 3, "event_kind": "work_ready",
                     "subject_id": "sq_x", "facts": None}],
            findings=[], objectives=[
                {"objective_id": "obj-1", "text": "Do it.",
                 "constraints": json.dumps(["Stay READ_ONLY."]),
                 "consumed_in_cycle": None}],
            open_proposals=[], workstreams={}, **extra)

    def test_projection_renders_from_plain_view_data(self):
        view = self._view()
        projection = render_projection(view, {"repository_id": "autobot"})
        self.assertEqual(projection["objectives"][0]["constraints"],
                         ["Stay READ_ONLY."])
        self.assertTrue(projection["objectives"][0]["new"])
        self.assertEqual(projection["objectives"][0]["project"],
                         {"project_id": None, "workstreams": [],
                          "team": [], "needs_staffing": []})
        self.assertEqual(projection["taskforce"]["staffable"], [])
        self.assertEqual(
            projection["what_is_new"]["authority_events"],
            [{"seq": 3, "kind": "work_ready", "subject": "sq_x"}])
        json.dumps(projection)
        policy = ReasoningEngineerPolicy(lambda prompt: "{}")
        self.assertIs(ReasoningChiefEngineerPolicy,
                      ReasoningEngineerPolicy)  # the earlier name
        self.assertTrue(policy.needs_reasoning(view))  # a new objective
        view.objectives[0]["consumed_in_cycle"] = 1
        self.assertFalse(policy.needs_reasoning(view))  # routine progress
        view.workforce_events.append(
            {"event_id": 1, "event_kind": "seat_retained",
             "subject_id": "ts_x", "facts": None})
        self.assertFalse(policy.needs_reasoning(view))  # its own decision
        view.workforce_events.append(
            {"event_id": 2, "event_kind": "demonstration_recorded",
             "subject_id": "dem:x", "facts": None})
        self.assertTrue(policy.needs_reasoning(view))
        # A reasoning Engineer selects for itself: the standing rule
        # never does it for the model.
        self.assertEqual(policy.staff(view, []), [])
        prompt = policy.build_prompt(view)
        for needle in ("OPEN_PROJECT", "ASSIGN_SPECIALIST",
                       "RETAIN_SPECIALIST", "PROJECT MANAGER",
                       "provider, a model or a specialist",
                       "Never name a provider or a model anywhere",
                       "does NOT make a specialist a Taskforce member"):
            self.assertIn(needle, prompt)

    def test_a_workstream_waiting_for_staffing_is_reasoned_over(self):
        view = self._view(projects={"obj-1": {
            "project_id": "pj_x", "workstreams": [], "team": [],
            "needs_staffing": [{"sequence_id": "sq_x"}]}})
        view.objectives[0]["consumed_in_cycle"] = 1
        policy = ReasoningEngineerPolicy(lambda prompt: "{}")
        self.assertTrue(policy.needs_reasoning(view))

    def test_a_finished_project_and_a_resume_are_reasoned_over(self):
        policy = ReasoningEngineerPolicy(lambda prompt: "{}")
        for state, expected in (("EXECUTING", False), ("COMPLETED", True)):
            view = self._view(engineering={"obj-1": {"workstreams": [
                {"sequence_id": "sq_x", "state": state}]}})
            view.objectives[0]["consumed_in_cycle"] = 1
            self.assertEqual(policy.needs_reasoning(view), expected, state)
        view = self._view()
        view.objectives[0]["consumed_in_cycle"] = 1
        self.assertFalse(policy.needs_reasoning(view))
        view.workforce_events.append(
            {"event_id": 1, "event_kind": "operator_resumed",
             "subject_id": "obj-1", "facts": None})
        self.assertTrue(policy.needs_reasoning(view))

    def test_a_refused_proposal_is_not_work_to_complete(self):
        """A proposal refused at admission has no sequence: it neither
        completes nor keeps a project open. An admission still queued for
        you does keep it open, and no admitted work at all is not a
        finished project."""
        policy = ReasoningEngineerPolicy(lambda prompt: "{}")
        refused = {"sequence_id": None, "state": "REJECTED"}
        done = {"sequence_id": "sq_x", "state": "COMPLETED"}
        queued = {"sequence_id": None, "state": "QUEUED_FOR_YOU"}
        complete = {"class": "PROJECT_COMPLETE", "objective_id": "obj-1"}
        for streams, finished in (([done, refused], True),
                                  ([refused], False),
                                  ([done, queued], False)):
            view = self._view(
                engineering={"obj-1": {"workstreams": streams}})
            view.objectives[0]["consumed_in_cycle"] = 1
            self.assertEqual(policy.needs_reasoning(view), finished,
                             streams)
            errors = []
            kept = policy._completion_guard([complete], view, errors)
            self.assertEqual(kept == [complete], finished, streams)
            self.assertEqual(errors == [], finished, streams)

    def test_routing_never_reaches_the_projection(self):
        """A specialist's handle may spell a provider or a model. The
        Engineer is shown identities, offers and verified records — the
        route stays downstream of staffing."""
        taskforce = {
            "retained_seats": [{
                "seat_id": "ts_a", "specialist_id": "sp_a",
                "handle": "claude-opus", "capability": "research",
                "role": "investigator", "status": "RETAINED"}],
            "outside_specialists": [{
                "specialist_id": "sp_b", "handle": "claude-haiku",
                "is_member": False, "busy": 0, "available": True,
                "offers": [{"capability": "verification",
                            "role": "reviewer", "standing": "OUTSIDE",
                            "qualifying": 0, "not_qualifying": 0}]}],
            "retention_candidates": [{
                "specialist_id": "sp_b", "handle": "claude-haiku",
                "capability": "verification", "role": "reviewer",
                "qualifying_demonstrations": ["dem:1"]}]}
        projects = {"obj-1": {
            "project_id": "pj_x", "workstreams": [], "needs_staffing": [],
            "team": [{"specialist_id": "sp_b", "handle": "claude-haiku",
                      "joined_as": "OUTSIDE_SPECIALIST"}]}}
        projection = render_projection(
            self._view(taskforce=taskforce, projects=projects))
        text = json.dumps(projection)
        for hidden in ("handle", "claude-opus", "claude-haiku"):
            self.assertNotIn(hidden, text)
        self.assertEqual(
            projection["taskforce"]["staffable"],
            [{"capability": "research", "role": "investigator"},
             {"capability": "verification", "role": "reviewer"}])
        self.assertEqual(
            projection["taskforce"]["retention_candidates"][0][
                "specialist_id"], "sp_b")


if __name__ == "__main__":
    unittest.main()
