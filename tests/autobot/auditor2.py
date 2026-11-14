"""auditor2 - the independent state oracle (TI-1 WP1).

The runtime suite's state assertions read the ledger through the same
connection, the same hasher and the same receipt parsers the Sequencer
writes with. This module re-derives the durable authority facts of one
runtime data directory (``autobot.db`` plus ``store/``) with its OWN code:
its own read-only connection, its own file walk and SHA-256, its own JSON
reading, its own canonical writer (``preimage2``) and its own statement of
every rule it checks. It imports NOTHING from ``autobot`` or
``contract_library`` (a source scan and a fresh-interpreter import-graph
check enforce that), it never writes, and it executes nothing.

The rules, restated from the sources named beside each:

Journal chain (src/autobot/journal.py; chapter 1 spec section 4, JH-1).
  ``transition_journal`` holds one row per TransitionRecord, ordered by
  ``seq``. ``payload`` is the canonical bytes' UTF-8 text of the record.
  ``entry_hash = SHA-256("autobot/journal/transition/v1" LF
  JCS({"payload": <record>, "previous": <previous entry_hash>}))`` and the
  genesis entry omits ``previous``. Every row's ``previous_hash`` must be
  the prior row's ``entry_hash`` (NULL for genesis). Because the stored
  text IS canonical and "payload" sorts before "previous", the preimage is
  the byte splice ``{"payload":<stored text>[,"previous":"<hash>"]}``; this
  module hashes that splice, separately proves the stored text is its own
  canonical form, and recomputes the hash a third way from the parsed
  record.

D-68 pairing (src/autobot/sequencer.py ``recover`` contract item 2).
  Every state row agrees with the LAST journal entry of its machine and
  subject (the machine's initial state when no entry exists), and every
  journaled subject has its row. Machines and subjects: ``sequence`` /
  sequence_id (initial DECLARED), ``attempt`` / attempt_id (DECLARED),
  ``permit_reservation`` / reservation_id (always journaled),
  ``contract_admission`` / ``adm-<admission_id>`` (always journaled),
  ``effect_intent`` / intent_id (PENDING: a successor or a t-s15a intent is
  minted inside another subject's entry). Beyond recovery's bounded
  last-state agreement this module also replays the chain: an entry's
  ``from_state`` must be the subject's state after its previous entry, and
  a sequence or attempt row's version and fence must be the ones its last
  entry journaled.

Admission rows. Every admitted witness, artifact, receipt, candidate,
  environment, attestation, verification result and registry object is
  journaled in the transaction that wrote its row; the row's columns must
  equal the facts that entry carries.

Content references (src/autobot/store.py; D-31, D-66). A store object
  lives at ``store/objects/<first two hex>/<64 hex>`` and its bytes must
  re-hash to its address. Every ledger column that holds a content address
  is enumerated from the live schema: a digest-valued column this module
  has not classified is itself a finding.

Identities (chapter 1 D-29/D-32/D-33, via ``preimage2``). Every indexed
  object re-derives its object hash from its stored bytes; every frozen
  record re-derives its three tiers; every registration's artifact_root
  re-derives from its closure manifest.

Receipt bindings (chapter 4 VERIFICATION.md; INV-17, D-47, D-52/D-53).
  obj-16, obj-18, obj-19/obj-20 and obj-37 are re-parsed from raw store
  bytes and every binding member is compared with the rows it names: the
  attempt identity and fence, the contract and record hashes, the one
  admitted candidate, the one admitted environment, the typed part
  reference, the bound registration, the admitted attestation, the outcome
  and its execution evidence, and - for a role-transform check - the
  observed prompt identity re-derived from the admitted obj-11 bytes.

Retention reachability (INV-58; ``Sequencer.retention_roots``). A closure
  leaf is live while any NON-TERMINAL sequence's frozen record binds its
  registration: non-terminal sequence -> record -> resolved_bindings
  (verifier and transform registrations) -> registration closure ->
  leaves. python = script + dependency lock; native = the executable;
  packaged/manifest = the manifest plus every entry a valid PackageManifest
  v1 names; packaged/image = none. A manifest is valid only when every
  entry path names the same plain file on every filesystem (F-TI-6): a
  slash-separated NFC path with no empty, ``.`` or ``..`` segment, no
  control character, backslash or edge whitespace, none of < > : " | ? *,
  no trailing dot, no Windows device name (with or without an extension,
  trailing spaces ignored), no 8.3 short-alias form (a tilde followed by
  a digit), no name the runner writes into the run directory; no two
  paths equal under case folding; none both a file and a directory of
  another.

Findings versus reports. A FINDING is durable state that contradicts a
rule above. A REPORT is a registered gap the runtime itself only reports
(a bound closure leaf with no bytes, D-C4-9; an unreferenced store object,
D-66's harmless side).

Usage as a fresh-process oracle:
    python -I auditor2.py <runtime data dir>
prints the report as JSON; exit status 0 clean, 1 findings, 2 unreadable.
"""

import hashlib
import json
import os
import sqlite3
import sys
import unicodedata

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import preimage2  # noqa: E402  (the sibling oracle: stdlib only as well)

REPORT_SCHEMA = "autobot-ti1-audit/v1"
JOURNAL_PREFIX = b"autobot/journal/transition/v1\n"

# chapter0 states.json, restated (a test pins these to the registry).
SEQUENCE_TERMINAL = frozenset((
    "COMPLETED", "SUPERSEDED", "ABANDONED", "DELEGATION_REVOKED",
    "PUBLISHED_UNVERIFIED",
))

CONTENT = "content"      # a D-31 store address: must resolve and re-hash
IDENTITY = "identity"    # a domained identity: never a store address
OPAQUE = "opaque"        # an external value that may look like a digest

# Every digest-capable ledger column, classified. A digest-valued column
# missing from this table is a finding (ledger.unclassified_digest_column).
COLUMN_ROLES = {
    ("objects", "object_hash"): IDENTITY,
    ("objects", "content_address"): CONTENT,
    ("declarations", "object_hash"): IDENTITY,
    ("records", "record_hash"): IDENTITY,
    ("records", "requirement_digest"): IDENTITY,
    ("records", "contract_digest"): IDENTITY,
    ("records", "content_address"): CONTENT,
    ("admissions", "payload_content_address"): CONTENT,
    ("admissions", "contract_digest"): IDENTITY,
    ("admissions", "record_hash"): IDENTITY,
    ("sequences", "record_hash"): IDENTITY,
    ("sequences", "requirement_digest"): IDENTITY,
    ("sequences", "contract_digest"): IDENTITY,
    ("attempts", "attempt_contract_hash"): IDENTITY,
    ("duplicate_admissions", "payload_contract_digest"): IDENTITY,
    ("duplicate_admissions", "existing_record_hash"): IDENTITY,
    ("duplicate_admissions", "payload_content_address"): CONTENT,
    ("transition_journal", "entry_hash"): IDENTITY,
    ("transition_journal", "previous_hash"): IDENTITY,
    ("attempt_calls", "projection_address"): CONTENT,
    ("attempt_witnesses", "content_sha256"): CONTENT,
    ("attempt_verifications", "receipt_sha256"): CONTENT,
    ("attempt_verifications", "environment_sha256"): CONTENT,
    ("released_edges", "edge_value"): IDENTITY,
    ("released_edges", "acceptance_sha256"): CONTENT,
    ("released_edges", "release_sha256"): CONTENT,
    ("attempt_receipts", "content_sha256"): CONTENT,
    ("attempt_artifacts", "content_sha256"): CONTENT,
    ("attempt_candidates", "content_sha256"): CONTENT,
    ("attempt_environments", "content_sha256"): CONTENT,
    ("attempt_attestations", "content_sha256"): CONTENT,
    ("effect_intents", "target"): OPAQUE,
    ("effect_intents", "expected_old"): OPAQUE,
    ("effect_intents", "new_value"): OPAQUE,
}

# Sequence-entry facts that name finalization receipts recovery resolves.
SEQUENCE_RECEIPT_FACTS = ("commit_receipt_address",
                          "observation_receipt_address",
                          "acceptance_sha256", "release_sha256")

# Other journal facts that name store content (evidence the journal cites).
JOURNAL_CONTENT_FACTS = ("content_address", "payload_content_address",
                         "content_sha256", "receipt_sha256",
                         "environment_sha256", "candidate_address",
                         "candidate_receipt_sha256",
                         "execution_receipt_address", "projection_address",
                         "witness_address", "attestation_sha256",
                         "attestation_content_address",
                         "commit_receipt_sha256")
JOURNAL_CONTENT_LIST_FACTS = ("failure_receipts", "verification_receipts")
JOURNAL_CONTENT_MAP_FACTS = ("role_transform_checks",)

# machine, table, key column, subject format, initial state
STATE_MACHINES = (
    ("sequence", "sequences", "sequence_id", "%s", "DECLARED"),
    ("attempt", "attempts", "attempt_id", "%s", "DECLARED"),
    ("permit_reservation", "reservations", "reservation_id", "%s", None),
    ("contract_admission", "admissions", "admission_id", "adm-%d", None),
    ("effect_intent", "effect_intents", "intent_id", "%s", "PENDING"),
)
# The state an entry's from_state must name when it is the subject's first
# entry (None: the first entry carries no from_state).
REPLAY_INITIAL = {"sequence": "DECLARED", "attempt": "DECLARED",
                  "permit_reservation": None,
                  "contract_admission": "RECEIVED",
                  "effect_intent": "PENDING"}

# machine, table, key column, journaled state, {fact: column} required,
# {fact: column} compared only when the fact is present
ADMISSION_MACHINES = (
    ("attempt_witness", "attempt_witnesses", "admission_id", "ADMITTED",
     {"attempt_id": "attempt_id", "witness_kind": "witness_kind",
      "producer": "producer", "content_sha256": "content_sha256",
      "call_id": "call_id"},
     {"native_session_id": "native_session_id"}),
    ("attempt_artifact", "attempt_artifacts", "admission_id", "ADMITTED",
     {"attempt_id": "attempt_id", "sequence_id": "sequence_id",
      "call_id": "call_id", "role": "role",
      "logical_path": "logical_path", "content_sha256": "content_sha256"},
     {}),
    ("attempt_receipt", "attempt_receipts", "admission_id", "ADMITTED",
     {"attempt_id": "attempt_id", "content_sha256": "content_sha256",
      "completeness": "completeness",
      "evidence_complete_by_utc": "evidence_complete_by_utc",
      "minter": "minter", "call_id": "call_id"}, {}),
    ("attempt_candidate", "attempt_candidates", "admission_id", "ADMITTED",
     {"attempt_id": "attempt_id", "content_sha256": "content_sha256",
      "finalization_class": "finalization_class"},
     {"candidate_tree": "candidate_tree"}),
    ("attempt_environment", "attempt_environments", "admission_id",
     "ADMITTED",
     {"attempt_id": "attempt_id", "content_sha256": "content_sha256"}, {}),
    ("attempt_attestation", "attempt_attestations", "admission_id",
     "ADMITTED",
     {"attempt_id": "attempt_id", "assertion_id": "assertion_id",
      "content_sha256": "content_sha256"}, {}),
    ("attempt_verification", "attempt_verifications", "verification_id",
     "RECORDED",
     {"attempt_id": "attempt_id", "outcome": "outcome",
      "by": "by_principal", "receipt_sha256": "receipt_sha256"}, {}),
)

RESULT_KEY = "autobot_verifier_result"


class AuditUnavailable(Exception):
    """The directory cannot be audited at all. Never reported as clean."""


# ------------------------------------------------------------- plumbing

def _finding(ctx, check, subject, detail):
    ctx["findings"].append({"check": check, "subject": "%s" % (subject,),
                            "detail": detail})


def _report(ctx, kind, entry):
    ctx["reported"].setdefault(kind, []).append(entry)


def _rows(ctx, table, order=None):
    """Every row of one table as plain dicts (an absent table is empty)."""
    if table not in ctx["tables"]:
        return []
    sql = "SELECT * FROM %s" % table
    if order:
        sql += " ORDER BY %s" % order
    cursor = ctx["conn"].execute(sql)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _open_read_only(db_path):
    """A FRESH read-only connection: this module can never create, migrate
    or mutate the ledger it audits. Returns (connection, read mode).

    A QUIESCENT ledger (no ``-wal``/``-shm`` sidecar: every writer closed
    and checkpointed) is opened ``immutable=1`` - SQLite then takes no lock
    and creates no sidecar, so an audit leaves the directory byte-for-byte
    as it found it. When a sidecar exists a writer is attached (or died
    attached) and the WAL may hold committed entries the main file lacks:
    the connection is then plain ``mode=ro`` and reads THROUGH the WAL, so
    nothing committed is missed; SQLite's own reader bookkeeping may touch
    the transient ``-shm`` index, never the ledger file or the WAL."""
    quiescent = not (os.path.exists(db_path + "-wal")
                     or os.path.exists(db_path + "-shm"))
    path = db_path.replace("\\", "/")
    for raw, quoted in (("%", "%25"), ("?", "%3f"), ("#", "%23")):
        path = path.replace(raw, quoted)
    if not path.startswith("/"):
        path = "/" + path
    query = "?mode=ro&immutable=1" if quiescent else "?mode=ro"
    conn = sqlite3.connect("file://" + path + query, uri=True, timeout=10.0)
    conn.execute("PRAGMA query_only = ON")
    return conn, ("immutable" if quiescent else "wal-read-only")


def _hash_file(path):
    """This module's own SHA-256 file walk: (D-30 digest text, bytes)."""
    hasher = hashlib.sha256()
    chunks = []
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 16)
            if not chunk:
                break
            hasher.update(chunk)
            chunks.append(chunk)
    return "sha256:" + hasher.hexdigest(), b"".join(chunks)


def read_object(ctx, address):
    """(status, bytes) for one content address: status is ``ok``,
    ``missing``, ``altered`` or ``malformed``. Cached per audit."""
    if not preimage2.is_digest_text(address):
        return ("malformed", None)
    cache = ctx["objects"]
    if address in cache:
        return cache[address]
    hexpart = address[7:]
    path = os.path.join(ctx["store_dir"], "objects", hexpart[:2], hexpart)
    if not os.path.isfile(path):
        result = ("missing", None)
    else:
        digest, data = _hash_file(path)
        result = ("ok", data) if digest == address else ("altered", None)
    cache[address] = result
    return result


def _require_object(ctx, check, subject, address):
    """Resolve a REQUIRED content reference; a failure is a finding."""
    status, data = read_object(ctx, address)
    if status != "ok":
        _finding(ctx, "content.%s" % status, subject,
                 "%s names %r, which is %s in the store"
                 % (check, address, status))
        return None
    ctx["required"].add(address)
    return data


def _json_object(ctx, check, subject, address, canonical):
    """A required store object parsed as one JSON object with this
    module's strict reader; optionally proven to be its own canonical
    form. Returns the dict, or None after recording the finding."""
    data = _require_object(ctx, check, subject, address)
    if data is None:
        return None
    try:
        payload = preimage2.strict_loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _finding(ctx, "%s.unreadable" % check, subject,
                 "bytes at %s are not strict UTF-8 JSON" % address)
        return None
    if not isinstance(payload, dict):
        _finding(ctx, "%s.unreadable" % check, subject,
                 "bytes at %s are not a JSON object" % address)
        return None
    if canonical:
        try:
            own = preimage2.canonical_bytes(payload)
        except preimage2.Preimage2Error:
            own = None
        if own != data:
            _finding(ctx, "%s.not_canonical" % check, subject,
                     "bytes at %s are not their own canonical form"
                     % address)
            return None
    return payload


def _tolerant_json(data):
    """Producer bytes parsed tolerantly (D-38): a dict or None."""
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ---------------------------------------------------------------- store

def audit_store_layout(ctx):
    """Walk the whole object tree once: every file sits at
    objects/<2 hex>/<64 hex> and re-hashes to its own name."""
    root = os.path.join(ctx["store_dir"], "objects")
    count = 0
    for directory in sorted(os.listdir(root)):
        subdir = os.path.join(root, directory)
        if not os.path.isdir(subdir):
            _report(ctx, "store_foreign_entries", directory)
            continue
        for name in sorted(os.listdir(subdir)):
            address = "sha256:" + name
            if (not preimage2.is_digest_text(address)
                    or name[:2] != directory):
                _report(ctx, "store_foreign_entries",
                        "%s/%s" % (directory, name))
                continue
            count += 1
            status, _data = read_object(ctx, address)
            if status != "ok":
                ctx["store_bad"].add(address)
    ctx["witness"]["store_objects"] = count


# -------------------------------------------------------------- journal

def audit_journal(ctx):
    rows = _rows(ctx, "transition_journal", order="seq")
    previous = None
    entries = []
    for row in rows:
        subject = "seq %d" % row["seq"]
        if row["previous_hash"] != previous:
            _finding(ctx, "journal.chain_link", subject,
                     "previous_hash %r is not the prior entry_hash %r"
                     % (row["previous_hash"], previous))
        text = row["payload"]
        claimed = row["previous_hash"]
        if not isinstance(text, str) or (
                claimed is not None
                and not preimage2.is_digest_text(claimed)):
            _finding(ctx, "journal.entry_hash", subject,
                     "the row is not hashable under the journal rule")
            previous = row["entry_hash"]
            continue
        # mechanism 1: the byte splice over the stored canonical text
        spliced = b'{"payload":' + text.encode("utf-8")
        if claimed is not None:
            spliced += b',"previous":"' + claimed.encode("ascii") + b'"'
        spliced += b"}"
        digest = "sha256:" + hashlib.sha256(
            JOURNAL_PREFIX + spliced).hexdigest()
        hash_ok = digest == row["entry_hash"]
        if not hash_ok:
            _finding(ctx, "journal.entry_hash", subject,
                     "entry_hash %r does not re-hash from the stored"
                     " payload (expected %s)" % (row["entry_hash"], digest))
        # mechanism 2: the stored text is its own canonical form
        payload = None
        try:
            payload = preimage2.strict_loads(text)
            canonical = preimage2.canonical_text(payload)
        except ValueError:
            canonical = None
        if canonical != text:
            _finding(ctx, "journal.payload_not_canonical", subject,
                     "the stored payload text is not canonical JSON")
            payload = None
        elif hash_ok and preimage2.journal_entry_hash2(
                payload, claimed) != row["entry_hash"]:
            # mechanism 3: parse, re-canonicalize, re-hash
            _finding(ctx, "journal.entry_hash", subject,
                     "the parsed record does not re-hash to entry_hash")
        if payload is not None:
            if not _well_formed_record(payload):
                _finding(ctx, "journal.record_shape", subject,
                         "the payload is not a v1 TransitionRecord")
            else:
                if row["appended_utc"] != payload["recorded_utc"]:
                    _finding(ctx, "journal.record_shape", subject,
                             "appended_utc is not the record's recorded_utc")
                entries.append(payload)
        previous = row["entry_hash"]
    ctx["entries"] = entries
    ctx["witness"]["journal_entries"] = len(rows)
    ctx["witness"]["journal_head"] = previous
    last = {}
    for payload in entries:
        last[(payload["machine"], payload["subject_id"])] = payload
    ctx["last"] = last


def _well_formed_record(payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("kind") != "TransitionRecord" \
            or payload.get("schema_version") != "v1":
        return False
    for member in ("machine", "subject_id", "rule", "to_state",
                   "recorded_utc"):
        if not isinstance(payload.get(member), str):
            return False
    facts = payload.get("facts", {})
    return isinstance(facts, dict)


# -------------------------------------------------------------- pairing

def audit_pairing(ctx):
    last = ctx["last"]
    for machine, table, key, fmt, initial in STATE_MACHINES:
        subjects = set()
        for row in _rows(ctx, table):
            subject = fmt % row[key]
            subjects.add(subject)
            entry = last.get((machine, subject))
            journaled = entry["to_state"] if entry else initial
            if journaled != row["state"]:
                _finding(ctx, "pairing.%s" % machine, subject,
                         "journal says %r, row says %r"
                         % (journaled, row["state"]))
            if machine in ("sequence", "attempt"):
                _audit_quadruple(ctx, machine, subject, row, entry)
        for (entry_machine, subject) in sorted(last):
            if entry_machine == machine and subject not in subjects:
                _finding(ctx, "pairing.%s" % machine, subject,
                         "the journal names this subject but its row is"
                         " gone")
    _audit_replay(ctx)
    _audit_admission_rows(ctx)
    _audit_registry_objects(ctx)
    _audit_lease(ctx)
    _audit_permits(ctx)
    _audit_repairs(ctx)
    _audit_current_attempts(ctx)
    _audit_released_edges(ctx)


def _audit_released_edges(ctx):
    """released_edges rows pair with their journaled t-s41 release (both
    directions) and carry the predecessor's own identity tiers."""
    journaled = {}
    for payload in ctx["entries"]:
        if payload["machine"] == "sequence" and payload["rule"] == "t-s41":
            facts = payload.get("facts", {})
            for release_id in facts.get("release_ids", []):
                journaled[release_id] = (payload["subject_id"], facts)
    sequences = dict((row["sequence_id"], row)
                     for row in _rows(ctx, "sequences"))
    tier_column = {"RECORD": "record_hash", "CONTRACT": "contract_digest",
                   "REQUIREMENT": "requirement_digest"}
    seen = set()
    for row in _rows(ctx, "released_edges"):
        subject = row["release_id"]
        seen.add(subject)
        entry = journaled.get(subject)
        if entry is None or entry[0] != row["predecessor_sequence_id"]:
            _finding(ctx, "pairing.released_edge", subject,
                     "the row has no journaled t-s41 release")
            continue
        facts = entry[1]
        predecessor = sequences.get(row["predecessor_sequence_id"], {})
        if (facts.get("acceptance_sha256") != row["acceptance_sha256"]
                or facts.get("release_sha256") != row["release_sha256"]):
            _finding(ctx, "pairing.released_edge", subject,
                     "the row's receipt references are not the journaled"
                     " ones")
        if predecessor.get(tier_column.get(row["edge_kind"])) != \
                row["edge_value"]:
            _finding(ctx, "pairing.released_edge", subject,
                     "edge_value is not the predecessor's %s tier"
                     % row["edge_kind"])
        if predecessor.get("state") not in ("DEPENDENCIES_RELEASED",
                                            "COMPLETED"):
            _finding(ctx, "pairing.released_edge", subject,
                     "the predecessor is %r, not durably released"
                     % (predecessor.get("state"),))
    for release_id in sorted(journaled):
        if release_id not in seen:
            _finding(ctx, "pairing.released_edge", release_id,
                     "the journal names this release but its row is gone")


def _audit_quadruple(ctx, machine, subject, row, entry):
    """The version and fence the last entry journaled are the row's."""
    if entry is None:
        expected_version = 1
        expected_fence = 0 if machine == "sequence" else row["fence"]
    else:
        facts = entry.get("facts", {})
        expected_version = facts.get("version")
        expected_fence = facts.get("fence")
    if row["version"] != expected_version:
        _finding(ctx, "pairing.%s_version" % machine, subject,
                 "journal says version %r, row says %r"
                 % (expected_version, row["version"]))
    if row["fence"] != expected_fence:
        _finding(ctx, "pairing.%s_fence" % machine, subject,
                 "journal says fence %r, row says %r"
                 % (expected_fence, row["fence"]))


def _audit_replay(ctx):
    """Replay the chain per subject: from_state continuity."""
    current = {}
    for payload in ctx["entries"]:
        machine = payload["machine"]
        if machine not in REPLAY_INITIAL:
            continue
        key = (machine, payload["subject_id"])
        if "from_state" in payload:
            before = current.get(key, REPLAY_INITIAL[machine])
            if payload["from_state"] != before:
                _finding(ctx, "journal.state_continuity",
                         payload["subject_id"],
                         "%s %s claims from_state %r but the subject was"
                         " %r" % (machine, payload["rule"],
                                  payload["from_state"], before))
        current[key] = payload["to_state"]


def _audit_admission_rows(ctx):
    last = ctx["last"]
    for machine, table, key, state, required, optional in \
            ADMISSION_MACHINES:
        subjects = set()
        for row in _rows(ctx, table):
            subject = row[key]
            subjects.add(subject)
            entry = last.get((machine, subject))
            if entry is None or entry["to_state"] != state:
                _finding(ctx, "pairing.%s" % machine, subject,
                         "the row has no journaled %s admission" % state)
                continue
            facts = entry.get("facts", {})
            for fact, column in sorted(required.items()):
                if facts.get(fact) != row[column]:
                    _finding(ctx, "pairing.%s" % machine, subject,
                             "journaled %s %r is not the row's %s %r"
                             % (fact, facts.get(fact), column, row[column]))
            for fact, column in sorted(optional.items()):
                if facts.get(fact) != row[column]:
                    _finding(ctx, "pairing.%s" % machine, subject,
                             "journaled %s %r is not the row's %s %r"
                             % (fact, facts.get(fact), column, row[column]))
            if machine == "attempt_verification":
                if "assertion_id" in facts:
                    row_key = facts["assertion_id"]
                else:
                    row_key = "role-transform/%s" % (facts.get("role"),)
                if row_key != row["assertion_id"]:
                    _finding(ctx, "pairing.%s" % machine, subject,
                             "journaled row key %r is not the row's %r"
                             % (row_key, row["assertion_id"]))
                if facts.get("environment_sha256") != \
                        row["environment_sha256"]:
                    _finding(ctx, "pairing.%s" % machine, subject,
                             "journaled environment %r is not the row's %r"
                             % (facts.get("environment_sha256"),
                                row["environment_sha256"]))
        for (entry_machine, subject) in sorted(last):
            if entry_machine == machine and subject not in subjects:
                _finding(ctx, "pairing.%s" % machine, subject,
                         "the journal names this admission but its row is"
                         " gone")


def _audit_registry_objects(ctx):
    """objects rows: a registry admission is journaled per object; an
    AttemptContract is journaled by its t-s06; a record by its t-c02."""
    last = ctx["last"]
    contracts = set()
    records = set()
    for payload in ctx["entries"]:
        facts = payload.get("facts", {})
        if payload["machine"] == "sequence" and payload["rule"] == "t-s06":
            contracts.add(facts.get("attempt_contract_hash"))
        if payload["machine"] == "contract_admission" \
                and payload["rule"] == "t-c02":
            records.add(facts.get("record_hash"))
    hashes = set()
    for row in _rows(ctx, "objects"):
        subject = row["object_hash"]
        hashes.add(subject)
        if row["kind"] == "AttemptContract":
            if subject not in contracts:
                _finding(ctx, "pairing.registry_objects", subject,
                         "an AttemptContract no t-s06 entry minted")
        elif row["kind"] == "TaskContractRecord":
            if subject not in records:
                _finding(ctx, "pairing.registry_objects", subject,
                         "a record object no t-c02 entry froze")
        else:
            entry = last.get(("registry_objects", subject))
            if entry is None or entry["to_state"] != "ADMITTED":
                _finding(ctx, "pairing.registry_objects", subject,
                         "the row has no journaled registry admission")
                continue
            facts = entry.get("facts", {})
            if facts.get("kind") != row["kind"] or \
                    facts.get("content_address") != row["content_address"]:
                _finding(ctx, "pairing.registry_objects", subject,
                         "journaled kind/content_address disagree with the"
                         " row")
    for (machine, subject) in sorted(last):
        if machine == "registry_objects" and subject not in hashes:
            _finding(ctx, "pairing.registry_objects", subject,
                     "the journal names this object but its row is gone")


def _audit_lease(ctx):
    rows = _rows(ctx, "instance_lease")
    entry = ctx["last"].get(("instance_lease", "sequencer"))
    if bool(rows) != (entry is not None):
        _finding(ctx, "pairing.instance_lease", "sequencer",
                 "the lease row and the journal disagree on existence")
        return
    for row in rows:
        facts = entry.get("facts", {})
        if facts.get("holder") != row["holder"] or \
                facts.get("epoch") != row["epoch"]:
            _finding(ctx, "pairing.instance_lease", "sequencer",
                     "row (%r epoch %r) is not the journaled (%r epoch %r)"
                     % (row["holder"], row["epoch"], facts.get("holder"),
                        facts.get("epoch")))


def _audit_permits(ctx):
    counts = {}
    for row in _rows(ctx, "reservations"):
        key = (row["sequence_id"], row["state"])
        counts[key] = counts.get(key, 0) + 1
    for row in _rows(ctx, "permit_state"):
        reserved = counts.get((row["sequence_id"], "RESERVED"), 0)
        consumed = counts.get((row["sequence_id"], "CONSUMED"), 0)
        if row["reserved_uses"] != reserved or \
                row["consumed_uses"] != consumed:
            _finding(ctx, "permits.counters", row["sequence_id"],
                     "counters (reserved %r, consumed %r) are not the"
                     " reservation rows (%d, %d)"
                     % (row["reserved_uses"], row["consumed_uses"],
                        reserved, consumed))
        if row["consumed_uses"] + row["reserved_uses"] > \
                row["permitted_uses"]:
            _finding(ctx, "permits.counters", row["sequence_id"],
                     "consumed + reserved exceeds permitted (INV-48)")


def _audit_repairs(ctx):
    repairs = {}
    for payload in ctx["entries"]:
        if payload["machine"] == "sequence" and payload["rule"] == "t-s11":
            subject = payload["subject_id"]
            repairs[subject] = repairs.get(subject, 0) + 1
    for row in _rows(ctx, "sequences"):
        if row.get("repairs_used", 0) != repairs.get(row["sequence_id"], 0):
            _finding(ctx, "pairing.sequence_repairs", row["sequence_id"],
                     "repairs_used %r is not the journaled t-s11 count %d"
                     % (row.get("repairs_used"),
                        repairs.get(row["sequence_id"], 0)))


def _audit_current_attempts(ctx):
    attempts = dict((row["attempt_id"], row)
                    for row in _rows(ctx, "attempts"))
    for row in _rows(ctx, "sequences"):
        current = row["current_attempt_id"]
        if current is None:
            continue
        attempt = attempts.get(current)
        if attempt is None or attempt["sequence_id"] != row["sequence_id"]:
            _finding(ctx, "pairing.sequence_current_attempt",
                     row["sequence_id"],
                     "current_attempt_id %r is not an attempt of this"
                     " sequence" % (current,))
        elif attempt["fence"] != row["fence"]:
            _finding(ctx, "pairing.sequence_current_attempt",
                     row["sequence_id"],
                     "sequence fence %r is not its current attempt's %r"
                     % (row["fence"], attempt["fence"]))


# --------------------------------------------------- content references

def audit_content_references(ctx):
    """Every content address every ledger column holds, enumerated from
    the LIVE schema, resolved and re-hashed."""
    conn = ctx["conn"]
    for table in sorted(ctx["tables"]):
        columns = [row[1] for row in conn.execute(
            "PRAGMA table_info(%s)" % table).fetchall()]
        for column in columns:
            role = COLUMN_ROLES.get((table, column))
            values = [row[0] for row in conn.execute(
                "SELECT DISTINCT %s FROM %s" % (column, table)).fetchall()]
            digests = [value for value in values
                       if preimage2.is_digest_text(value)]
            if role is None:
                if digests:
                    _finding(ctx, "ledger.unclassified_digest_column",
                             "%s.%s" % (table, column),
                             "the column holds digest text but this"
                             " auditor has no role for it")
                continue
            if role != CONTENT:
                continue
            for value in sorted(v for v in values if v is not None):
                if not preimage2.is_digest_text(value):
                    _finding(ctx, "content.malformed",
                             "%s.%s" % (table, column),
                             "%r is not a D-30 content address" % (value,))
                    continue
                _require_object(ctx, "%s.%s" % (table, column),
                                "%s.%s" % (table, column), value)
    # journaled finalization receipts (obj-26, obj-30, obj-35)
    for payload in ctx["entries"]:
        facts = payload.get("facts", {})
        if payload["machine"] == "sequence":
            for fact in SEQUENCE_RECEIPT_FACTS:
                if fact in facts:
                    _require_object(ctx, "journal.%s" % fact,
                                    payload["subject_id"], facts[fact])
        for address in _journal_named(facts):
            ctx["journal_named"].add(address)
    for address in sorted(ctx["journal_named"] - ctx["required"]):
        status, _data = read_object(ctx, address)
        if status != "ok":
            _report(ctx, "dangling_journal_references",
                    {"address": address, "status": status})


def _journal_named(facts):
    """Content addresses a journal entry's facts cite as evidence."""
    found = []
    for fact in JOURNAL_CONTENT_FACTS + SEQUENCE_RECEIPT_FACTS:
        value = facts.get(fact)
        if preimage2.is_digest_text(value):
            found.append(value)
    for fact in JOURNAL_CONTENT_LIST_FACTS:
        value = facts.get(fact)
        if isinstance(value, list):
            found.extend(v for v in value if preimage2.is_digest_text(v))
    for fact in JOURNAL_CONTENT_MAP_FACTS:
        value = facts.get(fact)
        if isinstance(value, dict):
            found.extend(v for v in value.values()
                         if preimage2.is_digest_text(v))
    return found


# ----------------------------------------------------------- identities

def audit_identities(ctx):
    """Every indexed identity re-derived from stored bytes (preimage2)."""
    payloads = {}
    for row in _rows(ctx, "objects"):
        subject = row["object_hash"]
        canonical = row["kind"] != "TaskContractRecord"
        payload = _json_object(ctx, "objects", subject,
                               row["content_address"], canonical)
        if payload is None:
            continue
        payloads[subject] = payload
        try:
            if row["kind"] == "TaskContractRecord":
                derived = preimage2.compute_tiers2(payload)[2]
            else:
                derived = preimage2.object_hash2(row["kind"], payload)
        except preimage2.Preimage2Error as exc:
            _finding(ctx, "identity.object_hash", subject,
                     "the stored payload has no object hash: %s" % exc)
            continue
        if derived != subject:
            _finding(ctx, "identity.object_hash", subject,
                     "stored bytes re-derive %s" % derived)
        closure = payload.get("closure")
        if row["kind"] in ("VerifierRegistration",
                           "TransformRegistration"):
            try:
                root = preimage2.artifact_root2(closure)
            except preimage2.Preimage2Error:
                root = None
            if root != payload.get("artifact_root"):
                _finding(ctx, "identity.artifact_root", subject,
                         "the closure re-derives %r, the registration"
                         " carries %r" % (root, payload.get("artifact_root")))
    ctx["object_payloads"] = payloads
    records = {}
    for row in _rows(ctx, "records"):
        subject = row["record_hash"]
        payload = _json_object(ctx, "records", subject,
                               row["content_address"], False)
        if payload is None:
            continue
        records[subject] = payload
        try:
            tiers = preimage2.compute_tiers2(payload)
        except preimage2.Preimage2Error as exc:
            _finding(ctx, "identity.record_tiers", subject,
                     "the stored record has no tiers: %s" % exc)
            continue
        if tiers != (row["requirement_digest"], row["contract_digest"],
                     row["record_hash"]):
            _finding(ctx, "identity.record_tiers", subject,
                     "stored bytes re-derive %r" % (tiers,))
        inputs = payload.get("specification_inputs", {})
        named = inputs.get("name_to_exact_byte_sha256", {}) \
            if isinstance(inputs, dict) else {}
        if isinstance(named, dict):
            for name in sorted(named):
                _require_object(ctx, "records.specification_input",
                                "%s %s" % (subject, name), named[name])
    ctx["record_payloads"] = records
    for row in _rows(ctx, "sequences"):
        record = None
        for candidate in _rows(ctx, "records"):
            if candidate["record_hash"] == row["record_hash"]:
                record = candidate
        if record is None:
            _finding(ctx, "identity.sequence_record", row["sequence_id"],
                     "the sequence names record %r, which has no row"
                     % (row["record_hash"],))
        elif (record["requirement_digest"] != row["requirement_digest"]
              or record["contract_digest"] != row["contract_digest"]):
            _finding(ctx, "identity.sequence_record", row["sequence_id"],
                     "the sequence's tier digests are not its record's")


# ------------------------------------------------------ receipt bindings

def typed_conclusion(output_bytes):
    """The registered result protocol, restated: the verifier's LAST
    non-blank stdout line is JSON ``{"autobot_verifier_result":
    {"conclusion": "PASS"|"FAIL"}}``. Anything else is no conclusion."""
    lines = [line.rstrip(b"\r") for line in output_bytes.split(b"\n")]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return None
    parsed = _tolerant_json(lines[-1])
    if parsed is None or not isinstance(parsed.get(RESULT_KEY), dict):
        return None
    conclusion = parsed[RESULT_KEY].get("conclusion")
    return conclusion if conclusion in ("PASS", "FAIL") else None


def observed_prompt_identities(ctx, attempt_id):
    """INV-49's observed side, re-derived from the admitted obj-11 bytes:
    the content identity of each provider record's persisted prompt."""
    identities = set()
    for row in _rows(ctx, "attempt_witnesses"):
        if row["attempt_id"] != attempt_id or \
                row["witness_kind"] != "obj-11":
            continue
        status, data = read_object(ctx, row["content_sha256"])
        if status != "ok":
            continue
        record = _tolerant_json(data)
        prompt = record.get("prompt") if record is not None else None
        if isinstance(prompt, str) and prompt:
            identities.add("sha256:" + hashlib.sha256(
                prompt.encode("utf-8")).hexdigest())
    return identities


def _index(rows, key):
    return dict((row[key], row) for row in rows)


def audit_receipt_bindings(ctx):
    attempts = _index(_rows(ctx, "attempts"), "attempt_id")
    sequences = _index(_rows(ctx, "sequences"), "sequence_id")
    candidates = {}
    for row in _rows(ctx, "attempt_candidates"):
        candidates.setdefault(row["attempt_id"], []).append(row)
    environments = {}
    for row in _rows(ctx, "attempt_environments"):
        environments.setdefault(row["attempt_id"], []).append(row)
    artifacts = {}
    for row in _rows(ctx, "attempt_artifacts", order="rowid"):
        artifacts.setdefault(row["attempt_id"], []).append(
            {"logical_path": row["logical_path"],
             "content_sha256": row["content_sha256"], "role": row["role"]})
    calls = _index(_rows(ctx, "attempt_calls"), "attempt_id")
    attestations = {}
    for row in _rows(ctx, "attempt_attestations"):
        attestations[(row["attempt_id"], row["assertion_id"])] = row

    def identity_ok(payload_identity, attempt):
        return (isinstance(payload_identity, dict)
                and payload_identity.get("attempt_id")
                == attempt["attempt_id"]
                and payload_identity.get("sequence_id")
                == attempt["sequence_id"]
                and payload_identity.get("fence") == attempt["fence"])

    # ---- obj-16 CandidateReceipt
    for attempt_id, rows in sorted(candidates.items()):
        if len(rows) != 1:
            _finding(ctx, "binding.obj-16", attempt_id,
                     "%d candidate rows for one attempt" % len(rows))
        for row in rows:
            subject = row["admission_id"]
            attempt = attempts.get(attempt_id)
            payload = _json_object(ctx, "binding.obj-16", subject,
                                   row["content_sha256"], True)
            if payload is None:
                continue
            if attempt is None:
                _finding(ctx, "binding.obj-16", subject,
                         "the candidate joins no attempt")
                continue
            sequence = sequences.get(attempt["sequence_id"], {})
            contract = ctx["object_payloads"].get(
                attempt["attempt_contract_hash"], {})
            tree = payload.get("candidate_tree")
            tree_value = tree.get("git_tree_sha") \
                if isinstance(tree, dict) else None
            expected_artifacts = sorted(
                artifacts.get(attempt_id, []),
                key=lambda a: (a["logical_path"], a["content_sha256"]))
            checks = (
                ("kind", payload.get("kind") == "CandidateReceipt"),
                ("minter", payload.get("minter") == "git_adapter"),
                ("attempt identity",
                 identity_ok(payload.get("attempt"), attempt)),
                ("attempt_contract_hash",
                 payload.get("attempt_contract_hash")
                 == attempt["attempt_contract_hash"]),
                ("record tiers",
                 (payload.get("record_hash"),
                  payload.get("requirement_digest"),
                  payload.get("contract_digest"))
                 == (sequence.get("record_hash"),
                     sequence.get("requirement_digest"),
                     sequence.get("contract_digest"))),
                ("finalization_class",
                 payload.get("finalization_class")
                 == row["finalization_class"]),
                ("candidate_tree", tree_value == row["candidate_tree"]),
                ("class/tree coupling",
                 (row["finalization_class"] == "SOURCE_MODIFYING")
                 == (tree_value is not None)),
                ("source_state",
                 payload.get("source_state")
                 == contract.get("source_state")),
                ("artifacts", payload.get("artifacts")
                 == expected_artifacts),
            )
            for member, ok in checks:
                if not ok:
                    _finding(ctx, "binding.obj-16", subject,
                             "%s does not bind the ledger" % member)

    # ---- obj-18 ActualVerificationEnvironment
    for attempt_id, rows in sorted(environments.items()):
        if len(rows) != 1:
            _finding(ctx, "binding.obj-18", attempt_id,
                     "%d environment rows for one attempt" % len(rows))
        for row in rows:
            subject = row["admission_id"]
            attempt = attempts.get(attempt_id)
            payload = _json_object(ctx, "binding.obj-18", subject,
                                   row["content_sha256"], False)
            if payload is None:
                continue
            if attempt is None:
                _finding(ctx, "binding.obj-18", subject,
                         "the environment joins no attempt")
                continue
            if payload.get("kind") != "ActualVerificationEnvironment":
                _finding(ctx, "binding.obj-18", subject,
                         "kind does not bind the ledger")
            if not identity_ok(payload.get("attempt"), attempt):
                _finding(ctx, "binding.obj-18", subject,
                         "attempt identity does not bind the ledger")

    # ---- obj-37 ManualAttestation.ASSERTION
    for (attempt_id, assertion_id), row in sorted(attestations.items()):
        subject = row["admission_id"]
        attempt = attempts.get(attempt_id)
        payload = _json_object(ctx, "binding.obj-37", subject,
                               row["content_sha256"], True)
        if payload is None or attempt is None:
            if attempt is None:
                _finding(ctx, "binding.obj-37", subject,
                         "the attestation joins no attempt")
            continue
        sequence = sequences.get(attempt["sequence_id"], {})
        candidate = candidates.get(attempt_id, [{}])[0]
        checks = (
            ("kind", payload.get("kind") == "ManualAttestation.ASSERTION"),
            ("attested_by", payload.get("attested_by") == "you"),
            ("assertion_id", payload.get("assertion_id") == assertion_id),
            ("attempt_id", payload.get("attempt_id") == attempt_id),
            ("attempt_contract_hash",
             payload.get("attempt_contract_hash")
             == attempt["attempt_contract_hash"]),
            ("candidate", payload.get("candidate_receipt_sha256")
             == candidate.get("content_sha256")),
            ("part", payload.get("part")
             == {"record": sequence.get("record_hash"),
                 "part_kind": "ASSERTION", "part_id": assertion_id}),
            ("attests", payload.get("attests") == "PASS"),
        )
        for member, ok in checks:
            if not ok:
                _finding(ctx, "binding.obj-37", subject,
                         "%s does not bind the ledger" % member)

    # ---- obj-19 / obj-20 verification results
    for row in _rows(ctx, "attempt_verifications"):
        subject = row["verification_id"]
        attempt = attempts.get(row["attempt_id"])
        payload = _json_object(ctx, "binding.obj-19/20", subject,
                               row["receipt_sha256"], True)
        if payload is None:
            continue
        if attempt is None:
            _finding(ctx, "binding.obj-19/20", subject,
                     "the result joins no attempt")
            continue
        sequence = sequences.get(attempt["sequence_id"], {})
        record = ctx["record_payloads"].get(sequence.get("record_hash"),
                                            {})
        candidate = candidates.get(row["attempt_id"], [{}])[0]
        environment = environments.get(row["attempt_id"], [{}])[0]
        assertion = payload.get("assertion")
        assertion = assertion if isinstance(assertion, dict) else {}
        part = assertion.get("part")
        part = part if isinstance(part, dict) else {}
        part_kind = part.get("part_kind")
        if part_kind == "ROLE_TRANSFORM":
            row_key = "role-transform/%s" % (part.get("part_id"),)
        else:
            row_key = part.get("part_id")
        expected_kind = ("VerificationReceipt"
                         if row["outcome"] in ("PASS", "ATTESTED")
                         else "VerificationFailureReceipt")
        checks = [
            ("kind", payload.get("kind") == expected_kind),
            ("minter", payload.get("minter") == "verifier_runner"),
            ("attempt identity",
             identity_ok(payload.get("attempt"), attempt)),
            ("attempt_contract_hash",
             payload.get("attempt_contract_hash")
             == attempt["attempt_contract_hash"]),
            ("record_hash", payload.get("record_hash")
             == sequence.get("record_hash")),
            ("candidate", payload.get("candidate_receipt_sha256")
             == candidate.get("content_sha256")),
            ("outcome", payload.get("outcome") == row["outcome"]),
            ("part record", part.get("record")
             == sequence.get("record_hash")),
            ("part row key", row_key == row["assertion_id"]),
        ]
        if payload.get("result_source") == "BY_ATTESTATION":
            attestation = attestations.get(
                (row["attempt_id"], row["assertion_id"]), {})
            checks.extend((
                ("attested outcome", row["outcome"] == "ATTESTED"),
                ("attester", row["by_principal"] == "you"),
                ("no environment", row["environment_sha256"] is None
                 and "environment_sha256" not in payload),
                ("no execution", "execution" not in payload
                 and "registration" not in payload),
                ("attestation", payload.get("attestation_sha256")
                 == attestation.get("content_sha256")),
                ("part kind", part_kind == "ASSERTION"),
            ))
        else:
            checks.extend(_verifier_checks(
                ctx, subject, row, payload, record, environment,
                assertion, part, calls.get(row["attempt_id"], {})))
        for member, ok in checks:
            if not ok:
                _finding(ctx, "binding.obj-19/20", subject,
                         "%s does not bind the ledger" % member)


def _verifier_checks(ctx, subject, row, payload, record, environment,
                     assertion, part, call):
    """The BY_VERIFIER members: environment, registration, execution
    evidence and (ROLE_TRANSFORM) the transform check."""
    bindings = record.get("resolved_bindings", {})
    bindings = bindings if isinstance(bindings, dict) else {}
    registration = payload.get("registration")
    registration = registration if isinstance(registration, dict) else {}
    registered = ctx["object_payloads"].get(
        registration.get("object_sha256"), {})
    execution = payload.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    exit_code = execution.get("exit_code")
    outcome = payload.get("outcome")
    basis = payload.get("conclusion_basis")
    output = None
    if "output_sha256" in execution:
        output = _require_object(ctx, "binding.obj-19/20.output", subject,
                                 execution["output_sha256"])
    checks = [
        ("result source", payload.get("result_source") == "BY_VERIFIER"),
        ("producer", row["by_principal"] == "verifier_runner"),
        ("environment", payload.get("environment_sha256")
         == environment.get("content_sha256")
         and row["environment_sha256"]
         == environment.get("content_sha256")),
        ("registration identity",
         registration.get("registration_id")
         == registered.get("registration_id")
         and registration.get("artifact_root")
         == registered.get("artifact_root")
         and registered.get("registration_id") is not None),
        ("execution evidence", output is not None),
    ]
    if part.get("part_kind") == "ROLE_TRANSFORM":
        role = part.get("part_id")
        transforms = record.get("role_transforms", {})
        transform_id = transforms.get(role) \
            if isinstance(transforms, dict) else None
        check = payload.get("transform_check")
        check = check if isinstance(check, dict) else {}
        inputs = record.get("specification_inputs", {})
        named = inputs.get("name_to_exact_byte_sha256", {}) \
            if isinstance(inputs, dict) else {}
        expected = check.get("expected_sha256")
        observed = check.get("observed_sha256")
        derived = observed_prompt_identities(ctx, row["attempt_id"])
        checks.extend((
            ("bound transform registration",
             registration.get("object_sha256")
             in bindings.get("transform_registrations", [])
             and registered.get("kind") == "TransformRegistration"
             and registered.get("transform_id") == transform_id),
            ("declared transform",
             transform_id is not None
             and assertion.get("verifier_id") == transform_id),
            ("dispatched role", call.get("role") == role),
            ("transform input",
             len(named) == 1
             and named.get(check.get("input_name"))
             == check.get("input_sha256")),
            ("observed prompt identity",
             (derived == set() and observed is None)
             or derived == set([observed])),
        ))
        output_address = execution.get("output_sha256")
        if outcome == "PASS":
            supported = (exit_code == 0 and expected is not None
                         and expected == observed
                         and output_address == expected)
        elif outcome == "FAIL":
            supported = (basis == "transform_output_mismatch"
                         and exit_code == 0 and expected is not None
                         and observed is not None and expected != observed
                         and output_address == expected)
        elif basis == "verifier_execution_failed":
            supported = (isinstance(exit_code, int) and exit_code != 0
                         and expected is None)
        elif basis == "missing_producer_evidence":
            supported = (exit_code == 0 and expected is not None
                         and output_address == expected
                         and observed is None)
        else:
            supported = False
        checks.append(("outcome supported by its evidence", supported))
    else:
        assertions = record.get("acceptance_assertions", {})
        declared = assertions.get("ids_and_environment_constraints", []) \
            if isinstance(assertions, dict) else []
        frozen = {}
        for entry in declared if isinstance(declared, list) else []:
            if isinstance(entry, dict) and \
                    entry.get("assertion_id") == part.get("part_id"):
                frozen = entry
        conclusion = None
        if output is not None and exit_code == 0:
            conclusion = typed_conclusion(output)
        if outcome in ("PASS", "FAIL"):
            supported = conclusion == outcome
        elif basis == "verifier_execution_failed":
            supported = isinstance(exit_code, int) and exit_code != 0
        elif basis == "malformed_verifier_result":
            supported = exit_code == 0 and conclusion is None
        else:
            supported = False
        checks.extend((
            ("part kind", part.get("part_kind") == "ASSERTION"),
            ("bound verifier registration",
             registration.get("object_sha256")
             in bindings.get("verifier_registrations", [])
             and registered.get("kind") == "VerifierRegistration"
             and registered.get("verifier_id")
             == frozen.get("verifier_id")),
            ("declared verifier",
             frozen.get("verifier_id") is not None
             and assertion.get("verifier_id") == frozen.get("verifier_id")
             and frozen.get("mode") == "DETERMINISTIC"),
            ("outcome supported by its evidence", supported),
        ))
    return checks


def audit_execution_receipts(ctx):
    """obj-15 rows against their admitted payload and the bound call."""
    attempts = _index(_rows(ctx, "attempts"), "attempt_id")
    calls = _index(_rows(ctx, "attempt_calls"), "attempt_id")
    for row in _rows(ctx, "attempt_receipts"):
        subject = row["admission_id"]
        payload = _json_object(ctx, "binding.obj-15", subject,
                               row["content_sha256"], True)
        if payload is None:
            continue
        attempt = attempts.get(row["attempt_id"], {})
        call = calls.get(row["attempt_id"], {})
        checks = (
            ("kind", payload.get("kind") == "ExecutionReceipt"),
            ("attempt_id", payload.get("attempt_id") == row["attempt_id"]),
            ("attempt_contract_hash",
             payload.get("attempt_contract_hash")
             == attempt.get("attempt_contract_hash")),
            ("completeness", payload.get("completeness")
             == row["completeness"]),
            ("evidence_complete_by_utc",
             payload.get("evidence_complete_by_utc")
             == row["evidence_complete_by_utc"]),
            ("minter", payload.get("minter") == row["minter"]
             == "collector"),
            ("call_id", payload.get("call_id") == row["call_id"]
             == call.get("call_id")),
        )
        for member, ok in checks:
            if not ok:
                _finding(ctx, "binding.obj-15", subject,
                         "%s does not bind the ledger" % member)


# ------------------------------------------------------------ retention

WINDOWS_DEVICE_NAMES = frozenset(
    ("con", "prn", "aux", "nul", "conin$", "conout$")
    + tuple("com%d" % number for number in range(1, 10))
    + tuple("lpt%d" % number for number in range(1, 10)))
RUNNER_OWNED_NAMES = frozenset(
    ("input.bin", "output.bin", "context.json", "subject"))


def _portable_segment(segment):
    """One closure-path segment, restated from the rule (F-TI-6)."""
    if segment in ("", ".", "..") or segment != segment.strip():
        return False
    if unicodedata.normalize("NFC", segment) != segment:
        return False
    for character in segment:
        if ord(character) < 0x20 or character == "\x7f" \
                or character in '\\<>:"|?*':
            return False
    if segment[-1] == ".":
        return False
    for after_tilde in segment.split("~")[1:]:
        if after_tilde[:1] and after_tilde[:1] in "0123456789":
            return False  # the 8.3 short-alias form NAME~1.EXT
    base = segment.partition(".")[0].rstrip(" ")
    return base.casefold() not in WINDOWS_DEVICE_NAMES


def _safe_relative_path(path):
    return isinstance(path, str) and bool(path) and all(
        _portable_segment(segment) for segment in path.split("/"))


def package_entry_addresses(data):
    """The entry addresses of a valid PackageManifest v1, else None."""
    manifest = _tolerant_json(data)
    if manifest is None or manifest.get("kind") != "PackageManifest" \
            or manifest.get("schema_version") != "v1":
        return None
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        return None
    by_folded_path = {}
    addresses = []
    for entry in entries:
        if not isinstance(entry, dict) \
                or not _safe_relative_path(entry.get("path")) \
                or not isinstance(entry.get("content_sha256"), str):
            return None
        folded = entry["path"].casefold()
        if folded.split("/")[0] in RUNNER_OWNED_NAMES \
                or folded in by_folded_path:
            return None  # runner-owned, repeated, or a case-fold twin
        by_folded_path[folded] = entry["path"]
        addresses.append(entry["content_sha256"])
    for folded in by_folded_path:
        segments = folded.split("/")
        for depth in range(1, len(segments)):
            if "/".join(segments[:depth]) in by_folded_path:
                return None  # both a file and a directory
    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str) \
            or entrypoint not in by_folded_path.values():
        return None
    interpreter = manifest.get("interpreter_id")
    if interpreter is not None and not isinstance(interpreter, str):
        return None
    return addresses


def closure_leaves(ctx, closure):
    kind = closure.get("kind") if isinstance(closure, dict) else None
    if kind == "python":
        return [closure.get("script_sha256"),
                closure.get("dependency_lock_sha256")]
    if kind == "native":
        return [closure.get("executable_sha256")]
    if kind == "packaged" and "image_digest" not in closure:
        manifest_address = closure.get("manifest_sha256")
        leaves = [manifest_address]
        status, data = read_object(ctx, manifest_address)
        if status == "ok":
            entries = package_entry_addresses(data)
            if entries is not None:
                leaves.extend(entries)
        return leaves
    return []


def audit_retention(ctx):
    """The live retention map, by this module's own walk."""
    registrations = {}
    for row in _rows(ctx, "objects"):
        if row["kind"] in ("VerifierRegistration", "TransformRegistration"):
            registrations[row["object_hash"]] = row["content_address"]
    records = _index(_rows(ctx, "records"), "record_hash")
    roots = {}
    for row in _rows(ctx, "sequences"):
        if row["state"] in SEQUENCE_TERMINAL:
            continue
        record_row = records.get(row["record_hash"])
        if record_row is None:
            continue
        status, data = read_object(ctx, record_row["content_address"])
        record = _tolerant_json(data) if status == "ok" else None
        if record is None:
            continue
        bindings = record.get("resolved_bindings", {})
        bindings = bindings if isinstance(bindings, dict) else {}
        bound = (list(bindings.get("verifier_registrations", []))
                 + list(bindings.get("transform_registrations", [])))
        for registration_hash in bound:
            address = registrations.get(registration_hash)
            status, data = read_object(ctx, address)
            registration = _tolerant_json(data) if status == "ok" else None
            if registration is None:
                _report(ctx, "missing_bound_leaves",
                        {"sequence_id": row["sequence_id"],
                         "registration": registration_hash,
                         "leaf": registration_hash})
                continue
            for leaf in closure_leaves(ctx, registration.get("closure")):
                if not isinstance(leaf, str):
                    continue
                roots.setdefault(leaf, set()).add(row["sequence_id"])
                if read_object(ctx, leaf)[0] == "missing":
                    _report(ctx, "missing_bound_leaves",
                            {"sequence_id": row["sequence_id"],
                             "registration": registration_hash,
                             "leaf": leaf})
    ctx["retention_roots"] = dict(
        (leaf, sorted(ids)) for leaf, ids in roots.items())


# ----------------------------------------------------------------- audit

def audit(data_dir):
    """Audit one runtime data directory; returns the report dict. Raises
    AuditUnavailable when there is no ledger to read - an unreadable
    directory is never a clean one."""
    data_dir = os.path.realpath(str(data_dir))
    db_path = os.path.join(data_dir, "autobot.db")
    store_dir = os.path.join(data_dir, "store")
    if not os.path.isfile(db_path):
        raise AuditUnavailable("no autobot.db in %s" % data_dir)
    if not os.path.isdir(os.path.join(store_dir, "objects")):
        raise AuditUnavailable("no content store in %s" % data_dir)
    try:
        conn, read_mode = _open_read_only(db_path)
        tables = set(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"))
    except sqlite3.Error as exc:
        raise AuditUnavailable("autobot.db is unreadable: %s" % exc)
    for required in ("meta", "transition_journal", "objects", "records",
                     "sequences", "attempts"):
        if required not in tables:
            conn.close()
            raise AuditUnavailable(
                "autobot.db has no %s table: not an Autobot ledger"
                % required)
    ctx = {"conn": conn, "tables": tables, "store_dir": store_dir,
           "findings": [], "reported": {}, "objects": {},
           "store_bad": set(), "required": set(), "journal_named": set(),
           "entries": [], "last": {}, "object_payloads": {},
           "record_payloads": {}, "retention_roots": {},
           "witness": {"data_dir": data_dir, "read_mode": read_mode,
                       "db_bytes": os.path.getsize(db_path)}}
    try:
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        ctx["witness"]["schema_version"] = version[0] if version else None
        ctx["witness"]["rows"] = dict(
            (table, conn.execute(
                "SELECT COUNT(*) FROM %s" % table).fetchone()[0])
            for table in sorted(tables))
        audit_store_layout(ctx)
        audit_journal(ctx)
        audit_pairing(ctx)
        audit_content_references(ctx)
        audit_identities(ctx)
        audit_receipt_bindings(ctx)
        audit_execution_receipts(ctx)
        audit_retention(ctx)
    finally:
        conn.close()
    referenced = ctx["required"] | ctx["journal_named"]
    for leaves in (ctx["retention_roots"],):
        referenced |= set(leaves)
    unreferenced = sorted(
        address for address, (status, _data) in ctx["objects"].items()
        if status == "ok" and address not in referenced)
    findings = sorted(ctx["findings"],
                      key=lambda f: (f["check"], f["subject"], f["detail"]))
    unique = []
    for finding in findings:
        if not unique or unique[-1] != finding:
            unique.append(finding)
    reported = dict(
        (kind, sorted(entries, key=lambda e: json.dumps(e, sort_keys=True)))
        for kind, entries in ctx["reported"].items())
    reported["unreferenced_objects"] = unreferenced
    return {
        "schema": REPORT_SCHEMA,
        "witness": ctx["witness"],
        "findings": unique,
        "reported": reported,
        "required_references": sorted(ctx["required"]),
        "journal_named_references": sorted(ctx["journal_named"]),
        "retention_roots": ctx["retention_roots"],
    }


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: python -I auditor2.py <data dir>\n")
        return 2
    try:
        report = audit(argv[1])
    except AuditUnavailable as exc:
        sys.stderr.write("auditor2: %s\n" % exc)
        return 2
    report["loaded_modules"] = sorted(
        set(name.split(".")[0] for name in sys.modules))
    json.dump(report, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
