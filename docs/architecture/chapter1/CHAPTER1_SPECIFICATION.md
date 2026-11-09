# Chapter 1 — Contract Surface: the unified specification (Phase B)

Status: RATIFIED — the operator ratified the 41 Chapter 1 decisions on
2026-11-08 after the RV-54..RV-60 review pass ("Ratify and freeze"); the
other 2 of the 43 decisions, D-49 and D-50, are the already-accepted
Chapter 0 corrections CORR-55/CORR-56. Every machine-readable registry,
schema, golden vector and conformance suite in this tree is finished,
self-consistent and executable. Baseline: merged main
`5fa870d8444d20d16321be9cad78e8fadca9476e`; handoff chain ch00-phase-c →
ch01-phase-a → ch00-amendment-01 → ch01-phase-b → ch01 (unified).

The registries are canonical; this prose explains them and is checked against
them (`harness/check_chapter1.py`, run by `harness/run_all.py`). Counts this
document commits to: 43 decisions (D-19..D-61), 76 invariants traced, 42
object types covered, 19 vectors + 2 predicates + 1 journal test resolved,
19 semantic rules, 10 complete v1 schemas across 6 schema files.

## 1. Scope (D-19)

Chapter 1 fixes representation and identity: canonicalization, identifier
grammars, hash constructions, the v1 schemas of the ten required objects
(obj-00, obj-02, obj-03, obj-04 with embedded obj-05/06/07/17, obj-18,
obj-32), the enum register, and the validation rules the Registry's freeze
needs. Every other object is classified and bound to an owning runtime
chapter in `registries/representation.json`; those chapters add fields under
Chapter 1's rules and may not change the rules.

## 2. Canonicalization (D-09 as implemented; D-24, D-25, D-37, D-46)

Canonical bytes are RFC 8785 (JCS) over a restricted value domain:

- Objects, arrays, strings, booleans, and integers in [0, 2^53-1]. Fractional
  and negative numbers are forbidden, so JCS number serialization reduces to
  exact decimal digits and every implementation agrees byte-for-byte (D-24).
- null never appears; an absent value is an absent key (D-37).
- Object keys sort by UTF-16 code units (RFC 8785 §3.2.3); the same collation
  orders every set field (D-25). Sets are sorted unique arrays; a payload out
  of order or with duplicate keys is rejected, never re-sorted.
- Strings must be NFC-normalized valid Unicode without NUL; the validator
  rejects non-NFC input rather than normalizing (D-46). Escaping is JCS's
  (shorthand escapes, `\u00xx` lowercase for other controls, literal UTF-8
  otherwise).

Reference implementation: `contract_library/canonical.py`; independent
implementation: `harness/jcs2.py`; both must agree (D-58).

## 3. Identifiers (D-22, D-23, D-40)

Minted ids: `<prefix>_<32 lowercase hex>` (128 random bits) with prefixes
tc, sq, at, rv, in, rg, dg, da, ve, lg — property sheets in
`registries/identifiers.json`. Name ids (verifier_id, transform_id,
repository_id, assertion_id, edge_id, roles, input names):
`^[a-z0-9][a-z0-9._-]{0,63}$`. PrincipalRef is a registered principals.json
id: `^[a-z][a-z0-9_]{0,63}$` (D-40). repository_id is the operator-declared
slug in the RepositoryAuthorityDeclaration; clones, remotes and URL spellings
are resolution context, outside every tier (D-23). Equality is exact bytes
everywhere; ids are immutable and never reused.

## 4. Hash constructions (D-29..D-33)

Domain framing: every semantic identity is
`SHA-256( domain || canonical_bytes )` where domain is the ASCII bytes
`autobot/<category>/<name>/v1` plus one LF; domains contain no LF, so the
framing is prefix-free (two domains can never read the same input).

- `autobot/tier/requirement/v1` — RequirementDigest over the requirement
  preimage: the record's shape restricted to exactly the nine requirement
  fields of identity.json.
- `autobot/tier/contract/v1` — ContractDigest over
  `{requirement_digest, …the sixteen contract fields}`.
- `autobot/object/TaskContractRecord/v1` — RecordHash over
  `{contract_digest, …the seven record fields}`; this is obj-04's object
  hash. Tier preimages carry fields only; type and version bind through the
  domain (D-33). The three digests are stored beside the record (ledger
  columns, D-04), never inside the bytes they cover; the full payload is the
  content-store object from which all three recompute.
- `autobot/object/<kind>/v1` — every other protocol object's object hash,
  over its full payload; `<kind>` is the record's kind value, so multi-kind
  types (D-20) separate per kind. The library refuses a payload whose kind or
  schema_version disagrees with the domain (SEM-17).
- `autobot/closure/<kind>/v1` — ArtifactRoot over a ClosureManifest whose
  leaves are content addresses: native {executable_sha256}; python
  {script_sha256, interpreter_id, dependency_lock_sha256}; packaged
  {manifest_sha256} or {image_digest} (D-32).
- `autobot/journal/transition/v1` — the journal chain:
  `entry_hash = H(domain || JCS({payload, previous?}))`; the genesis entry
  omits `previous` (D-37). Chapter 2 owns the payload fields; the
  construction is fixed here (JH-1).
- Content addresses (specification inputs, witness bytes, closure leaves,
  store addresses) are plain SHA-256 of exact bytes, no domain (D-31). For
  git-resolved inputs the exact bytes are the committed blob's, never the
  working tree's (D-42).

Digest text everywhere: `sha256:` + 64 lowercase hex; any other spelling is
rejected (D-30).

## 5. Instants and windows (D-27, D-28)

Instants: `YYYY-MM-DDTHH:MM:SS.mmmZ`, exactly. An audit instant never orders
or gates authority (R-6 orders by version, fence and CAS). Durations are
integer `*_seconds`; every authority window is persisted as an absolute
`*_expires_utc` computed once when the window opens; expiry is `now >= end`,
evaluated by the Sequencer's clock (t-d02's comparison). INV-67's
`hard_timeout < ttl` is SEM-12.

## 6. Schemas (D-34..D-36, D-43)

Six JSON Schema 2020-12 files in `schemas/` carry the ten required objects,
written in a closed authoring profile (type, properties, required,
additionalProperties:false, enum, const, pattern, items, bounds,
patternProperties, propertyNames, oneOf, local $defs/$ref) that the library
implements exactly (`schema_profile.py`), so the schemas are standard and
executable here with zero dependencies. Every object carries
`schema_version: "v1"`; recognition is MALFORMED vs UNSUPPORTED vs SUPPORTED
(D-34); newer software keeps v1 readers forever and may parse anything for
audit without granting authority (D-35). Unknown fields are rejected on every
Autobot-minted object (D-36); producer witnesses are the opposite model —
exact bytes preserved, tolerant correlation parse, unknown fields grant
nothing (D-38) — admission living in the ExecutionReceipt's
admitted_witnesses section (D-21).

## 7. Semantic rules (SEM-01..SEM-19)

`registries/semantic_rules.json` lists every rule the schemas cannot express,
each with its Chapter 0 basis, enforcing function and parity test: the freeze
couplings (K-1..K-10 become SEM-01..SEM-11, including D-54's exact meaning of
`repository_declaration_bound_and_matching` and D-44's delegation-field
consistency that makes V-Q's object unrepresentable), the window arithmetic
(SEM-12), derived eligibility (SEM-13, D-52), supersession provenance
(SEM-14, D-45), artifact-root recomputation (SEM-15), nondeterministic-
verifier ineligibility (SEM-16, D-57), kind/domain agreement (SEM-17),
canonical-form admission (SEM-18), and repository_id uniqueness (SEM-19,
D-23: a Registry admission rule — the schema accepts any well-formed slug;
a new declaration claiming an existing repository_id must come from the
same operator, enforced by Chapter 2's `registry_admit_declaration`, with
the parity test proving the schema alone cannot catch it).

## 8. Vectors and predicates

All 19 Phase A vectors are resolved (`registries/vectors_resolution.json`):
13 executable with frozen digests, 5 rejections with fixed codes (V-I set
order, V-J unknown field, V-K unsupported version, V-Q delegation-id
inconsistency, V-S location mismatch), V-B/V-G/V-L as executable equality and
separation proofs. V-Q is preserved and closed by a mechanical impossibility
proof (D-44): the validator rejects every inconsistent pair and a property
test varies the id with the binding fixed. P-1 (CH1-scope-check) and P-2
(CH1-duplicate-admission) run as predicates with a stub live-Sequence lookup,
as Phase A planned; JH-1 proves the journal chain. The golden file
`vectors/golden/golden_digests.json` freezes every digest, the V-A canonical
preimages, the fixture object hashes, artifact roots and content addresses.

## 9. Conformance (D-58, D-59)

Five oracles: the dual JCS implementations; the frozen goldens re-verified on
every run; seeded property tests; schema/runtime parity tests for every SEM
rule; and a cross-process recomputation in a fresh `-I` interpreter standing
in for the Registry and the Sequencer computing independently. The contract
library is stdlib-only, enforced by a static import scan plus a runtime
import-graph check (D-59). The checker itself is tested by planted defects
(`test_checker_negative.py`), control copy first.

## 10. Traceability

`registries/traceability.json` maps all 76 invariants: INV-05, INV-21,
INV-28 and INV-40 are owned and proven here (the three inherited CH1 tests
plus the duplicate-admission predicate); every other invariant names its
owning runtime chapter (ch2..ch8) or the Chapter 0 checker, plus what Chapter
1 contributes to it. Amendment 1 is carried representationally: intent_class
{attempt_scoped, staging_delete, workspace_sync}, the per-class check lists,
retryable/not_retryable sets, and the closure of the formerly open U-44/U-47
bindings on obj-25, obj-29 and obj-41 (INV-47, INV-73..INV-75).

## 11. The contract library (the typed interface)

`contract_library` exposes, stdlib-only and deterministic:

- `canonicalize(value) -> bytes`; `CanonicalizationError`.
- `content_address(bytes)`, `domained_digest(domain, payload)`,
  `object_hash(kind, payload)`, `requirement_digest / contract_digest /
  record_hash / compute_tiers(record)`, `artifact_root(manifest)`,
  `journal_entry_hash(payload, previous)` — all returning D-30 text.
- `requirement_preimage / contract_preimage / record_preimage` (the exact
  constructions, exposed for audit).
- Grammars: `DIGEST_RE, INSTANT_RE, MINTED_ID_RE, NAME_ID_RE, PRINCIPAL_RE,
  is_nfc, repo_path_ok, ref_name_ok, path_contains, scope_within, utf16_key`.
- Validation: `load_schemas(dir)`, `recognize_version(payload)`,
  `validate_payload(kind, payload, schemas) -> ValidationResult`,
  `validate_freeze(record, context) -> (hard, queue)`,
  `freeze_disposition(record, context, schemas)` (P-1),
  `admit(payload, live_sequences, context, schemas)` (P-2),
  `derive_eligibility(record)`, `derived_finalization_requirements(...)`.

Dependency boundary (D-59): no AutoSwarm, Helios, HDB, Git execution,
network, authority-service runtime, storage engine or agent APIs. The
Registry, the Sequencer and verifier runners embed this library; Chapter 2's
ledger supplies the live-Sequence lookup that `admit` stubs.

## 12. Acceptance criteria (the integrated gate)

`harness/run_all.py` passes iff: the goldens regenerate byte-identically,
every unittest suite passes (canonicalization, schemas, semantic rules,
vectors, cross-process, isolation, checker negatives), and
`check_chapter1.py`'s registry checks pass (decision ids and order against
the frozen candidates, enum consistency against Chapter 0, schema coverage,
parity-test existence, vector ids against frozen Phase A, goldens
consistency, the 76-row traceability, the 42-row representation against the
frozen inventory, the D-60 handoff chain, and this document's counts).

## 13. What stays open, deliberately

- U-06 (AutoSwarm's outcome vocabulary): completion_policies values are an
  open NFC vocabulary until it resolves; the enum register marks it.
- U-01..U-05, U-07..U-09, U-11..U-17 carry forward exactly as Chapter 0
  recorded them; none blocks this surface.
- Publication: D-19..D-61 are DECIDED (ratified 2026-11-08); the ratified
  tree was frozen into `docs/architecture/chapter1/phase-b` with its handoff,
  then restructured on 2026-11-09 per the operator's specification into this
  unified `docs/architecture/chapter1` layout (handoff `ch01.json`,
  predecessor `ch01-phase-b.json`). The phase-b freeze is committed locally
  as `1805347c29d7` on branch `chapter1-unified` (parent = main
  `5fa870d8444d`, which origin still matches); the unified tree's own
  commit → push → PR → merge remain separate operator approvals, none
  given yet.
