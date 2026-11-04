"""Verify whether the command-gate actually governs the CHILD execution path.

The coordinator's own `autoswarm run ...` launch may be gated by a PreToolUse
hook, but that does NOT imply the shell commands a child agent runs are gated.
This checker inspects the real configuration and reports, per route, whether the
gate is authoritative over that child's shell calls -- so the answer is evidence,
not assumption.

How it decides:

1. The PreToolUse hook matcher is `Bash|PowerShell`, and the settings.json
   wrapper is of the form:
       if (Test-Path (Join-Path $PWD '.command-gate')) { '{}' }   # local override: defer
       else { & <global-gate-hook> }                              # default route
   A local `.command-gate` in cwd is the override that defers (the child's shell
   runs ungated). With NO local `.command-gate`, the wrapper routes to the global
   gate named in settings.json.

2. Codex does not load Claude Code's ~/.claude/settings.json hooks, so a codex
   child's shell calls never reach the gate -- any compliance is prompt-level
   (voluntary), not enforced.

3. Reaching the gate is not the same as being blocked by it. If the gate's hook
   exceeds its configured timeout it is cancelled, and the outcome then depends
   on your hook's fail-open / fail-closed policy -- verify that policy.

Run:
    uv run --no-project --python 3.12 python verify_enforcement.py
"""

import json
import sys
import tomllib
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"


def _load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"_error": str(exc)}


def _pretooluse_shell_hook(settings: dict):
    """Return (matches_shell, passthrough_on_command_gate, raw_command)."""
    for entry in settings.get("hooks", {}).get("PreToolUse", []):
        matcher = entry.get("matcher", "")
        if "Bash" in matcher and "PowerShell" in matcher:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                passthrough = (".command-gate" in cmd and "'{}'" in cmd)
                return True, passthrough, cmd
    return False, False, ""


def _route_verdict(agent: str, workspace: Path, hook_present: bool,
                   passthrough_on_gate: bool):
    has_gate_dir = (workspace / ".command-gate").is_dir()
    if agent == "codex":
        return ("NOT HOOK-GOVERNED",
                "Codex does not load Claude Code settings.json hooks; the child's "
                "shell calls never reach the gate hook. Gate compliance is "
                "prompt-level only (voluntary).")
    if agent == "claude-code":
        if not hook_present:
            return ("NOT HOOK-GOVERNED",
                    "no PreToolUse Bash|PowerShell hook found in settings.json")
        if passthrough_on_gate and has_gate_dir:
            return ("PASSTHROUGH (ungated)",
                    f"workspace contains .command-gate, so the settings.json wrapper "
                    f"emits '{{}}' and the gate script never runs for this child's "
                    f"shell calls. ({workspace}\\.command-gate present)")
        return ("ROUTED TO GLOBAL GATE (enforced only if the hook completes in time)",
                "no local .command-gate, so by design the wrapper routes this child's "
                "shell calls to the global gate named in settings.json. Reaching the "
                "gate is not the same as being blocked by it: if the gate's hook "
                "exceeds its configured timeout it is cancelled, and your hook's "
                "fail-open/closed policy then decides the outcome. Confirm that policy.")
    return ("UNKNOWN AGENT", f"unrecognized agent '{agent}'")


def main():
    here = Path(__file__).parent
    cfg = tomllib.loads((here / "config.toml").read_text(encoding="utf-8"))
    settings = _load_settings()
    if "_error" in settings:
        print(f"could not read {SETTINGS}: {settings['_error']}")
        return 1

    hook_present, passthrough, raw = _pretooluse_shell_hook(settings)

    print("=== command-gate child-path enforcement ===\n")
    print(f"settings.json: {SETTINGS}")
    print(f"  PreToolUse Bash|PowerShell hook present: {hook_present}")
    print(f"  passthrough when cwd has .command-gate:  {passthrough}")
    if raw:
        print(f"  wrapper: {raw[:160]}{'…' if len(raw) > 160 else ''}")

    # the operator's own launch command (this coordinator dir)
    op_gated = not (here / ".command-gate").is_dir()
    print(f"\ncoordinator launch dir: {here}")
    print(f"  operator's `autoswarm run` launch is itself gated: {op_gated} "
          f"({'no' if op_gated else 'has'} .command-gate in cwd)")

    print("\nper-route child governance:")
    any_governed = False
    for name, rc in cfg.get("routes", {}).items():
        agent = rc.get("agent", "")
        ws = Path(rc.get("workspace", ""))
        verdict, why = _route_verdict(agent, ws, hook_present, passthrough)
        if verdict == "HOOK-GOVERNED":
            any_governed = True
        print(f"\n  [{name}] agent={agent} workspace={ws}")
        print(f"      verdict: {verdict}")
        print(f"      reason:  {why}")

    print("\n=== conclusion ===")
    if any_governed:
        print("At least one route is hook-governed along the child path.")
    else:
        print("Child-path routing is by design: codex children are not hooked "
              "(voluntary), a local .command-gate defers, and a no-local-gate\n"
              "claude-code child is routed to the global gate named in settings.json.\n"
              "Note that reaching the gate is not the same as being blocked by it: if\n"
              "the gate's hook exceeds its configured timeout it is cancelled, and the\n"
              "outcome depends on your hook's fail-open/closed policy. If you need hard\n"
              "enforcement, confirm the hook fails closed on timeout or add an\n"
              "OS/sandbox boundary.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
