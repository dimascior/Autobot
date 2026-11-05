# Architecture

This document maps the whole system an engineer must understand to run the
coordinator — written for someone with **zero prior context** on these pieces.

## The one rule that explains the layout

The coordinator is a **hub**, not a monorepo. Every other program below lives in
its **own separately-versioned repo** (some are load-bearing-live and huge), so
the coordinator **discovers and depends on them at runtime — it never vendors
them.** `discover.py` exists precisely so these stay external and get resolved
from authoritative sources at runtime. The job of this repo is to *name, find,
and check* those programs, not to contain them.

See [SYSTEMS.md](SYSTEMS.md) for each program's repo and setup, and
[ROUTES.md](ROUTES.md) for how a route maps to an agent, a model and a `--cwd`.

## Dependency graph

```
swarm-coordinator (this repo)  ── the hub
└─ AutoSwarm ───────────────────── execution engine (autoswarm run --agent …); $AUTOSWARM_HOME
   ├─ route "opus"  → claude-code child   --cwd <workspace>   (CWD, NOT a dependency)
   │     └─ Bash/PowerShell ─► Helios command-gate
   └─ route "luna"  → codex child         --cwd <workspace>   (CWD, NOT a dependency)
         ├─ Bash/PowerShell ─► Helios command-gate
         ├─ PreCompact / SessionStart ─► codex-handoff
         │       ├─ [required] Codex Claude DevTools  (codex-export.v1 session reader)
         │       ├─ [feature ] CODEAPI           (live terminal endpoint routing)
         │       └─ [feature ] Helios contract   (gate schema / policy / manifest / ledger)
         └─ catalog/evidence queries ─► HeliosEvidenceCatalog  (hdb)

Helios command-gate ──[ installed / validated / rebaselined / restored / audited by ]──► Akashic
```

## Layers

| Layer | Component | Role |
|---|---|---|
| 0 Hub | **swarm-coordinator** | ingest → plan → safety-gate → dispatch; dry-run by default |
| 1 Engine | **AutoSwarm** | `autoswarm run --agent …` spawns one child session per dispatch |
| 2 Agents | **codex** (luna), **claude-code** (opus) | the subscription-CLI agents a route drives |
| 3 Continuity | **codex-handoff** | Codex `PreCompact`/`SessionStart` hooks that hand Luna its state back across compaction |
| 4 Bridges | **Codex Claude DevTools**, **CODEAPI** | session export (`codex-export.v1`) and VS Code HTTP/PTY terminal control |
| 5 Governance | **Helios**, **Akashic**, **hdb/HeliosEvidenceCatalog** | the gate, its installer/validator, and the evidence DB the children query |
| — Reference | **HeliosEntitlementAuthority** | reference implementation + the luna route's `--cwd` (a workspace) |

## Interconnections (each at the layer where it intersects)

1. **coordinator → AutoSwarm** — `dispatch.py` runs `autoswarm run --agent … --cwd <workspace>`; results read from `autoswarm_home/calls/<id>/result.json`. The launch is a plain `subprocess`, so it is itself ungated.
2. **AutoSwarm → agent child** — one child session per dispatch, in the route's `--cwd`.
3. **codex child → Helios gate** — `~/.codex/hooks.json` carries `PreToolUse`/`PostToolUse` on `Bash|PowerShell` → the gate hooks. Same gate the claude children get via `~/.claude/settings.json`. Read/Grep/Glob bypass it.
4. **codex child → codex-handoff** — the same `~/.codex/hooks.json` wires `PreCompact → handoff.mjs snapshot` and `SessionStart(^(compact|resume)$) → handoff.mjs inject`. **Starting a codex session loads the handoff automatically.**
5. **codex-handoff → Codex Claude DevTools** (`lib/export.mjs`, **required**) — shells to `Codex_claude_devtools/scripts/codex-export.ts` via `tsx`, demands schema `codex-export.v1`; it never parses raw rollout JSONL. Config key: `devtoolsDir`.
6. **codex-handoff → CODEAPI** (`lib/codeapi.mjs`) — at inject, scans ports 47337-47400, matches each recorded `codeapiWindowId` to a live `/session`, checks `/term/list`, emits a live routing block; never trusts the recorded port. Config: `codeapi.ports`/`enabled`.
7. **codex-handoff → Helios contract** (`lib/helios.mjs`) — read-only read of the gate `schema` / `policy` / integrity `manifest` and the session ledger to carry the gate's *current* required fields + chain predecessor into the hand-off. Config: `heliosRoot`.
8. **Helios → Akashic** — Akashic installs / activates / validates / rebaselines / restores / audits the Helios runtime (ships `helios-runtime-v4.4.0`). "If you use Helios, Akashic is how it got there and how it's re-verified."
9. **agent children → hdb / HeliosEvidenceCatalog** — the child sessions query the evidence/digest DB via `hdb` (`digestsearch`, `tree`, `status`, …).
10. **coordinator → findings/evidence** — the coordinator *reads* findings files and the gate evidence tree to decide dispatchability; it does not own them.

## Requiredness — the model `discover.py` encodes

Requiredness is **tiered and per route**. A program is required only when a
route that uses it is enabled; the route *workspaces* are never a dependency.

| Tier | Meaning | Members |
|---|---|---|
| `required` | coordinator can't run at all | `autoswarm_home`, `autoswarm_cli`, `gate_root` |
| `route:codex` | required iff a `codex` route is enabled | `codex_cli`, `codex_handoff`, `devtools_dir` |
| `route:claude-code` | required iff a `claude-code` route is enabled | `claude_cli` |
| `accommodation` | used by child sessions / the chain, not called by the coordinator; reported, never blocks | `codeapi`, `evidence_catalog` (hdb), `akashic`, `findings_dir` |
| `param` | a per-dispatch `--cwd`; **never a dependency** | the route workspaces |
| `info` | informational | `claude_home`, `codex_home` |

### Required vs. needed-to-accommodate

codex-handoff's own README rates CODEAPI / Helios / Akashic as *optional* (the
tool degrades without them). This deployment makes them required in practice
because **the gate is always on**: every shell command is gated (so Helios is
present), Helios's runtime is installed/validated by Akashic, and codex sessions
run the handoff (which needs Codex Claude DevTools). Both facts are true at different
scopes; the table above is the coordinator's view.

- **Required programs:** AutoSwarm + the enabled routes' agent CLIs, plus — for a codex route — codex-handoff and Codex Claude DevTools, on the always-on Helios gate.
- **Needed to accommodate (present, not called by the coordinator):** CODEAPI, Akashic, hdb/HeliosEvidenceCatalog.
- **Not a dependency:** the route workspaces — they are the child's `--cwd`.

## Reading `coordinator-discover --check`

`discover.py` resolves every dependency from authoritative sources — no internal
path is hardcoded (run it as `coordinator-discover --check`, or
`python -m coordinator.discover --check`):

- `gate_root` ← `~/.claude/settings.json` (and cross-checked against `~/.codex/hooks.json`)
- `codex_handoff` ← `~/.codex/hooks.json` (`PreCompact`/`SessionStart` command)
- `devtools_dir` ← `$CODEX_HANDOFF_DEVTOOLS` → `~/.codex-handoff/config.json` `devtoolsDir`
- `codeapi` ← `~/.codex-handoff/config.json` `codeapi`
- `autoswarm_home` ← `$AUTOSWARM_HOME` → `%LOCALAPPDATA%\autoswarm`
- the CLIs ← `PATH`

`--check` groups the report by tier, shows a per-route section for each enabled
agent, and exits non-zero only when a **required** dependency (for an enabled
route) is unresolved. Unset workspaces and accommodations are reported but never
fail the check — and `load_config` is likewise non-fatal, so the loop always
starts and `--check` is the single preflight that tells you what is not ready.
