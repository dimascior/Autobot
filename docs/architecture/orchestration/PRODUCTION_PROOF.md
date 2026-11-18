# Production proof

What has run in production, with the identities that let anyone check it,
and what has only run in the test suite. Nothing here is a claim about
code that was merely read.

## Authority baseline

These two proofs predate the orchestration work and are its substrate.

| Proof | Date | Evidence |
|---|---|---|
| READ_ONLY authority plane, end to end | 2026-11-16 | PR 17 (`b19716a`), "Restore manifest-bound READ_ONLY production execution"; the preserved bundle `pilot-evidence-20261116/` |
| SOURCE_MODIFYING authority plane, end to end | 2026-11-17 | PR 18 (`0eca39c`) and PR 19; the specialist-created candidate commit `05e3d1ad91e247cfb767e3c25926f74d3fad9a35` on `main`, parent `e3ad4d0ff4066db8dc7132274cd702d2b4034ea7`; record [../../implementation/source_modifying_pilot.md](../../implementation/source_modifying_pilot.md); AutoSwarm call `call_3e068c43ebf4a527bbbf4e6df12511b8` |

The SOURCE_MODIFYING pilot demonstrated: frozen task scope, isolated
worktree, a real specialist, a specialist-created candidate commit, a
structured effect result, candidate derivation, scope validation,
verification, a publication permit, an EffectIntent, a local ref
compare-and-swap, a CommitReceipt, COMPLETED.

Two things about those runs are recorded as findings in
[ARCHITECTURE.md](ARCHITECTURE.md) (F-TF-3, F-TF-4): the driver exited
while the specialist was still executing and the command was run again to
continue, and the specialist's permission requests were approved by a
script outside the runtime. Both are closed in the code this document
ships with.

## The Engineer, projects and the Taskforce

The production pilots began on 2026-11-18 from `main` at `3b03b6a` and
are in progress as of this commit; this section is replaced by their
records when they complete. The first of them found F-TF-15 to F-TF-17
([ARCHITECTURE.md](ARCHITECTURE.md)), which this commit fixes.

What exists is the deterministic proof: the suites in
[README.md](README.md#tests) drive the real Registry, Sequencer,
Collector and verifier seam with rule policies and a scripted provider,
and the architecture-conformance suite pins the organizational model one
sentence per test. They establish the logic. They do not establish that a
real specialist, a real reasoning model and the real AutoSwarm behave as
scripted, which is what the production pilots are for.

Production's plan store was migrated to schema v2 on 2026-11-18 with
nobody in the organization. Its first member was earned that day the way
every later one is — verified work by an outside specialist, then an
explicit retention decision; the record follows with the pilots'.
