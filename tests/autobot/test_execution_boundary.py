"""ExecutionManifest boundary tests.

Proves: schema byte-identity across repos, manifest builder derives
from durable state, launch gate wires through, the manifest validates
correctly, and the real cross-process boundary preserves byte identity.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import support
from autobot.autoswarm_transport import AutoSwarmTransport
from autobot.execution_boundary import build_execution_manifest, manifest_schema
from autobot.execution_boundary.launch_gate import launch_with_manifest
from autobot.models import ConflictError
from contract_library import content_address
from contract_library.schema_profile import validate as schema_validate


AUTOSWARM_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "source" / "AutoSwarm")
AUTOSWARM_PYTHON = AUTOSWARM_ROOT / ".venv" / "Scripts" / "python.exe"
AUTOSWARM_FIXTURE = AUTOSWARM_ROOT / "tests" / "fixtures" / "provider.py"


CANONICAL_SCHEMA = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "autobot" / "execution_boundary" / "schemas"
    / "execution_manifest.schema.json")

VENDORED_SCHEMA = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "source" / "AutoSwarm" / "src" / "autoswarm"
    / "autobot_boundary" / "schemas"
    / "execution_manifest.schema.json")


class SchemaByteParity(unittest.TestCase):
    """The vendored schema in AutoSwarm must be byte-identical to the
    canonical schema in Autobot."""

    def test_vendored_schema_matches_canonical(self):
        canonical = CANONICAL_SCHEMA.read_bytes()
        self.assertTrue(VENDORED_SCHEMA.is_file(),
                        "vendored schema not found at %s" % VENDORED_SCHEMA)
        vendored = VENDORED_SCHEMA.read_bytes()
        self.assertEqual(canonical, vendored,
                         "vendored schema bytes differ from canonical")


class ManifestBuilderTests(unittest.TestCase):
    """The manifest builder derives every field from durable state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, self.va = support.admitted_runtime(self.dir,
                                                    clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _dispatch(self):
        record = support.local_record_payload(
            finalization_class="READ_ONLY",
            evidence_requirements=["COMMAND_GOVERNANCE",
                                   "EXECUTION_LIFECYCLE"])
        admitted = self.rt.registry.admit_payload(record,
                                                  submitted_by="planner")
        self.seq.mark_ready(admitted["sequence_id"])
        info = self.seq.start_execution(admitted["sequence_id"])
        self.seq.reserve_attempt(info["attempt_id"],
                                 expected_fence=info["fence"])
        projection = self.rt.store.put(b"projection for manifest test\n")
        dispatched = self.seq.dispatch_attempt(
            info["attempt_id"], "builder", "codex", projection)
        return info, dispatched, admitted

    def test_manifest_built_from_durable_state(self):
        info, dispatched, admitted = self._dispatch()
        assignment = {"provider": "codex", "model": None,
                      "selected_by": "agent_factory",
                      "role": "builder", "expected_output": ""}
        data, address = build_execution_manifest(
            self.seq, info["attempt_id"], assignment, "research")
        manifest = json.loads(data)
        self.assertEqual(manifest["kind"], "ExecutionManifest")
        self.assertEqual(manifest["call_id"], dispatched["call_id"])
        self.assertEqual(manifest["attempt_id"], info["attempt_id"])
        self.assertEqual(manifest["role"], "builder")
        self.assertEqual(manifest["capability"], "research")
        self.assertEqual(
            manifest["constraints"]["finalization_class"], "READ_ONLY")
        self.assertEqual(
            manifest["constraints"]["evidence_requirements"],
            ["COMMAND_GOVERNANCE", "EXECUTION_LIFECYCLE"])
        self.assertEqual(
            manifest["provider_assignment"]["provider"], "codex")
        self.assertEqual(
            manifest["provider_assignment"]["selected_by"],
            "agent_factory")

    def test_manifest_validates_against_schema(self):
        info, dispatched, admitted = self._dispatch()
        assignment = {"provider": "codex", "model": None,
                      "selected_by": "agent_factory"}
        data, _ = build_execution_manifest(
            self.seq, info["attempt_id"], assignment, "research")
        manifest = json.loads(data)
        issues = schema_validate(manifest, manifest_schema())
        self.assertEqual(issues, [])

    def test_manifest_carries_role_transform(self):
        info, dispatched, admitted = self._dispatch()
        assignment = {"provider": "codex", "model": None,
                      "selected_by": "agent_factory"}
        data, _ = build_execution_manifest(
            self.seq, info["attempt_id"], assignment, "research")
        manifest = json.loads(data)
        self.assertIsNotNone(manifest.get("role_transform"))
        self.assertEqual(manifest["role_transform"]["transform_id"],
                         "identity-projection")

    def test_manifest_is_canonical_and_content_addressed(self):
        info, dispatched, admitted = self._dispatch()
        assignment = {"provider": "codex", "model": None,
                      "selected_by": "agent_factory"}
        data1, addr1 = build_execution_manifest(
            self.seq, info["attempt_id"], assignment, "research")
        data2, addr2 = build_execution_manifest(
            self.seq, info["attempt_id"], assignment, "research")
        self.assertEqual(data1, data2)
        self.assertEqual(addr1, addr2)

    def test_manifest_refuses_non_dispatched(self):
        record = support.local_record_payload(
            task_contract_id="tc_" + "0bad" * 8,
            objective="should fail",
            finalization_class="READ_ONLY")
        admitted = self.rt.registry.admit_payload(record,
                                                  submitted_by="planner")
        self.seq.mark_ready(admitted["sequence_id"])
        info = self.seq.start_execution(admitted["sequence_id"])
        self.seq.reserve_attempt(info["attempt_id"],
                                 expected_fence=info["fence"])
        with self.assertRaises(ConflictError):
            build_execution_manifest(
                self.seq, info["attempt_id"],
                {"provider": "codex", "selected_by": "agent_factory"},
                "research")


class CrossProcessTransport(AutoSwarmTransport):
    """Real AutoSwarm CLI transport for cross-process integration tests."""

    def __init__(self, store, home, workspace):
        super().__init__(
            store, str(home), str(workspace),
            autoswarm_argv=[str(AUTOSWARM_PYTHON), "-m", "autoswarm"])
        config_path = Path(home) / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(config_path), "w", encoding="utf-8") as fh:
            json.dump({"providers": {"codex": {
                "command": [str(AUTOSWARM_PYTHON),
                            str(AUTOSWARM_FIXTURE), "codex"]
            }}}, fh)

    def _cli(self, args, *, stdin_text=None):
        env = dict(os.environ)
        env["AUTOSWARM_HOME"] = str(self._home)
        src = str(AUTOSWARM_ROOT / "src")
        env["PYTHONPATH"] = src + (
            os.pathsep + env["PYTHONPATH"]
            if env.get("PYTHONPATH") else "")
        result = subprocess.run(
            [*self._argv, *args],
            input=stdin_text, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            payload = None
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                pass
            if payload and not payload.get("ok"):
                error = payload.get("error", {})
                raise RuntimeError(
                    "autoswarm %s: [%s] %s"
                    % (args[0], error.get("code", "?"),
                       error.get("message", result.stderr)))
            raise RuntimeError(
                "autoswarm %s exited %d: %s"
                % (args[0], result.returncode, result.stderr))
        return json.loads(result.stdout).get("data")


class CrossProcessIntegrationTests(unittest.TestCase):
    """The actual Autobot-to-AutoSwarm CLI process boundary.

    Exercises: Autobot dispatch → build_execution_manifest → CAS →
    AutoSwarmTransport.launch_manifest → temp manifest file →
    real AutoSwarm CLI subprocess → engine.start_call → manifest
    validation → manifest persistence → ExecutionClaimRecord.
    """

    def setUp(self):
        if not AUTOSWARM_PYTHON.is_file():
            self.skipTest("AutoSwarm venv not found at %s"
                          % AUTOSWARM_PYTHON)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.autoswarm_home = self.dir / "autoswarm"
        self.workspace = self.dir / "ws"
        self.workspace.mkdir()
        self.clock = support.manual_clock()
        self.rt, self.va = support.admitted_runtime(self.dir,
                                                    clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)

    def tearDown(self):
        self.rt.db.close()
        for d in (self.autoswarm_home / "calls").glob("call_*"):
            try:
                handle = json.loads(
                    (d / "handle.json").read_text(encoding="utf-8"))
                if handle.get("pid"):
                    import signal
                    try:
                        os.kill(handle["pid"], signal.SIGTERM)
                    except OSError:
                        pass
            except (FileNotFoundError, json.JSONDecodeError, KeyError):
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _dispatch(self):
        record = support.local_record_payload(
            finalization_class="READ_ONLY",
            evidence_requirements=["COMMAND_GOVERNANCE",
                                   "EXECUTION_LIFECYCLE"])
        admitted = self.rt.registry.admit_payload(record,
                                                  submitted_by="planner")
        self.rt.sequencer.mark_ready(admitted["sequence_id"])
        info = self.rt.sequencer.start_execution(admitted["sequence_id"])
        self.rt.sequencer.reserve_attempt(info["attempt_id"],
                                          expected_fence=info["fence"])
        projection = self.rt.store.put(json.dumps({
            "engineering_context": {"guidance": support.VA_INPUT}
        }).encode("utf-8"))
        dispatched = self.rt.sequencer.dispatch_attempt(
            info["attempt_id"], "builder", "codex", projection)
        return info, dispatched, admitted

    def test_manifest_bytes_cross_process_identity(self):
        """The exact canonical manifest bytes Autobot produced cross the
        CLI process boundary byte-identically."""
        info, dispatched, admitted = self._dispatch()
        assignment = {"provider": "codex", "model": None,
                      "selected_by": "agent_factory",
                      "role": "builder", "expected_output": ""}
        manifest_bytes, manifest_address = build_execution_manifest(
            self.rt.sequencer, info["attempt_id"], assignment, "research")
        autobot_sha = "sha256:" + hashlib.sha256(
            manifest_bytes).hexdigest()

        transport = CrossProcessTransport(
            self.rt.store, self.autoswarm_home, self.workspace)
        manifest = json.loads(manifest_bytes)
        native = transport.launch_manifest(
            manifest_address, manifest,
            manifest_bytes=manifest_bytes)

        call_dir = (self.autoswarm_home / "calls"
                    / dispatched["call_id"])
        self.assertTrue(call_dir.is_dir(),
                        "AutoSwarm call directory was not created")
        self.assertTrue((call_dir / "manifest.json").is_file())
        self.assertTrue((call_dir / "manifest.sha256").is_file())
        self.assertTrue((call_dir / "claim.json").is_file())
        self.assertTrue((call_dir / "task.json").is_file())

        persisted = (call_dir / "manifest.json").read_bytes()
        persisted_sha = (call_dir / "manifest.sha256").read_text(
            encoding="utf-8").strip()
        claim = json.loads(
            (call_dir / "claim.json").read_bytes())

        self.assertEqual(persisted, manifest_bytes,
                         "manifest bytes are not byte-identical")
        self.assertEqual(persisted_sha, autobot_sha)
        self.assertEqual(claim["manifest_sha256"], autobot_sha)
        self.assertEqual(claim["provider"], "codex")
        self.assertEqual(claim["kind"], "ExecutionClaimRecord")

    def test_launch_gate_passes_canonical_bytes(self):
        """launch_with_manifest passes the canonical bytes through the
        transport, not a re-serialization."""
        info, dispatched, admitted = self._dispatch()
        assignment = {"provider": "codex", "model": None,
                      "selected_by": "agent_factory",
                      "role": "builder", "expected_output": ""}

        captured = {}

        class CapturingTransport(CrossProcessTransport):
            def launch_manifest(self, addr, mfst, *, manifest_bytes=None):
                captured["manifest_bytes"] = manifest_bytes
                return super().launch_manifest(
                    addr, mfst, manifest_bytes=manifest_bytes)

        transport = CapturingTransport(
            self.rt.store, self.autoswarm_home, self.workspace)
        result = launch_with_manifest(
            self.rt.sequencer, transport, info["attempt_id"],
            assignment, "research")

        self.assertIn("manifest_bytes", captured)
        self.assertIsNotNone(captured["manifest_bytes"])
        canonical, _ = build_execution_manifest(
            self.rt.sequencer, info["attempt_id"], assignment, "research")
        self.assertEqual(captured["manifest_bytes"], canonical,
                         "launch gate did not pass canonical bytes")


if __name__ == "__main__":
    unittest.main()
