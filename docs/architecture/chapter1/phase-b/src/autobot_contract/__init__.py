"""autobot_contract — the Chapter 1 contract library (D-19..D-61).

Deterministic representation, identity and validation for Autobot's
protocol objects. Dependency boundary (D-59): the Python standard library
only; no AutoSwarm, Helios, HDB, Git execution, network, authority-service
runtime, storage engine or agent APIs.

Public API (the typed interface the Chapter 1 specification documents):

Canonicalization (D-24, D-09)
    canonicalize(value) -> bytes
    CanonicalizationError

Digests (D-29..D-33)
    content_address(data: bytes) -> str
    domained_digest(domain: bytes, payload) -> str
    object_hash(kind: str, payload: dict) -> str
    requirement_digest(record) / contract_digest(record) / record_hash(record) -> str
    compute_tiers(record) -> (rd, cd, rh)
    artifact_root(manifest: dict) -> str
    journal_entry_hash(payload: dict, previous: str | None) -> str
    requirement_preimage / contract_preimage / record_preimage (constructions)

Grammars (D-22, D-26, D-27, D-30, D-40)
    DIGEST_RE, INSTANT_RE, MINTED_ID_RE, NAME_ID_RE, PRINCIPAL_RE
    is_nfc, minted_id_ok, repo_path_ok, ref_name_ok, path_contains,
    scope_within, utf16_key

Validation (D-34..D-37, D-43, D-52, D-54; SEM-01..SEM-18)
    load_schemas(schemas_dir) -> dict
    recognize_version(payload) -> SUPPORTED | UNSUPPORTED_VERSION | MALFORMED_VERSION
    validate_payload(kind, payload, schemas) -> ValidationResult
    validate_freeze(record, context) -> (hard_issues, queue_issues)
    freeze_disposition(record, context, schemas) -> dict        (P-1)
    admit(payload, live_sequences, context, schemas) -> dict    (P-2)
    derive_eligibility(record) -> AUTOMATIC | HUMAN_REQUIRED    (D-52)
    derived_finalization_requirements(cls, location, mode)      (SEM-05)
"""

from .canonical import CanonicalizationError, canonicalize, INT_MAX
from .digests import (
    DigestError,
    DOMAIN_JOURNAL_TRANSITION,
    DOMAIN_TIER_CONTRACT,
    DOMAIN_TIER_REQUIREMENT,
    artifact_root,
    compute_tiers,
    content_address,
    contract_digest,
    contract_preimage,
    digest_text,
    domain_closure,
    domain_object,
    domained_digest,
    journal_entry_hash,
    object_hash,
    record_hash,
    record_preimage,
    requirement_digest,
    requirement_preimage,
)
from .grammars import (
    DIGEST_RE,
    INSTANT_RE,
    MINTED_ID_RE,
    NAME_ID_RE,
    PRINCIPAL_RE,
    is_nfc,
    minted_id_ok,
    path_contains,
    ref_name_ok,
    repo_path_ok,
    scope_within,
    utf16_key,
)
from .schema_profile import Issue, SchemaProfileError, check_profile
from .validate import (
    ValidationResult,
    admit,
    derive_eligibility,
    derived_finalization_requirements,
    freeze_disposition,
    load_schemas,
    recognize_version,
    validate_freeze,
    validate_payload,
)

__all__ = [name for name in dir() if not name.startswith("_")]
