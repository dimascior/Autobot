# Chapter 0 registers

Generated from `registries/*.json` by `python check_registries.py --render`. Do not edit by hand: `check_registries.py` fails when this file differs from the registries.

## Principals

| ID | Name | Kind | LLM |
|---|---|---|---|
| you | You (human authority) | human | no |
| registry | Autobot Contract Registry | autobot_seat | no |
| planner | Autobot Planner (Chief Engineer seat) | autobot_seat | yes |
| agent_factory | Autobot Agent Factory | autobot_seat | no |
| sequencer | Autobot Evidence Sequencer | autobot_seat | no |
| dispatcher | Autobot Dispatcher | autobot_seat | no |
| launcher | Autobot Launcher (target topology, runs as you or the agent account) | autobot_seat | no |
| collector | Autobot Evidence Collector | autobot_seat | no |
| git_adapter | Autobot Git adapter | autobot_seat | no |
| environment_probe | Autobot Environment probe | autobot_seat | no |
| verifier_runner | Autobot Verifier runner | autobot_seat | no |
| git_sequencer | Autobot Git sequencer | autobot_seat | no |
| recovery | Autobot Recovery (triggers transitions; the Sequencer applies them) | autobot_seat | no |
| authority_cli | Autobot authority CLI (thin; submits your requests) | autobot_seat | no |
| autobot_service | Autobot authority service boundary (IPC, AuthorityAuthenticationProvider) | autobot_seat | no |
| autobot_code | Autobot code (schema, migrations, retention) | autobot_seat | no |
| migration | Autobot v3 to v4 migration | autobot_seat | no |
| autoswarm | AutoSwarm | external_program | no |
| helios | Helios command gate | external_program | no |
| akashic | Akashic | external_program | no |
| hdb | HDB / HeliosEvidenceCatalog | external_program | no |
| git | Git repositories and remotes | external_program | no |
| ci_system | CI system (for example GitHub Actions) | external_program | no |
| agent_clis | claude and codex CLIs (as transcript writers) | external_program | no |
| registered_verifiers | Registered deterministic verifier programs | external_program | no |
| task_force | Task Force agents (orchestrator, worker, monitor, poller) | agent | yes |

## Capabilities

| ID | Capability | Owner | Others | Forbidden to | Enforced (interim / target) |
|---|---|---|---|---|---|
| cap-01 | Objective and intent | you | planner R | task_force | policy / presence |
| cap-02 | Content of contracts you write | you | registry C | task_force | policy / presence |
| cap-03 | DelegationContract grants | you | registry C, sequencer C | task_force, planner | policy / presence |
| cap-04 | Delegation revocation | you | sequencer C, recovery C | task_force, planner | policy / presence |
| cap-05 | Repository authority declarations | you | registry C, git_sequencer C | task_force, planner | policy / presence |
| cap-06 | Verifier and transform registrations | you | verifier_runner C, registry C | task_force, planner | policy / presence |
| cap-07 | Content of descendant contracts inside a delegation | planner | registry C | task_force | code / ipc_acl |
| cap-08 | Admission and freeze of TaskContractRecords (three tiers, bindings, duplicates) | registry | sequencer C, dispatcher C | planner, task_force | code / ipc_acl |
| cap-09 | Revision minting | registry | sequencer C | task_force | code / ipc_acl |
| cap-10 | Supersession and abandonment decisions | you | registry C, sequencer C | task_force, planner | policy / presence |
| cap-11 | Readiness (edges by tier, freshness, quarantine, finalizer support) | sequencer | planner R | none | code / ipc_acl |
| cap-12 | Fulfillment reuse | sequencer | none | planner, task_force | code / ipc_acl |
| cap-13 | Batch proposal | planner | sequencer C | none | code / ipc_acl |
| cap-14 | Seat, route and model selection | agent_factory | dispatcher C | task_force | code / ipc_acl |
| cap-15 | Reservation, attempt creation, fencing | sequencer | none | none | code / ipc_acl |
| cap-16 | Leases and staleness | sequencer | recovery C | none | code / ipc_acl |
| cap-17 | Dispatch projection and launch request | dispatcher | launcher C, autoswarm C | none | code / ipc_acl |
| cap-18 | Launch relay under your or the agent identity | launcher | autoswarm C | none | code / own |
| cap-19 | Launch and session lifecycle | autoswarm | collector C | none | own / own |
| cap-20 | Command admission | helios | collector R | sequencer | own / own |
| cap-21 | Helios runtime integrity | akashic | helios C | sequencer | own / own |
| cap-22 | Doing the work, including children's prompt transforms | task_force | registered_verifiers R | none | own / own |
| cap-23 | Watching the worker | task_force | collector R | none | own / own |
| cap-24 | Execution-lifecycle WitnessRecords | autoswarm | collector C | none | own / own |
| cap-25 | Command-governance WitnessRecords | helios | collector C, hdb C | none | own / own |
| cap-26 | Session-transcript WitnessRecords | agent_clis | collector C | none | own / own |
| cap-27 | Remote-check WitnessRecords | ci_system | verifier_runner C | none | own / own |
| cap-28 | Cross-session index | hdb | collector R, registered_verifiers R | none | own / own |
| cap-29 | Subject-state capture (observation start and end) | collector | registered_verifiers C | none | code / ipc_acl |
| cap-30 | ExecutionReceipt (admission, completeness per role, index state) | collector | verifier_runner C, sequencer C | task_force | code / ipc_acl |
| cap-31 | Source-state capture (Git) | git_adapter | sequencer C | none | code / ipc_acl |
| cap-32 | CandidateReceipt | git_adapter | verifier_runner C, git_sequencer C | task_force | code / ipc_acl |
| cap-33 | Environment capture | environment_probe | verifier_runner C | none | code / ipc_acl |
| cap-34 | Assertion evaluation, including role-transform checks | registered_verifiers | verifier_runner C | task_force, planner | own / own |
| cap-35 | Verification, failure and remote-verification receipts | verifier_runner | sequencer C | task_force | code / ipc_acl |
| cap-36 | Per-sequence publication permits | you | git_sequencer C | task_force, planner | policy / presence |
| cap-37 | Permit use accounting (identity rows, counters, leases) | sequencer | git_sequencer C | none | code / ipc_acl |
| cap-38 | Local commit and local ref CAS | git_sequencer | sequencer C | task_force | code / ipc_acl |
| cap-39 | Staging publication and staging-ref deletion | git_sequencer | verifier_runner C | task_force | code / ipc_acl |
| cap-40 | Canonical remote publication | git_sequencer | sequencer C | task_force | code / ipc_acl |
| cap-41 | Workspace sync (not authority) | git_sequencer | you O | none | code / ipc_acl |
| cap-42 | What tree a ref names | git | git_adapter R, git_sequencer C | none | own / own |
| cap-43 | Acceptance | sequencer | you O | planner, task_force, autoswarm | code / ipc_acl |
| cap-44 | Manual attestation (assertions and effect outcomes) | you | verifier_runner C | task_force, planner | policy / presence |
| cap-45 | Dependency release | sequencer | none | none | code / ipc_acl |
| cap-46 | Authorizing external effects | you | sequencer O | task_force, planner | policy / presence |
| cap-47 | Caps, STOP, dry run, routes, budgets | you | sequencer C, agent_factory C | task_force | policy / presence |
| cap-48 | Authenticating authority requests | autobot_service | none | none | code / ipc_acl |
| cap-49 | Single instance | sequencer | none | none | code / ipc_acl |
| cap-50 | Ledger schema and migrations | autobot_code | none | none | code / ipc_acl |
| cap-51 | Applying Sequence, Attempt, intent and reservation transitions; the hash-chained journal | sequencer | recovery C, you O | task_force, planner | code / ipc_acl |
| cap-52 | Effect intents: issue, expire, reconcile outcome | sequencer | git_sequencer C | none | code / ipc_acl |
| cap-53 | Forward repair obligations | sequencer | planner R | none | code / ipc_acl |
| cap-54 | Repair waivers | you | sequencer C | task_force, planner | policy / presence |
| cap-55 | Content-store retention of programs and witness copies | autobot_code | none | none | code / ipc_acl |

## Objects

| ID | Object | Minter | Class | Mutability | Attempt-bound |
|---|---|---|---|---|---|
| obj-00 | DelegationContract | you | authority_record | immutable; never edited to revoke | no |
| obj-01 | DelegationRevocationRecord | you | authority_record | immutable | no |
| obj-02 | RepositoryAuthorityDeclaration (authority_location, canonical ref, publication_mode) | you | authority_record | immutable; a change is a new declaration | no |
| obj-03 | VerifierRegistration / TransformRegistration (artifact closure) | you | authority_record | immutable; a change is a new registration | no |
| obj-04 | TaskContractRecord (three identity tiers, resolved bindings) | registry | contract | immutable once FROZEN | no |
| obj-05 | SpecificationInputs (exact-byte hashes) | registry | contract_part | immutable | no |
| obj-06 | RoleTransform | registry | contract_part | immutable | no |
| obj-07 | DependencyEdge (required record, contract or requirement) | registry | contract_part | immutable | no |
| obj-08 | Sequence | sequencer | state | CAS transitions only | no |
| obj-09 | AttemptContract | sequencer | contract | immutable | no |
| obj-10 | PromptProjection (dispatch) | dispatcher | non_authoritative_receipt | immutable | no |
| obj-11 | WitnessRecord: execution lifecycle | autoswarm | witness | producer-owned; content-addressed copy on admission | no |
| obj-12 | WitnessRecord: command governance | helios | witness | producer-owned; content-addressed copy on admission | no |
| obj-13 | WitnessRecord: session transcript | agent_clis | witness | producer-owned; content-addressed copy on admission | no |
| obj-14 | WitnessRecord: remote check | ci_system | witness | producer-owned; content-addressed copy on admission | no |
| obj-15 | ExecutionReceipt | collector | receipt | append-only; re-collection makes a new version | yes |
| obj-16 | CandidateReceipt | git_adapter | receipt | immutable | yes |
| obj-17 | VerificationEnvironmentConstraint | registry | contract_part | immutable | no |
| obj-18 | ActualVerificationEnvironment | environment_probe | receipt | immutable | yes |
| obj-19 | VerificationReceipt | verifier_runner | receipt | immutable | yes |
| obj-20 | VerificationFailureReceipt | verifier_runner | receipt | immutable | yes |
| obj-21 | RemoteVerificationReceipt | verifier_runner | receipt | immutable | yes |
| obj-22 | PublicationPermit (per-sequence grant) | you | authority_record | single use; tracked as a one-use StandingPermitState | no |
| obj-23 | StandingPermitState (permitted, consumed, reserved) | sequencer | permit_state | guarded counter updates only | no |
| obj-24 | PermitUseReservation (identity, status, lease) | sequencer | permit_state | status RESERVED to CONSUMED or RELEASED, once | no |
| obj-25 | EffectIntent | sequencer | intent | outcome transitions only | no |
| obj-26 | CommitReceipt | git_sequencer | receipt | immutable | yes |
| obj-27 | StagingPublicationReceipt | git_sequencer | receipt | immutable | yes |
| obj-28 | PublicationReceipt | git_sequencer | receipt | immutable | yes |
| obj-29 | WorkspaceSyncReceipt | git_sequencer | non_authoritative_receipt | immutable | no |
| obj-30 | AcceptanceReceipt | sequencer | receipt | immutable | no |
| obj-31 | FulfillmentReuseReceipt (dominance proof) | sequencer | receipt | immutable | no |
| obj-32 | DuplicateAdmissionRecord | registry | journal | immutable | no |
| obj-33 | ForwardRepairObligation | sequencer | obligation | status transitions only | no |
| obj-34 | RepairWaiver | you | authority_record | immutable | no |
| obj-35 | DependencyReleaseReceipt | sequencer | receipt | immutable | no |
| obj-36 | TransitionRecord | sequencer | journal | append-only hash chain | no |
| obj-37 | ManualAttestation (kind ASSERTION or EFFECT_OUTCOME) | you | authority_record | immutable | no |
| obj-38 | SupersessionRecord / AbandonRecord | you | authority_record | immutable | no |
| obj-39 | InstanceLease | sequencer | lease | CAS heartbeat | no |
| obj-40 | LegacyRecord | migration | legacy | read-only | no |
| obj-41 | StagingRefDeletionReceipt | git_sequencer | receipt | immutable | no |

## Storage (D-04)

| Store | Holds |
|---|---|
| ledger | authoritative metadata, identities, hashes, bindings, state transitions and receipt references |
| content_store | immutable receipt payloads and retained evidence and artifact bytes, addressed by hash |
| hdb | index only: HDB never gates completeness, acceptance or state unless a TaskContract declares an HDB-derived assertion; Git carries source proof and path-free trailers only |
| git | source proof and path-free references only |

| Object class | Stored in |
|---|---|
| authority_record | ledger+content_store |
| contract | ledger+content_store |
| contract_part | ledger |
| state | ledger |
| receipt | ledger+content_store |
| non_authoritative_receipt | ledger+content_store |
| witness | producer original; admitted copy in content_store, referenced from the ledger |
| journal | ledger |
| intent | ledger |
| permit_state | ledger |
| obligation | ledger |
| lease | ledger |
| legacy | ledger |

## State machines

### sequence (initial DECLARED)

| State | Terminal | Waits for you |
|---|---|---|
| DECLARED | no | no |
| BLOCKED | no | no |
| BLOCKED_UNSUPPORTED_FINALIZER | no | yes |
| READY | no | no |
| EXECUTING | no | no |
| CANDIDATE_FROZEN | no | no |
| VERIFYING | no | no |
| VERIFIED | no | no |
| COMMITTING | no | no |
| COMMITTED | no | no |
| STAGING_PUBLISHING | no | no |
| STAGED | no | no |
| REMOTE_VERIFYING | no | no |
| REMOTE_VERIFIED | no | no |
| PUBLISHING | no | no |
| PUBLISHED | no | no |
| POST_PUBLICATION_OBSERVING | no | no |
| ACCEPTED | no | no |
| DEPENDENCIES_RELEASED | no | no |
| COMPLETED | yes | no |
| HALTED | no | yes |
| HALTED_AFTER_VERIFICATION | no | no |
| SUPERSEDED | yes | no |
| ABANDONED | yes | no |
| DELEGATION_REVOKED | yes | no |
| PUBLISHED_UNVERIFIED | yes | no |

### attempt (initial DECLARED)

| State | Terminal | Waits for you |
|---|---|---|
| DECLARED | no | no |
| RESERVED | no | no |
| DISPATCHED | no | no |
| RUNNING | no | no |
| FINISHED | no | no |
| CANDIDATE_FROZEN | no | no |
| EVIDENCE_COMPLETE | no | no |
| VERIFIED | yes | no |
| REJECTED | yes | no |
| FAILED | yes | no |
| STALE | yes | no |
| FENCED | yes | no |
| RECONCILED | yes | no |

### effect_intent (initial PENDING)

| State | Terminal | Waits for you |
|---|---|---|
| PENDING | no | no |
| HAPPENED | yes | no |
| ABSENT | yes | no |
| UNKNOWN_OUTCOME | no | no |
| SUPERSEDED_BY_RETRY | yes | no |
| HUMAN_RESOLVED | yes | no |

### permit_reservation (initial RESERVED)

| State | Terminal | Waits for you |
|---|---|---|
| RESERVED | no | no |
| CONSUMED | yes | no |
| RELEASED | yes | no |

### forward_repair_obligation (initial OPEN)

| State | Terminal | Waits for you |
|---|---|---|
| OPEN | no | no |
| REPAIR_DISPATCHED | no | no |
| RESOLVED | yes | no |
| WAIVED | yes | no |

### delegation (initial ACTIVE)

| State | Terminal | Waits for you |
|---|---|---|
| ACTIVE | no | no |
| REVOKED | yes | no |
| EXPIRED | yes | no |

### contract_admission (initial RECEIVED)

| State | Terminal | Waits for you |
|---|---|---|
| RECEIVED | no | no |
| DRAFT | no | no |
| QUEUED_FOR_YOU | no | yes |
| FROZEN | yes | no |
| DUPLICATE | yes | no |
| REJECTED | yes | no |

## Finalization paths

| Path | Name | States | Side states before the PONR | PONR |
|---|---|---|---|---|
| RO | READ_ONLY | DECLARED → READY → EXECUTING → CANDIDATE_FROZEN → VERIFYING → VERIFIED → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | BLOCKED, HALTED | t-s14 |
| SL | SOURCE_MODIFYING, authority_location LOCAL | DECLARED → READY → EXECUTING → CANDIDATE_FROZEN → VERIFYING → VERIFIED → COMMITTING → COMMITTED → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | BLOCKED, HALTED | t-s16 |
| SRD | SOURCE_MODIFYING, REMOTE, DIRECT_REF_CAS, no remote check | DECLARED → READY → EXECUTING → CANDIDATE_FROZEN → VERIFYING → VERIFIED → COMMITTING → COMMITTED → PUBLISHING → PUBLISHED → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | BLOCKED, BLOCKED_UNSUPPORTED_FINALIZER, HALTED | t-s33 |
| SRP | SOURCE_MODIFYING, REMOTE, PRE_PUBLICATION_REMOTE_VERIFICATION | DECLARED → READY → EXECUTING → CANDIDATE_FROZEN → VERIFYING → VERIFIED → COMMITTING → COMMITTED → STAGING_PUBLISHING → STAGED → REMOTE_VERIFYING → REMOTE_VERIFIED → PUBLISHING → PUBLISHED → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | BLOCKED, BLOCKED_UNSUPPORTED_FINALIZER, HALTED, HALTED_AFTER_VERIFICATION | t-s33 |
| SRPO | SOURCE_MODIFYING, REMOTE, POST_PUBLICATION_OBSERVATION | DECLARED → READY → EXECUTING → CANDIDATE_FROZEN → VERIFYING → VERIFIED → COMMITTING → COMMITTED → PUBLISHING → PUBLISHED → POST_PUBLICATION_OBSERVING → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | BLOCKED, BLOCKED_UNSUPPORTED_FINALIZER, HALTED | t-s33 |
| RUD | Reuse at admission | DECLARED → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | none | t-s43 |
| RUR | Reuse once ready | DECLARED → READY → ACCEPTED → DEPENDENCIES_RELEASED → COMPLETED | BLOCKED | t-s44 |

## Transitions

| ID | Machine | From → to | Actor (trigger) | Capabilities | Requires | Mints | Effect / intent | Preconditions | Paths | PONR |
|---|---|---|---|---|---|---|---|---|---|---|
| t-s01 | sequence | DECLARED → BLOCKED | sequencer | cap-11 | none | none | n/a | edge_unsatisfied | ALL_EXEC, RUR | n/a |
| t-s02 | sequence | BLOCKED → READY | sequencer | cap-11 | none | none | n/a | all_edges_satisfied_by_declared_tier, freshness_reevaluated_now, finalizer_supported_in_v1, not_quarantined_or_is_repair | ALL_EXEC, RUR | n/a |
| t-s03 | sequence | DECLARED → READY | sequencer | cap-11 | none | none | n/a | all_edges_satisfied_by_declared_tier, freshness_reevaluated_now, finalizer_supported_in_v1, not_quarantined_or_is_repair | ALL_EXEC, RUR | n/a |
| t-s04 | sequence | DECLARED → BLOCKED_UNSUPPORTED_FINALIZER | sequencer | cap-11 | none | none | n/a | bound_publication_mode_mediated, requires_canonical_source_publication | REMOTE | n/a |
| t-s06 | sequence | READY → EXECUTING | sequencer | cap-15 | none | obj-09 | n/a | cas_state_version_record_hash, effective_budget_available, instance_lease_held, delegation_active, not_quarantined_or_is_repair | ALL_EXEC | n/a |
| t-s07 | sequence | EXECUTING → CANDIDATE_FROZEN | sequencer | cap-51 | obj-16 | none | n/a | attempt_finished_per_completion_policy, fence_matches | ALL_EXEC | n/a |
| t-s08 | sequence | EXECUTING → READY | sequencer | cap-16 | none | none | n/a | attempt_failed_or_stale, retry_budget_left | ALL_EXEC | n/a |
| t-s09 | sequence | CANDIDATE_FROZEN → VERIFYING | sequencer | cap-51 | obj-15 | none | n/a | execution_receipt_complete, fence_matches | ALL_EXEC | n/a |
| t-s45 | sequence | CANDIDATE_FROZEN → HALTED | sequencer | cap-51 | obj-15 | none | n/a | execution_receipt_integrity_failed_or_incomplete_beyond_bound, fence_matches | ALL_EXEC | n/a |
| t-s10 | sequence | VERIFYING → VERIFIED | sequencer | cap-51 | obj-19 | none | n/a | required_assertions_pass_or_attested, bound_registered_artifacts_only, fence_matches, attestation_before_commit_if_source_human_required | ALL_EXEC | n/a |
| t-s11 | sequence | VERIFYING → READY | sequencer | cap-51 | obj-20 | none | n/a | assertion_failed, repair_budget_left, fence_matches | ALL_EXEC | n/a |
| t-s12 | sequence | VERIFYING → HALTED | sequencer | cap-51 | none | none | n/a | unknown_or_env_mismatch_or_budget_exhausted_or_bound_artifact_unavailable | ALL_EXEC | n/a |
| t-s13 | sequence | HALTED → READY | sequencer (you) | cap-51 | none | none | n/a | your_budget_grant_or_attestation, revalidated_preconditions | ALL_EXEC | n/a |
| t-s14 | sequence | VERIFIED → ACCEPTED | sequencer | cap-43 | obj-19 | obj-30 | n/a | read_only, automatic_or_attested, fence_matches | RO | yes |
| t-s15a | sequence | VERIFIED → COMMITTING | sequencer | cap-37, cap-52 | none | obj-24, obj-25 | intent for eff-local-canonical-cas | source_modifying, permit_reserved, delegation_active_at_intent, cas_state_version_attempt_fence, fence_matches, effect_intent_committed, intent_ttl_gt_hard_timeout | SL | n/a |
| t-s15b | sequence | VERIFIED → COMMITTING | sequencer | cap-37, cap-52 | none | obj-24, obj-25 | intent for eff-local-sequence-ref | source_modifying, permit_reserved, delegation_active_at_intent, cas_state_version_attempt_fence, fence_matches, effect_intent_committed, intent_ttl_gt_hard_timeout | REMOTE | n/a |
| t-s16 | sequence | COMMITTING → COMMITTED | sequencer | cap-51, cap-37 | obj-26 | none | eff-local-canonical-cas | effect_happened, fence_matches, reservation_consumed | SL | yes |
| t-s17 | sequence | COMMITTING → COMMITTED | sequencer | cap-51 | obj-26 | none | eff-local-sequence-ref | effect_happened, fence_matches | REMOTE | n/a |
| t-s18 | sequence | COMMITTING → READY | sequencer | cap-51 | obj-26 | none | n/a | canonical_ref_moved_expected_old_stale, reservation_released, fence_matches | SL | n/a |
| t-s19 | sequence | COMMITTING → VERIFIED | sequencer | cap-51 | none | none | n/a | effect_absent_per_outcome_rule, reservation_released | SOURCE | n/a |
| t-s20 | sequence | COMMITTED → ACCEPTED | sequencer | cap-43 | obj-26 | obj-30 | n/a | finalization_requirements_satisfied, fence_matches | SL | n/a |
| t-s21 | sequence | COMMITTED → STAGING_PUBLISHING | sequencer | cap-51, cap-52 | none | obj-25 | intent for eff-staging-push | pre_publication_mode, staging_ref_in_permit_scope, delegation_active_at_intent, reservation_held, effect_intent_committed, fence_matches, intent_ttl_gt_hard_timeout | SRP | n/a |
| t-s22 | sequence | STAGING_PUBLISHING → STAGED | sequencer | cap-51 | obj-27 | none | eff-staging-push | effect_happened, fence_matches | SRP | n/a |
| t-s23 | sequence | STAGING_PUBLISHING → COMMITTED | sequencer | cap-51 | none | none | n/a | effect_absent_per_outcome_rule, staging_retry_budget_left | SRP | n/a |
| t-s24 | sequence | STAGING_PUBLISHING → READY | sequencer | cap-51 | none | none | n/a | staging_failed_beyond_budget, reservation_released | SRP | n/a |
| t-s25 | sequence | STAGED → REMOTE_VERIFYING | sequencer | cap-51, cap-37 | obj-27 | none | n/a | ci_started_for_exact_sha, reservation_lease_renewing, fence_matches | SRP | n/a |
| t-s26 | sequence | REMOTE_VERIFYING → REMOTE_VERIFIED | sequencer | cap-51 | obj-21 | none | n/a | remote_check_passed_exact_sha, fence_matches | SRP | n/a |
| t-s27 | sequence | REMOTE_VERIFYING → READY | sequencer | cap-51 | obj-21 | none | n/a | remote_check_failed, reservation_released, fence_matches | SRP | n/a |
| t-s28 | sequence | REMOTE_VERIFYING → HALTED_AFTER_VERIFICATION | sequencer | cap-51, cap-37 | none | none | n/a | reservation_lease_expired_or_ci_wait_exceeded, reservation_released, receipts_retained | SRP | n/a |
| t-s29 | sequence | HALTED_AFTER_VERIFICATION → REMOTE_VERIFYING | sequencer | cap-51, cap-37 | obj-27 | obj-24 | n/a | revalidated_candidate_source_fence_staging_sha_environment_delegation, fresh_reservation_acquired, delegation_active, fence_matches | SRP | n/a |
| t-s30 | sequence | HALTED_AFTER_VERIFICATION → READY | sequencer | cap-51 | none | none | n/a | revalidation_failed_new_attempt | SRP | n/a |
| t-s31 | sequence | COMMITTED → PUBLISHING | sequencer | cap-51, cap-52 | none | obj-25 | intent for eff-canonical-push | direct_ref_cas_mode, delegation_active_at_intent, reservation_held, fence_matches, effect_intent_committed, intent_ttl_gt_hard_timeout | SRD, SRPO | n/a |
| t-s32 | sequence | REMOTE_VERIFIED → PUBLISHING | sequencer | cap-51, cap-52 | none | obj-25 | intent for eff-canonical-push | direct_ref_cas_mode, delegation_active_at_intent, reservation_held, fence_matches, effect_intent_committed, intent_ttl_gt_hard_timeout | SRP | n/a |
| t-s33 | sequence | PUBLISHING → PUBLISHED | sequencer | cap-51, cap-37 | obj-28 | none | eff-canonical-push | effect_happened, fence_matches, reservation_consumed | REMOTE | yes |
| t-s34 | sequence | PUBLISHING → READY | sequencer | cap-51 | none | none | n/a | canonical_ref_neither_expected_old_nor_target, reservation_released | REMOTE | n/a |
| t-s35 | sequence | PUBLISHING → REMOTE_VERIFIED | sequencer | cap-51 | none | none | n/a | effect_absent_by_positive_cancellation_proof, reservation_released | SRP | n/a |
| t-s36 | sequence | PUBLISHING → COMMITTED | sequencer | cap-51 | none | none | n/a | effect_absent_by_positive_cancellation_proof, reservation_released | SRD, SRPO | n/a |
| t-s37 | sequence | PUBLISHED → ACCEPTED | sequencer | cap-43 | obj-28 | obj-30 | n/a | finalization_requirements_satisfied, fence_matches | SRD, SRP | n/a |
| t-s38 | sequence | PUBLISHED → POST_PUBLICATION_OBSERVING | sequencer | cap-51 | none | none | n/a | post_publication_mode | SRPO | n/a |
| t-s39 | sequence | POST_PUBLICATION_OBSERVING → ACCEPTED | sequencer | cap-43 | obj-21 | obj-30 | n/a | remote_check_passed_exact_sha, fence_matches | SRPO | n/a |
| t-s40 | sequence | POST_PUBLICATION_OBSERVING → PUBLISHED_UNVERIFIED | sequencer | cap-53 | obj-21 | obj-33 | n/a | remote_check_failed_after_ponr, fence_matches | SRPO | n/a |
| t-s41 | sequence | ACCEPTED → DEPENDENCIES_RELEASED | sequencer | cap-45 | obj-30 | obj-35 | n/a | successor_edges_durably_satisfied | ALL_EXEC, REUSE | n/a |
| t-s42 | sequence | DEPENDENCIES_RELEASED → COMPLETED | sequencer | cap-51 | obj-35 | none | n/a | none | ALL_EXEC, REUSE | n/a |
| t-s43 | sequence | DECLARED → ACCEPTED | sequencer | cap-12, cap-43 | none | obj-31, obj-30 | n/a | same_requirement_digest, requester_reuse_policy_permits, dominating_finalization_proof, bindings_equal_or_policy_alternative, fresh_now, exact_canonical_head_if_source | RUD | yes |
| t-s44 | sequence | READY → ACCEPTED | sequencer | cap-12, cap-43 | none | obj-31, obj-30 | n/a | same_requirement_digest, requester_reuse_policy_permits, dominating_finalization_proof, bindings_equal_or_policy_alternative, fresh_now, exact_canonical_head_if_source | RUR | yes |
| t-a01 | attempt | DECLARED → RESERVED | sequencer | cap-15 | obj-09 | none | n/a | none | none | n/a |
| t-a02 | attempt | RESERVED → DISPATCHED | sequencer (dispatcher) | cap-51 | obj-10 | none | n/a | call_id_bound | none | n/a |
| t-a03 | attempt | DISPATCHED → RUNNING | sequencer (collector) | cap-51 | obj-11 | none | n/a | handle_with_native_session_id | none | n/a |
| t-a04 | attempt | RUNNING → FINISHED | sequencer | cap-51 | obj-11 | none | n/a | completion_policy_satisfied_per_role | none | n/a |
| t-a05 | attempt | FINISHED → CANDIDATE_FROZEN | sequencer | cap-51 | obj-16 | none | n/a | fence_matches | none | n/a |
| t-a06 | attempt | CANDIDATE_FROZEN → EVIDENCE_COMPLETE | sequencer | cap-51 | obj-15 | none | n/a | fence_matches, required_joins_present | none | n/a |
| t-a13 | attempt | CANDIDATE_FROZEN → FAILED | sequencer | cap-51 | obj-15 | none | n/a | execution_receipt_integrity_failed_or_incomplete_beyond_bound, fence_matches | none | n/a |
| t-a07 | attempt | EVIDENCE_COMPLETE → VERIFIED | sequencer | cap-51 | obj-19 | none | n/a | fence_matches | none | n/a |
| t-a08 | attempt | EVIDENCE_COMPLETE → REJECTED | sequencer | cap-51 | obj-20 | none | n/a | fence_matches | none | n/a |
| t-a09 | attempt | RESERVED → FAILED | sequencer | cap-51 | none | none | n/a | launch_failed | none | n/a |
| t-a10 | attempt | DISPATCHED → FAILED | sequencer | cap-51 | none | none | n/a | launch_failed_after_call_id | none | n/a |
| t-a11 | attempt | RUNNING → FAILED | sequencer | cap-51 | none | none | n/a | required_success_member_not_success | none | n/a |
| t-a12 | attempt | RUNNING → STALE | sequencer | cap-16 | none | none | n/a | lease_expired_required_member_without_result | none | n/a |
| t-i01 | effect_intent | PENDING → HAPPENED | sequencer | cap-52 | none | none | n/a | target_observed_at_new_value | none | n/a |
| t-i02 | effect_intent | PENDING → ABSENT | sequencer | cap-52 | none | none | n/a | absence_proven_per_outcome_rule | none | n/a |
| t-i03 | effect_intent | PENDING → UNKNOWN_OUTCOME | sequencer | cap-52 | none | none | n/a | expired_and_target_at_expected_old_or_unobservable | none | n/a |
| t-i04 | effect_intent | UNKNOWN_OUTCOME → HAPPENED | sequencer | cap-52 | none | none | n/a | target_observed_at_new_value | none | n/a |
| t-i05 | effect_intent | UNKNOWN_OUTCOME → ABSENT | sequencer | cap-52 | none | none | n/a | stale_expected_old_observed_or_positive_cancellation_proof | none | n/a |
| t-i06 | effect_intent | UNKNOWN_OUTCOME → SUPERSEDED_BY_RETRY | sequencer | cap-52, cap-37 | none | obj-25 | n/a | authority_active_now, reservation_held, identical_effect_same_target_same_expected_old | none | n/a |
| t-i07 | effect_intent | UNKNOWN_OUTCOME → HUMAN_RESOLVED | sequencer (you) | cap-52 | obj-37 | none | n/a | authority_inactive, your_effect_outcome_attestation_or_retry_permit | none | n/a |
| t-r01 | permit_reservation | RESERVED → CONSUMED | sequencer | cap-37 | none | none | n/a | ponr_effect_happened, row_status_reserved_rowcount_1 | none | n/a |
| t-r02 | permit_reservation | RESERVED → RELEASED | sequencer | cap-37 | none | none | n/a | pre_ponr_exit_and_no_pending_or_unknown_intent, idempotent_rowcount_0_or_1 | none | n/a |
| t-o01 | forward_repair_obligation | OPEN → REPAIR_DISPATCHED | sequencer | cap-53 | none | none | n/a | delegation_covers_repair_or_your_record, chain_depth_within_max_repairs | none | n/a |
| t-o02 | forward_repair_obligation | REPAIR_DISPATCHED → RESOLVED | sequencer | cap-53 | obj-30 | none | n/a | repair_acceptance_same_requirement_digest | none | n/a |
| t-o03 | forward_repair_obligation | REPAIR_DISPATCHED → OPEN | sequencer | cap-53 | none | none | n/a | repair_sequence_terminal_without_acceptance | none | n/a |
| t-o04 | forward_repair_obligation | OPEN → WAIVED | sequencer (you) | cap-53 | obj-34 | none | n/a | lifts_quarantine, mints_no_acceptance_and_satisfies_no_edge | none | n/a |
| t-o05 | forward_repair_obligation | REPAIR_DISPATCHED → WAIVED | sequencer (you) | cap-53 | obj-34 | none | n/a | lifts_quarantine, mints_no_acceptance_and_satisfies_no_edge | none | n/a |
| t-d01 | delegation | ACTIVE → REVOKED | you | cap-04 | none | obj-01 | n/a | authenticated_authority_request | none | n/a |
| t-d02 | delegation | ACTIVE → EXPIRED | sequencer | cap-51 | none | none | n/a | now_at_or_after_expires_utc | none | n/a |
| t-c01 | contract_admission | RECEIVED → DRAFT | registry | cap-08 | none | none | n/a | parsed | none | n/a |
| t-c02 | contract_admission | DRAFT → FROZEN | registry | cap-08 | none | obj-04, obj-05, obj-06, obj-07, obj-17 | n/a | inputs_resolved_exact_bytes, assertions_bound_to_registration_records, transforms_bound_to_registration_records, repository_declaration_bound_and_matching, scope_and_floor_within_active_delegation_or_authored_by_you, eligibility_computed, no_live_record_same_contract_digest_or_force_new_sequence | none | n/a |
| t-c03 | contract_admission | DRAFT → DUPLICATE | registry | cap-08 | none | obj-32 | n/a | same_contract_digest_live_sequence_no_force_new | none | n/a |
| t-c04 | contract_admission | DRAFT → QUEUED_FOR_YOU | registry | cap-08 | none | none | n/a | outside_delegation_scope_or_floor | none | n/a |
| t-c05 | contract_admission | QUEUED_FOR_YOU → FROZEN | registry (you) | cap-08 | none | obj-04, obj-05, obj-06, obj-07, obj-17 | n/a | your_authenticated_freeze, inputs_resolved_exact_bytes, assertions_bound_to_registration_records | none | n/a |
| t-c06 | contract_admission | QUEUED_FOR_YOU → REJECTED | registry (you) | cap-08 | none | none | n/a | your_decision | none | n/a |
| t-c07 | contract_admission | DRAFT → REJECTED | registry | cap-08 | none | none | n/a | unresolved_input_or_unbound_assertion_or_location_mismatch | none | n/a |

## Wildcards

| ID | Machine | From | To | Actor (trigger) | Requires | Preconditions | Paths |
|---|---|---|---|---|---|---|---|
| w-s01 | sequence | any state before the PONR | DELEGATION_REVOKED | sequencer (you) | obj-01 | revocation_committed_before_next_linearization_point, no_pending_or_unknown_intent, fence_bumped, reservation_released | ALL_EXEC, REUSE |
| w-s02 | sequence | any state before the PONR | SUPERSEDED | sequencer (you) | obj-38 | your_record, no_pending_or_unknown_intent, fence_bumped, reservation_released | ALL_EXEC, REUSE |
| w-s03 | sequence | any state before the PONR | ABANDONED | sequencer (you) | obj-38 | your_record, no_pending_or_unknown_intent, fence_bumped, reservation_released | ALL_EXEC, REUSE |
| w-a01 | attempt | any non-terminal state | FENCED | sequencer | none | newer_fence_issued | none |
| w-a02 | attempt | any non-terminal state | RECONCILED | sequencer (recovery) | none | recovery_resolved_after_crash | none |

## Effects

| ID | Type | Target | Executor | Capability | Canonical on | Receipt | Outcome rule |
|---|---|---|---|---|---|---|---|
| eff-local-canonical-cas | LOCAL_REF_CAS | canonical local branch | git_sequencer | cap-38 | SL | obj-26 | local |
| eff-local-sequence-ref | LOCAL_REF_CAS | refs/autobot/<seq>/<att> | git_sequencer | cap-38 | none | obj-26 | local |
| eff-staging-push | STAGING_PUSH | remote refs/heads/autobot/<seq>/<att> | git_sequencer | cap-39 | none | obj-27 | remote_create |
| eff-canonical-push | CANONICAL_PUSH | remote canonical ref, lease = expected old | git_sequencer | cap-40 | SRD, SRP, SRPO | obj-28 | remote_cas |
| eff-staging-delete | STAGING_REF_DELETE | remote staging ref, after ACCEPTED or retention expiry (D-16) | git_sequencer | cap-39 | none | obj-41 | remote_delete |
| eff-workspace-sync | LOCAL_REF_CAS | your local branch, only when safe (CORR-32) | git_sequencer | cap-41 | none | obj-29 | local |

## Outcome rules

| Rule | Observation | Outcome |
|---|---|---|
| local | target_equals_new | HAPPENED |
| local | target_equals_expected_old_and_executor_fenced_and_no_ref_lock | ABSENT |
| local | target_equals_expected_old_otherwise | UNKNOWN_OUTCOME |
| local | target_other | ABSENT |
| remote_cas | target_equals_new | HAPPENED |
| remote_cas | target_equals_expected_old | UNKNOWN_OUTCOME |
| remote_cas | target_other | ABSENT |
| remote_cas | unobservable | UNKNOWN_OUTCOME |
| remote_create | target_equals_new | HAPPENED |
| remote_create | target_absent | UNKNOWN_OUTCOME |
| remote_create | target_other | ABSENT |
| remote_create | unobservable | UNKNOWN_OUTCOME |
| remote_delete | target_absent | HAPPENED |
| remote_delete | target_equals_expected_old | UNKNOWN_OUTCOME |
| remote_delete | target_other | ABSENT |
| remote_delete | unobservable | UNKNOWN_OUTCOME |

## Policy defaults

| Setting | Value |
|---|---|
| status | configuration defaults, not semantic constants (D-18); effective value = the stricter of delegation or repository policy and operational config |
| effect_intent_ttl_seconds | 300 |
| effect_hard_timeout_seconds | 240 |
| reservation_lease_seconds | 7200 |
| reservation_lease_renewable | True |
| staging_ref_retention_days | 14 |

## Identity tiers

| Tier | Question | Embeds | Fields |
|---|---|---|---|
| requirement_digest | Is this the same engineering work? | n/a | objective, finalization_class, source_domain.repository_id, source_domain.authority_location, source_domain.target_ref, specification_inputs.name_to_exact_byte_sha256, allowed_scope.paths, allowed_scope.artifact_paths, acceptance_assertions.ids_and_environment_constraints |
| contract_digest | Is this the same fulfillment policy for that work? | requirement_digest | retry_policy, repair_policy, reuse_policy, effect_policy, finalization_requirements, remote_verification_mode, capability_requirements, role_transforms, completion_policies, evidence_requirements, predecessors, resolved_bindings.repository_authority_declaration, resolved_bindings.verifier_registrations, resolved_bindings.transform_registrations, resolved_bindings.delegation_contract, resolved_bindings.mandatory_floor |
| record_hash | Is this the same admitted request event? | contract_digest | task_contract_id, revision, authored_by, frozen_utc, delegation_id, supersession_provenance, force_new_sequence |

## Bindings resolved at freeze

| ID | Object | Bound as | Carries |
|---|---|---|---|
| reg-repository | obj-02 | resolved_bindings.repository_authority_declaration | authority_location, canonical_ref, publication_mode |
| reg-verifier | obj-03 | resolved_bindings.verifier_registrations | registration_id, verifier_id, version, artifact_root, entrypoint, execution_requirements |
| reg-transform | obj-03 | resolved_bindings.transform_registrations | registration_id, transform_id, version, artifact_root, entrypoint, execution_requirements |
| reg-delegation | obj-00 | resolved_bindings.delegation_contract | delegation record hash, policy bounds (D-18) |
| reg-floor | obj-00 | resolved_bindings.mandatory_floor | mandatory assertion IDs per effect class and repository |

## Authority edges

| From | To | Authority | Topology |
|---|---|---|---|
| you | registry | freeze your contracts; delegation scope | interim |
| you | sequencer | permits, attestations, supersession, abandonment, waivers, budgets, revocations | interim |
| you | autobot_service | authenticated authority requests (AuthorityAuthenticationProvider) | target |
| autobot_service | registry | authenticated freeze requests | target |
| autobot_service | sequencer | authenticated authority records | target |
| registry | sequencer | frozen records become Sequences | both |
| sequencer | dispatcher | reserved, fenced attempts | both |
| dispatcher | autoswarm | in-process launch | interim |
| dispatcher | launcher | launch request over IPC | target |
| launcher | autoswarm | autoswarm run under your or the agent identity | target |
| autoswarm | task_force | sessions | both |
| helios | task_force | command admission | both |
| akashic | helios | integrity verdict | both |
| sequencer | git_sequencer | effect intents | both |
| git_sequencer | git | ref CAS and pushes | both |

## Forbidden edges

| From | To | Reason | Topology |
|---|---|---|---|
| task_force | sequencer | agents never transition state | both |
| task_force | registry | agents never author or freeze their own contracts | both |
| task_force | autobot_service | agents never make authority requests (CORR-11) | both |
| task_force | git_sequencer | agents never publish | both |
| planner | sequencer | the LLM proposes; it never decides | both |
| planner | registry | the Planner authors drafts; only the Registry freezes (CORR-15) | both |
| planner | git_sequencer | the LLM never publishes | both |
| autoswarm | sequencer | an outcome is evidence, not acceptance | both |
| helios | sequencer | Helios never decides task state (I-1) | both |
| sequencer | helios | Autobot never directs the gate | both |
| sequencer | akashic | a Reset is an external effect under your permit | both |
| verifier_runner | sequencer | verifiers return results; the Sequencer decides | both |
| collector | sequencer | evidence never grants authority | both |
| hdb | sequencer | an index is never authority (C5) | both |
| ci_system | sequencer | a remote check is a witness | both |
| dispatcher | autoswarm | in the target the service never launches agents as itself (CORR-12) | target |
| autobot_service | task_force | agents run under your or the agent identity, never the service's (CORR-12) | target |

## Invariants

| ID | Invariant | Status | Tests | Enforcement points |
|---|---|---|---|---|
| INV-01 | No execution without a frozen contract. | active | liveness.exits_and_reachability (registry), CH2-reserve-requires-frozen-record (chapter 2) | t-s06: cas_state_version_record_hash |
| INV-02 | No execution without a frozen source or subject state in the AttemptContract. | active | CH2-attempt-contract-captures-source-state (chapter 2) | none |
| INV-03 | Every artifact belongs to exactly one attempt, by causal provenance. | active | CH3-artifact-provenance-single-attempt (chapter 3) | none |
| INV-04 | Evidence cannot satisfy another task merely because it is newer. | active | CH3-mtime-never-selects-artifact (chapter 3) | none |
| INV-05 | A frozen contract's digests never change; acceptance criteria cannot change after freeze. | revised | CH1-golden-digest-stability (chapter 1) | none |
| INV-06 | An LLM may propose but cannot authorize PASS. | active | objects.minter_kinds (registry), CH4-llm-output-never-pass (chapter 4) | none |
| INV-07 | PASS requires the contract's mandatory deterministic assertions. | active | CH4-mandatory-assertions-required (chapter 4) | t-s10: required_assertions_pass_or_attested |
| INV-08 | For DIRECT_REF_CAS the committed tree equals the verified tree; MEDIATED is proven later by the mediated adapter. | revised | CH6-commit-tree-equals-verified-tree (chapter 6) | none |
| INV-09 | Only an AcceptanceReceipt releases a successor. | active | graph.published_unverified_releases_nothing (registry), CH5-release-requires-acceptance (chapter 5) | none |
| INV-10 | Crash, restart or replay reproduces the same next state. | active | CH2-failpoint-matrix (chapter 2) | none |
| INV-11 | A TaskContractRecord never changes inside its Sequence; two Sequences with the same RequirementDigest are independent. | revised | CH2-record-immutable-in-sequence (chapter 2) | none |
| INV-12 | One current Attempt per Sequence. | active | CH2-single-current-attempt (chapter 2) | none |
| INV-13 | Authoritative receipts carry sequence, attempt, version and fence; a stale fence yields no authority. | active | fence.attempt_bound_receipts (registry), CH2-stale-fence-rejected (chapter 2) | none |
| INV-14 | The Git sequencer acts only on a matching state, version, current attempt and fence. | active | fence.attempt_bound_receipts (registry), CH6-late-commit-refused (chapter 6) | none |
| INV-15 | Refs move by compare-and-swap; never force, never merge. | active | CH6-no-force-no-merge (chapter 6) | none |
| INV-16 | A source-modifying Sequence is ACCEPTED only after every finalization requirement has its receipt. | revised | graph.no_uncommitted_source_acceptance (registry) | none |
| INV-17 | Verification binds candidate, environment, assertions and verifiers. | active | CH4-verification-binding (chapter 4) | none |
| INV-18 | Evidence is attempt-bound; reuse of evidence across attempts needs an explicit rule. | active | CH4-evidence-not-reused (chapter 4) | none |
| INV-19 | A partial PASS releases nothing. | active | CH5-partial-pass-blocks (chapter 5) | none |
| INV-20 | DEPENDENCIES_RELEASED is durable; restart resumes release. | active | CH5-release-resumes-after-crash (chapter 5) | none |
| INV-21 | specification_inputs is an explicit, closed set hashed as exact bytes; normalization is a separate registered transform. | revised | identity.tiers (registry), CH1-exact-byte-input-hash (chapter 1) | t-c02: inputs_resolved_exact_bytes |
| INV-22 | Provenance comes from WitnessRecords, never from mtime or agent claims. | active | CH3-provenance-from-witnesses (chapter 3) | none |
| INV-23 | Integrity failures in the ExecutionReceipt block acceptance (refined by INV-68). | revised | CH3-integrity-failure-blocks (chapter 3) | t-s45: execution_receipt_integrity_failed_or_incomplete_beyond_bound |
| INV-24 | Publication needs a valid permit use: per-sequence or standing, reserved before the effect. | active | effects.require_intent (registry) | none |
| INV-25a | Interim: one InstanceLease holder at a time. | active | CH2-instance-lease (chapter 2) | none |
| INV-25b | Target: an OS lock and the service identity's ACL as well. | active | CH8-service-acl (chapter 8) | none |
| INV-26 | Authority records name a principal; in the target the IPC peer plus the human-presence factor authenticate it. | revised | authority.acyclic_and_forbidden (registry), CH8-presence-factor (chapter 8) | none |
| INV-27 | Anything written to a repository carries IDs and hashes only, never machine paths. | active | CH6-trailers-path-free (chapter 6) | none |
| INV-28 | No contract freezes outside the active DelegationContract's scope. | active | invariants.enforcement_points (registry), CH1-scope-check (chapter 1) | t-c02: scope_and_floor_within_active_delegation_or_authored_by_you |
| INV-29 | Each identity tier is deterministic from its own inputs and from no higher tier's inputs. | revised | identity.tiers (registry) | none |
| INV-30 | Evidence completeness depends only on raw witnesses; INDEX_LAG never blocks unless an assertion declares an HDB-derived query. | active | CH3-index-lag-diagnostic (chapter 3) | none |
| INV-31 | A READ_ONLY AcceptanceReceipt is scoped to its observation window. | active | CH5-observation-window (chapter 5) | none |
| INV-32 | A HUMAN_REQUIRED contract cannot reach ACCEPTED without an attestation for each human-attested assertion. | active | CH5-human-required-attestation (chapter 5) | t-s10: required_assertions_pass_or_attested |
| INV-33 | Delegation re-check at reservation and publication. | replaced by INV-41 | none | none |
| INV-34 | A delegated freeze includes the delegation's mandatory assertion floor. | active | invariants.enforcement_points (registry) | t-c02: scope_and_floor_within_active_delegation_or_authored_by_you |
| INV-35 | Agents and the Planner's LLM never run under the service identity. | active | authority.acyclic_and_forbidden (registry), CH8-launch-identity (chapter 8) | none |
| INV-36 | A Sequence moves its declared canonical ref exactly once, by CAS; that move is the point of no return. | revised | ponr.per_path (registry), ponr.no_backward (registry) | none |
| INV-37 | Specification inputs are re-hashed at dispatch and at collection; a change is INTEGRITY_FAILED. | active | CH3-input-rehash (chapter 3) | none |
| INV-38 | A source-modifying HUMAN_REQUIRED contract needs your attestations before COMMITTING. | active | invariants.enforcement_points (registry) | t-s10: attestation_before_commit_if_source_human_required |
| INV-39 | For a REMOTE repository the PONR is the remote canonical CAS; local branches are projections and workspace sync is never needed for acceptance. | active | ponr.per_path (registry) | none |
| INV-40 | One Sequence per TaskContractRecord, except a duplicate admission returns the existing record. | revised | liveness.exits_and_reachability (registry), CH1-duplicate-admission (chapter 1) | none |
| INV-41 | Every authority consumption before the PONR needs an active delegation; at the PONR its state is frozen into the receipt; after the PONR recovery finishes forward. | active | graph.revocation_covers_pre_ponr (registry), effects.require_intent (registry), CH6-revocation-timeline (chapter 6) | none |
| INV-42 | Permit uses are reservation rows; consume and release each change a row from RESERVED exactly once. | revised | CH2-permit-race-two-reservers-one-use (chapter 2) | none |
| INV-43 | Every externally visible push has its own receipt; staging pushes never move a canonical ref. | active | effects.definitions (registry) | none |
| INV-44 | Specification identity is the exact bytes; prompt identity is the declared transform's output; A1 proves the chain. | active | CH4-a1-transform-chain (chapter 4) | none |
| INV-45 | authority_location is declared once per repository; a contract that disagrees, or whose finalization doesn't fit it, cannot freeze. | active | invariants.enforcement_points (registry) | t-c02: repository_declaration_bound_and_matching |
| INV-46 | Workspace sync never moves a checked-out branch out from under its worktree; otherwise it records SKIPPED. | active | CH6-workspace-sync-safety (chapter 6) | none |
| INV-47 | An effect's checks and intent commit in one ledger transaction just before the effect. | active | effects.require_intent (registry) | none |
| INV-48 | consumed_uses + reserved_uses <= permitted_uses at all times; only the Sequencer changes permit state. | revised | objects.minters (registry), CH2-permit-counters (chapter 2) | none |
| INV-49 | Child prompts are checked after the fact against their declared role transform; the Dispatcher projects only what it renders. | active | CH4-role-transform-check (chapter 4) | none |
| INV-50 | Reuse needs the same RequirementDigest and a dominating proof. | revised | invariants.enforcement_points (registry) | t-s43: dominating_finalization_proof, t-s44: dominating_finalization_proof |
| INV-51 | PUBLISHED_UNVERIFIED releases nothing. | active | graph.published_unverified_releases_nothing (registry) | none |
| INV-52 | Only registered verifier and transform artifacts, at their bound hashes, produce results; registration is yours. | active | registrations.bindings (registry) | none |
| INV-53 | Every authority-affecting lookup is resolved at freeze to an immutable registration record; an unavailable bound artifact HALTs and is never substituted. | active | registrations.bindings (registry) | t-s12: unknown_or_env_mismatch_or_budget_exhausted_or_bound_artifact_unavailable |
| INV-54 | A payload matching the ContractDigest of a record with a non-terminal Sequence creates no record and no Sequence, unless it carries force_new_sequence. | active | invariants.enforcement_points (registry) | t-c03: same_contract_digest_live_sequence_no_force_new |
| INV-55 | Every ref or push effect runs under an EffectIntent with a bounded window; an expired intent authorizes nothing new. | active | effects.require_intent (registry) | none |
| INV-56 | v1 publishes only DIRECT_REF_CAS repositories. | active | invariants.enforcement_points (registry) | t-s31: direct_ref_cas_mode, t-s32: direct_ref_cas_mode |
| INV-57 | PUBLISHED_UNVERIFIED always mints a ForwardRepairObligation; a waiver closes it but satisfies no edge. | active | graph.published_unverified_releases_nothing (registry) | none |
| INV-58 | Bound artifact closures stay in the content store while any non-terminal record binds them. | active | CH4-artifact-retention (chapter 4) | none |
| INV-59 | A late consume against a released reservation is an integrity alarm, never a silent overbook. | revised | CH6-late-consume-alarm (chapter 6) | none |
| INV-60 | Every reservation has its own renewable lease, independent of any intent. | active | CH2-reservation-lease (chapter 2) | none |
| INV-61 | Duplicate admissions are recorded by the Registry as DuplicateAdmissionRecords. | active | transitions.minted_by_participant (registry) | none |
| INV-62 | Remote ref outcomes: target at the new value is HAPPENED; at the expected old value is UNKNOWN_OUTCOME; anything else is ABSENT. Grace periods are operational only. | active | effects.outcome_rules (registry), CH6-remote-outcome-rule (chapter 6) | none |
| INV-63 | Lease expiry releases authority, keeps every receipt and HALTs; resumption re-validates and re-reserves. | active | invariants.enforcement_points (registry) | t-s28: receipts_retained, t-s29: fresh_reservation_acquired |
| INV-64 | A contract whose finalization its bound repository mode cannot satisfy in v1 never becomes READY. | active | invariants.enforcement_points (registry) | t-s02: finalizer_supported_in_v1, t-s03: finalizer_supported_in_v1 |
| INV-65 | Source reuse requires the accepted commit to be the canonical head (EXACT_HEAD); ANCESTOR_OK is not available in v1. | active | invariants.enforcement_points (registry) | t-s43: exact_canonical_head_if_source, t-s44: exact_canonical_head_if_source |
| INV-66 | A frozen contract executes the exact registered artifact closure, never whatever a filesystem path currently holds. | active | registrations.bindings (registry) | none |
| INV-67 | effect_hard_timeout < effect_intent_ttl; reservation leases are renewable and bounded by max_wait; effective bounds are the stricter of policy and config. | active | policy.bounds (registry) | t-s31: intent_ttl_gt_hard_timeout |
| INV-68 | An orphan, a DENY-then-ran event, or execution with no recorded decision makes the ExecutionReceipt INTEGRITY_FAILED; DENY counts are diagnostic. | active | CH3-integrity-classes (chapter 3) | none |
| INV-69 | A missing required join makes the ExecutionReceipt INCOMPLETE. | active | invariants.enforcement_points (registry) | t-a06: required_joins_present |
| INV-70 | An UNKNOWN_OUTCOME intent resolves only by observation, by an identical CAS retried under a fresh intent with active authority, or by your decision. | active | liveness.exits_and_reachability (registry) | none |
| INV-71 | No state lies on both sides of a PONR on any path, and no resume can skip verification. | active | ponr.no_backward (registry), graph.no_unverified_acceptance (registry) | none |
| INV-72 | Receipts and authoritative state live only in the Autobot ledger and its content-addressed store; HDB is a derived index that never gates completeness, acceptance or state unless a TaskContract declares an HDB-derived assertion; Git holds source proof and path-free references only. | active | objects.storage (registry), CH2-receipt-storage (chapter 2) | none |

## Corrections

| ID | Correction | Origin | Status | Replaced or refined by | Refinement |
|---|---|---|---|---|---|
| CORR-01 | DelegationContract above TaskContract | user | APPLIED | none | n/a |
| CORR-02 | Authority service is the target architecture; interim in-process over the same IPC | user | APPLIED | none | n/a |
| CORR-03 | WitnessRecords minted by producers; Collector alone mints the ExecutionReceipt | user | APPLIED | none | n/a |
| CORR-04 | Semantic digest vs record hash | user | APPLIED | CORR-41 | n/a |
| CORR-05 | HDB is an index, never a prerequisite | user | APPLIED | none | n/a |
| CORR-06 | Explicit closed specification_inputs | user | APPLIED | CORR-30 | n/a |
| CORR-07 | CommitReceipt / PublicationReceipt split; finalization_requirements | user | APPLIED | none | n/a |
| CORR-08 | Per-role completion policy | user | APPLIED | none | n/a |
| CORR-09 | ObservationWindow | user | APPLIED | none | n/a |
| CORR-10 | AUTOMATIC vs HUMAN_REQUIRED eligibility | user | APPLIED | none | n/a |
| CORR-11 | OS identity does not separate you from your agents; human-presence factor | assistant | ACCEPTED | none | n/a |
| CORR-12 | Launcher runs agents under your identity, never the service's | assistant | ACCEPTED | none | n/a |
| CORR-13 | Canonical ref moves once, by CAS, at the PONR | assistant | ACCEPTED | CORR-25, CORR-29 | n/a |
| CORR-14 | Delegation's mandatory assertion floor | assistant | ACCEPTED | none | n/a |
| CORR-15 | Planner authors descendant contracts; only the Registry freezes | assistant | ACCEPTED | none | n/a |
| CORR-16 | Delegation re-check at reservation and publication | assistant | REPLACED | CORR-27 | n/a |
| CORR-17 | Stricter of config and delegation budget wins | assistant | ACCEPTED | none | n/a |
| CORR-18 | One Sequence per digest | assistant | REJECTED | CORR-26 | n/a |
| CORR-19 | Re-hash inputs at collection; normalization rules | assistant | ACCEPTED | CORR-30 | n/a |
| CORR-20 | Completion policy derived from roles; orchestrator cancels, Recovery backstops | assistant | ACCEPTED | none | n/a |
| CORR-21 | observation_requirement on DependencyEdges | assistant | ACCEPTED | none | n/a |
| CORR-22 | Separate RemoteVerificationReceipt minted by the Verifier runner | assistant | ACCEPTED | none | n/a |
| CORR-23 | Backlog and configs are authoring surfaces in the target | assistant | ACCEPTED | none | n/a |
| CORR-24 | Attestation before COMMITTING for source-modifying HUMAN_REQUIRED work | assistant | ACCEPTED | none | n/a |
| CORR-25 | Canonical source authority location | user | APPLIED | none | n/a |
| CORR-26 | Semantic equivalence is not fulfillment; one Sequence per TaskContractRecord | user | APPLIED | none | n/a |
| CORR-27 | Delegation revocation and the PONR | user | APPLIED | none | n/a |
| CORR-28 | Standing permits consumed atomically | user | APPLIED | CORR-40 | n/a |
| CORR-29 | Staging publication receipt; PRE and POST verification modes | user | APPLIED | none | n/a |
| CORR-30 | Specification identity vs prompt identity | user | APPLIED | none | n/a |
| CORR-31 | authority_location declared per repository | assistant | ACCEPTED | none | n/a |
| CORR-32 | WorkspaceSync never moves a checked-out branch unsafely | assistant | ACCEPTED | none | n/a |
| CORR-33 | Linearization point for external effects | assistant | ACCEPTED | CORR-44 | n/a |
| CORR-34 | reserved_uses counter; the Sequencer changes permit state | assistant | ACCEPTED | CORR-40 | n/a |
| CORR-35 | Dispatch projection vs role transforms | assistant | ACCEPTED | none | n/a |
| CORR-36 | Reuse policy and liveness | assistant | ACCEPTED | CORR-41, CORR-47 | n/a |
| CORR-37 | Staging refs that trigger CI; retention | assistant | ACCEPTED | CORR-45 | n/a |
| CORR-38 | PUBLISHED_UNVERIFIED end state | assistant | ACCEPTED | CORR-46 | n/a |
| CORR-39 | Verifier and transform registrations are yours | assistant | ACCEPTED | CORR-42, CORR-48 | n/a |
| CORR-40 | Permit reservations have identity | user | APPLIED | none | n/a |
| CORR-41 | Three-tier identity | user | APPLIED | none | n/a |
| CORR-42 | Authority dependencies frozen by record identity | user | APPLIED | none | n/a |
| CORR-43 | Duplicate admission | user | APPLIED | none | n/a |
| CORR-44 | Bounded effect intent | user | APPLIED | none | n/a |
| CORR-45 | Repository publication mode | user | APPLIED | none | n/a |
| CORR-46 | ForwardRepairObligation | user | APPLIED | none | n/a |
| CORR-47 | finalization_class in the RequirementDigest; dominating proof; EXACT_HEAD only for source reuse (R7) | assistant | ACCEPTED | none | ANCESTOR_OK deferred |
| CORR-48 | Bound programs kept in the content store | assistant | ACCEPTED | none | the registered execution artifact closure (artifact_root, entrypoint, execution_requirements), not one file |
| CORR-49 | Release after expiry only when absence is proven | assistant | ACCEPTED | none | the expected old value is UNKNOWN_OUTCOME; only a stale expected value or a transport cancellation proof is ABSENT; grace periods are operational only |
| CORR-50 | Reservation lease separate from intents | assistant | ACCEPTED | none | lease expiry releases authority and HALTs; receipts stay; resumption re-validates and re-reserves |
| CORR-51 | DuplicateAdmissionRecord minted by the Registry; terminal-state list | assistant | ACCEPTED | none | n/a |
| CORR-52 | MEDIATED repositories in v1 | assistant | ACCEPTED | none | the record freezes but its Sequence never becomes READY (BLOCKED_UNSUPPORTED_FINALIZER) |
| CORR-53 | Forward repair bounds | assistant | ACCEPTED | none | quarantine the canonical ref while OPEN; a waiver lifts it and still satisfies no edge |
| CORR-54 | D-04 confirmed: the Autobot ledger plus the content-addressed store hold receipts; HDB is an index only; Git holds source proof and path-free references | user | APPLIED | none | folded in before the acceptance commit; checked as objects.storage (INV-72) |

## Decisions

| ID | Question | Status | Disposition |
|---|---|---|---|
| D-01 | How Autobot holds authority | DECIDED | Target: a separate authority service. Interim: in-process over the same IPC protocol. |
| D-02 | The human-authority boundary | DECIDED | One decision with D-01: separate OS/service identity, ACL-protected ledger and content store, narrow IPC. |
| D-02b | How the service tells you apart from your agents | DECIDED | A human-presence factor first, behind an abstract AuthorityAuthenticationProvider. |
| D-03 | Publication permits | DECIDED | Standing scoped delegation permitted (standing_publication_permits). |
| D-04 | Where receipts live | DECIDED | Authoritative receipt storage is the Autobot ledger plus its content-addressed store. The ledger holds authoritative metadata, identities, hashes, bindings, state transitions and receipt references; the content store holds the immutable receipt payloads and the retained evidence and artifact bytes, addressed by hash. HDB is a derived, queryable evidence index, non-authoritative by default: its freshness or availability never gates receipt completeness, acceptance or authoritative state unless a TaskContract explicitly declares an HDB-derived assertion. Git holds source-state proof and path-free proof references (trailers); it is not the primary receipt store. |
| D-05 | Helios changes | DECIDED | No required Helios architecture change. U-01 stays a blocker for AUTOMATIC contracts that require Helios integrity. |
| D-06 | Supersession while an attempt is in flight | DECIDED | Fence immediately when before the PONR. |
| D-07 | Legacy proposals v13, v13-rc, v14 | DECIDED | Import as legacy; re-verify only when a current dependency or an explicit historical-close request needs it. |
| D-08 | Live-system investigations | DECIDED | Yes, qualified by ObservationWindow / SubjectState semantics. |
| D-09 | Canonical serialization | DECIDED | RFC 8785 JCS and full SHA-256, with three identity tiers. |
| D-10 | AutoSwarm joins | DECIDED | Joins only, no fork change; a missing required join is INCOMPLETE. |
| D-11 | Who freezes Planner-drafted contracts | DECIDED | Your freeze during bootstrap; a DelegationContract for autonomous operation. |
| D-12 | Gate verdicts in acceptance | DECIDED | DENY counts are diagnostic. An orphan, a DENY-then-ran event, or execution with no recorded decision is INTEGRITY_FAILED. |
| D-13 | Two contracts with the same digest | DECIDED | One Sequence per TaskContractRecord; equivalence is recorded, reuse is explicit. |
| D-14 | When remote verification runs | DECIDED | PRE_PUBLICATION_REMOTE_VERIFICATION by default; POST_PUBLICATION_OBSERVATION when a contract declares it. |
| D-16 | Staging-ref retention | DECIDED | Delete after ACCEPTED; otherwise bounded retention such as 14 days; deletion is a receipted external effect. |
| D-17 | Quarantine during an open repair obligation | DECIDED | Yes: quarantine the affected canonical ref; a waiver lifts it. |
| D-18 | Window and lease lengths | DECIDED | Configurable bounded defaults, not architecture constants: hard timeout below intent TTL; leases renewable up to max wait; the stricter of policy and config applies. |

## Unknowns

| ID | Unknown | Status | Next probe | Blocks |
|---|---|---|---|---|
| U-01 | Why commands with a recorded DENY still ran (07:19Z and 21:56Z on 2026-11-07) | OPEN | Trace the hook output and timing for those tool_use_ids | AUTOMATIC acceptance of contracts that require Helios integrity (D-05); Chapters 3 and 5 |
| U-02 | Whether the Codex DevTools API can be relied on for write provenance | OPEN | Check server mode; fall back to codex-export.ts | Chapter 3 provenance for Codex workers |
| U-03 | Running git in folders owned by BUILTIN/Administrators without changing your global safe.directory | OPEN | Your setting, or the CODEAPI elevated route | Chapter 6 on swarm-coordinator |
| U-04 | Whether Helios ledgers for Codex sessions are as complete as Claude's | NARROWED | Compare one Luna session's ledger with its rollout | Chapter 3 |
| U-05 | The catalog watermark interface | OPEN | hdb status | Nothing authoritative; INDEX_LAG is diagnostic (Chapter 3) |
| U-06 | AutoSwarm's outcome vocabulary, including cancelled calls | OPEN | Read AutoSwarm's code | Chapter 3 completion policies |
| U-07 | Whether controller_id is always set for --no-parent children | NARROWED | Check the v13 and v14 children | Chapter 3 joins |
| U-08 | Whether the monitor's and poller's results are required | RESOLVED | Completion policy per role (C8) | n/a |
| U-09 | What the proof-policy names mean | RESOLVED | finalization_requirements (C7) | n/a |
| U-10 | Environment capture for Python verifiers | OPEN | Define with U-18 | Chapters 1 and 4 |
| U-11 | Windows 10 Home mechanics for a service identity, ACL-protected ledger and named-pipe IPC with peer identity | OPEN | Prototype the pipe and ACL | Chapter 8 (Chapter 2 defines the interface only) |
| U-12 | Calibrating the role transforms: PowerShell 5.1 encoding and appended newline, AutoSwarm --input reading, BOM and CRLF | OPEN | Recompute v14 and v15 children's task.json prompts from their input files | Chapter 4 registration of A1's transforms |
| U-13 | Whether CI exists for REMOTE_VERIFICATION on the target repositories | OPEN | List each repository's workflows | Chapter 6 PRE_PUBLICATION contracts |
| U-14 | The Git sequencer's identity and push credential in the target topology | OPEN | Decide with U-11 | Chapter 8 |
| U-15 | Which Windows human-presence check backs the AuthorityAuthenticationProvider | OPEN | Evaluate Windows Hello consent for the CLI | Chapter 8 |
| U-16 | Whether CI triggers on the staging branch, and whether branch protection forces pull requests | OPEN | Push a disposable staging branch on a test repository | Chapter 6 |
| U-17 | How long a server can still apply a push after its client died | NARROWED | Operational polling interval only | Nothing authoritative: CORR-49 as refined no longer uses a delay as proof (Chapter 6 operations) |
| U-18 | What the execution closure is for verifiers that depend on the claude, codex and Python toolchains | OPEN | Inventory each verifier's dependencies | Chapters 1 and 4 registration schema details |

