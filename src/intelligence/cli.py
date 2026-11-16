"""autobot-authority: the production authority-plane entrypoint.

    autobot-authority bootstrap     # validate assembly, print readiness
    autobot-authority status        # current objectives and sequences
    autobot-authority run           # add objective and run to stability

Assembly only — this module wires configuration to the existing proven
components and never introduces new protocol semantics.
"""

import argparse
import json
import sys
from pathlib import Path

from .runtime import AuthorityRuntime, ConfigError


def _project_root():
    here = Path(__file__).resolve()
    cand = here.parents[2]
    if (cand / "config.toml").exists() or (cand / "src" / "autobot").is_dir():
        return cand
    return Path.cwd()


def cmd_bootstrap(args):
    """Construct the full runtime, validate readiness, print summary."""
    try:
        rt = AuthorityRuntime.bootstrap(project_root=args.root)
    except ConfigError as exc:
        print("bootstrap FAILED: %s" % exc, file=sys.stderr)
        return 1
    try:
        print("authority runtime assembled")
        print("  holder:     %s" % rt.sequencer.holder)
        print("  epoch:      %s" % rt.sequencer.epoch)
        print("  repository: %s" % rt.environment.repository_id)
        print("  factory:    %d registrations"
              % len(rt.factory._rows))
        print("  transport:  %s -> %s"
              % (rt.transport.__class__.__name__,
                 rt.transport._home))
        print("  gate_root:  %s" % rt.transport._gate_root)
        st = rt.status()
        print("  objectives: %d active" % len(st["objectives"]))
        print("  sequences:  %d active, %d recently completed"
              % (len(st["active_sequences"]),
                 len(st["recent_completed"])))
        print("bootstrap OK")
    finally:
        rt.close()
    return 0


def cmd_status(args):
    """Print current runtime state."""
    try:
        rt = AuthorityRuntime.bootstrap(project_root=args.root)
    except ConfigError as exc:
        print("cannot assemble runtime: %s" % exc, file=sys.stderr)
        return 1
    try:
        st = rt.status()
        if st["objectives"]:
            print("active objectives:")
            for obj in st["objectives"]:
                print("  %s: %s" % (obj["objective_id"], obj["text"]))
        else:
            print("no active objectives")
        if st["active_sequences"]:
            print("active sequences:")
            for seq in st["active_sequences"]:
                print("  %s  %s  (%s)"
                      % (seq["sequence_id"], seq["state"],
                         seq["contract_digest"][:16]))
        if st["recent_completed"]:
            print("recently completed:")
            for seq in st["recent_completed"]:
                print("  %s  %s" % (seq["sequence_id"], seq["state"]))
    finally:
        rt.close()
    return 0


def cmd_run(args):
    """Add an objective and run the driver to stability."""
    from .chief import ChiefEngineerPolicy

    class OperatorPolicy(ChiefEngineerPolicy):
        """A single-objective policy: emits one INVESTIGATE proposal
        for the operator's objective, then stops proposing."""

        def __init__(self, guidance):
            self.guidance = guidance
            self.proposed = False

        def plan(self, view):
            if self.proposed or not view.objectives:
                return []
            self.proposed = True
            obj = view.objectives[0]
            return [{
                "objective_id": obj["objective_id"],
                "class": "INVESTIGATE",
                "rationale": "operator-directed investigation",
                "requirement": {
                    "objective": obj["text"],
                    "capability": "research",
                    "roles": {"builder": {
                        "required": True,
                        "success_outcomes": ["success"]}},
                    "evidence_requirements": [
                        "COMMAND_GOVERNANCE", "EXECUTION_LIFECYCLE"],
                    "finalization_class": "READ_ONLY",
                    "guidance": self.guidance,
                },
                "snapshot": {"entries": view.snapshot["entries"],
                             "head_hash": view.snapshot["head_hash"]},
                "provenance": {"findings": [], "events": []},
            }]

    if not args.objective:
        print("--objective is required", file=sys.stderr)
        return 1

    try:
        rt = AuthorityRuntime.from_config(project_root=args.root)
    except ConfigError as exc:
        print("cannot assemble runtime: %s" % exc, file=sys.stderr)
        return 1

    try:
        objective_id = args.objective_id or (
            "obj-op-%s" % rt.clock.now().strftime("%Y%m%d%H%M%S"))
        rt.add_objective(objective_id, args.objective)
        print("objective added: %s" % objective_id)

        guidance = args.guidance or args.objective
        policy = OperatorPolicy(guidance)

        if args.dry_run:
            print("dry-run: runtime assembled, objective added,"
                  " not executing")
            st = rt.status()
            print(json.dumps(st, indent=2, default=str))
            return 0

        print("running to stability...")
        result = rt.run_until_stable(policy)
        print("stable after %d steps" % result["steps"])

        st = rt.status()
        if st["recent_completed"]:
            for seq in st["recent_completed"]:
                print("  %s: %s" % (seq["sequence_id"], seq["state"]))

        if result.get("waiting"):
            print("proposals waiting:")
            for p in result["waiting"]:
                print("  %s: %s" % (p["proposal_id"], p["status"]))
    finally:
        rt.close()
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="autobot-authority",
        description="Production authority-plane runtime")
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: auto-detect)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("bootstrap",
                   help="validate assembly and print readiness")
    sub.add_parser("status",
                   help="show current objectives and sequences")

    run_p = sub.add_parser("run", help="add objective and execute")
    run_p.add_argument("--objective", required=True,
                       help="objective text")
    run_p.add_argument("--objective-id", default=None,
                       help="objective id (default: auto-generated)")
    run_p.add_argument("--guidance", default=None,
                       help="specialist guidance (default: objective text)")
    run_p.add_argument("--dry-run", action="store_true",
                       help="assemble and add objective without executing")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "bootstrap": cmd_bootstrap,
        "status": cmd_status,
        "run": cmd_run,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
