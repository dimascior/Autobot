"""preimage2 - independent second implementations of the Chapter 1
identity constructions (TI-1 WP2, the D-58 standard carried from
canonicalization to the PREIMAGE layer).

Chapter 1's dual oracle (harness/jcs2.py) protects canonicalization only.
The constructions built ON canonical bytes - content addresses, the domained
object hash, the three identity tiers, the closure artifact root and the
journal entry hash - had one implementation, and the runtime suite computed
its expected values with that same implementation. This module is the
second one.

Independence rules (enforced by tests/autobot/test_independent_identity.py):
- this file imports NOTHING from ``autobot`` or ``contract_library`` (a
  source scan plus a fresh ``-I`` interpreter import-graph check);
- every layout below was written from the FROZEN chapter-1 documentation
  and proven against the frozen golden vectors, never transcribed from the
  library source:
    * CHAPTER1_SPECIFICATION.md section 2 (canonical bytes) and section 4
      (hash constructions);
    * registries/decisions.json D-24, D-29, D-30, D-31, D-32, D-33, D-37;
    * registries/semantic_rules.json SEM-17;
    * chapter0/registries/identity.json (the tier field lists);
    * vectors/golden/golden_digests.json (the acceptance vectors).

The restated rules:

Canonical bytes (D-24/D-25/D-37): RFC 8785 JCS over objects, arrays,
strings, booleans and integers in [0, 2**53-1]; null, floats and negative
integers never appear; object keys sort by UTF-16 code units.

Digest text (D-30): ``sha256:`` plus 64 lowercase hex digits.

Content address (D-31): plain SHA-256 of the exact bytes, no domain.

Domained identity (D-29): SHA-256 over ``<label> LF <canonical bytes>``
where the label is the ASCII text ``autobot/<category>/<name>/v1`` and no
label contains LF.

Object hash (D-29, SEM-17): label ``autobot/object/<kind>/v1`` over the
full payload; the payload's kind must equal the label's kind and its
schema_version must be ``v1``. obj-04 (kind TaskContractRecord) is the
exception D-33 states: its object hash is the record_hash tier.

Identity tiers (D-33, identity.json): digest-value embedding.
  requirement_digest = H(autobot/tier/requirement/v1, the record's shape
      restricted to the nine requirement fields)
  contract_digest = H(autobot/tier/contract/v1, {requirement_digest} plus
      the record restricted to the sixteen contract fields)
  record_hash = H(autobot/object/TaskContractRecord/v1, {contract_digest}
      plus the record restricted to the seven record fields)
An absent optional field is an absent key (D-37).

Artifact root (D-32): label ``autobot/closure/<kind>/v1`` over the
ClosureManifest's canonical bytes; ``<kind>`` is the manifest's own kind.

Journal entry hash (spec section 4, JH-1): label
``autobot/journal/transition/v1`` over ``{payload, previous}``; the genesis
entry omits ``previous``.

Mechanisms deliberately unlike the library's and unlike jcs2's: the
canonical writer is iterative (an explicit work stack, no recursion and no
generator), string escaping is one translation table, and key order comes
from a code-point RANK remap instead of materialized UTF-16 units (see
``utf16_rank``).

Usage as a fresh-interpreter oracle (the D-58 cross-process pattern):
    python -I preimage2.py <chapter1 fixtures dir>
prints every recomputed golden as JSON; nothing but the stdlib is loaded.
"""

import binascii
import hashlib
import json
import sys

INT_MAX = 2 ** 53 - 1
DIGEST_PREFIX = "sha256:"
RECORD_KIND = "TaskContractRecord"

# chapter0/registries/identity.json, restated; a test pins these three
# tuples to the registry file so they cannot drift from it.
REQUIREMENT_FIELDS = (
    "objective",
    "finalization_class",
    "source_domain.repository_id",
    "source_domain.authority_location",
    "source_domain.target_ref",
    "specification_inputs.name_to_exact_byte_sha256",
    "allowed_scope.paths",
    "allowed_scope.artifact_paths",
    "acceptance_assertions.ids_and_environment_constraints",
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
    "resolved_bindings.repository_authority_declaration",
    "resolved_bindings.verifier_registrations",
    "resolved_bindings.transform_registrations",
    "resolved_bindings.delegation_contract",
    "resolved_bindings.mandatory_floor",
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

LABEL_REQUIREMENT = "autobot/tier/requirement/v1"
LABEL_CONTRACT = "autobot/tier/contract/v1"
LABEL_JOURNAL = "autobot/journal/transition/v1"

# D-32 enumerates the closure kinds an artifact root exists for.
CLOSURE_KINDS = ("native", "python", "packaged")


class Preimage2Error(ValueError):
    """A value outside the documented domain of a construction."""


# ------------------------------------------------------- canonical bytes

# RFC 8785 section 3.2.2.2 as one translation table: the two mandatory
# escapes, the five shorthand controls, lowercase \u00xx for every other
# control below U+0020. Everything else is written literally.
_ESCAPES = {0x22: '\\"', 0x5C: "\\\\", 0x08: "\\b", 0x09: "\\t",
            0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r"}
for _control in range(0x20):
    _ESCAPES.setdefault(_control, "\\u%04x" % _control)
del _control


def utf16_rank(text):
    """The sort key that orders strings exactly as their UTF-16 code units
    do (RFC 8785 section 3.2.3), without materializing any code unit.

    A code point below U+D800 is one unit equal to itself. A supplementary
    code point is a surrogate pair whose lead unit lies in D800..DBFF, so
    it sorts ABOVE every code point below U+D800 and BELOW every code point
    in U+E000..U+FFFF; two supplementary code points compare like their
    (lead, trail) pairs, which is their code-point order. Ranking
    U+E000..U+FFFF above the supplementary planes (adding 0x110000) and
    leaving everything else at its code point therefore reproduces the
    unit order character by character. Lone surrogates have no UTF-8 form
    and are refused."""
    rank = []
    for char in text:
        point = ord(char)
        if 0xD800 <= point <= 0xDFFF:
            raise Preimage2Error("lone surrogate U+%04X has no canonical"
                                 " form" % point)
        if 0xE000 <= point <= 0xFFFF:
            point += 0x110000
        rank.append(point)
    return rank


def _quote(text):
    return '"' + text.translate(_ESCAPES) + '"'


def canonical_text(value):
    """The canonical JSON text (a str) of one value in the D-24 domain."""
    pieces = []
    work = [(False, value)]
    while work:
        literal, item = work.pop()
        if literal:
            pieces.append(item)
        elif item is True:
            pieces.append("true")
        elif item is False:
            pieces.append("false")
        elif item is None:
            raise Preimage2Error("null never appears (D-37: absence is an"
                                 " absent key)")
        elif isinstance(item, float):
            raise Preimage2Error("fractional numbers are forbidden (D-24)")
        elif isinstance(item, int):
            if item < 0 or item > INT_MAX:
                raise Preimage2Error("integer %r is outside [0, 2**53-1]"
                                     " (D-24)" % (item,))
            pieces.append("%d" % item)
        elif isinstance(item, str):
            pieces.append(_quote(item))
        elif isinstance(item, (list, tuple)):
            work.append((True, "]"))
            for index in range(len(item) - 1, -1, -1):
                work.append((False, item[index]))
                if index:
                    work.append((True, ","))
            work.append((True, "["))
        elif isinstance(item, dict):
            keys = list(item)
            for key in keys:
                if not isinstance(key, str):
                    raise Preimage2Error("object keys are strings, not %s"
                                         % type(key).__name__)
            keys.sort(key=utf16_rank)
            work.append((True, "}"))
            for index in range(len(keys) - 1, -1, -1):
                work.append((False, item[keys[index]]))
                work.append((True, _quote(keys[index]) + ":"))
                if index:
                    work.append((True, ","))
            work.append((True, "{"))
        else:
            raise Preimage2Error("type %s is outside the canonical domain"
                                 % type(item).__name__)
    return "".join(pieces)


def canonical_bytes(value):
    """RFC 8785 canonical bytes of one value in the D-24 domain."""
    try:
        return canonical_text(value).encode("utf-8")
    except UnicodeEncodeError:
        raise Preimage2Error("string content has no UTF-8 form (lone"
                             " surrogate)")


# ------------------------------------------------------------- digests

def digest_text(data):
    """D-30 text of the SHA-256 of ``data``: fed in bounded slices so a
    large store object never needs a second in-memory copy."""
    hasher = hashlib.new("sha256")
    view = memoryview(data)
    for offset in range(0, len(view), 1 << 16):
        hasher.update(view[offset:offset + (1 << 16)])
    return DIGEST_PREFIX + binascii.hexlify(hasher.digest()).decode("ascii")


def is_digest_text(value):
    """True for exactly ``sha256:`` plus 64 lowercase hex digits (D-30)."""
    if not isinstance(value, str) or len(value) != 71:
        return False
    if not value.startswith(DIGEST_PREFIX):
        return False
    return all(char in "0123456789abcdef" for char in value[7:])


def content_address2(data):
    """D-31: the plain SHA-256 of the exact bytes, no domain."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise Preimage2Error("a content address is over bytes, not %s"
                             % type(data).__name__)
    return digest_text(bytes(data))


def domain_prefix(label):
    """D-29 framing: the ASCII label plus exactly one LF. A label carrying
    LF (or any non-ASCII character) would break prefix-freedom and is
    refused."""
    if not isinstance(label, str) or not label:
        raise Preimage2Error("a domain label is non-empty text")
    if "\n" in label:
        raise Preimage2Error("a domain label contains no LF (D-29)")
    try:
        return label.encode("ascii") + b"\n"
    except UnicodeEncodeError:
        raise Preimage2Error("a domain label is ASCII (D-29)")


def domained_digest2(label, payload):
    """SHA-256 over ``label LF canonical_bytes(payload)`` (D-29)."""
    return digest_text(domain_prefix(label) + canonical_bytes(payload))


# ----------------------------------------------------------------- tiers

def restrict(record, paths):
    """The record's shape restricted to exactly the named dotted field
    paths (D-33): a present value keeps its nested position, an absent one
    stays absent (D-37) and never materializes an empty parent."""
    out = {}
    for path in paths:
        parts = path.split(".")
        node = record
        present = True
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                present = False
                break
        if not present:
            continue
        target = out
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = node
    return out


def requirement_preimage2(record):
    return restrict(record, REQUIREMENT_FIELDS)


def contract_preimage2(record, requirement_digest):
    preimage = restrict(record, CONTRACT_FIELDS)
    preimage["requirement_digest"] = requirement_digest
    return preimage


def record_preimage2(record, contract_digest):
    preimage = restrict(record, RECORD_FIELDS)
    preimage["contract_digest"] = contract_digest
    return preimage


def compute_tiers2(record):
    """(requirement_digest, contract_digest, record_hash) for one frozen
    record payload, by digest-value embedding (D-33)."""
    if not isinstance(record, dict):
        raise Preimage2Error("a record payload is an object")
    requirement = domained_digest2(LABEL_REQUIREMENT,
                                   requirement_preimage2(record))
    contract = domained_digest2(LABEL_CONTRACT,
                                contract_preimage2(record, requirement))
    record_hash = domained_digest2(object_label(RECORD_KIND),
                                   record_preimage2(record, contract))
    return requirement, contract, record_hash


# ---------------------------------------------------------- object hash

def object_label(kind):
    if not isinstance(kind, str) or not kind:
        raise Preimage2Error("an object kind is non-empty text")
    return "autobot/object/%s/v1" % kind


def object_hash2(kind, payload):
    """A protocol object's hash under its per-kind domain (D-29), with
    SEM-17's refusal: the payload's own kind must be the label's kind and
    its schema_version must be the label's version segment."""
    if not isinstance(payload, dict):
        raise Preimage2Error("an object payload is an object")
    if payload.get("kind") != kind:
        raise Preimage2Error("SEM-17: payload kind %r is not the domain"
                             " kind %r" % (payload.get("kind"), kind))
    if payload.get("schema_version") != "v1":
        raise Preimage2Error("SEM-17: payload schema_version %r is not the"
                             " domain's v1" % (payload.get("schema_version"),))
    if kind == RECORD_KIND:
        return compute_tiers2(payload)[2]
    return domained_digest2(object_label(kind), payload)


# -------------------------------------------------------- artifact root

def artifact_root2(manifest):
    """D-32: the closure identity over the ClosureManifest's canonical
    bytes, domained by the manifest's own closure kind."""
    if not isinstance(manifest, dict):
        raise Preimage2Error("a ClosureManifest is an object")
    kind = manifest.get("kind")
    if kind not in CLOSURE_KINDS:
        raise Preimage2Error("closure kind %r is not one of D-32's %s"
                             % (kind, ", ".join(CLOSURE_KINDS)))
    return domained_digest2("autobot/closure/%s/v1" % kind, manifest)


# -------------------------------------------------------------- journal

def journal_entry_hash2(payload, previous=None):
    """JH-1: ``H(label, {payload, previous})``; genesis omits previous."""
    wrapper = {"payload": payload}
    if previous is not None:
        wrapper["previous"] = previous
    return domained_digest2(LABEL_JOURNAL, wrapper)


# -------------------------------------------------- strict JSON reading

def _refuse_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise Preimage2Error("duplicate object key %r" % (key,))
        out[key] = value
    return out


def _refuse_number(text):
    raise Preimage2Error("number %r is outside the canonical domain"
                         % (text,))


def strict_loads(text):
    """Parse JSON text refusing what the canonical domain cannot hold:
    duplicate keys, fractional or exponent numbers, NaN/Infinity. (null is
    refused by ``canonical_text`` when a caller canonicalizes; producer
    witnesses may legitimately carry it, so the reader itself tolerates
    it.)"""
    return json.loads(text, object_pairs_hook=_refuse_duplicates,
                      parse_float=_refuse_number,
                      parse_constant=_refuse_number)


# ------------------------------------------- fresh-interpreter recompute

def recompute_goldens(fixtures_dir):
    """Recompute every frozen golden this module can derive, from the
    WRITTEN fixture files alone (the D-58 oracle-5 shape)."""
    import os

    def load(name):
        with open(os.path.join(fixtures_dir, name), "rb") as handle:
            return strict_loads(handle.read().decode("utf-8"))

    graph = load("graph.json")
    base = load("va_record.json")
    payloads = load("vector_payloads.json")
    environment = load("env_fixture.json")
    out = {
        "object_hashes": dict(
            (name, object_hash2(payload["kind"], payload))
            for name, payload in graph.items()),
        "artifact_roots": dict(
            (name, artifact_root2(payload["closure"]))
            for name, payload in graph.items() if "closure" in payload),
        "actual_verification_environment_hash": object_hash2(
            environment["kind"], environment),
        "canonical_record_payload_sha256": content_address2(
            canonical_bytes(base)),
    }
    requirement, contract, record_hash = compute_tiers2(base)
    out["V-A"] = {
        "requirement_digest": requirement,
        "contract_digest": contract,
        "record_hash": record_hash,
        "canonical_requirement_preimage": canonical_text(
            requirement_preimage2(base)),
        "canonical_contract_preimage": canonical_text(
            contract_preimage2(base, requirement)),
        "canonical_record_preimage": canonical_text(
            record_preimage2(base, contract)),
    }
    for vector_id in sorted(payloads):
        tiers = compute_tiers2(payloads[vector_id])
        out[vector_id] = {"requirement_digest": tiers[0],
                          "contract_digest": tiers[1],
                          "record_hash": tiers[2]}
    neutral = {"kind": "neutral", "schema_version": "v1"}
    out["V-L"] = {
        "payload_canonical": canonical_text(neutral),
        "as_delegation_contract": domained_digest2(
            object_label("DelegationContract"), neutral),
        "as_repository_authority_declaration": domained_digest2(
            object_label("RepositoryAuthorityDeclaration"), neutral),
    }
    first = journal_entry_hash2({"transition": "t-c02", "seq": 1})
    second = journal_entry_hash2({"transition": "t-s06", "seq": 2},
                                 previous=first)
    third = journal_entry_hash2({"transition": "t-s07", "seq": 3},
                                previous=second)
    out["JH-1"] = [first, second, third]
    return out


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: python -I preimage2.py <fixtures dir>\n")
        return 2
    out = recompute_goldens(argv[1])
    out["loaded_modules"] = sorted(
        set(name.split(".")[0] for name in sys.modules))
    json.dump(out, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
