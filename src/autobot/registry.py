"""The Registry authority service (actor ``registry``).

Admits bound protocol objects (declarations, delegations, registrations)
into the ledger + CAS, enforces SEM-19 repository_id uniqueness (D-23), and
runs the t-c01..t-c07 contract-admission machine over the packaged contract
library's P-1/P-2 (D-65). Every admission outcome commits in exactly one
transaction with its TransitionRecords (D-68), so the live-Sequence lookup,
the decision and the Sequence's lifecycle-coupled creation (INV-40) are
atomic — this is the real ledger replacing P-2's live-Sequence stub.

Admission transactions serialize on the database's single write lock
(BEGIN IMMEDIATE); the instance lease additionally fences the Sequencer's
multi-step authority (sequencer.py).
"""

import json
from pathlib import Path

from contract_library import (
    CanonicalizationError,
    admit,
    canonicalize,
    derive_eligibility,
    load_schemas,
    object_hash,
    validate_payload,
)

from . import journal
from .models import (
    SEQUENCE_INITIAL,
    SEQUENCE_TERMINAL,
    AdmissionError,
    instant,
    mint_id,
)

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

_SEQUENCE_TERMINAL_SQL = "('%s')" % "','".join(sorted(SEQUENCE_TERMINAL))


class Registry:
    def __init__(self, db, store, clock, schemas_dir=None):
        self.db = db
        self.store = store
        self.clock = clock
        self.schemas = load_schemas(str(schemas_dir or SCHEMAS_DIR))

    # ------------------------------------------------------- bound objects

    def admit_object(self, payload, submitted_by="you"):
        """Admit one non-record protocol object (declaration, delegation,
        registration): validate, store its canonical bytes, index it, and —
        for RepositoryAuthorityDeclaration — bind repository_id (SEM-19)."""
        if not isinstance(payload, dict):
            raise AdmissionError("payload must be an object")
        kind = payload.get("kind")
        result = validate_payload(kind, payload, self.schemas)
        if not result.ok:
            raise AdmissionError(
                "object failed validation: %s" % ", ".join(result.codes())
            )
        payload_hash = object_hash(kind, payload)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            address = self.store.put(canonicalize(payload))
            cur.execute(
                "INSERT OR IGNORE INTO objects (object_hash, kind, content_address, minted_utc)"
                " VALUES (?, ?, ?, ?)",
                (payload_hash, kind, address, now),
            )
            if cur.rowcount == 0:
                # Idempotent re-admission of identical bytes: nothing
                # happened, so nothing is journaled.
                return payload_hash
            if kind == "RepositoryAuthorityDeclaration":
                # SEM-19 / D-23: one logical repository per repository_id —
                # the PRIMARY KEY holds the uniqueness; a newer declaration
                # for the same repository revises the binding.
                cur.execute(
                    "INSERT INTO declarations (repository_id, object_hash) VALUES (?, ?)"
                    " ON CONFLICT(repository_id) DO UPDATE SET object_hash = excluded.object_hash",
                    (payload["repository_id"], payload_hash),
                )
            journal.append(cur, journal.transition_record(
                machine="registry_objects", subject_id=payload_hash,
                rule="ch2:admit-object", to_state="ADMITTED", recorded_utc=now,
                facts={"kind": kind, "content_address": address,
                       "submitted_by": submitted_by},
            ))
        return payload_hash

    # ------------------------------------------------------------ helpers

    def _preserved_bytes(self, payload, raw):
        if raw is not None:
            return bytes(raw)
        try:
            return canonicalize(payload)
        except CanonicalizationError:
            # Non-canonicalizable submissions are still preserved by exact
            # bytes so the rejection is auditable (D-21 spirit).
            return json.dumps(
                payload, sort_keys=True, ensure_ascii=False
            ).encode("utf-8")

    def _lookup_payload(self, cur, bound_hash):
        row = cur.execute(
            "SELECT content_address FROM objects WHERE object_hash = ?",
            (bound_hash,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(self.store.get(row["content_address"]).decode("utf-8"))

    def _binding_context(self, cur, record):
        """validate_freeze's context, resolved from the ledger. Returns
        (context, missing_hashes)."""
        bindings = record.get("resolved_bindings", {})
        if not isinstance(bindings, dict):
            return None, ["$.resolved_bindings is not an object"]
        missing = []

        def bound(value):
            if not isinstance(value, str):
                missing.append(repr(value))
                return None
            payload = self._lookup_payload(cur, value)
            if payload is None:
                missing.append(value)
                return None
            return {"payload": payload, "hash": value}

        declaration = bound(bindings.get("repository_authority_declaration"))
        delegation = None
        if "delegation_contract" in bindings:
            delegation = bound(bindings["delegation_contract"])
        verifiers = [bound(h) for h in bindings.get("verifier_registrations", [])]
        transforms = [bound(h) for h in bindings.get("transform_registrations", [])]
        if missing:
            return None, missing
        return {
            "declaration": declaration,
            "delegation": delegation,
            "verifier_registrations": verifiers,
            "transform_registrations": transforms,
        }, []

    def _unresolved_inputs(self, record):
        """t-c02's inputs_resolved_exact_bytes precondition: every declared
        specification input must resolve in the CAS."""
        inputs = record.get("specification_inputs", {})
        mapping = inputs.get("name_to_exact_byte_sha256", {}) if isinstance(inputs, dict) else {}
        unresolved = []
        if isinstance(mapping, dict):
            for name in sorted(mapping):
                address = mapping[name]
                if not isinstance(address, str) or not self.store.has(address):
                    unresolved.append(name)
        return unresolved

    # ----------------------------------------------------------- admission

    def admit_payload(self, payload, submitted_by, raw=None):
        """The t-c01..t-c07 pipeline for one submission, atomically.

        Returns the P-2 outcome dict extended with admission_id and, for
        NEW_RECORD, the created sequence_id (INV-40 lifecycle coupling)."""
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise AdmissionError(
                    "submission is not parseable JSON (t-c01 requires parsed)"
                )
        preserved = self._preserved_bytes(payload, raw)
        now = instant(self.clock.now())

        with self.db.transaction() as cur:
            address = self.store.put(preserved)
            cur.execute(
                "INSERT INTO admissions (state, payload_content_address, submitted_by, received_utc)"
                " VALUES ('RECEIVED', ?, ?, ?)",
                (address, submitted_by, now),
            )
            admission_id = cur.lastrowid
            subject = "adm-%d" % admission_id
            journal.append(cur, journal.transition_record(
                machine="contract_admission", subject_id=subject, rule="t-c01",
                from_state="RECEIVED", to_state="DRAFT", recorded_utc=now,
                facts={"payload_content_address": address,
                       "submitted_by": submitted_by},
            ))

            # The atomic live-Sequence lookup (the stub's real replacement).
            live = {}
            for row in cur.execute(
                "SELECT contract_digest, record_hash FROM sequences"
                " WHERE state NOT IN %s" % _SEQUENCE_TERMINAL_SQL
            ).fetchall():
                live[row["contract_digest"]] = row["record_hash"]

            context, missing = self._binding_context(cur, payload) \
                if isinstance(payload, dict) else (None, ["payload is not an object"])
            if missing:
                issues = [{"code": "ERR_UNRESOLVED_BINDING", "path": "$.resolved_bindings",
                           "message": "bound object not in the ledger: %s" % h}
                          for h in missing]
                return self._finish(cur, admission_id, subject, "t-c07",
                                    "DRAFT", "REJECTED", now,
                                    {"outcome": "REJECTED", "records_minted": 0,
                                     "sequences_created": 0, "issues": issues})

            result = admit(payload, live, context, self.schemas)

            if result["outcome"] == "NEW_RECORD":
                unresolved = self._unresolved_inputs(payload)
                if unresolved:
                    issues = [{"code": "ERR_UNRESOLVED_INPUT",
                               "path": "$.specification_inputs.name_to_exact_byte_sha256.%s" % name,
                               "message": "input bytes not in the content store (t-c02/t-c07)"}
                              for name in unresolved]
                    return self._finish(cur, admission_id, subject, "t-c07",
                                        "DRAFT", "REJECTED", now,
                                        {"outcome": "REJECTED", "records_minted": 0,
                                         "sequences_created": 0, "issues": issues})
                expiry_issue = self._delegation_expired(context, now)
                if expiry_issue is not None:
                    result = {"outcome": "QUEUED_FOR_YOU", "records_minted": 0,
                              "sequences_created": 0, "issues": [expiry_issue]}

            if result["outcome"] == "REJECTED":
                return self._finish(cur, admission_id, subject, "t-c07",
                                    "DRAFT", "REJECTED", now, result)

            if result["outcome"] == "QUEUED_FOR_YOU":
                return self._finish(cur, admission_id, subject, "t-c04",
                                    "DRAFT", "QUEUED_FOR_YOU", now, result,
                                    decided=False)

            if result["outcome"] == "DUPLICATE":
                dup = dict(result["duplicate_admission"])
                duplicate_id = mint_id("da")
                cur.execute(
                    "INSERT INTO duplicate_admissions (duplicate_admission_id,"
                    " payload_contract_digest, existing_record_hash,"
                    " payload_content_address, submitted_by, received_utc)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (duplicate_id, dup["payload_contract_digest"],
                     dup["existing_record_hash"], address, submitted_by, now),
                )
                result = dict(result, duplicate_admission_id=duplicate_id)
                return self._finish(
                    cur, admission_id, subject, "t-c03", "DRAFT", "DUPLICATE",
                    now, result,
                    extra_facts={"duplicate_admission_id": duplicate_id,
                                 "existing_record_hash": dup["existing_record_hash"],
                                 "mints": ["obj-32"]},
                    contract_digest=dup["payload_contract_digest"],
                )

            # NEW_RECORD: freeze the record and couple the Sequence (INV-40).
            rd = result["requirement_digest"]
            cd = result["contract_digest"]
            rh = result["record_hash"]
            eligibility = derive_eligibility(payload)
            cur.execute(
                "INSERT OR IGNORE INTO records (record_hash, task_contract_id,"
                " requirement_digest, contract_digest, content_address,"
                " eligibility, frozen_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rh, payload["task_contract_id"], rd, cd, address,
                 eligibility, payload["frozen_utc"]),
            )
            cur.execute(
                "INSERT OR IGNORE INTO objects (object_hash, kind, content_address, minted_utc)"
                " VALUES (?, 'TaskContractRecord', ?, ?)",
                (rh, address, now),
            )
            sequence_id = mint_id("sq")
            cur.execute(
                "INSERT INTO sequences (sequence_id, record_hash, requirement_digest,"
                " contract_digest, state, version, fence, created_utc, updated_utc)"
                " VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?)",
                (sequence_id, rh, rd, cd, SEQUENCE_INITIAL, now, now),
            )
            result = dict(result, sequence_id=sequence_id,
                          eligibility=eligibility)
            return self._finish(
                cur, admission_id, subject, "t-c02", "DRAFT", "FROZEN", now,
                result,
                extra_facts={"record_hash": rh, "requirement_digest": rd,
                             "contract_digest": cd, "sequence_id": sequence_id,
                             "eligibility": eligibility,
                             "mints": result.get("mints", []),
                             "sequence_rule": result.get("sequence_rule", {})},
                contract_digest=cd, record_hash=rh,
            )

    @staticmethod
    def _delegation_expired(context, now):
        delegation = context.get("delegation")
        if delegation is None:
            return None
        expires = delegation["payload"].get("expires_utc")
        if isinstance(expires, str) and expires <= now:
            # The bound delegation is no longer ACTIVE (t-d02), so the freeze
            # is not within an active delegation: it waits for you (t-c04).
            return {"code": "SEM-11", "path": "$.resolved_bindings.delegation_contract",
                    "message": "bound delegation expired at admission (t-d02, INV-28)"}
        return None

    def _finish(self, cur, admission_id, subject, rule, from_state, to_state,
                now, result, extra_facts=None, decided=True,
                contract_digest=None, record_hash=None):
        issues = result.get("issues")
        cur.execute(
            "UPDATE admissions SET state = ?, contract_digest = ?, record_hash = ?,"
            " issues = ?, decided_utc = ? WHERE admission_id = ?",
            (to_state, contract_digest, record_hash,
             json.dumps(issues) if issues else None,
             now if decided else None, admission_id),
        )
        facts = {"outcome": result["outcome"]}
        if issues:
            facts["issues"] = issues
        if extra_facts:
            facts.update(extra_facts)
        journal.append(cur, journal.transition_record(
            machine="contract_admission", subject_id=subject, rule=rule,
            from_state=from_state, to_state=to_state, recorded_utc=now,
            facts=facts,
        ))
        return dict(result, admission_id=admission_id)
