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


def cmd_seed(args):
    """Admit the canonical production objects through the Registry.

    Deterministic: the same seed version under the same closure bytes
    and interpreter identity always produces the same canonical objects
    and object hashes.  A second seed adds zero new authority objects.

    Admits (pilot-read-only-v1):
      - RepositoryAuthorityDeclaration (LOCAL, autobot)
      - DelegationContract (pilot-read-only)
      - VerifierRegistration (registry-checker, vector-checker)
      - TransformRegistration (identity-projection)
      - Live closure bytes and dependency lock in CAS"""
    from autobot import Database, Registry, Sequencer, Store, SystemClock
    from contract_library import object_hash

    from .production_seed import (
        SEED_VERSION,
        all_seed_objects,
        store_closure_bytes,
    )

    project_root = args.root or _project_root()
    config = _load_seed_config(project_root)
    authority_section = config.get("authority", {})
    data_dir = Path(authority_section.get("data_dir", str(project_root)))
    authority_db_path = data_dir / authority_section.get(
        "authority_db", "autobot.db")
    store_root = data_dir / authority_section.get("store_root", "store")
    holder = authority_section.get("holder", "autobot-authority")

    clock = SystemClock()
    db = Database(authority_db_path)
    store = Store(store_root)
    registry = Registry(db, store, clock)
    sequencer = Sequencer(db, store, clock, holder=holder)
    sequencer.acquire_lease(ttl_seconds=300)

    store_closure_bytes(store)

    print("seed profile: %s" % SEED_VERSION)
    admitted = []
    skipped = []
    seed_objects = all_seed_objects()

    for logical_name, payload in seed_objects:
        obj_hash = object_hash(payload["kind"], payload)
        try:
            registry.admit_object(payload, submitted_by="you")
            admitted.append((logical_name, payload["kind"], obj_hash))
            print("  OK   %-40s %s" % (logical_name, obj_hash[:24]))
        except Exception as exc:
            msg = str(exc).lower()
            if "already" in msg or "duplicate" in msg:
                admitted.append((logical_name, payload["kind"], obj_hash))
                print("  DUP  %-40s %s" % (logical_name, obj_hash[:24]))
            else:
                skipped.append((logical_name, payload["kind"], str(exc)))
                print("  FAIL %-40s %s" % (logical_name, exc))

    db.close()
    print("seed complete: %d admitted, %d skipped"
          % (len(admitted), len(skipped)))
    if skipped:
        print("SEED FAILED: required objects not admitted:",
              file=sys.stderr)
        for name, kind, reason in skipped:
            print("  %s (%s): %s" % (name, kind, reason),
                  file=sys.stderr)
        return 1
    return 0


def _load_seed_config(project_root):
    try:
        import tomllib as _tomllib
    except ModuleNotFoundError:
        try:
            import tomli as _tomllib
        except ModuleNotFoundError:
            _tomllib = None
    if _tomllib is None:
        return {}
    config = {}
    for name in ("config.toml", "config.local.toml"):
        path = project_root / name
        if path.is_file():
            with open(str(path), "rb") as fh:
                local = _tomllib.load(fh)
            for section, values in local.items():
                if isinstance(values, dict):
                    config.setdefault(section, {}).update(values)
                else:
                    config[section] = values
    return config


def cmd_bootstrap(args):
    """Construct the full runtime, validate readiness, print summary."""
    try:
        rt = AuthorityRuntime.bootstrap(project_root=args.root)
    except ConfigError as exc:
        print("bootstrap FAILED: %s" % exc, file=sys.stderr)
        return 1
    try:
        decl = rt.environment.declaration
        deleg = rt.environment.delegation
        assignment = rt.factory.select("research", "builder")
        print("authority runtime assembled")
        print("  holder:      %s" % rt.sequencer.holder)
        print("  epoch:       %s" % rt.sequencer.epoch)
        print("  repository:  %s" % rt.environment.repository_id)
        print("  location:    %s" % decl.get("authority_location"))
        print("  canonical:   %s" % decl.get("canonical_ref"))
        print("  delegation:  %s" % deleg.get("delegation_id"))
        print("  delegated:   %s" % deleg.get("delegated_to"))
        print("  scope:       %s"
              % deleg.get("allowed_scope", {}).get("paths", []))
        print("  created:     %s" % deleg.get("created_utc"))
        print("  expires:     %s" % deleg.get("expires_utc"))
        print("  provider:    %s" % assignment.get("provider"))
        print("  transport:   %s -> %s"
              % (rt.transport.__class__.__name__,
                 rt.transport._home))
        print("  workspace:   %s" % rt.transport._workspace)
        print("  gate_root:   %s" % rt.transport._gate_root)
        print("  verifiers:   %s" % rt.environment.verifier_hashes)
        print("  transforms:  %s" % rt.environment.transform_hashes)
        st = rt.status()
        print("  objectives:  %d active" % len(st["objectives"]))
        print("  sequences:   %d active, %d recently completed"
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
        for the operator's objective, then stops proposing.

        The guidance bytes are content-addressed in the CAS and placed
        explicitly in specification_inputs so that the frozen record's
        role-transform verifier can check:
            prompt == identity-projection(frozen specification input)
            == frozen specification input bytes
        This is the exact identity chain the joined lifecycle proof
        already verified."""

        def __init__(self, guidance, spec_inputs):
            self.guidance = guidance
            self.spec_inputs = spec_inputs
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
                    "specification_inputs": self.spec_inputs,
                },
                "snapshot": {"entries": view.snapshot["entries"],
                             "head_hash": view.snapshot["head_hash"]},
                "provenance": {"findings": [], "events": []},
            }]

    if not args.objective:
        print("--objective is required", file=sys.stderr)
        return 1

    try:
        rt = AuthorityRuntime.bootstrap(project_root=args.root)
    except ConfigError as exc:
        print("bootstrap FAILED: %s" % exc, file=sys.stderr)
        return 1

    try:
        objective_id = args.objective_id or (
            "obj-op-%s" % rt.clock.now().strftime("%Y%m%d%H%M%S"))
        rt.add_objective(objective_id, args.objective)
        print("objective added: %s" % objective_id)

        guidance = args.guidance or args.objective
        prompt_bytes = guidance.encode("utf-8")
        prompt_address = rt.store.put(prompt_bytes)
        spec_inputs = {
            "name_to_exact_byte_sha256": {
                "specialist_prompt": prompt_address,
            }
        }
        print("prompt stored: %s" % prompt_address)
        policy = OperatorPolicy(guidance, spec_inputs)

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

    sub.add_parser("seed",
                   help="admit canonical production objects")
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
        "seed": cmd_seed,
        "bootstrap": cmd_bootstrap,
        "status": cmd_status,
        "run": cmd_run,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
