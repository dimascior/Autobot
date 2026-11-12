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
bound, join, member fact or witness set the ledger does not support.

C3-M2 extends the derivation with the causal evidence graph (D-C3-5/6):
recognized witness facts join each admitted entry to the bound execution,
contradictions (cross-execution identities, deny_then_ran) classify
INTEGRITY_FAILED, per-role member completeness evaluates the frozen
required membership, and artifact provenance rides the receipt. Tests pin
every table here to the registry files (the witnesses.py discipline).

C3-M3 closes the remaining registered chapter-3 obligations: the receipt
binds ``input_rehash`` — every specification input the frozen record
declares, re-probed against the content store at collection, a change
classifying INTEGRITY_FAILED (INV-37, the chapter-1 obj-15 always
binding, D-C3-9) — and the contradiction rules complete the D-12/INV-68
integrity classes with execution_without_decision (D-C3-8). The index
stays OUT of this module by construction: the payload is a pure function
of durable ledger facts, so a live catalog probe cannot enter it without
breaking the D-C3-2 byte-equality (INV-30; the diagnostic surface lives
on the Collector).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contract_library import canonicalize
from contract_library.schema_profile import check_profile
from contract_library.schema_profile import validate as schema_validate

from .models import instant
from . import witness_semantics

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


def probe_inputs(store, record_payload):
    """INV-37's collection-side re-hash (D-C3-9): every specification
    input the frozen record declares, probed non-raising against the
    content store. The receipt binds the result (the chapter-1
    ``input_rehash`` obj-15 binding), and a missing or altered input
    classifies INTEGRITY_FAILED — deterministic over store state, like
    the D-66 witness probe. Recovery halts earlier on the same
    corruption (the records loop re-resolves every declared input), the
    same deliberate interplay D-C3-3 records for witnesses."""
    declared = record_payload.get("specification_inputs", {}).get(
        "name_to_exact_byte_sha256", {})
    rehash = {}
    for name in sorted(declared):
        address = declared[name]
        rehash[name] = {"content_sha256": address,
                        "intact": not store.verify([address])}
    return rehash


def extract_all_facts(store, entries, failures):
    """Recognized facts per admitted entry (D-C3-5), keyed by content
    address; entries whose bytes failed the probe contribute none."""
    failed = set(entry["content_sha256"] for entry in failures)
    facts = {}
    for entry in entries:
        address = entry["content_sha256"]
        if address in failed:
            continue
        try:
            data = store.get(address)
        except Exception:
            continue
        extracted = witness_semantics.extract_facts(
            entry["witness_kind"], data)
        if extracted:
            facts[address] = extracted
    return facts


def build_receipt(state, integrity_failures, window_seconds,
                  witness_facts=None, input_rehash=None):
    """The pure obj-15 derivation (D-C3-2): a function of durable ledger
    facts plus the configured window — no clock, no caller claims, so
    unchanged facts re-derive byte-identical bytes.

    ``state`` is the evidence_state shape: record_payload, role,
    attempt_id, attempt_contract_hash, call_id, native_session_id (or
    None), witnesses (D-21 entry rows, insertion order) and artifacts
    (provenance rows). ``witness_facts`` maps content addresses to the
    recognized facts extracted from the exact producer bytes;
    ``input_rehash`` is probe_inputs' result over the frozen record's
    declared specification inputs (INV-37)."""
    witness_facts = witness_facts or {}
    input_rehash = input_rehash or {}
    record_payload = state["record_payload"]
    role = state["role"]
    call_id = state["call_id"]
    native = state.get("native_session_id")
    entries = sorted((_entry(e) for e in state["witnesses"]),
                     key=_entry_key)
    failures = sorted((_entry(e) for e in integrity_failures),
                      key=_entry_key)
    required = sorted(set(
        EVIDENCE_KIND_BY_REQUIREMENT[requirement]
        for requirement in record_payload.get("evidence_requirements", [])))
    present = sorted(set(e["witness_kind"] for e in entries))
    missing = sorted(set(required) - set(present))

    # --- causal joins (D-C3-5/6): recognized facts tie each admitted
    # entry to the bound execution; absence is UNJOINED (missing), a
    # conflicting identity is CONTRADICTED (integrity).
    joins = []
    fact_triples = []
    joined_any = False
    contradictions = []
    for entry in entries:
        facts = witness_facts.get(entry["content_sha256"], {})
        verdict = witness_semantics.join_verdict(
            entry["witness_kind"], facts, call_id, native)
        join = dict(entry)
        if facts:
            join["facts"] = {k: facts[k] for k in sorted(facts)}
        join["join"] = verdict
        controller = facts.get("controller_id")
        if entry["witness_kind"] == "obj-11" and controller is not None:
            join["controller_well_formed"] = \
                witness_semantics.controller_well_formed(controller)
        joins.append(join)
        fact_triples.append((_entry_key(entry), entry["witness_kind"],
                             facts))
        if verdict == "JOINED":
            joined_any = True
        elif verdict == "CONTRADICTED":
            contradictions.append(
                {"rule": "cross_execution_identity",
                 "witness_kind": entry["witness_kind"],
                 "content_sha256": entry["content_sha256"]})
    contradictions.extend(witness_semantics.deny_then_ran(fact_triples))
    contradictions.extend(
        witness_semantics.execution_without_decision(fact_triples))
    contradictions.sort(key=lambda c: (c["rule"],
                                       c.get("content_sha256", ""),
                                       c.get("command_sha256", ""),
                                       c.get("session_id", ""),
                                       c.get("tool_use_id", "")))

    # --- member completeness (C8/CORR-08/CORR-20): the frozen record's
    # completion_policies is the ONLY membership authority. Evidence
    # attributes to a role through the causal chain — in v1's
    # single-member attempts, the dispatched role owns the joined
    # evidence; other required roles cannot be attributed by construction.
    policies = record_payload.get("completion_policies", {})
    members = {}
    members_complete = True
    for member_role in sorted(policies):
        policy = policies[member_role]
        required_member = bool(policy.get("required", False))
        attributed = bool(member_role == role and joined_any)
        entry = {"required": required_member, "attributed": attributed}
        if member_role == role:
            outcomes = sorted(set(
                facts["outcome"] for _k, kind, facts in fact_triples
                if kind == "obj-11" and isinstance(facts.get("outcome"),
                                                   str)))
            if outcomes:
                entry["reported_outcomes"] = outcomes
        members[member_role] = entry
        if required_member and not attributed:
            members_complete = False

    artifacts = sorted(
        ({"logical_path": a["logical_path"],
          "content_sha256": a["content_sha256"], "role": a["role"]}
         for a in state.get("artifacts", [])),
        key=lambda a: (a["logical_path"], a["content_sha256"]))

    inputs_intact = all(entry["intact"] for entry in input_rehash.values())
    if failures or contradictions or not inputs_intact:
        completeness = "INTEGRITY_FAILED"
    elif missing or not members_complete:
        completeness = "INCOMPLETE"
    else:
        completeness = "COMPLETE"
    anchor_utc = state["anchor_utc"]
    payload = {
        "schema_version": "v1",
        "kind": RECEIPT_KIND,
        "minter": RECEIPT_MINTER,
        "attempt_id": state["attempt_id"],
        "attempt_contract_hash": state["attempt_contract_hash"],
        "call_id": call_id,
        "completeness": completeness,
        "completeness_by_role": {
            role: {"required_kinds": required,
                   "present_kinds": present,
                   "missing_kinds": missing}},
        "member_completeness": members,
        "admitted_witnesses": entries,
        "integrity_failures": failures,
        "causal_joins": joins,
        "contradictions": contradictions,
        "artifact_provenance": artifacts,
        "input_rehash": input_rehash,
        "derivation": {"rule": DERIVATION_RULE,
                       "anchor_utc": anchor_utc,
                       "window_seconds": window_seconds},
        "evidence_complete_by_utc": completion_bound(anchor_utc,
                                                     window_seconds),
    }
    if native:
        payload["native_session_id"] = native
    return payload


def derive_evidence(store, state, window_seconds):
    """The one derivation entry point the Collector, the admission seam
    and recovery share: probe integrity (non-raising, D-C3-3), re-probe
    the frozen record's specification inputs (INV-37, D-C3-9), extract
    recognized facts from the exact producer bytes (D-C3-5), and build
    the canonical obj-15 payload (D-C3-2)."""
    failures = probe_integrity(store, state["witnesses"])
    facts = extract_all_facts(store, state["witnesses"], failures)
    inputs = probe_inputs(store, state["record_payload"])
    return build_receipt(state, failures, window_seconds,
                         witness_facts=facts, input_rehash=inputs)


def receipt_bytes(payload):
    """The canonical bytes whose content address is the receipt's
    identity (and the D-C3-2 equality the admission seam enforces)."""
    return canonicalize(payload)
