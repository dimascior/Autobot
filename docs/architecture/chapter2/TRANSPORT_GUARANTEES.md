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

## Status

| Guarantee | Status |
|---|---|
| G1 | PROVEN in the runtime (t-a02 commits before launch; crash stage `dispatched`) |
| G2 | NOT native; closed by A1 or A2 above — design named, not yet executed |
| G3 | PROVEN by inspection + the coordinator's production discipline |
| G4 | PROVEN surface (`result.json` outcome); the bounded rule for "can no longer come into existence" is A1/A2-dependent |
| G5 | PROVEN surface (call directory + result content; witness files are producer originals on disk) |

**Conclusion.** The fake transport's contract is implementable over the
real provider: correlation, honest observation and positive determination
exist today as durable provider state; launch idempotency requires the
named A1/A2 mechanism. Until the bounded real-provider test executes one
of them, the fake transport is representative of the CONTRACT, and G2's
real-world closure remains an explicitly open obligation of that test —
nothing in M3's acceptance claims it is already proven live.
