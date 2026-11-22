"""The compact dashboard (python -m intelligence.dashboard --compact) shows
what the stores record: objective times and work state, the sessions the
authority plane holds in flight, what each orchestration run's objective
really is, seat records split by whether work went through the seat, and
assignment outcomes.

The fixture databases are created by the real schema owners (PlanStore and
autobot.Database), then filled with rows that mirror production on
2026-11-21: a halted pilot, a live investigation, completed objectives
whose runs were never closed, and seats with demonstrations recorded both
through and outside them.
"""

import dataclasses
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  (puts src on sys.path)
from autobot import Database
from intelligence import PlanStore
from intelligence.dashboard import cli_stream
from intelligence.dashboard.model import build_snapshot
from intelligence.dashboard.readers import (
    AuthorityReader, AutoSwarmReader, ConfigReader, IntelligenceReader)

NOW = "2026-11-21T18:00:00Z"
ROUTE = json.dumps({"agent": "codex"})

# The production routing policy's shape (config.toml): the Engineer reasons
# on the planner model; every specialist session runs on the default route.
CONFIG = """[models]
planner = "claude-opus-4-6[1m]"

[routing]
default = "luna-6-max"
escalation = "opus-4-6-1m"

[routing.routes.luna-6-max]
agent = "codex"
model = "gpt-6-luna"
effort = "max"

[routing.routes.opus-4-6-1m]
agent = "claude-code"
model = "claude-opus-4-6[1m]"

[routing.routes.haiku]
agent = "claude-code"
model = "haiku"
"""


def _insert(conn, table, **cols):
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    conn.execute("INSERT INTO %s (%s) VALUES (%s)" % (table, names, marks),
                 tuple(cols.values()))


class CompactFixture(object):
    """Builds intelligence.db, autobot.db and an AutoSwarm home in a
    temporary directory."""

    def __init__(self, root):
        self.root = Path(root)
        self.intel_path = self.root / "intelligence.db"
        self.auth_path = self.root / "autobot.db"
        self.home = self.root / "autoswarm-home"
        # Codex rollouts are read from here, never from the real ~/.codex
        self.codex_home = self.root / "codex-home"
        PlanStore(self.intel_path).close()
        Database(self.auth_path).close()
        (self.root / "config.toml").write_text(CONFIG, encoding="utf-8")
        self._fill_intelligence()
        self._fill_authority()
        self._fill_autoswarm()

    @staticmethod
    def _open(path):
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=DELETE")
        return conn

    def _fill_intelligence(self):
        c = self._open(self.intel_path)
        _insert(c, "cycles", cycle_id=900, started_utc=NOW,
                snapshot_entries=0, status="CLOSED")

        def objective(oid, status, created, completed=None, text=None):
            _insert(c, "objectives", objective_id=oid,
                    text=text or "text of %s" % oid, authored_by="you",
                    status=status, created_utc=created,
                    completed_utc=completed)

        objective("obj-halted", "ACTIVE", "2026-11-20T17:41:40.633Z",
                  text="Add a one-line version marker")
        objective("obj-live", "ACTIVE", "2026-11-21T17:57:51.599Z",
                  text="Investigate the current pricing tiers")
        objective("obj-done-1", "COMPLETE", "2026-11-21T17:13:53.058Z",
                  "2026-11-21T17:55:49.605Z")
        objective("obj-done-2", "COMPLETE", "2026-11-21T15:54:55.843Z",
                  "2026-11-21T16:04:02.695Z")
        objective("obj-done-3", "COMPLETE", "2026-11-21T15:25:38.367Z",
                  "2026-11-21T15:49:14.594Z")
        objective("obj-done-4", "COMPLETE", "2026-11-18T06:12:38.265Z",
                  "2026-11-18T06:13:52.725Z")
        objective("obj-done-5", "COMPLETE", "2026-11-17T14:48:55.239Z",
                  "2026-11-18T06:08:46.141Z")

        def project(pid, oid, status, opened):
            _insert(c, "projects", project_id=pid, objective_id=oid,
                    ordinal=1, title="project %s" % pid, status=status,
                    opened_by="PROJECT_MANAGER", opened_utc=opened)

        project("pj_halt", "obj-halted", "ACTIVE", "2026-11-20T17:41:40.674Z")
        project("pj_live", "obj-live", "ACTIVE", "2026-11-21T17:57:51.632Z")
        project("pj_done", "obj-done-1", "COMPLETED", "2026-11-21T17:13:53Z")

        def workstream(wid, pid, key, sequence_id):
            proposal_id = "prop:%s" % wid
            _insert(c, "proposals", proposal_id=proposal_id, cycle_id=900,
                    **{"class": "CREATE_TASK"}, status="ADMITTED",
                    rationale="r", body="{}", sequence_id=sequence_id,
                    snapshot_entries=0, created_utc=NOW)
            _insert(c, "project_workstreams", workstream_id=wid,
                    project_id=pid, workstream_key=key,
                    proposal_id=proposal_id, created_utc=NOW)

        workstream("ws_h1", "pj_halt", "append-marker", "sq_halt")
        workstream("ws_h2", "pj_halt", "review-marker", "sq_blocked")
        workstream("ws_l1", "pj_live", "w1", "sq_live")
        workstream("ws_d1", "pj_done", "review-x", "sq_done")
        workstream("ws_d2", "pj_done", "investigate-authority-model", "sq_inv")

        def specialist(sid, handle):
            _insert(c, "specialists", specialist_id=sid, handle=handle,
                    provider="codex", model="gpt-6-luna", route=ROUTE,
                    offers="[]", origin="outside_pool", status="ACTIVE",
                    first_seen_utc=NOW, updated_utc=NOW)

        specialist("sp_res", "res-holder")
        specialist("sp_doc", "doc-holder")
        specialist("sp_out", "outsider")
        for sid in ("sp_res", "sp_doc"):
            _insert(c, "taskforce_memberships", membership_id="tm_" + sid,
                    specialist_id=sid, status="ACTIVE", joined_utc=NOW,
                    joined_by_decision="dec", updated_utc=NOW)
        _insert(c, "taskforce_seats", seat_id="ts_res",
                membership_id="tm_sp_res", specialist_id="sp_res",
                capability="research", role="investigator",
                status="ESTABLISHED", created_utc="2026-11-18T06:13:52.725Z",
                retained_since_utc="2026-11-18T06:13:52.725Z",
                updated_utc=NOW)
        _insert(c, "taskforce_seats", seat_id="ts_doc",
                membership_id="tm_sp_doc", specialist_id="sp_doc",
                capability="documentation", role="builder",
                status="ESTABLISHED", created_utc="2026-11-18T14:06:46.973Z",
                retained_since_utc="2026-11-18T14:06:46.973Z",
                updated_utc=NOW)

        def retention(did, kind, sid, cap, role, at):
            _insert(c, "retention_decisions", decision_id=did, kind=kind,
                    specialist_id=sid, capability=cap, role=role,
                    demonstration_ids="[]", rationale="r",
                    decided_by="taskforce", decided_utc=at)

        retention("dec1", "RETAIN", "sp_res", "research", "investigator",
                  "2026-11-18T06:13:52.725Z")
        retention("dec2", "ESTABLISH", "sp_res", "research", "investigator",
                  "2026-11-18T13:57:53.614Z")

        def demonstration(did, seat_id, outcome, at):
            _insert(c, "capability_demonstrations", demonstration_id=did,
                    specialist_id="sp_res", seat_id=seat_id,
                    assignment_id="asg_" + did, standing="ESTABLISHED",
                    capability="research", role="investigator",
                    environment="{}", system_domains="[]",
                    sequence_id="sq_x", attempt_id="at_x",
                    task_contract_id="tc", record_hash="sha256:r",
                    contract_digest="sha256:c", verification_refs="[]",
                    evidence_refs="[]", criteria="{}", outcome=outcome,
                    observed_findings="[]", recorded_utc=at)

        demonstration("d0", None, "QUALIFYING", "2026-11-18T06:13:52.016Z")
        demonstration("d1", "ts_res", "NOT_QUALIFYING",
                      "2026-11-18T06:18:31.865Z")
        demonstration("d2", "ts_res", "QUALIFYING", "2026-11-18T13:26:04Z")
        demonstration("d3", "ts_res", "QUALIFYING", "2026-11-18T13:57:53Z")

        def assignment(aid, sid, seat, sequence_id, wid, status, outcome,
                       selected, settled=None, attempt_id=None, call_id=None,
                       objective_id=None, path="RETAINED_SEAT",
                       capability="research", role="investigator"):
            _insert(c, "project_assignments", assignment_id=aid,
                    specialist_id=sid, seat_id=seat, sequence_id=sequence_id,
                    workstream_id=wid, attempt_ordinal=1,
                    attempt_id=attempt_id, call_id=call_id,
                    objective_id=objective_id, capability=capability,
                    role=role, provider="codex", model="gpt-6-luna",
                    selection_path=path, decided_by="TASKFORCE",
                    selected_reason="{}", standing_at_assignment="ESTABLISHED",
                    status=status, outcome=outcome, selected_utc=selected,
                    settled_utc=settled)

        assignment("asg_live", "sp_res", "ts_res", "sq_live", "ws_l1",
                   "EXECUTING", None, "2026-11-21T17:58:24.902Z",
                   attempt_id="at_live", call_id="call_live",
                   objective_id="obj-live")
        assignment("asg_acc", "sp_out", None, "sq_done", "ws_d1", "SETTLED",
                   "VERIFIED_ACCEPTED", "2026-11-21T17:41:26.103Z",
                   settled="2026-11-21T17:49:05.557Z",
                   path="OUTSIDE_SPECIALIST", capability="architecture",
                   role="reviewer")
        assignment("asg_fail", "sp_doc", "ts_doc", "sq_halt", "ws_h1",
                   "SETTLED", "ATTEMPT_FAILED", "2026-11-20T17:42:20.069Z",
                   settled="2026-11-20T18:25:29.047Z",
                   capability="documentation", role="builder")
        _insert(c, "project_team", project_id="pj_halt",
                specialist_id="sp_doc", joined_as="RETAINED_MEMBER",
                status="ACTIVE", joined_utc=NOW, first_assignment_id="asg_fail")

        def run(rid, oid, mode, created, nodes):
            _insert(c, "coordinator_decisions", decision_id="cd_" + rid,
                    objective_id=oid, policy_id="production-coordinator/v1",
                    input_digest="d", elevation_mode=mode,
                    features=json.dumps({"engineering_design": True,
                                         "organizational_complexity": True}),
                    created_utc=created)
            _insert(c, "orchestration_runs", orchestration_id=rid,
                    objective_id=oid, coordinator_decision_id="cd_" + rid,
                    mode=mode, status="ACTIVE", created_utc=created,
                    created_by="coordinator")
            for key, status in nodes:
                _insert(c, "orchestration_nodes",
                        node_id="nd_%s_%s" % (rid, key), orchestration_id=rid,
                        node_key=key, status=status, created_utc=created)

        run("run_done1", "obj-done-1", "ENGINEER_ELEVATED",
            "2026-11-21T17:13:53.093Z",
            [("engineer-reasoning", "COMPLETED")])
        run("run_halt", "obj-halted", "ENGINEER_ELEVATED",
            "2026-11-21T15:25:38.402Z",
            [("engineer-reasoning", "COMPLETED")])
        run("run_live", "obj-live", "DIRECT", "2026-11-21T17:57:51.632Z",
            [("direct-work", "EXECUTING")])
        run("run_orc", "obj-done-3", "ORCHESTRATOR_ELEVATED",
            "2026-11-21T15:25:38.402Z", [])
        c.commit()
        c.close()

    def _fill_authority(self):
        c = self._open(self.auth_path)

        def sequence(sid, state, updated):
            _insert(c, "sequences", sequence_id=sid,
                    record_hash="sha256:r_" + sid, requirement_digest="rd",
                    contract_digest="sha256:c_" + sid, state=state,
                    version=1, fence=0, created_utc="2026-11-20T17:42:19Z",
                    updated_utc=updated)

        sequence("sq_halt", "HALTED", "2026-11-20T18:25:48.475Z")
        sequence("sq_blocked", "BLOCKED", "2026-11-20T17:42:20.053Z")
        sequence("sq_live", "EXECUTING", "2026-11-21T17:58:25.012Z")
        sequence("sq_done", "COMPLETED", "2026-11-21T17:55:49Z")
        sequence("sq_super", "SUPERSEDED", "2026-11-19T00:00:00Z")
        sequence("sq_inv", "COMPLETED", "2026-11-21T17:24:10.973Z")
        _insert(c, "attempts", attempt_id="at_live", sequence_id="sq_live",
                state="RUNNING", version=3, fence=1,
                attempt_contract_hash="sha256:ac",
                created_utc="2026-11-21T17:58:25.012Z",
                updated_utc="2026-11-21T17:58:32.268Z")
        _insert(c, "attempt_calls", attempt_id="at_live", role="investigator",
                provider="codex", call_id="call_live",
                projection_address="sha256:p",
                native_session_id="01a1ff42-0000",
                created_utc="2026-11-21T17:58:25Z",
                updated_utc="2026-11-21T17:58:32Z")
        journal = [
            {"machine": "sequence", "subject_id": "sq_halt",
             "from_state": "EXECUTING", "to_state": "HALTED",
             "rule": "t-s45", "recorded_utc": "2026-11-20T18:25:48.475Z",
             "facts": {"reason": "collector classification INTEGRITY_FAILED",
                       "completeness": "INTEGRITY_FAILED"}},
            {"machine": "sequence", "subject_id": "sq_inv",
             "from_state": "DEPENDENCIES_RELEASED", "to_state": "COMPLETED",
             "rule": "t-s42", "recorded_utc": "2026-11-21T17:24:10.973Z"},
            {"machine": "attempt", "subject_id": "at_live",
             "from_state": "DISPATCHED", "to_state": "RUNNING",
             "rule": "t-a03", "recorded_utc": "2026-11-21T17:58:32.268Z"},
            # admissions carry no from_state (D-37)
            {"machine": "attempt_receipt", "subject_id": "rcp_live01",
             "to_state": "ADMITTED", "rule": "ch3:admit-execution-receipt",
             "recorded_utc": "2026-11-21T17:58:40.100Z",
             "facts": {"attempt_id": "at_live", "sequence_id": "sq_live"}},
            {"machine": "registry_objects",
             "subject_id": "sha256:abcdef0123456789", "to_state": "ADMITTED",
             "rule": "t-r01", "recorded_utc": "2026-11-21T17:58:45.000Z"},
        ]
        for n, payload in enumerate(journal, 1):
            _insert(c, "transition_journal", entry_hash="sha256:e%d" % n,
                    payload=json.dumps(payload),
                    appended_utc=payload["recorded_utc"])
        c.commit()
        c.close()

    def _fill_autoswarm(self):
        call = self.home / "calls" / "call_live"
        call.mkdir(parents=True)
        (call / "handle.json").write_text(json.dumps({
            "pid": 18800, "started_at": "2026-11-21T17:58:27.103885+00:00",
            "native_session_id": "01a1ff42-0000", "phase": "execution",
            "child_pid": 4540}), encoding="utf-8")
        (call / "manifest.json").write_text(json.dumps({
            "attempt_id": "at_live", "call_id": "call_live",
            "sequence_id": "sq_live", "capability": "research",
            "role": "investigator",
            "constraints": {"finalization_class": "READ_ONLY",
                            "source_revision": "a5fc1ab"},
            "provider_assignment": {"provider": "codex",
                                    "model": "gpt-6-luna",
                                    "selected_by": "agent_factory"}}),
            encoding="utf-8")
        (call / "stdout.log").write_text("", encoding="utf-8")

    def autoswarm(self):
        return AutoSwarmReader(self.home, codex_home=self.codex_home)

    def snapshot(self):
        intel = IntelligenceReader(str(self.intel_path))
        auth = AuthorityReader(str(self.auth_path))
        try:
            snap = build_snapshot(intel, auth,
                                  config_reader=ConfigReader(self.root),
                                  autoswarm_reader=self.autoswarm(),
                                  now=NOW)
        finally:
            intel.close()
            auth.close()
        return dataclasses.replace(snap, captured_utc=NOW)


class CompactViewTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = CompactFixture(cls._tmp.name)
        cls.snap = cls.fx.snapshot()
        cls.lines = cli_stream.render(cls.snap, width=120)
        cls.text = "\n".join(cls.lines)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def section(self, name):
        """The lines of one ══ section, header excluded."""
        out, inside = [], False
        for line in self.lines:
            if line.startswith("══ "):
                inside = line.startswith("══ %s" % name)
                continue
            if inside:
                out.append(line)
        return out

    def line_with(self, section, needle):
        for line in self.section(section):
            if needle in line:
                return line
        self.fail("%r not in %s section:\n%s" % (
            needle, section, "\n".join(self.section(section))))

    # --- OBJECTIVES -----------------------------------------------------

    def test_objectives_header_counts_active_and_complete(self):
        self.assertIn("══ OBJECTIVES  2 active · 5 complete", self.text)

    def test_active_objective_shows_creation_time_and_age(self):
        row = self.line_with("OBJECTIVES", "obj-halted")
        self.assertIn("since 11-20 17:41", row)
        self.assertIn("1d00h18m", row)
        row = self.line_with("OBJECTIVES", "obj-live")
        self.assertIn("since 17:57:51", row)
        self.assertIn("2m08s", row)

    def test_active_objective_says_what_its_work_is_doing(self):
        rows = self.section("OBJECTIVES")
        i = next(n for n, line in enumerate(rows) if "obj-halted" in line)
        self.assertIn("HALTED: append-marker since 11-20 18:25", rows[i + 1])
        i = next(n for n, line in enumerate(rows) if "obj-live" in line)
        self.assertIn("working: w1", rows[i + 1])

    def test_complete_objective_shows_start_end_and_duration(self):
        row = self.line_with("OBJECTIVES", "obj-done-1")
        self.assertIn("17:13:53 → 17:55:49", row)
        self.assertIn("41m56s", row)

    def test_older_complete_objectives_collapse_to_a_count(self):
        self.assertIn("+ 2 more complete, 11-17 to 11-18",
                      "\n".join(self.section("OBJECTIVES")))
        self.assertNotIn("obj-done-5", "\n".join(self.section("OBJECTIVES")))

    # --- SESSIONS -------------------------------------------------------

    def test_sessions_come_from_attempts_in_flight(self):
        self.assertIn("══ SESSIONS  1 running", self.text)
        head = self.line_with("SESSIONS", "w1 · obj-live")
        # the route first: the handle is a name, not a model
        self.assertIn("luna-6-max (codex/gpt-6-luna, effort max)"
                      " · res-holder (seat)", head)

    def test_a_session_with_no_record_says_so(self):
        # the fixture's Codex session wrote no stdout and has no rollout
        self.line_with("SESSIONS", "no activity recorded yet")
        self.assertNotIn("no output yet", self.text)

    def test_header_names_the_models_in_use(self):
        self.assertIn("models: Engineer claude-opus-4-6[1m] · specialists"
                      " luna-6-max · escalation opus-4-6-1m", self.text)

    def test_session_detail_reads_the_real_handle_keys(self):
        detail = self.line_with("SESSIONS", "RUNNING since 17:58:32")
        self.assertIn("pid 18800", detail)
        self.assertIn("session 1m32s", detail)
        self.assertIn("READ_ONLY", detail)

    # --- RUNS -----------------------------------------------------------

    def test_runs_header_separates_recorded_from_effective(self):
        self.assertIn("RUNS  4 recorded ACTIVE → 1 working · 1 stuck · "
                      "2 done", self.text)

    def test_run_of_a_complete_objective_reads_done(self):
        row = self.line_with("RUNS", "run_done1")
        self.assertIn("done", row)
        joined = "\n".join(self.section("RUNS"))
        self.assertIn("objective COMPLETE 17:55:49", joined)

    def test_run_with_halted_work_reads_stuck(self):
        joined = "\n".join(self.section("RUNS"))
        self.assertIn("stuck", self.line_with("RUNS", "run_halt"))
        self.assertIn("work HALTED: append-marker since 11-20 18:25", joined)

    def test_run_without_nodes_says_so(self):
        joined = "\n".join(self.section("RUNS"))
        self.assertIn("0 nodes", joined)

    def test_runs_explain_the_recorded_status(self):
        self.assertIn("recorded status: set at admission, cleared only by"
                      " Orchestrator COMPLETE_RUN", self.text)

    def test_run_notes_start_in_one_column(self):
        rows = [line for line in self.section("RUNS") if " run_" in line[:8]]
        self.assertEqual(len(rows), 4)
        for row in rows:
            # head: symbol, run id, mode, objective, effective state = 59
            self.assertEqual(row[59], " ", row)
            self.assertNotEqual(row[60], " ", row)

    # --- TASKFORCE ------------------------------------------------------

    def test_taskforce_header_names_the_outside_pool(self):
        self.assertIn("TASKFORCE  2 members · 2 ESTABLISHED · outside: "
                      "outsider", self.text)

    def test_seat_record_splits_through_the_seat_from_without(self):
        row = self.line_with("TASKFORCE", "research/investigator")
        self.assertIn("res-holder", row)
        self.assertIn("EST 11-18", row)
        self.assertIn("2Q 1N", row)
        self.assertIn("(+1Q)", row)
        self.assertIn("> w1", row)
        seat = next(s for s in self.snap.taskforce.seats
                    if s.capability == "research")
        # the agent view's "Nq": every qualifying demonstration of the
        # seat's capability and role, with or without the seat
        self.assertEqual(seat.qualifying_count, 3)

    def test_seat_on_a_halted_project_is_flagged(self):
        row = self.line_with("TASKFORCE", "documentation/builder")
        self.assertIn("# 1 halted", row)

    def test_taskforce_names_the_route_not_just_the_handle(self):
        self.assertIn("every seat runs on luna-6-max (codex/gpt-6-luna,"
                      " effort max) · escalation opus-4-6-1m", self.text)
        self.assertIn("names are handles, not models", self.text)

    # --- ASSIGNMENTS ----------------------------------------------------

    def test_settled_assignments_show_their_outcome(self):
        acc = self.line_with("ASSIGNMENTS", "review-x")
        self.assertTrue(acc.startswith("  * "))
        self.assertIn("accepted", acc)
        failed = self.line_with("ASSIGNMENTS", "append-marker")
        self.assertTrue(failed.startswith("  ! "))
        self.assertIn("failed", failed)
        live = self.line_with("ASSIGNMENTS", " w1 ")
        self.assertIn("executing", live)
        for line in self.section("ASSIGNMENTS"):
            self.assertFalse(line.startswith("  ? "), line)

    # --- ACTIVITY -------------------------------------------------------

    def test_activity_keeps_hours_and_minutes(self):
        self.assertIn("17:58:32.268 [authority   ]", self.text)

    def test_activity_names_work_by_its_workstream_key(self):
        act = "\n".join(self.section("ACTIVITY"))
        self.assertIn("w1 attempt DISPATCHED -> RUNNING", act)
        self.assertIn("append-marker EXECUTING -> HALTED [t-s45]", act)

    def test_activity_shows_admitted_records_as_new(self):
        act = "\n".join(self.section("ACTIVITY"))
        self.assertIn("w1 receipt new -> ADMITTED", act)
        # work it cannot name keeps its id
        self.assertIn("registry_objects.sha256:abcde new -> ADMITTED", act)
        self.assertNotIn("? ->", act)

    # --- width ----------------------------------------------------------

    def test_eighty_columns_never_overflow(self):
        for line in cli_stream.render(self.snap, width=80):
            self.assertLessEqual(len(line), 80, line)

    def test_eighty_columns_keep_flags_and_names_whole(self):
        narrow = "\n".join(cli_stream.render(self.snap, width=80))
        self.assertIn("# 1 halted", narrow)
        self.assertIn("append-marker", narrow)
        self.assertIn("stuck   HALTED 11-20 18:25", narrow)
        self.assertIn("done    COMPLETE 17:55:49", narrow)
        self.assertIn("recorded status: set at admission", narrow)

    def test_eighty_columns_move_a_transition_to_its_own_line(self):
        narrow = cli_stream.render(self.snap, width=80)
        i = next(n for n, line in enumerate(narrow)
                 if line.endswith("] investigate-authority-model"))
        self.assertEqual(narrow[i + 1],
                         "      DEPENDENCIES_RELEASED -> COMPLETED [t-s42]")
        self.assertIn("investigate-authority-model DEPENDENCIES_RELEASED"
                      " -> COMPLETED [t-s42]", self.text)

    def test_eighty_columns_move_a_title_to_its_own_line(self):
        narrow = cli_stream.render(self.snap, width=80)
        i = next(n for n, line in enumerate(narrow)
                 if "HALTED: append-marker since 11-20 18:25" in line)
        self.assertEqual(narrow[i + 1], "        Add a one-line version marker")
        self.assertIn("HALTED: append-marker since 11-20 18:25 · Add a"
                      " one-line version marker", self.text)

    def test_eighty_columns_keep_session_facts_whole(self):
        narrow = "\n".join(cli_stream.render(self.snap, width=80))
        for fact in ("luna-6-max (codex/gpt-6-luna, effort max)",
                     "res-holder (seat)", "pid 18800", "session 1m32s",
                     "READ_ONLY"):
            self.assertIn(fact, narrow)


class ReaderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = CompactFixture(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_readers_cannot_write(self):
        intel = IntelligenceReader(str(self.fx.intel_path))
        auth = AuthorityReader(str(self.fx.auth_path))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                intel.conn.execute("CREATE TABLE probe (x)")
            with self.assertRaises(sqlite3.OperationalError):
                auth.conn.execute("CREATE TABLE probe (x)")
        finally:
            intel.close()
            auth.close()

    def test_terminal_sequences_are_not_active(self):
        auth = AuthorityReader(str(self.fx.auth_path))
        try:
            states = {s["sequence_id"]: s["state"]
                      for s in auth.active_sequences()}
        finally:
            auth.close()
        self.assertNotIn("sq_super", states)
        self.assertNotIn("sq_done", states)
        self.assertEqual(states.get("sq_halt"), "HALTED")

    def test_journal_admissions_have_no_from_state(self):
        auth = AuthorityReader(str(self.fx.auth_path))
        try:
            rows = {t["subject_id"]: t for t in auth.recent_transitions()}
            attempt_sequences = auth.attempt_sequences()
        finally:
            auth.close()
        self.assertIsNone(rows["rcp_live01"]["from_state"])
        self.assertEqual(rows["rcp_live01"]["facts"]["sequence_id"],
                         "sq_live")
        self.assertEqual(rows["at_live"]["from_state"], "DISPATCHED")
        self.assertEqual(attempt_sequences["at_live"], "sq_live")

    def test_autoswarm_reader_uses_handle_and_manifest_keys(self):
        reader = self.fx.autoswarm()
        files = reader.call_files("call_live")
        self.assertEqual(files["pid"], 18800)
        self.assertEqual(files["started_utc"],
                         "2026-11-21T17:58:27.103885+00:00")
        self.assertEqual((files["provider"], files["model"]),
                         ("codex", "gpt-6-luna"))
        self.assertFalse(files["finished"])
        self.assertIsNone(files["finished_utc"])
        self.assertIsNone(files["activity"])  # no rollout for this session
        session = reader.active_sessions()[0]
        self.assertEqual(session["provider"], "codex")
        self.assertEqual(session["started_utc"],
                         "2026-11-21T17:58:27.103885+00:00")
        self.assertEqual(session["stdout_lines"], 0)


if __name__ == "__main__":
    unittest.main()
