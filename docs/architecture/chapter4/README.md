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

Remaining chapter-4 registered proofs (later milestones):
`CH4-a1-transform-chain` (INV-44), `CH4-role-transform-check` (INV-49),
`CH4-artifact-retention` (INV-58 — recovery already marks closure leaves
referenced; the retention WINDOW proof remains).

Documents: `VERIFICATION.md` (the architecture), `registries/decisions.json`
(D-C4-1..7), `schemas/` (the obj-16/19/20 mirrors, byte-identical to
`src/autobot/schemas_chapter4/`).

Boundaries kept out of this chapter's M1, per the frozen roadmap: obj-14
remote checks and remote verification/publication (Chapter 6), obj-30 and
obj-35 full schemas (Chapter 5), ACCEPTANCE_FRESH (existing deferred arm),
HDB as authority (forbidden), planner/Taskforce authority over verifier
results (forbidden), CORR-57 governance and Helios implementation cleanup
(separate tracks).
