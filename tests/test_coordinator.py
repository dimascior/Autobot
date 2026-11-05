"""Behaviour tests for the coordinator ledger + state logic.

Run from the repo root with the package importable, e.g.:
    PYTHONPATH=src python -m unittest discover -s tests
  or:  pip install -e . && python -m unittest discover -s tests

Covers the pre-live acceptance checks that don't need the live gate:
  - re-ingest idempotency, dispatch exclusion by kind/status, missing-predecessor
    blocking, unblock-on-completion, dependency-edge idempotency (phase 2);
  - non-destructive migration, resolution lineage, no auto-promotion of a
    finished execution into a completed task, authority-only closure, and
    restart without duplicate dispatch (phase 3).
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from coordinator.ledger import Ledger, _add_missing_columns
import coordinator.resolve as resolve_mod
import coordinator.dispatch as dispatch_mod
import coordinator.state as state_mod
import coordinator.discover as discover_mod
from coordinator.discover import Resolved, is_blocking, REQUIRED, ROUTE, ACCOMMODATION, PARAM, INFO


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def _task(self, tid, **kw):
        status = kw.pop("status", "open")
        title = kw.pop("title", tid)
        return self.ledger.upsert_item(id=tid, kind="task", domain="d", title=title,
                                       source="test", status=status, **kw)

    # ── phase 2: provenance + dependency authority ───────────────────

    def test_reingest_is_idempotent(self):
        self.assertTrue(self._task("a"))          # first time: new
        self.assertFalse(self._task("a"))         # second time: not new
        self.assertEqual(sum(1 for i in self.ledger.all_items() if i["id"] == "a"), 1)

    def test_only_tasks_are_dispatchable(self):
        self._task("t1")
        self.ledger.upsert_item(id="f1", kind="finding", domain="d", title="f1",
                                source="test", status="completed")
        self.ledger.upsert_item(id="e1", kind="execution", domain="d", title="e1",
                                source="test", status="completed")
        self.assertEqual({i["id"] for i in self.ledger.dispatchable_tasks()}, {"t1"})

    def test_completed_task_excluded(self):
        self._task("done")
        self.ledger.set_status("done", "completed")
        self.assertNotIn("done", {i["id"] for i in self.ledger.dispatchable_tasks()})

    def test_missing_predecessor_blocks(self):
        self._task("dependent")
        self.ledger.add_dependency("ghost", "dependent")   # ghost never ingested
        ghost = self.ledger.get_item("ghost")
        self.assertIsNotNone(ghost)                         # edge preserved, not dropped
        self.assertEqual(ghost["kind"], "unknown")
        self.assertEqual(ghost["status"], "unresolved")
        self.assertFalse(self.ledger.is_unblocked("dependent"))
        self.assertNotIn("dependent",
                         {i["id"] for i in self.ledger.dispatchable_tasks()})

    def test_only_completed_predecessor_unblocks(self):
        self._task("dep")
        self.ledger.add_dependency("pre", "dep")
        self.assertFalse(self.ledger.is_unblocked("dep"))
        # real predecessor arrives as a still-running execution -> still blocked
        self.ledger.upsert_item(id="pre", kind="execution", domain="d", title="pre",
                                source="test", status="open")
        self.assertFalse(self.ledger.is_unblocked("dep"))
        self.ledger.set_status("pre", "completed")          # only completion satisfies
        self.assertTrue(self.ledger.is_unblocked("dep"))

    def test_dependency_edges_idempotent(self):
        self._task("x")
        self._task("y")
        self.ledger.add_dependency("x", "y")
        self.ledger.add_dependency("x", "y")
        self.assertEqual(len(self.ledger.all_dependencies()), 1)

    def test_blocking_transitions_both_ways(self):
        self._task("b")
        self.ledger.add_dependency("pre", "b")
        state_mod.apply_blocking(self.ledger)
        self.assertEqual(self.ledger.get_item("b")["status"], "blocked")
        self.ledger.upsert_item(id="pre", kind="execution", domain="d", title="pre",
                                source="test", status="completed")
        state_mod.apply_blocking(self.ledger)
        self.assertEqual(self.ledger.get_item("b")["status"], "open")

    def test_terminal_status_sticky_on_reingest(self):
        self._task("c")
        self.ledger.set_status("c", "completed")
        self._task("c", title="changed title")             # re-ingest must not reopen
        self.assertEqual(self.ledger.get_item("c")["status"], "completed")

    # ── phase 3: resolution, authority, restart safety ───────────────

    def _completed_dispatch(self, item_id, *, outcome="completed", call_id="call-x"):
        """Reserve + launch a dispatch and land a result.json, returning the home."""
        home = Path(self.tmp.name) / "home"
        (home / "calls" / call_id).mkdir(parents=True, exist_ok=True)
        (home / "calls" / call_id / "result.json").write_text(
            json.dumps({"outcome": outcome, "content": "run output"}), encoding="utf-8")
        cid = self.ledger.start_cycle("test")
        did = self.ledger.reserve_dispatch(item_id=item_id, cycle_id=cid,
                                           model="m", prompt="p")
        self.ledger.mark_dispatch_launched(did, call_id)
        return home, did

    def test_completed_execution_parks_not_completes(self):
        """A finished execution must NOT auto-complete its task."""
        self._task("t")
        home, _ = self._completed_dispatch("t")
        dispatch_mod.sync_active_dispatches(self.ledger, home)
        self.assertEqual(self.ledger.get_item("t")["status"], "awaiting_resolution")
        self.assertNotIn("t", {i["id"] for i in self.ledger.dispatchable_tasks()})

    def test_resolution_proposal_does_not_promote(self):
        """The resolve stage proposes; it never closes a task."""
        self._task("t")
        home, _ = self._completed_dispatch("t")
        dispatch_mod.sync_active_dispatches(self.ledger, home)
        findings = Path(self.tmp.name) / "findings"
        findings.mkdir()
        (findings / "helios-findings-20261103-1305.md").write_text(
            "# P1\nevidence\n", encoding="utf-8")
        out = resolve_mod.evaluate_awaiting(
            self.ledger, findings_dir=findings, autoswarm_home=home)
        self.assertEqual(out["resolved"], 1)                 # corroborated -> resolved
        # proposal recorded, but the item is still parked, NOT completed
        self.assertEqual(self.ledger.get_item("t")["status"], "awaiting_resolution")
        props = self.ledger.proposed_resolutions()
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0]["verdict"], "resolved")

    def test_resolve_is_idempotent(self):
        self._task("t")
        home, _ = self._completed_dispatch("t")
        dispatch_mod.sync_active_dispatches(self.ledger, home)
        findings = Path(self.tmp.name) / "findings"
        findings.mkdir()
        (findings / "helios-findings-20261103-1305.md").write_text("x", encoding="utf-8")
        resolve_mod.evaluate_awaiting(self.ledger, findings_dir=findings, autoswarm_home=home)
        again = resolve_mod.evaluate_awaiting(self.ledger, findings_dir=findings,
                                              autoswarm_home=home)
        self.assertEqual(again["evaluated"], 0)
        self.assertEqual(again["skipped"], 1)
        self.assertEqual(len(self.ledger.proposed_resolutions()), 1)

    def test_review_verdict_when_no_authoritative_evidence(self):
        self._task("t")
        home, _ = self._completed_dispatch("t")
        dispatch_mod.sync_active_dispatches(self.ledger, home)
        empty = Path(self.tmp.name) / "no-findings"
        empty.mkdir()
        out = resolve_mod.evaluate_awaiting(self.ledger, findings_dir=empty,
                                            autoswarm_home=home)
        self.assertEqual(out["review"], 1)                   # completed but uncorroborated

    def test_only_authority_completes_a_task(self):
        self._task("t")
        home, _ = self._completed_dispatch("t")
        dispatch_mod.sync_active_dispatches(self.ledger, home)
        findings = Path(self.tmp.name) / "findings"
        findings.mkdir()
        (findings / "helios-findings-20261103-1305.md").write_text("x", encoding="utf-8")
        resolve_mod.evaluate_awaiting(self.ledger, findings_dir=findings, autoswarm_home=home)
        res = self.ledger.proposed_resolutions()[0]
        # empty authority is refused
        with self.assertRaises(ValueError):
            self.ledger.authorize_resolution(res["id"], "")
        self.assertEqual(self.ledger.get_item("t")["status"], "awaiting_resolution")
        # a named authority enacts the closure
        outcome = self.ledger.authorize_resolution(res["id"], "tester")
        self.assertEqual(outcome["verdict"], "resolved")
        self.assertEqual(self.ledger.get_item("t")["status"], "completed")
        self.assertEqual(self.ledger.get_resolution(res["id"])["status"], "authorized")
        self.assertEqual(self.ledger.get_resolution(res["id"])["authority"], "tester")

    def test_rework_creates_new_task_and_supersedes(self):
        self._task("w", priority=5)
        rid = self.ledger.record_resolution(
            item_id="w", verdict="rework", evaluator="test",
            rationale="insufficient", acceptance_criteria="crit")
        outcome = self.ledger.authorize_resolution(rid, "tester")
        self.assertIn("rework_task", outcome)
        rework = self.ledger.get_item(outcome["rework_task"])
        self.assertIsNotNone(rework)
        self.assertEqual(rework["kind"], "task")
        self.assertEqual(rework["status"], "open")
        self.assertEqual(self.ledger.get_item("w")["status"], "superseded")

    def test_resolution_lineage_persists(self):
        self._task("t")
        refs = [{"type": "dispatch", "call_id": "c1"}, {"type": "findings_file",
                                                        "path": "f.md"}]
        hashes = {"f.md": "deadbeef" * 8}
        rid = self.ledger.record_resolution(
            item_id="t", verdict="review", evaluator="rule:v1",
            rationale="why", acceptance_criteria="criteria text",
            source_refs=refs, evidence_hashes=hashes)
        got = self.ledger.get_resolution(rid)
        self.assertEqual(got["evaluator"], "rule:v1")
        self.assertEqual(got["verdict"], "review")
        self.assertEqual(json.loads(got["source_refs"]), refs)
        self.assertEqual(json.loads(got["evidence_hashes"]), hashes)
        self.assertEqual(got["acceptance_criteria"], "criteria text")

    def test_record_resolution_rejects_unknown_verdict(self):
        self._task("t")
        with self.assertRaises(ValueError):
            self.ledger.record_resolution(item_id="t", verdict="bogus", evaluator="x")

    def test_reserved_item_not_dispatchable(self):
        self._task("r")
        cid = self.ledger.start_cycle("test")
        self.ledger.reserve_dispatch(item_id="r", cycle_id=cid, model="m", prompt="p")
        self.assertEqual(self.ledger.get_item("r")["status"], "in_progress")
        self.assertTrue(self.ledger.has_open_dispatch("r"))
        self.assertNotIn("r", {i["id"] for i in self.ledger.dispatchable_tasks()})

    def test_restart_does_not_redispatch_reserved_item(self):
        self._task("r")
        cid = self.ledger.start_cycle("test")
        self.ledger.reserve_dispatch(item_id="r", cycle_id=cid, model="m", prompt="p")
        db_path = self.ledger.db_path
        self.ledger.close()
        # simulate a process restart: a fresh Ledger on the same db
        self.ledger = Ledger(db_path)
        self.assertEqual(self.ledger.get_item("r")["status"], "in_progress")
        self.assertTrue(self.ledger.has_open_dispatch("r"))
        self.assertNotIn("r", {i["id"] for i in self.ledger.dispatchable_tasks()})


class MigrationTests(unittest.TestCase):
    """Non-destructive upgrade: an older-version db keeps every row and gains the
    new objects, with a backup left behind."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "legacy.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _build_v2_db(self):
        c = sqlite3.connect(str(self.path))
        c.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE items (
                id TEXT PRIMARY KEY, kind TEXT, domain TEXT, title TEXT,
                description TEXT, evidence TEXT, status TEXT, source TEXT,
                source_ref TEXT, task_class TEXT, route TEXT, priority INTEGER,
                created_utc TEXT, updated_utc TEXT, last_seen_utc TEXT,
                resolved_utc TEXT, cooldown_until TEXT, dispatch_count INTEGER,
                content_hash TEXT
            );
            CREATE TABLE dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, cycle_id INTEGER,
                call_id TEXT, model TEXT, prompt TEXT, status TEXT,
                dispatched_utc TEXT, completed_utc TEXT, result_summary TEXT, outcome TEXT
            );
            """
        )
        c.execute("INSERT INTO meta VALUES ('schema_version','2')")
        for n in ("keep-1", "keep-2"):
            c.execute(
                "INSERT INTO items (id,kind,domain,title,status,source,created_utc,"
                "updated_utc,last_seen_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                (n, "task", "d", f"title {n}", "open", "legacy",
                 "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z"),
            )
        c.execute(
            "INSERT INTO dispatches (item_id,cycle_id,call_id,model,prompt,status,"
            "dispatched_utc) VALUES (?,?,?,?,?,?,?)",
            ("keep-1", 1, "c", "m", "p", "completed", "2026-11-01T00:00:00Z"),
        )
        c.commit()
        c.close()

    def test_v2_to_v3_preserves_rows_and_adds_resolutions(self):
        self._build_v2_db()
        ledger = Ledger(self.path)                    # opens -> migrates v2 -> v3
        try:
            items = ledger.all_items()
            self.assertEqual({i["id"] for i in items}, {"keep-1", "keep-2"})
            self.assertEqual(ledger.get_item("keep-1")["title"], "title keep-1")
            self.assertEqual(len(ledger.dispatches_for("keep-1")), 1)
            # the new table exists and is usable
            self.assertEqual(ledger.proposed_resolutions(), [])
            # version stamped forward
            ver = ledger.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"]
            self.assertEqual(ver, "3")
        finally:
            ledger.close()
        # a rollback backup of the pre-migration db was left behind
        backups = list(Path(self.tmp.name).glob("legacy.db.bak.v2.*"))
        self.assertTrue(backups, "expected a pre-migration backup file")

    def test_fresh_db_is_latest_version_no_backup(self):
        ledger = Ledger(self.path)
        try:
            ver = ledger.conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"]
            self.assertEqual(ver, "3")
        finally:
            ledger.close()
        self.assertFalse(list(Path(self.tmp.name).glob("legacy.db.bak.*")))

    def test_add_missing_columns_is_additive_and_idempotent(self):
        c = sqlite3.connect(str(self.path))
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE t (id TEXT)")
        added = _add_missing_columns(c, "t", {"id": "TEXT", "extra": "TEXT DEFAULT ''"})
        self.assertEqual(added, ["extra"])                    # only the missing one
        again = _add_missing_columns(c, "t", {"extra": "TEXT DEFAULT ''"})
        self.assertEqual(again, [])                           # idempotent
        c.close()


class DiscoveryTierTests(unittest.TestCase):
    """The per-route required-vs-accommodated model: workspaces (params) and
    accommodations never block; a route program blocks only when its agent is
    enabled; apply_to_config never raises on an unset workspace or path."""

    def test_required_blocks_only_when_unresolved(self):
        self.assertTrue(is_blocking(
            Resolved("autoswarm_home", None, "MISSING", "x", REQUIRED), set()))
        self.assertFalse(is_blocking(
            Resolved("autoswarm_home", Path("/x"), "override", "x", REQUIRED), set()))

    def test_route_program_blocks_only_when_its_agent_is_enabled(self):
        r = Resolved("codex_handoff", None, "MISSING", "x", ROUTE, "codex")
        self.assertTrue(is_blocking(r, {"codex"}))            # codex route on -> blocks
        self.assertFalse(is_blocking(r, {"claude-code"}))     # codex route off -> N/A
        self.assertFalse(is_blocking(r, set()))

    def test_params_and_accommodations_never_block(self):
        for r in (
            Resolved("luna_workspace", None, "MISSING", "x", PARAM, "codex"),
            Resolved("codeapi", None, "MISSING", "x", ACCOMMODATION, "codex"),
            Resolved("findings_dir", None, "MISSING", "x", ACCOMMODATION),
            Resolved("codex_home", None, "MISSING", "x", INFO),
        ):
            self.assertFalse(is_blocking(r, {"codex", "claude-code"}), r.key)

    def test_apply_to_config_never_raises_and_fills_path_strings(self):
        cfg = {
            "paths": {"autoswarm_home": "", "gate_root": "", "findings_dir": "",
                      "backlog_file": "backlog.toml", "ledger_db": "coordinator.db"},
            "routes": {"luna": {"agent": "codex", "workspace": ""},
                       "opus": {"agent": "claude-code", "workspace": ""}},
        }
        out = discover_mod.apply_to_config(cfg)               # must not raise
        for k in ("autoswarm_home", "gate_root", "findings_dir"):
            self.assertIsInstance(out["paths"][k], str)       # loop.py can always read these

    def test_resolve_paths_nonstrict_reports_the_codex_chain(self):
        d = discover_mod.resolve_paths({"routes": {"luna": {"agent": "codex"}}},
                                       strict=False)           # must not raise
        for k in ("autoswarm_home", "gate_root", "codex_handoff", "devtools_dir",
                  "luna_workspace", "codeapi"):
            self.assertIn(k, d)


class WorkspaceResolutionTests(unittest.TestCase):
    """The codex route workspace is discovered from the agent's own session
    history -- the cwd of the latest `autoswarm-child` session -- not hand-set."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / "sessions"
        (self.base / "2026" / "11").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _rollout(self, name, *, originator, cwd):
        meta = {"type": "session_meta",
                "payload": {"originator": originator, "cwd": cwd}}
        (self.base / "2026" / "11" / name).write_text(
            json.dumps(meta) + "\n", encoding="utf-8")

    def test_picks_latest_autoswarm_child_over_newer_adhoc_run(self):
        # a NEWER ad-hoc codex_exec run must be ignored in favour of the latest
        # autoswarm-child dispatch, even though its filename sorts later.
        self._rollout("rollout-2026-11-05T10-00-00-aaaa.jsonl",
                      originator="codex_exec", cwd="E:\\Users\\dimas")
        self._rollout("rollout-2026-11-04T10-00-00-bbbb.jsonl",
                      originator="autoswarm-child", cwd="C:\\repo\\target")
        got = discover_mod.latest_autoswarm_workspace("codex", sessions_dir=self.base)
        self.assertEqual(got, Path("C:\\repo\\target"))

    def test_non_codex_agent_and_missing_dir_return_none(self):
        self._rollout("rollout-2026-11-04T10-00-00-bbbb.jsonl",
                      originator="autoswarm-child", cwd="C:\\repo\\target")
        self.assertIsNone(
            discover_mod.latest_autoswarm_workspace("claude-code", sessions_dir=self.base))
        self.assertIsNone(
            discover_mod.latest_autoswarm_workspace("codex", sessions_dir=self.base / "nope"))

    def test_no_autoswarm_child_on_record_returns_none(self):
        self._rollout("rollout-2026-11-05T10-00-00-aaaa.jsonl",
                      originator="codex_exec", cwd="E:\\Users\\dimas")
        self.assertIsNone(
            discover_mod.latest_autoswarm_workspace("codex", sessions_dir=self.base))


if __name__ == "__main__":
    unittest.main()
