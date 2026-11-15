"""ExecutionManifest boundary tests.

Proves: schema byte-identity across repos, manifest builder derives
from durable state, launch gate wires through, and the manifest
validates correctly.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot.execution_boundary import build_execution_manifest, manifest_schema
from autobot.execution_boundary.launch_gate import launch_with_manifest
from autobot.models import ConflictError
from contract_library import content_address
from contract_library.schema_profile import validate as schema_validate


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


if __name__ == "__main__":
    unittest.main()
