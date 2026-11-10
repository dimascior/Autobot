# Milestone 3 — the first contract-authorized specialist execution

Objective: turn ONE frozen TaskContract into ONE authorized specialist
execution through PromptProjection and AutoSwarm, preserving identity,
authority and evidence throughout. INTEGRATION_PLAN.md is the stage map;
this is the executable vertical-slice specification.

## Already in place (the closure slice on this branch)

The authoritative attempt lifecycle t-a02..t-a06 with the t-a10/t-a11/t-a13
failure arms is implemented on the Sequencer, with schema v3's
`attempt_calls` binding (role, provider, call_id, projection reference,
native session id), fence-in-SQL guards where fence_matches is registered
(t-a05/t-a06), per-role completion policies read from the frozen record,
recovery pairing (an execution state without its bound call — or vice
versa — HALTs; the committed projection reference must resolve), and the
`dispatched` crash stage proving the restart reconstructs the execution
relationship from durable state. t-a12 (STALE) waits for the member-lease
semantics of the evidence seam.

## Owned interfaces to build

1. **ProjectState (ch2 runtime, read-only)** —
   `project_state(db) -> dict`: sequences by state with record identities,
   current attempts + bound calls, in-doubt intents, permit/reservation
   standings, queued admissions, and the in-doubt set recovery reports.
   No writes. This is the Chief Engineer's authoritative view.
2. **PromptProjection (minimal indexed form; obj-10 schema is ch3's)** —
   `project_prompt(record_payload, role, attempt_identity,
   engineering_context) -> (bytes, content_address)`: deterministic
   rendering of the frozen contract's objective, scope, inputs, acceptance
   assertions, evidence requirements and role context, plus the Chief
   Engineer's guidance under an explicit provenance header; bytes into the
   CAS; `authority_effect: none` stated in the artifact. Reproducible:
   same inputs, same address.
3. **AutoSwarm transport (integration layer)** — typed commands with a
   **durable dispatch correlation established before any external
   launch**: the Sequencer mints the `call_id` and commits the
   `attempt_calls` row (t-a02) FIRST; only then does the adapter call
   `launch(projection_address, provider, role, call_id)`, with the
   `call_id` as the provider-side idempotency key, and
   `find_session(call_id) -> native_session_id | None` as the
   reconciliation query. **Launch/restart protocol**: an interrupted
   process that restarts into a DISPATCHED attempt with no
   `native_session_id` asks `find_session(call_id)` — an existing provider
   session is ADOPTED (t-a03 with its native id), an absent one is a
   launch failure (t-a10); a second `launch` with the same `call_id` must
   return the existing session, never create a duplicate execution.
   `collect(call_id) -> {witness_files, outcome}` reads results.
   Implemented twice: a **deterministic fake transport** (scripted session
   ids and witness bytes, duplicate-launch and failure injection) for the
   acceptance tests, then a bounded real-provider test using the AutoSwarm
   CLI conventions (AUTOSWARM_HOME, `--sandbox danger-full-access` for
   codex, remote-control enablement).
4. **Witness admission seam (ch2 tables; payload semantics ch3)** —
   `admit_witness(attempt_id, kind, producer, data, correlation) ->
   {content_address, admission_id}`: exact producer bytes to CAS and an
   `attempt_witnesses` row (schema v4) carrying **Chapter 1 D-21's
   structured admission entry exactly**: `witness_kind` (the obj-11..13
   discriminator), `producer` (a principals.json id), `content_sha256`
   (the D-31 address of the exact bytes), `admitted_utc` (D-27 instant),
   and the execution `correlation` (call_id and, once known, the native
   session id), journaled in the same transaction. The ExecutionReceipt
   assembly then references admitted rows — its `admitted_witnesses`
   become these structured entries, not bare addresses — making t-a06's
   `required_joins_present` a real join (receipt entries ⊆ admitted rows
   for the attempt) and giving t-a13's `evidence_complete_by_utc` bound
   its authoritative derivation (Chapter 3 semantics over D-21 facts).

## The acceptance scenario (executable, fake transport)

One integration test drives the full slice and asserts each criterion:

1. a valid objective is a frozen TaskContractRecord (the V-A fixture);
2. Registry admission freezes it and couples its Sequence (NEW_RECORD,
   tiers equal to the goldens);
3. orchestration requests an eligible Attempt (mark_ready,
   start_execution, reserve_attempt);
4. the Attempt's authority and lifecycle state are durable (fence, lease,
   attempt row);
5. PromptProjection yields a content-addressed prompt bound to the exact
   record + role + attempt identity (re-rendering reproduces the address);
6. the fake AutoSwarm receives the authorized request
   (dispatch_attempt with the projection reference and its call id);
7. the session correlates to the exact Attempt (attempt_calls row carries
   call_id + native session id after attempt_running);
8. execution lifecycle evidence is preserved and admitted (witness bytes
   byte-identical through the admission seam; admission journaled);
9. the Attempt's transitions match the frozen machine (journal rules
   t-a02..t-a06 in order, nothing else interleaved for that subject);
10. a process restart reconstructs the execution relationship from durable
    state (the `dispatched` crash stage pattern extended to RUNNING, and
    reconciliation after an interrupted execution: t-a10 or resume);
11. the Chief Engineer receives an updated ProjectState reflecting the
    execution and its next obligations (EVIDENCE_COMPLETE attempt visible,
    verification named as the next gate).

Failure arms in the same suite: launch failure (t-a10), required-role
non-success (t-a11), integrity-failed receipt (t-a13), fake-transport
timeout → reconciliation path.

## Ownership boundaries

ch2 owns every authoritative transition, table and journal entry above;
ch3 owns specialist execution semantics, obj-10/11/15 payload schemas and
witness admission *semantics* (the seam's tables land in ch2 so the
lifecycle is durable now); ch0/ch1 stay frozen; the Chief Engineer /
Taskforce intelligence and the real AutoSwarm transport stay in the
integration layer (D-63: they consume these APIs and never write
authoritative tables).

## Exit criteria

The acceptance scenario green on both interpreters; the bounded
real-provider run producing a correlated native session and admitted
witnesses; crash matrix extended through RUNNING; the coordinator suite
untouched; all under gated evidence with the four authorization
boundaries intact.
