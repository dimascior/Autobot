# Chapter 2 — the Sequencer and the authoritative ledger/runtime

Chapter 2 turns the frozen contract surface into the authoritative runtime.
Entry was proven on 2026-11-09 (gates 20261109-ch2e-001/002): the five-handoff
chain and all 42 Chapter 1 artifact hashes verified from merge commit
`af341e4`'s own blobs, and `contract_library` imported from that rebuilt tree
reproduced every committed golden digest. Chapter 1 is closed and is consumed
here, never reopened.

## Decisions

The kickoff decision set D-62..D-71 (ratified by the operator, 2026-11-09)
lives in [registries/decisions.json](registries/decisions.json). In one line
each: stdlib SQLite in a dedicated `autobot.db` (D-62); one authority runtime
owns all mutations (D-63); the authority core is `src/autobot/`, beside — not
inside — the old coordinator (D-64); runtime code imports a byte-identical
packaged mirror of `contract_library`, identity-tested against the frozen
Chapter 1 copy (D-65); object bytes live in a filesystem CAS with a
write-fsync-rename discipline, referenced from the ledger (D-66); a new
authoritative schema, not an adaptation of `coordinator.ledger` (D-67); every
authoritative transition is one transaction ending in a hash-chained
TransitionRecord (D-68); an injected UTC clock, with versions and fences — not
time — determining authority (D-69); monotonic non-destructive migrations that
refuse a newer database (D-70); and the first implementation slice stops at
the authority primitives, before any external effect (D-71).

## The runtime pipeline (operator's sequence)

TaskContractRecord → admission/freeze verification → Sequence creation →
Attempt creation → lease + reservation + fence → durable transition journal →
EffectIntent lifecycle → crash/replay recovery → verification transitions →
AcceptanceReceipt → dependency release.

## First executable milestone (D-71)

```text
validated TaskContractRecord
        ↓ (t-c01, t-c02 | t-c03 | t-c04 | t-c07 — one atomic transaction each)
atomic duplicate/live-sequence lookup (P-2 over the real ledger)
        ↓
Sequence row (INV-40 lifecycle coupling) + initial TransitionRecord
        ↓ (t-s03, then t-s06)
AttemptContract capturing frozen source state (obj-09, INV-02)
        ↓ (t-a01)
Attempt reservation
        ↓
InstanceLease (INV-25a) + fence (INV-13)
        ↓
process crash → restart (D-69)
        ↓
same Sequence / Attempt / fence, no duplicate dispatch, journal chain valid
```

Once that passes: permit races (INV-42/48/60 beyond the primitives) and
EffectIntent persistence. Only then does Chapter 2 connect AutoSwarm or Git
effects.

## Milestone 2 — the EffectIntent lifecycle (2026-11-09/10)

The crash-safe external-effect boundary, as authority primitives with no
external executor yet (D-71's ordering):

- `effects.py`: the six registered effects, four outcome rules, the
  architectural retryable split (INV-73) and the attempt_scoped checks,
  pinned byte-for-byte to chapter 0's transitions.json by
  `test_effects_registry.py`.
- Schema v2 (D-70, additive): the obj-25 retry-identity columns
  (`target`, `expected_old`, `new_value`); a database newer than the code
  is refused, and a refused open closes its connection.
- `Sequencer.mint_intent` (attempt_scoped only): delegation active,
  reservation held with a live INV-60 lease, fence equality, and the
  SEM-12/INV-67 window rule, all inside one transaction ending in a
  `ch2:mint-intent` TransitionRecord.
- The boundary itself: `committed_intent()` is the executor's read — only a
  durably committed, still-PENDING intent inside its hard-timeout window
  authorizes beginning the effect (`effect_intent_committed`).
- The machine: t-i01/t-i02 from PENDING; t-i03 only once the window expired
  (before that, PENDING holds because the effect may still land — CORR-49's
  rule that the old value is not evidence of absence); t-i04/t-i05 from
  UNKNOWN_OUTCOME; `expire_intents()` as t-i03's sweep arm; t-i06 retries
  bind the superseded intent and re-run the full checks; t-i07 resolves by
  your attestation once authority is inactive.
- Recovery: journal↔row reconciliation covers intents, windows must satisfy
  INV-67, retry chains must be intact in both directions, and PENDING or
  UNKNOWN_OUTCOME intents are reported as the in-doubt set the restart must
  resolve by observation, never by inference (D-69). The crash matrix gains
  `intent` and `mid_intent_tx` stages.

Deferred by design: the standalone intent classes (staging_delete,
workspace_sync) mint through their own machines (t-g01/t-w01) in a later
milestone, and the sequence-machine wiring that mints intents on the real
paths (t-s15a/b, t-s21, t-s31/32) lands with the verification milestone
that can reach VERIFIED.

### Finding for the operator (correction candidate)

The frozen D-29 kind grammar (`^[A-Za-z][A-Za-z0-9.]*$`,
contract_library/digests.py) cannot express obj-37's registry kind
`ManualAttestation.EFFECT_OUTCOME` — the underscore is outside the
grammar — so no object hash exists for that kind under Chapter 1 as
frozen. Until obj-37's owning chapter fixes its per-kind domain label (or
a correction renames the kind), t-i07 preserves the attestation by exact
bytes (D-31 content address), referenced from the journal entry.

## Layout

- `src/autobot/` — the authority core: `db.py` (D-62/D-68/D-70 database),
  `store.py` (D-66 CAS), `models.py` (states, ids, instants, clock, errors),
  `journal.py` (obj-36 hash chain), `registry.py` (actor `registry`:
  object admission, SEM-19, t-c01..t-c07 over P-1/P-2), `sequencer.py`
  (actor `sequencer`: instance lease, sequence/attempt transitions,
  permit arithmetic, witness admission, verification, recovery),
  `effects.py`, `principals.py` and `witnesses.py` (registry-pinned
  vocabularies), `projection.py` (obj-10 minimal form), `project_state.py`
  (the read-only view), `transport.py` (the transport contract +
  `reconcile_dispatched`), `verification.py` (the typed verifier-runner
  interface + SEM-16/D-52/obj-18 evaluation), `schemas/` (byte mirror of
  the six v1 schemas).
- `src/contract_library/` — byte-identical mirror of
  `docs/architecture/chapter1/contract_library/` (D-65).
- `tests/autobot/` — the CH2-* runtime tests named by Chapter 0's
  invariants.json, plus the D-65 identity test and a registry cross-check
  that the runtime's state tables equal `chapter0/registries/states.json`.

Run the suite from the repository root:

```
python -m unittest discover -s tests/autobot -p "test_*.py" -v
```

## Journal vocabulary note

TransitionRecord payloads carry `rule`: a Chapter 0 transition id (`t-…`)
when a registered machine transition happened, or a `ch2:`-prefixed rule for
authority primitives the full protocol later subsumes into their owning
transitions (`ch2:admit-object`, `ch2:instance-lease`, `ch2:lease-renew`,
`ch2:grant-permit`, `ch2:reserve-use`, `ch2:renew-reservation`,
`ch2:mint-intent`, `ch2:admit-witness`, `ch2:register-verification`,
`ch2:attest-assertion`). The journal
records exactly what happened; no entry ever claims a protocol transition
that did not occur. The chain construction is JH-1 (`journal_entry_hash`,
genesis omits `previous`).

## Adversarial review (2026-11-09)

Four properties the operator named were audited and are now enforced in the
mechanism and proven executable (tests/autobot/test_adversarial.py plus the
recovery suite):

1. **Transaction boundary.** Every mutating method is one `BEGIN IMMEDIATE`
   transaction whose last write is the TransitionRecord append; a failed
   append rolls the mutation back (proven by fault injection), a torn
   transaction vanishes whole (crash matrix, `mid_transaction`), and
   `recover()` reconciles journal↔rows in both directions — a durable
   mutation without its entry, an entry whose mutation rolled back, or
   permit counters disagreeing with their reservation rows all HALT.
2. **Fence ownership.** The expected state, version and fence are conditions
   of the mutating UPDATE statements themselves (`sequences`, `attempts`,
   including the attempt↔sequence fence equality as a correlated subquery),
   never a Python check followed by a separate write; a second connection
   bypassing every in-process check cannot apply a stale mutation (proven).
3. **CAS durability.** Windows cannot fsync directories, so the recovery
   contract is explicit: orphaned temp files and unreferenced objects are
   harmless — reported by `recover()`, never consulted; a committed ledger
   reference to missing or altered bytes HALTs (on read, at put-collision,
   and across the whole reference set — objects, records, admission
   preservations, duplicate preservations, and every frozen record's
   specification-input address — at recovery).
4. **Recovery semantics.** Restart by the same holder retakes its lease with
   an advanced epoch; takeover by another holder is refused until expiry and
   then also advances the epoch; the dead process's old token has no
   authority afterwards, and neither path can duplicate dispatch. The lease
   row must match its last journal entry, so authority is itself journaled.

### Closure pass (2026-11-10)

Milestone 2 closed with one review correction (terminal Sequences grant no
intent authority) and the M3-preparation slice: the attempt-execution
transitions t-a02..t-a06 with their t-a10/t-a11/t-a13 arms, schema v3's
`attempt_calls` execution binding, recovery pairing for executions, and
the `dispatched` crash stage. Proof of record: autobot 82/82 on CPython
3.8.10 and 3.12.11, coordinator 41/41, CORR-57 conformance 12/12 (gates
20261110-ch2m2-001..010). [M3_SPEC.md](M3_SPEC.md) is the executable
vertical-slice specification for the first contract-authorized specialist
execution.

## Milestone 3 — the first contract-authorized specialist execution (2026-11-10)

The [M3_SPEC.md](M3_SPEC.md) vertical slice, implemented against the
deterministic fake transport (the operator's first acceptance gate; the
bounded real-provider test follows it):

- **call_id ownership reconciled** (operator's review point): the Sequencer
  mints the call identity inside the t-a02 transaction — the `call_id` and
  its `attempt_calls` row are durably committed BEFORE any external launch,
  and no caller can supply or reuse one. `dispatch_attempt` returns the
  minted id, the provider-side idempotency key.
- **Schema v4** (D-70, additive): `attempt_witnesses` — one row per D-21
  structured admission entry (`witness_kind` = a registered WitnessRecord
  object id, `producer` = that kind's registered minter, `content_sha256` =
  the D-31 address of the exact bytes, `admitted_utc`, and the execution
  correlation read from `attempt_calls`), with an identity index making
  re-admission idempotent.
- **`Sequencer.admit_witness`** — the admission seam (D-21/D-38): exact
  producer bytes to the CAS, the entry journaled (`ch2:admit-witness`) in
  the same transaction, correlation ledger-derived (never caller-supplied),
  producers validated against the pinned principals/witness registries
  (`principals.py`, `witnesses.py`, cross-checked by
  `test_principals_registry.py`). Admission requires a bound, unfrozen
  execution; payload SEMANTICS stay Chapter 3's.
- **t-a06's `required_joins_present` is now a real join**: the receipt's
  `admitted_witnesses` must be D-21 structured entries, each matching an
  admitted row FOR THIS ATTEMPT — receipt entries ⊆ admitted rows; a
  receipt cannot introduce evidence the seam never saw.
- **PromptProjection** (`projection.py`, obj-10's minimal indexed form):
  a pure deterministic renderer binding the recomputed record tiers, the
  dispatched role and transform, the exact attempt identity and the
  attributed Chief Engineer guidance, with `authority_effect: none` stated
  in the artifact; same inputs, same content address.
- **ProjectState** (`project_state.py`): the Chief Engineer's read-only
  view — sequences by state, attempts with bound calls and witness counts,
  state-derived work eligibility, outstanding obligations (verification
  named as the gate after EVIDENCE_COMPLETE), in-doubt intents, permits,
  lease, journal head. No writes, no interpretation (D-63/D-64).
- **The transport contract and restart protocol** (`transport.py`):
  idempotent launch on the minted `call_id`; `find_session -> None` is
  UNOBSERVED, never FAILED — `reconcile_dispatched` adopts an observable
  session (admitting the provider's own record as the obj-11 witness,
  t-a03), fails only on the transport's positive determination (t-a10),
  and otherwise HOLDS the attempt in DISPATCHED. The deterministic
  `FakeTransport` (tests) implements the contract with injection controls;
  [TRANSPORT_GUARANTEES.md](TRANSPORT_GUARANTEES.md) grounds the real
  AutoSwarm mapping and names the one gap (native launch idempotency) with
  its closure designs.
- **The acceptance scenario** (`test_acceptance_m3.py`): the published 11
  criteria in one integration test, plus the adversarial arms — uncertain
  launch outcome held, hidden-session adoption, stale-authority dispatch
  refused, duplicate launch collapsed, missing-evidence receipts refused,
  and the t-a13 boundary still failing closed. The crash matrix gains the
  `running` stage; recovery pairs witness rows with their journaled
  admissions in both directions.

Still deferred: t-a12 (member-lease staleness), t-a07/t-a08 (verification,
M4), receipt completeness classification (the t-a13 seam, Chapter 3),
standalone effect machines, and the bounded real-provider test (the gate
after this one).

## Milestone 4 — verification authority and state transitions (2026-11-11)

The operator's M4 directive, implemented on the committed M3/G2 baseline
(the full disposition table and finding register are the
[RECONCILIATION.md](RECONCILIATION.md) M4 addendum):

- **The sequence walks beside its attempt**: t-s07 rides t-a05 inside
  `freeze_candidate` and t-s09 rides t-a06 inside `complete_evidence` —
  same trigger, evidence and fence, one transaction, two TransitionRecords
  — so evidence completion leaves the sequence VERIFYING. t-s09's join
  half rests on independently validated admission facts; its
  completeness-classification half is PARTIAL with Chapter 3, and the
  receipt's declared completeness field is never read in either direction.
  t-s45 rides t-a13 and shares the same Chapter 3 fail-closed seam.
- **Results enter only through typed admission seams** (schema v5
  `attempt_verifications`, one authoritative row per attempt and
  assertion): `register_verifier_result` admits the verifier_runner's
  obj-19/obj-20 bytes for DETERMINISTIC assertions — SEM-16 eligibility
  re-evaluated from the registered `service:*` requirements, the bound
  registration resolved or IntegrityHalt (INV-53), and a declared
  environment constraint matched against an obj-18 capture scope-bound to
  the exact attempt; `attest_assertion` admits your scope-bound obj-37
  `ManualAttestation.ASSERTION` (D-52) for HUMAN_ATTESTED assertions.
  Identical re-admissions are idempotent; conflicting ones never
  overwrite.
- **The registered outcomes**: `conclude_verification` commits t-a07 +
  t-s10 when every declared assertion passed or was attested (the
  attestation durable BEFORE the commit, by construction), or t-a08 with
  t-s11 (repair budget left: `repairs_used` against the frozen
  `repair_policy.max_repairs`) or t-s12 budget_exhausted.
  `halt_verification` applies t-s12's other registered arms (unknown,
  env_mismatch, bound_artifact_unavailable); the attempt holds in
  EVIDENCE_COMPLETE, and the HALTED resume (t-s13) stays yours. Durable
  HALTED state commits only through t-s12 itself — an admission refusal
  (ConflictError) and an INV-53 integrity stop (IntegrityHalt) mutate
  nothing, proven by journal-count tests.
- **t-a12**: `mark_member_stale` — the member lease recorded at t-a03
  expired for a REQUIRED role with no result; the sequence retries through
  the pre-existing t-s08.
- **Recovery** gains section 8c: verification rows pair with their
  journal entries in both directions, bytes re-hash, VERIFIED/REJECTED
  attempts must carry results, and `repairs_used` must equal the journaled
  t-s11 history. The crash matrix gains the `verifying` stage, restarting
  into a half-verified attempt and CONCLUDING it from durable state.

Proof of record: autobot 124/124 on CPython 3.8.10 and 3.12.11 (107
baseline + 17 M4 conformance tests), coordinator 41/41. Still deferred:
t-s13 (your resume), the t-a13/t-s45 classification authority (Chapter 3),
the verifier-runner implementation and receipt schemas (Chapter 4), and
finalization (M5).

## Reconciliation and progression

[RECONCILIATION.md](RECONCILIATION.md) is the row-by-row reconciliation of
milestone 2 against the frozen transition registry (2026-11-10, in-session
review): every t-i rule's enforcement point and test, the declared partial
arms (t-i05 positive-cancellation proof, t-i07 retry-permit), and the
deferred surfaces. [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) maps the full
engineering loop onto owners and sequences the remaining Chapter 2
milestones (M3 attempt execution + evidence seam, M4 verification
transitions, M5 finalization wiring SL-first, M6 acceptance/release/
ProjectState). The CORR-57 package (exact diff, compatibility proof
12/12 PASS under gate 20261110-ch2m2-007, conformance script) awaits the
operator's disposition in the chapter1-amendment-01 draft.

## Traceability

The ch2-owned invariants (traceability.json): INV-01, INV-02, INV-10, INV-11,
INV-12, INV-13, INV-25a, INV-42, INV-48, INV-60, INV-72. Each has a named
CH2-* test; the milestone-1 suite implements their first executable forms,
tightened as later milestones add the full paths (verification, effects,
acceptance, release).
