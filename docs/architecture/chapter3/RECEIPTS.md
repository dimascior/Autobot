# Chapter 3, milestone 1 — the authoritative ExecutionReceipt

> **C3-M2 amendment (2026-11-12, in-chapter):** the obj-15 payload was
> extended with the causal evidence graph — `causal_joins`,
> `contradictions`, `member_completeness`, `artifact_provenance` and the
> optional `native_session_id` — and the derivation entry point became
> `receipts.derive_evidence` (integrity probe + recognized-fact
> extraction + build). Every M1 pin was re-proven on the amended shape;
> [PROVENANCE.md](PROVENANCE.md) documents the extension. The chapter is
> unpublished, so the v1 schema amends in place.

> **C3-M3 amendment (2026-11-12, in-chapter):** the v1 payload gained
> the required `input_rehash` member (INV-37, D-C3-9: inputs re-hash at
> dispatch and at collection, bound in the receipt, a change is
> INTEGRITY_FAILED) and the contradiction rule
> `execution_without_decision` (D-12/INV-68, D-C3-8); §8's "index
> state" facet landed as the Collector's injected diagnostic (INV-30,
> D-C3-7) — never in the payload. Every pin re-proven: 206/206 on both
> interpreters + 41/41 ([CLOSURE.md](CLOSURE.md)).

C3-M1 closes the last deliberate stand-in inside the continuous loop's
evidence boundary: ExecutionReceipt (obj-15) completeness classification.
Since M3 the receipt-consuming transitions have been registered,
journal-ready and **fail-closed** behind the `_admitted_classification`
seam; since M7 the Taskforce has assembled the receipt itself with
`"completeness": "COMPLETE"` hardcoded (F-CE-4). After this milestone the
classification is a derived, admitted ledger fact minted by the registered
collector seat, and t-a13, t-s45 and the classification half of t-s09 are
live.

## 1. The boundary (who)

| Concern | Principal | Frozen source |
| --- | --- | --- |
| Mint obj-15; classify completeness per role; derive the bound | `collector` (Autobot Evidence Collector, autobot_seat, llm: false) | principals.json; objects.json obj-15 `minter: collector`; cap-30 owner; CORR-03 |
| Admit the receipt; re-derive and refuse divergence; apply t-a06/t-a13/t-s09/t-s45 | `sequencer` (cap-51; cap-30 level C) | transitions.json rows; cap-30 others |
| Ask for collection; drive the registered arcs | `task_force` — **forbidden on cap-30**: it never mints, classifies or submits | capabilities.json cap-30 forbidden list |
| Produce witnesses | autoswarm / helios / agent_clis / ci_system (obj-11..obj-14 registered minters) | witnesses.py pinned to objects.json |

The registered edge `collector → sequencer` carries "ExecutionReceipt";
the forbidden-edge list simultaneously records "evidence never grants
authority" — the receipt delivers *facts*, and only the Sequencer's
registered transitions act on them. cap-30 enforcement is `interim: code`
(the Collector component is the only minting path; the seam refuses any
payload whose `minter` is not the registered obj-15 minter); `ipc_acl`
stays with the later process-separation milestone.

## 2. The obj-15 payload (what)

Canonical JSON (contract_library `canonicalize`), validated against
`execution_receipt.schema.json` (D-43 authoring profile), mirrored
byte-identically between `docs/architecture/chapter3/schemas/` (provenance)
and `src/autobot/schemas_chapter3/` (runtime), pinned by test:

| Field | Meaning |
| --- | --- |
| `schema_version`, `kind`, `minter` | `v1`, `ExecutionReceipt`, `collector` (INV-68 identity + registered minter) |
| `attempt_id`, `attempt_contract_hash`, `call_id` | the INV-69/INV-68 attempt binding plus the execution correlation, read from the ledger |
| `completeness` | `COMPLETE` \| `INCOMPLETE` \| `INTEGRITY_FAILED` — derived, never declared (D-C3-2) |
| `completeness_by_role` | per dispatched role (cap-30's "completeness per role"; v1 has one role per attempt): `required_kinds` (the frozen record's `evidence_requirements` mapped to registered witness kinds), `present_kinds` (distinct admitted kinds), `missing_kinds` |
| `admitted_witnesses` | exactly the attempt's full admitted D-21 entry set `{witness_kind, producer, content_sha256}`, sorted — a receipt can neither invent nor omit evidence |
| `integrity_failures` | the admitted entries whose bytes failed the D-66 probe at collection (sorted; `[]` when none) |
| `derivation` | `{rule: "ch3:evidence-window-after-freeze", anchor_utc, window_seconds}` — the D-C3-1 audit trail |
| `evidence_complete_by_utc` | `anchor + window` (D-27 instant; lexically comparable per D-28) |

The payload is a **pure function of durable ledger facts plus the
configured window**, with no collection timestamp: re-collection over
unchanged facts re-mints byte-identical bytes (admission idempotent), and
re-collection after facts changed mints a new version — exactly obj-15's
registered mutability ("append-only; re-collection makes a new version").

The requirement→kind mapping is pinned by test to both registries:
`EXECUTION_LIFECYCLE→obj-11 (autoswarm)`, `COMMAND_GOVERNANCE→obj-12
(helios)`, `SESSION_TRANSCRIPT→obj-13 (agent_clis)`, `REMOTE_CHECK→obj-14
(ci_system)`.

## 3. Derivation semantics (how)

1. **Scope**: collection is defined only for an attempt in
   `CANDIDATE_FROZEN` (t-a05 happened; `admit_witness` refuses frozen
   attempts, so the witness set is immutable from freeze onward).
2. **Integrity probe** (D-C3-3): every admitted row's bytes are probed
   non-raising (`store.verify`); any missing or altered bytes classify the
   receipt `INTEGRITY_FAILED` (final — the bound is irrelevant).
3. **Per-role completeness**: for the attempt's dispatched role, required
   kinds come from the frozen record's `evidence_requirements`; all
   required kinds admitted → `COMPLETE`; otherwise `INCOMPLETE`.
4. **Bound** (D-C3-1): `anchor` = the attempt's CANDIDATE_FROZEN instant
   (the t-a05 `recorded_utc`, equal to the attempt row's `updated_utc`
   while frozen); `window` = `evidence_collection_window_seconds`
   (D-18 configuration default `3600`, D-28 integer seconds, collector
   configuration in v1). `evidence_complete_by_utc = anchor + window`,
   always present, always auditable.

## 4. The admission seam

`Sequencer.admit_execution_receipt(attempt_id, data)` — one transaction:

- lease; attempt exists and is `CANDIDATE_FROZEN`;
- `data` parses and IS its own canonical form (identity discipline);
- schema-validates against the mirrored obj-15 schema;
- binding pre-checks with named errors: kind, attempt_id,
  attempt_contract_hash (INV-68/INV-69), `minter == collector` (cap-30);
- **re-derivation (D-C3-2)**: the seam rebuilds the entire payload from
  the ledger (frozen record, dispatched role, witness rows, D-66 probe,
  D-C3-1 anchor) with the submitted `derivation.window_seconds` and
  requires the result **byte-identical** to `data` — any forged
  classification, bound, anchor, role fact or witness set refuses;
- content-addressed storage; idempotent on `(attempt_id, content_sha256)`
  (a duplicate admission journals nothing); otherwise one
  `attempt_receipts` row (schema v7) and one journaled
  `ch3:admit-execution-receipt` → `ADMITTED` in the same transaction.

`evidence_state(attempt_id)` is the matching public read surface (the
collector consumes public APIs only, D-63): the frozen attempt's identity,
role, record payload, witness rows and anchor.

## 5. The transitions, live

- **t-a06 + t-s09** (`complete_evidence`): everything M3/M4 enforced is
  unchanged (fence correlation, receipt resolution and binding, the REAL
  witness join); the admitted-classification half is now enforced — the
  presented receipt must be ADMITTED for this attempt and its admitted
  `completeness` must be `COMPLETE`. `execution_receipt_complete` is whole:
  join half (Chapter 2) + classification half (this milestone).
- **t-a13 + t-s45** (`fail_candidate`): `_admitted_classification` now
  returns the admitted row's facts for the presented receipt address
  (refusing an unadmitted receipt), and the arms M4 wrote become
  reachable: `INTEGRITY_FAILED` fails at once; `INCOMPLETE` fails only
  once its derived bound has elapsed (premature stays refused); the
  sequence HALTs beside the attempt in the same transaction (INV-23:
  integrity failures block acceptance, before verification ever runs).
- A hand-built receipt presented directly to either transition is refused
  as **not admitted** — declared facts carry no authority (D-C3-2).

## 6. The Taskforce, off the stand-in

`collect_running` no longer assembles any receipt. The FINISHED arc ends
at `freeze_candidate`; a dedicated frozen-evidence pass then runs for
EVERY attempt in `CANDIDATE_FROZEN` (fresh this sweep or inherited across
a restart): ask the Collector (`collect_and_admit`), then act on the
admitted classification — `COMPLETE` → `complete_evidence`;
`INTEGRITY_FAILED`, or `INCOMPLETE` with an elapsed bound →
`fail_candidate`; `INCOMPLETE` inside its window → leave frozen (the next
sweep re-collects; unchanged facts re-admit idempotently and quietly).
The Taskforce never sees, sets or forwards a classification of its own —
F-CE-4 closed.

## 7. Recovery (8f)

`recover()` pairs `attempt_receipts` with the journal in both directions
(reverse_tables + per-row checks), re-hashes every receipt's bytes
(D-66), re-validates the schema, re-checks the row↔payload column
agreement, and **re-derives the payload byte-identically** from durable
facts (the t-a05 journal instant as anchor, the row's witness set, the
frozen record) — a forged or divergent row HALTs. Strengthenings: an
attempt at or past EVIDENCE_COMPLETE must hold an admitted COMPLETE
receipt; an attempt FAILED by t-a13 must hold the matching admitted
non-COMPLETE receipt named by its journal facts. A genuine
INTEGRITY_FAILED ledger halts earlier, at 8b's witness re-hash — D-C3-3
records that interplay as deliberate (both arms fail closed).

## 8. Preserved surfaces

Witness payload SEMANTICS beyond byte integrity (obj-11..obj-14 payload
schemas) stay open — admission remains exact-producer-bytes. cap-30's
"index state" facet (the HDB INDEX_LAG diagnostics) is not represented in
the v1 payload and stays with the index chapter. t-s09's receipt is
attempt-scoped v1 (multi-member attempts arrive with their own
milestone). The D-18 "stricter of delegation/repository policy" window
resolution is carried as collector configuration until the policy
plumbing chapter. `project_state` is unchanged (frozen-evidence attempts
are visible by state; a receipts facet can join the view when a consumer
needs it). Chapter 4 (production verifier runner), Chapter 5
(obj-30/obj-35 payload schemas, ACCEPTANCE_FRESH), Chapter 6 (remote
arcs, t-g*/t-w*) and the operator authorities (t-s13, cap-36, cap-44,
cap-54, cap-10) are untouched. The M7 pilot and all committed arcs run
unchanged over the real collector.

## 9. Row dispositions

| Row / obligation | Disposition |
| --- | --- |
| t-a06 `required_joins_present` | UNCHANGED (M3's real join), now defense in depth behind the admitted set equality |
| t-a06/t-s09 `execution_receipt_complete` | **IMPLEMENTED, whole**: join half (Chapter 2) + the admitted COMPLETE classification (this milestone), checked before any byte probing |
| t-a13 `execution_receipt_integrity_failed_or_incomplete_beyond_bound` | **IMPLEMENTED**: the fail-closed `_admitted_classification` seam is live over `attempt_receipts`; INTEGRITY_FAILED immediate, INCOMPLETE only beyond the derived D-C3-1 bound |
| t-s45 (same precondition) | **IMPLEMENTED**: rides t-a13's transaction exactly as M4 wrote it, now reachable; INV-23's registered test (`CH3-integrity-failure-blocks`) executes as `test_ch3_integrity_failure_blocks` |
| obj-15 payload/schema | **DEFINED**: the mirrored `execution_receipt.schema.json` (D-43 profile) plus the derivation in `receipts.py`, pinned to objects.json/cap-30/witness kinds by test |
| cap-30 ownership | **ENFORCED (interim: code)**: the Collector component is the only minting path; the seam refuses non-collector minters; the Taskforce only asks (F-CE-4 closed) |
| Deadline/member semantics | **DEFINED (D-C3-1)**: per-role required kinds from the frozen record's `evidence_requirements`; bound = CANDIDATE_FROZEN instant + the configured D-28 window |
| Restart/reconciliation | **IMPLEMENTED**: recover() 8f — bidirectional journal pairing, D-66 re-hash, schema re-validation, row↔payload agreement, byte-identical re-derivation, plus the EVIDENCE_COMPLETE-requires-COMPLETE and t-a13-names-its-receipt strengthenings |

## 10. Findings (F-C3)

- **F-C3-1 FIXED** — the classification check originally ran after the
  t-a06 join, so an admitted INTEGRITY_FAILED receipt presented to
  `complete_evidence` hit the D-66 byte-probe backstop (IntegrityHalt)
  instead of the registered refusal. The admitted-classification check
  now runs immediately after receipt resolution: a non-COMPLETE
  classification refuses deterministically before any byte probing.
- **F-C3-2 accepted** — `fail_candidate` consumes whichever ADMITTED
  receipt version the caller presents; an elapsed INCOMPLETE version
  stays consumable after a later re-collection. Sound in v1 because the
  witness set is immutable from the freeze (admit_witness refuses frozen
  attempts), so completeness can never improve across versions —
  latest-version dominance rules belong to the multi-version semantics
  of a later chapter.
- **F-C3-3 accepted** — an INCOMPLETE bound elapses on the clock, not on
  a ledger event, so the failure arc fires on the NEXT Taskforce sweep;
  `run_until_stable` legitimately parks a within-window attempt as
  stable-with-waiting-work (visible in `project_state`). The production
  driver's time-based cadence owns re-sweeping (the F-CE-2 knob's
  sibling).
- **F-C3-4 accepted** — every collection and every admission re-hashes
  all admitted witness bytes (the D-66 probe runs on both sides of the
  authority boundary, the admission inside its transaction). Correct and
  deliberate for v1; a production plane can add verified-at watermarks
  without weakening the boundary.
- **F-C3-5 FIXED** — the journal's payload-vocabulary note predated ch3
  rules; it now names chapter-prefixed primitives generally.
- The D-C3-3 interplay (a genuine INTEGRITY_FAILED ledger halts the next
  recover() at 8b's witness re-hash) is a designed, tested behavior, not
  a finding: both arms fail closed, and the operator owns the store.

## 11. Proof of record

- venv CPython 3.12.11: **179/179 OK** (gate 20261112-ce-038; the first
  run, 20261112-ce-036, flushed four defects — two stale PARTIAL
  assertions, the v6 schema pin, and F-C3-1 — diagnosed under ce-037).
- PATH CPython 3.8.10: **179/179 OK** (gate 20261112-ce-039).
- Coordinator regression: **41/41 OK** (gate 20261112-ce-040).
- 179 = M7's 167 + 12 new C3-M1 conformance tests (11 in
  `tests/autobot/test_receipts.py`, one new adversarial arm in
  `tests/autobot/test_attempts_execution.py`), with the three receipt
  tests that pinned the fail-closed seam rewritten as live-arm proofs.
