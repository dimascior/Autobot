"""PromptProjection — the minimal indexed form of obj-10 (M3).

``project_prompt`` is a PURE deterministic renderer: the frozen contract's
objective, scope, acceptance assertions, evidence requirements and
specification inputs, the dispatched role (with its registered transform),
the exact attempt identity, and the Chief Engineer's guidance under an
explicit attributed provenance header — canonicalized and content-addressed
into the CAS. ``authority_effect: none`` is stated in the artifact itself:
a projection grants nothing and transitions nothing (obj-10 is a
non_authoritative_receipt in the Chapter 0 registry).

Reproducible by construction: identical inputs canonicalize to identical
bytes, so re-rendering yields the same content address. The record binding
is RECOMPUTED (compute_tiers over the payload), never caller-claimed.
obj-10's full payload schema is Chapter 3's; t-a02 consumes the returned
address as its resolvable projection reference.
"""

from contract_library import canonicalize, compute_tiers

from .models import ConflictError
from .principals import PRINCIPALS

_IDENTITY_FIELDS = ("sequence_id", "attempt_id", "fence",
                    "attempt_contract_hash")

# Requirement-tier fields carried verbatim when the frozen record has them
# (D-37: absent fields are omitted, never null).
_CARRIED_FIELDS = ("allowed_scope", "acceptance_assertions",
                   "evidence_requirements", "specification_inputs")

# The Chief Engineer's APPROVED context bundle (the integration-layer
# expansion): optional, attributed, carried only when supplied — the
# base {author, guidance} artifact is byte-identical to the M3 form.
# predecessor_findings are the planner-selected finding summaries (each
# must name its finding_id so the projection stays traceable to the
# attempt that produced it); context_refs are content addresses.
_OPTIONAL_CONTEXT = ("context_refs", "predecessor_findings",
                     "repository_context", "expected_outputs")


def project_prompt(store, record_payload, role, attempt_identity,
                   engineering_context):
    """Render one prompt projection; returns ``(bytes, content_address)``."""
    policies = record_payload.get("completion_policies", {})
    if role not in policies:
        raise ConflictError(
            "role %r is not named by the frozen record's completion_policies"
            % (role,))
    if not isinstance(attempt_identity, dict):
        raise ConflictError("attempt_identity must be a mapping")
    missing = [f for f in _IDENTITY_FIELDS if f not in attempt_identity]
    if missing:
        raise ConflictError(
            "attempt_identity lacks %s: the projection must bind the exact"
            " attempt" % ", ".join(missing))
    if not isinstance(engineering_context, dict):
        raise ConflictError("engineering_context must be a mapping")
    author = engineering_context.get("author")
    guidance = engineering_context.get("guidance")
    if author not in PRINCIPALS:
        raise ConflictError(
            "engineering context must be attributed to a registered"
            " principal; %r is not one" % (author,))
    if not (isinstance(guidance, str) and guidance):
        raise ConflictError(
            "engineering context requires non-empty guidance text")

    # The exact record binding, by recomputation (INV-11 discipline).
    rd, cd, rh = compute_tiers(record_payload)

    artifact = {
        "schema_version": "v1",
        "kind": "PromptProjection",
        "authority_effect": "none",
        "record": {"record_hash": rh, "requirement_digest": rd,
                   "contract_digest": cd,
                   "task_contract_id": record_payload["task_contract_id"]},
        "role": role,
        "attempt": {f: attempt_identity[f] for f in _IDENTITY_FIELDS},
        "objective": record_payload["objective"],
        "engineering_context": {"provenance": "chief-engineer-guidance",
                                "author": author, "guidance": guidance},
    }
    for field in _CARRIED_FIELDS:
        if field in record_payload:
            artifact[field] = record_payload[field]
    transform = record_payload.get("role_transforms", {}).get(role)
    if transform is not None:
        artifact["role_transform"] = transform

    # The approved engineering-context bundle (optional, validated).
    for field in _OPTIONAL_CONTEXT:
        if field not in engineering_context:
            continue
        value = engineering_context[field]
        if field == "context_refs":
            if not (isinstance(value, list) and all(
                    isinstance(v, str) and v for v in value)):
                raise ConflictError(
                    "context_refs must be a list of content addresses")
        elif field == "predecessor_findings":
            if not (isinstance(value, list) and all(
                    isinstance(v, dict) and isinstance(
                        v.get("finding_id"), str) and v["finding_id"]
                    for v in value)):
                raise ConflictError(
                    "each predecessor finding must carry its finding_id"
                    " (attempt traceability)")
        elif not (isinstance(value, str) and value):
            raise ConflictError("%s must be non-empty text" % field)
        artifact["engineering_context"][field] = value

    # The completion contract the specialist returns against: derived
    # from the FROZEN record alone (what this role must produce, and the
    # witness kinds the contract requires joined), so it cannot drift
    # from authority.
    artifact["completion_contract"] = {"role_policy": policies[role]}
    if "evidence_requirements" in record_payload:
        artifact["completion_contract"]["evidence_requirements"] = (
            record_payload["evidence_requirements"])

    data = canonicalize(artifact)
    return data, store.put(data)
