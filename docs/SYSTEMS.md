# Systems the coordinator depends on

The coordinator orchestrates a set of **separate programs**, each in its own
repo or installed product. This repo contains none of them — it discovers them
at runtime (see [ARCHITECTURE.md](ARCHITECTURE.md)). Clone/install and configure
the ones your enabled routes need; `coordinator-discover --check` reports exactly
what is missing.

| Program | What it is | Repo / source | The coordinator's relationship | How `discover.py` finds it |
|---|---|---|---|---|
| **AutoSwarm** | Execution engine; `autoswarm run --agent codex\|claude-code` spawns a child per dispatch | installed product (MSIX); home `$AUTOSWARM_HOME` | **required** (any route) | `$AUTOSWARM_HOME` → `%LOCALAPPDATA%\autoswarm`; CLI via `PATH` |
| **codex CLI** | The agent a `codex` route drives | OpenAI Codex CLI | **required — codex routes** | `PATH` (`codex`) |
| **claude CLI** | The agent a `claude-code` route drives | Anthropic Claude Code | **required — claude-code routes** | `PATH` (`claude`) |
| **codex-handoff** | Codex `PreCompact`/`SessionStart` hooks that restore Luna's state across compaction | standalone repo (MIT); zero npm deps | **required — codex routes** | `~/.codex/hooks.json` (the wired `handoff.mjs`) |
| **Codex Claude DevTools** (repo `Codex_claude_devtools`) | Session reader/visualizer; `scripts/codex-export.ts` → `codex-export.v1` | `github.com/dimascior/Codex_claude_devtools` | **required by codex-handoff** | `$CODEX_HANDOFF_DEVTOOLS` → `~/.codex-handoff/config.json` `devtoolsDir` |
| **CODEAPI** | VS Code extension: HTTP/PTY bridge on loopback (`/session`, `/term/*`, ports 47337-47400) | `github.com/dimascior/CODEAPI` | **accommodation** (child-side terminal routing) | `~/.codex-handoff/config.json` `codeapi` |
| **Helios** (command-gate) | PreToolUse/PostToolUse gate over every `Bash\|PowerShell` command; schema + policy + manifest + ledger + evidence | `github.com/dimascior/Helios-` | **required substrate** (always on) | `gate_root` from `~/.claude/settings.json` |
| **Akashic** | Installs / activates / validates / rebaselines / restores / audits the Helios runtime | `github.com/dimascior/Akashic` | **accommodation** (Helios prerequisite) | `systems.akashic_dir` / `AKASHIC_DIR` (reported only) |
| **hdb / HeliosEvidenceCatalog** | The evidence/digest catalog DB; `hdb read/status/digestsearch/tree …` is its query CLI | own repo | **accommodation** (child-session data source) | `systems.evidence_catalog_dir` / `EVIDENCE_CATALOG_DIR`; `hdb` via `PATH` |
| *(route workspace)* | The child's `--cwd` — a target repo, **not a program** to install | — | **param** (never a dependency) | discovered: claude-code → parent of `gate_root`; codex → latest autoswarm-child session `cwd` (`~/.codex/sessions`) |

## The codex chain, in order

Enabling a `codex` (luna) route pulls in a chain. If you run only a
`claude-code` (opus) route, none of this is required:

```
codex route
  → codex CLI                 (the agent)
  → codex-handoff             (wired in ~/.codex/hooks.json; snapshot/inject)
     → Codex Claude DevTools  (REQUIRED: codex-export.v1 reader)
     → CODEAPI                (feature: live terminal routing)
     → Helios gate contract   (feature: schema/policy/manifest/ledger)
  → hdb / HeliosEvidenceCatalog  (child queries the catalog)
Helios runtime  ← installed/validated by Akashic
```

## Configuring the chain

codex-handoff reads its own config from `~/.codex-handoff/config.json` (or
`$CODEX_HANDOFF_STATE`). The two values with no default are:

- `devtoolsDir` (or `$CODEX_HANDOFF_DEVTOOLS`) → the Codex_claude_devtools checkout holding `scripts/codex-export.ts` (run `pnpm install` there so `tsx` exists).
- `heliosRoot` (or `$CODEX_HANDOFF_HELIOS_ROOT`) → the Helios `.command-gate` directory, if you use a gate.

The coordinator does not set these; it **reads** them for its preflight. To
override discovery from the coordinator side, set the matching key in
`config.toml` `[systems]` (or `config.local.toml`). See each program's own
README for full setup — this repo links out rather than duplicating it.
