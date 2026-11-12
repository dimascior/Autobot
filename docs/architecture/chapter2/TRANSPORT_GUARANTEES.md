# Real-provider transport guarantees (M3 analysis, 2026-11-10)

The operator's M3 condition: prove that a real AutoSwarm provider supports
the required idempotency and session-correlation guarantees before treating
the fake transport's behavior as representative of production. This is that
analysis, grounded by inspection of the installed provider and the in-repo
integration layer. The **executable** proof is the bounded real-provider
test, which per the same directive runs only after the deterministic
fake-transport acceptance gate and its adversarial review.

## The required guarantees (src/autobot/transport.py)

| # | Guarantee | Contract point |
|---|---|---|
| G1 | Durable dispatch correlation: the `call_id` exists in the authoritative ledger before any external process | `Sequencer.dispatch_attempt` mints and commits it (t-a02) before `launch` |
| G2 | Idempotent launch: one `call_id`, one session, however many `launch` calls | `Transport.launch` |
| G3 | Honest observation: `find_session -> None` means UNOBSERVED, never FAILED | `Transport.find_session`; `reconcile_dispatched` holds the attempt in DISPATCHED |
| G4 | Positive failure determination: t-a10 only on proof, never on absence | `Transport.positive_launch_failure` |
| G5 | Collectable producer originals: witness bytes exactly as produced | `Transport.collect` → `Sequencer.admit_witness` (D-21/D-38) |

## What the installed provider provably has (inspection, 2026-11-10)

Inspected: `AUTOSWARM_HOME = E:\Users\dimas\autoswarm-home` (the live home
with 18 recorded calls) and `src/coordinator/dispatch.py` (the integration
layer that already drives this provider in production).

1. **A durable, per-call registry keyed by the D-22 call-id form.** Every
   execution lives at `AUTOSWARM_HOME/calls/call_<32hex>/` — the exact
   `call_<32hex>` shape `mint_id("call")` now produces. The directory is
   the provider's own correlation unit.
2. **A session-correlation record.** `calls/<call_id>/handle.json` carries
   `native_session_id` (the provider session UUID), `pid`, `child_pid`,
   `phase`, `started_at`, a `runner_lock` path, and the remote-control
   block (`session_url`, `bridge_session_id`). `find_session(call_id)` is
   therefore a read of durable provider state: handle present with
   `native_session_id` → the session exists; handle absent or not yet
   flushed → **unobserved**, exactly G3's None.
3. **A positive outcome record.** `calls/<call_id>/result.json` carries
   `outcome` (`completed` / failure values) and `content`; the coordinator
   already derives completed/failed from it and treats its absence as
   "still running", never as failure (`sync_active_dispatches`). That file
   is G4's provider-side error record and G5's collection surface, next to
   the witness files the call directory accumulates.
4. **The uncertainty rule is already production practice.** On a launch
   timeout the coordinator leaves the reservation for reconciliation with
   the comment "a child may or may not exist … do NOT revert (that risks a
   duplicate)" — G3/G4 as operating discipline, independently arrived at.
5. **An exclusivity primitive.** `runner.lock` inside the call directory
   shows the provider already serializes per-call execution; directory
   creation under `calls/` is the natural atomic claim point on this
   filesystem layout.

## The one gap: G2 is not native today

Today's AutoSwarm **mints its own call id inside `launch`** and returns it
in the response (`.data.call_id`, parsed by `dispatch.py`). The caller
cannot present a key, so two `launch` calls create two calls — the launch
is not idempotent on OUR minted `call_id`, and the correlation becomes
durable provider-side only after the external process has begun.

Two mechanisms close G2; either satisfies the contract, and the bounded
real-provider test proves whichever lands:

- **A1 — adapter-owned idempotency (no provider change).** The adapter
  keeps a durable map `<our call_id> -> <provider call_id>` under its own
  directory, created with `O_EXCL` BEFORE invoking the CLI. A second
  `launch(call_id)` finds the mapping and reads the existing handle instead
  of invoking. A crash between map-create and CLI invoke leaves a mapping
  with no provider call: `find_session` returns None (held, G3), and
  `positive_launch_failure` may use the registered bounded rule (mapping
  exists, no provider call directory, no live runner process) to prove the
  launch never happened.
- **A2 — provider-side key (fork flag, recommended).** Teach the AutoSwarm
  fork (`dimascior/AutoSwarm`) a `--call-id` argument: the CLI creates
  `calls/<given id>/` with exclusive semantics and refuses or adopts an
  existing directory. Idempotency then rides the provider's own atomic
  claim point (the same directory-exclusivity its `runner.lock` already
  relies on), and `find_session` needs no adapter mapping at all.

## G2 closure — A2 executed (2026-11-11)

The bounded real-provider test ran against the installed provider and the
live home, per the operator's directive (Helios gates 20261110-g2-001..008):

- **The A2 mechanism landed** as fork commit
  `2bbaca726a3776e136623310fc436f302968c858` on base `addc189` (clean
  tree): `engine.start_call` accepts a caller-supplied `call_id` (strictly
  `call_` + 32 lowercase hex, the native mint's own shape), adopts an
  existing complete claim by returning `inspect_call` instead of launching,
  arbitrates races at the provider's own atomic claim point (the
  `exist_ok=False` call-directory create), and refuses a claim directory
  without a task record with the distinct `call_claim_incomplete` error —
  "refuses or adopts", exactly. The `run` command exposes it as
  `--call-id`; no behavior changes when the flag is absent (the self-mint
  remains). Fork suite: 209/211 passing — regression-stable against the
  clean baseline, not fully green (the same two pre-existing Kimi
  failures, g2-003 before / g2-004 after); the new
  `test_supplied_call_id_is_exclusive_and_adoptive` covers mint-shape
  refusal, adoption without rewrite, single-record identity, and the
  incomplete-claim refusal. Reinstalled via uv from the clone (g2-005/006)
  and verified live (g2-007).
- **The bounded live run** (`tests/autobot/live_g2.py`, g2-008, one real
  claude-code child on `claude-opus-4-6[1m]` per the operator's standing
  model policy) drove the REAL Sequencer end to end and passed all checks:
  - **T3 (uncertainty honesty)**: before any launch, `find_session` on the
    minted id returned None and `reconcile_dispatched` HELD the attempt in
    DISPATCHED with the journal untouched — absence never became failure
    evidence.
  - **T1 (idempotent launch)**: two real CLI launches with the one
    Sequencer-minted key `call_944b0c33c5975c3b1178c2bd0635c800` produced
    exactly one new call directory and one task record; the second launch
    adopted.
  - **T2 (recovery from durable state)**: `handle.json` alone yielded
    native session `ffda9300-6c41-4507-a6fd-a234db5b2fd3`;
    `reconcile_dispatched` admitted the exact handle bytes as the obj-11
    witness and t-a03 bound the native id; the attempt reached RUNNING,
    then FINISHED on the child's completed result (admitted as evidence),
    and `recover()` passed over the whole live flow.

  Evidence (retained per the operator's auditability requirement): the
  run report with full provenance — fork commit, model, provider home,
  observed outcomes — at
  [evidence/g2-report-20261111.json](evidence/g2-report-20261111.json);
  the provider's own durable records in the live home's
  `calls/call_944b0c33…` directory; the Autobot ledger under the session
  scratchpad; and the gate records above.

  Scope qualifications (the operator's, preserved): the live run proves
  **sequential** adoption — concurrent-launch and crash-interleaving
  cases are not independently established by it and rest on the atomic
  directory claim (the intended mechanism, with the fork's explicit race
  arbiter on `FileExistsError`). `call_claim_incomplete` fails closed by
  design; **recovering** from an incomplete claim is a separate
  availability concern outside this closure.

## Status

| Guarantee | Status |
|---|---|
| G1 | PROVEN in the runtime (t-a02 commits before launch; crash stage `dispatched`) |
| G2 | CLOSED LIVE via A2 (2026-11-11): one minted key, two launches, one session — see the execution record above |
| G3 | PROVEN live: pre-launch None held the attempt; the real session was observed from `handle.json` alone |
| G4 | PROVEN surface (`result.json` outcome); the bounded "can no longer come into existence" rule remains design-stated — t-a10's positive-failure path stays proven by the deterministic arms, not live |
| G5 | PROVEN live: handle and result bytes admitted as obj-11 witnesses byte-identically |

**Conclusion.** The transport contract is now executed over the real
provider: durable pre-launch correlation, launch idempotency on the
Sequencer-minted key, honest observation, and byte-exact evidence
collection are demonstrated live; positive launch-failure determination
(t-a10) remains exercised only by the deterministic arms, since no live
failure was fabricated. The fake transport is thereby representative of
the contract AND of the deployed provider's observed behavior.
