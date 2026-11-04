"""discover.py -- resolve the coordinator's machine-specific paths from
authoritative sources, so config.toml can ship with NO hardcoded paths.

For each path the coordinator needs, resolution tries, in order:

  1. override       a non-empty value in config.toml, or a dedicated env var
                    (AUTOSWARM_HOME, GATE_ROOT, OPUS_WORKSPACE, LUNA_WORKSPACE,
                    FINDINGS_DIR, CODEX_HOME). An explicit value always wins.
  2. authoritative  the real source of truth already on the machine:
                      - gate_root / opus workspace  <- ~/.claude/settings.json
                        (the PreToolUse hook command names the global gate)
                      - autoswarm_home              <- $AUTOSWARM_HOME
                      - the CLIs                    <- PATH (shutil.which)
  3. probe          a well-known conventional location
                    (%LOCALAPPDATA%\\autoswarm, %USERPROFILE%\\.codex, ~/.claude)
  4. unresolved     reported as MISSING with the exact fix to apply

Only `luna_workspace` (and `findings_dir`, which defaults to it) has no
authoritative pointer anywhere on the system -- it is the single value an
operator must supply (env var or config.local.toml). Everything else resolves
with zero configuration. The gate path and the opus workspace are READ from the
operator's own settings.json at runtime, so no internal project name is baked
into this file -- it stays safe to publish.

Nothing here runs a shell command; it only reads files, environment and PATH.

    python discover.py --check              # found / derived / missing table
    python discover.py --check --no-config  # ignore config.toml -> pure discovery
    python discover.py --json               # resolved paths as JSON
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

try:
    import tomllib  # stdlib on Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

HOME = Path.home()
SETTINGS = HOME / ".claude" / "settings.json"

# Matches the quoted '<drive>:\...\.command-gate\hooks\<something>.ps1' that the
# settings.json hook command invokes for the GLOBAL gate. The local-override
# branch uses Join-Path $PWD '.command-gate' (no drive, no .ps1) and is ignored.
GATE_PS1_RE = re.compile(
    r"""['"]([A-Za-z]:\\[^'"]*?\\\.command-gate)\\hooks\\[^'"]*\.ps1['"]""",
    re.IGNORECASE,
)


@dataclass
class Resolved:
    key: str
    value: Path | None
    source: str                 # override | authoritative | probe | MISSING
    detail: str
    required: bool = True

    @property
    def status(self) -> str:
        if self.value is None:
            return "MISSING"
        return {
            "override": "SET",
            "authoritative": "OK",
            "probe": "PROBED",
        }.get(self.source, self.source)

    @property
    def present(self) -> bool:
        return self.value is not None and self.value.exists()


class DiscoveryError(RuntimeError):
    """Raised when one or more required paths cannot be resolved."""


def _env(*names: str) -> str | None:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return None


def _is_real(v) -> bool:
    """A usable override: a non-empty string that is not a <PLACEHOLDER>."""
    return isinstance(v, str) and bool(v.strip()) and not v.strip().startswith("<")


def gate_root_from_settings(settings_path: Path = SETTINGS) -> Path | None:
    """Pull the global .command-gate root out of the PreToolUse/PostToolUse hook
    command in settings.json -- the gate's own source of truth for its path."""
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


def _overrides_from_config(cfg: dict | None) -> dict:
    """Explicit, non-placeholder path values from an already-loaded config.
    These win over discovery. Keys: autoswarm_home/findings_dir/gate_root and
    <route>_workspace."""
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


def resolve_all(cfg: dict | None = None) -> dict[str, Resolved]:
    """Resolve every path, returning a dict of Resolved records (never raises)."""
    ov = _overrides_from_config(cfg)
    R: dict[str, Resolved] = {}

    # ---- gate_root : settings.json is authoritative --------------------------
    v = _env("GATE_ROOT") or ov.get("gate_root")
    if v:
        R["gate_root"] = Resolved("gate_root", Path(v), "override", "env/config")
    else:
        gr = gate_root_from_settings()
        R["gate_root"] = (
            Resolved("gate_root", gr, "authoritative",
                     f"parsed from {SETTINGS}")
            if gr else
            Resolved("gate_root", None, "MISSING",
                     f"no .command-gate *.ps1 path in {SETTINGS}; "
                     "set GATE_ROOT or paths.gate_root")
        )

    # ---- opus_workspace : parent of gate_root --------------------------------
    v = _env("OPUS_WORKSPACE") or ov.get("opus_workspace")
    if v:
        R["opus_workspace"] = Resolved("opus_workspace", Path(v), "override", "env/config")
    else:
        gr = R["gate_root"].value
        R["opus_workspace"] = (
            Resolved("opus_workspace", gr.parent, "authoritative",
                     "parent of gate_root")
            if gr else
            Resolved("opus_workspace", None, "MISSING",
                     "derives from gate_root (unresolved); set OPUS_WORKSPACE")
        )

    # ---- autoswarm_home : $AUTOSWARM_HOME, else %LOCALAPPDATA%\autoswarm ------
    env_home = os.environ.get("AUTOSWARM_HOME")
    if env_home and env_home.strip():
        R["autoswarm_home"] = Resolved("autoswarm_home", Path(env_home.strip()),
                                       "authoritative", "env $AUTOSWARM_HOME")
    elif ov.get("autoswarm_home"):
        R["autoswarm_home"] = Resolved("autoswarm_home", Path(ov["autoswarm_home"]),
                                       "override", "config paths.autoswarm_home")
    else:
        la = os.environ.get("LOCALAPPDATA") or str(HOME / "AppData" / "Local")
        cand = Path(la) / "autoswarm"
        R["autoswarm_home"] = (
            Resolved("autoswarm_home", cand, "probe",
                     r"%LOCALAPPDATA%\autoswarm (no $AUTOSWARM_HOME set)")
            if cand.exists() else
            Resolved("autoswarm_home", None, "MISSING",
                     "set AUTOSWARM_HOME (MSIX home-split) or install AutoSwarm")
        )

    # ---- luna_workspace : no authoritative source -- override only -----------
    v = _env("LUNA_WORKSPACE") or ov.get("luna_workspace")
    R["luna_workspace"] = (
        Resolved("luna_workspace", Path(v), "override", "env/config")
        if v else
        Resolved("luna_workspace", None, "MISSING",
                 "no system pointer exists; set LUNA_WORKSPACE or "
                 "routes.luna.workspace (config.local.toml) to Luna's workspace")
    )

    # ---- findings_dir : defaults to luna_workspace ---------------------------
    v = _env("FINDINGS_DIR") or ov.get("findings_dir")
    if v:
        R["findings_dir"] = Resolved("findings_dir", Path(v), "override", "env/config")
    else:
        lw = R["luna_workspace"].value
        R["findings_dir"] = (
            Resolved("findings_dir", lw, "authoritative",
                     "defaults to luna_workspace")
            if lw else
            Resolved("findings_dir", None, "MISSING",
                     "defaults to luna_workspace (unresolved); set FINDINGS_DIR")
        )

    # ---- devtool homes (informational) ---------------------------------------
    R["claude_home"] = Resolved("claude_home", HOME / ".claude", "authoritative",
                                "~/.claude (holds settings.json)", required=False)
    codex = _env("CODEX_HOME")
    if codex:
        R["codex_home"] = Resolved("codex_home", Path(codex), "override",
                                   "env $CODEX_HOME", required=False)
    else:
        cand = HOME / ".codex"
        R["codex_home"] = Resolved("codex_home", cand, "probe", "~/.codex",
                                   required=False)

    # ---- CLIs on PATH (informational; MSIX app-aliases may not resolve) ------
    for key, exe in (("autoswarm_cli", "autoswarm"),
                     ("claude_cli", "claude"),
                     ("codex_cli", "codex")):
        ov_cli = _env(key.upper())
        if ov_cli:
            R[key] = Resolved(key, Path(ov_cli), "override", "env", required=False)
            continue
        found = shutil.which(exe)
        R[key] = (
            Resolved(key, Path(found), "authoritative", "on PATH", required=False)
            if found else
            Resolved(key, None, "MISSING",
                     f"'{exe}' not found via PATH (an MSIX app-alias can still "
                     f"run in a shell); set {key.upper()} to pin it", required=False)
        )

    return R


def resolve_paths(cfg: dict | None = None, *, strict: bool = True) -> dict:
    """Programmatic entry point. Returns {key: str|None}. With strict=True,
    raises DiscoveryError if any REQUIRED path is unresolved."""
    R = resolve_all(cfg)
    if strict:
        missing = [r for r in R.values() if r.required and r.value is None]
        if missing:
            lines = "\n".join(f"  - {m.key}: {m.detail}" for m in missing)
            raise DiscoveryError("could not resolve required path(s):\n" + lines)
    return {k: (str(r.value) if r.value is not None else None) for k, r in R.items()}


def apply_to_config(cfg: dict) -> dict:
    """Fill absent/placeholder paths + route workspaces in an already-loaded
    config from discovery (explicit values win). Mutates and returns cfg.
    Integration hook for loop.load_config -- not wired in by default."""
    R = resolve_all(cfg)
    cfg.setdefault("paths", {})
    for k in ("autoswarm_home", "findings_dir", "gate_root"):
        if not _is_real(cfg["paths"].get(k)):
            r = R[k]
            if r.value is None:
                raise DiscoveryError(f"{k}: {r.detail}")
            cfg["paths"][k] = str(r.value)
    cfg.setdefault("routes", {})
    for rname, rkey in (("luna", "luna_workspace"), ("opus", "opus_workspace")):
        rc = cfg["routes"].setdefault(rname, {})
        if not _is_real(rc.get("workspace")):
            r = R[rkey]
            if r.value is None:
                raise DiscoveryError(f"routes.{rname}.workspace: {r.detail}")
            rc["workspace"] = str(r.value)
    return cfg


def _merge(base: dict, overlay: dict) -> dict:
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _load_sibling_config():
    """config.toml with config.local.toml layered on top (local wins) -- the
    same view loop.load_config sees, so --check reflects real resolution."""
    if not tomllib:
        return None
    here = Path(__file__).parent
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


def _check(use_config: bool) -> int:
    cfg = _load_sibling_config() if use_config else None
    R = resolve_all(cfg)
    order = ["gate_root", "opus_workspace", "autoswarm_home", "luna_workspace",
             "findings_dir", "claude_home", "codex_home",
             "autoswarm_cli", "claude_cli", "codex_cli"]
    src = "config.toml + discovery" if use_config else "pure discovery (--no-config)"
    print(f"discover.py -- path resolution [{src}]")
    print(f"settings.json: {SETTINGS}\n")
    w = max(len(k) for k in order)
    for k in order:
        r = R[k]
        val = str(r.value) if r.value is not None else "(unresolved)"
        info = "" if r.required else " (info)"
        print(f"  {k:<{w}}  {r.status:<7}  {val}{info}")
        notes = []
        if r.status == "MISSING":
            notes.append(r.detail)
        elif r.value is not None and not r.present:
            notes.append("resolved but NOT PRESENT on disk")
        if r.value is not None and r.source == "probe" and k == "autoswarm_home":
            notes.append("probe fallback -- verify dispatched runs land here")
        for n in notes:
            print(f"  {'':<{w}}           -> {n}")
    missing = [r for r in R.values() if r.required and r.value is None]
    absent = [r for r in R.values()
              if r.required and r.value is not None and not r.present]
    print()
    if missing:
        print(f"RESULT: {len(missing)} required path(s) UNRESOLVED -- see '->' notes.")
        return 1
    if absent:
        print(f"RESULT: all resolved, but {len(absent)} required path(s) "
              f"NOT PRESENT on disk.")
        return 1
    print("RESULT: all required paths resolved and present on disk.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Resolve coordinator paths from authoritative sources.")
    ap.add_argument("--check", action="store_true",
                    help="print a found/derived/missing table (default action)")
    ap.add_argument("--no-config", action="store_true",
                    help="ignore config.toml overrides -> show pure discovery")
    ap.add_argument("--json", action="store_true",
                    help="print resolved paths as JSON (non-strict)")
    args = ap.parse_args(argv)
    if args.json:
        cfg = None if args.no_config else _load_sibling_config()
        print(json.dumps(resolve_paths(cfg, strict=False), indent=2))
        return 0
    return _check(use_config=not args.no_config)


if __name__ == "__main__":
    sys.exit(main())
