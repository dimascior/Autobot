# Chapter 4 Verification Architecture (C4-M1)

Every statement here is implemented and proven by the suite
(`tests/autobot/test_chapter4.py` plus the rewired verification, receipt,
provenance, finalization, acceptance, recovery and pilot suites). Registry
citations are to the frozen Chapter 0/1 registers.

## 1. CandidateReceipt (obj-16)

Minted by the **git_adapter** (cap-32; task_force forbidden), immutable,
attempt-bound. The payload binds: the attempt identity (sequence, attempt,
fence), the durable AttemptContract hash, all three frozen record tiers,
the finalization class, the AttemptContract's frozen `source_state`
(INV-02), the admitted artifact provenance triples (INV-03 — the ledger's
events, never a caller claim), the capture instant, and — for
SOURCE_MODIFYING work — the candidate Git tree identity read from Git's
own object model (cap-42: `rev-parse <tree-ish>^{tree}`).

Candidate identity derives from authoritative repository and ledger facts
only. Never: agent claims, transcript statements, mtime, a working tree
observed later without binding, or "latest commit" heuristics (a source
scan pins the chapter-4 modules blind to mtime and directory listings).

### Freeze admission

`t-a05`/`t-s07` REQUIRE obj-16. `Sequencer.freeze_candidate` validates,
inside the one freeze transaction: canonical-form bytes (the address IS
the payload's identity), the mirrored schema, and equality of every
ledger-derivable member against the ledger itself. A validated receipt
commits its `attempt_candidates` row — **one per attempt**, enforced by a
unique index — journaled as `ch4:admit-candidate`.

### Candidate freeze and immutability

The row plus the content-addressed bytes are the immutability anchor:
after the freeze, repository and workspace drift cannot reach the
verification subject. The runner materializes the subject from the
content store by the admitted addresses; the live conformance verifier
re-hashes every subject artifact against its content address, so drift
into the subject would FAIL. The proof drives: candidate captured →
repository gains commits and `main` moves → verification still PASSes
over the frozen bytes and every receipt binds the original obj-16, whose
tree is no longer the repository's head.

### READ_ONLY vs SOURCE_MODIFYING shape

READ_ONLY work gains **no invented candidate tree** (D-37 absence): its
candidate is the admitted artifact/observation set, and minting with a
repository is refused. SOURCE_MODIFYING work MUST bind a tree; capture
without a repository and tree-ish is refused, and in the Taskforce flow
the tree-ish is the specialist's PROPOSED new value (the effect
proposal), never "whatever the workspace holds now" — an SM attempt with
no capturable tree stays FINISHED, a visible wait.

## 2. ActualVerificationEnvironment (obj-18)

Minted by the **environment_probe** (cap-33) under the FROZEN Chapter 1
schema, used without weakening. D-56's capture rule, literally:
interpreter {implementation, exact version, executable SHA-256 — the
binary's identity, never its path}; platform {os, os_version,
architecture}; env_vars — ONLY the variables the frozen constraints name
(an unset variable is omitted and the match fails closed); tools — ONLY
the tools the constraints name {presence, exact version, executable
SHA-256}, an unprobeable tool omitted and failing closed. The probe never
dumps the process environment: secrets stay out of receipts.

**One capture per attempt**, admitted durably
(`Sequencer.admit_environment_capture`, `attempt_environments`, unique
index) BEFORE any verifier launch — the crash matrix's capture point:
restart resumes from the admitted capture; a different capture for the
same attempt is refused. Restart never recaptures its way to a more
favorable environment.

## 3. Environment constraint evaluation

`verification.environment_satisfies` implements the complete D-56
vocabulary: interpreter implementation and exact version lists, platform
os and architecture lists, named tool presence with exact version lists,
named env var exact values. Every unsupported section AND every
unsupported rule key fails closed. A mismatch is a
verification-environment authority problem: the runner refuses BEFORE
executing anything and the registered HALT is `t-s12 env_mismatch` —
never an ordinary failed assertion, never a consumed repair.

## 4. Registered verifier closure (D-57 / INV-53 / INV-66)

`verifier_runner.resolve_closure`, before any execution:

1. recompute `artifact_root` from the ClosureManifest and require
   equality with the registered value (SEM-15 — also enforced at
   registration admission by the Registry);
2. prove every required leaf's exact bytes exist in the content store
   (python: script + dependency lock; native: the executable) — the D-66
   re-hash on every read;
3. prove the registered interpreter is the one actually available
   (`interpreter_id` equality with the live identity).

Anything missing or divergent is an IntegrityHalt — never a
substitution: no PATH lookup, no same-named workspace file, no other
version "because it works". The durable sequence-level halt is `t-s12
bound_artifact_unavailable`. Packaged closures are not executable by this
runtime in v1 and HALT rather than being approximated. Service-dependent
registrations (`service:*`) cannot even freeze against a DETERMINISTIC
assertion (SEM-16 at `validate_freeze`) and the runner re-checks
eligibility as defense in depth.

Execution materializes the script bytes FROM THE CONTENT STORE into a
fresh run directory (entrypoints containing `..` are refused), materializes
the subject artifacts by content address, writes a context file (the
assertion, the obj-16 payload, the obj-18 payload, the registration
identity, the subject manifest) and invokes the verified interpreter with
`-I`. The subject is therefore the frozen candidate, by construction.

## 5. The verifier runner (cap-35)

`ProductionVerifierRunner.verify_attempt` is the registered eleven-step
flow per DETERMINISTIC assertion without an admitted result: read the
frozen assertion and its exact bound registration through the Sequencer's
`verification_state` read surface (resolution failure is the INV-53
HALT), check eligibility, resolve the closure, bind the admitted obj-16,
capture-or-reuse the attempt's one obj-18, validate every declared
constraint before any execution, execute the exact closure, translate the
observation, mint canonical obj-19/obj-20 bytes and submit them through
`register_verifier_result`. HUMAN_ATTESTED assertions are never touched.
The Sequencer executes nothing — it admits results and applies the
registered transitions; no verifier initiates a retry.

## 6. The result protocol and vocabulary (D-C4-3)

A registered verifier reports its conclusion by exiting 0 and printing,
as its last stdout line, `{"autobot_verifier_result": {"conclusion":
"PASS" | "FAIL", ...}}`. The runner translates:

| observation | outcome | obj | conclusion_basis |
|---|---|---|---|
| exit 0, conclusion PASS | PASS | obj-19 | — |
| exit 0, conclusion FAIL | FAIL | obj-20 | verifier_reported_fail |
| exit ≠ 0 | INCONCLUSIVE | obj-20 | verifier_execution_failed |
| exit 0, no typed conclusion | INCONCLUSIVE | obj-20 | malformed_verifier_result |

Only a genuine reported FAIL follows the verification-failure/repair path
(t-a08 → t-s11 while budget remains, t-s12 budget_exhausted after).
Authority uncertainty — a crash, prose, malformed output — is
INCONCLUSIVE: it satisfies nothing, consumes no repair, and routes to
`t-s12 unknown`. No new protocol outcomes were invented for exit codes.

## 7. VerificationReceipt (obj-19) and VerificationFailureReceipt (obj-20)

Canonical, schema-pinned payloads (mirrors byte-identical between
`src/autobot/schemas_chapter4/` and this chapter's `schemas/`). Both bind:
the attempt identity INCLUDING the fence, the AttemptContract hash, the
record hash, the admitted obj-16 address, the admitted obj-18 address,
the D-47 typed assertion part reference `{record, part_kind: ASSERTION,
part_id}` (never a mutable list position), the exact bound registration
{object hash, registration_id, artifact_root}, `result_source:
BY_VERIFIER`, the outcome, and the execution provenance {closure kind,
entrypoint, exit code, output content address}. obj-20 is a separate kind
with its own `conclusion_basis` — never obj-19 with a boolean flipped.

### Admission (CH4-verification-binding, INV-17/INV-18)

`register_verifier_result` validates: canonical-form bytes, the schema
for the kind, kind↔outcome agreement, the minter, and EQUALITY of every
binding against durable ledger facts — the current fence (a stale or
foreign receipt has no authority), the attempt's one candidate, the
attempt's one environment, the assertion's part reference and verifier,
the frozen record's bound registration. Every axis is attacked
independently in the suite and fails closed with nothing committed.

### Outcome↔evidence consistency (F-C4-1, D-C4-5)

Found in this chapter's adversarial review: a fully bound receipt could
still CLAIM an outcome its own evidence did not support. The admission
now resolves the execution output bytes from the content store and
requires the claimed outcome to agree with them under the result protocol
(PASS needs a PASS conclusion at exit 0; FAIL needs a reported FAIL;
verifier_execution_failed needs a nonzero exit; malformed needs exit 0
with no typed conclusion). Recovery re-verifies the same consistency.
Residual, recorded honestly: in the interim in-process topology (D-01)
a writer with content-store access could plant consistent evidence; the
target authority-service identity (Chapter 8) closes the caller boundary.
INV-06's registered proof stands on the full stack: SEM-16 eligibility,
the typed protocol (prose is never a conclusion), the minter gates, the
binding admission and the evidence consistency.

## 8. Assertion aggregation (CH4-mandatory-assertions-required, INV-07)

`conclude_verification` demands an authoritative result row for EVERY
declared assertion — strictly stronger than the mandatory floor, which is
a frozen subset — so one PASS never masks a missing assertion and no
optional result substitutes. One row per (attempt, assertion) is a unique
index: duplicates are idempotent or refused, never overwritten; a result
bound to part A cannot register as B. INCONCLUSIVE members make
conclusion impossible (t-s12 unknown is the exit). All-PASS (or ATTESTED)
drives t-a07 + t-s10; any FAIL drives t-a08 with t-s11/t-s12.

## 9. The attestation boundary (D-53, unchanged)

HUMAN_ATTESTED assertions pass only through YOUR
`ManualAttestation.ASSERTION` (obj-37), scope-bound to the attempt,
assertion and contract hash — `attest_assertion` is unchanged, the
verifier runner never touches those assertions, and nothing lets a
verifier or model stand in for you. obj-19's `result_source` is the
constant `BY_VERIFIER` in v1; the BY_ATTESTATION receipt arm belongs to
the chapter that consumes it at acceptance (Chapter 5), with the admitted
rows as t-s10's consumption surface meanwhile.

## 10. HALT vs FAIL

FAIL is an assertion conclusion by the registered verifier over the bound
candidate; it drives repair under the frozen budget. HALT is authority
uncertainty or an authority-boundary problem: unknown/INCONCLUSIVE
results, environment mismatch, budget exhaustion, a bound artifact that
no longer resolves (`t-s12`'s four registered arms), an integrity stop
(IntegrityHalt) that commits no transition until the durable t-s12 is
applied with the observed reason. The suite proves a crash is never
dressed up as FAIL and a wrong environment never consumes a repair.

## 11. Recovery (8h) and the crash matrix

Restart validation now covers the chapter-4 proof objects, both
directions: every `attempt_candidates` row — bytes re-hash, canonical
form, schema, row↔payload agreement, every ledger binding re-validated
(attempt identity and fence, contract hash, record tiers, the durable
AttemptContract's source_state, the admitted artifact triples, the
class/tree coupling), journal pairing; every post-freeze attempt MUST
have its candidate row (forged state halts). Every
`attempt_environments` row — bytes, schema, attempt binding, journal
pairing. Every verifier-produced `attempt_verifications` row — canonical
bytes, schema, the full INV-17 binding equality, outcome↔evidence
consistency, and the terminal-state coherence: VERIFIED attempts carry
only PASS/ATTESTED rows, REJECTED attempts carry a FAIL row. Admitted
registrations' closure leaves are referenced content (INV-58's
storage half), never orphans.

Crash points proven: candidate bytes without admission are harmless
orphans; the freeze (admission + t-a05 + t-s07) is one transaction —
death leaves FINISHED with nothing half-admitted; the admitted capture
survives death before launch and is REUSED, never recaptured; admitted
results without conclusion conclude after restart from durable rows; the
hard-death subprocess matrix (`crash_child.py` verifying stage) restarts
across the C4 rows and 8h validates them; repeated recovery converges.
The candidate and the captured environment are immutable proof objects —
restart does not silently recapture them to obtain a more favorable
result.

## 12. Chapter boundaries

Not in this chapter's M1, per the frozen roadmap: obj-14 remote-check
witnesses and remote verification/publication (Chapter 6), obj-30/obj-35
full schemas (Chapter 5), ACCEPTANCE_FRESH (existing deferred arm), HDB
as authority (forbidden by existing architecture), planner/Taskforce
authority over verifier results (forbidden by cap-32/cap-35), CORR-57
governance and Helios implementation cleanup (separate tracks). Remaining
chapter-4 registered proofs for later milestones:
`CH4-a1-transform-chain` (INV-44), `CH4-role-transform-check` (INV-49),
`CH4-artifact-retention`'s retention-window half (INV-58), plus the
BY_ATTESTATION obj-19 arm and executable packaged closures.
