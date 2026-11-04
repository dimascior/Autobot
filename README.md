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
- **Zero-config paths.** `discover.py` resolves machine paths from authoritative
  sources (the agent's `settings.json`, `$AUTOSWARM_HOME`, `PATH`); only the
  values with no system pointer go in a gitignored `config.local.toml`.
- **Explicit child governance.** When routes run under a PreToolUse command-gate,
  `verify_enforcement.py` reports, per route, whether a child's shell calls are
  actually governed — evidence, not assumption.

## Layout

| File | Role |
|---|---|
| `loop.py` | the cycle + entry point (`--once`, `--dry-run`) |
| `ingest.py` | curated backlog + evidence sweep |
| `ledger.py` / `state.py` | durable state; exclusion, staleness, ranking |
| `planner.py` | picks the batch (rule-based, or a CLI planner) |
| `safety.py` / `authorize.py` | the approval gate; authority-only task closure |
| `dispatch.py` | `autoswarm run` invocations + idempotency / restart safety |
| `resolve.py` | proposes evidence-backed verdicts (never closes a task) |
| `discover.py` | path resolution (`--check` for a found/missing table) |
| `verify_enforcement.py` | per-route child-governance report |
| `config.toml` | committed config (machine paths blank → discovered) |
| `config.local.toml` | **gitignored** machine paths (you create this) |
| `backlog.example.toml` | sample backlog; copy to `backlog.toml` |

## Setup

Requires Python 3.11+, `uv`, the `autoswarm` CLI, and the agent CLIs you route to
(`claude`, `codex`), each logged in to its subscription.

```sh
cp backlog.example.toml backlog.toml       # your dispatchable threads (gitignored)
```

Create `config.local.toml` (gitignored) for the paths with no system pointer —
at minimum the luna workspace; everything else is auto-discovered:

```toml
[paths]
autoswarm_home = '...'   # or rely on $AUTOSWARM_HOME, else %LOCALAPPDATA%\autoswarm
[routes.luna]
workspace = '...'        # the one path discovery cannot find
```

Then confirm resolution and run one dry cycle:

```sh
python discover.py --check
uv run --no-project --python 3.12 python loop.py --once --dry-run
```

`discover.py --check` prints each path as `SET` / `OK` (discovered) / `PROBED` /
`MISSING`; resolve any `MISSING` before running the loop.

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
