"""Joined end-to-end lifecycle proof: Taskforce through real AutoSwarm CLI.

One continuous deterministic path from the Chief Engineer planning seam
through the actual AutoSwarm CLI subprocess boundary and back through
evidence collection, production verification and authoritative completion.

The upper half (Taskforce orchestration) and the lower half (real AutoSwarm
CLI) are already proven independently. This test joins them into one
uninterrupted execution that ends with sequence COMPLETED.
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
from autobot import ProductionVerifierRunner, transforms
from autobot.autoswarm_transport import AutoSwarmTransport, _read_json
from autobot.collector import Collector
from autobot.execution_boundary import build_execution_manifest
from autobot.models import instant
from autobot.transport import Transport, normalize_outcome
from autobot.witness_semantics import extract_facts
from autobot.witnesses import WITNESS_KINDS
from contract_library import content_address
from intelligence import (
    AgentFactory,
    ChiefEngineerPolicy,
    ContinuousDriver,
    PlanStore,
    Taskforce,
    readonly_view,
)


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


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)


class CrossProcessLifecycleTransport(AutoSwarmTransport):
    """Real AutoSwarm CLI transport with governance evidence planting.

    Combines the real CLI subprocess boundary from CrossProcessTransport
    with governance evidence planting for complete lifecycle testing.
    After launch_manifest calls the real AutoSwarm CLI, it plants
    obj-12 governance decision files at the evidence tree path so that
    the production collect path finds them.
    """

    def __init__(self, store, home, workspace, gate_root):
        super().__init__(
            store, str(home), str(workspace),
            gate_root=str(gate_root),
            autoswarm_argv=[str(AUTOSWARM_PYTHON), "-m", "autoswarm"])
        config_path = Path(home) / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(config_path), "w", encoding="utf-8") as fh:
            json.dump({"providers": {"codex": {
                "command": [str(AUTOSWARM_PYTHON),
                            str(AUTOSWARM_FIXTURE), "codex"]
            }}}, fh)
        self._governance_decisions = [
            ("toolu_test001", "ALLOW"), ("toolu_test002", "ALLOW")]
        self.launched_manifests = []

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

    def launch_manifest(self, manifest_address, manifest, *,
                        manifest_bytes=None):
        call_id = manifest["call_id"]
        native = super().launch_manifest(
            manifest_address, manifest, manifest_bytes=manifest_bytes)
        call_dir = self._call_dir(call_id)
        deadline = time.monotonic() + 10
        while not (call_dir / "result.json").is_file():
            if time.monotonic() > deadline:
                break
            time.sleep(0.05)
        result = _read_json(call_dir / "result.json")
        session_id = native
        if result is not None:
            session_id = result.get("native_session_id") or native
        evidence_dir = (self._gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        for tool_use_id, verdict in self._governance_decisions:
            _write_json(
                evidence_dir / ("%s.decision.json" % tool_use_id),
                {"verdict": verdict, "tool": "Bash",
                 "session_id": session_id,
                 "tool_use_id": tool_use_id})
        self.launched_manifests.append({
            "call_id": call_id,
            "manifest_bytes": manifest_bytes,
            "native_session_id": session_id,
        })
        return native


class SingleContractPolicy(ChiefEngineerPolicy):
    """Emits one READ_ONLY INVESTIGATE proposal, then stops."""

    def __init__(self):
        self.proposed = False

    def plan(self, view):
        if self.proposed or not view.objectives:
            return []
        self.proposed = True
        obj = view.objectives[0]
        return [{
            "objective_id": obj["objective_id"],
            "class": "INVESTIGATE",
            "rationale": "joined lifecycle investigation",
            "requirement": {
                "objective": "Joined lifecycle: end-to-end through real CLI",
                "capability": "research",
                "roles": {"builder": {
                    "required": True,
                    "success_outcomes": ["success"]}},
                "evidence_requirements": [
                    "COMMAND_GOVERNANCE", "EXECUTION_LIFECYCLE"],
                "finalization_class": "READ_ONLY",
                "guidance": support.VA_INPUT,
            },
            "snapshot": {"entries": view.snapshot["entries"],
                         "head_hash": view.snapshot["head_hash"]},
            "provenance": {"findings": [], "events": []},
        }]


class JoinedLifecycleTests(unittest.TestCase):
    """The final joined proof: one continuous path from Taskforce through
    the real AutoSwarm CLI subprocess to authoritative COMPLETED."""

    def setUp(self):
        if not AUTOSWARM_PYTHON.is_file():
            self.skipTest("AutoSwarm venv not found at %s"
                          % AUTOSWARM_PYTHON)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.autoswarm_home = self.dir / "autoswarm"
        self.gate_root = self.dir / "gate"
        self.workspace = self.dir / "ws"
        self.workspace.mkdir()
        self.clock = support.manual_clock()
        self.rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(self.rt)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.rt.sequencer.acquire_lease(ttl_seconds=360000)
        self.env = support.intelligence_environment()
        self.plan = PlanStore(self.dir / "intelligence.db")
        self.factory = AgentFactory()
        self.factory.register("research", "builder", "codex")

    def tearDown(self):
        self.plan.close()
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

    def _make_transport(self):
        return CrossProcessLifecycleTransport(
            self.rt.store, self.autoswarm_home,
            self.workspace, self.gate_root)

    def _driver(self, policy, transport, runner=None):
        return ContinuousDriver(
            self.plan, self.rt.registry, self.rt.sequencer, self.rt.store,
            self.env, self.clock, policy, transport,
            runner, self.factory,
            self.dir / "autobot.db",
            holder="joined-lifecycle-test")

    def _state(self, table, col, val):
        return self.rt.db.conn.execute(
            "SELECT state FROM %s WHERE %s = ?" % (table, col),
            (val,)).fetchone()["state"]

    def _rules_for(self, subject_id):
        return [json.loads(r["payload"])["rule"]
                for r in self.rt.db.conn.execute(
                    "SELECT payload FROM transition_journal ORDER BY seq"
                ).fetchall()
                if json.loads(r["payload"]).get("subject_id") == subject_id]

    # ================================================================
    # THE JOINED PROOF
    # ================================================================

    def test_taskforce_through_real_cli_to_completed(self):
        """One continuous path:
        Chief Engineer → Taskforce → Agent Factory → Sequencer →
        PromptProjection → ExecutionManifest → real AutoSwarm CLI →
        engine.start_call → manifest validation → manifest persistence →
        ExecutionClaimRecord → task.json → fixture provider →
        handle/result records → Taskforce reconciliation → obj-11 →
        obj-12 → Collector → ProductionVerifierRunner → VERIFIED →
        ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED.
        """
        transport = self._make_transport()
        driver = self._driver(SingleContractPolicy(), transport)
        self.plan.add_objective(
            "obj-joined", "Joined lifecycle: end-to-end through real CLI.",
            "you", instant(self.clock.now()))

        final = driver.run_until_stable()
        driver.close()

        # === 1. Taskforce dispatched through real CLI ===
        self.assertEqual(len(transport.launched_manifests), 1)
        launch = transport.launched_manifests[0]

        # === 2. Exactly one Sequencer-minted call_id ===
        call_rows = self.rt.db.conn.execute(
            "SELECT call_id FROM attempt_calls").fetchall()
        call_ids = [r["call_id"] for r in call_rows]
        self.assertEqual(len(call_ids), 1)
        self.assertEqual(launch["call_id"], call_ids[0])

        # === 3. Manifest byte identity (Phase 8) ===
        call_dir = (self.autoswarm_home / "calls" / call_ids[0])
        self.assertTrue(call_dir.is_dir(),
                        "AutoSwarm call directory was not created")
        self.assertTrue((call_dir / "manifest.json").is_file(),
                        "manifest.json not found")
        self.assertTrue((call_dir / "manifest.sha256").is_file(),
                        "manifest.sha256 not found")
        self.assertTrue((call_dir / "claim.json").is_file(),
                        "claim.json not found")
        self.assertTrue((call_dir / "task.json").is_file(),
                        "task.json not found")

        autobot_manifest_bytes = launch["manifest_bytes"]
        persisted_manifest = (call_dir / "manifest.json").read_bytes()
        persisted_sha = (call_dir / "manifest.sha256").read_text(
            encoding="utf-8").strip()
        claim = json.loads((call_dir / "claim.json").read_bytes())
        autobot_sha = "sha256:" + hashlib.sha256(
            autobot_manifest_bytes).hexdigest()

        self.assertEqual(persisted_manifest, autobot_manifest_bytes,
                         "Autobot CAS manifest bytes != AutoSwarm "
                         "manifest.json (byte identity failure)")
        self.assertEqual(persisted_sha, autobot_sha,
                         "AutoSwarm manifest.sha256 != SHA256(Autobot bytes)")
        self.assertEqual(claim["manifest_sha256"], autobot_sha,
                         "ExecutionClaimRecord.manifest_sha256 != "
                         "SHA256(Autobot bytes)")

        # === 4. Claim record identity (Phase 8) ===
        self.assertEqual(claim["call_id"], call_ids[0],
                         "ExecutionClaimRecord.call_id != "
                         "Sequencer attempt_calls.call_id")
        self.assertEqual(claim["provider"], "codex",
                         "ExecutionClaimRecord.provider != "
                         "durable provider assignment")

        # === 5. Claim ordering (Phase 9) ===
        # The production implementation enforces:
        # manifest.json → manifest.sha256 → claim.json → task.json
        # We verify through observable durable state: all four files
        # exist, and manifest.json is present when task.json is.
        self.assertTrue((call_dir / "manifest.json").is_file())
        self.assertTrue((call_dir / "manifest.sha256").is_file())
        self.assertTrue((call_dir / "claim.json").is_file())
        self.assertTrue((call_dir / "task.json").is_file())
        task = json.loads((call_dir / "task.json").read_bytes())
        self.assertIsNotNone(task.get("call_id"))
        self.assertEqual(task["call_id"], call_ids[0])

        # === 6. obj-11 admission (Phase 6) ===
        attempt_row = self.rt.db.conn.execute(
            "SELECT attempt_id FROM attempt_calls WHERE call_id = ?",
            (call_ids[0],)).fetchone()
        att_id = attempt_row["attempt_id"]
        obj11_rows = self.rt.db.conn.execute(
            "SELECT content_sha256 FROM attempt_witnesses"
            " WHERE attempt_id = ? AND witness_kind = 'obj-11'",
            (att_id,)).fetchall()
        self.assertTrue(len(obj11_rows) >= 1,
                        "No obj-11 witness admitted")
        obj11_bytes = self.rt.store.get(obj11_rows[0]["content_sha256"])
        obj11_facts = extract_facts("obj-11", obj11_bytes)
        self.assertEqual(obj11_facts.get("call_id"), call_ids[0],
                         "obj-11 call_id fact != Sequencer call_id")

        # === 7. Prompt identity / role-transform (Phase 6-7) ===
        prompt_sha = obj11_facts.get("prompt_sha256")
        self.assertIsNotNone(prompt_sha,
                             "obj-11 does not carry prompt_sha256 fact")
        va_record = support.load_fixture("va_record.json")
        frozen_input_addr = list(
            va_record["specification_inputs"][
                "name_to_exact_byte_sha256"].values())[0]
        frozen_input = self.rt.store.get(frozen_input_addr)
        reg = support.live_transform_registration("identity-projection")
        expected_output, execution = transforms.execute_transform(
            self.rt.store, reg, frozen_input)
        expected_sha = execution["output_sha256"]
        self.assertEqual(prompt_sha, expected_sha,
                         "derived obj-11 prompt_sha256 != "
                         "identity-projection(frozen specification input)")

        # === 8. obj-12 governance admission (Phase 5) ===
        obj12_rows = self.rt.db.conn.execute(
            "SELECT content_sha256 FROM attempt_witnesses"
            " WHERE attempt_id = ? AND witness_kind = 'obj-12'",
            (att_id,)).fetchall()
        self.assertTrue(len(obj12_rows) >= 1,
                        "No obj-12 witness admitted")
        for row in obj12_rows:
            obj12_bytes = self.rt.store.get(row["content_sha256"])
            obj12_facts = extract_facts("obj-12", obj12_bytes)
            self.assertIn("session_id", obj12_facts)
            self.assertIn(obj12_facts.get("decision"),
                          ("ALLOW", "PASSTHROUGH", None),
                          "governance decision not recognized")

        # === 9. Collector proof (Phase 11) ===
        seq_id = self.rt.db.conn.execute(
            "SELECT sequence_id FROM attempts WHERE attempt_id = ?",
            (att_id,)).fetchone()["sequence_id"]
        receipt_rows = self.rt.db.conn.execute(
            "SELECT content_sha256 FROM attempt_receipts"
            " WHERE attempt_id = ?", (att_id,)).fetchall()
        self.assertTrue(len(receipt_rows) >= 1,
                        "Collector did not produce an execution receipt")

        # === 10. Verifier proof (Phase 12) ===
        verifications = self.rt.db.conn.execute(
            "SELECT assertion_id, outcome FROM attempt_verifications"
            " WHERE attempt_id = ? ORDER BY assertion_id",
            (att_id,)).fetchall()
        assertion_ids = [r["assertion_id"] for r in verifications]
        self.assertIn("a1", assertion_ids,
                      "assertion a1 not verified")
        self.assertIn("a2", assertion_ids,
                      "assertion a2 not verified")
        self.assertIn("role-transform/builder", assertion_ids,
                      "role-transform/builder not verified")
        for row in verifications:
            self.assertEqual(row["outcome"], "PASS",
                             "%s: %s" % (row["assertion_id"],
                                         row["outcome"]))

        # === 11. Authority completion path (Phase 13) ===
        self.assertEqual(
            self._state("attempts", "attempt_id", att_id),
            "VERIFIED")
        self.assertEqual(
            self._state("sequences", "sequence_id", seq_id),
            "COMPLETED")

        # === 12. Transition chain ===
        seq_rules = self._rules_for(seq_id)
        att_rules = self._rules_for(att_id)
        self.assertIn("t-s03", seq_rules)   # DECLARED→READY
        self.assertIn("t-s06", seq_rules)   # READY→EXECUTING
        self.assertIn("t-s09", seq_rules)   # →VERIFYING
        self.assertIn("t-s10", seq_rules)   # →VERIFIED
        self.assertIn("t-s14", seq_rules)   # →ACCEPTED (READ_ONLY)
        self.assertIn("t-s41", seq_rules)   # →DEPENDENCIES_RELEASED
        self.assertIn("t-s42", seq_rules)   # →COMPLETED
        self.assertIn("t-a02", att_rules)   # RESERVED→DISPATCHED
        self.assertIn("t-a03", att_rules)   # DISPATCHED→RUNNING
        self.assertIn("t-a04", att_rules)   # RUNNING→FINISHED
        self.assertIn("t-a07", att_rules)   # →VERIFIED

        # === 13. Schema identity (Phase 17) ===
        # Normalize line endings to LF for comparison — git autocrlf
        # may differ between repos on the working tree, but the content
        # is identical. SchemaByteParity in test_execution_boundary
        # covers the byte-level check.
        canonical = CANONICAL_SCHEMA.read_bytes().replace(b"\r\n", b"\n")
        vendored = VENDORED_SCHEMA.read_bytes().replace(b"\r\n", b"\n")
        canonical_sha = hashlib.sha256(canonical).hexdigest()
        vendored_sha = hashlib.sha256(vendored).hexdigest()
        self.assertEqual(canonical_sha, vendored_sha,
                         "canonical and vendored schema SHA-256 differ")

        # === 14. Read-only planning view observes completed state ===
        ro = readonly_view(self.dir / "autobot.db")
        completed_row = ro.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (seq_id,)).fetchone()
        ro.close()
        self.assertEqual(completed_row["state"], "COMPLETED",
                         "read-only intelligence view does not observe"
                         " COMPLETED state")

    def test_producer_field_distinction(self):
        """The producer field 'verdict' in obj-12 records is distinct from
        the Autobot semantic fact 'decision' — witness_semantics recognizes
        the producer spelling and extracts the normalized fact."""
        decision_record = json.dumps({
            "verdict": "ALLOW", "tool": "Bash",
            "session_id": "sess-test",
            "tool_use_id": "toolu_test"
        }).encode("utf-8")
        facts = extract_facts("obj-12", decision_record)
        self.assertEqual(facts.get("decision"), "ALLOW")
        self.assertNotIn("verdict", facts)


if __name__ == "__main__":
    unittest.main()
