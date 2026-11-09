# Chapter 1 decision package — D-19..D-61 (RATIFIED 2026-11-08)

43 decisions, one per frozen Phase A candidate, in the candidate registry's
order. 41 are DECIDED — you ratified them on 2026-11-08 ("Ratify and
freeze"), after the RV-54..RV-60 review pass; 2 (D-49, D-50) are the
already-accepted Chapter 0 corrections CORR-55/CORR-56, listed for
completeness. Every decision was implemented and proven executable before it
was put to you: the registries, schemas, golden vectors and conformance
suites all encode exactly these dispositions, so the ratified design is the
one that runs. Full fields (basis, alternatives, rationale, affected tiers,
schemas, compatibility, conformance) are in `registries/decisions.json`;
this is the review view.

**How to read it:** the eight decisions marked ◆ are the ones with real
design freedom — the rest follow from Chapter 0 with little slack. Decisions
that must land together: D-29/D-30/D-31/D-33 (one identity construction),
D-37/D-44/D-45 (absence and the delegation triple), D-32/D-56/D-57
(closures and environments), D-21/D-38 (witness trust model).

## Representation and canonical form

- **D-19 scope**: complete v1 schemas for exactly the ten required objects;
  all 32 others classified and bound to owning chapters under Chapter 1 rules.
- **D-20 multi-kind**: one schema per type with a `kind` discriminator
  (obj-03, obj-37, obj-38); per-kind hash domains.
- ◆ **D-24 integers**: every protocol number is an integer in [0, 2^53-1];
  floats and negatives forbidden outright. This is what makes two independent
  JCS implementations agree byte-for-byte, and it bounds every counter.
  Policy structures fixed: retry {max_retries}, repair {max_repairs},
  reuse {mode, freshness_seconds}, effect {ttl, hard_timeout}.
- ◆ **D-25 collections**: sets are sorted unique arrays under one collation
  (UTF-16 code units, the same as JCS key order); non-canonical order is
  rejected, never silently re-sorted. V-I therefore becomes a rejection vector.
- **D-26 paths and refs**: repository-relative slash paths, exact case, no
  dot segments; `.` means repo root in scope sets only; containment is the
  prefix rule. Refs are full `refs/...` names under git's constraints.
- **D-27 instants**: `YYYY-MM-DDTHH:MM:SS.mmmZ` exactly; an audit instant
  never orders or gates authority.
- **D-28 windows**: durations are integer seconds; every authority window is
  stored as an absolute `*_expires_utc` computed once at open; expiry is
  `now >= end` on the Sequencer's clock.
- **D-34 versioning**: `schema_version` matches `^v[1-9][0-9]*$`; v1 readers
  accept exactly `v1`; MALFORMED and UNSUPPORTED are distinct refusals.
- **D-35 compatibility**: committed objects stay readable forever; audit-mode
  parsing grants nothing; LEGACY_READ_ONLY is a storage class, not a version
  state.
- **D-36 unknown fields**: rejected on every Autobot-minted object (V-J).
- ◆ **D-37 absence**: an absent value is an absent key; null is forbidden
  everywhere; required fields are never absent. Paired-absence rules carry
  the delegation triple and supersession.
- **D-46 text**: identity-bearing strings must be NFC already; the validator
  rejects rather than normalizes; no edge whitespace; `\n` only in the
  objective.

## Identity and digests

- ◆ **D-22 identifiers**: minted ids are `<prefix>_<32 hex>` (128 random
  bits, per-seat); names are lowercase slugs; assertion and edge ids are
  contract-scoped. Nothing derives an id from content or time (R-6).
- ◆ **D-23 repository identity**: repository_id is the slug you declare in
  the RepositoryAuthorityDeclaration; clones, remotes and URL spellings are
  resolution context outside every tier.
- ◆ **D-29 hash domains**: every semantic identity is SHA-256 over an
  LF-terminated `autobot/...​/v1` domain plus canonical bytes; per-kind object
  domains; tier domains for the two sub-tiers; prefix-free framing (V-L).
- **D-30 digest text**: `sha256:` + 64 lowercase hex, everywhere, exactly.
- **D-31 content addressing**: byte payloads (spec inputs, witnesses, store
  addresses, closure leaves) hash plain, no domain; typed meanings hash
  domained. The boundary is bytes vs meanings.
- **D-32 artifact roots**: a domained hash of a per-kind ClosureManifest
  whose leaves are content addresses (native / python / packaged).
- ◆ **D-33 tier embedding**: digest-value embedding — the ContractDigest
  preimage carries the RequirementDigest text; the RecordHash preimage
  carries the ContractDigest text; digests live beside the record, never
  inside the hashed bytes; obj-04's object hash is the RecordHash.
- **D-42 spec bytes**: committed blob bytes, never working-tree bytes
  (the CRLF divergence measured on this machine decides it); filesystem
  inputs hash raw file bytes (V-H).

## The contract surface

- **D-39 enums**: one register (`registries/enums.json`), UPPER_SNAKE wire
  values, frozen per schema version; completion outcomes stay an open
  vocabulary pending U-06 — the one deliberate open set.
- **D-40 principals**: a PrincipalRef is the registered principals.json id.
- **D-41 authentication evidence**: records name the principal only; the
  authority service journals the authentication facts in the same ledger
  transaction as the mint; nothing environment-bound enters portable records.
- ◆ **D-44 delegation fields**: the ContractDigest binds the delegation
  record's hash and the resolved floor; delegation_id in the RecordHash is a
  consistency-constrained copy of the bound record's value. Consequence:
  V-Q's object is unrepresentable — proven by rejection plus a property test.
- **D-45 supersession provenance**: {supersedes_record, authorized_by}, both
  or neither; the two digests can never be equal (different domains).
- **D-47 embedded references**: record hash + typed part selector
  (ASSERTION / DEPENDENCY_EDGE / ENVIRONMENT_CONSTRAINT / ROLE_TRANSFORM,
  by assertion_id / edge_id / role); obj-07 gains edge_id for this.
- **D-48 legacy identity**: the imported bytes' content address plus import
  provenance; a legacy item has no object hash and never authority.
- **D-51 absent-effect receipts**: no receipt for a push that didn't happen;
  the intent's terminal state plus the journal is the trace; t-s18's
  EXPECTED_OLD_STALE CommitReceipt stays the one local exception. obj-27 and
  obj-28 stay HAPPENED-only, keeping INV-41's common bindings valid.
- **D-52 eligibility**: derived — HUMAN_REQUIRED iff any assertion's mode is
  HUMAN_ATTESTED; never stored; no identity.json change.
- **D-53 attestation bindings**: an ASSERTION attestation binds the record,
  the assertion reference, the candidate and the attempt; the
  VerificationReceipt's per-assertion results carry BY_VERIFIER or
  BY_ATTESTATION with the source's hash; acceptance reaches attestations
  through the verification it binds.
- ◆ **D-54 freeze matching**: bound-and-matching = declaration resolves +
  location equal + repository_id equal + target_ref equals canonical_ref for
  source-modifying contracts (K-10 closed strict) + mode fits the declaration
  (MEDIATED freezes but never runs in v1) + finalization fit (K-6/K-7).
- **D-55 recordless decisions**: your budget grants (t-s13) and rejections
  (t-c06) are journaled decision payloads in the TransitionRecord, naming
  you; no new record type.
- **D-56 python environment**: the probe captures typed interpreter /
  platform / named-tools / named-env facts only (never the full environment);
  constraints may pin exactly those; closure answers what runs, the probe
  answers where.
- **D-57 verifier closures**: pin the pinnable (CLI executable + version,
  scripts, locks), declare `service:*` for the rest; any service-declaring
  registration is ineligible to verify a DETERMINISTIC assertion (INV-06
  already caps LLMs at proposing).
- **D-21 witness admission** / **D-38 witness preservation**: admission is a
  section of the ExecutionReceipt (no new type, no Chapter 0 correction);
  producer bytes are preserved exactly, parsed tolerantly, and grant nothing.

## Process

- **D-43 schema language**: JSON Schema 2020-12 in a closed authoring
  profile the library implements exactly; every semantic rule is SEM-xx with
  a parity test.
- **D-58 test oracles**: all five — dual JCS, frozen goldens, property tests,
  parity tests, cross-process recomputation.
- **D-59 isolation**: the contract library is stdlib-only; enforced by scan
  and runtime import graph.
- **D-60 handoff chain**: every handoff records its predecessor's name and
  SHA-256; the checker recomputes the chain.
- **D-61 id namespaces**: continue the global sequences — these are
  D-19..D-61, corrections continue at CORR-57, unknowns at U-19; D-15 stays
  permanently unassigned.

## Already accepted (not for ratification)

- **D-49** (dc.standalone-intents) = CORR-55, merged: staging_ref and
  workspace_sync machines, t-g01/t-w01 initiation rules, INV-74/INV-75.
- **D-50** (dc.retry-intents) = CORR-56, merged: t-i06's initiation checks
  and retry preconditions, INV-73.

Chapter 1 carries both into representation: the intent_class enum, the
per-class check lists, and the closed bindings on obj-25/obj-29/obj-41.

## Ratification record

You ratified D-19..D-61 on 2026-11-08 ("Ratify and freeze"). The statuses
are DECIDED in `registries/decisions.json` and the workspace is frozen into
`docs/architecture/chapter1/phase-b` with its handoff (predecessor
ch00-amendment-01). The commit → push → PR → merge sequence remains four
separate authorizations, exactly as Phases C/A/amendment did; none has been
given yet, and the three previously frozen phases are untouched.
