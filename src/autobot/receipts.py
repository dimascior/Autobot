"""The registered ExecutionReceipt (obj-15) semantics, pinned to Chapter 0.

cap-30 makes the collector the owner of "ExecutionReceipt (admission,
completeness per role, index state)" — task_force forbidden; objects.json
registers obj-15 with minter ``collector``, class receipt, mutability
"append-only; re-collection makes a new version" and attempt binding;
CORR-03 has the Collector admit the producers' WitnessRecords and mint the
ONE ExecutionReceipt. This module holds the frozen vocabulary and the PURE
derivation both sides of the authority boundary share: the Collector
derives and mints, and the admission seam re-derives and refuses
divergence (D-C3-2), so a receipt can never declare a classification,
bound, role fact or witness set the ledger does not support. Tests pin
every table here to the registry files (the witnesses.py discipline).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contract_library import canonicalize
from contract_library.schema_profile import check_profile
from contract_library.schema_profile import validate as schema_validate

from .models import instant

RECEIPT_KIND = "ExecutionReceipt"

# obj-15's registered minter (objects.json; CORR-03).
RECEIPT_MINTER = "collector"

# D-C3-1's registered derivation rule id, carried inside every receipt.
DERIVATION_RULE = "ch3:evidence-window-after-freeze"

# D-C3-1: a D-18 configuration default (D-28 integer seconds), never a
# semantic constant. The effective value is the stricter of delegation or
# repository policy and operational config — carried as collector
# configuration in v1.
DEFAULT_EVIDENCE_WINDOW_SECONDS = 3600

# The frozen record's evidence_requirements enum -> the registered witness
# kind that satisfies it (task_contract_record.schema.json enum on one
# side, objects.json witness entries on the other; pinned by test).
EVIDENCE_KIND_BY_REQUIREMENT = {
    "EXECUTION_LIFECYCLE": "obj-11",
    "COMMAND_GOVERNANCE": "obj-12",
    "SESSION_TRANSCRIPT": "obj-13",
    "REMOTE_CHECK": "obj-14",
}

COMPLETENESS_VALUES = ("COMPLETE", "INCOMPLETE", "INTEGRITY_FAILED")

_SCHEMA_PATH = (Path(__file__).resolve().parent / "schemas_chapter3"
                / "execution_receipt.schema.json")
_SCHEMA_CACHE = []


def receipt_schema():
    """The mirrored obj-15 schema, profile-checked once (D-43)."""
    if not _SCHEMA_CACHE:
        with open(str(_SCHEMA_PATH), "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        check_profile(schema)
        _SCHEMA_CACHE.append(schema)
    return _SCHEMA_CACHE[0]


def validate_receipt(payload):
    """Issues against the mirrored obj-15 schema (empty when valid)."""
    return schema_validate(payload, receipt_schema())


def parse_instant(text):
    """A D-27 fixed-width instant back to an aware UTC datetime."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc)


def completion_bound(anchor_utc, window_seconds):
    """D-C3-1: evidence_complete_by_utc = anchor + window (lexically
    comparable with any other D-27 instant, per D-28)."""
    return instant(parse_instant(anchor_utc)
                   + timedelta(seconds=window_seconds))


def _entry_key(entry):
    return (entry["witness_kind"], entry["producer"],
            entry["content_sha256"])


def _entry(row):
    return {"witness_kind": row["witness_kind"],
            "producer": row["producer"],
            "content_sha256": row["content_sha256"]}


def probe_integrity(store, entries):
    """The non-raising D-66 probe (D-C3-3): the admitted entries whose
    bytes are missing from the store or no longer re-hash."""
    bad = []
    for entry in entries:
        if store.verify([entry["content_sha256"]]):
            bad.append(entry)
    return bad


def build_receipt(record_payload, role, attempt_id, attempt_contract_hash,
                  call_id, witness_entries, integrity_failures, anchor_utc,
                  window_seconds):
    """The pure obj-15 derivation (D-C3-2): a function of durable ledger
    facts plus the configured window — no clock, no caller claims, so
    unchanged facts re-derive byte-identical bytes."""
    entries = sorted((_entry(e) for e in witness_entries), key=_entry_key)
    failures = sorted((_entry(e) for e in integrity_failures),
                      key=_entry_key)
    required = sorted(set(
        EVIDENCE_KIND_BY_REQUIREMENT[requirement]
        for requirement in record_payload.get("evidence_requirements", [])))
    present = sorted(set(e["witness_kind"] for e in entries))
    missing = sorted(set(required) - set(present))
    if failures:
        completeness = "INTEGRITY_FAILED"
    elif missing:
        completeness = "INCOMPLETE"
    else:
        completeness = "COMPLETE"
    return {
        "schema_version": "v1",
        "kind": RECEIPT_KIND,
        "minter": RECEIPT_MINTER,
        "attempt_id": attempt_id,
        "attempt_contract_hash": attempt_contract_hash,
        "call_id": call_id,
        "completeness": completeness,
        "completeness_by_role": {
            role: {"required_kinds": required,
                   "present_kinds": present,
                   "missing_kinds": missing}},
        "admitted_witnesses": entries,
        "integrity_failures": failures,
        "derivation": {"rule": DERIVATION_RULE,
                       "anchor_utc": anchor_utc,
                       "window_seconds": window_seconds},
        "evidence_complete_by_utc": completion_bound(anchor_utc,
                                                     window_seconds),
    }


def receipt_bytes(payload):
    """The canonical bytes whose content address is the receipt's
    identity (and the D-C3-2 equality the admission seam enforces)."""
    return canonicalize(payload)
