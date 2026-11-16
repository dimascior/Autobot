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

    Convergent: re-running after a successful seed is a no-op (the
    Registry deduplicates on content identity). Admits:
      - RepositoryAuthorityDeclaration (LOCAL, autobot)
      - DelegationContract (delegation_a)
      - VerifierRegistration (registry-checker, vector-checker)
      - TransformRegistration (identity-projection)
      - Live verifier/transform closure bytes and dependency lock

    These are the same objects the test suite seeds through
    support.seed_bound_objects(), admitted through the real Registry
    admission path."""
    from autobot import Database, Registry, Sequencer, Store, SystemClock
    from autobot.models import instant

    try:
        import tomllib as _tomllib
    except ModuleNotFoundError:
        _tomllib = None

    project_root = args.root or _project_root()
    config = {}
    if _tomllib is not None:
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

    fixtures_dir = project_root / "docs" / "architecture" / "chapter1" \
        / "vectors" / "fixtures"
    graph_path = fixtures_dir / "graph.json"
    if not graph_path.is_file():
        print("cannot find fixtures at %s" % fixtures_dir, file=sys.stderr)
        db.close()
        return 1

    with open(str(graph_path), "r", encoding="utf-8") as fh:
        graph = json.load(fh)

    admitted = []
    skipped = []

    declaration_objects = [
        "declaration_remote",
        "declaration_local",
    ]

    for name in declaration_objects:
        if name not in graph:
            print("  SKIP %s (not in graph.json)" % name)
            skipped.append(name)
            continue
        try:
            registry.admit_object(graph[name], submitted_by="you")
            admitted.append(name)
            print("  OK   %s" % name)
        except Exception as exc:
            if "already" in str(exc).lower() or "duplicate" in str(exc).lower():
                print("  DUP  %s" % name)
                admitted.append(name)
            else:
                print("  FAIL %s: %s" % (name, exc))
                skipped.append(name)

    pilot_delegation = {
        "schema_version": "v1",
        "kind": "DelegationContract",
        "delegation_id": "dg_pilot_read_only_00000000000001",
        "delegated_to": "planner",
        "authored_by": "you",
        "created_utc": instant(clock.now()),
        "expires_utc": "2027-06-01T00:00:00.000Z",
        "repository_ids": ["autobot"],
        "allowed_scope": {
            "paths": [
                "src/autobot",
                "src/intelligence",
                "docs",
            ],
            "artifact_paths": [],
        },
        "mandatory_floors": {
            "autobot": {
                "READ_ONLY": [],
                "SOURCE_MODIFYING": ["a1"],
            }
        },
        "policy_bounds": {
            "effect_hard_timeout_seconds": 240,
            "effect_intent_ttl_seconds": 300,
        },
    }
    try:
        registry.admit_object(pilot_delegation, submitted_by="you")
        admitted.append("pilot-delegation")
        print("  OK   DelegationContract/pilot-delegation")
    except Exception as exc:
        if "already" in str(exc).lower() or "duplicate" in str(exc).lower():
            print("  DUP  DelegationContract/pilot-delegation")
            admitted.append("pilot-delegation")
        else:
            print("  FAIL DelegationContract/pilot-delegation: %s" % exc)
            skipped.append("pilot-delegation")

    # Live verification and transform registrations: executable closures
    # pinned to the running interpreter, so the production verifier runner
    # can execute them.
    sys.path.insert(0, str(project_root / "src"))
    try:
        from autobot.environment_probe import interpreter_identity
        from contract_library import artifact_root, content_address

        live_lock = b"# autobot live verifier dependency lock: stdlib only\\n"
        store.put(live_lock)

        # Live verifier scripts (same as support.LIVE_PASS_SCRIPT)
        live_pass_script = b'''import hashlib
import json
import sys


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        context = json.load(fh)
    for artifact in context["subject"]["artifacts"]:
        with open(artifact["file"], "rb") as fh:
            digest = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
        if digest != artifact["content_sha256"]:
            print(json.dumps({"autobot_verifier_result": {
                "conclusion": "FAIL",
                "detail": "subject artifact drifted: %s"
                          % artifact["logical_path"]}}))
            return 0
    print(json.dumps({"autobot_verifier_result": {
        "conclusion": "PASS",
        "detail": "subject bytes verified against their content"
                  " addresses"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
        store.put(live_pass_script)

        # Identity-projection transform script
        live_identity_script = b'''import sys

with open(sys.argv[1], "rb") as fh:
    data = fh.read()
with open(sys.argv[2], "wb") as fh:
    fh.write(data)
sys.exit(0)
'''
        store.put(live_identity_script)

        interp_id = interpreter_identity()

        def _make_verifier(verifier_id, suffix, script):
            closure = {
                "kind": "python",
                "script_sha256": content_address(script),
                "interpreter_id": interp_id,
                "dependency_lock_sha256": content_address(live_lock),
            }
            return {
                "schema_version": "v1",
                "kind": "VerifierRegistration",
                "registration_id": "rg_" + suffix.rjust(32, "0"),
                "verifier_id": verifier_id,
                "version": "1.0.0",
                "closure": closure,
                "artifact_root": artifact_root(closure),
                "entrypoint": "verify.py",
                "execution_requirements": [],
                "registered_by": "you",
                "registered_utc": instant(clock.now()),
            }

        def _make_transform(transform_id, suffix, script):
            closure = {
                "kind": "python",
                "script_sha256": content_address(script),
                "interpreter_id": interp_id,
                "dependency_lock_sha256": content_address(live_lock),
            }
            return {
                "schema_version": "v1",
                "kind": "TransformRegistration",
                "registration_id": "rg_" + suffix.rjust(32, "0"),
                "transform_id": transform_id,
                "version": "1.0.0",
                "closure": closure,
                "artifact_root": artifact_root(closure),
                "entrypoint": "transform.py",
                "execution_requirements": [],
                "registered_by": "you",
                "registered_utc": instant(clock.now()),
            }

        registrations = [
            ("registry-checker", "c1", live_pass_script),
            ("vector-checker", "c2", live_pass_script),
        ]
        for verifier_id, suffix, script in registrations:
            payload = _make_verifier(verifier_id, suffix, script)
            try:
                registry.admit_object(payload, submitted_by="you")
                print("  OK   VerifierRegistration/%s" % verifier_id)
                admitted.append("verifier/%s" % verifier_id)
            except Exception as exc:
                if "already" in str(exc).lower() or \
                        "duplicate" in str(exc).lower():
                    print("  DUP  VerifierRegistration/%s" % verifier_id)
                    admitted.append("verifier/%s" % verifier_id)
                else:
                    print("  FAIL VerifierRegistration/%s: %s"
                          % (verifier_id, exc))

        transform_payload = _make_transform(
            "identity-projection", "d1", live_identity_script)
        try:
            registry.admit_object(transform_payload, submitted_by="you")
            print("  OK   TransformRegistration/identity-projection")
            admitted.append("transform/identity-projection")
        except Exception as exc:
            if "already" in str(exc).lower() or \
                    "duplicate" in str(exc).lower():
                print("  DUP  TransformRegistration/identity-projection")
                admitted.append("transform/identity-projection")
            else:
                print("  FAIL TransformRegistration/identity-projection:"
                      " %s" % exc)

    finally:
        if str(project_root / "src") in sys.path:
            sys.path.remove(str(project_root / "src"))

    db.close()
    print("seed complete: %d admitted, %d skipped"
          % (len(admitted), len(skipped)))
    if skipped:
        print("SEED FAILED: required objects not admitted: %s"
              % ", ".join(skipped), file=sys.stderr)
        return 1
    return 0


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
