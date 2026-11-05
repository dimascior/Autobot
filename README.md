# swarm-coordinator

A small coordination layer over **AutoSwarm** that turns a curated backlog into
dispatched agent runs — using the agent CLIs' own subscription login (never API
keys) and running dry by default.

Each cycle: **ingest → reconcile → resolve → state → plan → safety-gate →
dispatch**. It reads a curated backlog plus the evidence the swarm already
produced (prior calls, findings files, the gate tree), decides what is
dispatchable, and launches at most a few runs per cycle through `autoswarm run`.
It never closes a task on its own — completed work parks for a human authority.

## Design principles

- **Subscription-CLI auth, no API keys.** Routes map to a provider CLI via
  `autoswarm run --agent` (e.g. `claude-code`, `codex`), which use the operator's
  own login. No key material anywhere in the repo.
- **Dry-run by default.** `safety.dry_run = true` previews every decision and
  dispatches nothing until you flip it.
- **Approval-gated mutations.** `modify` / `rebaseline` / `reset` / `rewrite`
  tasks park for explicit human approval (`authorize.py`) rather than
  auto-dispatching.
- **Zero-config paths.** `discover.py` resolves every machine path from
  authoritative sources (`settings.json`, `~/.codex/hooks.json`, codex-handoff's
  config, `$AUTOSWARM_HOME`, `PATH`, and `~/.codex/sessions` for a route's `--cwd`),
  tiered per route. Nothing is hardcoded; a gitignored `config.local.toml` is only
  for overrides you choose to pin.
- **Explicit child governance.** When routes run under a PreToolUse command-gate,
  `verify_enforcement.py` reports, per route, whether a child's shell calls are
  actually governed — evidence, not assumption.

## Layout

```text
swarm-coordinator/
├── pyproject.toml            # package + console scripts (coordinator, -discover, -authorize, -verify)
├── config.toml               # committed config (machine paths blank → discovered); [systems] names the chain
├── config.local.toml         # gitignored; OPTIONAL overrides (discovery fills the rest)
├── backlog.example.toml      # sample backlog; copy to backlog.toml
├── docs/                     # ARCHITECTURE.md · SYSTEMS.md · ROUTES.md
├── src/
│   └── coordinator/          # the package
│       ├── loop.py                 # the cycle + entry point (--once, --dry-run)
│       ├── ingest.py               # curated backlog + evidence sweep
│       ├── ledger.py  state.py     # durable state; exclusion, staleness, ranking
│       ├── planner.py              # picks the batch (rule-based, or a CLI planner)
│       ├── safety.py  authorize.py # the approval gate; authority-only task closure
│       ├── dispatch.py             # autoswarm run invocations + idempotency / restart safety
│       ├── resolve.py              # evidence-backed verdicts (never closes a task)
│       ├── discover.py             # dependency resolution (--check = per-tier / per-route readiness)
│       └── verify_enforcement.py   # per-route child-governance report
└── tests/
    └── test_coordinator.py
```

Config and data (`config.toml`, `config.local.toml`, `backlog.toml`,
`coordinator.db`) live at the checkout root; the package resolves them against
that root (`coordinator.PROJECT_ROOT`), so run the commands below from the root.

## Execution flow

One command runs one cycle; the cycle is a fixed pipeline, each stage a module
that reads from and writes to the SQLite ledger (`coordinator.db`).

Entry: `coordinator` (`loop:main`) → `load_config()` (merges `config.toml` +
`config.local.toml`, then `discover.apply_to_config()`) → `run_loop()` →
`one_cycle()`. Each cycle, in order:

1. **`safety.run_safety_checks`** — kill switch (`STOP` file), concurrent cap,
   cycles-per-hour and daily ceiling. Returns the free `slots`; raises
   `SafetyViolation` to halt the cycle.
2. **`ingest.ingest`** — sweeps the sources into the ledger: `backlog.toml` (the
   *only* dispatchable `task` items), `$AUTOSWARM_HOME/calls` (executions),
   `findings_dir` (findings), and the gate tree's `pending|inflight|blocked`
   (gates). Malformed JSON is quarantined, never dropped.
3. **`dispatch.sync_active_dispatches`** — reads `calls/<id>/result.json` for
   in-flight dispatches; a completed run parks its task at `awaiting_resolution`
   (never auto-completed), a failed one at `failed`.
4. **`resolve.evaluate_awaiting`** — for each `awaiting_resolution` item, compares
   the task's acceptance criteria against the execution artifacts and
   authoritative evidence and records a *proposed* verdict (`resolved` / `rework`
   / `review`) with evidence hashes. It proposes only; it never closes a task.
5. **`state.compute_state`** — applies staleness (an idle `in_progress` dispatch
   → `stale`) and blocking (`open` ↔ `blocked` by predecessor completion), then
   ranks the dispatchable tasks into a summary.
6. **`planner.plan`** — drafts the batch (`[{id, model, prompt}]`) from the ranked
   tasks via the `claude` CLI (subscription login) or the rule-based fallback,
   capped at `available_slots`.
7. **`safety.gate_batch`** — parks approval-class task classes
   (`modify` / `rebaseline` / `reset` / `rewrite`) into the approval queue; the
   rest pass through as `auto`.
8. **`dispatch.dispatch_batch` → `dispatch_item`** — per auto item: cooldown,
   open-dispatch and workspace guards, then (unless `dry_run`) reserve a dispatch
   row *before* launching (restart safety) and run
   `autoswarm run --agent <agent> --model <model> --cwd <workspace> …`. Dry-run
   logs the intent and launches nothing.
9. **`ledger.complete_cycle`** — records the cycle counts; the summary is printed.

Task **closure is deliberately out of band**: no cycle stage ever marks a task
`completed`. Only `authorize.py` (`coordinator-authorize`), driven by a named
human authority, enacts a proposed resolution — completing the task, or spawning
an authorized rework task and superseding the original.

## Entry points

`pyproject.toml` installs four console scripts (each also runnable as
`python -m coordinator.<module>`):

| Command | Module | Purpose |
|---|---|---|
| `coordinator` | `loop:main` | run the cycle — `--once`, `--dry-run`, `--config` |
| `coordinator-discover` | `discover:main` | dependency readiness — `--check`, `--json`, `--no-config` |
| `coordinator-authorize` | `authorize:main` | authority closure — `--list`, `--resolution N --authority <id> --approve/--reject` |
| `coordinator-verify` | `verify_enforcement:main` | per-route child-governance report |

## Module reference

Every module reads from and/or writes to the ledger; this gives each one's public
entry point, what it consumes, and what it produces (its endpoint).

| Module | Entry point | Consumes | Produces (endpoint) |
|---|---|---|---|
| `loop.py` | `main()`; `one_cycle()` | `config.toml` (+ `.local`), the ledger | orchestrates the pipeline; `coordinator.db`; printed summary |
| `discover.py` | `main()`; `apply_to_config()` / `resolve_all()` (lib) | `settings.json`, `~/.codex/hooks.json`, codex-handoff config, `$AUTOSWARM_HOME`, `PATH`, `~/.codex/sessions` | resolved-paths dict / readiness table; fills `cfg` paths |
| `ingest.py` | `ingest(ledger, …)` | `backlog.toml`, `calls/`, `findings_dir`, gate tree | ledger items (`task`/`execution`/`finding`/`gate`), quarantine rows |
| `state.py` | `compute_state(ledger, …)` | ledger items + dispatches | status transitions; ranked state summary |
| `planner.py` | `plan(ledger, state, …)` | state summary; `claude` CLI | a dispatch batch `[{id, model, prompt}]` |
| `safety.py` | `run_safety_checks()`, `gate_batch()` | caps, `STOP` file, `task_class` | free slots; `approval_queue` rows; raises `SafetyViolation` |
| `dispatch.py` | `dispatch_batch()` / `dispatch_item()`, `sync_active_dispatches()` | batch, `routes`, `calls/<id>/result.json` | `autoswarm run` child launches; `dispatches` rows; result sync |
| `resolve.py` | `evaluate_awaiting(ledger, …)` | `awaiting_resolution` items, findings | *proposed* `resolutions` (never closes a task) |
| `authorize.py` | `main()` | proposed `resolutions` | **authorized** closure → task `completed` / rework task |
| `ledger.py` | `Ledger(db_path)` | — | `coordinator.db`: items, dependencies, dispatches, cycles, resolutions, approvals, quarantine, audit |
| `verify_enforcement.py` | `main()` | `settings.json`, `routes` | per-route child-governance verdict (stdout) |
| `__init__.py` | — | — | `PROJECT_ROOT` (the checkout root used for config/data) |

## Systems it depends on

The coordinator is a hub: it **discovers and depends on** several external
programs at runtime and **vendors none** of them. What's required depends on
which routes you enable — a `claude-code` route needs little, a `codex` route
pulls in a chain:

| Program | Role | Required when |
|---|---|---|
| AutoSwarm | execution engine (`autoswarm run`) | always |
| `claude` / `codex` CLI | the route's agent | that route is enabled |
| codex-handoff | Codex compaction continuity (hooks) | a codex route is enabled |
| Codex Claude DevTools | `codex-export.v1` session reader | a codex route is enabled |
| Helios command-gate | gates every shell command | always (gate is on) |
| CODEAPI · Akashic · hdb/HeliosEvidenceCatalog | terminal routing · gate installer · evidence DB | accommodations (reported, never block) |

Route **workspaces are a child's `--cwd`, not a dependency** — resolved
programmatically (claude-code → parent of `gate_root`; codex → the latest
autoswarm session's `cwd`), never hand-set; an unresolved one never blocks
startup. Run `coordinator-discover --check` for a per-tier,
per-route readiness table. Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/SYSTEMS.md](docs/SYSTEMS.md), [docs/ROUTES.md](docs/ROUTES.md).

## Setup

Requires Python 3.11+, `uv`, the `autoswarm` CLI, and the agent CLIs you route to
(`claude`, `codex`), each logged in to its subscription.

```sh
pip install -e .                           # installs the `coordinator` package + its CLIs
cp backlog.example.toml backlog.toml       # your dispatchable threads (gitignored)
```

Everything is auto-discovered, so `config.local.toml` is **optional** — create it
(gitignored) only to override what discovery gets wrong or to pin a value:

```toml
[paths]
autoswarm_home = '...'   # only if $AUTOSWARM_HOME is unset and the probe misses
[routes.luna]
workspace = '...'        # only to pin a --cwd; else resolved from the latest
                         # autoswarm codex session
```

Then confirm resolution and run one dry cycle:

```sh
coordinator-discover --check               # or: python -m coordinator.discover --check
coordinator --once --dry-run               # or: python -m coordinator.loop --once --dry-run
```

`coordinator-discover --check` prints each dependency as `SET` / `OK` / `PROBED` /
`MISSING`, grouped by tier and by enabled route; it fails only on a **required**
dependency for an enabled route (workspaces and accommodations are reported but
never block). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Safety

Dispatch is off until `safety.dry_run = false`. Even then, caps
(`max_concurrent`, `max_dispatches_per_cycle`, `max_cycles_per_hour`,
`daily_ceiling`), cooldowns, a `STOP` file, and the approval gate bound what a
cycle can do. Mutating task classes never auto-dispatch — they wait for
`authorize.py`.

If your routes run under a command-gate, run `verify_enforcement.py` to confirm,
per route, whether child shell calls are actually governed (and verify your
hook's fail-open / fail-closed policy for the no-local-gate case).

## License

MIT — see [LICENSE](LICENSE).
