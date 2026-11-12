# Chapter 3, milestone 2 — evidence provenance and causal joins

C3-M1 made the ExecutionReceipt's completeness classification
authoritative. C3-M2 makes the evidence UNDER that classification
causally attributable: the Collector no longer receives "some files
associated with this task" — it receives an evidence graph proving that
this frozen Attempt produced these sessions, these commands, these
transcripts and these artifacts, under these role requirements, with no
link resting on timestamps, agent claims or provider heuristics.

## 1. The causal chain (what joins to what)

    TaskContractRecord ──(compute_tiers, INV-11)── PromptProjection (obj-10)
        └─ AttemptContract ──(ledger)── attempt_calls.call_id   [Sequencer-minted, t-a02]
              └─ provider task record (obj-11 bytes: call_id, agent_id, controller_id)
                    └─ handle (obj-11 bytes: native_session_id, pid, started_at)
                          ├─ transcript (obj-13 bytes: session identity)
                          ├─ command governance (obj-12 bytes: session_id, tool_use_id, command_sha256)
                          └─ result (obj-11 bytes: outcome, exit_code, artifacts)
              └─ admitted artifacts (attempt_artifacts rows, ch3:admit-artifact)
                    └─ ExecutionReceipt (obj-15: admitted set + causal joins + per-role completeness)

Every arrow is either a ledger fact (minted ids, admission rows) or a
RECOGNIZED fact extracted tolerantly from producer-owned bytes. No arrow
is ever derived from mtime, directory order, "newest" anything, process
timing or string similarity (INV-04, INV-22).

## 2. The raw witness trust model (obj-11 / obj-12 / obj-13)

- The authoritative content address is `SHA-256(exact producer bytes)` —
  already the admission seam's behavior (D-21/D-38). No canonicalization,
  rewriting, stripping, normalization or inferred fields, ever
  (`CH3-raw-witness-bytes-preserved`, unknown fields and nulls included).
- Parsing is TOLERANT and exists only to extract **recognized
  correlation facts** (`witness_semantics.py`): a witness that is not
  JSON, or carries none of the recognized keys, admits normally and
  contributes no facts. Unknown fields grant nothing and refuse nothing.
- Recognized facts per kind (each key optional; producers own their
  schemas):
  - **obj-11** (autoswarm): `call_id`, `agent_id`, `controller_id`,
    `native_session_id`, `provider`, `outcome`, `exit_code`, `phase` —
    the task / handle / result record family observed in the live home.
  - **obj-12** (helios): `session_id`, `tool_use_id`, `correlation_id`,
    `command_sha256`, `decision`, `executed_utc`, `success` — the
    per-command governance evidence record family.
  - **obj-13** (agent_clis): `native_session_id` / `session_id` /
    session-meta `id`, `call_id`, `provider` — transcript identity.
- **Admission-time correlation gate**: a recognized fact that NAMES the
  execution identity must MATCH the ledger's bound call row — a witness
  whose `call_id` names another call, or whose session identity names
  another attempt's bound session, is REFUSED at admission (cross-attempt
  injection dies at the seam; INV-69 discipline extended to evidence).
  Facts that are absent stay tolerated: absence classifies later as
  missing, never as contradiction (§6).

## 3. The resolved unknowns (grounded 2026-11-12)

- **U-07 RESOLVED — controller joins.** The provider CONTRACT guarantees
  `controller_id` on every created task record: `messages.prepare` stamps
  `{"agent_id", "controller_id"}` on every task; creation REFUSES
  (`controller_required`) when no controller can be established
  (host-session identity → a `main_<32hex>` anchor; a spawning child →
  its own `agent_id`; or the explicit `--controller`); the runner
  hard-indexes `task["controller_id"]` before writing any result; and
  `--no-parent` nulls only the REMOTE-CONTROL `parent` block, never the
  controller. Empirically: 19/19 retained task records in the live home
  carry a well-formed `(call|main)_[a-f0-9]{32}` controller_id, including
  `parent: null` calls and a call→call chain. The frozen child-parent
  causal join is therefore `task.controller_id`, shape-validated;
  `main_*` controllers are host-session anchors outside the attempt
  graph, `call_*` controllers chain to their own task records. Missing
  controller_id in an obj-11 task-record witness = a malformed producer
  record = missing evidence (INCOMPLETE); a controller that names a call
  the ledger proves belongs elsewhere = contradiction (INTEGRITY_FAILED).
- **U-06 RESOLVED — outcome vocabulary.** The provider emits EXACTLY
  three outcomes (`runner.py`): `completed`, `failed`, `cancelled` —
  `cancelled` when a cancel marker/output-cancel is observed, `completed`
  only on success-exit + no error + terminal output, `failed` otherwise
  (the result record is INITIALIZED "failed", so interruptions that still
  write a result are failures). Timeout/stale has NO provider outcome —
  and the absence of `result.json` proves ONLY that no terminal provider
  outcome has been observed, never that the call is running: running,
  stale or interrupted status comes from separate durable
  lifecycle/session evidence (the t-a03 handle witnesses; staleness is
  t-a12's), because absence of an authoritative observation is not an
  observation of the opposite state (the operator's pre-commit
  correction; the same G3 rule as find_session). The frozen transport
  mapping
  (`AUTOSWARM_OUTCOME_TO_COMPLETION`): `completed → "success"`,
  `failed → "failed"`, `cancelled → "cancelled"`; any OTHER string passes
  through verbatim and can satisfy a role only if the frozen
  `completion_policies.success_outcomes` lists it — the provider reports,
  the frozen contract decides (never the provider).
- **U-04 NARROWED — Codex governance coverage.** A real Codex (Luna)
  session's per-command governance evidence record carries the same
  recognized identity facts as the Claude path: the agent session id, the
  per-tool-use id, the command text and its sha-256, and the execution
  result fields (verified on a retained record from the 2026-11-01 run).
  Equivalence of COVERAGE BREADTH across all tool surfaces is not
  quantified here; the contract's `evidence_requirements` remain the
  authority, and a Codex contract that requires COMMAND_GOVERNANCE gets
  the same classification rules as any other.
- **U-02 NARROWED — Codex write provenance.** The durable producer
  evidence exists without any live API: Codex sessions leave
  session-identified rollout records (`~/.codex/sessions/.../rollout-
  <timestamp>-<session-uuid>.jsonl`, first event `session_meta`) whose
  event stream records patch/exec/tool activity (116 such events in the
  sampled real rollout), and the documented export bridge
  (`Codex_claude_devtools/scripts/codex-export.ts`, schema
  `codex-export.v1`) is what the coordinator's codex route already
  requires — it "never parses raw rollout JSONL" and is the normalized
  reader. The Chapter 3 contract is provider-independent: a Codex
  artifact is attributable ONLY when durable producer/session evidence
  binds it to the Attempt (obj-13 bytes = the rollout or its v1 export);
  probing the live DevTools HTTP interface for richer per-write
  attribution stays an operational follow-up, not an authority input.
- **U-01 — the DENY-then-ran detection rule.** The deterministic join,
  over witness bytes alone: a governance witness recording
  `decision = DENY` for `(command_sha256 S, session_id N)` is
  CONTRADICTED by any witness of the same attempt proving execution of a
  command with sha `S` in session `N` at-or-after that decision — the
  join keys are the command's content hash and the session/tool-use
  identity, NEVER wall-clock proximity. The Collector classifies the
  contradiction INTEGRITY_FAILED (`deny_then_ran` in the receipt's
  contradictions). The historical root cause remains a Helios-side item,
  kept separate from this protocol rule.

## 4. obj-10 PromptProjection — Chapter 3 closure

The full payload schema lands as `prompt_projection.schema.json`
(docs provenance copy + `src/autobot/schemas_chapter3/` mirror,
byte-identical, pinned): `schema_version` v1, `kind`, `minter:
"dispatcher"` (obj-10's registered minter), `authority_effect: "none"`,
the recomputed record binding (record_hash / requirement_digest /
contract_digest / task_contract_id), the exact attempt identity
quadruple, `role` (+ registered `role_transform`), the carried frozen
authority context (allowed_scope, acceptance_assertions,
evidence_requirements, specification_inputs), the attributed
`engineering_context` (author, guidance, and the approved optional
bundle: context_refs, predecessor_findings with finding_ids,
repository_context, expected_outputs), and the record-derived
`completion_contract`. Call identity is NOT in the projection — the
Sequencer mints `call_id` at t-a02, strictly after projection, and the
ledger binds projection_address to the call (documented, pinned by the
determinism tests). `project_prompt` validates the payload against the
schema at mint. Determinism pins: same inputs → same bytes/address;
different attempt → different projection; different approved context →
different projection; provider/model appear NOWHERE in the payload (the
M7 pin, carried).

## 5. Artifact provenance (INV-03 / INV-04 / INV-22)

Schema v8 `attempt_artifacts`: one PROVENANCE EVENT per admitted
engineering artifact — `admission_id` (minted), `attempt_id`,
`sequence_id`, `call_id` (ledger-derived), `role` (the dispatched role),
`logical_path` (producer-declared logical identity), `content_sha256`
(exact bytes in the CAS), `admitted_utc`; unique on
`(attempt_id, logical_path, content_sha256)`; journaled
`ch3:admit-artifact` in the same transaction; admission window
DISPATCHED/RUNNING/FINISHED (like witnesses — everything a frozen
candidate references is admitted before t-a05). Byte-identical artifacts
from two attempts are two events; ownership flows only from the
admission's ledger correlation, never from the filesystem: the runtime
reads artifact BYTES handed over with explicit attribution and never
consults paths' mtimes (a source-scan proof pins `getmtime`/`st_mtime`
out of the authority plane, and the behavioral INV-04 proof plants a
newer impostor file that changes nothing). The candidate itself is an
admitted artifact first; `freeze_candidate` then references its address.

## 6. Missing versus contradictory (the C3-M1 distinction, extended)

- MISSING causal evidence — a required kind absent, a required join
  absent (no obj-11 record naming the bound call, a transcript that
  cannot be tied to the session) → INCOMPLETE, with the D-C3-1 bound.
- CONTRADICTORY evidence — a recognized fact proving an impossible or
  forbidden relationship → INTEGRITY_FAILED, named in the receipt's
  `contradictions`: a witness bound to another call/session (refused at
  admission when visible there; classified when only cross-witness),
  a controller claiming call A while the provider record proves call B,
  DENY-then-ran (§3 U-01), byte-integrity failures (D-C3-3).
- LLM statements are evidence CONTENT only (`CH3-provenance-from-
  witnesses`): "tests passed" in a transcript carries no authority —
  verification is the registered verifier plane's, completeness is the
  Collector's derivation.

## 7. The receipt, extended (obj-15 v1 amended in-chapter)

`build_receipt` additionally derives, from the same durable inputs:
`causal_joins` (per admitted witness entry: the recognized facts that
join it to the bound execution and the join verdict), `contradictions`
(sorted, each `{rule, ...identities}`; non-empty → INTEGRITY_FAILED),
`member_completeness` (per frozen role: `required`, evidence
attributable to that role via the causal chain, and — where recognized —
the policy verdict on its reported outcome), and `artifact_provenance`
(the attempt's admitted artifact events). The admission seam and
recovery 8f re-derive ALL of it byte-identically (D-C3-2 unchanged);
the chapter is unpublished, so the obj-15 schema amends in place with
every M1 pin re-proven.

## 8. Role/member completion (C8, CORR-08, CORR-20)

The frozen record's `completion_policies` is the ONLY membership
authority: required roles are the entries with `required: true`;
evidence never creates membership ("whichever sessions happen to exist"
counts for nothing). t-a04/t-a11 evaluate the collected, MAPPED outcome
(§3 U-06) against the dispatched role's `success_outcomes` — a
cancelled poller whose policy lists "cancelled" finishes; a failed
required reviewer fails the attempt (t-a11). The receipt's
`member_completeness` then evaluates EVIDENCE per required role:
a required role without attributable evidence → INCOMPLETE; optional
roles absent → no effect; evidence attributes to a role only through
the causal chain (the ledger's dispatched role in v1 single-member
attempts — a contract requiring more concurrent members than the
execution model dispatches classifies INCOMPLETE by construction and
waits for the multi-member milestone rather than inventing attribution).

## 9. HDB / index boundary (INV-30, U-05)

Nothing in this milestone reads any index: every join operates on
admitted witness bytes and ledger/content-store facts. INDEX_LAG can
never block COMPLETE unless a frozen assertion explicitly declares an
HDB-derived query (none do in v1); the watermark interface stays a
deferred diagnostic (U-05, untouched).

## 10. Recovery

8f re-derives the extended receipt (joins, contradictions, member and
artifact facts ride the same byte-equality). New 8g validates artifact
provenance both ways (row ↔ `ch3:admit-artifact` journal entry; bytes
resolve and re-hash; the attempt's execution binding exists) and the
witness-correlation coherence of adopted executions (a RUNNING-or-later
attempt's bound native session must not be contradicted by its own
admitted obj-11 facts). Absent-but-collectible evidence stays the
C3-M1 INCOMPLETE/deadline rule — recovery never invents certainty; and
repeated recovery converges (pure functions over durable rows).

## 11. Findings (F-C3, continued)

- **F-C3-6 accepted** — the deny_then_ran join relaxes to attempt scope
  when a governance record names no session: a DENY without session
  identity joins same-sha execution evidence within the attempt's
  admitted set. Deliberate (D-C3-6 records the fallback): the admission
  gate already refused anything naming another session, so the
  attempt-scoped set is the right containment — and the alternative
  (ignoring degenerate producer records) would let a producer evade the
  rule by omission.
- **F-C3-7 accepted** — a forged pre-binding identity (an obj-11 witness
  admitted at DISPATCHED naming a session that later differs from the
  t-a03 binding) passes the tolerant admission gate but is CONTRADICTED
  at collection (the join verdict against the bound session) and HALTs
  at recovery's 8g coherence check: classification catches live, 8g
  catches durable — both fail closed.
- **F-C3-8 accepted** — admit_artifact accepts new bytes under an
  existing logical path as a NEW provenance event (append-only, latest
  by rowid), exactly obj-15's re-collection semantics; the receipt
  carries every event, so a flapping producer is visible, never
  silently collapsed.
- The D-25 lesson re-learned in this milestone's own fixtures: a
  two-outcome success_outcomes list must be in canonical set order, or
  admission rejects the contract (caught by the first suite run,
  ce-053/ce-054).

## 12. Proof of record

- venv CPython 3.12.11: **193/193 OK** (gate 20261112-ce-055; the first
  run, ce-053, flushed four defects — the crash-witness byte pin, the
  missing sequence_id in a new test helper ×2, and the D-25 ordering of
  a fixture's success_outcomes — diagnosed under ce-054).
- PATH CPython 3.8.10: **193/193 OK** (gate 20261112-ce-056).
- Coordinator regression: **41/41 OK** (gate 20261112-ce-057).
- 193 = C3-M1's 179 + 14 new conformance tests in
  `tests/autobot/test_provenance.py`: the three registered invariant
  proofs (INV-03 `CH3-artifact-provenance-single-attempt`, INV-04
  `CH3-mtime-never-selects-artifact` behavioral + source-scan, INV-22
  `CH3-provenance-from-witnesses`), raw-byte preservation with unknown
  fields and nulls, cross-attempt witness injection refusals, the
  controller join with shape validation, deny_then_ran positive and
  negative arms, frozen-membership and outcome-mapping proofs
  (cancelled builder fails t-a11, cancelled poller finishes t-a04),
  obj-10 closure determinism, artifact-forgery recovery HALTs, and the
  restart reconstruction of the whole causal graph. INV-30 is satisfied
  structurally in v1: no module of the plane reads any index, no frozen
  assertion declares an HDB-derived query, and the U-05 watermark
  surface stays deferred as diagnostic-only.
