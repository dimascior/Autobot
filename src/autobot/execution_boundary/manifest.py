"""Build the ExecutionManifest from durable post-t-a02 state.

Every authority-bearing field is re-read from the Sequencer's ledger,
never accepted as a parameter. The provider assignment comes from the
Taskforce's staffing decision because seat, route and model selection
(cap-14) is exactly what staffing owns; ``selected_by`` carries the
registered cap-14 principal id (``agent_factory``), which stays the wire
identity of that function. The manifest grants nothing and transitions
nothing — it describes an already-authorized execution.
"""

import json
from pathlib import Path

from contract_library import canonicalize
from contract_library.schema_profile import check_profile
from contract_library.schema_profile import validate as schema_validate

from ..models import ConflictError


def _omit_none(d):
    return {k: v for k, v in d.items() if v is not None}

_SCHEMA_PATH = (Path(__file__).resolve().parent / "schemas"
                / "execution_manifest.schema.json")
_SCHEMA_CACHE = []


def manifest_schema():
    if not _SCHEMA_CACHE:
        with open(str(_SCHEMA_PATH), "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        check_profile(schema)
        _SCHEMA_CACHE.append(schema)
    return _SCHEMA_CACHE[0]


def build_execution_manifest(sequencer, attempt_id, staffing_assignment,
                             capability, parent_call_id=None):
    """Build from durable ledger state after t-a02.

    ``staffing_assignment`` is the Taskforce staffing decision for this
    attempt (provider, model, selected_by, role, expected_output, and the
    frozen source revision when the work has one). The manifest re-reads
    every other field from the ledger.

    Returns ``(canonical_bytes, content_address)``."""
    db = sequencer.db.conn
    att = db.execute(
        "SELECT * FROM attempts WHERE attempt_id = ?",
        (attempt_id,)).fetchone()
    if att is None:
        raise ConflictError(
            "attempt %s does not exist" % attempt_id)
    if att["state"] != "DISPATCHED":
        raise ConflictError(
            "manifest requires DISPATCHED, found %s" % att["state"])
    call = db.execute(
        "SELECT * FROM attempt_calls WHERE attempt_id = ?",
        (attempt_id,)).fetchone()
    if call is None:
        raise ConflictError(
            "attempt %s has no bound call (t-a02 incomplete)"
            % attempt_id)
    seq = db.execute(
        "SELECT * FROM sequences WHERE sequence_id = ?",
        (att["sequence_id"],)).fetchone()
    record_row = db.execute(
        "SELECT content_address FROM records WHERE record_hash = ?",
        (seq["record_hash"],)).fetchone()
    record = json.loads(
        sequencer.store.get(record_row["content_address"]).decode("utf-8"))

    role_transforms = record.get("role_transforms", {})
    transform = role_transforms.get(call["role"])
    transform_field = None
    if transform is not None:
        bindings = record.get("resolved_bindings", {})
        transform_hashes = bindings.get("transform_registrations", [])
        transform_field = {"transform_id": transform}
        if transform_hashes:
            transform_field["registration_hash"] = transform_hashes[0]

    artifact = {
        "schema_version": "v1",
        "kind": "ExecutionManifest",
        "sequence_id": att["sequence_id"],
        "attempt_id": attempt_id,
        "attempt_contract_hash": att["attempt_contract_hash"],
        "call_id": call["call_id"],
        "projection_address": call["projection_address"],
        "role": call["role"],
        "capability": capability,
        # The execution route of THIS attempt: the provider the ledger
        # bound at dispatch and the provider's own model identifier
        # exactly as routing resolved it. A later attempt of the same
        # work may carry a different route; nothing here is part of the
        # frozen contract. (This schema is shared byte for byte with
        # AutoSwarm's vendored copy, so the route's reasoning effort and
        # session arguments travel beside the manifest, not in it.)
        "provider_assignment": _omit_none({
            "provider": call["provider"],
            "model": staffing_assignment.get("model"),
            "selected_by": staffing_assignment.get("selected_by",
                                                    "agent_factory"),
        }),
        "constraints": {
            "finalization_class": record["finalization_class"],
            "evidence_requirements": record.get(
                "evidence_requirements", []),
        },
    }
    wp = record.get("work_profile")
    if wp is not None:
        artifact["constraints"]["work_profile"] = wp
    ep = record.get("execution_profile")
    if ep is not None:
        artifact["constraints"]["execution_profile"] = ep
    rs = record.get("resource_scope")
    if rs is not None:
        artifact["constraints"]["resource_scope"] = rs
    source_rev = staffing_assignment.get("source_revision")
    if source_rev is not None:
        artifact["constraints"]["source_revision"] = source_rev
    target = record.get("source_domain", {}).get("target_ref")
    if target is not None:
        artifact["constraints"]["target_ref"] = target
    if transform_field is not None:
        artifact["role_transform"] = transform_field
    if parent_call_id is not None:
        artifact["parent_execution"] = {
            "parent_call_id": parent_call_id,
        }

    issues = schema_validate(artifact, manifest_schema())
    if issues:
        raise ConflictError(
            "manifest failed schema validation: %s"
            % "; ".join(repr(i) for i in issues))
    data = canonicalize(artifact)
    return data, sequencer.store.put(data)
