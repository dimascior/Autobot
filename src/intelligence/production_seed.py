"""Production authority seed profile: pilot-read-only-v1.

Owns the deterministic construction of every production authority object
the first READ_ONLY pilot requires. The same seed version under the same
closure bytes and interpreter identity always produces the same canonical
objects and object hashes — a second seed adds zero new authority objects.

Seed evolution is explicit: when a closure, interpreter, delegation scope,
or registration version intentionally changes, that becomes a new seed
version with new object hashes.
"""

import sys
from pathlib import Path

from autobot.environment_probe import interpreter_identity
from contract_library import artifact_root, content_address

SEED_VERSION = "pilot-read-only-v1"

EVIDENCE_PROFILES = {
    "READ_ONLY_REPORT": ["EXECUTION_LIFECYCLE"],
    "READ_ONLY_GOVERNED": ["COMMAND_GOVERNANCE", "EXECUTION_LIFECYCLE"],
    "SOURCE_MODIFYING_GOVERNED": [
        "COMMAND_GOVERNANCE", "EXECUTION_LIFECYCLE"],
}

# The engineering-organization profile: an objective whose workstreams
# mix finalization classes takes each contract's evidence requirements
# from its own class — READ_ONLY work that runs no command is witnessed by
# its execution lifecycle, SOURCE_MODIFYING work is always governed.
BY_CLASS_PROFILE = "BY_CLASS"
EVIDENCE_BY_CLASS = {
    "READ_ONLY": EVIDENCE_PROFILES["READ_ONLY_REPORT"],
    "SOURCE_MODIFYING": EVIDENCE_PROFILES["SOURCE_MODIFYING_GOVERNED"],
}

SEED_EPOCH = "2026-11-16T00:00:00.000Z"

LIVE_LOCK = b"# autobot live verifier dependency lock: stdlib only\\n"

LIVE_PASS_SCRIPT = b'''import hashlib
import json
import sys


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        context = json.load(fh)
    for artifact in context["subject"]["artifacts"]:
        with open(artifact["file"], "rb") as fh:
            digest = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
        if digest != artifact["content_sha256"]:
            print(json.dumps({"autobot_verifier_result": {
                "conclusion": "FAIL",
                "detail": "subject artifact drifted: %s"
                          % artifact["logical_path"]}}))
            return 0
    print(json.dumps({"autobot_verifier_result": {
        "conclusion": "PASS",
        "detail": "subject bytes verified against their content"
                  " addresses"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

LIVE_IDENTITY_SCRIPT = b'''import sys

with open(sys.argv[1], "rb") as fh:
    data = fh.read()
with open(sys.argv[2], "wb") as fh:
    fh.write(data)
sys.exit(0)
'''


def production_declaration():
    return {
        "authored_by": "you",
        "authority_location": "LOCAL",
        "canonical_ref": "refs/heads/main",
        "declared_utc": SEED_EPOCH,
        "kind": "RepositoryAuthorityDeclaration",
        "publication_mode": "DIRECT_REF_CAS",
        "repository_id": "autobot",
        "schema_version": "v1",
    }


PILOT_DELEGATION_ID = "dg_00000000000000000000000000f10001"


def pilot_delegation():
    return {
        "allowed_scope": {
            "artifact_paths": [],
            "paths": [
                "docs",
                "src/autobot",
                "src/intelligence",
            ],
        },
        "authored_by": "you",
        "created_utc": SEED_EPOCH,
        "delegated_to": "planner",
        "delegation_id": PILOT_DELEGATION_ID,
        "expires_utc": "2027-06-01T00:00:00.000Z",
        "kind": "DelegationContract",
        "mandatory_floors": {
            "autobot": {
                "READ_ONLY": [],
                "SOURCE_MODIFYING": ["a1"],
            }
        },
        "policy_bounds": {
            "effect_hard_timeout_seconds": 240,
            "effect_intent_ttl_seconds": 300,
        },
        "repository_ids": ["autobot"],
        "schema_version": "v1",
    }


def _make_closure(script):
    return {
        "kind": "python",
        "script_sha256": content_address(script),
        "interpreter_id": interpreter_identity(),
        "dependency_lock_sha256": content_address(LIVE_LOCK),
    }


def verifier_registration(verifier_id, suffix):
    closure = _make_closure(LIVE_PASS_SCRIPT)
    return {
        "schema_version": "v1",
        "kind": "VerifierRegistration",
        "registration_id": "rg_" + suffix.rjust(32, "0"),
        "verifier_id": verifier_id,
        "version": "1.0.0",
        "closure": closure,
        "artifact_root": artifact_root(closure),
        "entrypoint": "verify.py",
        "execution_requirements": [],
        "registered_by": "you",
        "registered_utc": SEED_EPOCH,
    }


def transform_registration():
    closure = _make_closure(LIVE_IDENTITY_SCRIPT)
    return {
        "schema_version": "v1",
        "kind": "TransformRegistration",
        "registration_id": "rg_" + "d1".rjust(32, "0"),
        "transform_id": "identity-projection",
        "version": "1.0.0",
        "closure": closure,
        "artifact_root": artifact_root(closure),
        "entrypoint": "transform.py",
        "execution_requirements": [],
        "registered_by": "you",
        "registered_utc": SEED_EPOCH,
    }


def all_seed_objects():
    """Return the ordered list of (logical_name, payload) for every
    production authority object in this seed profile."""
    return [
        ("declaration/autobot-local", production_declaration()),
        ("delegation/pilot-read-only", pilot_delegation()),
        ("verifier/registry-checker", verifier_registration(
            "registry-checker", "c1")),
        ("verifier/vector-checker", verifier_registration(
            "vector-checker", "c2")),
        ("transform/identity-projection", transform_registration()),
    ]


def store_closure_bytes(store):
    """Ensure the CAS holds the closure scripts and dependency lock."""
    store.put(LIVE_LOCK)
    store.put(LIVE_PASS_SCRIPT)
    store.put(LIVE_IDENTITY_SCRIPT)


def production_defaults(project_root=None, evidence_profile="READ_ONLY_REPORT"):
    """The compiler defaults for the pilot-read-only-v1 profile.

    ``evidence_profile`` selects the evidence requirements:
    READ_ONLY_REPORT requires only EXECUTION_LIFECYCLE;
    READ_ONLY_GOVERNED also requires COMMAND_GOVERNANCE.

    Reads the V-A engineering profile from the frozen fixtures and
    patches the environment constraint to match the running interpreter,
    exactly as the test suite's intelligence_environment() does."""
    import json as _json
    from pathlib import Path as _Path

    if project_root is None:
        project_root = _Path(__file__).resolve().parents[2]
    fixtures = (project_root / "docs" / "architecture" / "chapter1"
                / "vectors" / "fixtures")
    with open(str(fixtures / "va_record.json"), "r",
              encoding="utf-8") as fh:
        record = _json.load(fh)

    interp = interpreter_identity()
    parts = interp.split("-")
    impl = parts[0]
    version = parts[1]

    assertions = _json.loads(_json.dumps(record["acceptance_assertions"]))
    assertions["ids_and_environment_constraints"][0][
        "environment_constraint"] = {
        "interpreter": {
            "implementations": [impl],
            "versions": [version],
        }
    }
    delegation = pilot_delegation()
    by_class = evidence_profile == BY_CLASS_PROFILE
    defaults = {
        "acceptance_assertions": assertions,
        "allowed_scope": delegation["allowed_scope"],
        "capability_requirements": record["capability_requirements"],
        "evidence_requirements": (
            EVIDENCE_BY_CLASS["READ_ONLY"] if by_class
            else EVIDENCE_PROFILES[evidence_profile]),
        "retry_policy": {"max_retries": 2},
        "repair_policy": {"max_repairs": 1},
        "reuse_policy": {"mode": "NEVER"},
        "effect_policy": {"effect_intent_ttl_seconds": 300,
                          "effect_hard_timeout_seconds": 240},
        "role_transform_id": "identity-projection",
        "specification_inputs": record.get("specification_inputs", {}),
    }
    if by_class:
        defaults["evidence_requirements_by_class"] = {
            key: list(value) for key, value in EVIDENCE_BY_CLASS.items()}
    return defaults
