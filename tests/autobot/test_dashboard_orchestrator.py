"""The dashboard follows work the Orchestrator plans (the Opus pilots,
2026-11-22): tasks staffed directly by an Orchestrator node with no
project workstream, plans with nothing started, who plans and on what
model, sessions that are live versus past their session, what a Codex
session's own record says it is doing, and halts named in words.

The fixture is the agent view's plus an ORCHESTRATOR_ELEVATED objective
whose five tasks mirror obj-pilot-opus-v4 (no dependency edges, one
verification inconclusive) and a plan whose tasks have not started.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

import support  # noqa: F401  (puts src on sys.path)
from intelligence.dashboard import cli_stream
from intelligence.dashboard.readers import AutoSwarmReader

from test_dashboard_agent import AgentFixture, band, box, frame
from test_dashboard_compact import NOW, _insert

ORC = "ORCHESTRATOR_ELEVATED"
POLICY = "opus-orchestrator/v1"


def _epoch(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc).timestamp()


class OrchestratorFixture(AgentFixture):

    def __init__(self, root):
        super(OrchestratorFixture, self).__init__(root)
        self._fill_orchestration()
        self._fill_orchestration_authority()
        self._fill_orchestration_calls()

    def _fill_orchestration(self):
        c = self._open(self.intel_path)
        for oid, created, text, mode, route in (
                ("obj-orc", "2026-11-21T17:50:00Z",
                 "Design the migration of the authority store", ORC, POLICY),
                ("obj-plan", "2026-11-21T17:52:00Z",
                 "Plan a second migration", ORC, POLICY),
                # the Coordinator's direct path also works through a node
                ("obj-direct", "2026-11-21T17:53:00Z",
                 "Sync the luna catalog", "DIRECT", None)):
            _insert(c, "objectives", objective_id=oid, text=text,
                    authored_by="you", status="ACTIVE", created_utc=created)
            _insert(c, "coordinator_decisions", decision_id="cd_" + oid,
                    objective_id=oid, policy_id="production-coordinator/v1",
                    input_digest="d", elevation_mode=mode,
                    features=json.dumps({"engineering_design": True}),
                    route=route, created_utc=created)
            _insert(c, "orchestration_runs", orchestration_id="run_" + oid,
                    objective_id=oid, coordinator_decision_id="cd_" + oid,
                    mode=mode, status="ACTIVE", created_utc=created,
                    created_by="coordinator")

        def node(nid, run, key, status, completed=None):
            _insert(c, "orchestration_nodes", node_id=nid,
                    orchestration_id=run, node_key=key, status=status,
                    work_profile="RESEARCH", realization_kind="TASK_CONTRACT",
                    created_utc="2026-11-21T17:50:30Z",
                    completed_utc=completed)

        node("nd_a", "run_obj-orc", "Research the lock semantics", "EXECUTING")
        node("nd_b", "run_obj-orc", "Analyze the dependencies", "EXECUTING")
        node("nd_c", "run_obj-orc", "Verify the plan", "COMPLETED",
             "2026-11-21T17:58:00Z")
        node("nd_d", "run_obj-orc", "Write the final report", "PENDING")
        node("nd_e", "run_obj-orc", "Review the design", "EXECUTING")
        node("nd_p1", "run_obj-plan", "node-research", "PENDING")
        node("nd_p2", "run_obj-plan", "node-design", "PENDING")
        node("nd_dir", "run_obj-direct", "direct-work", "EXECUTING")

        def assignment(aid, sid, seat, path, nid, seq, attempt, call, status,
                       outcome=None, settled=None, objective="obj-orc"):
            _insert(c, "project_assignments", assignment_id=aid,
                    specialist_id=sid, seat_id=seat, sequence_id=seq,
                    attempt_ordinal=1, attempt_id=attempt, call_id=call,
                    objective_id=objective, capability="research",
                    role="investigator", provider="codex", model="gpt-6-luna",
                    selection_path=path, decided_by="TASKFORCE",
                    selected_reason="{}",
                    standing_at_assignment="OUTSIDE" if seat is None
                    else "ESTABLISHED", status=status, outcome=outcome,
                    selected_utc="2026-11-21T17:51:00Z", settled_utc=settled,
                    orchestration_node_id=nid)

        assignment("asg_a", "sp_out", None, "OUTSIDE_SPECIALIST", "nd_a",
                   "sq_a", "at_a", "call_a", "EXECUTING")
        assignment("asg_b", "sp_res", "ts_res", "RETAINED_SEAT", "nd_b",
                   "sq_b", "at_b", "call_b", "EXECUTING")
        assignment("asg_c", "sp_res", "ts_res", "RETAINED_SEAT", "nd_c",
                   "sq_c", "at_c", "call_c", "SETTLED", "VERIFIED_ACCEPTED",
                   "2026-11-21T17:58:00Z")
        assignment("asg_e", "sp_doc", None, "OUTSIDE_SPECIALIST", "nd_e",
                   "sq_e", "at_e", "call_e", "COLLECTED")
        assignment("asg_dir", "sp_res", "ts_res", "RETAINED_SEAT", "nd_dir",
                   "sq_dir", None, None, "SELECTED", objective="obj-direct")
        for n, (oids, classes, at) in enumerate((
                (["obj-orc"], ["CREATE_NODE"] * 5, "2026-11-21T17:50:20Z"),
                (["obj-plan"], ["CREATE_NODE"] * 2, "2026-11-21T17:52:20Z"),
                (["obj-orc"], ["SET_NODE_ROLE"], "2026-11-21T17:59:30Z")), 1):
            _insert(c, "orchestration_reasoning", orchestration_cycle_id=n,
                    input_digest="in%d" % n, policy_id=POLICY,
                    decisions=json.dumps([{"class": k} for k in classes]),
                    objectives=json.dumps(oids), recorded_utc=at)
        # as on 2026-11-22: the Orchestrator opened a project for obj-orc,
        # but its binding decisions named the node and project by its own
        # keys, so the applicator skipped them; the project stays empty
        _insert(c, "projects", project_id="pj_orc", objective_id="obj-orc",
                ordinal=1, title="Authority store migration",
                status="ACTIVE", opened_by="IMPLICIT",
                opened_utc="2026-11-21T17:50:00Z")
        _insert(c, "orchestration_reasoning", orchestration_cycle_id=4,
                input_digest="in4", policy_id=POLICY,
                decisions=json.dumps([{"class": "BIND_NODE_TO_PROJECT",
                                       "node": "node-research",
                                       "project": "proj-migration"}] * 2),
                application_results=json.dumps([{
                    "class": "BIND_NODE_TO_PROJECT", "status": "SKIPPED",
                    "note": "missing node_id or project_id"}] * 2),
                objectives=json.dumps(["obj-orc"]),
                recorded_utc="2026-11-21T17:50:25Z")
        # a seat released without a retention decision, and a specialist
        # the Taskforce released after its outside work
        _insert(c, "specialists", specialist_id="sp_rel", handle="rel-holder",
                provider="codex", model="gpt-6-luna",
                route=json.dumps({"agent": "codex"}), offers="[]",
                origin="outside_pool", status="ACTIVE", first_seen_utc=NOW,
                updated_utc=NOW)
        _insert(c, "taskforce_memberships", membership_id="tm_sp_rel",
                specialist_id="sp_rel", status="RELEASED", joined_utc=NOW,
                joined_by_decision="dec", updated_utc="2026-11-21T17:59:00Z")
        _insert(c, "taskforce_seats", seat_id="ts_rel",
                membership_id="tm_sp_rel", specialist_id="sp_rel",
                capability="architecture", role="reviewer", status="RELEASED",
                created_utc="2026-11-18T06:13:52.725Z",
                retained_since_utc="2026-11-18T06:13:52.725Z",
                updated_utc="2026-11-21T17:59:00Z")
        _insert(c, "workforce_events", event_key="wf_rel",
                event_kind="outside_specialist_released", subject_id="sp_out",
                facts=json.dumps({"capability": "research"}),
                recorded_utc="2026-11-21T17:59:58Z")
        _insert(c, "workforce_events", event_key="wf_seat",
                event_kind="seat_retained", subject_id="ts_res",
                facts=json.dumps({"capability": "research"}),
                recorded_utc="2026-11-21T17:59:57Z")
        c.commit()
        c.close()

    def _fill_orchestration_authority(self):
        c = self._open(self.auth_path)
        for sid, state, updated in (
                ("sq_a", "EXECUTING", "2026-11-21T17:51:10Z"),
                ("sq_b", "EXECUTING", "2026-11-21T17:51:11Z"),
                ("sq_c", "COMPLETED", "2026-11-21T17:58:00Z"),
                ("sq_e", "HALTED", "2026-11-21T17:57:00Z"),
                ("sq_dir", "READY", "2026-11-21T17:53:10Z")):
            _insert(c, "sequences", sequence_id=sid,
                    record_hash="sha256:r_" + sid, requirement_digest="rd",
                    contract_digest="sha256:c_" + sid, state=state, version=1,
                    fence=0, created_utc="2026-11-21T17:51:00Z",
                    updated_utc=updated)
        for aid, sid, state, updated in (
                ("at_a", "sq_a", "RUNNING", "2026-11-21T17:51:20Z"),
                ("at_b", "sq_b", "RUNNING", "2026-11-21T17:51:21Z"),
                ("at_c", "sq_c", "VERIFIED", "2026-11-21T17:58:00Z"),
                ("at_e", "sq_e", "EVIDENCE_COMPLETE",
                 "2026-11-21T17:57:00Z")):
            _insert(c, "attempts", attempt_id=aid, sequence_id=sid,
                    state=state, version=3, fence=1,
                    attempt_contract_hash="sha256:ac_" + aid,
                    created_utc="2026-11-21T17:51:00Z", updated_utc=updated)
        for aid, call, native in (("at_a", "call_a", "nat-aaaa"),
                                  ("at_b", "call_b", "nat-bbbb"),
                                  ("at_e", "call_e", "nat-eeee")):
            _insert(c, "attempt_calls", attempt_id=aid, role="investigator",
                    provider="codex", call_id=call,
                    projection_address="sha256:p", native_session_id=native,
                    created_utc="2026-11-21T17:51:00Z",
                    updated_utc="2026-11-21T17:51:20Z")
        journal = [
            {"machine": "attempt", "subject_id": "at_a",
             "from_state": "DISPATCHED", "to_state": "RUNNING",
             "rule": "t-a03", "recorded_utc": "2026-11-21T17:51:20.000Z"},
            {"machine": "sequence", "subject_id": "sq_e",
             "from_state": "VERIFYING", "to_state": "HALTED",
             "rule": "t-s12", "recorded_utc": "2026-11-21T17:57:00.000Z",
             "facts": {"version": 4, "fence": 1, "reason": "unknown"}},
        ]
        for n in range(3):  # a session's witnesses, admitted one by one
            journal.append(
                {"machine": "attempt_witness", "subject_id": "wit_%d" % n,
                 "to_state": "ADMITTED", "rule": "ch2:admit-witness",
                 "recorded_utc": "2026-11-21T17:59:4%d.000Z" % n,
                 "facts": {"attempt_id": "at_a", "sequence_id": "sq_a"}})
        for n, payload in enumerate(journal, 100):
            _insert(c, "transition_journal", entry_hash="sha256:e%d" % n,
                    payload=json.dumps(payload),
                    appended_utc=payload["recorded_utc"])
        c.commit()
        c.close()

    def _fill_orchestration_calls(self):
        for call, pid, started, native in (
                ("call_a", 2001, "2026-11-21T17:51:15+00:00", "nat-aaaa"),
                ("call_b", 2002, "2026-11-21T17:51:16+00:00", "nat-bbbb"),
                ("call_e", 2005, "2026-11-21T17:51:17+00:00", "nat-eeee")):
            d = self.home / "calls" / call
            d.mkdir(parents=True)
            (d / "handle.json").write_text(json.dumps({
                "pid": pid, "started_at": started,
                "native_session_id": native, "phase": "execution"}),
                encoding="utf-8")
            (d / "manifest.json").write_text(json.dumps({
                "call_id": call, "constraints": {
                    "finalization_class": "READ_ONLY"},
                "provider_assignment": {"provider": "codex",
                                        "model": "gpt-6-luna"}}),
                encoding="utf-8")
            (d / "stdout.log").write_text("", encoding="utf-8")
        for call, ended in (("call_b", "2026-11-21T17:58:30Z"),
                            ("call_e", "2026-11-21T17:56:40Z")):
            result = self.home / "calls" / call / "result.json"
            result.write_text("{}", encoding="utf-8")
            os.utime(str(result), (_epoch(ended), _epoch(ended)))
        # the live session's own record: Codex writes nothing to stdout
        # until it ends
        folder = self.codex_home / "sessions" / "2026" / "11" / "21"
        folder.mkdir(parents=True)
        self.rollout = folder / "rollout-2026-11-21T17-51-15-nat-aaaa.jsonl"
        events = [
            {"timestamp": "2026-11-21T17:51:16.000Z", "type": "session_meta",
             "payload": {"id": "nat-aaaa"}},
            {"timestamp": "2026-11-21T17:52:00.000Z", "type": "response_item",
             "payload": {"type": "reasoning", "summary": [
                 {"type": "summary_text",
                  "text": "**Reading the schema**\n\nI will read it."}]}}]
        for n in range(4):
            events.append({"timestamp": "2026-11-21T17:5%d:10.000Z" % (3 + n),
                           "type": "response_item",
                           "payload": {"type": "custom_tool_call",
                                       "name": "exec", "input": "rg x"}})
            events.append({"timestamp": "2026-11-21T17:5%d:20.000Z" % (3 + n),
                           "type": "response_item",
                           "payload": {"type": "custom_tool_call_output"}})
        events.append({"timestamp": "2026-11-21T17:59:55.000Z",
                       "type": "event_msg", "payload": {"type": "token_count"}})
        self.rollout.write_text("".join(json.dumps(e) + "\n" for e in events),
                                encoding="utf-8")


class OrchestratorWorkTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = OrchestratorFixture(cls._tmp.name)
        cls.snap = cls.fx.snapshot()
        # tall enough that no band cuts a row these tests read
        cls.lines = frame(cls.snap, 146, 80)
        cls.text = "\n".join(cls.lines)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def assertIn_any(self, needle, rows):
        self.assertTrue(any(needle in r for r in rows),
                        "%r not in:\n%s" % (needle, "\n".join(rows)))

    # --- who plans, and what each run's work is -------------------------

    def test_orchestrator_box_names_the_planner_and_its_model(self):
        rows = box(self.lines, "ORCHESTRATOR")
        self.assertIn_any("plans on claude-opus-4-6[1m] · opus-orchestrator/v1"
                          " · 4 cycles", rows)
        self.assertIn_any("last 17:59:30 (30s ago)", rows)
        self.assertIn_any("7 runs ACTIVE → 1 working · 2 stuck · 2 waiting"
                          " · 2 done", rows)

    def test_a_runs_tasks_are_named_by_who_made_them(self):
        # the Coordinator's direct work is not an Orchestrator plan
        rows = band(self.lines, "DAG")
        self.assertIn_any("◆ obj-direct  0/1 done · direct work, no project",
                          rows)
        self.assertIn_any("○ direct-work", rows)
        self.assertIn("3 runs without a project", self.text)

    def test_decisions_the_applicator_skipped_are_counted(self):
        rows = box(self.lines, "ORCHESTRATOR")
        self.assertIn_any("not applied: BIND_NODE_TO_PROJECT ×2", rows)
        text = "\n".join(cli_stream.render(self.snap, width=146))
        self.assertIn("not applied: BIND_NODE_TO_PROJECT x2", text)

    def test_an_empty_project_beside_its_unbound_plan_is_one_heading(self):
        rows = band(self.lines, "DAG")
        heads = [r for r in rows if r.startswith("◆ obj-orc ")]
        self.assertEqual(len(heads), 1, rows)
        self.assertIn("Orchestrator plan, not bound to pj_orc", heads[0])
        self.assertFalse(any("no workstreams yet" in r for r in rows), rows)

    def test_a_released_seat_dates_from_its_release(self):
        rows = box(self.lines, "TASKFORCE")
        # its membership is released too, so the header lists it outside
        self.assertIn_any("outside: outsider, rel-holder", rows)
        seat = next(r for r in rows if "rel-holder" in r and "/" in r)
        self.assertIn("RELEASED 17:59", seat)
        text = "\n".join(cli_stream.render(self.snap, width=146))
        self.assertIn("RELEASED 17:59", text)

    def test_a_workforce_event_names_its_specialist(self):
        rows = band(self.lines, "ACTIVITY")
        self.assertIn_any("⛊ outside specialist released outsider", rows)
        self.assertIn_any("⛊ seat retained res-holder's research seat", rows)

    def test_work_staffed_by_a_node_counts_as_the_objectives_work(self):
        rows = box(self.lines, "ORCHESTRATOR")
        orc = next(r for r in rows if "obj-orc" in r)
        # one task halted, so the run is stuck, not "waiting · no work"
        self.assertIn("stuck · HALTED 17:57:00", orc)
        plan = next(r for r in rows if "obj-plan" in r)
        self.assertIn("waiting · 2 tasks planned, none started", plan)
        o = {x.objective_id: x for x in self.snap.objectives}
        self.assertEqual(o["obj-orc"].work_state, "HALTED")
        self.assertEqual(o["obj-orc"].work_note, "Review the design")

    # --- the work graph ---------------------------------------------------

    def test_dag_draws_the_plans_tasks_by_their_keys(self):
        rows = band(self.lines, "DAG")
        self.assertIn_any("◆ obj-orc  1/5 done · Orchestrator plan, not bound"
                          " to pj_orc opened 17:50:00", rows)
        research = next(r for r in rows if "Research the lock semantics" in r)
        self.assertIn("EXECUTING", research)
        self.assertIn("outsider · outside", research)
        self.assertIn("attempt RUNNING", research)
        verify = next(r for r in rows if "Verify the plan" in r)
        self.assertIn("COMPLETED", verify)
        self.assertIn("accepted", verify)
        report = next(r for r in rows if "Write the final report" in r)
        self.assertIn("PLANNED", report)
        self.assertIn("not started", report)
        self.assertIn_any("no dependencies recorded between these 5 tasks",
                          rows)

    def test_a_t_s12_halt_is_named_in_words(self):
        rows = band(self.lines, "DAG")
        review = next(r for r in rows if "Review the design" in r)
        self.assertIn("HALTED", review)
        self.assertIn("t-s12 verification inconclusive", review)
        a1 = [a for a in self.snap.attention if a.rule == "A1"
              and a.subject == "Review the design"]
        self.assertEqual(a1[0].detail, "t-s12 · verification inconclusive")

    def test_a_plan_with_nothing_started_is_one_row(self):
        rows = band(self.lines, "DAG")
        self.assertIn_any("○ 2 tasks planned, none started: node-research,"
                          " node-design", rows)

    # --- what executes ----------------------------------------------------

    def test_executing_lists_live_sessions_and_counts_the_rest(self):
        self.assertIn("━━ EXECUTING ━ 2 running · 1 ended, not collected · 1"
                      " past the session (EVIDENCE_COMPLETE 1) ", self.text)
        rows = band(self.lines, "EXECUTING")
        self.assertFalse(any("Review the design" in r for r in rows), rows)

    def test_a_codex_session_shows_what_its_own_record_says(self):
        rows = band(self.lines, "EXECUTING")
        at = next(i for i, r in enumerate(rows)
                  if "Research the lock semantics" in r)
        self.assertTrue(rows[at].startswith(
            "▶ Research the lock semantics  luna-6-max · outsider (outside)"),
            rows[at])
        self.assertIn("RUNNING since 17:51:20 · session 8m45s", rows[at])
        # at 146 columns the session's activity continues on the next row
        self.assertIn("4 tool calls · Reading the schema · last 5s ago",
                      " ".join(rows[at:at + 2]))
        one = band(frame(self.snap, 230, 53), "EXECUTING")
        self.assertIn_any("session 8m45s · 4 tool calls · Reading the schema"
                          " · last 5s ago · pid 2001", one)

    def test_a_session_that_wrote_its_result_has_a_stopped_clock(self):
        rows = band(self.lines, "EXECUTING")
        ended = next(r for r in rows if "Analyze the dependencies" in r)
        self.assertTrue(ended.startswith("■ "), ended)
        self.assertIn("session ended 17:58:30 after 7m14s", " ".join(rows))
        self.assertIn("result not collected yet", " ".join(rows))

    def test_working_now_includes_members_outside_their_seats(self):
        rows = box(self.lines, "TASKFORCE")
        joined = " ".join(rows)
        self.assertIn("outsider → Research the lock semantics (outside)",
                      joined)
        self.assertIn("res-holder → w1", joined)

    # --- activity ---------------------------------------------------------

    def test_activity_names_task_work_and_folds_repeats(self):
        rows = band(self.lines, "ACTIVITY")
        self.assertIn_any("17:59:42.000 ⬡ Research the lock semantics witness"
                          " new -> ADMITTED ×3", rows)
        self.assertIn_any("⬡ Review the design VERIFYING -> HALTED [t-s12]",
                          rows)
        self.assertIn_any("⬡ Research the lock semantics attempt DISPATCHED"
                          " -> RUNNING", rows)

    # --- the compact view ---------------------------------------------------

    def test_compact_view_counts_sessions_and_names_the_planner(self):
        text = "\n".join(cli_stream.render(self.snap, width=146))
        self.assertIn("══ SESSIONS  2 running · 1 ended, not collected · 1 past"
                      " the session (EVIDENCE_COMPLETE 1)", text)
        self.assertIn("planned by opus-orchestrator/v1 · on claude-opus-4-6[1m]"
                      " · 4 reasoning cycles, 10 decisions · last 17:59:30",
                      text)
        self.assertIn("4 tool calls · Reading the schema · last 5s ago", text)
        self.assertIn("session ended 17:58:30 after 7m14s", text)

    # --- the widest terminals ---------------------------------------------

    def test_at_230_columns_every_live_session_fits(self):
        lines = frame(self.snap, 230, 53)
        rows = band(lines, "EXECUTING")
        self.assertFalse(any("more rows" in r for r in rows), rows)
        for key in ("w1", "Research the lock semantics",
                    "Analyze the dependencies"):
            self.assertIn_any(key, rows)


class CodexActivityReaderTests(unittest.TestCase):

    def test_the_rollout_is_read_incrementally(self):
        tmp = tempfile.mkdtemp()
        try:
            fx = OrchestratorFixture(tmp)
            reader = fx.autoswarm()
            first = reader.codex_activity("nat-aaaa",
                                          "2026-11-21T17:51:15+00:00")
            self.assertEqual((first["events"], first["tool_calls"]), (11, 4))
            self.assertEqual(first["doing"], "Reading the schema")
            with open(str(fx.rollout), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "timestamp": "2026-11-21T18:00:05.000Z",
                    "type": "response_item",
                    "payload": {"type": "function_call"}}) + "\n")
                fh.write('{"timestamp": "2026-11-21T18:00:06.000Z", "type": '
                         '"event_msg", "payload": {"type": "task_com')
            later = reader.codex_activity("nat-aaaa",
                                          "2026-11-21T17:51:15+00:00")
            # the half-written last line waits for the rest of it
            self.assertEqual((later["events"], later["tool_calls"]), (12, 5))
            self.assertEqual(later["last_utc"], "2026-11-21T18:00:05.000Z")
            self.assertFalse(later["finished"])
            with open(str(fx.rollout), "a", encoding="utf-8") as fh:
                fh.write('plete"}}\n')
            done = reader.codex_activity("nat-aaaa",
                                         "2026-11-21T17:51:15+00:00")
            self.assertTrue(done["finished"])
            self.assertIsNone(reader.codex_activity(
                "nat-none", "2026-11-21T17:51:15+00:00"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_missing_rollout_is_looked_for_again_later(self):
        tmp = tempfile.mkdtemp()
        try:
            reader = AutoSwarmReader(None, codex_home=tmp)
            self.assertIsNone(reader.codex_activity(
                "nat-late", "2026-11-21T17:51:15+00:00"))
            folder = os.path.join(tmp, "sessions", "2026", "11", "21")
            os.makedirs(folder)
            with open(os.path.join(folder, "rollout-x-nat-late.jsonl"), "w",
                      encoding="utf-8") as fh:
                fh.write('{"timestamp": "2026-11-21T17:52:00.000Z",'
                         ' "type": "session_meta", "payload": {}}\n')
            # within 10 s the miss is remembered; after it, found
            self.assertIsNone(reader.codex_activity(
                "nat-late", "2026-11-21T17:51:15+00:00"))
            path, looked = reader._rollouts["nat-late"]
            reader._rollouts["nat-late"] = (path, looked - 11)
            found = reader.codex_activity("nat-late",
                                          "2026-11-21T17:51:15+00:00")
            self.assertEqual(found["events"], 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
