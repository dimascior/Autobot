# Chapter 3 — evidence and receipt authority

Chapter 2 ended with the complete authority runtime and the Chief
Engineer / Taskforce intelligence plane on `main`, with one deliberate
stand-in left inside the evidence boundary: ExecutionReceipt completeness
was integration-supplied and the receipt-consuming transitions failed
closed. Chapter 3 makes evidence completeness and ExecutionReceipt
classification authoritative.

Frozen sources this chapter consumes: obj-15 (minter `collector`,
append-only, attempt_bound; CORR-03), cap-30 ("ExecutionReceipt —
admission, completeness per role, index state"; task_force forbidden),
the t-a06/t-s09/t-a13/t-s45 rows and their registered preconditions,
INV-23 with its registered chapter-3 test id, the contract schema's
`evidence_requirements` enum, the registered witness kinds obj-11..obj-14,
and D-18/D-27/D-28 for windows and instants. Chapter-3 decisions live in
[registries/decisions.json](registries/decisions.json) (D-C3-1 bound
derivation, D-C3-2 derived-not-declared admission, D-C3-3 the
INTEGRITY_FAILED/D-66 interplay); the obj-15 payload schema lives in
[schemas/](schemas/) and is mirrored byte-identically into
`src/autobot/schemas_chapter3/`.

## Milestone 1 — the authoritative ExecutionReceipt (C3-M1)

[RECEIPTS.md](RECEIPTS.md): the registered `collector` seat
(`src/autobot/collector.py` + `src/autobot/receipts.py`), the
`admit_execution_receipt` seam with byte-identical re-derivation, schema
v7 `attempt_receipts`, the live t-a13/t-s45 arms and the completed
classification half of t-s09, the Taskforce's frozen-evidence pass
replacing the F-CE-4 stand-in, recovery section 8f, findings F-C3-1..5,
and the proof of record (179/179 on CPython 3.12.11 and 3.8.10,
coordinator 41/41).

## Milestone 2 — evidence provenance and causal joins (C3-M2)

[PROVENANCE.md](PROVENANCE.md): the raw-witness trust model
(`src/autobot/witness_semantics.py` — exact producer bytes, tolerant
recognized facts, the admission identity gate), the causal chain joins
(call/session/controller, U-07 resolved), the frozen AutoSwarm outcome
mapping (U-06 resolved, D-C3-4), the artifact-provenance index (schema
v8 `attempt_artifacts`, `admit_artifact`, INV-03/04/22's registered
proofs), obj-10's Chapter 3 closure (`prompt_projection.schema.json`,
minter `dispatcher`), the extended obj-15 evidence graph (causal joins,
contradictions with `deny_then_ran` — U-01's detection rule — member
completeness per the frozen policies, artifact provenance), recovery 8g,
findings F-C3-6..8, and the proof of record (193/193 on CPython 3.12.11
and 3.8.10, coordinator 41/41). The chapter-0 unknowns register was
updated factually: U-06 and U-07 RESOLVED, U-01/U-02/U-04 NARROWED,
U-05 untouched (diagnostic, deferred).

## Milestone 3 — Chapter 3 closure (C3-M3)

[CLOSURE.md](CLOSURE.md): the completion matrix against every frozen
chapter-3 obligation. The milestone's three functional closures are the
registered proofs that were still unclaimed after M2 — INV-37
`CH3-input-rehash` (the t-a02 dispatch gate, the collection probe and
the obj-15 `input_rehash` always-binding, D-C3-9), INV-68
`CH3-integrity-classes` (the `execution_without_decision` orphan rule,
D-C3-8, with the F-C3-9 one-record deny_then_ran fix), and INV-30
`CH3-index-lag-diagnostic` (cap-30's index-state facet as the
Collector's injected catalog diagnostic, D-C3-7, with U-05 resolved
from the real catalog implementation) — plus the unknowns register
dispositions, the chapter-wide adversarial review and the stand-in
scan. Proof of record: 206/206 on CPython 3.12.11 and 3.8.10,
coordinator 41/41, all three legs green on the first run.

Deferred OUT of Chapter 3 (dispositions in the matrix): witness payload
schemas beyond byte integrity and recognized facts, multi-version
receipt dominance (F-C3-2), multi-member attempt execution (member
completeness classifies today; concurrent member dispatch is a later
milestone), the policy-resolved evidence window, the live Codex
DevTools write-probe (U-02's operational half) and the Codex coverage
quantification (U-04), and everything Chapters 4-6 own (production
verifier runners, acceptance/release payload schemas with
ACCEPTANCE_FRESH, the remote arcs and standalone effect machines).
