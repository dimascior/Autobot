# The engineering intelligence plane (M7 — Chief Engineer / Taskforce integration, 2026-11-11)

The first continuously operating loop over the committed Chapter 2
authority runtime:

    project_state() → Chief Engineer reasoning → typed proposals →
    compiled TaskContractRecords → Registry admission → Taskforce seats
    → PromptProjection → transport execution → evidence → verification
    → real finalization → acceptance → dependency release →
    project_state() → reasoning again.

`src/intelligence/` is **integration-layer code** in the D-63/D-64
sense: it consumes the public Registry/Sequencer APIs and never writes
an authoritative table. It is a NEW package — the old `src/coordinator`
keeps its own semantics untouched (D-64), and `src/autobot` imports
nothing from it.

## The precise plane boundary

| Concern | Authority plane (src/autobot) | Intelligence plane (src/intelligence) |
|---|---|---|
| State mutation | Registry/Sequencer transactions only (D-63, D-68) | never — public APIs only |
| Authority reads | its own connection | a SQLite **mode=ro** connection (`readonly_view`): a write on it is a refused operation, test-pinned |
| Reasoning state | none | `intelligence.db` (PlanStore): objectives, cycles, events, proposals, findings, seats, driver lease — losing it loses no authority |
| Evidence | CAS + ledger references | content-address references only |
| Identity | three frozen tiers, call_id, fences | **reuses** protocol identity: proposal id = candidate contract_digest; dispatch idempotency = the Sequencer-minted call_id |
| Who may do what | cap-43/cap-45/cap-51 (sequencer), cap-08 (registry) — planner and task_force forbidden | cap-01 R (objective), cap-07 (descendant contract content), cap-13 (batch proposal), cap-14 (seat/route/model — agent_factory function), cap-22/23 (doing/watching work), cap-53 R (repair obligations) |

The policy seam is narrower still: `ChiefEngineerPolicy.plan(view)`
receives a `PlanningView` — plain data (authority snapshot, snapshot
version, unconsumed events/findings, objectives, open proposals,
workstream queues) — and returns typed proposals. It holds no handle of
any kind, so even a defective policy cannot mutate anything.

## The two principal handoffs

The frozen edge registry forbids `planner → registry` ("the Planner
authors drafts; only the Registry freezes", CORR-15) and cap-14 gives
seat/route/model selection to the `agent_factory` (forbidden to the
task_force). Both handoffs are REAL in this plane, and each has an
adversarial test:

1. **Authorship ≠ submission ≠ freeze.** The planner authors the
   candidate's content (cap-07; the frozen payload carries
   `authored_by: "planner"`). The deterministic `ChiefEngineerService`
   then submits the draft as `SUBMITTING_PRINCIPAL =
   "autobot_service"` — the registered authority-service boundary,
   whose `autobot_service → registry` edge is "authenticated freeze
   requests" — and the Registry alone admits and freezes (cap-08,
   which forbids the planner). No Registry call ever executes as the
   planner principal, and the ledger shows the split: the admission
   row's `submitted_by` is the service boundary while the record's
   `authored_by` is the planner
   (`test_planner_authors_and_the_service_submits`).
2. **Ask WHAT ≠ decide WHICH.** The Chief Engineer / Taskforce
   determine what capability and role a sequence needs — engineering
   reasoning, and the whole request. The **Agent Factory**
   (`roles.AgentFactory`, the registered `agent_factory` principal)
   alone decides which seat satisfies it — provider, model, context
   budget, tools — and stamps every assignment and persisted seat row
   `selected_by: "agent_factory"`; the dispatcher then consumes the
   assignment at t-a02. The Taskforce never names a provider or model
   (`test_seat_selection_belongs_to_the_agent_factory`).

## Grounding

| Element | Frozen basis |
|---|---|
| Chief Engineer = the `planner` seat (llm: true) | principals.json; cap-01 (objective is YOURS, planner References), cap-07, cap-13 |
| Taskforce = `task_force` (+ dispatcher/launcher/collector functions it drives through APIs) | principals.json; cap-22, cap-23; forbidden from cap-43/51 enforcement stays the Sequencer's lease |
| Seat/provider/model selection outside contract identity | cap-14 (agent_factory); the compiled payload carries no provider — test-pinned |
| Proposal → admission | `Registry.admit_payload` (t-c01..t-c07); QUEUED_FOR_YOU (SEM-11/t-c04) is the operator boundary, surfaced as a proposal status |
| Readiness/allocation | t-s03/t-s01/t-s02 + released_edges (M6); INV-12 one current attempt |
| Execution | t-s06, t-a01..t-a06 via Sequencer APIs; Transport contract + `reconcile_dispatched` (M3/G2) |
| Verification | register_verifier_result / conclude_verification (M4); the runner seam is Chapter 4's to fill |
| Finalization/acceptance/release | M5 intent machine + git executor; t-s14/t-s20/t-s41/t-s42 (M6); permits cap-36 = YOURS |
| Repair | t-s11 (repairs_used < max_repairs → READY), t-s08 retry, t-s12/t-s13 HALT (resume = YOUR budget grant, cap-47/cap-54) |

## The Chief Engineer service

`ChiefEngineerService.run_cycle(ro_conn, project_state, now)`:

1. **Pump** planning events from the journal (below).
2. Wake only on news: unconsumed events, unconsumed findings, or a new
   objective — otherwise no cycle opens.
3. Snapshot `project_state()` through the read-only connection; the
   cycle row binds the snapshot (journal entry count + head hash).
4. `policy.plan(view)` → typed proposals, validated structurally, the
   batch capped by `max_proposals_per_cycle` (runaway guard).
5. Dispose each proposal (below); consume the cycle's events, findings
   and objective novelty exactly once; close the cycle.

ProjectState inputs consumed: sequences by state with record
identities, released edges, attempts with bound calls and witness and
verification counts, work-eligibility lists, every obligation queue
(verification / finalization / acceptance / release / halted / blocked
/ ready / in-doubt intents / queued admissions / reservations),
permits, the instance lease, and the journal head as the snapshot
version.

## Typed proposals and their disposal

| Class | Binds | Disposal |
|---|---|---|
| CREATE_TASK / INVESTIGATE / REQUEST_REVIEW | requirement (objective text, class, capability, roles, guidance, edges; REVIEW adds a RECORD edge on the reviewed record; INVESTIGATE/REVIEW are READ_ONLY by construction) | compile → durable proposal row (id = contract_digest) → `admit_payload` → ADMITTED / QUEUED_FOR_YOU / REJECTED; a DUPLICATE converges: the row binds the live sequence (obj-32) |
| DECOMPOSE_TASK | ordered parts; `depends_on_parts` may only point backwards (cycles unrepresentable; cross-digest cycles are hash-fixed-point impossible) | expanded into CREATE_TASK children; later parts' edges target earlier parts' computed record hashes |
| REPAIR_TASK / ASSIGN_SPECIALIST | an existing sequence + capability + guidance | a seat AUTHORIZATION row — the only way a sequence past its first attempt is ever re-executed |
| WAIT_FOR_AUTHORITY | subject + reason | a WAITING row: the registered authority path is yours (permit, budget grant, queued admission, supersession) |
| PROJECT_COMPLETE | the objective | closes the objective and retires its WAITING rows |

Every proposal binds objective, rationale, triggering snapshot, and
provenance (finding ids, planning-event seqs). Provenance lives in the
plan store — "why did the planner create this contract" — while the
frozen record keeps answering only "what exactly was authorized".

## The compiler (cap-07)

`compile_proposal(proposal, environment, clock)` is a pure function.
The `RepositoryEnvironment` carries the bound declaration, YOUR active
delegation, the registered verifier/transform registrations (hashes
recomputed, never claimed) and the engineering defaults (assertions,
scope, policies, protocol capability ids, evidence requirements, the
role transform). The compiler emits the full canonical candidate — 
D-25 set ordering throughout, SEM-03 target_ref from the declaration's
canonical_ref, SEM-05-derived finalization_requirements, the
delegation triple with the registered floor per class, D-54 READ_ONLY
shaping, schema-required specification_inputs/effect_policy always
present — and the Registry remains the sole validator and freezer
(cap-08).

## Planning events

Derived from the TransitionRecord journal through the read-only
connection; **event identity is journal identity** (`journal_seq` is
the plan-store primary key), so one durable transition yields at most
one planning event across any number of pumps and restarts; a
watermark bounds the scan. Registered kinds:

contract_admitted, admission_queued_for_you, admission_duplicate,
admission_rejected (t-c machine); work_ready / repair_ready (t-s11) /
retry_ready (t-s08) / stale_base_ready (t-s18), work_blocked,
sequence_halted, sequence_verified, sequence_committed,
sequence_accepted, dependencies_released, sequence_completed (sequence
machine); verification_failed (t-a08), attempt_failed, attempt_stale
(attempt machine); authority_object_admitted (registry objects — your
declarations/delegations/registrations). New objectives (cap-01) and
admitted findings are intelligence-plane events: unconsumed rows of
their own tables.

## The Taskforce

One breadth-first sweep per driver step, over authority state as the
only truth (seats are bookkeeping reconciled FROM ledger facts):

1. `reconcile_dispatched` — the same registered adoption path a
   restart uses (UNCERTAIN holds, t-a10 needs positive proof).
2. **Allocation gate**: a READY sequence's FIRST attempt allocates
   from its originating admitted proposal; any later attempt requires
   a seat authorization from REPAIR_TASK/ASSIGN_SPECIALIST — failures
   never re-execute themselves, findings never become work unbidden.
   All READY sequences allocate before any later stage runs, so
   independent contracts run concurrently at the provider.
3. Seat selection (cap-14): the Taskforce requests WHAT it needs
   (capability, role; never a provider); the AGENT FACTORY decides
   WHICH seat — provider/model/context budget/expected output, stamped
   `selected_by: "agent_factory"` — none of it contract identity. The
   projection binds the frozen record, the exact attempt
   identity, and the planner's approved context bundle (guidance,
   predecessor findings by finding_id, context refs, repository
   context, expected outputs) plus a derived completion contract; then
   t-a02 mints the call_id and the transport launches on it.
4. Collection: producer-original witnesses admitted (D-21), findings
   admitted with attempt/call traceability, per-role completion
   (t-a04/t-a11), candidate freeze + evidence join (t-a05/t-a06). The
   ExecutionReceipt's completeness remains the integration stand-in
   pending Chapter 3 (F-CE-4; the t-a13 boundary carried from M3).
5. Verification through the runner seam (obj-18 capture where the
   assertion constrains the environment; INCONCLUSIVE → the registered
   t-s12 unknown arm; HUMAN_ATTESTED assertions wait for YOU, cap-44).
6. The deterministic authority arcs: RO acceptance (t-s14); SL
   finalization ONLY inside your permit grant (cap-36) — the specialist
   proposes the effect triple from its collected artifact, and the
   registered local-CAS executor and intent machine decide what
   happened; then t-s20 → t-s41 → t-s42, and blocked successors
   re-evaluate through t-s02.

## Repair loop

Verification failure → t-a08 (REJECTED, receipts journaled) and either
t-s11 (READY again, repairs_used incremented — surfaced as a
`repair_ready` event with the failed assertion ids) or t-s12 HALT
(budget_exhausted → the planner can only WAIT_FOR_AUTHORITY: t-s13 is
yours). A REPAIR_TASK proposal authorizes the informed second attempt;
its projection carries the failure context. Requirement identity never
changes — the same frozen record drives the repair attempt; protocol
repair/supersession objects (obj-33/34, cap-10) stay deferred with
their chapters, and a new-requirement repair is a new CREATE_TASK.

## Restart and idempotency

Layered, never clever: the ledger is the only truth (D-69); planning
events key on journal seq; proposals converge on deterministic
identity (and at the Registry as DUPLICATE); an interrupted submission
resubmits its durable candidate byte-for-byte (F-CE-3 test); dispatch
rides call_id idempotency and the adoption protocol; witness and
verification admissions are idempotent on content; one driver reasons
at a time (the plan-store driver lease — authority needs no such
guard, its instance lease already fences). The pilot kills the whole
plane at the permit boundary and continues under a fresh driver with
zero duplicate proposals, admissions or launches.

## The autonomous pilot (tests/autobot/test_autonomous_pilot.py)

Rule-driven, never scripted: `PilotPolicy` decides from authoritative
state alone. A (INVESTIGATE, RO) returns a structured finding; the
planner decomposes B (SL over the real local Git CAS) **from the
finding's text** plus C (RO review) edge-blocked on B; B pauses
VERIFIED at your permit, where the plane restarts; your
`grant_permit` is the word that lets finalization, acceptance and
release run; C turns READY through t-s02, executes, completes;
PROJECT_COMPLETE closes the objective and the loop reaches a stable
no-action state (two quiet steps; one more stays quiet). Conformance
arms: repair-proposal flow, the planner-word re-execution gate, and
concurrent allocation of independent READY contracts.

## Findings (F-CE)

| id | Finding | Disposition |
|---|---|---|
| F-CE-1 | Premature completion: the pilot policy declared PROJECT_COMPLETE in the same cycle that decomposed new work (its view predates its own output), closing the objective before B/C ran | FIXED — completion may only come from a cycle that proposes nothing else; pinned by the pilot (the missing WAIT row exposed it) |
| F-CE-2 | No cross-cycle total-work budget: per-cycle caps and the driver's max-step guard bound a run, but a long-lived daemon has no objective-scoped contract budget | accepted for v1 — every admission is journaled and permit-gated; the budget knob is yours (cap-47) and belongs to the production driver |
| F-CE-3 | The crash window between the durable proposal row and the Registry outcome was unproven | FIXED — recovery test: the stored candidate resubmits byte-for-byte, one record results |
| F-CE-4 | The Taskforce assembles ExecutionReceipt completeness itself | accepted/carried — the t-a13 receipt-classification authority is Chapter 3's; this is the same stand-in the M3 suite uses, now stated here |
| F-CE-5 | The "latest VERIFIED attempt" was picked by minted id, which does not sort chronologically; after a t-s18 stale-base arc two VERIFIED attempts coexist and the wrong fence could be presented | FIXED — insertion-order selection (the F-M5-1 lesson applied to the plane) |
| F-CE-6 | The service submitted candidates as `submitted_by="planner"`, collapsing cap-07 authorship into the FORBIDDEN planner→registry edge (CORR-15) — flagged in the operator's review | FIXED — the deterministic service submits as the `autobot_service` boundary principal while the payload keeps `authored_by: "planner"`; adversarially pinned (`test_planner_authors_and_the_service_submits`) |
| F-CE-7 | Seat/provider/model selection ran as a Taskforce-owned call, blurring cap-14 (owner agent_factory, forbidden task_force) — flagged in the operator's review | FIXED — the component IS the Agent Factory (`AgentFactory`, principal `agent_factory`); the Taskforce asks only (capability, role), every assignment and seat row records `selected_by: "agent_factory"`; adversarially pinned (`test_seat_selection_belongs_to_the_agent_factory`) |

## Preserved open surfaces (unchanged by this phase)

ACCEPTANCE_FRESH stays a PARTIAL readiness arm (fail-closed; its
freshness semantics belong to the deferred reuse/dominance rows).
Chapter 3 keeps t-a13, t-s45 and the classification half of t-s09.
Chapter 4 keeps production verifier runners and receipt schemas.
Chapters 5/6 keep the obj-30/obj-35 payload schemas, remote
finalization/publication arcs, standalone effect machines and the
remaining finalization families. t-s13 HALT resume, permits, manual
attestation and supersession stay YOURS (cap-54/36/44/10). CORR-57
keeps its separate amendment process. The AutoSwarm A2 provider change
stays independently versioned on its fork. Helios command/evidence
governance stays distinct from Autobot task-state authority. The
LLM-backed Chief Engineer policy is a later provider behind the same
`ChiefEngineerPolicy` seam (the Transport→G2 precedent); this phase
ships the deterministic policy harness that proves the plane.

## Proof of record

167/167 on the repo venv CPython 3.12.11 (gate 20261111-ce-017),
167/167 on PATH CPython 3.8.10 (ce-018), coordinator 41/41 (ce-019) —
the final bytes, with the operator's two boundary corrections
(F-CE-6/F-CE-7) and their adversarial tests. Earlier legs on the same
slice: 165/165 ×2 + 41/41 after the self-review fixes (ce-008/009/010),
first green at 164 (ce-005/006/007).
