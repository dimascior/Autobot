# Finalization and real effect execution (M5, 2026-11-11)

The SL path — the smallest complete finalization arc — implemented against
the frozen Chapter 0 registries, driven by REAL EffectIntent outcomes over
a real local Git repository. The grounding below records every involved
row exactly as frozen; the conformance proof is
`tests/autobot/test_finalization.py` plus the `m5_post_effect` hard-death
stage in the crash matrix.

## The M5 transition surface (frozen rows)

| Row | From → To | Actor | Capabilities | Requires | Mints | Preconditions (registry verbatim) | Effect / class | PONR | Owner | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| t-s15a | VERIFIED → COMMITTING | sequencer | cap-37, cap-52 | — | obj-24, obj-25 | source_modifying, permit_reserved, delegation_active_at_intent, cas_state_version_attempt_fence, fence_matches, effect_intent_committed, intent_ttl_gt_hard_timeout | intent_for eff-local-canonical-cas (attempt_scoped) | no | ch2 | IMPLEMENTED (`begin_commit`: reservation + intent minted in the one transaction, the registered mints) |
| t-s16 | COMMITTING → COMMITTED | sequencer | cap-51, cap-37 | obj-26 | — | effect_happened, fence_matches, reservation_consumed | eff-local-canonical-cas | **YES** | ch2 (obj-26 payload schema: owning later chapter) | IMPLEMENTED (`commit_committed`: t-r01 consumes in the same transaction — the registered durable consumption point) |
| t-s18 | COMMITTING → READY | sequencer | cap-51 | obj-26 | — | canonical_ref_moved_expected_old_stale, reservation_released, fence_matches | observation of the same effect | no | ch2 | IMPLEMENTED (`commit_stale`: durable ABSENT with journaled observation target_other; new attempt required) |
| t-s19 | COMMITTING → VERIFIED | sequencer | cap-51 | — | — | effect_absent_per_outcome_rule, reservation_released | — | no | ch2 | IMPLEMENTED (`commit_absent`: durable ABSENT with the journaled fenced expected-old observation; same candidate re-mints) |
| t-i01..t-i05 | intent outcome machine | sequencer | cap-52 | — | — | registered observation vocabulary | outcome rule `local` | no | ch2 | PRE-EXISTING (M2), exercised over the real rule in M5 |
| t-i06 | UNKNOWN_OUTCOME → SUPERSEDED_BY_RETRY | sequencer | cap-52, cap-37 | — | obj-25 | retry identity + attempt_scoped checks + binds_superseded_intent | retryable effects | no | ch2 | PRE-EXISTING, exercised against the real effect |
| t-i07 | UNKNOWN_OUTCOME → HUMAN_RESOLVED | sequencer (trigger you) | cap-52 | obj-37 | — | authority_inactive + your attestation | — | no | ch2 | PRE-EXISTING |
| t-r01 | RESERVED → CONSUMED | sequencer | cap-37 | — | — | ponr_effect_happened, row_status_reserved_rowcount_1 | — | consumption point | ch2 | PRE-EXISTING primitive, coupled inside t-s16 |
| t-r02 | RESERVED → RELEASED | sequencer | cap-37 | — | — | pre_ponr_exit…, idempotent | — | no | ch2 | PRE-EXISTING, coupled inside t-s18/t-s19 |
| t-s20 | COMMITTED → ACCEPTED | sequencer | cap-43 | obj-26 | obj-30 | finalization_requirements_satisfied, fence_matches | — | no | ch2 | DEFERRED (M6 acceptance) |
| t-s15b, t-s17, t-s21.. | remote finalization arcs | — | — | — | — | — | — | — | ch2 | DEFERRED (remote paths after SL) |

Effect: `eff-local-canonical-cas` — LOCAL_REF_CAS on the canonical local
branch, executor `git_sequencer` (cap-38), requires_intent, receipt
obj-26, outcome rule `local`, intent class attempt_scoped.

Outcome rule `local`, as frozen: `target_equals_new` → HAPPENED;
`target_equals_expected_old_and_executor_fenced_and_no_ref_lock` → ABSENT
(a local ref update has no asynchronous server, so a fenced executor plus
the old value proves absence); `target_equals_expected_old_otherwise` →
UNKNOWN_OUTCOME; `target_other` → ABSENT.

## The executor contract (src/autobot/git_executor.py)

The first real effect executor (principal git_sequencer, cap-38):

1. begins only from `committed_intent()` — a durably committed PENDING
   intent inside its hard-timeout window (D-71);
2. verifies the effect kind and the full CAS identity (target,
   expected_old, new_value) plus the intent's sequence/attempt/fence
   bindings before touching anything;
3. executes exactly `git update-ref <target> <new> <old>` — Git's own
   atomic compare-and-swap — and nothing else;
4. observes the ref independently afterwards, classifies under the frozen
   rule, and feeds `resolve_intent` (the registered outcome machine);
5. mints the obj-26 CommitReceipt bytes on HAPPENED (git_sequencer is the
   registered minter).

`observe_and_resolve` is the restart re-entry: observation only, with the
caller's positive `executor_fenced` statement (process death under the
single-writer discipline). The executor grants no protocol authority: only
the Sequencer's registered transitions move the Sequence.

## PONR model and reservation timing

The external point of no return is the ref CAS itself. Its durable
representation: the intent is committed BEFORE the effect (t-s15a), the
outcome is committed by observation (t-i01), and the reservation's
consumption (t-r01: `ponr_effect_happened`, exactly one RESERVED row moves
once) is durable INSIDE t-s16's transaction — the registered point. An
interruption between any two steps leaves a durable state recovery
continues from; nothing is ever inferred.

## Crash/recovery matrix (the nine windows, by durable equivalence)

| Window | Durable state | Covered by |
|---|---|---|
| before intent commit | sequence VERIFIED, nothing minted | the state machine itself (begin_commit re-runs) |
| after intent commit / before executor / after invocation pre-mutation | COMMITTING + PENDING intent, ref unmoved | `test_restart_with_pending_intent_completes_by_observation` (in-process) |
| immediately before PONR | same as above | same |
| immediately after PONR / after mutation pre-observation | COMMITTING + PENDING intent, ref MOVED | `test_crash_m5_post_effect` (hard death, real git): restart infers nothing; observation alone resolves HAPPENED |
| after observation / before ledger transition | COMMITTING + HAPPENED intent, reservation RESERVED | `test_restart_after_happened_before_t_s16_completes` |
| after ledger transition | COMMITTED, reservation CONSUMED | happy-path + repeated `recover()` convergence |

The recovery contract distinguishes: definitely happened (observation
target_equals_new), definitely absent (fenced expected-old, or
target_other), and durable uncertainty (UNKNOWN_OUTCOME after expiry,
resolved only by later observation, retry t-i06 or your t-i07).

## Receipt ownership

obj-25 EffectIntent (minted t-s15a) ≠ the effect observation (journaled
facts on the intent transitions) ≠ obj-26 CommitReceipt (git_sequencer's
bytes, required by t-s16 and — as the observation receipt of a moved ref —
by t-s18; exact bytes preserved, identity-bound, re-hashed in recovery).
PublicationReceipt (obj-28), staging receipts (obj-27/41) and
AcceptanceReceipt (obj-30) belong to the deferred remote/M6 rows. obj-26's
payload SCHEMA stays with its owning later chapter: Chapter 2 owns the
reference handling and identity binding only.

## M5 findings

| id | Finding | Resolution |
|---|---|---|
| F-M5-1 | `_finalization_intent` selected the "latest" intent by created_utc, which ties when two intents mint in the same instant (frozen clock, same millisecond) | FIXED: insertion order (rowid) is the durable total order; proven by the t-s19 re-mint test |
| F-M5-2 | Accepted edge: if the permit-use reservation's lease expires between the PONR and t-s16, the sequence holds in COMMITTING with a HAPPENED intent and no SL exit row — the registered rows offer no consumption of a non-RESERVED reservation | Registry-faithful hold: bounded by the 7200 s default lease and `renew_reservation` (INV-60); visible in project_state (`committing` + the intent); no invented transition |
| F-M5-3 | Accepted: `committed_intent()` is a read outside the mutating transaction, so the authority window between the read and the external CAS is bounded by the hard timeout and the single-writer discipline, not by a lock | Matches D-71's design; recorded as the executor contract's operating assumption |
