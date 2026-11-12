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

Still open after M1: witness payload schemas beyond byte integrity,
cap-30's index-state facet, multi-version receipt dominance, the
policy-resolved evidence window, and everything Chapters 4-6 own
(production verifier runners, acceptance/release payload schemas with
ACCEPTANCE_FRESH, the remote arcs and standalone effect machines).
