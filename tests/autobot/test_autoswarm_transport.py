"""Tests for the production AutoSwarm Transport adapter.

Validates the Transport contract over durable AutoSwarm call records
without launching real provider sessions. Evidence is collected from
mock call directories that mirror AutoSwarm's record layout.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autobot.autoswarm_transport import AutoSwarmTransport, _read_json
from autobot.transport import Transport, reconcile_dispatched, normalize_outcome
from autobot.witnesses import WITNESS_KINDS


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)


class _MockStore:
    """A content-addressed store backed by a dict."""
    def __init__(self):
        self._data = {}

    def put(self, data):
        import hashlib
        address = "sha256:" + hashlib.sha256(data).hexdigest()
        self._data[address] = bytes(data)
        return address

    def get(self, address):
        return self._data[address]


class AutoSwarmTransportContractTests(unittest.TestCase):
    """The adapter satisfies the Transport interface."""

    def test_is_a_transport(self):
        store = _MockStore()
        t = AutoSwarmTransport(store, "/tmp/fake-home", "/tmp/ws")
        self.assertIsInstance(t, Transport)
        self.assertEqual(t.producer_id, WITNESS_KINDS["obj-11"]["producer"])


class FindSessionTests(unittest.TestCase):
    """find_session returns the session when observable, None otherwise."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_no_call_directory_returns_none(self):
        self.assertIsNone(self.transport.find_session("call_" + "a" * 32))

    def test_task_without_handle_returns_none(self):
        call_id = "call_" + "a" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        self.assertIsNone(self.transport.find_session(call_id))

    def test_running_session_returns_original_task_bytes(self):
        """obj-11 evidence is the original task.json bytes. The
        native_session_id is obtained from handle.json independently."""
        call_id = "call_" + "b" * 32
        task_data = {"call_id": call_id, "provider": "codex",
                     "prompt": "investigate logging",
                     "workspace": str(self.home),
                     "created_at": "2026-01-01T00:00:00Z"}
        _write_json(self._call_dir(call_id) / "task.json", task_data)
        _write_json(self._call_dir(call_id) / "handle.json",
                     {"pid": 12345, "native_session_id": "sess-001",
                      "started_at": "2026-01-01T00:00:01Z",
                      "phase": "execution"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-001")
        self.assertEqual(found["evidence"],
                         (self._call_dir(call_id) / "task.json").read_bytes())
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["call_id"], call_id)
        self.assertEqual(evidence["prompt"], "investigate logging")

    def test_returned_session_returns_original_task_bytes(self):
        """Even when result.json exists, obj-11 evidence is task.json
        (the record carrying received-prompt identity)."""
        call_id = "call_" + "c" * 32
        task_data = {"call_id": call_id, "provider": "codex",
                     "prompt": "refactor bridge",
                     "workspace": str(self.home),
                     "created_at": "2026-01-01T00:00:00Z"}
        _write_json(self._call_dir(call_id) / "task.json", task_data)
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-002"})
        found = self.transport.find_session(call_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["native_session_id"], "sess-002")
        self.assertEqual(found["evidence"],
                         (self._call_dir(call_id) / "task.json").read_bytes())
        evidence = json.loads(found["evidence"])
        self.assertEqual(evidence["prompt"], "refactor bridge")

    def test_handle_without_session_id_returns_none(self):
        call_id = "call_" + "d" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id, "provider": "codex",
                      "workspace": str(self.home)})
        _write_json(self._call_dir(call_id) / "handle.json",
                     {"pid": 12345, "phase": "startup"})
        self.assertIsNone(self.transport.find_session(call_id))


class PositiveLaunchFailureTests(unittest.TestCase):
    """positive_launch_failure proves failure only when the launch itself
    never established a provider session."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_no_result_returns_none(self):
        call_id = "call_" + "e" * 32
        _write_json(self._call_dir(call_id) / "task.json",
                     {"call_id": call_id})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_completed_result_returns_none(self):
        call_id = "call_" + "f" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "completed", "native_session_id": "s"})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_failed_with_session_returns_none(self):
        call_id = "call_" + "1" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": "s",
                      "error": "execution error",
                      "diagnostics": {"phase": "execution"}})
        self.assertIsNone(self.transport.positive_launch_failure(call_id))

    def test_startup_failure_returns_reason(self):
        call_id = "call_" + "2" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": None,
                      "error": "CLI not found",
                      "diagnostics": {"phase": "startup"}})
        reason = self.transport.positive_launch_failure(call_id)
        self.assertIsNotNone(reason)
        self.assertIn("CLI not found", reason)

    def test_handshake_failure_returns_reason(self):
        call_id = "call_" + "3" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed", "native_session_id": None,
                      "error": "auth required",
                      "diagnostics": {"phase": "handshake"}})
        self.assertEqual(self.transport.positive_launch_failure(call_id),
                         "auth required")

    def test_failed_without_session_returns_reason(self):
        call_id = "call_" + "4" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"outcome": "failed",
                      "error": "runner crashed before handle"})
        reason = self.transport.positive_launch_failure(call_id)
        self.assertIn("runner crashed", reason)


class CollectTests(unittest.TestCase):
    """collect gathers producer-owned evidence from durable sources."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.gate_root = Path(self.tmpdir.name) / "gate"
        self.store = _MockStore()
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home),
            gate_root=str(self.gate_root))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call_dir(self, call_id):
        return self.home / "calls" / call_id

    def test_stdout_log_not_collected_as_transcript(self):
        """stdout.log is AutoSwarm's pipe capture, not a provider-native
        session transcript. obj-13 must not be emitted from it."""
        call_id = "call_" + "5" * 32
        d = self._call_dir(call_id)
        _write_json(d / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-x"})
        d.mkdir(parents=True, exist_ok=True)
        (d / "stdout.log").write_bytes(b"raw provider output\n")
        collected = self.transport.collect(call_id)
        self.assertEqual(collected["outcome"], "success")
        kinds = [wf[0] for wf in collected["witness_files"]]
        self.assertNotIn("obj-13", kinds)

    def test_collect_governance_individual_entries(self):
        """Each decision.json is a separate obj-12 witness entry with
        its original file bytes — never a combined JSON array."""
        session_id = "sess-gov-001"
        call_id = "call_" + "6" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": session_id})
        evidence_dir = (self.gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        _write_json(evidence_dir / "toolu_001.decision.json",
                     {"verdict": "ALLOW", "tool": "Read"})
        _write_json(evidence_dir / "toolu_002.decision.json",
                     {"verdict": "DENY", "tool": "Bash"})
        collected = self.transport.collect(call_id)
        obj12_entries = [wf for wf in collected["witness_files"]
                         if wf[0] == "obj-12"]
        self.assertEqual(len(obj12_entries), 2)
        for kind, producer, data in obj12_entries:
            self.assertEqual(producer, "helios")
            record = json.loads(data)
            self.assertIsInstance(record, dict)
            self.assertIn("verdict", record)

    def test_no_governance_without_gate_root(self):
        transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.home))
        call_id = "call_" + "7" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed",
                      "native_session_id": "sess-x"})
        collected = transport.collect(call_id)
        obj12_present = any(wf[0] == "obj-12"
                            for wf in collected["witness_files"])
        self.assertFalse(obj12_present)

    def test_collect_normalizes_outcome(self):
        call_id = "call_" + "7" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "completed"})
        self.assertEqual(self.transport.collect(call_id)["outcome"],
                         "success")

    def test_collect_without_result_returns_none(self):
        """No result.json means the session is not terminal — collect
        returns None so the Taskforce can safely skip it."""
        call_id = "call_" + "8" * 32
        self._call_dir(call_id).mkdir(parents=True)
        self.assertIsNone(self.transport.collect(call_id))

    def test_collect_always_includes_result_as_obj11(self):
        """result.json is always collected as the obj-11 lifecycle witness,
        even when no governance or transcript evidence exists."""
        call_id = "call_" + "0" * 32
        _write_json(self._call_dir(call_id) / "result.json",
                     {"call_id": call_id, "outcome": "failed",
                      "error": "auth"})
        collected = self.transport.collect(call_id)
        obj11 = [w for w in collected["witness_files"]
                 if w[0] == "obj-11"]
        self.assertEqual(len(obj11), 1)
        self.assertEqual(obj11[0][1], "autoswarm")
        import json
        witness = json.loads(obj11[0][2])
        self.assertEqual(witness["call_id"], call_id)
        self.assertEqual(collected["outcome"], "failed")


class LaunchProjectionTests(unittest.TestCase):
    """launch resolves the frozen projection and passes the prompt."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.store = _MockStore()
        self.workspace = Path(self.tmpdir.name) / "ws"
        self.workspace.mkdir()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_launch_resolves_projection_and_calls_cli(self):
        projection = {
            "schema_version": "v1",
            "kind": "PromptProjection",
            "engineering_context": {
                "provenance": "chief-engineer-guidance",
                "author": "planner",
                "guidance": "Investigate the logging layer"
            }
        }
        data = json.dumps(projection, sort_keys=True).encode("utf-8")
        address = self.store.put(data)
        captured = {}

        def mock_run(argv, *, input=None, capture_output=False,
                     text=False, env=None, encoding=None, errors=None):
            captured["argv"] = argv
            captured["input"] = input
            captured["encoding"] = encoding
            captured["env_home"] = env.get("AUTOSWARM_HOME") if env else None

            class R:
                returncode = 0
                stdout = json.dumps({
                    "ok": True,
                    "data": {"call_id": "call_" + "a" * 32,
                             "state": "running",
                             "native_session_id": "sess-test"}
                })
                stderr = ""
            return R()

        transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.workspace))
        with patch("autobot.autoswarm_transport.subprocess.run", mock_run):
            native = transport.launch(address, "codex", "builder",
                                      "call_" + "a" * 32)
        self.assertEqual(native, "sess-test")
        self.assertIn("--call-id", captured["argv"])
        self.assertEqual(captured["input"],
                         "Investigate the logging layer")
        self.assertEqual(captured["env_home"], str(self.home))
        # The pipe is UTF-8 in both directions, whatever the locale.
        self.assertEqual(captured["encoding"], "utf-8")


class _Recorder:
    """Stands in for subprocess.run: records every argv, succeeds."""

    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        outer = self

        class R:
            returncode = outer.returncode
            stdout = json.dumps({"ok": outer.returncode == 0, "data": {
                "call_id": "call_" + "a" * 32,
                "native_session_id": "sess-test"},
                "error": {"code": "approval_not_pending",
                          "message": "no longer pending"}})
            stderr = "boom"
        return R()

    def git(self):
        return [c for c in self.calls if c and c[0] == "git"]

    def cli(self):
        return [c for c in self.calls if not (c and c[0] == "git")]


class PermissionRequestTests(unittest.TestCase):
    """The transport reads a session's pending permission requests from
    AutoSwarm's own records and relays the caller's decision; it never
    originates one."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name)
        self.transport = AutoSwarmTransport(
            _MockStore(), str(self.home), str(self.home),
            autoswarm_argv=["autoswarm-x"])
        self.call_id = "call_" + "b" * 32
        self.directory = self.home / "calls" / self.call_id

    def tearDown(self):
        self.tmpdir.cleanup()

    def _approval(self, name, request=True, **extra):
        path = self.directory / "approvals" / ("approval_" + name * 32)
        path.mkdir(parents=True)
        if request is True:
            _write_json(path / "request.json", {
                "kind": "approval_required",
                "request_id": "approval_" + name * 32,
                "native_request": {"subtype": "can_use_tool",
                                   "tool_name": "Read", "input": {}}})
        elif request:
            (path / "request.json").write_text(request, encoding="utf-8")
        for filename in extra:
            _write_json(path / (filename + ".json"), extra[filename])
        return "approval_" + name * 32

    def test_only_undecided_requests_of_a_live_call_are_pending(self):
        self.assertEqual(self.transport.pending_requests(self.call_id), [])
        open_id = self._approval("1")
        self._approval("2", decision={"decision": "allow"})
        self._approval("3", resolution={"state": "replied"})
        self._approval("4", request=False)       # no request record yet
        self._approval("5", request='{"kind": ')  # still being written
        pending = self.transport.pending_requests(self.call_id)
        self.assertEqual([r["request_id"] for r in pending], [open_id])
        _write_json(self.directory / "result.json", {"outcome": "completed"})
        self.assertEqual(self.transport.pending_requests(self.call_id), [])

    def test_a_cancelled_call_has_nothing_pending(self):
        self._approval("1")
        _write_json(self.directory / "cancel.json", {"at": "now"})
        self.assertEqual(self.transport.pending_requests(self.call_id), [])

    def test_a_decision_is_relayed_for_one_request_once(self):
        request_id = self._approval("1")
        recorder = _Recorder()
        with patch("autobot.autoswarm_transport.subprocess.run", recorder):
            self.transport.decide_request(
                self.call_id, request_id, "allow", "inside the scope")
            self.transport.decide_request(self.call_id, request_id, "deny")
        allow, deny = recorder.calls
        self.assertEqual(allow, [
            "autoswarm-x", "approve", self.call_id, request_id,
            "--scope", "once", "--message", "inside the scope"])
        self.assertEqual(deny, [
            "autoswarm-x", "deny", self.call_id, request_id,
            "--scope", "once"])
        with self.assertRaises(ValueError):
            self.transport.decide_request(self.call_id, request_id,
                                          "maybe")
        with patch("autobot.autoswarm_transport.subprocess.run",
                   _Recorder(returncode=1)):
            with self.assertRaises(RuntimeError):
                self.transport.decide_request(self.call_id, request_id,
                                              "allow")

    def test_the_base_contract_asks_nothing(self):
        base = Transport()
        self.assertEqual(base.pending_requests(self.call_id), [])
        with self.assertRaises(NotImplementedError):
            base.decide_request(self.call_id, "approval_x", "allow")


class WorkspaceIsolationTests(unittest.TestCase):
    """A specialist given a frozen source revision works in an isolated
    worktree at it — SOURCE_MODIFYING always, READ_ONLY when the manifest
    carries one — and the worktree is removed only by this transport, only
    when asked, and only if it is one this transport made."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.workspace = Path(self.tmpdir.name) / "ws"
        self.home.mkdir()
        self.workspace.mkdir()
        self.store = _MockStore()
        self.projection = self.store.put(json.dumps({
            "engineering_context": {"guidance": "Read and report."}
        }).encode("utf-8"))
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.workspace),
            autoswarm_argv=["autoswarm-x"])
        self.call_id = "call_" + "c" * 32

    def tearDown(self):
        self.tmpdir.cleanup()

    def _manifest(self, finalization_class, source_revision=None):
        constraints = {"finalization_class": finalization_class,
                       "evidence_requirements": ["EXECUTION_LIFECYCLE"]}
        if source_revision:
            constraints["source_revision"] = source_revision
        return {"call_id": self.call_id,
                "projection_address": self.projection,
                "provider_assignment": {"provider": "claude-code",
                                        "model": "sonnet",
                                        "selected_by": "agent_factory"},
                "constraints": constraints}

    def _launch(self, manifest):
        recorder = _Recorder()
        with patch("autobot.autoswarm_transport.subprocess.run", recorder):
            self.transport.launch_manifest("sha256:" + "0" * 64, manifest)
        run = recorder.cli()[0]
        return recorder, run[run.index("--cwd") + 1]

    def test_read_only_with_a_frozen_revision_is_isolated(self):
        revision = "a" * 40
        recorder, cwd = self._launch(self._manifest("READ_ONLY", revision))
        git = recorder.git()
        self.assertEqual(len(git), 1)
        self.assertEqual(git[0][3:6], ["worktree", "add", "--detach"])
        self.assertEqual(git[0][-1], revision)
        self.assertEqual(git[0][-2], cwd)
        self.assertEqual(Path(cwd).parent, self.workspace)
        self.assertTrue(Path(cwd).name.startswith("autobot-wt-"))

    def test_read_only_without_a_revision_uses_the_shared_workspace(self):
        recorder, cwd = self._launch(self._manifest("READ_ONLY"))
        self.assertEqual(recorder.git(), [])
        self.assertEqual(cwd, str(self.workspace))

    def test_source_modifying_is_always_isolated(self):
        recorder, cwd = self._launch(self._manifest("SOURCE_MODIFYING"))
        self.assertEqual(recorder.git()[0][-1], "refs/heads/main")
        self.assertNotEqual(cwd, str(self.workspace))

    def test_a_relaunch_reuses_the_calls_workspace(self):
        given = str(self.workspace / "autobot-wt-first")
        _write_json(self.home / "calls" / self.call_id / "task.json",
                    {"call_id": self.call_id, "workspace": given})
        recorder, cwd = self._launch(
            self._manifest("SOURCE_MODIFYING", "b" * 40))
        self.assertEqual(recorder.git(), [])
        self.assertEqual(cwd, given)

    def test_staged_inputs_go_into_the_isolated_worktree_only(self):
        """The accepted predecessor results the caller stages for a call
        are written into that call's isolated worktree, byte for byte,
        under a directory Git is told to ignore; a name can never leave
        that directory; and a shared workspace is never written."""
        (self.workspace / ".git" / "info").mkdir(parents=True)
        self.transport.stage_inputs(self.call_id, [
            ("policy-trace", b"accepted result\n"),
            ("../evil name", b"x")])
        _recorder, cwd = self._launch(self._manifest("READ_ONLY", "a" * 40))
        inputs = Path(cwd) / ".autobot" / "inputs"
        self.assertEqual((inputs / "policy-trace.md").read_bytes(),
                         b"accepted result\n")
        self.assertEqual(sorted(p.name for p in inputs.iterdir()),
                         [".._evil_name.md", "policy-trace.md"])
        exclude = self.workspace / ".git" / "info" / "exclude"
        self.assertEqual(exclude.read_text(encoding="utf-8").splitlines(),
                         [".autobot/"])
        self.assertEqual(self.transport._staged, {})  # used once
        # Staged for work that is not isolated: nothing is written into
        # the shared workspace.
        self.transport.stage_inputs(self.call_id, [("x", b"y")])
        _recorder, cwd = self._launch(self._manifest("READ_ONLY"))
        self.assertEqual(cwd, str(self.workspace))
        self.assertFalse((self.workspace / ".autobot").exists())
        self.assertEqual(exclude.read_text(encoding="utf-8").splitlines(),
                         [".autobot/"])

    def _route_manifest(self, provider="codex", model="gpt-6-luna"):
        manifest = self._manifest("SOURCE_MODIFYING")
        manifest["provider_assignment"] = {
            "provider": provider, "model": model,
            "selected_by": "agent_factory"}
        return manifest

    def test_the_routes_identifiers_and_arguments_reach_the_provider(self):
        """The manifest's provider and model are passed to AutoSwarm
        exactly; the route's reasoning effort and native session
        arguments, staged by the caller for the call, are passed beside
        them, with the arguments' placeholders filled from the workspace
        this session launches in; and they are used for that one launch
        only."""
        (self.workspace / ".git").mkdir()
        gate = Path(self.tmpdir.name) / "gate"
        (gate / "pending").mkdir(parents=True)
        self.transport = AutoSwarmTransport(
            self.store, str(self.home), str(self.workspace),
            gate_root=str(gate), autoswarm_argv=["autoswarm-x"])
        self.transport.stage_session(self.call_id, [
            "--sandbox", "workspace-write", "-c",
            "roots=['{gate_pending}', '{gate_pending_real}', '{repo_git}',"
            " '{workspace}']"], effort="max")
        recorder, cwd = self._launch(self._route_manifest())
        run = recorder.cli()[0]
        self.assertEqual(run[:8], ["autoswarm-x", "run", "--agent", "codex",
                                   "--model", "gpt-6-luna", "--effort",
                                   "max"])
        native = [a[len("--provider-arg="):] for a in run
                  if a.startswith("--provider-arg=")]
        pending = str(gate / "pending")
        self.assertEqual(native, [
            "--sandbox", "workspace-write", "-c",
            "roots=['%s', '%s', '%s', '%s']" % (
                pending, os.path.realpath(pending),
                str(self.workspace / ".git"), cwd)])
        self.assertLess(run.index("--provider-arg=--sandbox"),
                        run.index("--no-parent"))
        self.assertEqual(self.transport._session, {})  # used once
        again, _cwd = self._launch(self._route_manifest())
        self.assertFalse([a for a in again.cli()[0]
                          if a.startswith("--provider-arg=")])
        self.assertNotIn("--effort", again.cli()[0])

    def test_a_route_with_no_effort_or_arguments_launches_as_before(self):
        recorder, cwd = self._launch(self._route_manifest(
            provider="claude-code", model="sonnet"))
        run = recorder.cli()[0]
        self.assertEqual(run[:5], ["autoswarm-x", "run", "--agent",
                                   "claude-code", "--model"])
        self.assertEqual(run[5:11], ["sonnet", "--no-parent",
                                     "--background", "--cwd", cwd,
                                     "--call-id"])
        self.assertNotIn("--effort", run)

    def test_an_argument_this_session_cannot_fill_stops_the_launch(self):
        """A route argument naming the command-gate directory cannot be
        filled by a transport that has none: the provider is never
        started on a half-filled argument."""
        self.transport.stage_session(self.call_id, [
            "-c", "roots=['{gate_pending}']"])
        recorder = _Recorder()
        with patch("autobot.autoswarm_transport.subprocess.run", recorder):
            with self.assertRaises(RuntimeError) as raised:
                self.transport.launch_manifest(
                    "sha256:" + "0" * 64, self._route_manifest())
        self.assertIn("{gate_pending}", str(raised.exception))
        self.assertEqual(recorder.cli(), [])

    def test_a_session_configuration_is_applied_only_to_its_provider(self):
        """F-TF-28: one route's sandbox and approval arguments are never
        handed to another provider's session. Configuration staged for
        one provider stops the launch of a manifest that names another,
        before the provider is started."""
        staged = ["--sandbox", "workspace-write"]
        self.transport.stage_session(self.call_id, staged, effort="max",
                                     provider="codex")
        recorder = _Recorder()
        with patch("autobot.autoswarm_transport.subprocess.run", recorder):
            with self.assertRaises(RuntimeError) as raised:
                self.transport.launch_manifest(
                    "sha256:" + "0" * 64, self._route_manifest(
                        provider="claude-code", model="sonnet"))
        self.assertIn("is for provider codex", str(raised.exception))
        self.assertIn("names claude-code", str(raised.exception))
        self.assertEqual(recorder.cli(), [])
        # For the provider it was staged for, it is applied.
        self.transport.stage_session(self.call_id, staged, effort="max",
                                     provider="codex")
        recorder, _cwd = self._launch(self._route_manifest())
        self.assertIn("--provider-arg=--sandbox", recorder.cli()[0])
        self.assertIn("--effort", recorder.cli()[0])

    def test_a_route_probe_runs_outside_any_attempt(self):
        """A preflight session gets the route — provider, model, effort,
        session arguments, an isolated worktree — and no manifest and no
        caller-supplied call id, because no attempt stands behind it."""
        recorder = _Recorder()
        with patch("autobot.autoswarm_transport.subprocess.run", recorder):
            launched = self.transport.probe_route(
                {"provider": "codex", "model": "gpt-6-luna",
                 "effort": "max",
                 "provider_args": ["--sandbox", "workspace-write"]},
                "Read and report.")
        self.assertEqual(recorder.git()[0][3:6],
                         ["worktree", "add", "--detach"])
        self.assertEqual(recorder.git()[0][-1], "refs/heads/main")
        run = recorder.cli()[0]
        self.assertEqual(run[:8], ["autoswarm-x", "run", "--agent", "codex",
                                   "--model", "gpt-6-luna", "--effort",
                                   "max"])
        self.assertIn("--provider-arg=workspace-write", run)
        self.assertNotIn("--manifest", run)
        self.assertNotIn("--call-id", run)
        self.assertEqual(run[run.index("--cwd") + 1],
                         launched["workspace"])
        self.assertTrue(Path(launched["workspace"]).name.startswith(
            "autobot-wt-"))
        self.assertEqual(launched["call_id"], "call_" + "a" * 32)

    def test_release_removes_only_its_own_worktree(self):
        task = self.home / "calls" / self.call_id / "task.json"
        mine = self.workspace / "autobot-wt-mine"
        mine.mkdir()

        def release():
            recorder = _Recorder()
            with patch("autobot.autoswarm_transport.subprocess.run",
                       recorder):
                return self.transport.release_workspace(self.call_id), \
                    recorder.git()

        self.assertEqual(release(), (False, []))            # no call record
        _write_json(task, {"workspace": str(mine)})
        released, git = release()
        self.assertTrue(released)
        self.assertEqual(git[0][3:6], ["worktree", "remove", "--force"])
        self.assertEqual(git[0][-1], str(mine))
        # The shared workspace itself is never removed.
        _write_json(task, {"workspace": str(self.workspace)})
        self.assertEqual(release(), (False, []))
        # Nor a directory that merely looks like one of ours elsewhere.
        elsewhere = Path(self.tmpdir.name) / "autobot-wt-elsewhere"
        elsewhere.mkdir()
        _write_json(task, {"workspace": str(elsewhere)})
        self.assertEqual(release(), (False, []))
        # Nor one that no longer exists.
        _write_json(task, {"workspace": str(self.workspace
                                            / "autobot-wt-gone")})
        self.assertEqual(release(), (False, []))
