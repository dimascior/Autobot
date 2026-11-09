"""SEM-01..SEM-18 parity tests (D-43): each rule rejects a payload or
context the structural schema alone accepts. Also V-Q's property proof and
the P-1 dispositions."""

import copy
import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import fixtures  # noqa: E402
from autobot_contract import (  # noqa: E402
    DigestError, derive_eligibility, freeze_disposition, load_schemas,
    object_hash, utf16_key, validate_payload,
)

SCHEMAS = load_schemas(ROOT / "schemas")
G, HASHES = fixtures.build_graph()
BASE = fixtures.va_record(G, HASHES)
CONTEXT = fixtures.binding_context(G, HASHES)


def _mut(payload, fn):
    out = copy.deepcopy(payload)
    fn(out)
    return out


def _codes(disposition):
    return [i["code"] for i in disposition["issues"]]


def _freeze(record, context=CONTEXT):
    return freeze_disposition(record, context, SCHEMAS)


class TestFreezeRules(unittest.TestCase):
    def test_sem01_vs(self):
        record = _mut(BASE, lambda r: (
            r["source_domain"].__setitem__("authority_location", "LOCAL"),
            r.pop("remote_verification_mode"),
            r.__setitem__("finalization_requirements", ["LOCAL_CANONICAL_COMMIT"]),
        ))
        self.assertTrue(validate_payload("TaskContractRecord", record, SCHEMAS).ok,
                        "V-S payload must be schema-clean so only the freeze rule catches it")
        out = _freeze(record)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-01", _codes(out))

    def test_sem02(self):
        other = _mut(G["declaration_remote"], lambda d: d.__setitem__("repository_id", "other-repo"))
        other_hash = object_hash(other["kind"], other)
        record = _mut(BASE, lambda r: r["resolved_bindings"].__setitem__(
            "repository_authority_declaration", other_hash))
        ctx = copy.deepcopy(CONTEXT)
        ctx["declaration"] = {"payload": other, "hash": other_hash}
        out = _freeze(record, ctx)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-02", _codes(out))

    def test_sem03(self):
        record = _mut(BASE, lambda r: r["source_domain"].__setitem__("target_ref", "refs/heads/dev"))
        out = _freeze(record)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-03", _codes(out))

    def test_sem04(self):
        # payload-local half: a mode on a LOCAL contract never validates.
        record = _mut(BASE, lambda r: (
            r["source_domain"].__setitem__("authority_location", "LOCAL"),
            r.__setitem__("finalization_requirements", ["LOCAL_CANONICAL_COMMIT"]),
        ))
        result = validate_payload("TaskContractRecord", record, SCHEMAS)
        self.assertIn("SEM-04", result.codes())

    def test_sem05(self):
        record = _mut(BASE, lambda r: r.__setitem__(
            "finalization_requirements",
            ["CANONICAL_PUBLICATION", "REMOTE_VERIFICATION", "SEQUENCE_REF_COMMIT"]))
        result = validate_payload("TaskContractRecord", record, SCHEMAS)
        self.assertIn("SEM-05", result.codes())

    def test_sem06(self):
        ctx = fixtures.binding_context(G, HASHES, transforms=())
        record = _mut(BASE, lambda r: r["resolved_bindings"].__setitem__("transform_registrations", []))
        out = _freeze(record, ctx)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-06", _codes(out))

    def test_sem07(self):
        ctx = fixtures.binding_context(G, HASHES, verifiers=("verifier_checker_v1",))
        record = _mut(BASE, lambda r: r["resolved_bindings"].__setitem__(
            "verifier_registrations", [HASHES["verifier_checker_v1"]]))
        out = _freeze(record, ctx)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-07", _codes(out))

    def test_sem08(self):
        record = _mut(BASE, lambda r: r["resolved_bindings"]["mandatory_floor"].__setitem__(
            "delegation", HASHES["delegation_b"]))
        out = _freeze(record)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-08", _codes(out))

    def test_sem09(self):
        record = _mut(BASE, lambda r: r.__setitem__(
            "delegation_id", "dg_000000000000000000000000000000ff"))
        out = _freeze(record)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-09", _codes(out))

    def test_sem09_vq_property(self):
        # V-Q's impossibility: with the binding fixed, no delegation_id other
        # than the bound record's ever admits.
        rng = random.Random(20261108)
        bound = G["delegation_a"]["delegation_id"]
        for _ in range(20):
            candidate = "dg_" + "".join(rng.choice("0123456789abcdef") for _ in range(32))
            if candidate == bound:
                continue
            record = _mut(BASE, lambda r: r.__setitem__("delegation_id", candidate))
            out = _freeze(record)
            self.assertEqual(out["disposition"], "REJECTED", candidate)
            self.assertIn("SEM-09", _codes(out))

    def test_sem10(self):
        record = _mut(BASE, lambda r: r["resolved_bindings"]["mandatory_floor"].__setitem__(
            "mandatory_assertion_ids", []))
        out = _freeze(record)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-10", _codes(out))

    def test_sem11_p1(self):
        outside = _mut(BASE, lambda r: r["allowed_scope"].__setitem__("paths", ["src/forbidden"]))
        out = _freeze(outside)
        self.assertEqual(out["disposition"], "QUEUED_FOR_YOU")
        authored = _mut(BASE, lambda r: (
            r.__setitem__("authored_by", "you"),
            r.pop("delegation_id"),
            r["resolved_bindings"].pop("delegation_contract"),
            r["resolved_bindings"].pop("mandatory_floor"),
        ))
        ctx_none = fixtures.binding_context(G, HASHES, delegation=None)
        self.assertEqual(_freeze(authored, ctx_none)["disposition"], "FROZEN")
        agent = _mut(authored, lambda r: r.__setitem__("authored_by", "planner"))
        self.assertEqual(_freeze(agent, ctx_none)["disposition"], "QUEUED_FOR_YOU")

    def test_sem11_repo_outside_delegation_queues(self):
        # RV-55: a repository the delegation does not cover queues for you
        # (t-c04); it is never rejected by the floor-entry resolution.
        other_decl = _mut(G["declaration_remote"], lambda d: d.__setitem__("repository_id", "other-repo"))
        other_hash = object_hash(other_decl["kind"], other_decl)
        record = _mut(BASE, lambda r: (
            r["source_domain"].__setitem__("repository_id", "other-repo"),
            r["resolved_bindings"].__setitem__("repository_authority_declaration", other_hash),
        ))
        ctx = copy.deepcopy(CONTEXT)
        ctx["declaration"] = {"payload": other_decl, "hash": other_hash}
        out = _freeze(record, ctx)
        self.assertEqual(out["disposition"], "QUEUED_FOR_YOU", out)
        self.assertTrue(all(i["code"] == "SEM-11" for i in out["issues"]), out)

    def test_sem16(self):
        record = _mut(BASE, lambda r: (
            r["acceptance_assertions"]["ids_and_environment_constraints"][0].__setitem__(
                "verifier_id", "llm-reviewer"),
            r["resolved_bindings"].__setitem__("verifier_registrations", sorted(
                [HASHES["verifier_llm_v1"], HASHES["verifier_vector_v1"]], key=utf16_key)),
        ))
        ctx = fixtures.binding_context(G, HASHES, verifiers=("verifier_llm_v1", "verifier_vector_v1"))
        out = _freeze(record, ctx)
        self.assertEqual(out["disposition"], "REJECTED")
        self.assertIn("SEM-16", _codes(out))


class TestPayloadRules(unittest.TestCase):
    def test_sem12(self):
        record = _mut(BASE, lambda r: r.__setitem__(
            "effect_policy", {"effect_intent_ttl_seconds": 240, "effect_hard_timeout_seconds": 240}))
        result = validate_payload("TaskContractRecord", record, SCHEMAS)
        self.assertIn("SEM-12", result.codes())

    def test_sem13(self):
        self.assertEqual(derive_eligibility(BASE), "AUTOMATIC")
        human = _mut(BASE, lambda r: r["acceptance_assertions"]["ids_and_environment_constraints"][1].__setitem__(
            "mode", "HUMAN_ATTESTED"))
        self.assertEqual(derive_eligibility(human), "HUMAN_REQUIRED")

    def test_sem14(self):
        same = "sha256:" + "ab" * 32
        record = _mut(BASE, lambda r: r.__setitem__(
            "supersession_provenance", {"supersedes_record": same, "authorized_by": same}))
        result = validate_payload("TaskContractRecord", record, SCHEMAS)
        self.assertIn("SEM-14", result.codes())

    def test_sem15(self):
        reg = _mut(G["verifier_checker_v1"], lambda r: r.__setitem__(
            "artifact_root", "sha256:" + "0" * 64))
        result = validate_payload("VerifierRegistration", reg, SCHEMAS)
        self.assertIn("SEM-15", result.codes())

    def test_sem17(self):
        with self.assertRaises(DigestError):
            object_hash("DelegationContract", G["declaration_remote"])

    def test_sem18_anchor(self):
        # SEM-18's canonical-form rules are proven across test_schemas and
        # test_canonicalization; anchor one here: unsorted set rejected.
        record = _mut(BASE, lambda r: r.__setitem__(
            "capability_requirements", ["cap-17", "cap-15"]))
        result = validate_payload("TaskContractRecord", record, SCHEMAS)
        self.assertIn("ERR_SET_ORDER", result.codes())


if __name__ == "__main__":
    unittest.main()
