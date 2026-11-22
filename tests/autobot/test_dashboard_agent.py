"""The agent view (a plain `python -m intelligence.dashboard`) shows the
organization's live state in every box and band: what each principal is
doing now, the work executing, the project work graph with why work is
halted or blocked, and one activity stream over every layer.

The fixture is the compact view's (real PlanStore and autobot.Database
schemas) plus what the agent view also reads: Engineer cycles, waiting
proposals and open staffing, the driver and sequencer leases, receipts,
verifications, effects, witnesses, permission answers, findings, frozen
records in a content-addressed store, and Taskforce and planning events.
"""

import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import support  # noqa: F401  (puts src on sys.path)
from intelligence.dashboard import agent_visual
from intelligence.dashboard.model import build_snapshot
from intelligence.dashboard.readers import (
    AuthorityReader, AutoSwarmReader, CASReader, ConfigReader,
    IntelligenceReader)
from intelligence.dashboard.screen import Screen

from test_dashboard_compact import NOW, CompactFixture, _insert


def _put_cas(root, payload):
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    folder = root / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / digest).write_bytes(data)
    return "sha256:" + digest


class AgentFixture(CompactFixture):
    """The compact fixture plus the rows only the agent view reads."""

    def __init__(self, root):
        super(AgentFixture, self).__init__(root)
        self.cas_root = self.root / "store" / "objects"
        self._fill_agent_intelligence()
        self._fill_agent_authority()

    def _fill_agent_intelligence(self):
        c = self._open(self.intel_path)
        _insert(c, "cycles", cycle_id=901, started_utc="2026-11-21T17:59:50Z",
                snapshot_entries=0, status="OPEN")
        _insert(c, "proposals", proposal_id="prop:wait", cycle_id=901,
                **{"class": "WAIT_FOR_AUTHORITY"}, objective_id="obj-halted",
                status="WAITING", rationale="sq_halt is HALTED by authority",
                body="{}", sequence_id="sq_halt", snapshot_entries=0,
                created_utc="2026-11-21T17:13:53.093Z")
        _insert(c, "assignment_decisions", decision_id="dec_repair",
                objective_id="obj-halted", project_id="pj_halt",
                sequence_id="sq_halt", attempt_ordinal=2, mode="taskforce",
                status="OPEN", decided_utc="2026-11-20T18:25:48.628Z")
        _insert(c, "driver_lease", lease_key="driver",
                holder="autobot-authority",
                acquired_utc="2026-11-21T17:59:55Z",
                expires_utc="2026-11-21T18:00:25Z")
        _insert(c, "workforce_events", event_key="wf1",
                event_kind="retained_seat_assigned", subject_id="asg_live",
                facts=json.dumps({"capability": "research",
                                  "role": "investigator", "seat_id": "ts_res",
                                  "project_id": "pj_live"}),
                recorded_utc="2026-11-21T17:58:24.950Z")
        _insert(c, "workforce_events", event_key="wf2",
                event_kind="demonstration_recorded", subject_id="dem:x",
                facts=json.dumps({"capability": "research",
                                  "role": "investigator",
                                  "outcome": "QUALIFYING"}),
                recorded_utc="2026-11-21T17:55:00Z")
        _insert(c, "planning_events", journal_seq=6, entry_hash="sha256:e6",
                event_kind="work_ready", machine="sequence",
                subject_id="sq_live", rule="t-s02",
                facts=json.dumps({"satisfied_edges": [
                    {"edge_id": "on-investigate", "edge_kind": "RECORD"}]}),
                recorded_utc="2026-11-21T17:58:24.900Z")
        _insert(c, "assignment_approvals", request_id="rq1",
                assignment_id="asg_live", call_id="call_live",
                tool_name="codex.fileChange", decision="deny",
                reason="outside the sandbox",
                decided_utc="2026-11-21T17:58:50Z")
        _insert(c, "findings", finding_id="f1", attempt_id="at_done",
                sequence_id="sq_done", role="reviewer",
                finding_class="gap", affected_component="x",
                evidence_refs="[]", significance="HIGH", confidence="HIGH",
                relation_to_requirement="supports",
                admitted_utc="2026-11-21T17:50:00Z")
        _insert(c, "orchestration_engineering_bindings",
                engineering_request_id="er1",
                node_id="nd_run_halt_engineer-reasoning",
                orchestration_id="run_halt", question="how",
                created_utc="2026-11-21T15:25:39Z",
                engineering_decision_id="ed1", content_sha256="ab" * 32)
        _insert(c, "objective_budgets", objective_id="obj-halted",
                max_contracts=4, max_attempts=10, max_outside_specialists=2,
                max_repairs=3, max_retention_decisions=1, set_by="operator",
                set_utc="2026-11-20T18:31:51Z")
        # before the routing change the holder ran on the model in its old
        # name: an assignment and a demonstration on claude-code/opus
        _insert(c, "project_assignments", assignment_id="asg_old",
                specialist_id="sp_doc", seat_id="ts_doc", sequence_id="sq_old",
                attempt_ordinal=1, capability="documentation", role="builder",
                provider="claude-code", model="opus",
                selection_path="RETAINED_SEAT", decided_by="TASKFORCE",
                selected_reason="{}", standing_at_assignment="ESTABLISHED",
                status="SETTLED", outcome="VERIFIED_ACCEPTED",
                selected_utc="2026-11-18T14:00:00Z",
                settled_utc="2026-11-18T14:05:00Z")
        _insert(c, "capability_demonstrations", demonstration_id="d_old",
                specialist_id="sp_doc", seat_id="ts_doc",
                assignment_id="asg_old", standing="ESTABLISHED",
                capability="documentation", role="builder", environment="{}",
                system_domains="[]", sequence_id="sq_old", attempt_id="at_old",
                task_contract_id="tc", record_hash="sha256:r",
                contract_digest="sha256:c", verification_refs="[]",
                evidence_refs="[]", criteria="{}", outcome="QUALIFYING",
                observed_findings="[]", recorded_utc="2026-11-18T14:06:00Z")
        c.commit()
        c.close()

    def _fill_agent_authority(self):
        halt_record = _put_cas(self.cas_root, {
            "kind": "TaskContractRecord", "predecessors": []})
        review_record = _put_cas(self.cas_root, {
            "kind": "TaskContractRecord", "predecessors": [
                {"edge_id": "review-of", "edge_kind": "RECORD",
                 "observation_requirement": "ACCEPTANCE",
                 "target": "sha256:r_sq_halt"}]})
        c = self._open(self.auth_path)
        for sid, address in (("sq_halt", halt_record),
                             ("sq_blocked", review_record)):
            _insert(c, "records", record_hash="sha256:r_" + sid,
                    task_contract_id="tc_" + sid, requirement_digest="rd",
                    contract_digest="sha256:c_" + sid,
                    content_address=address, eligibility="AUTOMATIC",
                    frozen_utc="2026-11-20T17:42:19Z")
        _insert(c, "instance_lease", lease_key="sequencer",
                holder="autobot-authority", epoch=31,
                acquired_utc="2026-11-21T17:58:00Z",
                expires_utc="2026-11-21T18:42:00Z")
        for aid, sid, state in (("at_halt", "sq_halt", "FAILED"),
                                ("at_done", "sq_done", "VERIFIED")):
            _insert(c, "attempts", attempt_id=aid, sequence_id=sid,
                    state=state, version=5, fence=1,
                    attempt_contract_hash="sha256:ac_" + aid,
                    created_utc="2026-11-20T17:42:20Z",
                    updated_utc="2026-11-20T18:25:48Z")
        for adm, aid, completeness in (("rcp_halt", "at_halt",
                                        "INTEGRITY_FAILED"),
                                       ("rcp_done", "at_done", "COMPLETE")):
            _insert(c, "attempt_receipts", admission_id=adm, attempt_id=aid,
                    content_sha256="sha256:" + adm, completeness=completeness,
                    evidence_complete_by_utc="2026-11-20T18:53:22Z",
                    minter="collector", call_id="call_" + aid,
                    admitted_utc="2026-11-20T18:25:48.432Z")
        _insert(c, "attempt_verifications", verification_id="vr1",
                attempt_id="at_done", assertion_id="a1",
                mode="DETERMINISTIC", verifier_id="registry-checker",
                outcome="PASS", by_principal="verifier",
                receipt_sha256="sha256:v", recorded_utc="2026-11-21T17:55:40Z")
        _insert(c, "effect_intents", intent_id="ei1", effect_id="ef1",
                intent_class="GIT_REF_UPDATE", state="HAPPENED",
                sequence_id="sq_done", attempt_id="at_done",
                created_utc="2026-11-21T17:55:41Z", version=2)
        _insert(c, "attempt_witnesses", admission_id="wit1",
                attempt_id="at_live", witness_kind="obj-11",
                producer="autoswarm", content_sha256="sha256:w",
                call_id="call_live", admitted_utc="2026-11-21T17:58:32Z")
        # seq 6: the transition planning event 6 annotates
        payload = {"machine": "sequence", "subject_id": "sq_live",
                   "from_state": "BLOCKED", "to_state": "READY",
                   "rule": "t-s02", "recorded_utc": "2026-11-21T17:58:24.900Z"}
        _insert(c, "transition_journal", entry_hash="sha256:e6",
                payload=json.dumps(payload),
                appended_utc=payload["recorded_utc"])
        c.commit()
        c.close()

    def snapshot(self, captured=NOW):
        intel = IntelligenceReader(str(self.intel_path))
        auth = AuthorityReader(str(self.auth_path))
        try:
            snap = build_snapshot(intel, auth,
                                  config_reader=ConfigReader(self.root),
                                  autoswarm_reader=self.autoswarm(),
                                  cas_reader=CASReader(self.cas_root),
                                  now=captured)
        finally:
            intel.close()
            auth.close()
        return snap


def frame(snapshot, width, height):
    scr = Screen(width, height)
    agent_visual.compose(scr, snapshot)
    return scr.text_lines()


def box(lines, title):
    """The text rows inside the box whose top border names ``title``."""
    for top, line in enumerate(lines):
        col = line.find(" " + title + "═")
        if col < 0:
            continue
        start = line.rfind("╔", 0, col)
        end = line.find("╗", col)
        rows = []
        for row in lines[top + 1:]:
            if len(row) <= start or row[start] != "║":
                break
            rows.append(row[start + 1:end].strip())
        return rows
    raise AssertionError("no %s box in:\n%s" % (title, "\n".join(lines)))


def band(lines, title):
    """The rows under one ━━ band title, up to the next band."""
    out, inside = [], False
    for line in lines:
        if "━━ %s " % title in line:
            inside = True
            continue
        if inside and line.lstrip().startswith("━━ "):
            break
        if inside:
            out.append(line.strip())
    return out


class AgentViewTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fx = AgentFixture(cls._tmp.name)
        cls.snap = cls.fx.snapshot()
        cls.lines = frame(cls.snap, 146, 53)
        cls.text = "\n".join(cls.lines)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def assertIn_any(self, needle, rows):
        self.assertTrue(any(needle in r for r in rows),
                        "%r not in:\n%s" % (needle, "\n".join(rows)))

    # --- header -----------------------------------------------------------

    def test_header_carries_time_heartbeats_and_attention(self):
        head = "\n".join(self.lines[:2])
        self.assertIn("2026-11-21 18:00:00 UTC", head)
        self.assertIn("driver ● step 5s ago", head)
        self.assertIn("journal #6", head)
        self.assertIn("1 halted", head)
        self.assertIn("1 blocked behind a halt", head)
        self.assertIn("1 waiting", head)
        # the lease runs to 18:00:25, after the 18:00:00 capture
        self.assertNotIn("driver stopped", head)

    def test_an_expired_lease_with_work_in_flight_is_flagged(self):
        late = frame(self.fx.snapshot("2026-11-21T18:01:00Z"), 146, 53)
        head = "\n".join(late[:2])
        self.assertIn("driver ○ step 1m05s ago · lease expired", head)
        self.assertIn("1 driver stopped", head)

    # --- boxes ------------------------------------------------------------

    def test_coordinator_box_shows_admissions_and_feature_sources(self):
        rows = box(self.lines, "COORDINATOR")
        self.assertIn_any("2 active · 5 complete objectives", rows)
        self.assertIn_any("● obj-live  DIR  2m08s", rows)
        self.assertIn_any("● obj-halted  ENG  1d00h18m", rows)
        self.assertIn_any("last admitted 17:57:51 (2m08s) obj-live → DIR",
                          rows)
        # derived, labelled: no complexity word in "Investigate the
        # current pricing tiers", so the feature came from context
        self.assertIn_any("organizational_complexity ← context: more than"
                          " 2 active runs", rows)
        self.assertIn_any("engineering_design ← no word in the text", rows)

    def test_orchestrator_box_says_what_each_run_is_doing(self):
        rows = box(self.lines, "ORCHESTRATOR")
        self.assertIn_any("4 runs ACTIVE → 1 working · 1 stuck · 2 done",
                          rows)
        live = next(r for r in rows if "obj-live" in r)
        self.assertIn("working · w1 in progress", live)
        halt = next(r for r in rows if "obj-halted" in r)
        self.assertIn("stuck · HALTED 11-20 18:25", halt)
        self.assertIn_any("0 nodes", rows)
        self.assertIn_any("recorded ACTIVE until an Orchestrator COMPLETE_RUN",
                          rows)

    def test_engineer_box_shows_cycle_projects_waits_and_staffing(self):
        rows = box(self.lines, "ENGINEER")
        self.assertIn_any("cycle 901 OPEN · started 17:59:50 (10s ago)"
                          " · 1 proposal", rows)
        self.assertIn_any("projects 2 active · 1 closed", rows)
        self.assertIn_any("pj_live project pj_live  0/1 done", rows)
        self.assertIn_any("⚑ waiting WAIT_FOR_AUTHORITY ×1 · append-marker",
                          rows)
        self.assertIn_any("staffing open 1 · append-marker → taskforce", rows)
        self.assertIn_any("reasoning 1 decisions · 1 text not stored (O4)",
                          rows)

    def test_taskforce_box_lists_every_seat_and_who_works_now(self):
        rows = box(self.lines, "TASKFORCE")
        self.assertIn_any("2 members · 2 EST · outside: outsider", rows)
        self.assertIn_any("working now res-holder → w1", rows)
        res = next(r for r in rows if "res-holder" in r and "/" in r)
        self.assertTrue(res.startswith("▶"), res)
        self.assertIn("research/inv", res)
        self.assertIn("2Q 1N", res)
        self.assertIn("(+1Q)", res)
        doc = next(r for r in rows if "doc-holder" in r)
        self.assertIn("# 1 halted", doc)

    def test_taskforce_box_names_the_model_each_seat_runs_on(self):
        # the handles keep old model names; the box says what runs
        rows = box(self.lines, "TASKFORCE")
        self.assertIn_any("every seat runs on luna-6-max (codex/gpt-6-luna,"
                          " effort max)", rows)
        self.assertIn_any("escalation opus-4-6-1m", rows)
        self.assertIn_any("made on other models until 11-18: 1Q by 1 holder",
                          rows)
        self.assertIn_any("names are handles, not models", rows)
        self.assertFalse(any("more rows" in r for r in rows), rows)

    def test_compact_lists_each_holders_records_by_model(self):
        from intelligence.dashboard import cli_stream
        text = "\n".join(cli_stream.render(self.snap, width=146))
        self.assertIn("models: Engineer claude-opus-4-6[1m] · specialists"
                      " luna-6-max · escalation opus-4-6-1m", text)
        self.assertIn("made on other models: doc-holder 1Q on opus until"
                      " 11-18", text)
        rows = box(self.lines, "ENGINEER")
        self.assertIn_any("reasons on claude-opus-4-6[1m]", rows)

    def test_authority_box_uses_full_state_names_and_trust_facts(self):
        rows = box(self.lines, "AUTHORITY")
        seq = next(r for r in rows if r.startswith("sequences"))
        self.assertIn("3/6 finished", seq)  # SUPERSEDED counts as finished
        for part in ("BLOCKED 1", "EXECUTING 1", "HALTED 1", "COMPLETED 2",
                     "SUPERSEDED 1"):
            self.assertIn(part, seq)
        self.assertIn_any("RUNNING 1", rows)
        self.assertIn_any("1 INTEGRITY_FAILED ⚑", rows)
        self.assertIn_any("verifications 1 PASS", rows)
        self.assertIn_any("effects 1 HAPPENED", rows)
        self.assertIn_any("witnesses 1", rows)
        self.assertIn_any("permissions 1 deny", rows)
        # the compact fixture's four plus the opus-era one
        self.assertIn_any("demonstrations 1 NOT_QUALIFYING, 4 QUALIFYING",
                          rows)
        self.assertIn_any("sequencer epoch 31 · lease until 18:42:00"
                          " (42m00s left)", rows)

    # --- bands ------------------------------------------------------------

    def test_executing_band_lists_live_sessions_only(self):
        rows = band(self.lines, "EXECUTING")
        # one row per session, the route first (a handle is a name)
        live = next(r for r in rows if r.startswith("▶ w1"))
        self.assertIn("▶ w1  luna-6-max · res-holder (seat)  RUNNING since"
                      " 17:58:32 · session 1m32s", live)
        for part in ("no activity recorded yet", "pid 18800", "READ_ONLY",
                     "obj-live"):
            self.assertIn(part, live)
        self.assertIn("━━ EXECUTING ━ 1 running ", self.text)
        for row in rows:
            self.assertNotIn("HALTED", row)
            self.assertNotIn("BLOCKED", row)
            self.assertNotIn("no output yet", row)

    def test_dag_band_draws_the_project_work_graph(self):
        rows = band(self.lines, "DAG")
        self.assertIn_any("◆ obj-halted  0/2 done · pj_halt opened 11-20"
                          " 17:41  project pj_halt", rows)
        halt = next(r for r in rows if "append-marker" in r
                    and "HALTED" in r)
        self.assertIn("HALTED 11-20 18:25 · 23h34m", halt)
        self.assertIn("doc-holder · seat", halt)
        self.assertIn("t-s45 receipt INTEGRITY_FAILED", halt)
        self.assertIn("next: taskforce attempt 2", halt)
        self.assertNotIn("…", halt)
        review = next(r for r in rows if "review-marker" in r)
        self.assertTrue(review.startswith("└▶ ◫"), review)
        self.assertIn("waits on append-marker (HALTED)", review)
        live = next(r for r in rows if " w1 " in r)
        self.assertIn("EXECUTING", live)
        self.assertIn("attempt RUNNING", live)
        self.assertIn_any("✦ obj-done-1  2/2 done · completed  project"
                          " pj_done", rows)

    def test_a_part_too_long_for_the_row_moves_whole(self):
        # at 100 columns the halted item's cause and next step no longer
        # fit beside it: they move to an indented row, nothing is cut
        rows = band(frame(self.snap, 100, 60), "DAG")
        at = next(i for i, r in enumerate(rows) if "append-marker" in r
                  and "HALTED" in r)
        self.assertNotIn("…", rows[at])
        self.assertIn("next: taskforce attempt 2", " ".join(rows[at:at + 2]))

    def test_activity_band_merges_every_layer_with_full_times(self):
        rows = band(self.lines, "ACTIVITY")
        self.assertIn_any("17:58:32.268 ⬡ w1 attempt DISPATCHED -> RUNNING",
                          rows)
        self.assertIn_any("⬡ w1 BLOCKED -> READY [t-s02] · ready:"
                          " on-investigate satisfied", rows)
        self.assertIn_any("17:58:24.950 ⛊ seat assigned: res-holder -> w1",
                          rows)
        self.assertIn_any("⛊ demonstration QUALIFYING research/investigator",
                          rows)
        self.assertIn_any("17:59:50.000 ⚙ cycle 901 open · 1 proposal", rows)
        self.assertIn_any("◆ admitted obj-live -> DIRECT", rows)
        self.assertIn_any("⛊ permission deny · codex.fileChange", rows)
        self.assertIn_any("⬡ w1 receipt new -> ADMITTED", rows)
        # the base fixture's cycle 900 started at the snapshot instant
        self.assertIn("newest 18:00:00 · 0s ago", self.text)

    # --- sizes ------------------------------------------------------------

    def test_every_size_draws_all_three_bands(self):
        for width, height in ((230, 53), (146, 53), (120, 40), (100, 30),
                              (80, 24)):
            lines = frame(self.snap, width, height)
            text = "\n".join(lines)
            for title in ("EXECUTING", "DAG", "ACTIVITY"):
                self.assertIn("━━ %s " % title, text,
                              "%s missing at %dx%d" % (title, width, height))
            self.assertLessEqual(len(lines), height)
            for line in lines:
                self.assertLessEqual(len(line), width, line)

    def test_small_terminal_keeps_one_line_per_principal(self):
        text = "\n".join(frame(self.snap, 80, 24))
        for head in ("◆ coord", "◈ orch", "⚙ eng", "⛊ tf", "⬡ auth"):
            self.assertIn(head, text)
        self.assertIn("working: res-holder → w1", text)

    def test_wide_terminal_uses_its_full_width(self):
        top = next(line for line in self.lines if "ORCHESTRATOR═" in line)
        self.assertGreater(len(top), 140)

    def test_from_200_columns_the_four_boxes_share_one_row(self):
        lines = frame(self.snap, 230, 53)
        top = next(line for line in lines if "COORDINATOR═" in line)
        for title in ("ORCHESTRATOR═", "ENGINEER═", "TASKFORCE═"):
            self.assertIn(title, top)
        # and the rows they free go to the live bands
        rows_146 = sum(len(band(self.lines, b)) for b in
                       ("EXECUTING", "DAG", "ACTIVITY"))
        rows_230 = sum(len(band(lines, b)) for b in
                       ("EXECUTING", "DAG", "ACTIVITY"))
        self.assertGreater(rows_230, rows_146)


class FollowsActivityTests(unittest.TestCase):
    """The frame changes when the organization does."""

    def test_new_cycle_transition_and_attempt_reach_every_part(self):
        tmp = tempfile.mkdtemp()
        try:
            fx = AgentFixture(tmp)
            before = "\n".join(frame(fx.snapshot(), 146, 53))
            c = fx._open(fx.intel_path)
            _insert(c, "cycles", cycle_id=902,
                    started_utc="2026-11-21T18:00:20Z", snapshot_entries=0,
                    status="OPEN")
            # a new admission and its run (Coordinator, Orchestrator)
            _insert(c, "objectives", objective_id="obj-new",
                    text="Refactor the schema", authored_by="you",
                    status="ACTIVE", created_utc="2026-11-21T18:00:25Z")
            _insert(c, "coordinator_decisions", decision_id="cd_new",
                    objective_id="obj-new",
                    policy_id="production-coordinator/v1", input_digest="d",
                    elevation_mode="ENGINEER_ELEVATED",
                    features=json.dumps({"engineering_design": True}),
                    created_utc="2026-11-21T18:00:25Z")
            _insert(c, "orchestration_runs", orchestration_id="run_new",
                    objective_id="obj-new", coordinator_decision_id="cd_new",
                    mode="ENGINEER_ELEVATED", status="ACTIVE",
                    created_utc="2026-11-21T18:00:25Z",
                    created_by="coordinator")
            # a seat holder takes the review (Taskforce)
            _insert(c, "project_assignments", assignment_id="asg_rev",
                    specialist_id="sp_doc", seat_id="ts_doc",
                    sequence_id="sq_blocked", workstream_id="ws_h2",
                    attempt_ordinal=1, attempt_id="at_rev",
                    objective_id="obj-halted", capability="documentation",
                    role="builder", provider="codex", model="gpt-6-luna",
                    selection_path="RETAINED_SEAT", decided_by="TASKFORCE",
                    selected_reason="{}", standing_at_assignment="ESTABLISHED",
                    status="EXECUTING", selected_utc="2026-11-21T18:00:22Z")
            c.execute("UPDATE driver_lease SET acquired_utc = ?,"
                      " expires_utc = ?", ("2026-11-21T18:00:24Z",
                                           "2026-11-21T18:00:54Z"))
            c.commit()
            c.close()
            a = fx._open(fx.auth_path)
            a.execute("UPDATE sequences SET state = 'EXECUTING',"
                      " updated_utc = ? WHERE sequence_id = 'sq_blocked'",
                      ("2026-11-21T18:00:21Z",))
            _insert(a, "attempts", attempt_id="at_rev",
                    sequence_id="sq_blocked", state="RUNNING", version=1,
                    fence=1, attempt_contract_hash="sha256:ac_rev",
                    created_utc="2026-11-21T18:00:22Z",
                    updated_utc="2026-11-21T18:00:22Z")
            payload = {"machine": "sequence", "subject_id": "sq_blocked",
                       "from_state": "READY", "to_state": "EXECUTING",
                       "rule": "t-s06",
                       "recorded_utc": "2026-11-21T18:00:21.500Z"}
            _insert(a, "transition_journal", entry_hash="sha256:e7",
                    payload=json.dumps(payload),
                    appended_utc=payload["recorded_utc"])
            a.commit()
            a.close()
            after_lines = frame(fx.snapshot("2026-11-21T18:00:30Z"), 146, 53)
            after = "\n".join(after_lines)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertNotIn("cycle 902", before)
        self.assertNotIn("obj-new", before)
        # Coordinator
        self.assertIn("3 active · 5 complete objectives", after)
        self.assertIn("last admitted 18:00:25 (5s) obj-new → ENG", after)
        self.assertIn('engineering_design ← "refactor", "schema"', after)
        # Orchestrator
        self.assertIn("5 runs ACTIVE → 1 working · 1 stuck · 1 waiting"
                      " · 2 done", after)
        self.assertIn("obj-new", box(after_lines, "ORCHESTRATOR")[3])
        self.assertNotIn("driver stopped", after)
        # Taskforce
        self.assertIn("doc-holder → review-marker", after)
        self.assertIn("res-holder → w1", after)
        # Engineer, header
        self.assertIn("cycle 902 OPEN · started 18:00:20 (10s ago)", after)
        self.assertIn("driver ● step 6s ago", after)
        self.assertIn("journal #7", after)
        executing = band(after_lines, "EXECUTING")
        self.assertTrue(any("▶ review-marker" in r for r in executing),
                        "\n".join(executing))
        dag = band(after_lines, "DAG")
        review = next(r for r in dag if "review-marker" in r)
        self.assertIn("EXECUTING", review)
        self.assertIn("attempt RUNNING", review)
        self.assertIn("18:00:21.500 ⬡ review-marker READY -> EXECUTING"
                      " [t-s06]", after)
        authority = box(after_lines, "AUTHORITY")
        self.assertTrue(any("EXECUTING 2" in r for r in authority),
                        "\n".join(authority))


class _TTY(io.StringIO):
    def isatty(self):
        return True


class StreamTests(unittest.TestCase):
    """The live loop drives the terminal: the alternate screen, autowrap
    off, rows placed absolutely, a full repaint on resize, and everything
    restored on exit."""

    def run_stream(self, out, sizes, ticks, **kwargs):
        tmp = tempfile.mkdtemp()
        slept = []

        def sleep(_):
            slept.append(1)
            if len(slept) >= ticks:
                raise KeyboardInterrupt

        try:
            fx = AgentFixture(tmp)
            sizes = iter(sizes)
            kwargs.setdefault("interval", 0.01)
            with mock.patch.object(agent_visual.time, "sleep", sleep), \
                    mock.patch.object(agent_visual.sys, "stdout", out), \
                    mock.patch.dict(os.environ,
                                    {"AUTOSWARM_HOME": str(fx.home),
                                     "CODEX_HOME": str(fx.codex_home)}), \
                    mock.patch.object(agent_visual, "_terminal_size",
                                      lambda: next(sizes)):
                agent_visual.stream(str(fx.intel_path), str(fx.auth_path),
                                    **kwargs)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return out.getvalue()

    def test_live_loop_takes_and_restores_the_terminal(self):
        text = self.run_stream(_TTY(), [(146, 53)] * 3, ticks=1)
        self.assertTrue(text.startswith("\033[?1049h\033[2J\033[?25l\033[?7l"),
                        repr(text[:40]))
        self.assertTrue(text.endswith("\033[?7h\033[?25h\033[?1049l"),
                        repr(text[-40:]))
        self.assertIn("AUTOBOT ORGANIZATION", text)
        self.assertNotIn("\n", text)

    def test_resize_repaints_the_whole_frame_at_the_new_size(self):
        text = self.run_stream(_TTY(), [(146, 53), (146, 53), (120, 40)],
                               ticks=2)
        self.assertEqual(text.count("\033[2J"), 3)  # start, frame, resize
        last = text.rsplit("\033[2J", 1)[1]
        self.assertIn("\033[40;1H", last)
        self.assertNotIn("\033[41;1H", last)

    def test_the_frame_after_a_resize_is_drawn_whole_too(self):
        # a terminal can redraw content it kept from before the resize
        text = self.run_stream(_TTY(), [(146, 53), (146, 53), (120, 40),
                                        (120, 40)], ticks=3)
        self.assertEqual(text.count("\033[2J"), 4)  # start, frame, 2 resize

    def test_every_tenth_second_the_frame_is_drawn_whole(self):
        # with a 5 s interval every second frame is a full repaint, so no
        # cell an earlier frame left on the terminal stays
        text = self.run_stream(_TTY(), [(146, 53)] * 5, ticks=4, interval=5)
        self.assertEqual(text.count("\033[2J"), 3)  # start, frames 0 and 2

    def test_once_prints_plain_lines_when_not_a_terminal(self):
        text = self.run_stream(io.StringIO(), [(146, 53)] * 2, ticks=1,
                               once=True)
        self.assertIn("AUTOBOT ORGANIZATION", text)
        self.assertIn("━━ DAG ", text)
        self.assertNotIn("\033[", text)


class ScreenTests(unittest.TestCase):

    def test_a_cell_never_inherits_bold_from_the_one_before(self):
        scr = Screen(2, 1)
        scr.put(0, 0, "A", 252, bold=True)
        scr.put(1, 0, "B", 252, bold=False)
        out = scr.render_diff(scr.diff())
        self.assertIn("\033[0m\033[38;5;252mB", out)

    def test_full_render_places_rows_without_newlines(self):
        scr = Screen(3, 2)
        scr.put(0, 1, "x", 252)
        out = scr.full_render()
        self.assertNotIn("\n", out)
        self.assertIn("\033[2;1H", out)


if __name__ == "__main__":
    unittest.main()
