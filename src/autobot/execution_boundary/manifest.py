"""Build the ExecutionManifest from durable post-t-a02 state.

Every authority-bearing field is re-read from the Sequencer's ledger,
never accepted as a parameter. The provider assignment comes from the
Agent Factory result because that is exactly what the factory owns.
The manifest grants nothing and transitions nothing — it describes
an already-authorized execution.
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


def build_execution_manifest(sequencer, attempt_id, factory_assignment,
                             capability, parent_call_id=None):
    """Build from durable ledger state after t-a02.

    ``factory_assignment`` is the Agent Factory's selection result
    (provider, model, selected_by, role, expected_output). The
    manifest re-reads every other field from the ledger.

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
        "provider_assignment": _omit_none({
            "provider": call["provider"],
            "model": factory_assignment.get("model"),
            "selected_by": factory_assignment.get("selected_by",
                                                   "agent_factory"),
        }),
        "constraints": {
            "finalization_class": record["finalization_class"],
            "evidence_requirements": record.get(
                "evidence_requirements", []),
        },
    }
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
