# Milestone 2 reconciliation against Chapter 0/1 (2026-11-10)

Direct evaluation of the `chapter2-effect-intents` implementation against the
frozen registries, carried out in-session per the standing review procedure.
Suite evidence: 72/72 under PATH Python 3.8.10 and the repo venv 3.12 (gates
20261110-ch2m2-003/005/006). The table below is the row-by-row claim; every
CONFORMS row names its enforcement point and its test.

## EffectIntent transitions (chapter0 transitions.json)

| Registry row | Implementation | Status |
|---|---|---|
| t-i01 PENDING→HAPPENED, `target_observed_at_new_value` | `resolve_intent`: observation routed through the effect's registered outcome rule; HAPPENED applies from PENDING with no expiry condition | CONFORMS (`test_happened_from_pending`) |
| t-i02 PENDING→ABSENT, `absence_proven_per_outcome_rule` | same routing; only observations the rule maps to ABSENT qualify | CONFORMS (`test_absent_from_pending`) |
| t-i03 PENDING→UNKNOWN_OUTCOME, `expired_and_target_at_expected_old_or_unobservable` | an UNKNOWN-mapping observation moves PENDING **only once `expires_utc` passed**; before that PENDING holds and nothing is journaled. `expire_intents()` is the sweep arm (observation `unobservable`) | CONFORMS (`test_unknown_requires_expiry_from_pending`, `test_expiry_sweep_moves_only_expired`) |
| t-i04 UNKNOWN→HAPPENED | rule-routed | CONFORMS (`test_unknown_requires_expiry_from_pending`) |
| t-i05 UNKNOWN→ABSENT, `stale_expected_old_observed_or_positive_cancellation_proof` | the stale-observation arm is the rule table's ABSENT mappings (`target_other`; local's fenced-old case). **The positive-cancellation-proof arm is NOT implemented** — it needs a transport-level proof carrier that does not exist before the executor milestone | PARTIAL, declared (`test_absent_from_unknown`) |
| t-i06 UNKNOWN→SUPERSEDED_BY_RETRY, mints obj-25; `retry_same_effect/target/expected_old`, `delegation_active_at_intent`, `reservation_held`, `fence_matches`, `effect_intent_committed`, `intent_ttl_gt_hard_timeout`, `binds_superseded_intent`; retryable set only | `retry_intent`: successor copies effect/target/expected_old/new_value from the durable row (schema v2 columns exist for exactly this), full `_attempt_scoped_context` re-runs, successor committed in the same transaction, `superseded_intent_id` binds, `RETRYABLE` pinned to the registry | CONFORMS (`test_retry_chain`, `test_retry_requires_live_reservation`; pin: `test_retryable_split_matches_registry`) |
| t-i07 UNKNOWN→HUMAN_RESOLVED, trigger you, requires obj-37; `authority_inactive`, `your_effect_outcome_attestation_or_retry_permit` | `resolve_intent_by_attestation`: refuses while the bound delegation is active (t-d02 expiry = v1 inactivity; revocation machinery is a later milestone), validates a minimal v1 EFFECT_OUTCOME attestation authored by you. **The retry-permit arm is NOT implemented.** Attestation preserved by D-31 content address pending CORR-57 (no object hash exists under the frozen kind grammar) | PARTIAL, declared (`test_t_i07_requires_inactive_authority`, `test_t_i07_resolves_with_attestation`) |
| machine shape (states, initial, terminals) | pinned to states.json | CONFORMS (`test_effect_intent_machine`) |

## Registered effect classes and outcome rules

- The six effects, their outcome rules, receipts, classes and `canonical_on`
  sets are data in `effects.py`, equal to the registry projection
  (`test_effects_match_registry`); every effect `requires_intent` (INV-75
  spot-check in the same test).
- The four outcome rules are byte-equal to the registry minus its prose
  notes (`test_outcome_rules_match_registry`); `resolve_intent` refuses any
  observation outside the effect's rule (closed vocabulary,
  `test_unknown_observation_vocabulary_is_closed`).
- attempt_scoped checks: `delegation_active_at_intent` (bound payload's
  expiry read from the ledger), `reservation_held` (live RESERVED row with
  an unexpired INV-60 lease — the stricter member of the
  permit_reserved/reservation_held alternative group), `fence_matches`
  (attempt fence = sequence fence; dead attempt states refused),
  `intent_ttl_gt_hard_timeout` (SEM-12/INV-67, also a durable check at
  recovery), `effect_intent_committed` (the `committed_intent()` boundary:
  PENDING + inside hard timeout, read after durable commit). Check list
  pinned (`test_attempt_scoped_checks_match_registry`).
- Standalone classes (staging_delete, workspace_sync): **DEFERRED,
  declared** — they mint through t-g01/t-w01, whose machines land with the
  Chapter 6-adjacent milestones. `mint_intent` refuses them explicitly.

## Retry and recovery behavior

- Retry identity is durable: schema v2's `target`/`expected_old`/`new_value`
  columns exist so `retry_same_*` is checked against committed facts, not
  caller claims; recovery re-verifies the whole chain in both directions
  (successor binds a SUPERSEDED_BY_RETRY prior with identical
  effect/target/expected_old; every superseded intent has exactly one
  successor) and HALTs on breakage (`IntentRecoveryGuards`).
- Recovery contract: intents join the journal↔row reconciliation (a t-i06
  successor's default state is PENDING, since its mint is recorded in the
  predecessor's entry); windows must satisfy INV-67 durably; PENDING and
  UNKNOWN_OUTCOME intents are reported as the in-doubt set the restart
  resolves by observation, never by inference (D-69).
- Crash proof: stages `intent` (committed intent survives exactly once,
  resumable to resolution) and `mid_intent_tx` (a torn intent transition
  vanishes whole) in the subprocess matrix (`test_crash_intent_stages`).
- A refused database open (D-70 SchemaTooNew) closes its connection — the
  leak the 3.12 run exposed, fixed and re-proven on that interpreter
  (ch2m2-004 → ch2m2-006).

## CORR-57 (cross-chapter compatibility)

The frozen D-29 kind grammar `^[A-Za-z][A-Za-z0-9.]*$` cannot express the
registered kind `ManualAttestation.EFFECT_OUTCOME`. The prepared correction
package, with the exact diff, compatibility proof and conformance tests for
operator disposition, is drafted at
`E:\Users\dimas\autobot-design\chapter1-amendment-01\` (summary in
README §Finding). Until disposition, t-i07 preserves attestations by exact
bytes (D-31) referenced from the journal entry — nothing in the frozen
trees was altered.

## Closure pass (2026-11-10)

The final integrated review before the commit candidate, with one focused
correction and the M3-preparation slice:

- **Correction applied**: `_attempt_scoped_context` now refuses a terminal
  Sequence outright — previously a forged terminal Sequence whose last
  attempt still matched the fence could have minted intents
  (`test_terminal_sequence_grants_no_intent_authority`).
- **Attempt execution slice IMPLEMENTED** (t-a02, t-a03, t-a04, t-a05,
  t-a06 and the t-a10/t-a11/t-a13 failure arms) through authoritative
  Sequencer interfaces: schema v3's `attempt_calls` binding (role,
  provider, call_id, projection reference, native session id; one
  execution per attempt by primary key), per-role completion policies read
  from the frozen record (success → t-a04, required-role non-success →
  t-a11), fence_matches in the mutating SQL where registered (t-a05/t-a06
  carry the correlated sequence-fence equality), obj-10/11/15/16 as
  content-addressed references that must resolve, t-a13 gated on the
  receipt's own INTEGRITY_FAILED/INCOMPLETE class (INV-68), and recovery
  pairing both ways (execution state ⟺ bound call; committed projection
  reference must resolve). The `dispatched` crash stage proves a restart
  reconstructs the execution relationship from durable state and resumes.
  Still DEFERRED: t-a12 (STALE, needs the member-lease semantics of the M3
  evidence seam), t-a07/t-a08 (M4 verification), deep witness joins
  (required_joins_present is v1 resolvability until the admission tables).
- **Operator-identified corrections, final pass (2026-11-10)** — two real
  t-a13 precondition gaps my closure review missed:
  1. *fence_matches was not enforced on t-a13.* The frozen row registers
     it; `fail_candidate` now takes the presented fence, checks the
     attempt↔sequence fence agreement, and carries the correlated
     sequence-fence equality in the mutating SQL exactly as t-a05/t-a06
     do. Adversarial proof: a stale fence is refused and leaves both the
     attempt state and the journal unchanged
     (`test_t_a13_stale_fence_changes_nothing`).
  2. *incomplete_beyond_bound was collapsed to incomplete.* The registered
     condition distinguishes an INTEGRITY_FAILED receipt (final, fails at
     once) from an INCOMPLETE receipt, which fails the candidate only once
     its permitted completion bound has ELAPSED. The bound is the
     receipt-declared `evidence_complete_by_utc`; HOW it derives from
     D-21 admission facts is Chapter 3's receipt semantics, so an
     INCOMPLETE receipt declaring no bound is refused as unevaluable —
     that arm is PARTIAL until Chapter 3 supplies it. Premature incomplete
     evidence never fails the candidate
     (`test_t_a13_incomplete_requires_elapsed_bound`).
- **Classification summary**: contract admission t-c01..t-c04/t-c07,
  sequence t-s01/t-s03/t-s06/t-s08, attempt t-a01..t-a06/t-a09..t-a11,
  permit t-r01/t-r02, intents t-i01..t-i07 — IMPLEMENTED with named
  tests. PARTIAL, declared: t-a13 receipt-classification authority
  (fail-closed: the completeness and deadline fields are structurally
  present but not authoritatively established until Chapter 3's
  receipt-admission mechanism exists), t-i05 cancellation-proof arm,
  t-i07 retry-permit arm. DEFERRED, declared: t-c05/t-c06 (queued-freeze
  triggers), t-a12, remaining sequence machine, standalone effect
  machines, wildcards.
- **Proof of record** (exact counts, after the final correction pass):
  autobot suite **84/84 OK** under PATH CPython 3.8.10 (gate
  20261110-ch2m2-011) and under the repo venv CPython 3.12.11 (ch2m2-012);
  coordinator regression **41/41 OK** under the venv (ch2m2-013); mirror
  identity and golden consistency inside the 84; CORR-57 conformance
  **12/12 PASS** from isolated copies (ch2m2-007). Earlier green runs of
  the same chain: 82/82 + 41/41 at ch2m2-008/009/010. Gate chain for the
  milestone: 20261110-ch2m2-001..013.

## Authority-boundary pass (2026-11-10)

Establishes the explicit, enforceable boundary between content-addressed
evidence (CAS resolution, D-66) and authoritative receipt conclusions
(INV-68/INV-69) on both receipt-consuming paths (t-a06 and t-a13).

- **`_resolve_receipt` added** (sequencer.py): a two-step receipt
  resolution that both `complete_evidence` (t-a06) and `fail_candidate`
  (t-a13) now use.  Step 1 resolves the receipt's content address in the
  CAS (D-66 re-hash).  Step 2 validates the receipt's authoritative
  identity: (a) `kind == "ExecutionReceipt"` (INV-68 identity),
  (b) `attempt_id` matches the durable attempt row (INV-69 binding),
  (c) `attempt_contract_hash` matches the durable contract (INV-68
  integrity — now required, not optional).
- **t-a13 declared PARTIAL (fail-closed)**: operator code-level review
  identified that the previous implementation trusted a receipt's
  self-declared `completeness` and `evidence_complete_by_utc` fields
  without establishing their authority.  An INTEGRITY_FAILED
  classification requires Chapter 3's receipt-admission mechanism; an
  INCOMPLETE deadline must derive from an authoritative policy/window
  (D-28).  `fail_candidate` now raises at the authority boundary before
  the completeness classification logic, which is preserved for Chapter 3.
  The classification logic (INTEGRITY_FAILED immediate failure,
  INCOMPLETE with elapsed-bound evaluation, unsupported-classification
  refusal) remains in the source below the boundary for Chapter 3 to
  activate when it supplies the receipt-admission authority.
- **Adversarial tests** (test_attempts_execution.py):
  - `test_t_a13_rejects_unbound_receipt`: wrong attempt_id → refused at
    identity boundary, state preserved.
  - `test_t_a13_rejects_wrong_contract_hash`: forged contract hash →
    refused, state preserved.
  - `test_t_a13_rejects_wrong_receipt_kind`: CAS object that is not an
    ExecutionReceipt → refused, state preserved.
  - `test_t_a06_rejects_unbound_receipt`: the evidence-completion path
    enforces the same identity boundary.
  - `test_receipt_requires_contract_hash`: receipt omitting
    attempt_contract_hash → refused at INV-68.
  - `test_t_a13_forged_past_deadline_wrong_binding`: forged deadline +
    wrong attempt_id → caught at binding check.
  - `test_t_a13_correct_binding_forged_classification`: correct binding
    + INTEGRITY_FAILED → caught at the PARTIAL authority boundary, not
    at the classification logic.
  - `test_t_a13_correct_binding_forged_deadline`: correct binding +
    INCOMPLETE + elapsed deadline → caught at the PARTIAL authority
    boundary, not at the deadline comparison.
  - `test_integrity_failed_candidate_is_partial`: INTEGRITY_FAILED with
    correct binding → authority boundary, state preserved.
  - `test_t_a13_incomplete_is_partial`: INCOMPLETE with elapsed bound
    and correct binding → authority boundary, state preserved.
- **M3 execution integration preserved**: the durable `call_id`,
  PromptProjection binding, AutoSwarm transport interface, and D-21
  witness-admission specification are unchanged.
- **M3 specification carry-forward**: `find_session(call_id)` returning
  `None` is not positive proof that an external launch failed; recovery
  must preserve an uncertain launch outcome until the transport
  establishes it (noted in INTEGRATION_PLAN.md).

## Journal honesty

Only real machine transitions carry `t-…` rule ids (t-i01..t-i07 inclusive);
the mint uses `ch2:mint-intent` because the registered minting transitions
(t-s15a/b, t-s21, t-s31/32) are sequence-machine rows this milestone does
not reach. No journal entry claims a protocol transition that did not occur.
