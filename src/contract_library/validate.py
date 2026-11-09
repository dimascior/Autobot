"""Admission-time validation: canonical form (SEM-18), structure (D-43
profile), per-kind semantics, freeze checks (SEM-01..SEM-16), eligibility
derivation (D-52), the P-1 scope predicate and the P-2 admission outcomes.

Stdlib only (D-59). The validator rejects non-canonical spellings; it never
normalizes (D-25, D-30, D-46).
"""

import json
import re
from pathlib import Path

from . import grammars
from .digests import artifact_root, compute_tiers, object_hash
from .schema_profile import Issue, check_profile, validate as schema_validate

INT_MAX = 2 ** 53 - 1
_VERSION_RE = re.compile(r"^v[1-9][0-9]*$")

KIND_TO_SCHEMA = {
    "DelegationContract": "delegation_contract.schema.json",
    "RepositoryAuthorityDeclaration": "repository_authority_declaration.schema.json",
    "VerifierRegistration": "registration.schema.json",
    "TransformRegistration": "registration.schema.json",
    "TaskContractRecord": "task_contract_record.schema.json",
    "ActualVerificationEnvironment": "actual_verification_environment.schema.json",
    "DuplicateAdmissionRecord": "duplicate_admission_record.schema.json",
}

# Fields holding D-26 paths (allow_root marks scope sets, where '.' is legal)
# and refs, per kind; the walk validates them exactly.
_PATH_FIELD_RULES = {
    "TaskContractRecord": {
        "paths": [("$.allowed_scope.paths[*]", True), ("$.allowed_scope.artifact_paths[*]", True)],
        "refs": ["$.source_domain.target_ref"],
    },
    "DelegationContract": {
        "paths": [("$.allowed_scope.paths[*]", True), ("$.allowed_scope.artifact_paths[*]", True)],
        "refs": [],
    },
    "RepositoryAuthorityDeclaration": {"paths": [], "refs": ["$.canonical_ref"]},
    # A closure-relative entrypoint must not escape the closure (INV-66, RV-56).
    "VerifierRegistration": {"paths": [("$.entrypoint", False)], "refs": []},
    "TransformRegistration": {"paths": [("$.entrypoint", False)], "refs": []},
}

_SET_KEY_FIELDS = ("assertion_id", "edge_id")


class ValidationResult:
    def __init__(self, issues):
        self.issues = issues

    @property
    def ok(self):
        return not self.issues

    def codes(self):
        return [i.code for i in self.issues]

    def as_dict(self):
        return {"ok": self.ok, "issues": [i.as_dict() for i in self.issues]}


def load_schemas(schemas_dir):
    """Load and profile-check every schema file. Returns {filename: schema}."""
    schemas = {}
    for path in sorted(Path(schemas_dir).glob("*.schema.json")):
        with open(path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        check_profile(schema)
        schemas[path.name] = schema
    return schemas


def recognize_version(payload):
    """D-34: SUPPORTED | UNSUPPORTED_VERSION | MALFORMED_VERSION."""
    version = payload.get("schema_version")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        return "MALFORMED_VERSION"
    if version != "v1":
        return "UNSUPPORTED_VERSION"
    return "SUPPORTED"


def _path_matches(pattern, path):
    # pattern uses [*] for any index; exact otherwise.
    regex = re.escape(pattern).replace(re.escape("[*]"), r"\[\d+\]")
    return re.fullmatch(regex, path) is not None


def _walk_canonical_form(value, path, issues, kind, in_objective=False):
    if value is None:
        issues.append(Issue("ERR_NULL_FORBIDDEN", path, "null is forbidden (D-37)"))
        return
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        issues.append(Issue("ERR_FLOAT_FORBIDDEN", path, "fractional numbers are forbidden (D-24)"))
        return
    if isinstance(value, int):
        if value < 0 or value > INT_MAX:
            issues.append(Issue("ERR_INT_RANGE", path, "integer outside [0, 2**53-1] (D-24)"))
        return
    if isinstance(value, str):
        if not grammars.is_nfc(value):
            issues.append(Issue("ERR_NOT_NFC", path, "string is not NFC-normalized valid Unicode (D-46)"))
            return
        if value and value != value.strip():
            issues.append(Issue("ERR_WHITESPACE_EDGE", path, "leading or trailing whitespace (D-46)"))
        for ch in value:
            code = ord(ch)
            if code == 0x7F or (code < 0x20 and not (ch == "\n" and in_objective)):
                issues.append(Issue("ERR_CONTROL_CHAR", path, "control character U+%04X (D-46)" % code))
                break
        return
    if isinstance(value, list):
        _check_set_order(value, path, issues)
        for i, item in enumerate(value):
            _walk_canonical_form(item, "%s[%d]" % (path, i), issues, kind)
        return
    if isinstance(value, dict):
        for key, val in value.items():
            if not isinstance(key, str):
                issues.append(Issue("ERR_KEY_TYPE", path, "object keys must be strings"))
                continue
            if not grammars.is_nfc(key):
                issues.append(Issue("ERR_NOT_NFC", "%s.%s" % (path, key), "map key is not NFC (D-46)"))
                continue
            _walk_canonical_form(
                val, "%s.%s" % (path, key), issues, kind,
                in_objective=(path == "$" and key == "objective"),
            )
        return
    issues.append(Issue("ERR_VALUE_TYPE", path, "unsupported value type"))


def _check_set_order(value, path, issues):
    """D-25: every v1 protocol array is a set — sorted, unique, one key rule."""
    if not value:
        return
    keys = []
    for item in value:
        if isinstance(item, str):
            keys.append(item)
        elif isinstance(item, dict):
            key_field = next((f for f in _SET_KEY_FIELDS if f in item), None)
            if key_field is None or not isinstance(item.get(key_field), str):
                issues.append(Issue(
                    "ERR_SET_KEY", path,
                    "set member lacks a sort key (%s)" % "/".join(_SET_KEY_FIELDS),
                ))
                return
            keys.append(item[key_field])
        else:
            issues.append(Issue("ERR_SET_MEMBER", path, "set members must be strings or keyed objects (D-25)"))
            return
    encoded = [grammars.utf16_key(k) for k in keys]
    if any(encoded[i] >= encoded[i + 1] for i in range(len(encoded) - 1)):
        if len(set(encoded)) != len(encoded):
            issues.append(Issue("ERR_SET_DUPLICATE", path, "duplicate set key (D-25)"))
        else:
            issues.append(Issue("ERR_SET_ORDER", path, "set not in canonical order (D-25)"))


def _validate_paths_and_refs(kind, payload, issues):
    rules = _PATH_FIELD_RULES.get(kind)
    if not rules:
        return
    def visit(value, path):
        if isinstance(value, dict):
            for key, val in value.items():
                visit(val, "%s.%s" % (path, key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, "%s[%d]" % (path, i))
        elif isinstance(value, str):
            for pattern, allow_root in rules["paths"]:
                if _path_matches(pattern, path) and not grammars.repo_path_ok(value, allow_root=allow_root):
                    issues.append(Issue("ERR_PATH_FORM", path, "not a canonical RepoPath (D-26)"))
            for pattern in rules["refs"]:
                if _path_matches(pattern, path) and not grammars.ref_name_ok(value):
                    issues.append(Issue("ERR_REF_FORM", path, "not a canonical RefName (D-26)"))
    visit(payload, "$")


def _record_semantics(record, issues):
    # SEM-12
    policy = record.get("effect_policy")
    if isinstance(policy, dict):
        ttl = policy.get("effect_intent_ttl_seconds")
        hard = policy.get("effect_hard_timeout_seconds")
        if isinstance(ttl, int) and isinstance(hard, int) and not hard < ttl:
            issues.append(Issue("SEM-12", "$.effect_policy", "effect_hard_timeout_seconds must be < effect_intent_ttl_seconds (INV-67)"))
    # reuse policy shape (D-24/D-28)
    reuse = record.get("reuse_policy")
    if isinstance(reuse, dict):
        mode = reuse.get("mode")
        if mode == "FRESH_WITHIN" and "freshness_seconds" not in reuse:
            issues.append(Issue("ERR_MISSING_FIELD", "$.reuse_policy", "FRESH_WITHIN requires freshness_seconds (D-28)"))
        if mode == "NEVER" and "freshness_seconds" in reuse:
            issues.append(Issue("ERR_UNKNOWN_FIELD", "$.reuse_policy.freshness_seconds", "NEVER forbids freshness_seconds (D-28)"))
    # SEM-14
    sp = record.get("supersession_provenance")
    if sp is not None and isinstance(sp, dict):
        if set(sp.keys()) != {"supersedes_record", "authorized_by"}:
            issues.append(Issue("SEM-14", "$.supersession_provenance", "must carry exactly supersedes_record and authorized_by (D-45)"))
        elif sp.get("supersedes_record") == sp.get("authorized_by"):
            issues.append(Issue("SEM-14", "$.supersession_provenance", "supersedes_record and authorized_by are digests from different domains and may never be equal (D-29, D-45)"))
    # delegation triple pairing (presence half of SEM-08/SEM-09)
    bindings = record.get("resolved_bindings", {})
    triple = (
        "delegation_contract" in bindings,
        "mandatory_floor" in bindings,
        "delegation_id" in record,
    )
    if any(triple) and not all(triple):
        issues.append(Issue("SEM-09", "$", "delegation_contract, mandatory_floor and delegation_id are present together or absent together (D-44)"))
    # class-dependent structure
    fin_class = record.get("finalization_class")
    source_domain = record.get("source_domain", {})
    if fin_class == "SOURCE_MODIFYING" and "target_ref" not in source_domain:
        issues.append(Issue("ERR_MISSING_FIELD", "$.source_domain.target_ref", "SOURCE_MODIFYING requires target_ref (D-26, INV-36)"))
    if fin_class == "READ_ONLY":
        if "target_ref" in source_domain:
            issues.append(Issue("ERR_UNKNOWN_FIELD", "$.source_domain.target_ref", "READ_ONLY forbids target_ref (D-54)"))
        if "remote_verification_mode" in record:
            issues.append(Issue("SEM-04", "$.remote_verification_mode", "READ_ONLY forbids a remote verification mode (D-54)"))
    # SEM-04 (payload-local part) and SEM-05
    location = source_domain.get("authority_location")
    mode = record.get("remote_verification_mode")
    if mode is not None and location == "LOCAL":
        issues.append(Issue("SEM-04", "$.remote_verification_mode", "a remote verification mode requires authority_location REMOTE (D-54)"))
    expected = derived_finalization_requirements(fin_class, location, mode)
    if expected is not None:
        actual = record.get("finalization_requirements")
        if actual is not None and actual != expected:
            issues.append(Issue("SEM-05", "$.finalization_requirements", "must equal %r for this class/location/mode (K-6, K-7)" % (expected,)))


def derived_finalization_requirements(fin_class, location, mode):
    """SEM-05: the exact requirement set per path family (K-6, K-7)."""
    if fin_class == "READ_ONLY":
        return []
    if fin_class != "SOURCE_MODIFYING":
        return None
    if location == "LOCAL":
        return ["LOCAL_CANONICAL_COMMIT"]
    if location != "REMOTE":
        return None
    if mode is None:
        return ["CANONICAL_PUBLICATION", "SEQUENCE_REF_COMMIT"]
    if mode == "PRE_PUBLICATION_REMOTE_VERIFICATION":
        return ["CANONICAL_PUBLICATION", "REMOTE_VERIFICATION", "SEQUENCE_REF_COMMIT", "STAGING_PUBLICATION"]
    if mode == "POST_PUBLICATION_OBSERVATION":
        return ["CANONICAL_PUBLICATION", "POST_PUBLICATION_OBSERVATION", "SEQUENCE_REF_COMMIT"]
    return None


def _registration_semantics(payload, issues):
    closure = payload.get("closure")
    declared_root = payload.get("artifact_root")
    if isinstance(closure, dict) and isinstance(declared_root, str):
        try:
            recomputed = artifact_root(closure)
        except Exception as exc:  # malformed closure
            issues.append(Issue("SEM-15", "$.closure", "artifact_root not computable: %s" % exc))
            return
        if recomputed != declared_root:
            issues.append(Issue("SEM-15", "$.artifact_root", "does not recompute from the closure manifest (D-32)"))


def validate_payload(kind, payload, schemas):
    """Canonical form + structure + payload-local semantics for one object."""
    issues = []
    if not isinstance(payload, dict):
        return ValidationResult([Issue("ERR_TYPE", "$", "payload must be an object")])
    recognition = recognize_version(payload)
    if recognition != "SUPPORTED":
        return ValidationResult([Issue("ERR_" + recognition, "$.schema_version", recognition)])
    if payload.get("kind") != kind:
        issues.append(Issue("ERR_KIND", "$.kind", "expected %r" % kind))
    _walk_canonical_form(payload, "$", issues, kind)
    schema_name = KIND_TO_SCHEMA.get(kind)
    if schema_name is None:
        issues.append(Issue("ERR_KIND", "$.kind", "no v1 schema for kind %r (D-19)" % kind))
        return ValidationResult(issues)
    schema_validate(payload, schemas[schema_name], issues=issues)
    _validate_paths_and_refs(kind, payload, issues)
    if kind == "TaskContractRecord":
        _record_semantics(payload, issues)
    if kind in ("VerifierRegistration", "TransformRegistration"):
        _registration_semantics(payload, issues)
    return ValidationResult(issues)


def derive_eligibility(record):
    """D-52: HUMAN_REQUIRED iff any assertion's mode is HUMAN_ATTESTED."""
    assertions = record.get("acceptance_assertions", {}).get("ids_and_environment_constraints", [])
    for assertion in assertions:
        if assertion.get("mode") == "HUMAN_ATTESTED":
            return "HUMAN_REQUIRED"
    return "AUTOMATIC"


def _assertion_ids(record):
    return [
        a.get("assertion_id")
        for a in record.get("acceptance_assertions", {}).get("ids_and_environment_constraints", [])
    ]


def validate_freeze(record, context):
    """SEM-01..SEM-10, SEM-16: the declaration/registration/delegation
    consistency checks of t-c02. ``context`` carries the bound payloads and
    their object hashes: {declaration: {payload, hash}, delegation:
    {payload, hash} | None, verifier_registrations: [...], transform_registrations: [...]}.
    Returns (issues, queue_issues): hard rejections vs. queue-for-you findings."""
    issues = []
    queue = []
    bindings = record.get("resolved_bindings", {})
    declaration = context["declaration"]
    decl_payload = declaration["payload"]

    if bindings.get("repository_authority_declaration") != declaration["hash"]:
        issues.append(Issue("SEM-01", "$.resolved_bindings.repository_authority_declaration", "bound declaration hash does not resolve"))
    if record.get("source_domain", {}).get("authority_location") != decl_payload.get("authority_location"):
        issues.append(Issue("SEM-01", "$.source_domain.authority_location", "disagrees with the bound declaration (INV-45, K-1)"))
    if record.get("source_domain", {}).get("repository_id") != decl_payload.get("repository_id"):
        issues.append(Issue("SEM-02", "$.source_domain.repository_id", "disagrees with the bound declaration (D-23)"))
    if record.get("finalization_class") == "SOURCE_MODIFYING":
        if record.get("source_domain", {}).get("target_ref") != decl_payload.get("canonical_ref"):
            issues.append(Issue("SEM-03", "$.source_domain.target_ref", "must equal the declaration's canonical_ref (K-10, D-54)"))
    mode = record.get("remote_verification_mode")
    if mode is not None and decl_payload.get("authority_location") != "REMOTE":
        issues.append(Issue("SEM-04", "$.remote_verification_mode", "declaration is not REMOTE (D-54)"))

    # K-2 / K-3: every role transform and assertion verifier resolves to
    # exactly one bound registration.
    bound_verifiers = {}
    for reg in context.get("verifier_registrations", []):
        bound_verifiers.setdefault(reg["payload"].get("verifier_id"), []).append(reg)
    bound_transforms = {}
    for reg in context.get("transform_registrations", []):
        bound_transforms.setdefault(reg["payload"].get("transform_id"), []).append(reg)

    listed_v = record.get("resolved_bindings", {}).get("verifier_registrations", [])
    actual_v = sorted((r["hash"] for r in context.get("verifier_registrations", [])), key=grammars.utf16_key)
    if listed_v != actual_v:
        issues.append(Issue("SEM-07", "$.resolved_bindings.verifier_registrations", "bound registration hashes do not match the context"))
    listed_t = record.get("resolved_bindings", {}).get("transform_registrations", [])
    actual_t = sorted((r["hash"] for r in context.get("transform_registrations", [])), key=grammars.utf16_key)
    if listed_t != actual_t:
        issues.append(Issue("SEM-06", "$.resolved_bindings.transform_registrations", "bound registration hashes do not match the context"))

    for assertion in record.get("acceptance_assertions", {}).get("ids_and_environment_constraints", []):
        vid = assertion.get("verifier_id")
        regs = bound_verifiers.get(vid, [])
        if len(regs) != 1:
            issues.append(Issue("SEM-07", "$.acceptance_assertions", "assertion %r resolves to %d bound registrations (K-3)" % (assertion.get("assertion_id"), len(regs))))
        elif assertion.get("mode") == "DETERMINISTIC":
            reqs = regs[0]["payload"].get("execution_requirements", [])
            if any(isinstance(r, str) and r.startswith("service:") for r in reqs):
                issues.append(Issue("SEM-16", "$.acceptance_assertions", "DETERMINISTIC assertion %r bound to a service-dependent registration (D-57)" % assertion.get("assertion_id")))
    for role, transform_id in record.get("role_transforms", {}).items():
        if len(bound_transforms.get(transform_id, [])) != 1:
            issues.append(Issue("SEM-06", "$.role_transforms.%s" % role, "transform %r resolves to %d bound registrations (K-2)" % (transform_id, len(bound_transforms.get(transform_id, [])))))

    # Delegation side: SEM-08, SEM-09, SEM-10, SEM-11 (P-1).
    delegation = context.get("delegation")
    has_binding = "delegation_contract" in bindings
    if delegation is None:
        if has_binding:
            issues.append(Issue("SEM-08", "$.resolved_bindings.delegation_contract", "bound delegation does not resolve"))
        elif record.get("authored_by") != "you":
            queue.append(Issue("SEM-11", "$", "no delegation and not authored by you: waits for you (t-c04/t-c05)"))
    else:
        del_payload = delegation["payload"]
        if not has_binding:
            issues.append(Issue("SEM-08", "$.resolved_bindings", "a delegated freeze must bind the delegation (D-44)"))
        else:
            if bindings.get("delegation_contract") != delegation["hash"]:
                issues.append(Issue("SEM-08", "$.resolved_bindings.delegation_contract", "bound delegation hash does not resolve"))
            floor = bindings.get("mandatory_floor", {})
            if floor.get("delegation") != delegation["hash"]:
                issues.append(Issue("SEM-08", "$.resolved_bindings.mandatory_floor", "floor names a different delegation record (K-4)"))
            if record.get("delegation_id") != del_payload.get("delegation_id"):
                issues.append(Issue("SEM-09", "$.delegation_id", "must equal the bound record's delegation_id (D-44, V-Q)"))
            floor_ids = floor.get("mandatory_assertion_ids", [])
            if not set(floor_ids).issubset(set(_assertion_ids(record))):
                issues.append(Issue("SEM-10", "$.resolved_bindings.mandatory_floor", "floor assertions missing from the contract (K-8, INV-34)"))
            repo = record.get("source_domain", {}).get("repository_id")
            fin_class = record.get("finalization_class")
            repo_covered = repo in del_payload.get("repository_ids", [])
            if repo_covered:
                # The floor-entry equality is meaningful only for a repository
                # the delegation covers; outside it, t-c04 queues the payload
                # for you instead of rejecting it (RV-55).
                expected_floor = (
                    del_payload.get("mandatory_floors", {}).get(repo, {}).get(fin_class, [])
                )
                if floor_ids != sorted(expected_floor, key=grammars.utf16_key):
                    issues.append(Issue("SEM-10", "$.resolved_bindings.mandatory_floor", "resolved floor does not equal the delegation's entry for this repository and class (reg-floor)"))
            else:
                queue.append(Issue("SEM-11", "$.source_domain.repository_id", "outside the delegation's repositories (INV-28)"))
            scope = del_payload.get("allowed_scope", {})
            if not grammars.scope_within(record.get("allowed_scope", {}).get("paths", []), scope.get("paths", [])):
                queue.append(Issue("SEM-11", "$.allowed_scope.paths", "outside the delegation's scope (K-9, INV-28)"))
            if not grammars.scope_within(record.get("allowed_scope", {}).get("artifact_paths", []), scope.get("artifact_paths", [])):
                queue.append(Issue("SEM-11", "$.allowed_scope.artifact_paths", "outside the delegation's artifact scope (K-9, INV-28)"))
    return issues, queue


def freeze_disposition(record, context, schemas):
    """P-1 + t-c02/t-c03-adjacent outcome for one payload: FROZEN,
    QUEUED_FOR_YOU or REJECTED, with the issues that decided it."""
    structural = validate_payload("TaskContractRecord", record, schemas)
    if not structural.ok:
        return {"disposition": "REJECTED", "issues": structural.as_dict()["issues"]}
    hard, queue = validate_freeze(record, context)
    if hard:
        return {"disposition": "REJECTED", "issues": [i.as_dict() for i in hard]}
    if queue:
        return {"disposition": "QUEUED_FOR_YOU", "issues": [i.as_dict() for i in queue]}
    return {"disposition": "FROZEN", "issues": []}


def admit(payload, live_sequences, context, schemas):
    """P-2: the admission outcome for one payload against a live-Sequence
    lookup (a stub for the Chapter 2 ledger): {contract_digest: record_hash}."""
    outcome = freeze_disposition(payload, context, schemas)
    if outcome["disposition"] == "REJECTED":
        return {"outcome": "REJECTED", "records_minted": 0, "sequences_created": 0, "issues": outcome["issues"]}
    rd, cd, rh = compute_tiers(payload)
    existing = live_sequences.get(cd)
    if existing is not None and not payload.get("force_new_sequence", False):
        return {
            "outcome": "DUPLICATE", "records_minted": 0, "sequences_created": 0,
            "returns_existing_record_hash": existing, "mints": ["obj-32"],
            "duplicate_admission": {"payload_contract_digest": cd, "existing_record_hash": existing},
        }
    if outcome["disposition"] == "QUEUED_FOR_YOU":
        return {"outcome": "QUEUED_FOR_YOU", "records_minted": 0, "sequences_created": 0, "issues": outcome["issues"]}
    return {
        "outcome": "NEW_RECORD", "records_minted": 1, "sequences_created": 1,
        "record_hash": rh, "requirement_digest": rd, "contract_digest": cd,
        "mints": ["obj-04", "obj-05", "obj-06", "obj-07", "obj-17"],
        "sequence_rule": {"object": "obj-08", "basis": "INV-40", "form": "lifecycle_coupling"},
    }
