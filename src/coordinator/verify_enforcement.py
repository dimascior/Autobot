"""Report, per route, whether a child's shell calls reach the command gate.

A child reaches the gate one of three ways. This checker reads the real
configuration and says which one applies, so the answer is evidence, not
assumption:

1. codex: does not read ~/.claude/settings.json. ~/.codex/hooks.json wires the
   gate for Codex sessions, including AutoSwarm children.
2. claude-code, workspace with its own `.command-gate`: the wrapper in
   ~/.claude/settings.json emits '{}' and leaves the call to the workspace's own
   hook in `<workspace>/.claude/settings.json`. That hand-off does not mean
   ungated: the child is gated unless the workspace has no such hook.
3. claude-code, no local `.command-gate`: the wrapper runs the global gate named
   in settings.json.

Reaching the gate is not the same as being blocked by it. If a hook exceeds its
timeout it is cancelled, and the outcome then depends on that hook's
fail-open / fail-closed policy, so check that too.

Run:
    coordinator-verify    (or: python -m coordinator.verify_enforcement)
"""

import json
import sys
from pathlib import Path

from coordinator import PROJECT_ROOT, discover
from coordinator.loop import load_config

SETTINGS = Path.home() / ".claude" / "settings.json"

GATED = "GATED"
NOT_GATED = "NOT GATED"
UNKNOWN = "UNKNOWN"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _shell_hook_commands(settings: dict) -> list[str]:
    """Every PreToolUse hook command whose matcher covers Bash and PowerShell."""
    commands = []
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        matcher = entry.get("matcher", "")
        if "Bash" in matcher and "PowerShell" in matcher:
            commands.extend(h.get("command", "") for h in entry.get("hooks", []))
    return commands


def _global_wrapper(settings: dict) -> tuple[bool, bool, str]:
    """Return (hook_present, hands_off_to_local_gate, raw_command)."""
    commands = _shell_hook_commands(settings)
    if not commands:
        return False, False, ""
    raw = commands[0]
    return True, (".command-gate" in raw and "'{}'" in raw), raw


def _workspace_hook_runs_gate(workspace: Path) -> bool:
    """True if the workspace's own Claude settings run a gate hook."""
    for name in ("settings.json", "settings.local.json"):
        settings = _read_json(workspace / ".claude" / name)
        if any(".command-gate" in c for c in _shell_hook_commands(settings)):
            return True
    return False


def _route_verdict(agent: str, workspace, hook_present: bool,
                   hands_off: bool) -> tuple[str, str]:
    if agent == "codex":
        gate = discover.codex_hooks().get("gate")
        if gate:
            return (GATED, f"~/.codex/hooks.json runs the gate at {gate}.")
        return (NOT_GATED, "~/.codex/hooks.json has no PreToolUse/PostToolUse gate, "
                           "so the child's shell calls never reach one.")
    if agent != "claude-code":
        return (UNKNOWN, f"unrecognized agent '{agent}'")
    if not workspace:
        return (UNKNOWN, "the route has no workspace (--cwd) resolved")
    workspace = Path(workspace)
    if hands_off and (workspace / ".command-gate").is_dir():
        if _workspace_hook_runs_gate(workspace):
            return (GATED, "the settings.json wrapper hands off to the workspace's "
                           "own hook in .claude/settings.json, which runs the gate.")
        return (NOT_GATED, "the settings.json wrapper hands off to the workspace, but "
                           "its .claude settings run no gate hook.")
    if hook_present:
        return (GATED, "settings.json runs the global gate for this workspace.")
    return (NOT_GATED, "no PreToolUse Bash|PowerShell hook found in settings.json")


def route_report(cfg: dict, settings: dict) -> list[dict]:
    """One verdict per route, using each route's resolved workspace."""
    hook_present, hands_off, _ = _global_wrapper(settings)
    rows = []
    for name, route in (cfg.get("routes") or {}).items():
        agent = route.get("agent", "")
        workspace = route.get("workspace") or None
        verdict, why = _route_verdict(agent, workspace, hook_present, hands_off)
        rows.append({"name": name, "agent": agent, "workspace": workspace,
                     "verdict": verdict, "why": why})
    return rows


def main() -> int:
    cfg = load_config(PROJECT_ROOT / "config.toml")
    settings = _read_json(SETTINGS)
    hook_present, hands_off, raw = _global_wrapper(settings)

    print("=== command-gate child-path enforcement ===\n")
    print(f"settings.json: {SETTINGS}")
    print(f"  PreToolUse Bash|PowerShell hook present: {hook_present}")
    print(f"  hands off when cwd has .command-gate:    {hands_off}")
    if raw:
        print(f"  wrapper: {raw[:160]}{'...' if len(raw) > 160 else ''}")

    print("\nper-route child governance:")
    rows = route_report(cfg, settings)
    for row in rows:
        print(f"\n  [{row['name']}] agent={row['agent']} workspace={row['workspace']}")
        print(f"      verdict: {row['verdict']}")
        print(f"      reason:  {row['why']}")

    ungoverned = [r["name"] for r in rows if r["verdict"] != GATED]
    print("\n=== conclusion ===")
    if ungoverned:
        print(f"Not confirmed gated: {', '.join(ungoverned)}.")
    else:
        print("Every route's shell calls reach the gate.")
    print("Reaching the gate is not the same as being blocked by it: a hook that "
          "exceeds its timeout\nis cancelled, and its fail-open/closed policy then "
          "decides the outcome. Check that policy.")
    return 1 if ungoverned else 0


if __name__ == "__main__":
    sys.exit(main())
