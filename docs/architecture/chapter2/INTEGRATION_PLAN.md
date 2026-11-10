# The integration progression (2026-11-10)

How the full engineering loop — Chief Engineer → Taskforce → TaskContract →
Registry → Sequencer → PromptProjection → AutoSwarm → Evidence →
Verification → AcceptanceReceipt → Dependency Release → Chief Engineer —
lands against the frozen protocol, stage by stage, with the ownership
boundaries the registries assign. "ch2 runtime" means `src/autobot`;
"integration layer" means the coordinator/orchestration side (D-63: it
consumes Autobot APIs and never writes authoritative tables); "chN" means
the chapter that owns the full payload schema per representation.json.

## Stage map

| Loop stage | Interface (consumed/provided) | Data model | Service | Owner |
|---|---|---|---|---|
| Chief Engineer reads state | `project_state()` — read-only view: live sequences by state, current attempts, in-doubt intents, permits/reservations, blocked edges, queued admissions, in-doubt set from `recover()` | existing ledger tables, no new writes | ch2 runtime (new read API) | ch2 provides the view; the intelligence consuming it is the integration layer |
| Taskforce / proposal formation | proposal builder emitting canonical TaskContractRecord payloads + bound-object admission calls | obj-00/02/03 payloads (schemas frozen, ch1) | integration layer, atop `Registry.admit_object` | integration layer |
| TaskContract freeze | `Registry.admit_payload` (exists, milestone 1) | obj-04..07/17, obj-32, Sequence row | ch2 runtime | done |
| Sequence readiness | `mark_ready` (exists); real edge evaluation needs release receipts | dependency edges from the frozen record; `released_edges` index | ch2 runtime (M6 extends t-s01/t-s02 edge logic) | ch2 |
| Attempt execution | `start_execution`/`reserve_attempt` (exist); new: `dispatch_attempt` (t-a02, requires obj-10 reference + call id), `attempt_running` (t-a03, witness handle), `finish_attempt` (t-a04, completion policies per role), t-a05/t-a06/t-a13 | attempts table (exists) + `attempt_calls` (call id, provider, session handle) | ch2 runtime; the launcher is the integration layer | ch2 transitions; integration layer drives |
| PromptProjection | `project_prompt(record_hash, role, attempt_identity, context_bundle) -> content address` — reproducible, attributable, authority_effect none | obj-10: content-addressed prompt artifact + ledger index row | new projection service (pure function over frozen record + registered role + approved context) | obj-10 schema is ch3's; ch2 lands the minimal indexed form, integration layer supplies the engineering context |
| AutoSwarm execution | typed commands: launch(projection, provider, role seat) → session handle; collect(handle) → artifacts + witness files | witness files (producer originals) | integration layer (existing `src/coordinator` dispatch + AutoSwarm CLI, AUTOSWARM_HOME conventions) | integration layer |
| Evidence admission | `admit_witness(kind, producer, bytes, correlation) -> content address + admission entry`; ExecutionReceipt assembly with completeness classes | obj-11..13 (raw witnesses: producer original + admitted copy in CAS, D-21/D-38), obj-15 | ch2 provides the admission seam (CAS + admission entries + journal); full receipt semantics are ch3's | seam ch2 (M3); semantics ch3 |
| Verification | `register_verifier_result(attempt, assertion, outcome, by) `; t-s09/t-s10/t-s11/t-s12, t-a07/t-a08; deterministic runner executes registered closures (INV-66) against obj-18 environment capture | obj-18 (schema frozen, ch1), obj-19/20 references | ch2 transitions + a verifier-runner interface; runner itself and receipt schemas are ch4's | transitions ch2 (M4); runner/receipts ch4 |
| Finalization effects | wire the registered minting transitions to the intent machine: t-s15a (SL) first — `mint_intent` already enforces the class checks; add the COMMITTING/COMMITTED/READY/VERIFIED arcs (t-s16/t-s17/t-s18/t-s19) consuming intent outcomes + reservation consume/release (t-r01/t-r02 exist) | sequences/intents/reservations (exist); CommitReceipt reference | ch2 runtime + the first real executor (local git CAS, smallest blast radius) | ch2 (M5); receipt schemas ch6 |
| Acceptance | t-s14 (RO) / t-s20 (SL): `finalization_requirements_satisfied` over receipt references; mint AcceptanceReceipt reference | obj-30 reference row (partitions are ch5's) | ch2 transitions (M6) | ch2 transition; obj-30 schema ch5 |
| Dependency release | t-s41/t-s42: durable successor-edge satisfaction; feeds `project_state` ready-work | obj-35 reference, `released_edges` | ch2 runtime (M6) | ch2; obj-35 schema ch5 |
| Loop closure | Chief Engineer re-reads `project_state`, proposes next work | — | integration layer | integration layer |

## Milestone sequence (remaining Chapter 2 obligations)

- **M3 — attempt execution + evidence seam.** t-a02..t-a06/t-a13 with
  per-role completion policies from the frozen record; `attempt_calls`;
  witness admission (D-21/D-38: exact producer bytes to CAS, admission
  entries journaled); minimal indexed PromptProjection. Conformance: policy
  matrix tests, witness byte-identity, crash stages at DISPATCHED/RUNNING.
- **M4 — verification transitions.** t-s07/t-s09..t-s12, t-a07/t-a08,
  t-s45; verifier-runner interface honoring SEM-16 eligibility and INV-53
  (bound artifact unavailable → HALT); attestation path for HUMAN_REQUIRED
  (D-52). Conformance: assertion-outcome matrices, budget exhaustion,
  environment mismatch HALTs.
- **M5 — finalization wiring, SL path first.** t-s15a → t-s16/18/19 driven
  by real intent outcomes; reservation consume at PONR (t-r01, already
  proven as a primitive); the local-CAS git executor as the first real
  effect executor, consuming `committed_intent()` only. Then SRD/SRP/SRPO
  arcs and the staging/remote effects. Conformance: PONR crash matrix
  (crash between intent commit and observation on a real local ref),
  outcome-rule observation tests against a real repository.
- **M6 — acceptance, release, ProjectState.** t-s14/t-s20, t-s41/t-s42,
  edge evaluation in t-s01/t-s02 against released edges; `project_state()`
  read API; the Chief Engineer loop pilot driving one full cycle
  (objective → contract → attempt → evidence → verification → acceptance →
  release) against a disposable repository. Conformance: INV-16/INV-51
  (nothing releases without full receipts), loop-closure integration test.
- **Later-chapter handoffs.** Standalone effect machines (t-g01..t-g03,
  t-w01..t-w04) and their receipts; full receipt/witness schemas (ch3/4/5/6);
  supersession/abandon/revocation wildcards (w-s01..03) and repair
  (obj-33/34); the Taskforce/Chief Engineer intelligence itself stays in
  the integration layer throughout (D-63/D-64).

## M3 specification carry-forwards

- **`find_session(call_id)` returning `None` is not proof of launch
  failure.** A provider session lookup that returns no result may mean the
  session was never created, OR that the transport cannot yet observe it
  (eventual consistency, network partition, provider delay). Recovery must
  preserve an uncertain launch outcome as UNKNOWN_OUTCOME or
  DISPATCHED (not FAILED) until the transport positively establishes the
  outcome. The DISPATCHED→FAILED arm (t-a10) requires positive evidence
  of launch failure, not absence of a session lookup result.
- **t-a13 receipt-classification authority.** The fail-closed boundary in
  `fail_candidate` blocks until Chapter 3's receipt-admission mechanism
  establishes the `completeness` and `evidence_complete_by_utc` fields as
  authoritative. M3's evidence seam provides the admission path; the
  classification logic already exists below the boundary.

## Standing constraints

Every milestone: invariant-named tests, adversarial arms, crash/restart
proofs, both interpreters, coordinator suite untouched, Helios gates on
every command, and the four authorization boundaries (implement / commit /
push / PR / merge) per the operator's words. CORR-57's disposition gates
the t-i07 object-identity follow-up but blocks nothing else in M3..M6.
