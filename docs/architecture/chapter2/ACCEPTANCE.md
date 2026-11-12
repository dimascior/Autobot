# M6 — acceptance, dependency release, and the loop closure (2026-11-11)

Milestone 6 closes the Chief Engineer control loop: a COMMITTED (or
READ_ONLY VERIFIED) sequence becomes ACCEPTED through a deterministic
authority decision over durable ledger facts, its dependents are released
as durable ledger rows, successors become READY only by consuming those
rows, and `project_state()` renders the whole loop as the one read
surface. Everything here executes against the frozen Chapter 0 registry
rows; nothing extends or reinterprets them.

## Grounding — the frozen rows this milestone implements

| id | from → to | actor | capability | requires | mints | preconditions (frozen) | path | PONR | owner | status |
|---|---|---|---|---|---|---|---|---|---|---|
| t-s14 | VERIFIED → ACCEPTED | sequencer | cap-43 | obj-19 | obj-30 | read_only, automatic_or_attested, fence_matches | RO | **yes** | ch2 | IMPLEMENTED (`accept_verified`) |
| t-s20 | COMMITTED → ACCEPTED | sequencer | cap-43 | obj-26 | obj-30 | finalization_requirements_satisfied, fence_matches | SL | no | ch2 | IMPLEMENTED (`accept_commit`) |
| t-s41 | ACCEPTED → DEPENDENCIES_RELEASED | sequencer | cap-45 | obj-30 | obj-35 | successor_edges_durably_satisfied | ALL_EXEC | no | ch2 | IMPLEMENTED (`release_dependencies`) |
| t-s42 | DEPENDENCIES_RELEASED → COMPLETED | sequencer | cap-51 | obj-35 | — | (none) | ALL_EXEC | no | ch2 | IMPLEMENTED (`complete_sequence`) |
| t-s01 | DECLARED → BLOCKED | sequencer | cap-11 | — | — | edge_unsatisfied | ALL_EXEC | no | ch2 | IMPLEMENTED (`mark_ready`) |
| t-s02 | BLOCKED → READY | sequencer | cap-11 | — | — | all_edges_satisfied_by_declared_tier, freshness_reevaluated_now, finalizer_supported_in_v1, not_quarantined_or_is_repair | ALL_EXEC | no | ch2 | IMPLEMENTED (`reevaluate_blocked`) |
| t-s03 | DECLARED → READY | sequencer | cap-11 | — | — | same four as t-s02 | ALL_EXEC | no | ch2 | IMPLEMENTED (`mark_ready`) |
| t-s37/t-s39/t-s43/t-s44 | reuse/dominance acceptance arms | — | — | — | — | — | RUR | — | ch2 later | DEFERRED |
| t-s04 | DECLARED → BLOCKED_UNSUPPORTED_FINALIZER | sequencer | cap-11 | — | — | bound_publication_mode_mediated, requires_canonical_source_publication | REMOTE | no | ch2 later | DEFERRED (REMOTE path) |

cap-43 forbids planner, task_force and autoswarm: the enforcement point
is the instance lease — every acceptance/release operation asserts the
single-writer lease that only the integration layer (D-63/D-64) holds;
no agent-facing surface reaches these methods.

## The acceptance decision — durable facts only

Acceptance is a *decision*, not a step: both rows read nothing from the
caller but the sequence identity and the expected fence. Every input is
a durable, independently re-verifiable ledger fact:

- **SL (t-s20, `accept_commit`)**: the state gate (COMMITTED — a
  superseded, abandoned or already-accepted sequence is refused by
  name), the sequence and current-attempt fences, the frozen record's
  `finalization_requirements` (each must lie in the SL-satisfiable set
  `{LOCAL_CANONICAL_COMMIT}`, else refusal names the unsatisfiable
  requirement), the journaled t-s16 facts (the obj-26 reference must
  exist and its bytes re-hash, D-66), the latest finalization intent's
  durable HAPPENED state, and the attempt's admitted verification rows.
  A COMMITTED sequence without journaled t-s16 facts is forged state and
  HALTs.
- **RO (t-s14, `accept_verified`)**: the state gate (VERIFIED), the
  fences, the frozen record's `finalization_class == READ_ONLY` (a
  SOURCE_MODIFYING sequence is directed through finalization first), and
  the admitted verification rows. t-s14 is the registered PONR row: the
  journal entry carries `ponr: true`.
- **Both paths**: the attempt's `attempt_verifications` rows must exist
  and every outcome must be PASS or ATTESTED — a VERIFIED attempt
  violating that is forged state and HALTs. Derived eligibility is read
  from the admitted record row: HUMAN_REQUIRED acceptance refuses unless
  an ATTESTED (obj-37) row is among the facts (automatic_or_attested).

**obj-30 (AcceptanceReceipt, minter: sequencer)** binds the exact
sequence, the three frozen identity tiers (record_hash,
requirement_digest, contract_digest), the attempt and its contract hash,
the finalization path, the fence, the eligibility, and the verification
receipt references; the SL receipt adds the frozen requirement list and
the obj-26 reference. Bytes are canonical and content-addressed; the
journal carries the address under the transition facts
(`acceptance_sha256`). A duplicate acceptance is refused at the state
gate with the journal untouched — exactly one t-s20/t-s14 entry per
acceptance, its receipt identity intact. The obj-30 **payload schema**
belongs to Chapter 5; this milestone owns its minting, binding,
reference handling and byte preservation only.

## Dependency release — durable rows, not graph inference

`release_dependencies` (t-s41) writes the release as **ledger facts**:

- One `released_edges` row per identity tier the acceptance can satisfy
  — (RECORD, record_hash), (CONTRACT, contract_digest), (REQUIREMENT,
  requirement_digest) — each binding the predecessor sequence, the
  acceptance reference (`acceptance_sha256`), the release receipt
  (`release_sha256`) and the instant. Schema v6; the UNIQUE
  (predecessor_sequence_id, edge_kind) index makes conflicting rows
  impossible at the storage layer.
- **obj-35 (DependencyReleaseReceipt, minter: sequencer)** binds the
  predecessor identity tiers, the acceptance reference, the minted
  release ids and the successors known at release time.
  `known_successor_sequence_ids` is a snapshot for the record — never
  authority: successors admitted later consume the same durable rows.
- The rows, the receipt reference, the CAS to DEPENDENCIES_RELEASED and
  the t-s41 journal entry commit in **one transaction**
  (successor_edges_durably_satisfied). A crash before the commit leaves
  a clean ACCEPTED retry (fresh release ids — content, not identity,
  is what successors consume); after it, the duplicate is refused at
  the state gate. Releasing twice is therefore impossible, and a
  conflicting release cannot be represented.
- t-s42 (`complete_sequence`) requires the journaled obj-35 reference to
  resolve (D-66) and has no further frozen preconditions.

Edges are **content-addressed** (the frozen obj-07 shape targets
digests, not sequence ids): if two sequences carry identical frozen
content, either one's durable release satisfies an edge naming that
digest. That is the Chapter 0 design (dependencies bind content
identity), recorded here so nobody mistakes it for a defect.

## Readiness — consuming released edges

`_edges_satisfied` evaluates each frozen obj-07 edge against DURABLE
rows only: a released_edges row of the declared kind and target whose
predecessor is itself durably released (DEPENDENCIES_RELEASED or
COMPLETED), selected deterministically (ORDER BY release_id). The
readiness matrix:

| case | evaluation |
|---|---|
| no predecessors declared | t-s03 READY at mark_ready |
| ACCEPTANCE edge, no matching row | unsatisfied → t-s01 BLOCKED |
| matching row, predecessor merely ACCEPTED | unsatisfied (acceptance alone unlocks nothing) |
| matching row, predecessor DEPENDENCIES_RELEASED | satisfied |
| matching row, predecessor COMPLETED | satisfied |
| several edges, all satisfied | t-s03/t-s02 READY with the satisfied-edge bindings journaled |
| any one edge unsatisfied | BLOCKED; `reevaluate_blocked` reports the edge ids, transitions nothing and journals nothing |
| observation_requirement ACCEPTANCE_FRESH | always unsatisfied — freshness semantics belong to the deferred reuse/dominance rules (fail closed) |

`finalizer_supported_in_v1` (INV-64) is enforced at **both** t-s02 and
t-s03 through one shared check on the frozen finalization_class
vocabulary; the mediated-publication arm (t-s04) defers with the REMOTE
path. `not_quarantined_or_is_repair` holds vacuously — no quarantine
exists in this runtime. `freshness_reevaluated_now` is evidenced by the
journaled evaluation instant on every READY entry.

## ProjectState — the loop's read surface

`project_state()` adds `accepted_awaiting_release`,
`dependencies_released`, `completed`, `blocked_work` and `ready_work`
to the obligations, and the durable `released_edges` rows to the top
level. It stays write-free and interpretation-free: states and rows,
deterministically ordered; the consuming intelligence stays in the
integration layer.

## The Chief Engineer loop — executed

The two-contract pilot (`test_full_loop_two_contracts`) runs the entire
loop in one process against a real Git repository: contract B (a RECORD
edge on contract A) is BLOCKED at declaration (t-s01); A runs the whole
M2–M5 arc — objective, freeze, readiness, execution, evidence,
verification, real `git update-ref` CAS finalization — then t-s20
acceptance (obj-30 verified from the store), with B still BLOCKED
through every stage, an in-process restart at ACCEPTED (fresh Database/
Store/Sequencer, recover, re-lease), t-s41 release (three durable rows,
B the known successor), t-s02 readiness consuming the rows (the
satisfied-edge binding journaled), `project_state` exposing B as
actionable and A as released, and t-s42 completion — with repeated
recovery converging. The multi-edge matrix and the RO arc run the same
loop across tiers and paths.

## Crash windows — collapsed by transaction atomicity

Every M6 operation is one ledger transaction; no external effect exists
between ACCEPTED and COMPLETED. The windows reduce to three equivalence
classes:

1. **Before an operation's transaction commits** — nothing changed; the
   operation retries cleanly (the store may hold orphaned
   content-addressed bytes from a receipt put ahead of the rollback —
   unreferenced and harmless, the M5 precedent).
2. **After a commit boundary** — ACCEPTED, DEPENDENCIES_RELEASED and
   COMPLETED are each durable, journal-paired states from which the
   next operation re-drives; proven by the in-process restart at
   ACCEPTED inside the loop pilot.
3. **Forged state across a death** — recovery 8e HALTs (below).

## Recovery 8e

- A sequence in ACCEPTED/DEPENDENCIES_RELEASED/COMPLETED without its
  journaled acceptance entry HALTs (forged acceptance).
- Every released_edges row must pair with a journaled t-s41 release id
  for the same predecessor (both directions: a row without the journal
  entry, and a journaled release id without its row, each HALT), its
  predecessor must be durably released, and its acceptance and release
  references re-hash (D-66).
- Every READY sequence with frozen predecessor edges must still satisfy
  them from the durable rows — forged readiness HALTs.
- The 8d receipt-reference loop extends to `acceptance_sha256` and
  `release_sha256` on every journaled sequence entry.

## Finding register

| id | finding | disposition |
|---|---|---|
| F-M6-1 | `mark_ready` journaled t-s03 without the finalizer_supported_in_v1 check — INV-64 names t-s03 as an enforcement point alongside t-s02 | FIXED: one shared check (`_assert_finalizer_supported`) at both rows |
| F-M6-2 | the forged-ACCEPTED recovery HALT had no test (the 8e check existed unproven) | FIXED: `test_forged_acceptance_state_halts_recovery` |
| F-M6-3 | `_edges_satisfied` picked an arbitrary row when several durable releases match one edge, making the journaled satisfied-edge binding nondeterministic | FIXED: deterministic ORDER BY release_id |
| F-M6-4 | `_sequence_rule_facts` scans the whole journal per acceptance/release/completion | ACCEPTED: the journal is the authority and the scan is the honest read (the M5 `_intent_observation` precedent); an index is an optimization for a later pass |
| F-M6-5 | identical frozen content in two sequences means either release satisfies an edge targeting that digest | ACCEPTED: content-addressed dependency is the frozen obj-07 design, documented above |

## Ownership boundaries (unchanged)

obj-30/obj-35 payload schemas — Chapter 5. Verifier result/attestation
payload schemas — Chapter 4. Publication families and remote acceptance
arms — Chapter 6 / the deferred REMOTE rows. t-a13 and t-s45 stay
PARTIAL and t-s09's completeness classification stays split pending
Chapter 3. CORR-57 remains a separate package awaiting disposition. The
AutoSwarm fork and the Helios evidence plane are outside this tree.
