# Autobot Chapter 0: the Evidence Sequencing architecture

**Status:** frozen by Phase C on 2026-11-08, and amended the same day by amendment 1 (CORR-55, CORR-56), which closes the intent lifecycle of standalone effects and retries (section 9). Nothing in this chapter is implemented yet; Chapters 1 to 8 implement it.

**Precedence:** the canonical form is the JSON in `registries/`. This document explains it, and `CHAPTER0_REGISTERS.md` (generated) lists all of it. Where prose and registries disagree, the registries win. `python check_registries.py` checks the registries, and also checks that this document cites only IDs the registries define.

## 1. What Chapter 0 decides

Chapter 0 decides **who may decide that work is done, and how that decision is proven**. The rules:

> An Attempt may fail without changing the TaskContract. A TaskContract may never change inside a Sequence. A stale Attempt may produce historical evidence but can never produce authoritative state. A source-modifying Sequence cannot become ACCEPTED until the exact verified candidate has been committed through a fenced, compare-and-swap Git transition.

> No agent, model, process, monitor, planner or task-force member may directly cause a successor state that it cannot prove through the frozen evidence contract.

> Evidence does not grant authority; authority does not rewrite evidence. (HEA, Plane 4 boundary specification, Rule 5)

In one sentence:

- Autobot is the only authority over whether work is done.
- You are the only authority over what may be done, either directly or through a DelegationContract that bounds what Autobot's planner may decide.
- You also decide anything that leaves the machine.
- Everything else either does the work or witnesses it.

The model:

| Term | Meaning |
|---|---|
| TaskContractRecord | One admitted request: a frozen requirement plus its own identity |
| Sequence | The authoritative fulfillment lifecycle of exactly one TaskContractRecord |
| Attempt | One fenced execution against one exact source state and environment |
| Candidate | The immutable output of one Attempt |
| Verification | Deterministic proof about that Candidate in an identified environment |
| Commit and publication | Compare-and-swap of that exact verified Candidate at the repository's declared canonical location |
| Acceptance | Proof that the request was fulfilled, by its own Sequence or by an explicit, policy-checked reuse |

## 2. How the chapter was built

- **Method.** HEA's Chapter 0:
  - **Phase A** recorded what exists.
  - **Phase B** gave every capability exactly one owner and every object exactly one minter.
  - **Phase C** checked it all again, froze it, and wrote a handoff.
- **Inputs.** Seven requests between 2026-11-07 and 2026-11-08:
  - the first Luna v15 run through Autobot;
  - a review of the coordinator;
  - the "Evidence Sequencing v1" proposal, mapped to HEA's commit history;
  - the TaskContract, Sequence and Attempt design;
  - three rounds of corrections: CORR-01 to CORR-10, CORR-25 to CORR-30, CORR-40 to CORR-46;
  - a review that accepted the remaining proposals with refinements.
- **Drafts.** Drafts 1 to 4 of this chapter stay outside the repository because they contain machine-local paths. The handoff records their SHA-256 hashes.
- **Settled.** Every correction is resolved in `registries/corrections.json`, every decision in `registries/decisions.json`.
- **HEA practices the chapter adopts:**
  - one question per authority
  - ownership levels
  - one minter per object
  - authority graphs kept separate from evidence graphs, with forbidden edges
  - independence rules
  - an unknowns registry with durable IDs
  - corrections that are appended, never rewritten
  - mechanically checked closure
  - the test that an agent who loses the conversation can rebuild the design from the repository

## 3. Who: the authorities and their questions

| Authority | Its one question |
|---|---|
| You | What may become true, directly or inside a delegation, and may this effect leave the machine? |
| Autobot (the Evidence Sequencer) | Has this exact request been fulfilled, proven by receipts bound to one fenced Attempt, and may its successors start? |
| AutoSwarm | Which sessions ran, with what input, and how did each end? (a witness) |
| Helios | May this exact command run at this position in this session? What ran? (admission, and a witness) |
| Akashic | Was the Helios mechanism that decided intact? |
| HDB | What happened across sessions? (an index, never a prerequisite) |
| Git | Which exact tree does this ref name? |
| The CI system | Did a remote check pass on this exact commit? (a witness) |
| A registered verifier | Does this exact candidate satisfy this assertion in this environment? |
| The Task Force (orchestrator, worker, monitor, poller) | None. They do the work and make claims |

## 4. Autobot's seats

Only the Sequencer applies transitions (cap-51). Every other seat submits receipts or proposals. These are the owners recorded in `registries/capabilities.json`:

| Seat | Owns | Must never |
|---|---|---|
| Contract Registry | Admission, freeze, identity tiers, bindings, duplicates (cap-08); revisions (cap-09) | Freeze outside an active delegation or without its floor; merge equivalent requests |
| Planner (an LLM) | Descendant contract content inside a delegation (cap-07); batch proposals (cap-13) | Freeze, register verifiers, hold a delegation, reserve, verify, accept or publish |
| Agent Factory | Seat, route and model selection (cap-14) | Let an agent choose a model |
| Evidence Sequencer | Readiness (cap-11), reuse (cap-12), reservation and fencing (cap-15), leases (cap-16), permit accounting (cap-37), acceptance (cap-43), release (cap-45), the single instance (cap-49), transitions and journal (cap-51), effect intents (cap-52), repair obligations (cap-53) | Accept without the chain; overbook a permit; rewrite a receipt |
| Dispatcher | Dispatch projection and launch request (cap-17) | Project a prompt it doesn't render |
| Launcher (target topology) | Launch relay under your identity or the agent account's (cap-18) | Run unrequested launches |
| Evidence Collector | Subject-state capture (cap-29); WitnessRecord admission and the ExecutionReceipt (cap-30) | Use file times or agent claims as evidence |
| Git adapter | Source-state capture (cap-31); CandidateReceipt (cap-32) | Read the working directory after the freeze |
| Environment probe | Environment capture (cap-33) | n/a |
| Verifier runner | Verification, failure and remote-verification receipts (cap-35) | Count LLM output as PASS; run an unregistered artifact |
| Git sequencer | Local commit (cap-38), staging (cap-39), canonical publication (cap-40), workspace sync (cap-41) | Force, merge, restage, or move a canonical ref twice |
| Authority service | Authenticating authority requests (cap-48) | Accept a request an agent could have made |
| Autobot code | Schema and migrations (cap-50); content-store retention (cap-55) | Delete an artifact a live record binds |

## 5. Two deployment topologies, one protocol

- **Interim** (today: one Windows user).
  - Autobot is one process, and the IPC protocol runs in-process.
  - "Only Autobot can X" holds by code convention.
  - The invariants hold against races, stale workers, drift, crashes and honest mistakes.
- **Target** (CORR-02, CORR-11, CORR-12).
  - Autobot is an authority service with its own OS identity. Its ledger and content store are writable only by that identity, and a narrow IPC interface is the only way in.
  - Agents still launch under your identity (or an agent account) through the Launcher. Under the service's identity they would have neither your subscription logins nor your user-level Helios hooks (INV-35).
  - Because agents run as you, the OS identity alone can't tell you apart from them. Authority requests therefore go through an AuthorityAuthenticationProvider, with a human-presence factor first (D-02b, INV-26).
- **Moving from interim to target** is a deployment change, not a code change: both topologies run the same invariant tests (Chapter 8).

## 6. What: the objects

Every object type has exactly one minter (`registries/objects.json`, 42 types):

| Class | Objects | Minted by |
|---|---|---|
| Authority records | DelegationContract (obj-00), DelegationRevocationRecord (obj-01), RepositoryAuthorityDeclaration (obj-02), Verifier and Transform registrations (obj-03), per-sequence PublicationPermit (obj-22), RepairWaiver (obj-34), ManualAttestation (obj-37), Supersession and Abandon records (obj-38) | You |
| Contracts | TaskContractRecord (obj-04) with its SpecificationInputs (obj-05), RoleTransforms (obj-06), DependencyEdges (obj-07) and environment constraints (obj-17) | The Registry |
| Contracts | AttemptContract (obj-09) | The Sequencer, at reservation |
| Lifecycle state | Sequence (obj-08), StandingPermitState (obj-23), PermitUseReservation (obj-24), EffectIntent (obj-25), ForwardRepairObligation (obj-33), InstanceLease (obj-39) | The Sequencer |
| Witnesses | Execution lifecycle (obj-11, AutoSwarm), command governance (obj-12, Helios), session transcripts (obj-13, the agent CLIs), remote checks (obj-14, CI) | Their producers |
| Receipts | ExecutionReceipt (obj-15, Collector); CandidateReceipt (obj-16, Git adapter); actual environment (obj-18, probe); Verification, failure and remote-verification receipts (obj-19, obj-20, obj-21, Verifier runner); Commit, staging-publication, publication and staging-deletion receipts (obj-26, obj-27, obj-28, obj-41, Git sequencer); Acceptance, reuse and release receipts (obj-30, obj-31, obj-35, Sequencer) | Deterministic seats only; never an LLM or an agent |
| Non-authoritative records | PromptProjection (obj-10), WorkspaceSyncReceipt (obj-29) | Dispatcher, Git sequencer |
| Journal | DuplicateAdmissionRecord (obj-32, Registry), TransitionRecord (obj-36, Sequencer) | As listed |
| Legacy | LegacyRecord (obj-40) | The migration |

The rules that govern them:

- **R-1** One minter per object type.
- **R-2** Immutable after commitment; a correction is a new object.
- **R-3** Referencing an object grants no authority over it.
- **R-4** Autobot computes every hash it trusts.
- **R-5** Permits are bounded and bound to state.
- **R-6** Facts are append-only; authority is a pointer moved by compare-and-swap.
- **R-7** Evidence does not grant authority.
- **R-8** Constructing and authorizing are different seats.
- **R-9** Producer output is not evidence until it is admitted.
- **R-10** A delegation bounds and never extends.
- **R-11** Equivalent is not identical.
- **R-12** An external effect's authority is decided at its linearization point.
- **R-13** The programs that decide PASS are registered authority.

**Where things live** (D-04, confirmed in CORR-54; checked by INV-72):

- **The Autobot ledger** holds authoritative metadata, identities, hashes, bindings, state transitions and receipt references.
- **The content-addressed store** holds the immutable receipt payloads and the retained evidence and artifact bytes.
- **HDB** is a derived, queryable index and never authoritative by default. Its freshness or availability gates nothing unless a TaskContract explicitly declares an HDB-derived assertion.
- **Git** holds source-state proof and path-free proof references (trailers). It is never the receipt store.

## 7. Identity

Three tiers, RFC 8785 JCS and SHA-256 (CORR-41, D-09; `registries/identity.json`):

| Tier | Answers | Fields |
|---|---|---|
| RequirementDigest | Is this the same engineering work? | Objective, `finalization_class` (CORR-47), repository and authority location, target ref, exact-byte specification inputs, allowed paths, assertions with their environment constraints |
| ContractDigest | Is this the same fulfillment policy? | The RequirementDigest plus retry, repair and reuse policies, effect policy, finalization requirements, remote-verification mode, capabilities, role transforms, completion policies, evidence requirements, predecessors, and the bindings resolved at freeze |
| RecordHash | Is this the same admitted request? | The ContractDigest plus contract ID, revision, author, freeze time, delegation, supersession provenance, `force_new_sequence` |

- **Admission** (t-c01 to t-c07).
  - A payload whose ContractDigest matches a record with a live Sequence is a duplicate (t-c03). The Registry records it and returns the existing record, unless the payload carries `force_new_sequence` (CORR-43, CORR-51, INV-54).
  - Anything else is a new TaskContractRecord with its own Sequence (CORR-26, INV-40). Equal digests mark equivalence; they never merge requests.
- **Bindings** (CORR-42, CORR-48).
  - At freeze, every authority-affecting lookup resolves to an immutable record, and those hashes enter the ContractDigest: the repository declaration, each verifier and transform registration, the delegation and its floor.
  - A registration binds an execution artifact closure: `artifact_root`, `entrypoint` and `execution_requirements`. The content store keeps that closure while any live record binds it.
  - A missing bound artifact halts the sequence (t-s12). Nothing is ever substituted (INV-53, INV-58, INV-66).
- **Reuse** (t-s43, t-s44). An earlier acceptance satisfies a new record only when all of these hold (CORR-36, CORR-47, INV-50, INV-65):
  - the RequirementDigest is the same;
  - the requester's own reuse policy allows it;
  - the earlier proof dominates the requester's (finalization requirements and bindings);
  - it is fresh enough;
  - for source work, the accepted commit is still the canonical head (EXACT_HEAD). ANCESTOR_OK is deferred.
- **Dependency edges** (obj-07) require one of: a specific record, the same contract, or the same requirement, with an observation requirement (CORR-21). Only an AcceptanceReceipt satisfies an edge, never a status (INV-09).

## 8. The lifecycle

The Sequence machine has seven finalization paths, each with exactly one point of no return (PONR):

| Path | Meaning | PONR |
|---|---|---|
| RO | READ_ONLY | Acceptance (t-s14) |
| SL | Source change, LOCAL authority | Local canonical compare-and-swap (t-s16) |
| SRD | Source change, REMOTE authority, no remote check | Remote canonical compare-and-swap (t-s33) |
| SRP | REMOTE, verified before publication (the default, D-14) | t-s33 |
| SRPO | REMOTE, observed after publication | t-s33 |
| RUD | Reuse at admission | Acceptance by reuse (t-s43) |
| RUR | Reuse once ready | Acceptance by reuse (t-s44) |

**Before the PONR**, a failure returns to READY for a new Attempt on the same record: a conflict, an absent effect, a failed staging push or a failed remote check (t-s08, t-s11, t-s18, t-s19, t-s24, t-s27, t-s34). Supersession, abandonment and revocation end the sequence and fence its in-flight attempt (w-s01, w-s02, w-s03).

**After the PONR** it is forward-only: recovery finishes to ACCEPTED, DEPENDENCIES_RELEASED and COMPLETED. The only other end is PUBLISHED_UNVERIFIED (t-s40). Nothing crosses the PONR backwards (INV-36, INV-71).

**Halts:**

- HALTED is entered before verification (t-s12, t-s45). Its only exits are your grant or attestation (t-s13), and the wildcards.
- HALTED_AFTER_VERIFICATION is entered when a permit lease expires during CI (t-s28). It keeps every receipt. Resuming it re-validates and takes a fresh reservation (t-s29), or starts a new attempt (t-s30) (CORR-50, INV-63).
- The two are separate states so that a resume can never skip verification. The negative tests check exactly this.

**Never runs in v1:** a contract on a MEDIATED repository that needs canonical source publication freezes, but it waits in BLOCKED_UNSUPPORTED_FINALIZER and never becomes READY (t-s04, CORR-52, INV-64).

**The Attempt machine** (t-a01 to t-a13, w-a01, w-a02):

- FINISHED follows the per-role completion policy: the worker must succeed, the orchestrator and the monitor must end, and the poller is cancelled when the worker finishes (CORR-08, CORR-20).
- STALE and FENCED are dead ends.
- Every receipt bound to an attempt needs a matching fence (INV-13).

## 9. Effects, intents and permits

Six effect types in `registries/transitions.json`:

| Effect | Role |
|---|---|
| eff-local-canonical-cas | Canonical, for LOCAL repositories |
| eff-local-sequence-ref | Not canonical: the sequence-scoped ref for REMOTE repositories |
| eff-staging-push | Not canonical; leaves the machine, so it gets its own receipt |
| eff-canonical-push | Canonical, for REMOTE repositories |
| eff-staging-delete | Housekeeping (D-16) |
| eff-workspace-sync | Not authority; only when safe (CORR-32, INV-46) |

**Intents** (CORR-33, CORR-44, CORR-55, CORR-56, INV-47, INV-55):

- Every effect runs under an EffectIntent. Every transition that mints one, retries and standalone effects included, commits it in one ledger transaction just before the effect, with the authorization checks of the effect's class (INV-47, as revised by amendment 1):

  | Class | Effects | The intent checks |
  |---|---|---|
  | Attempt-scoped | eff-local-canonical-cas, eff-local-sequence-ref, eff-staging-push, eff-canonical-push | an active delegation, a permit reservation (taken or held), the fence, the committed intent, and a hard timeout shorter than the intent window |
  | Staging delete | eff-staging-delete | an eligible staging ref, an ACCEPTED sequence or expired retention (D-16), the expected ref value known, cleanup authority from an immutable, previously established source, and a committed, bounded intent |
  | Workspace sync | eff-workspace-sync | an established canonical source, an eligible target, INV-46's safety predicate, and a committed, bounded intent |

- The Sequence's own compare-and-swap on its state version (t-s15a, t-s15b) comes on top of its class's checks.
- Standalone effects use their own class's checks and never inherit the attempt-scoped permit, reservation or fence checks (INV-74). Each has its own lifecycle, applied by the Sequencer (CORR-55). A lifecycle leaves its in-flight state only on HAPPENED or ABSENT under its effect's outcome rule, and holds there while the intent is UNKNOWN_OUTCOME:
  - **A staging ref** is RETAINED from its staging push, and its delete intent (t-g01) moves it to DELETING. Under the remote_delete rule, a ref observed absent is HAPPENED, even when it was already gone, and moves it to DELETED (t-g02). A ref at any other value is ABSENT and returns it to RETAINED (t-g03).
  - **A workspace sync** is DUE once a canonical ref your workspace tracks moves, and its intent (t-w01) moves it to SYNCING. The proven sync moves it to SYNCED (t-w02), and an ABSENT sync returns it to DUE (t-w04).
  - **SKIPPED.** When INV-46's safety predicate fails, the sync ends SKIPPED (t-w03): no intent, no ref mutation, and a WorkspaceSyncReceipt that records SKIPPED. That receipt closes only the no-effect branch. The effect itself closes only through its HAPPENED path (INV-75).
- The cleanup authority behind a staging delete comes from an immutable source established before the deletion, such as the frozen policy the staging ref was created under. It is never the configuration current when retention expires, so a later change can't authorize a deletion that wasn't authorized when the ref was created. Chapter 6 fixes its representation.
- A revocation committed after the intent doesn't stop that in-flight effect (INV-41).

**Outcomes** (CORR-49 as refined, INV-62):

- The target at the new value is HAPPENED.
- The target still at the expected old value is UNKNOWN_OUTCOME. The old value is not evidence of absence, because the operation could still succeed.
- Any other value is ABSENT, because the compare-and-swap's expected value is stale.
- Grace periods only tune polling. Local ref updates have no asynchronous server, so for them a fenced executor plus the old value does prove absence.

An UNKNOWN_OUTCOME resolves only by observation (t-i04, t-i05), by retrying the identical compare-and-swap under a fresh intent while authority is active (t-i06), or by your recorded decision (t-i07) (INV-70).

**The retry is an initiation rule of its own** (CORR-56, INV-73). t-i06:

- proves the same effect, target and expected old value as the intent it replaces;
- holds the attempt-scoped checks: an active delegation, the reservation still held, the fence, a bounded window and the committed intent. The fence is checked even though no wildcard can move it while an intent is UNKNOWN_OUTCOME, so the rule stays locally complete;
- moves the old intent to SUPERSEDED_BY_RETRY and mints a fresh intent that binds it.

It retries exactly the four attempt-scoped effects. The registry names the two standalone effects as not retryable, so their exclusion is a rule of its own, not a side effect of failing the reservation and fence checks. A standalone intent that ends UNKNOWN_OUTCOME resolves by observation (t-i04, t-i05) or, once authority is inactive, by your decision (t-i07).

**Permits** (CORR-28, CORR-40, CORR-50):

- Each use is a reservation row (obj-24), counted in a StandingPermitState (obj-23) that keeps `consumed_uses + reserved_uses <= permitted_uses` (INV-48).
- A reservation is taken entering COMMITTING. It is consumed exactly once at the PONR (t-r01) or released on any exit before it (t-r02, idempotent).
- A reservation has its own renewable lease, independent of any intent (INV-60).
- A late consume against a released reservation raises an integrity alarm (INV-59).
- A per-sequence permit is a one-use permit state.
- Window and lease lengths are bounded configuration, not constants (D-18, INV-67).

## 10. Evidence

- **Admission.** WitnessRecords (obj-11 to obj-14) are the producers' own files, unchanged. The Collector admits them into the content store and mints the one ExecutionReceipt (obj-15) (CORR-03).
- **The causal links** are written by AutoSwarm and the Helios hooks, never by agents: an attempt's call, then `controller_id` (children), then `native_session_id`, then the session's Helios ledger. File times are never evidence (INV-22).
- **Completeness:**
  - A missing required join makes the receipt INCOMPLETE (D-10, INV-69).
  - An orphan, a DENY-then-ran event, or execution with no recorded decision makes it INTEGRITY_FAILED (D-12, INV-68).
  - INDEX_LAG from HDB is a diagnostic and never blocks (CORR-05, INV-30).
- **Live subjects.** A READ_ONLY acceptance covers only its observation window (CORR-09, INV-31).
- **Prompts** (CORR-30, CORR-35, INV-44, INV-49):
  - Specification inputs are hashed as exact bytes.
  - The Dispatcher records a dispatch projection (obj-10) only for the prompt it renders, the orchestrator's.
  - Children's prompts follow declared role transforms (obj-06). Assertion A1 recomputes them afterwards from witnesses and checks them against what AutoSwarm recorded.
  - Inputs are hashed again at collection (INV-37).

## 11. Verification and eligibility

- **Environment.** The actual environment (obj-18) is checked against each assertion's constraint (obj-17) before the assertion runs. A mismatch is not a failure.
- **Who decides PASS.** Only registered artifacts at their bound hashes produce results (CORR-39, INV-52). An LLM never produces a PASS (INV-06).
- **Floor.** A delegated contract must include its delegation's mandatory assertions (CORR-14, INV-34).
- **Eligibility** is frozen with the contract (CORR-10):
  - AUTOMATIC contracts accept with no person.
  - HUMAN_REQUIRED contracts need your attestation (obj-37) for each human assertion (INV-32). For source changes, that attestation comes before COMMITTING (CORR-24, INV-38).

## 12. Finalization and publication

- **Authority location** (CORR-25, CORR-31, INV-39, INV-45). Each repository declares it once:
  - LOCAL: the local branch is canonical.
  - REMOTE: the remote branch is canonical, and the local branch is a projection.
- **Publication mode.** v1 publishes only DIRECT_REF_CAS repositories (CORR-45, INV-56).
- **Commit.** COMMITTING writes the verified tree as a commit whose tree and parent are checked (INV-08). For LOCAL that is the canonical branch; for REMOTE it is a sequence-scoped ref (t-s16, t-s17). It never restages the working directory, never forces and never merges (INV-15).
- **Before publication** (SRP): a staging push to a non-canonical remote branch (t-s21, t-s22), then CI on that exact commit (t-s25, t-s26), then the canonical push with an explicit lease (t-s32, t-s33).
- **After publication** (SRPO): a CI failure after publication ends PUBLISHED_UNVERIFIED (t-s40) and mints a ForwardRepairObligation (obj-33).
- **Trailers and staging refs.** Trailers carry IDs and hashes only, never machine paths (INV-27). Staging refs are deleted after acceptance, or after bounded retention, as a receipted effect (eff-staging-delete, obj-41, D-16).

## 13. Acceptance, release and repair

- **Acceptance.** The AcceptanceReceipt (obj-30) binds the record hash, the digest, every receipt the finalization requires, the path taken (automatic, attested or reused) and, for READ_ONLY, the observation window.
- **Release.** Release is durable and resumes after a crash (t-s41, INV-20). A partial PASS releases nothing (INV-19).
- **Forward repair** (CORR-46, CORR-53):
  - The obligation is repaired by the Planner inside a delegation that covers it, up to `max_repairs`; otherwise it goes to your queue (t-o01 to t-o05).
  - While it is open, other source work on that canonical ref is quarantined, except the repair (D-17).
  - Your waiver (obj-34) closes the obligation and lifts the quarantine. It never mints an acceptance and never satisfies a dependency edge (INV-57).

## 14. Delegation and your authority

- **DelegationContract** (obj-00). It bounds, by declaration:
  - domains, repositories, paths, artifact paths, task and effect classes, models
  - publication scope, acceptance eligibility, the mandatory assertion floor
  - decomposition depth, budget and standing permits
- **Budget.** The stricter of the delegation and your config applies (CORR-17).
- **Revocation** is its own record (obj-01, t-d01). Before the PONR it ends the sequences under that delegation. After the PONR it can't undo the effect, and successors are refused at their own reservation (CORR-27).
- **Bootstrap.** Without a delegation, every contract waits for your freeze (D-11, t-c04, t-c05). In the target topology, the backlog and configs are authoring surfaces only (CORR-23).
- **Capabilities only you hold:** cap-01 to cap-06, cap-10, cap-36, cap-44, cap-46, cap-47, cap-54.

## 15. Recovery, concurrency and the single instance

- **Journal.** Every transition is written to the hash-chained journal (obj-36). Crash, restart or replay must reach the same next state (INV-10); Chapter 2 tests every transition boundary with failpoints.
- **Reservation** is one compare-and-swap on state, version and record hash, inside one transaction (INV-12, INV-13).
- **Single instance.** In the interim, an instance lease (obj-39, INV-25a). In the target, an OS lock and the service identity as well (INV-25b).
- **Recovery** reads the target ref of every in-flight effect and applies the outcome rules. A canonical ref found at the new value is finished forward even if the delegation was revoked after the intent.

## 16. Deferred (scope freeze)

- Speculative attempt groups (competing workers on one sequence).
- Reuse of verification evidence across attempts. This is different from CORR-26's reuse of a whole acceptance.
- Finalization of external effects.
- The mediated publication adapter for pull-request or merge-queue repositories.
- ANCESTOR_OK source reuse.
- A separate daemon process.

## 17. The chapters after this one

| Chapter | Proves (runtime tests named in `registries/invariants.json`) |
|---|---|
| 1 | Contracts, identifiers, canonical serialization, golden hashes for all three tiers, registration and transform schemas; U-10 and U-18 |
| 2 | Ledger v4: state model, compare-and-swap reservation, fences, instance lease, permit counters and the two-reservers race, failpoint matrix; the IPC interface, called in-process |
| 3 | Evidence admission: WitnessRecord joins, completeness classes, provenance, input re-hashing, INDEX_LAG |
| 4 | Candidates and verification: artifact sets first (the Luna verifier set, A1's calibrated transforms, U-12), then source trees; artifact retention |
| 5 | Acceptance and release: AUTOMATIC and HUMAN_REQUIRED paths, reuse, observation windows, legacy import (D-07) |
| 6 | The Git sequencer: local commit, staging, remote verification, publication, outcome rules, the revocation timeline, workspace sync, staging retention; U-13 and U-16 |
| 7 | The Planner and Agent Factory as proposers inside delegations, with the floor |
| 8 | The target deployment: service identity, ACLs, the Launcher, the human-presence factor; U-11, U-14 and U-15 |

Open unknowns that block automatic acceptance: U-01, the unexplained DENY-then-ran events. Until it is resolved, AUTOMATIC acceptance of contracts that require Helios integrity stays blocked (D-05).
