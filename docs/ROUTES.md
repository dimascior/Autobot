# Routes

A **route** is how the coordinator turns a backlog item into a dispatched agent
run. Running "over multiple repos" means **defining routes and tagging backlog
items with a route** — each route carries its own agent, model and target
working directory.

## Anatomy of a route

```toml
[routes.luna]
agent = "codex"                 # which subscription CLI AutoSwarm invokes
model = "gpt-6-luna"            # the model for that agent
effort = "max"                  # optional
provider_args = ["--sandbox", "danger-full-access", "--ask-for-approval", "never"]
workspace = ""                  # the child's --cwd (a PARAMETER) -- discovered; set only to pin
```

- **`agent`** selects the program and therefore the **required chain** (see below).
- **`model`** / **`effort`** / **`provider_args`** are passed through to `autoswarm run`.
- **`workspace`** is the child's `--cwd`. It is a **per-dispatch parameter, not a system dependency**, and it is **resolved programmatically — you do not hand-set it.** Leave it blank and `discover.py` fills it: a `claude-code` route uses the gate-bearing dir (parent of `gate_root`); a `codex` route uses where AutoSwarm last ran it (the `cwd` of its latest `autoswarm-child` session in `~/.codex/sessions`). An explicit `routes.<name>.workspace` or `<NAME>_WORKSPACE` still wins when you need to pin a specific `--cwd`. An unresolved workspace never stops the coordinator from starting; dispatch just skips that route until it resolves.

Backlog items pick a route with `route = "<name>"` (default `luna`).

## What each route requires (inferred from `agent`)

`discover.py` infers the required programs from each route's `agent` — you don't
maintain a separate list:

| `agent` | Required programs (block `--check` when a route of this agent is enabled) | Governance precondition |
|---|---|---|
| `codex` | `codex` CLI, **codex-handoff**, **Codex Claude DevTools** (`devtoolsDir`) | runs under the gate via `~/.codex/hooks.json`; needs `--sandbox danger-full-access` (or it stalls writing gate files) |
| `claude-code` | `claude` CLI | gated either way: a `workspace` with its own `.command-gate` uses that workspace's hook in `.claude/settings.json` (the `~/.claude/settings.json` wrapper hands off to it), and any other workspace uses the global gate. Run `coordinator-verify` to see which applies |

Accommodations (CODEAPI, hdb/HeliosEvidenceCatalog, Akashic) are reported by
`--check` but never block — see [SYSTEMS.md](SYSTEMS.md).

## Adding a route (pointing the swarm at another repo)

1. Add a `[routes.<name>]` block with its `agent`, `model` and any `provider_args`; leave `workspace` blank.
2. The `workspace` resolves automatically — a `claude-code` route from the gate-bearing dir, a `codex` route from its latest AutoSwarm session's `cwd`. For a brand-new route with no run on record yet, pin the first `--cwd` with `routes.<name>.workspace` (in `config.local.toml`, gitignored) or `<NAME>_WORKSPACE`.
3. Run `coordinator-verify` to confirm the new route's shell calls reach the gate.
4. Run `coordinator-discover --check` — it will add a `route:<agent>` section and tell you if that agent's required chain is missing.
5. Tag backlog items with `route = "<name>"`.

## Why workspaces aren't dependencies

The programs in [SYSTEMS.md](SYSTEMS.md) must *exist and be wired* for a route
to function — they are discovered and checked. A workspace is just *where the
child happens to run*; it is chosen per dispatch and carries no capability of
its own. Conflating the two is the classic mistake: it makes the coordinator
refuse to start over a missing `--cwd` while silently ignoring a missing agent
CLI. `discover.py` keeps them in separate tiers for exactly this reason.
