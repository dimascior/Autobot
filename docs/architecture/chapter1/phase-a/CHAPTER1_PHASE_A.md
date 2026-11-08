# Chapter 1 Phase A: the contract surface (frozen 2026-11-08)

**Phase:** A, discovery. **Status:** frozen 2026-11-08, on your acceptance. Nothing is decided: the decision candidates are Phase B's, and U-44 and U-47 are open Chapter 0 correction candidates. No canonical bytes or golden hashes exist yet. **Upstream:** Chapter 0 on merged main `9cc0f5b2cb752c5d817d6e7c50c7da7d59700057` (tree `be36025f`, handoff `e256ccc4`).

Chapter 0 said who has authority, which objects exist and how proof flows. Chapter 1 will say what those objects are, down to the byte, type, version and hash. Phase A maps the ground that answer has to cover, without answering it.

**Precedence.** The canonical form is the JSON in `registries/`. This document explains it; `CHAPTER1_PHASE_A_REGISTERS.md` lists all of it and is generated. `check_phase_a.py` checks the registries against Chapter 0 and the HEA corpus, and `test_phase_a_negative.py` proves the checks fail when they should.

## 1. Entry: the exact baseline

Gate `20261108-ch1a-002` proved six links against git objects in the Autobot checkout:

1. main is `9cc0f5b` on GitHub, locally, and in the checkout.
2. main's parents are `2d8427c` and `09d608c`; `09d608c`'s parent is `cd67bfc`; `cd67bfc`'s is `2d8427c`. main's tree equals `09d608c`'s, and `git fsck --full` is clean.
3. The committed handoff hashes to `e256ccc4`, and the committed sidecar names exactly that.
4. The committed `latest.json` names chapter 00, phase C, `ch00-phase-c.json`, with that hash.
5. On files rebuilt from main's blobs, all 19 recorded hashes match, `check_registries.py` passes 36 of 36, and `test_checker_negative.py` catches 11 of 11.
6. The working tree is byte-identical to that commit, with a clean status.

Every run of the checker re-verifies all 18 Chapter 0 files in the committed handoff, and every cited CLOSURE or ARCHITECTURE section must exist as a heading (`baseline.chapter0`).

## 2. Method

- **Your seven steps**, in order. The last one is the checker.
- **Every statement in the brief is classified** (`claims.json`) as CH0-required, HEA-derived technique, Phase-A proposal or unresolved, and gets a disposition. REJECTED, METHOD_ONLY and PENDING_DECISION claims stay as audit history, but nothing may rest on them: the checker refuses any schema rule, vector, coupling or decision basis that cites one (`claims.normative`).
- **HEA evidence is split in two.** `hea_techniques.json` records what HEA proved; each citation is a file whose hash is recorded, a line range, and a quoted phrase the checker finds in those lines. `decision_candidates.json` records whether and how a technique applies to an Autobot object. No candidate's basis cites HEA.
- **Unknown IDs are durable.** U-10 and U-18 keep Chapter 0's exact text. New questions start at U-19. When a facet is split out of an unknown, the unknown keeps its earliest definition as its question, becomes NARROWED, and states its remaining scope; the facet gets a new ID that names its parent and the review entry that split it. A refinement that keeps the question is recorded beside it.
- **An independent review** read the whole draft after your third review. Each of its 23 findings was checked against the sources before anything changed, and the log records which held (RV-25 to RV-47).

## 3. What Chapter 0 already fixes

- **Scope** (CLOSURE §6): golden hashes for all three identity tiers, the registration and transform schemas, and U-10 and U-18.
- **Four runtime tests assigned to Chapter 1:** CH1-golden-digest-stability (INV-05), CH1-exact-byte-input-hash (INV-21), CH1-scope-check (INV-28) and CH1-duplicate-admission (INV-40). The brief names only the first two. The other two need the DelegationContract's scope fields and the DuplicateAdmissionRecord in Chapter 1.
- **Identity:** 9, 16 and 7 fields; nine required placements; three exclusions; JCS and full SHA-256 (D-09).
- **Five freeze-time bindings:** the repository declaration, the verifier and transform registrations, the delegation and its floor.

## 4. The 42 objects

| Schema form | Count | Objects |
|---|---|---|
| Independently versioned protocol object | 25 | the eight authority records, the two contracts, the thirteen receipts, and the two non-authoritative records |
| Embedded contract part | 4 | obj-05, obj-06, obj-07, obj-17 |
| Ledger state row | 6 | obj-08, obj-23, obj-24, obj-25, obj-33, obj-39 |
| Append-only journal record | 2 | obj-32, obj-36 |
| Raw witness with admission | 4 | obj-11 to obj-14 |
| Legacy read-only | 1 | obj-40 |

**Required in Chapter 1** (10): obj-00, obj-02, obj-03, obj-04, obj-05, obj-06, obj-07, obj-17, obj-18 and obj-32. The rest are classified now and fully specified later (U-19).

Each protocol object has an object hash over its canonical bytes. Whether that hash carries a domain label or a type binding is open (U-28, U-30, U-53).

**Bindings are conditional where Chapter 0's contexts differ.** Each object lists the bindings it always carries, then its binding rules:

```text
BindingSet(record) = common_bindings
                   ∪ the bindings of every rule whose complete when predicate holds for the record
```

- **Fixed when the record is produced.** A record is immutable after commitment (R-2), so every transition that later consumes it sees the same set.
- **Within one rule,** every selector in `when` must hold (AND). The selectors are path, effect, effect_result, kind and finalization_class, and a list of values in one selector means any of them.
- **Across rules,** every rule that holds adds its bindings. Rules are additive conjunctions, not exclusive variants of a record.
- **Within a partition** (the rules conditioned on the same selectors), every Chapter 0 context of the record resolves to exactly one rule. An RO AcceptanceReceipt matches the RO rule in its path partition and the READ_ONLY rule in its finalization_class partition, and inherits both.
- **Empty and open rules.** A rule with no bindings says explicitly that it adds nothing; an open rule cites the unknown that asks.

A rule may also list its **provenance**: the Chapter 0 transitions it covers, the ones that mint, prove or consume the record in a context the rule matches. Provenance sits outside `when` and is not a wire field. It never decides whether a rule holds, so a record's bindings can't depend on which transition consumes it. Where a rule lists provenance, the checker requires the list to be exact.

`effect_result` says what happened to the effect a receipt records: HAPPENED, or EXPECTED_OLD_STALE when the compare-and-swap found the ref moved. The transition whose own preconditions establish it says which. It is not an EffectIntent state; the six states are unchanged.

What a receipt records is kept apart from the transitions that consume it. t-s16 proves the SL canonical commit: it names eff-local-canonical-cas as its own effect, establishes HAPPENED, and is the point of no return. t-s20 performs no effect and isn't a point of no return; it consumes the receipt t-s16 established, so it sees the same record with the same bindings.

- The CommitReceipt (obj-26) has three rules (your decision on RV-25, RV-48, and your review of it, RV-49). Its common bindings are the candidate, the attempt, the intent and the ref facts: the expected old and new values, and the observed outcome.

  | Rule | when | provenance | binds |
  |---|---|---|---|
  | A | SL, eff-local-canonical-cas, HAPPENED | t-s16, t-s20 | committed tree, consumed reservation, delegation state at the point of no return |
  | B | REMOTE, eff-local-sequence-ref, HAPPENED | t-s17 | committed tree |
  | C | SL, eff-local-canonical-cas, EXPECTED_OLD_STALE | t-s18 | released reservation |

- The reservation is three binding facts: held while an intent is live, consumed at a point of no return, and released on an exit before one.
- The AcceptanceReceipt (obj-30) is minted on all seven paths. It is the point-of-no-return receipt only on RO, RUD and RUR, and it binds an observation window only for READ_ONLY contracts.
- The RemoteVerificationReceipt (obj-21) checks the staged commit on SRP and the published commit on SRPO.
- The CandidateReceipt (obj-16) binds its artifact set for every contract, since every RequirementDigest has artifact paths, and a source tree for source-modifying contracts.
- A DependencyEdge (obj-07) binds its target by declared tier: one record, or any record with the same ContractDigest or RequirementDigest.
- The ManualAttestation (obj-37) binds an assertion or an EffectIntent, by kind.
- An EffectIntent (obj-25) for the four attempt-scoped effects binds what its creating transitions check: the attempt and fence, the held reservation, and the delegation at intent (t-s15a, t-s15b, t-s21, t-s31, t-s32). The two standalone effects need an intent, but Chapter 0 has no transition that creates one, so their rules are open, and U-44 is a Chapter 0 correction candidate. t-i06 also mints an intent, a retry for the identical effect, outside any initiation rule. Chapter 0 gives no field that marks a retry, so it falls under its effect's rule. That rule binds the attempt, the delegation at intent and the intent window, and t-i06 checks none of them. U-47 is the second candidate (section 10).

The checker enforces these rules:

- **Your three assertions.** For every transition that mints, proves or consumes a conditionally bound receipt, on each path, the checker derives the context Chapter 0 gives it. Each context keeps two things apart:
  - **the transition:** its role (mints, proves or consumes), its own effect and its own point-of-no-return flag;
  - **the record's facts,** which are the only thing rules select on: path, effect, effect result, finalization class, and whether it records a point of no return that happened, with the transitions that established them.

  A transition proves an effect only where Chapter 0 names it as its own and its preconditions establish the result. A consumer that names no effect takes its receipt's facts from the transition upstream that established them, so t-s20 is credited with neither the effect nor the point of no return. Then:
  1. every context resolves to exactly one rule in each partition, and every rule that isn't open matches a context (`inventory.contexts`);
  2. every receipt that records a point of no return binds the delegation state, plus the consumed reservation where that point consumes one. The requirement comes from the transition that established the receipt, so t-s20's view is checked against t-s16's (`inventory.ponr_receipts`);
  3. no other receipt context inherits either binding (`inventory.ponr_receipts`).
- **Provenance stays apart** (`inventory.provenance`):
  - no rule selects on a transition;
  - every provenance list is exact;
  - a consumer is never credited with an effect;
  - every way into a consumer's state passes the transition that established its receipt, not just some way.
- A minted receipt binds every receipt its minting transition requires, in each context it is minted in.
- Every effect's receipt binds its intent.
- The reuse conditions are exactly the six preconditions of t-s43 and t-s44.
- Every effect that requires an intent closes in Chapter 0, and every transition that mints an intent is an initiation rule for a named effect, or the gap waits on an open Chapter 0 correction candidate that covers it (`effects.intent_closure`).
- An intent binds what its creating transition checks and nothing it doesn't. This covers every transition that mints one, and t-i06's unchecked bindings wait on U-47 (`inventory.intents`).
- Every object the Sequencer or the Registry mints has a minting transition or a Chapter 0 lifecycle rule, never both (`inventory.creation`).
- Every authority record cites U-34 and U-46, because INV-26 says each one names a principal.

**Four objects have no minting transition.** The Sequencer mints the Sequence (obj-08), the StandingPermitState (obj-23), the TransitionRecord (obj-36) and the InstanceLease (obj-39), but no Chapter 0 transition mints them. Each records how Chapter 0 creates it (`created_by`), citing Chapter 0 text that names the object: one Sequence per TaskContractRecord (INV-40), permit counting (INV-48, D-03), the journal (ARCHITECTURE §15, INV-10), and the instance lease (INV-25a). The Sequence's rule is a coupling with its record on INV-40, not a mint of t-c02. Chapter 0 names no trigger for the permit counters or the instance lease, and leaves both to Chapter 2 (ARCHITECTURE §17).

**Three object types hold two record kinds each** (obj-03, obj-37, obj-38). A tagged union keeps Chapter 0's 42 types; a correction would be needed only if the object or minter taxonomy itself split (U-20).

**Two identities are undecided.** Chapter 0 sets no identity policy for the LegacyRecord (U-43) or the DuplicateAdmissionRecord (U-22, U-30).

## 5. Identity fields and couplings

All 32 fields carry their Chapter 0 tier, which is checked against `identity.json`, plus a proposed semantic type and the unknowns that keep the wire form open. Every set-shaped field waits on U-25. A reference is a typed identifier, typed record hash or typed subobject reference to a governed entity, with absence written only by the rule you decide (U-40).

Ten couplings tie fields that can't change alone:

- Seven are stated in Chapter 0: K-1 (the declaration's authority location, INV-45), K-2 and K-3 (bound registrations, INV-53 and the t-c02 preconditions), K-4 (reg-floor), K-7 (finalization fits the authority location, INV-45), K-8 (the floor is among the assertions, INV-34) and K-9 (the paths lie inside the delegation's scope, INV-28).
- K-6 follows from the paths.
- K-5 waits on U-37, and K-10 on U-51, which asks what t-c02's "matching" covers beyond INV-45.

## 6. Golden vectors and predicate tests

Of the 19 vectors:

- **12 have their tier effect specified.** The checker derives which tiers move from `identity.json`. A vector that changes one field of a coupled pair either changes both fields or states why the pair stays valid.
- **1 is a specified rejection.** V-S changes authority_location while keeping the declaration bound, and the freeze must refuse it.
- **None is executable.** Every fixture waits on the hash construction (U-28), the tier preimages (U-30) and the digest text encoding (U-53). Each vector also names the unknowns open on the fields it changes, and the checker derives that list from the identity fields. A fixture is a full record, so it also waits on the fields it leaves unchanged.
- **6 are blocked.**

Corrections from the reviews:

- **V-O** is a hypothetical RecordHash preimage test, not an admission.
- **V-M** starts from V-A: REMOTE to LOCAL, with the new declaration bound and finalization requirements that fit LOCAL (K-7).
- **V-P** states that the new delegation's floor is among the contract's assertions (K-8) and that its scope contains the contract's paths (K-9), so the RequirementDigest stays the same.
- **P-2** states both admission outcomes. A duplicate creates no record and no Sequence, mints one DuplicateAdmissionRecord, and returns the existing record with its existing RecordHash. A new admission mints exactly what t-c02 mints; its Sequence comes from INV-40's one-for-one coupling with the record, since no transition mints obj-08. Both are checked against t-c02, t-c03, INV-40 and INV-54. A payload can also wait for you (t-c04) or be rejected (t-c06, t-c07); those outcomes are outside P-2.
- **V-Q** is blocked on U-37: it becomes valid or is deleted once U-37 decides how delegation_id relates to the bound record.
- **V-G** changes only the out-of-band resolution path, never a payload field.

P-1, the scope check, waits on U-26 and U-37.

## 7. Corrections to the brief

The checker counts 2 claims rejected and 21 partly true: 18 of those may be cited in their corrected form, and 3 are proposals still waiting on a decision (C-14, C-51, C-54). All stay in `claims.json` with what corrects them:

- **EffectIntent states (C-55, rejected).** EffectIntent has six states, not four: Phase C added SUPERSEDED_BY_RETRY and HUMAN_RESOLVED.
- **Python closure attribution (C-79, rejected).** The Python closure of script, interpreter and lock is Autobot's own Chapter 0 rule, not an HEA precedent.
- **The two unnamed Chapter 1 tests (C-06).** The brief doesn't name CH1-scope-check or CH1-duplicate-admission.
- **Binding gaps (C-67 to C-70).** The binding lists omitted the delegation state at the point of no return and the sixth reuse condition. They also treated receipts as having one context.
- **Authority windows (C-40).** Audit instants such as frozen_utc never order authority, but instants that end an authority window do gate it: t-d02 expires a delegation once now reaches its expires_utc. U-27 keeps the audit instants, U-39 the windows and the instants that end them.
- **HEA wire format (C-54).** HEA's domain types use an enum, but its wire format keeps a sentinel, so tagged variants on the wire are not HEA-proved.
- **HEA's unknown-field rule (C-57).** HEA never rejected unknown fields everywhere: it was strict for signed payloads and permissive for evidence (T-07).
- **Version categories (C-51).** HEA tested three refusal categories, not the brief's five. The five now span two unknowns: version recognition (U-31) and compatibility policy (U-45).
- **HEA as a basis (C-74, C-75).** HEA proved the techniques, but carrying each one is its own candidate. Chapter 0 doesn't require signing, and whether a record carries proof of authentication is open (U-46).
- **Typed identifiers (C-72).** Binding by immutable record and hash is Chapter 0's; typing the identifiers is a proposal.

## 8. HEA: proved techniques and what isn't carried

There are 24 techniques and 1 lesson. Every quoted phrase is found in its cited lines, in a file whose hash matches. The independent review narrowed six descriptions to what their lines show (RV-32): for example, HEA binds the type into its signature input, not its digest (T-20), and its strict mode is for signed payloads (T-05, T-06). The lesson (T-25): at f439f52, HEA's documents disagree with its own label registry and its test counts, so one machine-readable source, with documents checked against it, is the safer form.

Not carried:

- signing and key identities (Chapter 0 doesn't require signing; whether a record carries proof of authentication is open, U-46);
- digest agility (D-09 fixes SHA-256);
- generation as the ordering counter;
- HEA's own identifier sizes, sentinels and ranges;
- Rust.

## 9. Unknowns and decision candidates

U-10 and U-18 keep Chapter 0's text, and the other 16 Chapter 0 unknowns carry unchanged. U-06 also fixes the values completion_policies is written in, so that field cites it. There are 37 new unknowns, U-19 to U-55, none resolved.

- **Seven are NARROWED**, because facets were split out of them: U-27 (U-39 split out), U-28 (U-53), U-29 (U-54), U-31 (U-45), U-32 (U-40 and U-41), U-34 (U-46) and U-37 (U-52). Each keeps its first question and states what remains.
- **Two are Chapter 0 correction candidates:** U-44 and U-47 (section 10).
- **The independent review raised six new questions:** U-47 (retry intents), U-48 (receipts of effects that didn't happen), U-49 (eligibility), U-50 (attestation bindings), U-51 (freeze-time matching) and U-55 (your two recordless decisions).

Each unknown has exactly one candidate, so no decision forces another. There are 43 candidates: one per unknown, plus four method candidates (dc.test-oracles, dc.contract-isolation, dc.handoff-chain and dc.id-namespaces). The candidates for U-44 and U-47 decide whether to append corrections to Chapter 0, not Chapter 1 rules. None has a decision ID. decide_with marks six pairs best decided at one sitting.

To make the first golden vector, V-A, executable, Phase B must decide the candidates for its unknowns and for the ones every fixture waits on. The checker compares this list with the registries:

<!-- va-candidates -->
- dc.hash-domains, dc.tier-preimages and dc.digest-encoding, for every fixture;
- dc.identifier-model, dc.repository-identity and dc.paths-and-refs;
- dc.collections, dc.enums and dc.text;
- dc.content-addressing and dc.spec-bytes, for the specification inputs;
- dc.python-environment, for the assertions' environment constraints;
- dc.eligibility, in case eligibility must enter the RequirementDigest.
<!-- /va-candidates -->

To run the two predicate tests it also needs dc.reference-fields, decided with dc.absence-representation.

## 10. The Chapter 0 correction candidates (U-44, U-47)

Your third review found that U-44 is not an ordinary Chapter 1 question. Two effects, eff-staging-delete and eff-workspace-sync, require an intent, but no Chapter 0 transition creates one and none proves either effect. The other four effects close:

- an initiation transition mints their EffectIntent (t-s15a, t-s15b, t-s21, t-s31, t-s32);
- the intent can reach HAPPENED, ABSENT and UNKNOWN_OUTCOME from PENDING (t-i01, t-i02, t-i03);
- a transition that requires the effect's receipt follows HAPPENED (t-s16, t-s17, t-s22, t-s33).

Chapter 0's own checker can't see the gap. effects.definitions exempts standalone effects from needing a transition that performs them, and effects.require_intent starts only from transitions (RV-18).

The proposed correction is yours to decide. Phase A doesn't apply it:

- **Keep** INV-55, the EffectIntent (obj-25) with its six states, and the 42 object types with their minters.
- **Refine** INV-47 toward: EffectIntent creation and the effect-specific authorization checks commit atomically immediately before the effect. ARCHITECTURE §9 lists one set of intent-time checks for every effect (delegation, permit, fence, version, timeout); they would be stated per effect class.
- **Add** an initiation rule for each standalone effect that mints an EffectIntent bound to that exact effect, and a Chapter 0 checker property that every effect requiring an intent closes as above.
- **Acknowledge** each effect's authority inputs, with their exact rules decided in Chapter 6:
  - staging delete: an eligible staging ref, an ACCEPTED sequence or expired retention (D-16), the expected ref value, cleanup authority from frozen policy, and a bounded intent;
  - workspace sync: a safe workspace target, an established canonical source, the worktree safety predicate (INV-46), and a bounded intent.

**U-47, from the independent review.** t-i06 resolves an UNKNOWN_OUTCOME by retrying the identical compare-and-swap under a fresh EffectIntent. It names no effect (intent_for), so Chapter 0's intent checks don't reach it, and it lacks effect_intent_committed, intent_ttl_gt_hard_timeout and fence_matches. The fence may not need checking: no wildcard can bump it while an intent is UNKNOWN_OUTCOME (w-s01, w-s02 and w-s03 require no_pending_or_unknown_intent).

Since rules select on protocol fields only (RV-50), a retry intent falls under its effect's rule. That rule binds the fence's attempt, the delegation at intent and the intent window, and t-i06 checks none of them (RV-52). `inventory.intents` reports this, and lets it pass only while U-47 covers t-i06. The correction can close it in either of two ways: give t-i06 those checks, or give the retry a protocol field, such as the intent it supersedes, that a separate rule can select on. The proposed correction:

- **keeps** t-i06 and the six states;
- **refines** INV-47 so that every transition that mints an intent, a retry included, commits it with the effect's checks;
- **adds** a Chapter 0 checker property that checks every transition minting obj-25.

It is to be decided with U-44 (dc.retry-intents with dc.standalone-intents). Your third review saw U-44 as the only Chapter 0 work, so this one is new.

`effects.intent_closure` lets these gaps pass only while a candidate covers them. It fails if a candidate covers an effect or a transition that has no gap. Once the corrections are appended and the candidates resolved, the check must pass on the corrected Chapter 0 with no coverage at all.

## 11. Review log

`review.json` records 53 entries. Each was checked against the sources and lists what it changed:

- 21 findings from your three reviews;
- your decision on RV-25 (RV-48) and your review of that decision (RV-49);
- 7 findings from my own self-checks: RV-04, RV-18, RV-24 and RV-50, then RV-51 to RV-53 from the review before freeze;
- 23 from the independent review (RV-25 to RV-47).

Of the independent review's 23, 17 held as stated and 6 held in part, and all are now applied: RV-48 applies RV-25. The log is append-only. None of it changes Chapter 0; RV-17 and RV-28 record the correction candidates, which only you can apply, and RV-50 refines U-47.

## 12. Counts

The checker compares this table with the registries.

<!-- phase-a-counts -->
| Count | Value |
|---|---|
| object types | 42 |
| protocol objects | 25 |
| embedded contract parts | 4 |
| ledger state rows | 6 |
| append-only journal records | 2 |
| raw witnesses | 4 |
| legacy read-only | 1 |
| objects required in Chapter 1 | 10 |
| objects with conditional bindings | 6 |
| binding rules | 24 |
| binding rules with provenance | 7 |
| effect results | 2 |
| objects with lifecycle creation rules | 4 |
| identity fields | 32 |
| freeze-time bindings | 5 |
| field couplings | 10 |
| golden vectors | 19 |
| vectors with tier effect specified | 12 |
| vectors with rejection specified | 1 |
| executable vectors | 0 |
| blocked vectors | 6 |
| predicate tests | 2 |
| brief claims | 88 |
| claims CH0-required | 47 |
| claims HEA-derived technique | 12 |
| claims Phase-A proposal | 20 |
| claims unresolved | 9 |
| claims accepted | 49 |
| claims partially accepted | 18 |
| claims rejected | 2 |
| claims method only | 7 |
| claims pending decision | 12 |
| HEA techniques | 24 |
| HEA lessons from defects | 1 |
| inherited unknowns | 2 |
| new unknowns | 37 |
| NARROWED unknowns | 7 |
| Chapter 0 correction candidates | 2 |
| decision candidates | 43 |
| decide_with pairs | 6 |
| review findings | 53 |
| review findings from your reviews | 23 |
| review findings from self-checks | 7 |
| review findings from the independent review | 23 |
| checks | 33 |
<!-- /phase-a-counts -->

## 13. Running the checks

Python 3.11 or later, standard library only. The Chapter 0 negative cases plant their defects in a temporary copy of the Chapter 0 folder, after an unchanged copy passes as a control. `--evidence` confirms that each cited gate exists; without it, gate citations are accepted unchecked:

```text
python check_phase_a.py --ch0 <chapter0 folder> --handoffs <handoffs folder> --hea <HEA extract at f439f52> [--evidence <gate evidence folder>] [--render]
python test_phase_a_negative.py --ch0 <chapter0 folder> --handoffs <handoffs folder> --hea <HEA extract at f439f52>
```

## 14. Closure

The closure state, as your review of RV-48 set it:

| Item | State |
|---|---|
| RV-25, the conditional-binding defect | resolved (RV-48, RV-49) |
| binding grammar: additive rules, exclusive partitions, provenance outside `when` | established |
| effect_result vocabulary | established |
| the six EffectIntent states | unchanged |
| conditional-object coverage | complete |
| U-44, Chapter 0 correction candidate | open, carried into the handoff |
| U-47, Chapter 0 correction candidate | open, carried into the handoff |
| Phase A | frozen 2026-11-08 on your acceptance ("Yes, freeze it"); committed when you ask |

Phase A's job is to discover and register upstream contradictions like U-44 and U-47, so they don't block its closure. They stay explicit, unresolved candidates rather than being solved inside Chapter 1.

The freeze put these files, path-free, in `docs/architecture/chapter1/phase-a/`, as Chapter 0's were. The handoff `ch01-phase-a.json`, its SHA-256 sidecar and `latest.json` are in `docs/implementation/handoffs/`. The handoff records each file's hash and the Chapter 0 handoff it rests on. Phase B's entry check proves that chain against git objects once it is committed.

Closing Phase A doesn't apply U-44's or U-47's correction. Each is a separate Chapter 0 change for you to decide, appended with its own provenance.

Phase B then makes the decisions in section 9.
