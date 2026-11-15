"""Exact pilot fidelity gate: one complete READ_ONLY contract driven
through the actual Taskforce/Transport orchestration path with
controlled AutoSwarm fixtures.

Not a manual reproduction of Sequencer transitions — the Taskforce's
advance() method drives dispatch, adoption, collection, verification,
acceptance, release and completion through the registered production
code paths.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import ProductionVerifierRunner
from autobot.autoswarm_transport import AutoSwarmTransport, _read_json
from autobot.collector import Collector
from autobot.models import ConflictError, instant
from autobot.projection import project_prompt
from autobot.receipts import EVIDENCE_KIND_BY_REQUIREMENT
from autobot.transport import Transport, reconcile_dispatched, normalize_outcome
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


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)


class AutoSwarmFixtureTransport(AutoSwarmTransport):
    """Production AutoSwarmTransport over controlled mock call dirs.

    launch() creates the AutoSwarm fixture files that the real
    find_session/collect methods then read — proving the complete
    production read path without a real provider process."""

    def __init__(self, store, home, workspace, gate_root, *,
                 governance_decisions=None, result_content=None,
                 result_outcome="completed"):
        super().__init__(store, str(home), str(workspace),
                         gate_root=str(gate_root))
        self._governance = governance_decisions or [
            ("toolu_001", "ALLOW"), ("toolu_002", "ALLOW")]
        self._result_content = result_content
        self._result_outcome = result_outcome
        self._fixture_session_id = "sess-pilot-fidelity"
        self.launched = []

    def launch(self, projection_address, provider, role, call_id):
        projection_bytes = self._store.get(projection_address)
        projection = json.loads(projection_bytes)
        prompt = projection["engineering_context"]["guidance"]
        sid = self._fixture_session_id
        d = self._call_dir(call_id)
        _write_json(d / "task.json",
                     {"call_id": call_id, "provider": provider,
                      "prompt": prompt,
                      "workspace": str(self._workspace),
                      "native_session_id": sid})
        _write_json(d / "handle.json",
                     {"pid": 999, "native_session_id": sid,
                      "phase": "execution"})
        result_data = {"call_id": call_id, "outcome": self._result_outcome,
                       "native_session_id": sid, "provider": provider}
        if self._result_content is not None:
            result_data["content"] = self._result_content
        _write_json(d / "result.json", result_data)
        evidence_dir = (self._gate_root / "evidence" / "integrity"
                        / "sessions" / sid / "commands")
        for tool_use_id, verdict in self._governance:
            _write_json(evidence_dir / ("%s.decision.json" % tool_use_id),
                         {"verdict": verdict, "tool": "Read",
                          "session_id": sid,
                          "tool_use_id": tool_use_id})
        self.launched.append({"call_id": call_id, "prompt": prompt,
                              "provider": provider, "role": role})
        return sid

    def launch_manifest(self, manifest_address, manifest):
        """Fixture override: extract prompt from the projection and
        delegate to the fixture's launch() which creates mock files."""
        projection_bytes = self._store.get(manifest["projection_address"])
        projection = json.loads(projection_bytes)
        prompt = projection["engineering_context"]["guidance"]
        call_id = manifest["call_id"]
        provider = manifest["provider_assignment"]["provider"]
        role = manifest["role"]
        return self.launch(manifest["projection_address"], provider,
                           role, call_id)


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
            "rationale": "pilot fidelity investigation",
            "requirement": {
                "objective": "Pilot fidelity: investigate the logging layer",
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


class PilotFidelityTests(unittest.TestCase):
    """End-to-end through the actual Taskforce orchestration."""

    def setUp(self):
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
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _make_transport(self, **kwargs):
        return AutoSwarmFixtureTransport(
            self.rt.store, self.autoswarm_home, self.workspace,
            self.gate_root, **kwargs)

    def _driver(self, policy, transport, runner=None):
        return ContinuousDriver(
            self.plan, self.rt.registry, self.rt.sequencer, self.rt.store,
            self.env, self.clock, policy, transport,
            runner, self.factory,
            self.dir / "autobot.db",
            holder="pilot-fidelity-test")

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
    # Positive: complete lifecycle through Taskforce.advance()
    # ================================================================

    def test_full_taskforce_driven_lifecycle(self):
        """One READ_ONLY contract through the actual Taskforce
        orchestration: dispatch → adopt → collect → finish → freeze →
        classify → verify → accept → release → complete."""
        transport = self._make_transport()
        driver = self._driver(SingleContractPolicy(), transport)
        self.plan.add_objective(
            "obj-fidelity", "Fidelity gate: investigate logging.",
            "you", instant(self.clock.now()))

        final = driver.run_until_stable()
        driver.close()

        # --- 1. Taskforce dispatched through AutoSwarmTransport ---
        self.assertEqual(len(transport.launched), 1)
        launch = transport.launched[0]
        self.assertEqual(launch["provider"], "codex")
        self.assertEqual(launch["role"], "builder")

        # --- 2. Exactly one Sequencer-minted call_id ---
        call_rows = self.rt.db.conn.execute(
            "SELECT call_id FROM attempt_calls").fetchall()
        call_ids = [r["call_id"] for r in call_rows]
        self.assertEqual(len(call_ids), 1)
        self.assertEqual(launch["call_id"], call_ids[0])

        # --- 3. Persisted prompt is byte-identical to identity-transform output ---
        from autobot import transforms
        va_record = support.load_fixture("va_record.json")
        frozen_input_addr = list(
            va_record["specification_inputs"][
                "name_to_exact_byte_sha256"].values())[0]
        frozen_input = self.rt.store.get(frozen_input_addr)
        reg = support.live_transform_registration("identity-projection")
        expected_output, _ = transforms.execute_transform(
            self.rt.store, reg, frozen_input)
        self.assertEqual(launch["prompt"],
                         expected_output.decode("utf-8"))

        # --- 4. No obj-13 witness admitted ---
        attempt_id = call_rows[0]["call_id"]
        attempt_row = self.rt.db.conn.execute(
            "SELECT attempt_id FROM attempt_calls WHERE call_id = ?",
            (call_ids[0],)).fetchone()
        att_id = attempt_row["attempt_id"]
        obj13_rows = self.rt.db.conn.execute(
            "SELECT * FROM attempt_witnesses WHERE attempt_id = ?"
            " AND witness_kind = 'obj-13'",
            (att_id,)).fetchall()
        self.assertEqual(len(obj13_rows), 0)

        # --- 5. No fabricated artifact admitted ---
        artifacts = self.rt.db.conn.execute(
            "SELECT * FROM attempt_artifacts WHERE attempt_id = ?",
            (att_id,)).fetchall()
        self.assertEqual(len(artifacts), 0,
                         "No artifact should be admitted when the"
                         " transport returns no content")

        # --- 6. Collector derives COMPLETE from obj-11 + obj-12 ---
        obj11_count = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_witnesses"
            " WHERE attempt_id = ? AND witness_kind = 'obj-11'",
            (att_id,)).fetchone()["n"]
        obj12_count = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_witnesses"
            " WHERE attempt_id = ? AND witness_kind = 'obj-12'",
            (att_id,)).fetchone()["n"]
        self.assertTrue(obj11_count >= 1)
        self.assertTrue(obj12_count >= 1)

        # --- 7-8. Verifier executed, VERIFIED ---
        seq_id = self.rt.db.conn.execute(
            "SELECT sequence_id FROM attempts WHERE attempt_id = ?",
            (att_id,)).fetchone()["sequence_id"]
        verifications = self.rt.db.conn.execute(
            "SELECT assertion_id, outcome, verifier_id, receipt_sha256"
            " FROM attempt_verifications WHERE attempt_id = ?"
            " ORDER BY assertion_id", (att_id,)).fetchall()
        assertion_ids = [r["assertion_id"] for r in verifications]
        self.assertIn("a1", assertion_ids)
        self.assertIn("a2", assertion_ids)
        self.assertIn("role-transform/builder", assertion_ids)
        for row in verifications:
            self.assertEqual(row["outcome"], "PASS",
                             "%s: %s" % (row["assertion_id"],
                                         row["outcome"]))

        # --- 9-11. ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED ---
        self.assertEqual(
            self._state("sequences", "sequence_id", seq_id),
            "COMPLETED")

        # --- Transition chain ---
        seq_rules = self._rules_for(seq_id)
        att_rules = self._rules_for(att_id)
        self.assertIn("t-s03", seq_rules)   # DECLARED→READY
        self.assertIn("t-s06", seq_rules)   # READY→EXECUTING
        self.assertIn("t-s09", seq_rules)   # → VERIFYING
        self.assertIn("t-s10", seq_rules)   # → VERIFIED
        self.assertIn("t-s14", seq_rules)   # → ACCEPTED
        self.assertIn("t-s41", seq_rules)   # → DEP_RELEASED
        self.assertIn("t-s42", seq_rules)   # → COMPLETED
        self.assertIn("t-a02", att_rules)   # RESERVED→DISPATCHED
        self.assertIn("t-a03", att_rules)   # DISPATCHED→RUNNING
        self.assertIn("t-a04", att_rules)   # RUNNING→FINISHED
        self.assertIn("t-a07", att_rules)   # → VERIFIED

    # ================================================================
    # Negative: missing outcome
    # ================================================================

    def test_missing_outcome_cannot_reach_success(self):
        transport = self._make_transport(result_outcome=None)
        driver = self._driver(SingleContractPolicy(), transport)
        self.plan.add_objective(
            "obj-no-outcome", "Missing outcome arm.",
            "you", instant(self.clock.now()))
        driver.run_until_stable()
        driver.close()
        att = self.rt.db.conn.execute(
            "SELECT state FROM attempts ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        self.assertNotEqual(att["state"], "VERIFIED")

    # ================================================================
    # Negative: failed assertion
    # ================================================================

    def test_failed_assertion_cannot_reach_verified(self):
        """The vector-checker assertion FAILs. The sequence cannot
        reach VERIFIED, ACCEPTED or COMPLETED."""
        transport = self._make_transport()
        self.factory = AgentFactory()
        self.factory.register("research", "builder", "codex")

        class FailPolicy(ChiefEngineerPolicy):
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
                    "rationale": "failed assertion arm",
                    "requirement": {
                        "objective": "Failed assertion arm",
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

        # Use vector_behavior="FAIL" so the production verifier FAIL
        admit_result = self.rt.registry.admit_payload(
            support.local_record_payload(
                task_contract_id="tc_" + "fa11" * 8,
                objective="Failed assertion arm",
                finalization_class="READ_ONLY",
                evidence_requirements=["COMMAND_GOVERNANCE",
                                       "EXECUTION_LIFECYCLE"],
                vector_behavior="FAIL"),
            submitted_by="planner")
        # Drive through the Sequencer APIs directly to avoid needing
        # the policy to emit an already-admitted contract.
        seq = self.rt.sequencer
        seq.mark_ready(admit_result["sequence_id"])
        info = support.drive_to_evidence_complete(
            self.rt, admit_result, role="builder")
        runner = ProductionVerifierRunner(seq, self.rt.store)
        runner.verify_attempt(info["attempt_id"])
        result = seq.conclude_verification(info["attempt_id"],
                                           info["fence"])
        self.assertEqual(result["attempt"], "REJECTED")
        self.assertNotIn(
            self._state("sequences", "sequence_id",
                        admit_result["sequence_id"]),
            ("VERIFIED", "ACCEPTED", "COMPLETED"))

    # ================================================================
    # Negative: missing artifact cannot fabricate
    # ================================================================

    def test_no_content_means_no_artifact(self):
        transport = self._make_transport(result_content=None)
        driver = self._driver(SingleContractPolicy(), transport)
        self.plan.add_objective(
            "obj-no-artifact", "No artifact arm.",
            "you", instant(self.clock.now()))
        driver.run_until_stable()
        driver.close()
        total_artifacts = self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_artifacts").fetchone()["n"]
        self.assertEqual(total_artifacts, 0)


if __name__ == "__main__":
    unittest.main()
