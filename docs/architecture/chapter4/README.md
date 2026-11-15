# Chapter 4 — Production Verification Authority

Chapter 3 made execution evidence authoritative: what ran, under which
decisions, with which witnesses and artifacts, classified by the collector
and admitted against byte-exact re-derivation. Chapter 4 makes the
VERIFICATION of the candidate that execution produced authoritative:

    Attempt
    → CandidateReceipt (obj-16, minted by the git_adapter)
    → ActualVerificationEnvironment (obj-18, minted by the environment_probe)
    → exact registered verifier closure (D-57/INV-53/INV-66)
    → assertion evaluation (cap-34, the registered verifier concludes)
    → VerificationReceipt / VerificationFailureReceipt (obj-19/obj-20,
      minted by the verifier_runner)
    → Sequencer (t-a07/t-a08 with t-s10/t-s11/t-s12)
    → VERIFIED / repair / HALT

The intelligence plane, the Taskforce, AutoSwarm, transcript contents and
verifier prose can never manufacture PASS: cap-32 and cap-35 forbid the
task_force, the admission seams validate every binding against durable
ledger facts, and a conclusion exists only as the typed result protocol of
an executed registered closure, supported by its own durable output
evidence (F-C4-1).

## Milestone 1 (C4-M1) — this tree

Everything the M4 framework left as typed seams is production-capable:

- **obj-16 CandidateReceipt** — real minting by the git_adapter from
  ledger facts plus cap-42's tree capture; full admission at the freeze
  (t-a05/t-s07 require it); one immutable candidate per attempt;
  READ_ONLY work gains no invented tree.
- **obj-18 ActualVerificationEnvironment** — the real environment_probe
  under the frozen Chapter 1 schema and the complete D-56 constraint
  vocabulary; captured once, durable before any launch, never recaptured.
- **Registered closure execution** — SEM-15 recompute, leaf existence,
  interpreter identity; materialized from the content store, never a
  workspace path; packaged closures HALT in v1.
- **Production VerifierRunner** — the eleven-step registered flow,
  translating execution observations into the frozen result vocabulary;
  the Sequencer still executes nothing.
- **Canonical obj-19/obj-20** — schema-validated, fully bound
  (candidate, environment, D-47 assertion part, registration, Attempt),
  outcome consistent with the durable execution evidence.
- **t-s10/t-s11/t-s12 over production receipts**, recovery (8h) over the
  new proof objects, and the crash-matrix resume points.

The registered chapter-4 proofs closed in M1: `CH4-llm-output-never-pass`
(INV-06), `CH4-mandatory-assertions-required` (INV-07),
`CH4-verification-binding` (INV-17), `CH4-evidence-not-reused` (INV-18).

## Milestone 2 (C4-M2) — this tree

- **U-12 RESOLVED** — the A1 transform calibration from executing
  provider source plus recomputed retained evidence; the calibrated
  family registered and executed (`identity-projection`,
  `autoswarm-input-v1`), re-executed at every check admission.
- **`CH4-a1-transform-chain` (INV-44) closed** — exact input bytes →
  registered transform → expected prompt identity; BOM/CRLF/UTF-16/
  append/strip vectors all change an identity or fail closed.
- **`CH4-role-transform-check` (INV-49) closed** — the dispatched child's
  RECEIVED prompt identity from admitted obj-11 producer bytes only;
  mismatch is an authoritative FAIL on the repair path; missing producer
  evidence blocks conclusion; replays, stale registrations, forged
  expected values and dispatcher (obj-10 `transform_provenance`) drift
  die at the seam; conclusion REQUIRES the admitted check.
- **BY_ATTESTATION (D-C4-11)** — `attest_assertion` reshaped into the
  obj-37 admission (full D-53 bindings incl. candidate + D-47 part;
  `attempt_attestations`, schema v10); the runner incorporates admitted
  attestations into canonical obj-19 BY_ATTESTATION results (no
  registration, no execution, no environment); Chapter-5 acceptance
  semantics untouched.
- **Packaged closures (D-C4-10)** — the manifest form executes its exact
  content-addressed entries under one shared resolver/materializer
  (`autobot.transforms`); the image form HALTs explicitly; python/
  packaged execution parity proven.
- **`CH4-artifact-retention` (INV-58) closed** — `retention_roots` over
  non-terminal sequences, explicit re-derived `reclaim` (ledger columns
  and frozen records' specification inputs are never reclaimable),
  replacement frees nothing until every binder is terminal, recovery
  REPORTS missing bound leaves (D-C4-9).
- **F-C4-2 fixed (D-C4-12)** — the env-var constraint section is the
  frozen `env` spelling; `env_vars` as a constraint section fails closed.

## Test independence (TI-1) — this tree

The runtime suite (chapters 2–4) is held to chapter 1's D-58 standard:
second implementations that import nothing from production
(`preimage2`, `auditor2`, `transform2`), a local-only arm over the
operator's retained provider records, and a mutation matrix that ties
every refusal assertion in the suite to an invariant, a planted defect
and a control. The oracles exposed three production defects, fixed here
(F-TI-1 record identity at the object-admission seam, F-TI-4 reclaim
versus recovery, F-TI-6 closure paths on Windows), and one that is
recorded and left OPEN for the operator (F-TI-5: the obj-12 recognizer
reads no fact from the governance producer's real records). No authority
semantics and no schema changed. See `INDEPENDENCE.md` and D-C4-13.

Documents: `VERIFICATION.md` (the architecture, sections 13–19 for M2),
`INDEPENDENCE.md` (the TI-1 harness, findings and limits),
`registries/decisions.json` (D-C4-1..13), `schemas/` (the obj-16/19/20
mirrors, byte-identical to `src/autobot/schemas_chapter4/`).

Boundaries kept out of this chapter, per the frozen roadmap: obj-14
remote checks and remote verification/publication (Chapter 6), obj-30 and
obj-35 full schemas and acceptance semantics (Chapter 5),
ACCEPTANCE_FRESH (existing deferred arm), HDB as authority (forbidden),
planner/Taskforce authority over verifier results (forbidden), a general
repository garbage collector (reclaim judges only named candidates),
CORR-57 governance and Helios implementation cleanup (separate tracks).
