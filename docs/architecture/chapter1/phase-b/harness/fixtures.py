"""The golden-vector object graph: every fixture payload, its bindings and
the vector patch functions. Pure construction — no I/O, no randomness; every
value is a constant, so each run of the generator reproduces identical bytes.
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autobot_contract import content_address, object_hash, utf16_key  # noqa: E402

# ---------------------------------------------------------------- inputs

# Specification input bytes are defined here as text, hashed as UTF-8 bytes,
# so no filesystem or newline conversion can touch them (D-42 note).
VA_INPUT_LF = "Chapter 1 golden vector input.\nSecond line.\n"
VA_INPUT_CRLF = VA_INPUT_LF.replace("\n", "\r\n")

SCRIPT_CHECKER_V1 = b"registry-checker script v1\n"
SCRIPT_CHECKER_V2 = b"registry-checker script v2\n"
SCRIPT_VECTOR_V1 = b"vector-checker script v1\n"
SCRIPT_IDENTITY_V1 = b"identity-projection transform v1\n"
SCRIPT_STRICT_V1 = b"strict-projection transform v1\n"
SCRIPT_LLM_V1 = b"llm-reviewer wrapper v1\n"
DEPENDENCY_LOCK = b"locked-dependencies v1\n"
INTERPRETER_ID = "cpython-3.12.4-windows-x86_64"

T0 = "2026-11-08T22:00:00.000Z"


def _python_closure(script_bytes):
    return {
        "kind": "python",
        "script_sha256": content_address(script_bytes),
        "interpreter_id": INTERPRETER_ID,
        "dependency_lock_sha256": content_address(DEPENDENCY_LOCK),
    }


def _registration(kind, reg_hex, id_field, id_value, version, script, entrypoint, requirements):
    from autobot_contract import artifact_root
    closure = _python_closure(script)
    return {
        "schema_version": "v1",
        "kind": kind,
        "registration_id": "rg_" + reg_hex,
        id_field: id_value,
        "version": version,
        "closure": closure,
        "artifact_root": artifact_root(closure),
        "entrypoint": entrypoint,
        "execution_requirements": requirements,
        "registered_by": "you",
        "registered_utc": T0,
    }


def build_graph():
    """Every bound object with its object hash; deterministic."""
    g = {}

    g["declaration_remote"] = {
        "schema_version": "v1",
        "kind": "RepositoryAuthorityDeclaration",
        "repository_id": "autobot",
        "authority_location": "REMOTE",
        "canonical_ref": "refs/heads/main",
        "publication_mode": "DIRECT_REF_CAS",
        "authored_by": "you",
        "declared_utc": T0,
    }
    g["declaration_local"] = dict(
        g["declaration_remote"],
        authority_location="LOCAL",
        declared_utc="2026-11-08T22:05:00.000Z",
    )

    g["delegation_a"] = {
        "schema_version": "v1",
        "kind": "DelegationContract",
        "delegation_id": "dg_000000000000000000000000000000a1",
        "delegated_to": "planner",
        "repository_ids": ["autobot"],
        "allowed_scope": {"paths": ["artifacts", "docs"], "artifact_paths": ["artifacts"]},
        "mandatory_floors": {"autobot": {"READ_ONLY": [], "SOURCE_MODIFYING": ["a1"]}},
        "policy_bounds": {"effect_hard_timeout_seconds": 240, "effect_intent_ttl_seconds": 300},
        "authored_by": "you",
        "created_utc": T0,
        "expires_utc": "2027-01-01T00:00:00.000Z",
    }
    g["delegation_b"] = dict(g["delegation_a"], expires_utc="2027-06-01T00:00:00.000Z")

    g["verifier_checker_v1"] = _registration(
        "VerifierRegistration", "00000000000000000000000000000001",
        "verifier_id", "registry-checker", "1.0.0", SCRIPT_CHECKER_V1, "check.py", [])
    g["verifier_checker_v2"] = _registration(
        "VerifierRegistration", "00000000000000000000000000000002",
        "verifier_id", "registry-checker", "1.1.0", SCRIPT_CHECKER_V2, "check.py", [])
    g["verifier_vector_v1"] = _registration(
        "VerifierRegistration", "00000000000000000000000000000003",
        "verifier_id", "vector-checker", "1.0.0", SCRIPT_VECTOR_V1, "vectors.py", [])
    g["verifier_llm_v1"] = _registration(
        "VerifierRegistration", "00000000000000000000000000000006",
        "verifier_id", "llm-reviewer", "1.0.0", SCRIPT_LLM_V1, "review.py",
        ["service:anthropic-api"])
    g["transform_identity_v1"] = _registration(
        "TransformRegistration", "00000000000000000000000000000004",
        "transform_id", "identity-projection", "1.0.0", SCRIPT_IDENTITY_V1, "project.py", [])
    g["transform_strict_v1"] = _registration(
        "TransformRegistration", "00000000000000000000000000000005",
        "transform_id", "strict-projection", "1.0.0", SCRIPT_STRICT_V1, "project.py", [])

    hashes = {name: object_hash(payload["kind"], payload) for name, payload in g.items()}
    return g, hashes


def binding_context(g, hashes, declaration="declaration_remote", delegation="delegation_a",
                    verifiers=("verifier_checker_v1", "verifier_vector_v1"),
                    transforms=("transform_identity_v1",)):
    """The freeze context (validate_freeze's shape) for a chosen binding set."""
    return {
        "declaration": {"payload": g[declaration], "hash": hashes[declaration]},
        "delegation": None if delegation is None else {"payload": g[delegation], "hash": hashes[delegation]},
        "verifier_registrations": [{"payload": g[v], "hash": hashes[v]} for v in verifiers],
        "transform_registrations": [{"payload": g[t], "hash": hashes[t]} for t in transforms],
    }


def va_record(g, hashes):
    """V-A: the base requirement — SOURCE_MODIFYING, one REMOTE repository,
    refs/heads/main, one specification input, one path in scope, assertions
    a1 and a2 (both DETERMINISTIC, so eligibility derives AUTOMATIC)."""
    verifier_hashes = sorted(
        [hashes["verifier_checker_v1"], hashes["verifier_vector_v1"]], key=utf16_key)
    record = {
        "schema_version": "v1",
        "kind": "TaskContractRecord",
        "objective": "Produce the Chapter 1 golden vectors and prove the identity tiers.",
        "finalization_class": "SOURCE_MODIFYING",
        "source_domain": {
            "repository_id": "autobot",
            "authority_location": "REMOTE",
            "target_ref": "refs/heads/main",
        },
        "specification_inputs": {
            "name_to_exact_byte_sha256": {
                "chapter1-brief.md": content_address(VA_INPUT_LF.encode("utf-8")),
            }
        },
        "allowed_scope": {
            "paths": ["docs/architecture/chapter1"],
            "artifact_paths": ["artifacts/ch1"],
        },
        "acceptance_assertions": {
            "ids_and_environment_constraints": [
                {
                    "assertion_id": "a1",
                    "verifier_id": "registry-checker",
                    "mode": "DETERMINISTIC",
                    "environment_constraint": {
                        "interpreter": {"implementations": ["cpython"], "versions": ["3.12.4"]}
                    },
                },
                {
                    "assertion_id": "a2",
                    "verifier_id": "vector-checker",
                    "mode": "DETERMINISTIC",
                },
            ]
        },
        "retry_policy": {"max_retries": 2},
        "repair_policy": {"max_repairs": 1},
        "reuse_policy": {"mode": "NEVER"},
        "effect_policy": {"effect_intent_ttl_seconds": 300, "effect_hard_timeout_seconds": 240},
        "finalization_requirements": [
            "CANONICAL_PUBLICATION", "REMOTE_VERIFICATION", "SEQUENCE_REF_COMMIT", "STAGING_PUBLICATION",
        ],
        "remote_verification_mode": "PRE_PUBLICATION_REMOTE_VERIFICATION",
        "capability_requirements": ["cap-15", "cap-17"],
        "role_transforms": {"builder": "identity-projection"},
        "completion_policies": {"builder": {"required": True, "success_outcomes": ["success"]}},
        "evidence_requirements": ["COMMAND_GOVERNANCE", "EXECUTION_LIFECYCLE"],
        "predecessors": [],
        "resolved_bindings": {
            "repository_authority_declaration": hashes["declaration_remote"],
            "verifier_registrations": verifier_hashes,
            "transform_registrations": [hashes["transform_identity_v1"]],
            "delegation_contract": hashes["delegation_a"],
            "mandatory_floor": {
                "delegation": hashes["delegation_a"],
                "mandatory_assertion_ids": ["a1"],
            },
        },
        "task_contract_id": "tc_0123456789abcdef0123456789abcdef",
        "revision": 1,
        "authored_by": "planner",
        "frozen_utc": T0,
        "delegation_id": "dg_000000000000000000000000000000a1",
        "force_new_sequence": False,
    }
    return record


def env_fixture():
    """obj-18 positive fixture."""
    return {
        "schema_version": "v1",
        "kind": "ActualVerificationEnvironment",
        "environment_id": "ve_000000000000000000000000000000e1",
        "attempt": {
            "sequence_id": "sq_00000000000000000000000000000001",
            "attempt_id": "at_00000000000000000000000000000001",
            "version": 1,
            "fence": 1,
        },
        "probed_utc": T0,
        "interpreter": {
            "implementation": "cpython",
            "version": "3.12.4",
            "executable_sha256": content_address(b"python.exe bytes stand-in\n"),
        },
        "platform": {"os": "windows", "os_version": "10.0.19045", "architecture": "x86_64"},
        "env_vars": {"PYTHONUTF8": "1"},
        "tools": {"git": {"version": "2.47.0", "executable_sha256": content_address(b"git bytes stand-in\n")}},
    }


def duplicate_admission_fixture(existing_record_hash, payload_contract_digest, payload_bytes):
    """obj-32 fixture, built from V-A's own admission facts."""
    return {
        "schema_version": "v1",
        "kind": "DuplicateAdmissionRecord",
        "duplicate_admission_id": "da_00000000000000000000000000000001",
        "payload_contract_digest": payload_contract_digest,
        "existing_record_hash": existing_record_hash,
        "payload_content_address": content_address(payload_bytes),
        "submitted_by": "planner",
        "received_utc": "2026-11-08T22:10:00.000Z",
    }


# ------------------------------------------------------------- vector patches

def _patched(record, mutate):
    out = copy.deepcopy(record)
    mutate(out)
    return out


def vector_payloads(g, hashes, base):
    """Executable field-change vectors: id -> (payload, context_overrides).
    Rejection and predicate vectors are built in the tests/generator."""
    payloads = {}

    payloads["V-C"] = (_patched(base, lambda r: r["retry_policy"].__setitem__("max_retries", 5)), None)

    def to_read_only(r):
        r["finalization_class"] = "READ_ONLY"
        r["source_domain"].pop("target_ref")
        r.pop("remote_verification_mode")
        r["finalization_requirements"] = []
        # the delegation's READ_ONLY floor is empty (reg-floor resolution)
        r["resolved_bindings"]["mandatory_floor"]["mandatory_assertion_ids"] = []
    payloads["V-D"] = (_patched(base, to_read_only), None)

    payloads["V-E"] = (_patched(base, lambda r: r.__setitem__("force_new_sequence", True)), None)

    def rebind_verifier(r):
        r["resolved_bindings"]["verifier_registrations"] = sorted(
            [hashes["verifier_checker_v2"], hashes["verifier_vector_v1"]], key=utf16_key)
    payloads["V-F"] = (_patched(base, rebind_verifier),
                       {"verifiers": ("verifier_checker_v2", "verifier_vector_v1")})

    def crlf_input(r):
        r["specification_inputs"]["name_to_exact_byte_sha256"]["chapter1-brief.md"] = (
            content_address(VA_INPUT_CRLF.encode("utf-8")))
    payloads["V-H"] = (_patched(base, crlf_input), None)

    def to_local(r):
        r["source_domain"]["authority_location"] = "LOCAL"
        r.pop("remote_verification_mode")
        r["finalization_requirements"] = ["LOCAL_CANONICAL_COMMIT"]
        r["resolved_bindings"]["repository_authority_declaration"] = hashes["declaration_local"]
    payloads["V-M"] = (_patched(base, to_local), {"declaration": "declaration_local"})

    def retransform(r):
        r["role_transforms"]["builder"] = "strict-projection"
        r["resolved_bindings"]["transform_registrations"] = [hashes["transform_strict_v1"]]
    payloads["V-N"] = (_patched(base, retransform), {"transforms": ("transform_strict_v1",)})

    payloads["V-O"] = (_patched(base, lambda r: r.__setitem__("frozen_utc", "2026-11-08T22:00:01.000Z")), None)

    def rebind_delegation(r):
        r["resolved_bindings"]["delegation_contract"] = hashes["delegation_b"]
        r["resolved_bindings"]["mandatory_floor"]["delegation"] = hashes["delegation_b"]
    payloads["V-P"] = (_patched(base, rebind_delegation), {"delegation": "delegation_b"})

    payloads["V-R"] = (_patched(base, lambda r: r.__setitem__(
        "reuse_policy", {"mode": "FRESH_WITHIN", "freshness_seconds": 86400})), None)

    return payloads
