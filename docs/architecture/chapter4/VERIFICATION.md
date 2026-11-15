# Chapter 4 Verification Architecture (C4-M1 + C4-M2)

Every statement here is implemented and proven by the suite
(`tests/autobot/test_chapter4.py` and `test_chapter4_m2.py` plus the
rewired verification, receipt, provenance, finalization, acceptance,
recovery and pilot suites). Registry citations are to the frozen
Chapter 0/1 registers. Sections 1–12 are C4-M1 (amended where C4-M2
extended them); sections 13–19 are C4-M2. The test-independence milestone
(TI-1) amended sections 4, 17 and 18 for two defects its oracles exposed
(F-TI-6, F-TI-4); the harness itself is described in `INDEPENDENCE.md`.

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
fresh run directory (an entrypoint outside the closure-path grammar of
section 17 is refused before anything is written — F-TI-6), materializes
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

Not in this chapter, per the frozen roadmap: obj-14 remote-check
witnesses and remote verification/publication (Chapter 6), obj-30/obj-35
full schemas and acceptance semantics (Chapter 5), ACCEPTANCE_FRESH
(existing deferred arm), HDB as authority (forbidden by existing
architecture), planner/Taskforce authority over verifier results
(forbidden by cap-32/cap-35), CORR-57 governance and Helios
implementation cleanup (separate tracks). The proofs M1 left open —
`CH4-a1-transform-chain` (INV-44), `CH4-role-transform-check` (INV-49),
`CH4-artifact-retention` (INV-58), the BY_ATTESTATION obj-19 arm and
executable packaged closures — closed in C4-M2 (sections 13–19).

## 13. The A1 transform calibration (U-12, INV-44) — C4-M2

U-12 is RESOLVED from the executing provider source and recomputed
retained evidence, not from documentation or memory:

- **Ingestion (AutoSwarm `--input` file and stdin alike)**: strict UTF-8
  (non-UTF-8 input fails closed — never decoded by guess), the BOM kept
  as U+FEFF, universal newlines (CRLF and lone CR fold to LF), nothing
  appended, nothing stripped. `task.json` persists the prompt string
  VERBATIM (atomic UTF-8; JSON escaping shields the value), and the
  coordinator's stdin pipe round-trips the decoded string identically.
- **Evidence**: the v13 and v14 Luna prompts are byte-identical to their
  prompt files (raw file sha == recorded prompt sha; LF-only, no BOM);
  six PS 5.1-composed monitor prompts carry exactly the predicted
  signature — BOM retained, CRLF normalized at ingestion, the PS-appended
  terminator present in the input bytes. PowerShell 5.1's encoding and
  append effects are INPUT-side facts: they land in the specification
  bytes and are covered by INV-21 exact-byte identity.

The calibrated family is REGISTERED and EXECUTED: `identity-projection`
(byte identity — the runtime's declared role transform) and
`autoswarm-input-v1` (the ingestion calibration above), both reproducibly
pinned python closures with the argv protocol
`transform.py <input_path> <output_path>` (exit 0 = the output file holds
the exact output bytes; any nonzero exit is an execution failure with no
output). One byte of input difference is a different specification and a
different output identity; nothing anywhere decodes, normalizes or
compares TEXT — identity is bytes (INV-21/INV-44). Historical ingestion
paths that differed would be registered as DISTINCT transforms; none are
claimed beyond the calibrated one.

## 14. The role-transform check (INV-49) — C4-M2

**Chain**: the frozen record's single declared specification input (exact
bytes, by address) → the record's declared registered transform for the
DISPATCHED role (`role_transforms`, bound through
`resolved_bindings.transform_registrations`) → the EXPECTED prompt
identity → compared against the OBSERVED identity derived ONLY from
admitted obj-11 producer bytes (the provider's persisted task record
carries the prompt the child was actually given; `witness_semantics`
derives its content identity as a recognized fact). A child claiming
"this is the prompt I received" is not evidence, and neither is the
runner: a receipt claiming an observed identity that no admitted producer
record carries is refused, a claim differing from the admitted evidence
is refused, and several DISTINCT admitted identities are an ambiguity
refusal — never chosen from.

**Result model** (D-C4-8): a BY_VERIFIER result over the D-47
ROLE_TRANSFORM part, row key `role-transform/<role>`, with the bound
TransformRegistration and a `transform_check` block carrying the exact
identities. PASS ⇔ completed transform and observed == expected; FAIL ⇔
`transform_output_mismatch` (the ordinary repair path, t-a08/t-s11);
INCONCLUSIVE ⇔ `verifier_execution_failed` or
`missing_producer_evidence` (blocks to t-s12 unknown).

**Admission (D-C3-2 grade)**: for every completed-transform claim the
seam RE-EXECUTES the bound transform closure over the exact declared
input bytes and refuses an expected identity it did not just re-derive —
so a forged expected value dies even when every byte of the receipt is
internally consistent. The runner performs the same derivation first;
the seam's re-derivation is the authority.

**Conclusion**: when the frozen record declares a transform for the
dispatched role, NO conclusion exists without the admitted check; a PASS
row unlocks t-s10 (journaled under `role_transform_checks`), FAIL and
INCONCLUSIVE behave as any row. The production runner executes the check
before any assertion, so a child that received the wrong prompt is caught
even when every assertion would pass.

## 15. obj-10 transform provenance (INV-44) — C4-M2

`PromptProjection` gains the optional `transform_provenance` block
{transform_id, registration, input_name, input_sha256, output_sha256} —
the dispatcher's CLAIM of the registered transform execution it
performed, carried only for a role the frozen record binds to that
transform (both schema mirrors amended; `authority_effect` stays none).
At the check's admission the claim must agree with the re-derivation:
wrong transform, wrong registration, wrong input or wrong output in the
projection refuses the admission (dispatcher drift dies at the seam);
a projection that is not a canonical obj-10, or carries no claim, simply
contributes nothing — the check's authority never came from the
projection.

## 16. BY_ATTESTATION (D-52/D-53) — C4-M2

`attest_assertion` is the obj-37 ADMISSION seam (D-C4-11): durable
identity (minted by you), provenance (attests PASS) and FULL scope
binding — the exact attempt, its contract hash, the admitted obj-16
candidate the verdict is ABOUT, and the D-47 part reference
{record, ASSERTION, assertion_id}. The exact bytes are preserved (D-31)
and the `attempt_attestations` row (schema v10, one per
(attempt, assertion)) is journaled `ch4:admit-attestation`. The verifier
runner INCORPORATES an admitted attestation into the canonical obj-19
BY_ATTESTATION arm — {attestation_sha256, outcome ATTESTED, mode
HUMAN_ATTESTED, and NO registration, execution or environment: an
attestation is a human act and is never dressed up as an execution} —
through `register_attested_result`, which re-validates every binding
including byte-address equality with the ADMITTED attestation. The row's
authority principal is you; the runner can never synthesize an
attestation, only give an admitted one its canonical receipt form.
Attestation remains durable BEFORE t-s10 can commit. Chapter-5 boundary
untouched: acceptance consumes PASS/ATTESTED rows exactly as before.
A documented vocabulary boundary: an `environment_constraint` declared on
a HUMAN_ATTESTED assertion constrains nothing — no execution exists to
constrain, and D-52's authority never involved environments.

## 17. Packaged closures (D-C4-10) — C4-M2

The `packaged`/`manifest_sha256` form EXECUTES: the manifest bytes are a
PackageManifest v1 naming every entry {path, content_sha256}, the
entrypoint (one of the entries, and it must AGREE with the
registration's), and an optional pinned interpreter identity. Every entry
materializes from the content store at its relative slash path and only
the bound entrypoint executes.

**The closure-path grammar (TI-1, F-TI-6).** A closure path must name the
SAME plain file on every filesystem the runtime materializes into, or the
bound entrypoint can end up holding bytes the manifest binds to another
entry. The original grammar (no absolute paths, no `..`, no backslashes,
no exact duplicates) did not hold on Windows: two entries differing only
by case, by a trailing dot or space, or by a `::$DATA` stream suffix are
ONE file there, and a drive-letter segment (`C:tool.py`) resolves outside
the run directory. The grammar is now, identically on every platform: a
frozen D-26 RepoPath (relative, slash-separated; no empty, `.` or `..`
segment; no backslash, NUL or control character; no edge whitespace; NFC)
whose every segment also carries none of `< > : " | ? *`, no trailing
dot, no Windows device name (`CON`, `NUL.py`, `nul .txt`, `COM1` …) and
not the 8.3 short-alias form (`NAME~1.EXT`); no entry whose
first segment is a name the runner itself writes into the run directory
(`input.bin`, `output.bin`, `context.json`, `subject`); no two entries
equal under case folding; no entry that is both a file and a directory of
another. A manifest outside the grammar is an IntegrityHalt at resolution
(`t-s12 bound_artifact_unavailable`) — before anything is written or
executed. `materialize_closure` applies the same grammar to the
REGISTRATION's entrypoint for python and native closures (the frozen D-26
grammar a registration is admitted under still accepts `C:tool.py`; the
seam that writes does not), refuses a python entrypoint named like the
closure's own `dependency.lock`, and re-checks a package it did not
parse. The
`packaged`/`image_digest` form names a container image; no container
engine is part of this runtime's frozen environment, so it HALTs
explicitly rather than being approximated. ONE resolver and ONE
materializer (`autobot.transforms`) serve verifier executions and
transform executions alike; execution parity is proven for python and
packaged closures (the corpus holds no native binary to execute; native
parity is proven at resolution and materialization).

## 18. Artifact retention (INV-58) — C4-M2

Two halves, explicitly split:

- **Retention roots** (`Sequencer.retention_roots`): content address →
  the NON-terminal sequences whose frozen records bind it through their
  registrations' closures (python: script + lock; native: executable;
  packaged: manifest + every entry). A leaf is live while ANY
  non-terminal sequence binds it; registration REPLACEMENT frees nothing
  until every sequence bound to the old registration is terminal.
- **Reclamation** (`Sequencer.reclaim`): an EXPLICIT act over named
  addresses — never a sweeper — that recomputes the roots INSIDE the
  call (a list computed before a restart, an admission or a replacement
  is stale and protects nothing), refuses every live closure leaf, every
  address any ledger column references, and every frozen record's
  declared specification input (resolvable forever: recovery re-resolves
  ALL records' inputs and INV-44's chain starts at those exact bytes),
  unlinks the rest, and journals removals (`ch4:reclaim`).

  **TI-1, F-TI-4.** The rule is "never delete what `recover()` resolves",
  and the first implementation protected less than that: recovery also
  resolves the finalization receipts the JOURNAL names (the t-s16 commit
  receipt and observation receipt, the acceptance and release receipts —
  the last two also `released_edges` columns the column list lacked) and
  the execution evidence every admitted verifier result binds
  (`execution.output_sha256`, re-read by the F-C4-1 consistency check).
  Reclaiming any of them turned a clean ledger into a permanent recovery
  HALT (D-66). `reclaim` now refuses both families, judged exactly as
  recovery reads them (one shared constant names the journaled receipt
  facts). Still reclaimable, by the same rule: evidence the journal
  merely mentions and recovery never resolves (for example a t-i07
  attestation's address) — the independent auditor reports those as
  dangling references, never as findings.

Recovery REPORTS `retention.missing_bound_leaves` for live records whose
bound leaves do not resolve (D-C4-9) — visibility at restart, while the
fail-closed act stays dispatch-time INV-53. Deletion attempts in every
live state are refused; a shared leaf is retained until ALL binders are
terminal; eligibility after terminality is proven over the real
READ_ONLY completion path.

## 19. C4-M2 residuals, recorded

- The in-process writer residual of D-C4-5 applies to the transform
  plane identically: a writer with content-store access could plant
  consistent FAILURE diagnostics to BLOCK (never to pass — every
  completed-transform claim is re-derived at admission); Chapter 8's
  authority-service identity closes the caller boundary.
- The observed side is as strong as the admitted producer evidence:
  witness admission (producer gates, C3 provenance) is the trust
  boundary, and fabricated obj-11 records can at most force ambiguity or
  mismatch — refusals and FAILs, never PASS.
- Registration authors seed closure leaves; admitting a registration
  whose leaves were never uploaded leaves dispatch HALTing (INV-53) and
  recovery reporting (D-C4-9) — fail-closed, visible, recoverable.
