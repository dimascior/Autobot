"""Digest constructions (D-29..D-33): domains, tiers, object hashes,
content addresses, artifact roots and the journal hash chain.

Every semantic identity is SHA-256 over ``domain || canonical_bytes`` where
the domain is ASCII ``autobot/<category>/<name>/v1`` terminated by one LF
(0x0A). No domain contains LF, so the framing is prefix-free: two different
domains can never read the same hash input (D-29). Content addresses are
plain SHA-256 of exact bytes (D-31). Digest text is ``sha256:`` plus 64
lowercase hex digits (D-30). Stdlib only (D-59).
"""

import hashlib
import re

from .canonical import canonicalize

DOMAIN_TIER_REQUIREMENT = b"autobot/tier/requirement/v1\n"
DOMAIN_TIER_CONTRACT = b"autobot/tier/contract/v1\n"
DOMAIN_JOURNAL_TRANSITION = b"autobot/journal/transition/v1\n"

_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.]*$")
_CLOSURE_KINDS = ("native", "python", "packaged")

# The nine requirement-tier fields, the sixteen contract-tier fields and the
# seven record-tier fields, exactly as identity.json lists them (D-33).
REQUIREMENT_FIELDS = (
    "objective",
    "finalization_class",
    "source_domain",
    "specification_inputs",
    "allowed_scope",
    "acceptance_assertions",
)
CONTRACT_FIELDS = (
    "retry_policy",
    "repair_policy",
    "reuse_policy",
    "effect_policy",
    "finalization_requirements",
    "remote_verification_mode",
    "capability_requirements",
    "role_transforms",
    "completion_policies",
    "evidence_requirements",
    "predecessors",
    "resolved_bindings",
)
RECORD_FIELDS = (
    "task_contract_id",
    "revision",
    "authored_by",
    "frozen_utc",
    "delegation_id",
    "supersession_provenance",
    "force_new_sequence",
)


class DigestError(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def digest_text(hex_digest):
    return "sha256:" + hex_digest


def content_address(data):
    """Plain SHA-256 of exact bytes (D-31), spelled per D-30."""
    if not isinstance(data, (bytes, bytearray)):
        raise DigestError("ERR_BYTES_REQUIRED", "content addressing takes bytes")
    return digest_text(_sha256_hex(bytes(data)))


def domain_object(kind):
    if not _KIND_RE.match(kind):
        raise DigestError("ERR_DOMAIN_KIND", "invalid kind %r" % kind)
    return ("autobot/object/%s/v1\n" % kind).encode("ascii")


def domain_closure(kind):
    if kind not in _CLOSURE_KINDS:
        raise DigestError("ERR_DOMAIN_KIND", "invalid closure kind %r" % kind)
    return ("autobot/closure/%s/v1\n" % kind).encode("ascii")


def domained_digest(domain, payload):
    """SHA-256 over ``domain || canonicalize(payload)``, spelled per D-30."""
    if not isinstance(domain, bytes) or not domain.endswith(b"\n") or b"\n" in domain[:-1]:
        raise DigestError("ERR_DOMAIN_FRAMING", "domain must be LF-terminated, LF-free bytes")
    return digest_text(_sha256_hex(domain + canonicalize(payload)))


def _subset(record, fields):
    out = {}
    for field in fields:
        if field in record:
            out[field] = record[field]
    return out


def requirement_preimage(record):
    return _subset(record, REQUIREMENT_FIELDS)


def requirement_digest(record):
    return domained_digest(DOMAIN_TIER_REQUIREMENT, requirement_preimage(record))


def contract_preimage(record, req_digest=None):
    preimage = _subset(record, CONTRACT_FIELDS)
    preimage["requirement_digest"] = req_digest or requirement_digest(record)
    return preimage


def contract_digest(record, req_digest=None):
    return domained_digest(DOMAIN_TIER_CONTRACT, contract_preimage(record, req_digest))


def record_preimage(record, con_digest=None):
    preimage = _subset(record, RECORD_FIELDS)
    preimage["contract_digest"] = con_digest or contract_digest(record)
    return preimage


def record_hash(record, con_digest=None):
    """obj-04's object hash: the third tier, under the object domain (D-29)."""
    return domained_digest(
        domain_object("TaskContractRecord"), record_preimage(record, con_digest)
    )


def compute_tiers(record):
    """(requirement_digest, contract_digest, record_hash) for one record."""
    rd = requirement_digest(record)
    cd = contract_digest(record, rd)
    rh = record_hash(record, cd)
    return rd, cd, rh


def object_hash(kind, payload):
    """A flat protocol object's hash. Refuses a kind or version mismatch (SEM-17)."""
    if payload.get("kind") != kind:
        raise DigestError(
            "ERR_KIND_MISMATCH",
            "payload kind %r != domain kind %r" % (payload.get("kind"), kind),
        )
    if payload.get("schema_version") != "v1":
        raise DigestError(
            "ERR_VERSION_MISMATCH",
            "payload schema_version %r != domain version v1" % payload.get("schema_version"),
        )
    return domained_digest(domain_object(kind), payload)


def artifact_root(manifest):
    """D-32: the domained hash of a ClosureManifest, per closure kind."""
    kind = manifest.get("kind")
    return domained_digest(domain_closure(kind), manifest)


def journal_entry_hash(payload, previous=None):
    """D-29/D-55: hash-chained journal entries; genesis omits ``previous`` (D-37)."""
    entry = {"payload": payload}
    if previous is not None:
        entry["previous"] = previous
    return domained_digest(DOMAIN_JOURNAL_TRANSITION, entry)
