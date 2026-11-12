# Chapter 3 — closure (C3-M3)

The chapter's completion matrix: every frozen Chapter 3 obligation with
its disposition — **CLOSED** (implemented and proven), **NARROWED** (the
protocol question closed, a non-blocking operational question named), or
**DEFERRED** (explicitly owned by a later milestone or chapter). Baseline
`8a6fd7a` (C3-M2) on `1754423` (C3-M1) on `7321aa9` (M7); proof of
record in §10. The structural principle the whole chapter enforces:
authority is **derived, never declared** (D-C3-2), and absence of an
authoritative observation is never an observation of the opposite state.

## 1. Registered chapter-3 test obligations (invariants register)

Every `tests[].chapter == 3` entry in chapter 0's invariants register,
with the runtime test that executes it:

| Invariant | Registered id | Status | Executing proof |
| --- | --- | --- | --- |
| INV-03 artifacts belong to exactly one attempt by causal provenance | CH3-artifact-provenance-single-attempt | **CLOSED** (M2) | test_provenance.ArtifactProvenanceTests.test_ch3_artifact_provenance_single_attempt |
| INV-04 evidence never satisfies by being newer | CH3-mtime-never-selects-artifact | **CLOSED** (M2) | test_ch3_mtime_never_selects_artifact (behavioral + source scan) |
| INV-22 provenance from WitnessRecords, never mtime or claims | CH3-provenance-from-witnesses | **CLOSED** (M2) | test_ch3_provenance_from_witnesses |
| INV-23 integrity failures block acceptance | CH3-integrity-failure-blocks | **CLOSED** (M1) | test_attempts_execution.test_ch3_integrity_failure_blocks |
| INV-30 completeness from raw witnesses only; INDEX_LAG never blocks | CH3-index-lag-diagnostic | **CLOSED** (M3) | test_provenance.IndexLagDiagnosticTests (byte-equality under lagging/raising/garbage probes; the never-blocks transition arm; the blindness source scan) |
| INV-37 inputs re-hashed at dispatch and collection; change = INTEGRITY_FAILED | CH3-input-rehash | **CLOSED** (M3) | test_provenance.InputRehashTests (dispatch refusal; input_rehash binding; INTEGRITY_FAILED version; recovery halt) |
| INV-68 orphan / DENY-then-ran / no-decision = INTEGRITY_FAILED; DENY counts diagnostic | CH3-integrity-classes | **CLOSED** (M3) | test_provenance.IntegrityClassesTests (orphan; self-decided; sibling-decided; one-record deny+ran F-C3-9; enforced-DENY-diagnostic) |

## 2. Transition and row obligations

| Row | Disposition |
| --- | --- |
| t-a06 + t-s09 `execution_receipt_complete` | **CLOSED** (M1): join half (ch2) + admitted COMPLETE classification, checked before any byte probing (F-C3-1) |
| t-a13 + t-s45 `execution_receipt_integrity_failed_or_incomplete_beyond_bound` | **CLOSED** (M1): the fail-closed `_admitted_classification` seam; INTEGRITY_FAILED immediate, INCOMPLETE beyond the D-C3-1 bound; t-s45 HALTs in the same transaction (INV-23) |
| t-a02 dispatch | **CLOSED**: Sequencer-minted call identity (M3/ch2) + the INV-37 input gate (C3-M3) |
| t-a03 / t-a05 | **CLOSED** (ch2 + M1): positive handle witness; freeze instant = the D-C3-1 anchor |
| t-a04 / t-a11 outcome arms | **CLOSED** (M2): frozen `completion_policies` decide over the D-C3-4 mapped outcome; absence of a result record manufactures no state |
| t-a12 staleness | **CLOSED** (ch2): the member lease, never provider absence |
| cap-30 "admission, completeness per role, index state" | **CLOSED**: admission seam (M1), per-role members (M2), index-state diagnostic (M3, D-C3-7); enforcement `interim: code`, ipc_acl stays with process separation (ch8 family) |
| obj-15 always-bindings (attempt identity, admitted witness hashes, completeness class, input_rehash) | **CLOSED** (M1/M2/M3): all four are required schema members, derived and byte-equal across collector/seam/recovery |
| obj-10 PromptProjection | **CLOSED** (M2): schema both mirrors, minter `dispatcher`, validated at mint, call identity deliberately absent, determinism pinned |
| obj-11..obj-14 admission | **CLOSED** (ch2/M2): exact producer bytes, D-C3-5 identity gate; payload SCHEMAS beyond recognized facts **DEFERRED** (below) |

## 3. Decisions

D-C3-1 (bound), D-C3-2 (derived-never-declared), D-C3-3 (INTEGRITY_FAILED
/ D-66 interplay), D-C3-4 (outcome vocabulary + missing-result semantics,
operator-corrected), D-C3-5 (raw witness trust model), D-C3-6 (causal
joins + deny_then_ran), D-C3-7 (index diagnostic), D-C3-8 (integrity
classes), D-C3-9 (input re-hash) — all RECORDED in
[registries/decisions.json](registries/decisions.json) and enforced by
the registered proofs above.

## 4. Unknowns (chapter-0 register) — the chapter-3 slate

| Id | Disposition for Chapter 3 |
| --- | --- |
| U-01 DENY-then-ran root cause | **NARROWED**: the deterministic detection join is CLOSED in-protocol (D-C3-6, deny_then_ran incl. the F-C3-9 one-record arm); the historical Helios-side root cause stays a **DEFERRED** operational item, non-blocking |
| U-02 Codex DevTools write provenance | **NARROWED**: durable rollout/export provenance satisfies the required interface (Chapter 3 is provider-interface based, not DevTools-dependent); the live DevTools probe stays a **DEFERRED** operational follow-up, non-blocking |
| U-04 Codex ledger completeness | **NARROWED**: the SHAPE is proven (a real Codex governance record carries the same recognized facts); coverage breadth stays a **DEFERRED** operational comparison, non-blocking (the frozen evidence_requirements remain the authority) |
| U-05 catalog watermark interface | **RESOLVED** (M3): the helios-evidence-catalog status.v1 document (watermarks / integrity.closed), DSN-gated, failing closed when unconfigured; consumed only as the D-C3-7 diagnostic probe |
| U-06 provider outcome vocabulary | **RESOLVED** (M2, operator-corrected): exactly completed/failed/cancelled; an absent result proves only that no terminal outcome was observed |
| U-07 controller_id guarantee | **RESOLVED** (M2): provider-contract guaranteed; the causal child-parent join |
| U-08 member results / U-09 proof-policy names | **RESOLVED** (ch0/ch1: C8, C7) |
| U-03, U-10..U-18 | Owned by Chapters 1/4/6/8 as registered — none blocks Chapter 3 |

## 5. Recovery — one evidence subsystem

The chapter's durable surfaces and the recover() section that re-proves
each, all converging (pure functions over durable rows) and all paired
with the hash-chained journal (D-68, walked and validated every run):

- **Frozen records and inputs** — the records loop: digests recompute
  (INV-11) and every declared specification input resolves to its exact
  bytes (raising; the D-C3-9 interplay).
- **Witness admissions** — 8b: bound-execution join, D-66 re-hash,
  journal pairing both directions.
- **Receipts** — 8f: attempt-state window, D-66 re-hash, schema
  re-validation, row↔payload agreement, journal pairing, and the FULL
  byte-identical re-derivation from durable facts (now carrying causal
  joins, contradictions incl. the M3 rules, member completeness,
  artifact provenance and input_rehash); EVIDENCE_COMPLETE requires an
  admitted COMPLETE receipt; a journaled t-a13 names its receipt.
- **Artifacts** — 8g: bound-execution join, ledger-correlation
  agreement, D-66 re-hash, journal pairing; plus the witness-correlation
  coherence HALT for adopted executions.
- **Fail-closed interplays (deliberate, documented)**: corrupted witness
  bytes halt at 8b before 8f re-derives (D-C3-3); corrupted input bytes
  halt at the records loop before 8f re-derives (D-C3-9). A genuinely
  INTEGRITY_FAILED ledger requires the operator; nothing trusts or
  replaces bytes.
- **Post-freeze forgery**: an artifact or witness row injected after
  t-a05 cannot journal without rewriting the hash chain; with an
  admitted receipt it additionally diverges from the 8f re-derivation.
  Restart reconstruction of the whole causal graph is pinned by test.

## 6. Adversarial review record (C3-M3 pass)

The combined receipt + provenance plane was re-reviewed end to end; the
pass produced:

- **F-C3-9 FOUND and FIXED** — `deny_then_ran` used `elif`, so a single
  governance record proving BOTH the DENY and the execution (decision +
  PostToolUse/success in one record — the strongest witness) joined
  nothing. Membership is now independent; the one-record arm is a
  registered proof. Receipts derived before the fix re-derive
  identically unless such a record exists (none did in any committed
  fixture or ledger; the chapter is unpublished).
- The **orphan rule's absence-principle audit**: execution_without_
  decision is a POSITIVE observation (the execution record exists and
  the record family guarantees decisions precede execution) — the G3
  principle stands untouched; pure absence (an enforced DENY, a missing
  kind) still classifies at most INCOMPLETE, never by inference.
- The **toolu_x1 fixture audit**: M2's shared tool_use_id default makes
  the deny arms self-consistent under the new rule (the DENY decision
  decides its executions); the restart fixture's lone execution record
  now legitimately carries the orphan contradiction and its
  byte-determinism assertions still hold.
- The **index purity argument**: a live probe result inside obj-15 would
  break the D-C3-2 byte-equality between collector, seam and recovery —
  INDEX_LAG structurally cannot become authority "accidentally"; it
  would take a deliberate schema + derivation + seam + recovery change
  declaring an HDB-derived input, which INV-30's "unless" branch
  reserves to a frozen assertion (no v1 assertion kind exists; a future
  chapter adding one brings its own proof).

## 7. Stand-in scan

`src/` carries **zero** TODO / FIXME / XXX / stand-in / placeholder
markers in the authority plane (scan 2026-11-12; the coordinator
package's "placeholder" vocabulary is its own ledger domain concept).
The known historical stand-ins and their dispositions:

| Item | Disposition |
| --- | --- |
| F-CE-4 (Taskforce hardcoded `"completeness": "COMPLETE"`) | **CLOSED** (C3-M1): the Taskforce only asks the Collector; the seam refuses non-collector minters |
| t-a13 fail-closed seam (PARTIAL at M2/ch2) | **CLOSED** (C3-M1): live arms, whole t-s09 |
| FakeTransport | Registered acceptance-test harness, tests only — never in src authority paths |
| t-a10 remote-control live arm (fake-proven at G2) | Chapter-2/G2 operational record, outside the evidence plane; the AutoSwarm patch is live on the fork (2bbaca72) — **DEFERRED** to the operational track |
| Production verifier runner | **Chapter 4's** opening obligation (obj-18/19/20); the M4 VerifierRunner seam is real and registered |

## 8. Deferred surfaces (the honest list)

| Surface | Owner |
| --- | --- |
| Witness payload schemas beyond byte integrity + recognized facts (obj-11..obj-14) | A later milestone, when a consumer needs them; admission stays exact-producer-bytes |
| Multi-version receipt dominance rules | Later chapter (F-C3-2 records why v1 is sound: the witness set is immutable from freeze) |
| Multi-member concurrent dispatch | Its own milestone; today a contract requiring more members than dispatched classifies INCOMPLETE by construction (M2 §8) |
| Policy-resolved evidence window (D-18 "stricter of") | The policy plumbing chapter; carried as collector configuration (D-C3-1) |
| Live Codex DevTools probe (U-02) / coverage quantification (U-04) | Operational follow-ups, non-blocking |
| Catalog DSN configuration for a live index_state probe in production | Operational (U-05's resolution names the env contract) |
| INDEX_LAG "unless an assertion declares an HDB-derived query" | Vacuous in v1 (no such assertion kind exists); a chapter introducing one must bring its own proof |

## 9. What Chapter 4 inherits

The evidence boundary is whole: PromptProjection (obj-10) through
Sequencer-minted execution identity, provider/session/controller joins,
command governance with the full D-12 integrity classes, transcripts,
artifact provenance, input re-hash, per-role membership — derived into
one authoritative, append-only, byte-re-derivable ExecutionReceipt that
t-a06/t-s09/t-a13/t-s45 consume fail-closed. Chapter 4 (CandidateReceipt
and production verification authority) builds ON this receipt; nothing
in Chapter 3 waits on Chapter 4.

## 10. Proof of record (C3-M3)

- venv CPython 3.12.11: **206/206 OK** (gate 20261112-ce-073) — the
  first run, zero defects flushed.
- PATH CPython 3.8.10: **206/206 OK** (gate 20261112-ce-074).
- Coordinator regression: **41/41 OK** (gate 20261112-ce-075).
- 206 = C3-M2's 194 + 12 new registered proofs in
  `tests/autobot/test_provenance.py`: InputRehashTests (dispatch
  refusal, the input_rehash binding with the INTEGRITY_FAILED
  re-collection version, the recovery halt), IntegrityClassesTests
  (orphan, self-decided, sibling-decided, the F-C3-9 one-record
  deny+ran, the enforced-DENY diagnostic pin) and
  IndexLagDiagnosticTests (byte-equality under lagging/raising/garbage
  probes with the never-blocks transition arm, the U-05 interface
  recognition, the unavailability reports, the blindness source scan).
- U-05's live probes: gates 20261112-ce-071 (the `hdb` name absent from
  this session's PATH) and ce-072 (the catalog CLI fails closed without
  its DSN); the interface shape is pinned from the implementation
  source and exercised by the recognition tests.
