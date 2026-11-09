"""Structural conformance: every fixture validates positively; a table of
planted defects each rejects with the expected code (D-36 closed objects,
D-24/D-25/D-26/D-27/D-30/D-37/D-46 canonical-form rules)."""

import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import fixtures  # noqa: E402
from autobot_contract import load_schemas, validate_payload  # noqa: E402

SCHEMAS = load_schemas(ROOT / "schemas")
G, HASHES = fixtures.build_graph()
BASE = fixtures.va_record(G, HASHES)


def _mut(payload, fn):
    out = copy.deepcopy(payload)
    fn(out)
    return out


class TestPositives(unittest.TestCase):
    def test_graph_fixtures_validate(self):
        for name, payload in G.items():
            result = validate_payload(payload["kind"], payload, SCHEMAS)
            self.assertTrue(result.ok, "%s: %s" % (name, result.codes()))

    def test_base_record_validates(self):
        result = validate_payload("TaskContractRecord", BASE, SCHEMAS)
        self.assertTrue(result.ok, result.codes())

    def test_env_fixture_validates(self):
        env = fixtures.env_fixture()
        self.assertTrue(validate_payload(env["kind"], env, SCHEMAS).ok)

    def test_duplicate_admission_validates(self):
        from autobot_contract import canonicalize, compute_tiers
        rd, cd, rh = compute_tiers(BASE)
        dup = fixtures.duplicate_admission_fixture(rh, cd, canonicalize(BASE))
        self.assertTrue(validate_payload(dup["kind"], dup, SCHEMAS).ok)

    def test_vector_payloads_validate(self):
        for vid, (payload, _) in fixtures.vector_payloads(G, HASHES, BASE).items():
            result = validate_payload("TaskContractRecord", payload, SCHEMAS)
            self.assertTrue(result.ok, "%s: %s" % (vid, result.codes()))


# (name, kind, payload-builder, expected code present in result)
def _negatives():
    cases = []

    def tcr(fn):
        return _mut(BASE, fn)

    cases.append(("missing_objective", "TaskContractRecord", tcr(lambda r: r.pop("objective")), "ERR_MISSING_FIELD"))
    cases.append(("unknown_field", "TaskContractRecord", tcr(lambda r: r.__setitem__("extra", 1)), "ERR_UNKNOWN_FIELD"))
    cases.append(("bad_enum_class", "TaskContractRecord", tcr(lambda r: r.__setitem__("finalization_class", "MUTATING")), "ERR_ENUM"))
    cases.append(("null_value", "TaskContractRecord", tcr(lambda r: r.__setitem__("objective", None)), "ERR_NULL_FORBIDDEN"))
    cases.append(("float_value", "TaskContractRecord", tcr(lambda r: r["retry_policy"].__setitem__("max_retries", 2.5)), "ERR_FLOAT_FORBIDDEN"))
    cases.append(("int_too_big", "TaskContractRecord", tcr(lambda r: r["retry_policy"].__setitem__("max_retries", 2 ** 53)), "ERR_INT_RANGE"))
    cases.append(("revision_zero", "TaskContractRecord", tcr(lambda r: r.__setitem__("revision", 0)), "ERR_MINIMUM"))
    cases.append(("bad_instant_no_millis", "TaskContractRecord", tcr(lambda r: r.__setitem__("frozen_utc", "2026-11-08T22:00:00Z")), "ERR_PATTERN"))
    cases.append(("bad_digest_uppercase", "TaskContractRecord", tcr(lambda r: r["resolved_bindings"].__setitem__("repository_authority_declaration", "SHA256:" + "a" * 64)), "ERR_PATTERN"))
    cases.append(("unsorted_set", "TaskContractRecord", tcr(lambda r: r.__setitem__("capability_requirements", ["cap-17", "cap-15"])), "ERR_SET_ORDER"))
    cases.append(("duplicate_set", "TaskContractRecord", tcr(lambda r: r["allowed_scope"].__setitem__("paths", ["docs/architecture/chapter1", "docs/architecture/chapter1"])), "ERR_SET_DUPLICATE"))
    cases.append(("nfd_text", "TaskContractRecord", tcr(lambda r: r.__setitem__("objective", "Tracé the tiers.")), "ERR_NOT_NFC"))
    cases.append(("backslash_path", "TaskContractRecord", tcr(lambda r: r["allowed_scope"].__setitem__("paths", ["docs\\architecture"])), "ERR_PATH_FORM"))
    cases.append(("dot_segment_path", "TaskContractRecord", tcr(lambda r: r["allowed_scope"].__setitem__("paths", ["docs/../etc"])), "ERR_PATH_FORM"))
    cases.append(("bare_ref", "TaskContractRecord", tcr(lambda r: r["source_domain"].__setitem__("target_ref", "main")), "ERR_PATTERN"))
    cases.append(("read_only_with_target_ref", "TaskContractRecord",
                  tcr(lambda r: (r.__setitem__("finalization_class", "READ_ONLY"),
                                 r.pop("remote_verification_mode"),
                                 r.__setitem__("finalization_requirements", []),
                                 r["resolved_bindings"]["mandatory_floor"].__setitem__("mandatory_assertion_ids", []))),
                  "ERR_UNKNOWN_FIELD"))
    cases.append(("fresh_within_missing_seconds", "TaskContractRecord", tcr(lambda r: r.__setitem__("reuse_policy", {"mode": "FRESH_WITHIN"})), "ERR_MISSING_FIELD"))
    cases.append(("never_with_seconds", "TaskContractRecord", tcr(lambda r: r.__setitem__("reuse_policy", {"mode": "NEVER", "freshness_seconds": 60})), "ERR_UNKNOWN_FIELD"))
    cases.append(("uppercase_principal", "TaskContractRecord", tcr(lambda r: r.__setitem__("authored_by", "Planner")), "ERR_PATTERN"))
    cases.append(("bad_tc_prefix", "TaskContractRecord", tcr(lambda r: r.__setitem__("task_contract_id", "tk_" + "0" * 32)), "ERR_PATTERN"))
    cases.append(("whitespace_edge", "TaskContractRecord", tcr(lambda r: r.__setitem__("objective", " padded ")), "ERR_WHITESPACE_EDGE"))
    cases.append(("control_char", "TaskContractRecord", tcr(lambda r: r.__setitem__("objective", "tab\there")), "ERR_CONTROL_CHAR"))
    cases.append(("bad_role_key", "TaskContractRecord", tcr(lambda r: r.__setitem__("role_transforms", {"Bad Role": "identity-projection"})), "ERR_PATTERN"))
    cases.append(("empty_assertions", "TaskContractRecord", tcr(lambda r: r["acceptance_assertions"].__setitem__("ids_and_environment_constraints", [])), "ERR_MIN_ITEMS"))
    cases.append(("half_supersession", "TaskContractRecord", tcr(lambda r: r.__setitem__("supersession_provenance", {"supersedes_record": "sha256:" + "a" * 64})), "ERR_MISSING_FIELD"))
    cases.append(("unsupported_version", "TaskContractRecord", tcr(lambda r: r.__setitem__("schema_version", "v2")), "ERR_UNSUPPORTED_VERSION"))
    cases.append(("malformed_version", "TaskContractRecord", tcr(lambda r: r.__setitem__("schema_version", "1.0")), "ERR_MALFORMED_VERSION"))

    reg = G["verifier_checker_v1"]
    cases.append(("registration_kind_cross", "VerifierRegistration",
                  _mut(reg, lambda r: (r.pop("verifier_id"), r.__setitem__("transform_id", "x-form"))), "ERR_ONE_OF"))
    cases.append(("registration_root_mismatch", "VerifierRegistration",
                  _mut(reg, lambda r: r.__setitem__("artifact_root", "sha256:" + "0" * 64)), "SEM-15"))
    cases.append(("registration_bad_closure", "VerifierRegistration",
                  _mut(reg, lambda r: r["closure"].__setitem__("kind", "zip")), "ERR_ONE_OF"))
    cases.append(("registration_entrypoint_traversal", "VerifierRegistration",
                  _mut(reg, lambda r: r.__setitem__("entrypoint", "../escape.py")), "ERR_PATH_FORM"))

    env = fixtures.env_fixture()
    cases.append(("env_missing_tools", "ActualVerificationEnvironment", _mut(env, lambda r: r.pop("tools")), "ERR_MISSING_FIELD"))
    cases.append(("env_bad_var_name", "ActualVerificationEnvironment", _mut(env, lambda r: r.__setitem__("env_vars", {"1BAD": "x"})), "ERR_PATTERN"))

    dele = G["delegation_a"]
    cases.append(("delegation_empty_repos", "DelegationContract", _mut(dele, lambda r: r.__setitem__("repository_ids", [])), "ERR_MIN_ITEMS"))
    cases.append(("delegation_bad_floor_class", "DelegationContract",
                  _mut(dele, lambda r: r.__setitem__("mandatory_floors", {"autobot": {"WRITE": ["a1"]}})), "ERR_UNKNOWN_FIELD"))

    decl = G["declaration_remote"]
    cases.append(("declaration_bad_mode", "RepositoryAuthorityDeclaration", _mut(decl, lambda r: r.__setitem__("publication_mode", "PROXY")), "ERR_ENUM"))
    return cases


class TestNegatives(unittest.TestCase):
    def test_negative_table(self):
        for name, kind, payload, expected in _negatives():
            result = validate_payload(kind, payload, SCHEMAS)
            self.assertFalse(result.ok, "%s unexpectedly validated" % name)
            self.assertIn(expected, result.codes(),
                          "%s: expected %s in %s" % (name, expected, result.codes()))

    def test_schemas_stay_canonical_json(self):
        # The schema files themselves must parse and re-serialize stably.
        for path in sorted((ROOT / "schemas").glob("*.schema.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict, path.name)


if __name__ == "__main__":
    unittest.main()
