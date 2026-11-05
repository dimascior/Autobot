"""discover.py -- resolve the coordinator's runtime dependencies from
authoritative sources, and report readiness PER ENABLED ROUTE.

config.toml ships with NO hardcoded machine paths. For each dependency,
resolution tries, in order:

  1. override       a non-empty value in config.toml ([paths], [systems], or a
                    route's workspace) or a dedicated env var. Explicit wins.
  2. authoritative  the real source of truth already on the machine:
                      - gate_root              <- ~/.claude/settings.json hook
                      - gate / codex_handoff   <- ~/.codex/hooks.json hooks
                      - devtools_dir / codeapi <- codex-handoff's own config.json
                      - autoswarm_home         <- $AUTOSWARM_HOME
                      - the CLIs               <- PATH (shutil.which)
                      - a route's workspace    <- the route agent's last
                                                  autoswarm-child session cwd
  3. probe          a well-known conventional location
                    (%LOCALAPPDATA%\\autoswarm, ~/.codex, ~/.claude)
  4. unresolved     reported with the exact fix to apply

No internal project name is hardcoded here: every system path is read from the
operator's own settings.json, ~/.codex/hooks.json or ~/.codex-handoff/config.json
at runtime, so this file stays safe to publish.

Dependencies are TIERED, and requiredness is PER ROUTE -- a program is required
only when a route that uses it is enabled:

  required       the coordinator cannot run without it (autoswarm, gate_root)
  route:<agent>  required only if a route with that agent is enabled:
                   codex       -> codex_cli + codex_handoff + devtools_dir
                   claude-code -> claude_cli
  accommodation  used by the dispatched child SESSIONS or by the codex chain,
                 not called by the coordinator directly; reported, never blocks
                 (codeapi, evidence_catalog/hdb, akashic, findings_dir)
  param          a per-dispatch parameter -- a child's --cwd; NEVER a dependency.
                 Resolved programmatically, never hand-set (claude-code: the
                 gate-bearing dir, parent of gate_root; codex: where AutoSwarm
                 last ran it -- its latest autoswarm-child session cwd)
  info           informational (the devtool config homes)

The route workspaces are `param`, so an unset workspace never blocks the
coordinator from starting (it only stops that one route from dispatching). The
programs are what the coordinator checks for. Nothing here runs a shell command;
it only reads files, environment and PATH.

    python discover.py --check              # readiness table, grouped by tier/route
    python discover.py --check --no-config  # ignore config.toml -> pure discovery
    python discover.py --json               # resolved dependencies as JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from coordinator import PROJECT_ROOT

try:
    import tomllib  # stdlib on Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

HOME = Path.home()
SETTINGS = HOME / ".claude" / "settings.json"
CODEX_HOOKS = HOME / ".codex" / "hooks.json"
CODEX_SESSIONS = HOME / ".codex" / "sessions"

# Tiers (see module docstring).
REQUIRED = "required"
ROUTE = "route"
ACCOMMODATION = "accommodation"
PARAM = "param"
INFO = "info"

# Which programs each agent's route pulls in (route:<agent> tier).
AGENT_CHAIN = {
    "codex": ["codex_cli", "codex_handoff", "devtools_dir"],
    "claude-code": ["claude_cli"],
}

# The quoted '<drive>:\...\.command-gate\hooks\<something>.ps1' the settings.json
# hook invokes for the GLOBAL gate. The local-override branch uses Join-Path $PWD
# '.command-gate' (no drive, no .ps1) and is ignored.
GATE_PS1_RE = re.compile(
    r"""['"]([A-Za-z]:\\[^'"]*?\\\.command-gate)\\hooks\\[^'"]*\.ps1['"]""",
    re.IGNORECASE,
)
# The quoted path to a "<...>handoff.mjs" in a ~/.codex/hooks.json command string.
HANDOFF_RE = re.compile(r"""['"]([^'"]*handoff\.mjs)['"]""", re.IGNORECASE)


@dataclass
class Resolved:
    key: str
    value: object          # Path for a path, str for a fact (e.g. port band), or None
    source: str            # override | authoritative | probe | default | MISSING
    detail: str
    tier: str = REQUIRED
    agent: str | None = None          # for tier == ROUTE: the agent that needs it
    requires_child: str | None = None  # a sub-path that must exist for `present`

    @property
    def status(self) -> str:
        if self.value is None:
            return "MISSING"
        return {
            "override": "SET",
            "authoritative": "OK",
            "probe": "PROBED",
            "default": "DEFAULT",
        }.get(self.source, self.source)

    @property
    def present(self) -> bool:
        v = self.value
        if v is None:
            return False
        if isinstance(v, Path):
            if not v.exists():
                return False
            return (v / self.requires_child).exists() if self.requires_child else True
        return True  # a non-path fact: having a value is as present as we can check


class DiscoveryError(RuntimeError):
    """Raised by resolve_paths(strict=True) when a blocking dependency is unresolved."""


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return None


def _is_real(v) -> bool:
    """A usable override: a non-empty string that is not a <PLACEHOLDER>."""
    return isinstance(v, str) and bool(v.strip()) and not v.strip().startswith("<")


# ── authoritative source readers (no internal names baked in) ─────────────

def gate_root_from_settings(settings_path: Path = SETTINGS) -> Path | None:
    """The global .command-gate root from the PreToolUse/PostToolUse hook in
    ~/.claude/settings.json -- the gate's own source of truth for its path."""
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    hooks = data.get("hooks", {})
    for phase in ("PreToolUse", "PostToolUse"):
        for entry in hooks.get(phase, []):
            for h in entry.get("hooks", []):
                m = GATE_PS1_RE.search(h.get("command", "") or "")
                if m:
                    return Path(m.group(1))
    return None


def codex_hooks(path: Path = CODEX_HOOKS) -> dict:
    """Read ~/.codex/hooks.json: whether codex is hooked, the codex-handoff
    handoff.mjs path (PreCompact/SessionStart), and the gate it routes to."""
    out = {"present": False, "handoff": None, "gate": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    out["present"] = True
    hooks = data.get("hooks", {})
    for phase in ("PreCompact", "SessionStart"):
        for entry in hooks.get(phase, []):
            for h in entry.get("hooks", []):
                m = HANDOFF_RE.search(h.get("command", "") or "")
                if m:
                    out["handoff"] = Path(m.group(1))
                    break
            if out["handoff"]:
                break
        if out["handoff"]:
            break
    for phase in ("PreToolUse", "PostToolUse"):
        for entry in hooks.get(phase, []):
            for h in entry.get("hooks", []):
                m = GATE_PS1_RE.search(h.get("command", "") or "")
                if m:
                    out["gate"] = Path(m.group(1))
                    break
            if out["gate"]:
                break
        if out["gate"]:
            break
    return out


def handoff_config(env=None) -> tuple[dict, Path]:
    """codex-handoff's own config.json (CODEX_HANDOFF_STATE or ~/.codex-handoff):
    the authoritative source for devtoolsDir / heliosRoot / codeapi on this box."""
    env = env or os.environ
    state = env.get("CODEX_HANDOFF_STATE") or str(HOME / ".codex-handoff")
    p = Path(state) / "config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")), p
    except (OSError, json.JSONDecodeError):
        return {}, p


def latest_autoswarm_workspace(agent: str, sessions_dir: Path = CODEX_SESSIONS,
                               scan_limit: int = 80) -> Path | None:
    """Where `autoswarm run --agent <agent>` last executed, read from the agent's
    own session history -- the authoritative record of the route's --cwd, with no
    workspace name hardcoded or hand-set.

    For a codex route: the `cwd` of the most recent ~/.codex/sessions rollout whose
    session_meta `originator` is "autoswarm-child" (an AutoSwarm dispatch, not an
    ad-hoc `codex_exec` run). Returns None if there is no such record. Read-only and
    stdlib-only: scans plain .jsonl rollouts newest-first (compressed .zst history is
    skipped), reads only each file's first line, and stops at the first match."""
    if agent != "codex" or not sessions_dir.is_dir():
        return None
    try:
        rollouts = sorted(sessions_dir.rglob("rollout-*.jsonl"),
                          key=lambda p: p.name, reverse=True)
    except OSError:
        return None
    for p in rollouts[:scan_limit]:
        try:
            with p.open("r", encoding="utf-8") as fh:
                first = fh.readline(1_000_000)  # session_meta; cap a pathological line
        except OSError:
            continue
        try:
            meta = json.loads(first)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("type") != "session_meta":
            continue
        payload = meta.get("payload") or {}
        if isinstance(payload, dict) and payload.get("originator") == "autoswarm-child":
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                return Path(cwd.strip())
    return None


# ── overrides from an already-loaded config ───────────────────────────────

def _path_overrides(cfg: dict | None) -> dict:
    out: dict[str, str] = {}
    if not cfg:
        return out
    for k, v in (cfg.get("paths") or {}).items():
        if k in ("autoswarm_home", "findings_dir", "gate_root") and _is_real(v):
            out[k] = v.strip()
    for rname, rc in (cfg.get("routes") or {}).items():
        ws = (rc or {}).get("workspace")
        if _is_real(ws):
            out[f"{rname}_workspace"] = ws.strip()
    return out


def _systems_overrides(cfg: dict | None) -> dict:
    sysc = (cfg.get("systems") or {}) if cfg else {}
    return {k: v.strip() for k, v in sysc.items() if _is_real(v)}


def _enabled_agents(cfg: dict | None) -> set:
    if not cfg:
        return set(AGENT_CHAIN)  # pure discovery: show every chain
    return {(rc or {}).get("agent") for rc in (cfg.get("routes") or {}).values() if rc}


# ── resolution ─────────────────────────────────────────────────────────────

def resolve_all(cfg: dict | None = None) -> dict[str, Resolved]:
    """Resolve every dependency, returning {key: Resolved} (never raises)."""
    ov = _path_overrides(cfg)
    sysov = _systems_overrides(cfg)
    hooks = codex_hooks()
    hconf, _ = handoff_config()
    R: dict[str, Resolved] = {}

    # ---- gate_root : settings.json (then ~/.codex/hooks.json) --------------
    v = _env("GATE_ROOT") or ov.get("gate_root")
    if v:
        R["gate_root"] = Resolved("gate_root", Path(v), "override", "env/config", REQUIRED)
    else:
        gr = gate_root_from_settings() or hooks.get("gate")
        src = f"parsed from {SETTINGS}" if gate_root_from_settings() else "parsed from ~/.codex/hooks.json"
        R["gate_root"] = (
            Resolved("gate_root", gr, "authoritative", src, REQUIRED)
            if gr else
            Resolved("gate_root", None, "MISSING",
                     f"no .command-gate *.ps1 path in {SETTINGS} or ~/.codex/hooks.json; "
                     "set GATE_ROOT or paths.gate_root", REQUIRED)
        )

    # ---- autoswarm_home : $AUTOSWARM_HOME, else %LOCALAPPDATA%\autoswarm ----
    env_home = os.environ.get("AUTOSWARM_HOME")
    if env_home and env_home.strip():
        R["autoswarm_home"] = Resolved("autoswarm_home", Path(env_home.strip()),
                                       "authoritative", "env $AUTOSWARM_HOME", REQUIRED)
    elif ov.get("autoswarm_home"):
        R["autoswarm_home"] = Resolved("autoswarm_home", Path(ov["autoswarm_home"]),
                                       "override", "config paths.autoswarm_home", REQUIRED)
    else:
        la = os.environ.get("LOCALAPPDATA") or str(HOME / "AppData" / "Local")
        cand = Path(la) / "autoswarm"
        R["autoswarm_home"] = (
            Resolved("autoswarm_home", cand, "probe",
                     r"%LOCALAPPDATA%\autoswarm (no $AUTOSWARM_HOME set)", REQUIRED)
            if cand.exists() else
            Resolved("autoswarm_home", None, "MISSING",
                     "set AUTOSWARM_HOME (MSIX home-split) or install AutoSwarm", REQUIRED)
        )

    # ---- CLIs on PATH ------------------------------------------------------
    for key, exe, tier, agent in (
        ("autoswarm_cli", "autoswarm", REQUIRED, None),
        ("claude_cli", "claude", ROUTE, "claude-code"),
        ("codex_cli", "codex", ROUTE, "codex"),
    ):
        ov_cli = _env(key.upper())
        if ov_cli:
            R[key] = Resolved(key, Path(ov_cli), "override", "env", tier, agent)
            continue
        found = shutil.which(exe)
        R[key] = (
            Resolved(key, Path(found), "authoritative", "on PATH", tier, agent)
            if found else
            Resolved(key, None, "MISSING",
                     f"'{exe}' not found via PATH (an MSIX app-alias can still run "
                     f"in a shell); set {key.upper()} to pin it", tier, agent)
        )

    # ---- codex chain: codex-handoff (from ~/.codex/hooks.json) -------------
    v = _env("CODEX_HANDOFF_DIR") or sysov.get("codex_handoff_dir")
    if v:
        R["codex_handoff"] = Resolved("codex_handoff", Path(v), "override",
                                      "env/config systems.codex_handoff_dir", ROUTE, "codex")
    elif hooks.get("handoff"):
        R["codex_handoff"] = Resolved("codex_handoff", hooks["handoff"], "authoritative",
                                      "~/.codex/hooks.json PreCompact/SessionStart",
                                      ROUTE, "codex")
    else:
        R["codex_handoff"] = Resolved(
            "codex_handoff", None, "MISSING",
            "no handoff.mjs wired in ~/.codex/hooks.json; install codex-handoff and add its "
            "PreCompact/SessionStart hooks (required for a codex route)", ROUTE, "codex")

    # ---- codex chain: devtools_dir (from codex-handoff's own config/env) ---
    v = (_env("CODEX_HANDOFF_DEVTOOLS") or sysov.get("devtools_dir")
         or (hconf.get("devtoolsDir") if _is_real(hconf.get("devtoolsDir")) else None))
    if v:
        src = ("env CODEX_HANDOFF_DEVTOOLS" if _env("CODEX_HANDOFF_DEVTOOLS")
               else "config systems.devtools_dir" if sysov.get("devtools_dir")
               else "codex-handoff config.json devtoolsDir")
        R["devtools_dir"] = Resolved("devtools_dir", Path(v), "override" if sysov.get("devtools_dir") else "authoritative",
                                     src, ROUTE, "codex", requires_child="scripts/codex-export.ts")
    else:
        R["devtools_dir"] = Resolved(
            "devtools_dir", None, "MISSING",
            "set CODEX_HANDOFF_DEVTOOLS (or devtoolsDir in ~/.codex-handoff/config.json, or "
            "systems.devtools_dir) to the Codex_claude_devtools checkout that holds "
            "scripts/codex-export.ts -- codex-handoff needs it (codex-export.v1)",
            ROUTE, "codex", requires_child="scripts/codex-export.ts")

    # ---- accommodations (reported; the coordinator doesn't call these) -----
    codeapi = hconf.get("codeapi") if isinstance(hconf.get("codeapi"), dict) else {}
    ports = (sysov.get("codeapi_ports")
             or (",".join(map(str, codeapi["ports"])) if isinstance(codeapi.get("ports"), list)
                 else codeapi.get("ports") if isinstance(codeapi.get("ports"), str) else None)
             or "47337-47400")
    enabled = codeapi.get("enabled", True) and str(ports).strip() != "off"
    R["codeapi"] = Resolved(
        "codeapi", f"ports {ports}" if enabled else "off",
        "authoritative" if codeapi else "default",
        "live VS Code terminal routing at codex-handoff inject time (child-side); "
        "scanned by codex-handoff, not by the coordinator", ACCOMMODATION, "codex")

    v = _env("EVIDENCE_CATALOG_DIR", "HDB_ROOT") or sysov.get("evidence_catalog_dir")
    hdb_cli = shutil.which("hdb")
    detail = ("hdb / HeliosEvidenceCatalog -- the digest/evidence DB the child sessions query"
              + (f"; 'hdb' on PATH at {hdb_cli}" if hdb_cli else "; 'hdb' not on PATH"))
    R["evidence_catalog"] = (
        Resolved("evidence_catalog", Path(v), "override", detail, ACCOMMODATION, "codex")
        if v else
        Resolved("evidence_catalog", None, "MISSING",
                 detail + "; set systems.evidence_catalog_dir or EVIDENCE_CATALOG_DIR to report it",
                 ACCOMMODATION, "codex"))

    v = _env("AKASHIC_DIR") or sysov.get("akashic_dir")
    adetail = "Akashic -- installs/validates/rebaselines/restores the Helios runtime (gate prerequisite)"
    R["akashic"] = (
        Resolved("akashic", Path(v), "override", adetail, ACCOMMODATION)
        if v else
        Resolved("akashic", None, "MISSING",
                 adetail + "; set systems.akashic_dir or AKASHIC_DIR to report it",
                 ACCOMMODATION))

    # ---- route workspaces : per-dispatch --cwd (param, never blocks) -------
    # Resolved programmatically, never hand-set: an explicit override wins, else a
    # claude-code route uses the gate-bearing dir (parent of gate_root) and a codex
    # route uses where AutoSwarm last ran it (its latest autoswarm-child session cwd).
    routes = (cfg.get("routes") or {}) if cfg else {"luna": {"agent": "codex"}, "opus": {"agent": "claude-code"}}
    for rname, rc in routes.items():
        agent = (rc or {}).get("agent")
        key = f"{rname}_workspace"
        v = _env(f"{rname.upper()}_WORKSPACE") or ov.get(key)
        if v:
            R[key] = Resolved(key, Path(v), "override", "env/config", PARAM, agent)
            continue
        if agent == "claude-code" and R["gate_root"].value is not None:
            R[key] = Resolved(key, R["gate_root"].value.parent, "authoritative",
                              "parent of gate_root (a gate-bearing workspace)", PARAM, agent)
            continue
        ws = latest_autoswarm_workspace(agent)
        if ws is not None:
            R[key] = Resolved(key, ws, "authoritative",
                              f"cwd of the latest AutoSwarm {agent} session "
                              "(~/.codex/sessions, originator autoswarm-child)",
                              PARAM, agent)
            continue
        R[key] = Resolved(
            key, None, "MISSING",
            f"no override and no AutoSwarm {agent} session on record to infer a --cwd "
            f"from; run the route once so it is recorded, or set routes.{rname}.workspace "
            f"/ {rname.upper()}_WORKSPACE (a per-dispatch --cwd; not needed to start)",
            PARAM, agent)

    # ---- findings_dir : an input the coordinator reads; defaults to a codex
    #      route's resolved workspace (above). Unset -> the sweep ingests nothing.
    v = _env("FINDINGS_DIR") or ov.get("findings_dir")
    if v:
        R["findings_dir"] = Resolved("findings_dir", Path(v), "override", "env/config",
                                     ACCOMMODATION)
    else:
        codex_ws = next(
            (R[f"{rn}_workspace"].value for rn, rc in routes.items()
             if (rc or {}).get("agent") == "codex"
             and f"{rn}_workspace" in R and R[f"{rn}_workspace"].value is not None),
            None)
        R["findings_dir"] = (
            Resolved("findings_dir", codex_ws, "authoritative",
                     "defaults to the codex route's workspace", ACCOMMODATION)
            if codex_ws is not None else
            Resolved("findings_dir", None, "MISSING",
                     "no findings dir set and no codex workspace to default to; set "
                     "FINDINGS_DIR or paths.findings_dir (the sweep ingests nothing "
                     "until then)", ACCOMMODATION))

    # ---- devtool homes (informational) -------------------------------------
    R["claude_home"] = Resolved("claude_home", HOME / ".claude", "authoritative",
                                "~/.claude (holds settings.json)", INFO)
    codex = _env("CODEX_HOME")
    R["codex_home"] = (
        Resolved("codex_home", Path(codex), "override", "env $CODEX_HOME", INFO)
        if codex else
        Resolved("codex_home", HOME / ".codex", "probe", "~/.codex (sessions + hooks.json)", INFO))

    return R


def is_blocking(r: Resolved, enabled_agents: set) -> bool:
    """A dependency blocks readiness when it is required (always, or for an
    enabled route) and unresolved. Accommodations and params never block."""
    if r.tier == REQUIRED:
        return r.value is None
    if r.tier == ROUTE:
        return r.agent in enabled_agents and r.value is None
    return False


def resolve_paths(cfg: dict | None = None, *, strict: bool = True) -> dict:
    """Programmatic entry point. Returns {key: str|None}. With strict=True,
    raises DiscoveryError if any BLOCKING dependency (required, or required for
    an enabled route) is unresolved. Workspaces and accommodations never block."""
    R = resolve_all(cfg)
    if strict:
        enabled = _enabled_agents(cfg)
        blocked = [r for r in R.values() if is_blocking(r, enabled)]
        if blocked:
            lines = "\n".join(f"  - {m.key}: {m.detail}" for m in blocked)
            raise DiscoveryError("unresolved required dependency(ies):\n" + lines)
    return {k: (str(r.value) if r.value is not None else None) for k, r in R.items()}


def apply_to_config(cfg: dict) -> dict:
    """Fill blank machine paths + route workspaces from discovery (explicit
    values win). NEVER raises: an unresolved workspace or input is left blank so
    the coordinator still starts -- `discover.py --check` is the preflight that
    reports what is not ready. Mutates and returns cfg."""
    R = resolve_all(cfg)
    paths = cfg.setdefault("paths", {})
    # The three paths loop.py reads must exist as strings (ingest tolerates a
    # non-existent dir, so "" is safe); fill from discovery where blank.
    for k in ("autoswarm_home", "gate_root", "findings_dir"):
        if not _is_real(paths.get(k)):
            v = R[k].value
            paths[k] = str(v) if v is not None else ""
    # Route workspaces are a per-dispatch --cwd: fill when discovered, else leave
    # blank. Dispatch guards an empty workspace; it never stops the loop starting.
    for rname, rc in (cfg.get("routes") or {}).items():
        key = f"{rname}_workspace"
        if key in R and not _is_real(rc.get("workspace")):
            v = R[key].value
            if v is not None:
                rc["workspace"] = str(v)
    # Record discovered system paths for transparency (loop.py doesn't read these).
    systems = cfg.setdefault("systems", {})
    for k in ("codex_handoff", "devtools_dir", "evidence_catalog", "akashic"):
        if not _is_real(systems.get(k)) and R[k].value is not None:
            systems.setdefault(f"{k}_resolved", str(R[k].value))
    return cfg


# ── config layering (same view loop.load_config sees) ──────────────────────

def _merge(base: dict, overlay: dict) -> dict:
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_sibling_config():
    """config.toml with config.local.toml layered on top (local wins)."""
    if not tomllib:
        return None
    here = PROJECT_ROOT
    cfg = None
    for name in ("config.toml", "config.local.toml"):
        f = here / name
        if not f.exists():
            continue
        try:
            data = tomllib.loads(f.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            continue
        cfg = data if cfg is None else _merge(cfg, data)
    return cfg


# ── --check report ──────────────────────────────────────────────────────────

_TIER_LABEL = {
    REQUIRED: "required (always)",
    ACCOMMODATION: "accommodation (child-side / input; never blocks)",
    PARAM: "route workspace -- a --cwd parameter (never a dependency)",
    INFO: "informational",
}


def _check(use_config: bool) -> int:
    cfg = _load_sibling_config() if use_config else None
    R = resolve_all(cfg)
    enabled = _enabled_agents(cfg)
    src = "config.toml + discovery" if use_config else "pure discovery (--no-config)"
    print(f"discover.py -- dependency readiness [{src}]")
    print(f"settings.json: {SETTINGS}")
    print(f"codex hooks:   {CODEX_HOOKS}")
    print(f"enabled agents (from routes): {', '.join(sorted(a for a in enabled if a)) or '(none)'}\n")

    def show(key):
        r = R[key]
        val = str(r.value) if r.value is not None else "(unresolved)"
        print(f"  {key:<17}{r.status:<8}{val}")
        notes = []
        if r.status == "MISSING":
            notes.append(r.detail)
        elif isinstance(r.value, Path) and not r.present:
            notes.append(f"resolved but NOT PRESENT on disk"
                         + (f" (missing {r.requires_child})" if r.requires_child
                            and r.value.exists() else ""))
        for n in notes:
            print(f"  {'':17}  -> {n}")

    print(f"[{_TIER_LABEL[REQUIRED]}]")
    for k in ("autoswarm_home", "autoswarm_cli", "gate_root"):
        show(k)

    for agent in sorted(a for a in enabled if a in AGENT_CHAIN):
        print(f"\n[route:{agent}  (required because a '{agent}' route is enabled)]")
        for k in AGENT_CHAIN[agent]:
            show(k)
    off = sorted(a for a in AGENT_CHAIN if a not in enabled)
    if off:
        print(f"\n[route chains not enabled: {', '.join(off)}  (their programs are N/A)]")

    print(f"\n[{_TIER_LABEL[ACCOMMODATION]}]")
    for k in ("codeapi", "evidence_catalog", "akashic", "findings_dir"):
        show(k)

    print(f"\n[{_TIER_LABEL[PARAM]}]")
    for k in [k for k in R if k.endswith("_workspace")]:
        show(k)

    print(f"\n[{_TIER_LABEL[INFO]}]")
    for k in ("claude_home", "codex_home"):
        show(k)

    blocked = [r for r in R.values() if is_blocking(r, enabled)]
    absent = [r for r in R.values()
              if not is_blocking(r, enabled) and r.tier in (REQUIRED, ROUTE)
              and r.agent in (None, *enabled) and r.value is not None and not r.present]
    print()
    if blocked:
        print(f"RESULT: {len(blocked)} required dependency(ies) UNRESOLVED for the enabled "
              f"routes -- see '->' notes. Workspaces/accommodations do not block.")
        return 1
    if absent:
        print(f"RESULT: all required dependencies resolved, but {len(absent)} resolved "
              f"path(s) are NOT PRESENT on disk.")
        return 1
    print("RESULT: every required dependency for the enabled routes is resolved and present.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve coordinator dependencies from authoritative sources.")
    ap.add_argument("--check", action="store_true",
                    help="print a per-tier / per-route readiness table (default action)")
    ap.add_argument("--no-config", action="store_true",
                    help="ignore config.toml overrides -> show pure discovery")
    ap.add_argument("--json", action="store_true",
                    help="print resolved dependencies as JSON (non-strict)")
    args = ap.parse_args(argv)
    if args.json:
        cfg = None if args.no_config else _load_sibling_config()
        print(json.dumps(resolve_paths(cfg, strict=False), indent=2))
        return 0
    return _check(use_config=not args.no_config)


if __name__ == "__main__":
    sys.exit(main())
