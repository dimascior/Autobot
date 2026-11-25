"""autobot-authority: the production entrypoint.

    autobot-authority seed          # admit the canonical authority objects
    autobot-authority bootstrap     # validate assembly, print readiness
    autobot-authority reset         # full reset: wipe all state, re-seed,
                                    # validate (Option 3)
    autobot-authority status        # objectives, sequences, projects,
                                    # Taskforce
    autobot-authority engineer      # give the Engineer an objective and
                                    # run its project until the loop is
                                    # stably quiet
    autobot-authority run           # one operator-directed task (no
                                    # reasoning: you name the task)
    autobot-authority objectives    # list objectives, or close one
    autobot-authority taskforce     # the persistent organization: members,
                                    # seats, outside specialists, history
    autobot-authority coordinator   # admission decisions and elevation
    autobot-authority orchestrator  # runs, nodes (DAG), edges, reasoning
    autobot-authority engineer-report  # cycles, proposals, budgets
    autobot-authority query         # the engineering query layer
                                    # (projects, teams, tasks, seats, ...)

Assembly only — this module wires configuration to the existing
components and never introduces new protocol semantics.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from . import query
from .planstore import PlanStore, readonly_view
from .prompts import SOURCE_MODIFYING_CONTRACT
from .runtime import AuthorityRuntime, ConfigError

EVIDENCE_PROFILE_CHOICES = ["READ_ONLY_REPORT", "READ_ONLY_GOVERNED",
                            "SOURCE_MODIFYING_GOVERNED", "BY_CLASS"]

BUDGET_KEYS = ("max_contracts", "max_attempts", "max_outside_specialists",
               "max_repairs", "max_retention_decisions")


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


def cmd_reset(args):
    """Full organizational reset: delete all databases and the content
    store, then re-seed the authority trust chain from scratch.

    Phase 1 (tear down):
      - Cancel in-flight AutoSwarm sessions
      - Delete intelligence.db (planning: objectives, proposals,
        projects, orchestration, Taskforce membership, findings)
      - Delete autobot.db (authority: sequences, admissions,
        transitions, witnesses, permits, registered objects)
      - Delete coordinator.db (outer coordinator ledger)
      - Delete the content store (CAS)

    Phase 2 (rebuild):
      - Re-seed: admit declaration, delegation, verifiers, transforms,
        closure bytes (production_seed.all_seed_objects)
      - Validate: bootstrap the full runtime and check readiness

    The result is a system ready for ``run --coordinator --orchestrator
    --objective "..."`` with zero historical context."""
    import shutil

    if not args.confirm:
        print("reset requires --confirm (this deletes all databases"
              " and the content store)", file=sys.stderr)
        return 1

    project_root = args.root or _project_root()
    config = _load_seed_config(project_root)
    authority_section = config.get("authority", {})
    paths_section = config.get("paths", {})
    data_dir = Path(authority_section.get("data_dir", str(project_root)))

    authority_db = data_dir / authority_section.get(
        "authority_db", "autobot.db")
    intelligence_db = data_dir / authority_section.get(
        "intelligence_db", "intelligence.db")
    store_root = data_dir / authority_section.get("store_root", "store")
    coordinator_db = data_dir / paths_section.get(
        "ledger_db", "coordinator.db")

    # --- Phase 1: cancel in-flight sessions ---
    print("phase 1: tear down")
    cancelled = 0
    if authority_db.is_file():
        try:
            from autobot import Database
            db = Database(authority_db)
            rows = db.conn.execute(
                "SELECT ac.call_id FROM attempt_calls ac"
                " JOIN attempts a ON a.attempt_id = ac.attempt_id"
                " WHERE a.state IN ('DISPATCHED', 'RUNNING')"
            ).fetchall()
            db.close()
            if rows:
                autoswarm_home = paths_section.get("autoswarm_home", "")
                if not autoswarm_home:
                    autoswarm_home = os.environ.get("AUTOSWARM_HOME", "")
                if autoswarm_home:
                    import subprocess
                    for row in rows:
                        call_id = row["call_id"]
                        try:
                            subprocess.run(
                                [sys.executable, "-m", "autoswarm",
                                 "cancel", call_id],
                                capture_output=True, text=True,
                                env={**os.environ,
                                     "AUTOSWARM_HOME": autoswarm_home},
                                timeout=30)
                            print("  cancelled session %s" % call_id)
                            cancelled += 1
                        except Exception as exc:
                            print("  cancel %s failed: %s" % (
                                call_id, exc))
                else:
                    print("  %d in-flight sessions (no AUTOSWARM_HOME"
                          " to cancel them)" % len(rows))
        except Exception as exc:
            print("  could not read in-flight sessions: %s" % exc)
    print("  cancelled %d sessions" % cancelled)

    # --- Phase 1: delete databases and store ---
    deleted = []
    failed = []
    for db_path in (intelligence_db, authority_db, coordinator_db):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.is_file():
                try:
                    p.unlink()
                    deleted.append(p.name)
                except PermissionError:
                    failed.append(p.name)
    if store_root.is_dir():
        shutil.rmtree(str(store_root))
        deleted.append("store/")
    if deleted:
        print("  deleted: %s" % ", ".join(deleted))
    if failed:
        print("  LOCKED (close the dashboard or driver first): %s"
              % ", ".join(failed), file=sys.stderr)
        return 1
    if not deleted and not failed:
        print("  nothing to delete")

    # --- Phase 2: re-seed ---
    print("\nphase 2: rebuild")
    print("  seeding authority objects...")
    rc = cmd_seed(args)
    if rc != 0:
        print("RESET FAILED: seed did not complete", file=sys.stderr)
        return rc

    # --- Phase 2: bootstrap validation ---
    print("  validating assembly...")
    rc = cmd_bootstrap(args)
    if rc != 0:
        print("RESET WARNING: bootstrap validation failed"
              " (seed succeeded; check config)", file=sys.stderr)
        return rc

    print("\nreset complete: all state cleared, trust chain rebuilt")
    print("next: autobot-authority run --coordinator --orchestrator"
          " --objective \"...\"")
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


def _data_paths(args):
    project_root = args.root or _project_root()
    config = _load_seed_config(project_root)
    authority_section = config.get("authority", {})
    data_dir = Path(authority_section.get("data_dir", str(project_root)))
    return (data_dir / authority_section.get("authority_db", "autobot.db"),
            data_dir / authority_section.get("intelligence_db",
                                             "intelligence.db"))


def _open_read_only(args):
    """The plan store and a READ-ONLY authority connection, for the
    reporting commands: they take no lease and start no driver, so they
    are safe beside a running organization."""
    authority_db, intelligence_db = _data_paths(args)
    if not authority_db.is_file():
        raise ConfigError("no authority database at %s" % authority_db)
    return PlanStore(intelligence_db), readonly_view(authority_db)


def _evidence_store(args):
    """The content store as a read source for the reporting commands (a
    failed attempt then shows what the provider itself reported), or None
    when there is none yet."""
    from autobot import Store
    project_root = args.root or _project_root()
    config = _load_seed_config(project_root)
    authority_section = config.get("authority", {})
    data_dir = Path(authority_section.get("data_dir", str(project_root)))
    store_root = data_dir / authority_section.get("store_root", "store")
    if not (store_root / "objects").is_dir():
        return None
    return Store(store_root)


def _taskforce_lines(report):
    """The persistent organization, in a few lines."""
    members = [m for m in report["members"] if m["status"] == "ACTIVE"]
    lines = []
    lines.append("Taskforce: %d member(s) holding %d retained seat(s)"
                 " (%d established), %d inactive or released seat(s);"
                 " %d outside specialist(s), %d with verified work"
                 " awaiting a retention decision"
                 % (len(members), len(report["retained_members"]),
                    len(report["established_seats"]),
                    len(report["inactive_seats"]),
                    len(report["outside_specialists"]),
                    len(report["retention_candidates"])))
    for seat in report["retained_members"]:
        lines.append(
            "  %-11s %s  %s/%s  specialist %s (%s)  %d qualifying"
            % (seat["status"], seat["seat_id"],
               seat["capability"], seat["role"], seat["specialist_id"],
               seat["handle"], seat["performance"]["qualifying"]))
    for seat in report["inactive_seats"]:
        lines.append(
            "  %-11s %s  %s/%s  specialist %s (%s)"
            % (seat["status"], seat["seat_id"],
               seat["capability"], seat["role"], seat["specialist_id"],
               seat["handle"]))
    for candidate in report["retention_candidates"]:
        lines.append(
            "  OUTSIDE     %s (%s)  %s/%s  %d qualifying demonstration(s),"
            " no retention decision yet"
            % (candidate["specialist_id"], candidate["handle"],
               candidate["capability"], candidate["role"],
               len(candidate["qualifying_demonstrations"])))
    return lines


def _project_lines(projects):
    """The temporary side: each project, its workstreams and its team."""
    lines = []
    for project in projects:
        if project is None:
            continue
        lines.append("Project %s [%s] for %s: %s"
                     % (project["project_id"], project["status"],
                        project["objective_id"], project["title"][:100]))
        for workstream in project["workstreams"]:
            staffed = ", ".join(
                "%s as %s by %s" % (a["specialist_id"][:12],
                                    a["selection_path"], a["decided_by"])
                for a in workstream["assignments"]) or "unstaffed"
            lines.append("  workstream %-22s %-22s %s/%s  %s"
                         % (workstream["workstream"],
                            workstream["state"], workstream["capability"],
                            workstream["role"], staffed))
            for failed in workstream.get("failed_attempts", []):
                reported = failed.get("provider_report") or {}
                lines.append("    attempt %s %s%s"
                             % (failed["attempt_id"], failed["state"],
                                ": the provider reported %r"
                                % reported["error"][:160]
                                if reported.get("error") else ""))
        for member in project["team"]:
            lines.append(
                "  team %s (%s): joined as %s, participation %s%s;"
                " Taskforce member now: %s"
                % (member["specialist_id"], member["handle"],
                   member["joined_as"], member["participation"],
                   " (%s)" % member["end_reason"]
                   if member["end_reason"] else "",
                   "yes" if member["taskforce_member"] else "no"))
    return lines


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
        print("  transport:   %s -> %s"
              % (rt.transport.__class__.__name__,
                 rt.transport._home))
        print("  workspace:   %s" % rt.transport._workspace)
        print("  gate_root:   %s" % rt.transport._gate_root)
        print("  verifiers:   %s" % rt.environment.verifier_hashes)
        print("  transforms:  %s" % rt.environment.transform_hashes)
        print("  engineer:    model %s" % rt.engineer_model)
        print("  specialists: %d in the catalog"
              % len(rt.pool.profiles()))
        routing = rt.pool.routing
        if routing is not None:
            print("  routing:     default %s, escalation %s, fallback %s,"
                  " utility %s"
                  % (routing.default, routing.escalation,
                     routing.fallback or "none", routing.utility))
        for profile in rt.pool.profiles():
            print("    %-16s %s/%s%s%s  offers %s"
                  % (profile["handle"], profile["provider"],
                     profile["model"],
                     " effort %s" % profile["effort"]
                     if profile["effort"] else "",
                     " (route %s)" % profile["route_name"]
                     if profile.get("route_name") else "",
                     ", ".join("%s:%s" % tuple(o)
                               for o in profile["offers"])))
        st = rt.status()
        print("  objectives:  %d active" % len(st["objectives"]))
        print("  sequences:   %d active, %d recently completed"
              % (len(st["active_sequences"]),
                 len(st["recent_completed"])))
        print("bootstrap OK")
    finally:
        rt.close()
    return 0


def _route_text(route):
    if not route:
        return "none"
    return "%s  provider=%s model=%s effort=%s" % (
        route.get("name") or "-", route.get("provider"),
        route.get("model"), route.get("effort"))


def cmd_preflight(args):
    """Resolve every execution route production will use and, with
    --probe, exercise each with one small real session. Creates no
    objective, no contract and no attempt."""
    try:
        rt = AuthorityRuntime.bootstrap(project_root=args.root)
    except ConfigError as exc:
        print("preflight FAILED: %s" % exc, file=sys.stderr)
        return 1
    try:
        def progress(text):
            print("  " + text)
            sys.stdout.flush()
        if args.probe:
            print("probing the routes with real sessions...")
        report = rt.preflight(probe=args.probe,
                              timeout_seconds=args.timeout,
                              on_event=progress)
        print("engineer:      provider=claude-code model=%s (Engineer and"
              " Project Manager)" % report["engineer"]["model"])
        policy = report["policy"]
        if policy is None:
            print("routing:       no [routing] policy; every specialist"
                  " carries its own route")
        else:
            for route_class in ("default", "escalation", "utility"):
                print("%-14s %s" % (
                    route_class + ":",
                    _route_text(report["resolved"].get(route_class))))
            print("fallback:      %s" % (", ".join(policy["fallback"])
                                         or "none"))
            print("escalate:      %s" % (", ".join(policy["escalate"])
                                         or "nothing"))
            for route in policy["routes"]:
                print("  route %-14s provider=%s model=%s effort=%s"
                      " enabled=%s serves %s"
                      % (route["name"], route["provider"], route["model"],
                         route["effort"], route["enabled"],
                         ", ".join(route["classes"]) or "nothing"))
        print("specialists:")
        for specialist in report["specialists"]:
            print("  %-16s %s  route=%s provider=%s model=%s effort=%s"
                  % (specialist["handle"], specialist["specialist_id"],
                     specialist["route"] or "(its own)",
                     specialist["provider"], specialist["model"],
                     specialist["effort"]))
        if report["providers"] is not None:
            print("providers:     %s" % ", ".join(
                "%s=%s" % (name, "available" if up else "UNAVAILABLE")
                for name, up in sorted(report["providers"].items())
                if up or name in {s["provider"]
                                  for s in report["specialists"]}))
        for record in report.get("probes", []):
            print("probe %-22s %s  %ss  call=%s session=%s launched_with=%s"
                  % (record["label"], "OK" if record["ok"] else "FAILED",
                     record.get("seconds"), record.get("call_id"),
                     record.get("native_session_id"),
                     record.get("launched_with")
                     or record.get("served_by")))
        if "independent_sessions" in report:
            print("independent sessions: %s"
                  % report["independent_sessions"])
        for problem in report["problems"]:
            print("PROBLEM: %s" % problem)
        if args.report:
            with open(str(args.report), "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=1, sort_keys=True, default=str)
            print("report: %s" % args.report)
        print("preflight %s" % ("OK" if report["ok"] else "FAILED"))
        return 0 if report["ok"] else 1
    finally:
        rt.close()


def cmd_status(args):
    """Print current state without taking the lease."""
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        objectives = plan.active_objectives()
        if objectives:
            print("active objectives:")
            for obj in objectives:
                print("  %s: %s" % (obj["objective_id"], obj["text"]))
        else:
            print("no active objectives")
        active = [dict(r) for r in ro.execute(
            "SELECT sequence_id, state, contract_digest FROM sequences"
            " WHERE state NOT IN ('COMPLETED','ABANDONED','REJECTED')"
            " ORDER BY created_utc").fetchall()]
        if active:
            print("active sequences:")
            for seq in active:
                print("  %s  %s  (%s)"
                      % (seq["sequence_id"], seq["state"],
                         seq["contract_digest"][:16]))
        completed = ro.execute(
            "SELECT COUNT(*) AS n FROM sequences WHERE state ="
            " 'COMPLETED'").fetchone()["n"]
        print("completed sequences: %d" % completed)
        active_projects = [
            p for p in query.projects_report(plan, ro,
                                             _evidence_store(args))
            if p["status"] == "ACTIVE"]
        for line in _project_lines(active_projects):
            print(line)
        for line in _taskforce_lines(query.taskforce_report(plan)):
            print(line)
    finally:
        ro.close()
        plan.close()
    return 0


def _progress(report):
    """One line per step that did something."""
    if report["quiet"]:
        return
    parts = []
    planning = report["planning"]
    if planning["cycle_id"] is not None:
        parts.append("cycle %d: %s" % (
            planning["cycle_id"],
            ", ".join("%s=%s" % (p["class"], p["status"])
                      for p in planning["proposals"]) or "no proposals"))
    sweep = report["sweep"]
    for entry in sweep["allocated"]:
        parts.append("assigned %s -> %s (%s, %s)" % (
            entry["sequence_id"][:12], entry["specialist_id"][:12],
            entry["selection_path"], entry["role"]))
    if sweep["supervised"]:
        allowed = len([d for d in sweep["supervised"]
                       if d["decision"] == "allow"])
        parts.append("requests: %d allowed, %d denied"
                     % (allowed, len(sweep["supervised"]) - allowed))
    for entry in sweep["collected"]:
        parts.append("collected %s: %s" % (
            entry["sequence_id"][:12],
            entry.get("state") or entry.get("evidence")))
    for entry in sweep["verified"]:
        parts.append("verification %s: %s"
                     % (entry["attempt_id"][:12], entry["result"]))
    arcs = sweep["arcs"]
    if arcs.get("retried"):
        parts.append("ready again after a failed attempt %d"
                     % len(arcs["retried"]))
    for key in ("finalized", "accepted", "completed"):
        if arcs[key]:
            parts.append("%s %d" % (key, len(arcs[key])))
    for entry in sweep["settled"]:
        parts.append("settled %s: %s%s" % (
            entry["assignment_id"][:12], entry["outcome"],
            " (QUALIFYING)" if entry.get("qualifying") else ""))
    if parts:
        print("  " + "; ".join(parts))
        sys.stdout.flush()


def _grant_permits(rt, sequence_ids):
    """Your publication word, given on the command line
    (--authorize-publication): one use for each VERIFIED sequence that
    has no permit yet."""
    granted = []
    for sequence_id in sequence_ids:
        has_permit = rt.db.conn.execute(
            "SELECT 1 FROM permit_state WHERE sequence_id = ?",
            (sequence_id,)).fetchone()
        if not has_permit:
            rt.sequencer.grant_permit(sequence_id, permitted_uses=1)
            granted.append(sequence_id)
            print("permit granted: %s" % sequence_id)
    return granted


def cmd_run(args):
    """Add an objective and run ONE operator-directed task to stability."""
    from .engineer import EngineerPolicy

    class OperatorPolicy(EngineerPolicy):
        """A single-objective policy: emits one proposal for the
        operator's objective, then stops proposing. It does not reason:
        you named the task. The task is staffed by the Taskforce when a
        retained member holds its capability and role, otherwise by the
        standing project-manager rule (``EngineerPolicy.staff``).

        For READ_ONLY: emits INVESTIGATE (read-only by default).
        For SOURCE_MODIFYING: emits CREATE_TASK (compiler derives SM)."""

        def __init__(self, guidance, spec_inputs, finalization_class,
                     allow_paths=None, capability="research",
                     role="builder"):
            self.guidance = guidance
            self.spec_inputs = spec_inputs
            self.fin_class = finalization_class
            self.allow_paths = allow_paths
            self.capability = capability
            self.role = role
            self.proposed = False

        def plan(self, view):
            if self.proposed or not view.objectives:
                return []
            self.proposed = True
            obj = view.objectives[0]
            roles = {self.role: {"required": True,
                                 "success_outcomes": ["success"]}}
            if self.fin_class == "SOURCE_MODIFYING":
                cls = "CREATE_TASK"
                requirement = {
                    "objective": obj["text"],
                    "capability": self.capability,
                    "finalization_class": "SOURCE_MODIFYING",
                    "roles": roles,
                    "guidance": self.guidance,
                    "specification_inputs": self.spec_inputs,
                }
                if self.allow_paths:
                    requirement["allowed_scope"] = {
                        "paths": sorted(self.allow_paths),
                        "artifact_paths": [],
                    }
            else:
                cls = "INVESTIGATE"
                requirement = {
                    "objective": obj["text"],
                    "capability": self.capability,
                    "roles": roles,
                    "finalization_class": "READ_ONLY",
                    "guidance": self.guidance,
                    "specification_inputs": self.spec_inputs,
                }
            return [{
                "objective_id": obj["objective_id"],
                "class": cls,
                "rationale": "operator-directed %s" % cls.lower().replace("_", " "),
                "requirement": requirement,
                "snapshot": {"entries": view.snapshot["entries"],
                             "head_hash": view.snapshot["head_hash"]},
                "provenance": {"findings": [], "events": []},
            }]

    if not args.objective:
        print("--objective is required", file=sys.stderr)
        return 1

    evidence_profile = getattr(args, "evidence_profile", "READ_ONLY_REPORT")
    try:
        rt = AuthorityRuntime.bootstrap(
            project_root=args.root, evidence_profile=evidence_profile)
    except ConfigError as exc:
        print("bootstrap FAILED: %s" % exc, file=sys.stderr)
        return 1

    try:
        objective_id = args.objective_id or (
            "obj-op-%s" % rt.clock.now().strftime("%Y%m%d%H%M%S"))
        rt.add_objective(objective_id, args.objective)
        print("objective added: %s" % objective_id)

        guidance = args.guidance or args.objective
        fin_class = getattr(args, "finalization_class", "READ_ONLY")
        allow_paths = getattr(args, "allow_paths", None)
        if fin_class == "SOURCE_MODIFYING" and allow_paths:
            guidance = guidance + (SOURCE_MODIFYING_CONTRACT % allow_paths)
        prompt_bytes = guidance.encode("utf-8")
        prompt_address = rt.store.put(prompt_bytes)
        spec_inputs = {
            "name_to_exact_byte_sha256": {
                "specialist_prompt": prompt_address,
            }
        }
        print("prompt stored: %s" % prompt_address)
        policy = OperatorPolicy(guidance, spec_inputs, fin_class,
                                allow_paths=allow_paths,
                                capability=args.capability,
                                role=args.role)

        if args.dry_run:
            print("dry-run: runtime assembled, objective added,"
                  " not executing")
            st = rt.status()
            print(json.dumps(st, indent=2, default=str))
            return 0

        print("running to a stable quiet state...")
        result = rt.run(policy, max_wall_seconds=args.max_wall_seconds,
                        poll_seconds=args.poll_seconds, on_step=_progress)
        print("%s after %d steps"
              % ("stable" if result["stable"] else "TIMED OUT",
                 result["steps"]))

        authorize_pub = getattr(args, "authorize_publication", False)
        if fin_class == "SOURCE_MODIFYING" and authorize_pub:
            verified = [r["sequence_id"] for r in rt.db.conn.execute(
                "SELECT sequence_id FROM sequences"
                " WHERE state = 'VERIFIED'")]
            if _grant_permits(rt, verified):
                print("continuing after permit grant...")
                result = rt.run(policy,
                                max_wall_seconds=args.max_wall_seconds,
                                poll_seconds=args.poll_seconds,
                                on_step=_progress)
                print("%s after %d steps"
                      % ("stable" if result["stable"] else "TIMED OUT",
                         result["steps"]))

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


def cmd_engineer(args):
    """Give the Engineer an objective and run the organization until the
    loop is stably quiet: the Engineer solves the problem and, as Project
    Manager, opens the project and composes its team (a retained member
    the Taskforce resolves for a capability request, or an outside
    specialist it selects for the project), specialists execute through
    AutoSwarm, authority verifies and accepts, demonstrations and
    findings feed the next reasoning cycle.

    Without --objective the command RESUMES: it runs the loop over the
    objectives already active (after a timeout, a restart, or attempts
    the provider failed). A resume may change one active objective's
    budget: name it with --objective-id and give the budget flags."""
    try:
        rt = AuthorityRuntime.bootstrap(
            project_root=args.root, evidence_profile=args.evidence_profile)
    except ConfigError as exc:
        print("bootstrap FAILED: %s" % exc, file=sys.stderr)
        return 1
    try:
        objective_id = args.objective_id
        if args.objective:
            objective_id = objective_id or (
                "obj-eng-%s" % rt.clock.now().strftime("%Y%m%d%H%M%S"))
            budget = {}
            for key in BUDGET_KEYS:
                value = getattr(args, key)
                if value is not None:
                    budget[key] = value
            rt.add_objective(objective_id, args.objective,
                             constraints=args.constraints, budget=budget)
            print("objective added: %s" % objective_id)
        else:
            # The limits are yours (cap-47): a resumed objective's budget
            # may be changed — work lost to a provider failure spends
            # attempts through no fault of the plan.
            budget = {}
            for key in BUDGET_KEYS:
                value = getattr(args, key)
                if value is not None:
                    budget[key] = value
            if budget:
                if not objective_id:
                    active_objs = rt.plan.active_objectives()
                    if active_objs:
                        objective_id = active_objs[0]["objective_id"]
                        print("auto-selected objective: %s" % objective_id)
                    else:
                        print("--objective-id names the objective whose"
                              " budget changes (no active objectives found)",
                              file=sys.stderr)
                        return 1
                try:
                    limits = rt.set_objective_budget(objective_id, budget)
                except ConfigError as exc:
                    print("cannot change the budget: %s" % exc,
                          file=sys.stderr)
                    return 1
                print("budget set for %s: %s" % (
                    objective_id, ", ".join(
                        "%s=%d" % (key, limits[key])
                        for key in sorted(limits))))
            # A resume is news to the Engineer: it reasons again over the
            # current state rather than staying quiet.
            resumed = rt.signal_resume()
            if resumed:
                print("resuming: %s" % ", ".join(resumed))
        active = [o["objective_id"] for o in rt.plan.active_objectives()]
        if not active:
            print("no active objective: give one with --objective",
                  file=sys.stderr)
            return 1
        try:
            policy = rt.engineer_policy()
        except ConfigError as exc:
            print("cannot start the Engineer: %s" % exc, file=sys.stderr)
            return 1
        coordinator_policy = None
        orchestrator_policy = None
        if getattr(args, "coordinator", False):
            coordinator_policy = rt.production_coordinator_policy()
            print("coordinator: %s"
                  % coordinator_policy.policy_id)
        if getattr(args, "orchestrator", False):
            try:
                orchestrator_policy = rt.production_orchestrator_policy()
                print("orchestrator: %s"
                      % orchestrator_policy.policy_id)
            except ConfigError as exc:
                print("cannot start the Orchestrator: %s" % exc,
                      file=sys.stderr)
                return 1
        print("engineer: %s over model %s"
              % (policy.policy_id, rt.engineer_model))
        tracer = None
        if getattr(args, "trace", False):
            from .trace import StepTracer
            tracer = StepTracer()
        print("running to a stable quiet state...")
        rounds = 0
        while True:
            result = rt.run(policy,
                            coordinator_policy=coordinator_policy,
                            orchestrator_policy=orchestrator_policy,
                            tracer=tracer,
                            max_wall_seconds=args.max_wall_seconds,
                            poll_seconds=args.poll_seconds,
                            on_step=_progress)
            print("%s after %d steps (%d waits)"
                  % ("stable" if result["stable"] else "TIMED OUT",
                     result["steps"], result["waits"]))
            rounds += 1
            if not (result["stable"] and args.authorize_publication):
                break
            ours = set()
            for oid in active:
                for row in query.contract_proposals(rt.plan,
                                                    objective_id=oid):
                    if row["sequence_id"]:
                        ours.add(row["sequence_id"])
            verified = [r["sequence_id"] for r in rt.db.conn.execute(
                "SELECT sequence_id FROM sequences WHERE state ="
                " 'VERIFIED' ORDER BY sequence_id")
                if r["sequence_id"] in ours]
            if not _grant_permits(rt, verified) or rounds > 16:
                break
            print("continuing after permit grant...")

        remaining = [o["objective_id"]
                     for o in result["active_objectives"]]
        print("objectives still active: %s" % (remaining or "none"))
        if result["waiting"]:
            print("waiting on your authority:")
            for p in result["waiting"]:
                print("  %s  %s  %s" % (p["class"], p["status"],
                                        p["proposal_id"]))
        if result["in_flight"]:
            print("attempts still in flight: %s" % result["in_flight"])
        ro = readonly_view(rt._authority_db_path)
        try:
            projects = [query.project_state(rt.plan, ro, p["project_id"],
                                            store=rt.store)
                        for oid in active for p in rt.plan.projects(oid)]
        finally:
            ro.close()
        for line in _project_lines(projects):
            print(line)
        for line in _taskforce_lines(rt.taskforce_report()):
            print(line)
        if args.report:
            ro = readonly_view(rt._authority_db_path)
            try:
                payload = {
                    "objectives": {
                        oid: query.objective_state(rt.plan, ro, oid)
                        for oid in active},
                    "projects": projects,
                    "taskforce": rt.taskforce_report(),
                    "run": {"stable": result["stable"],
                            "timed_out": result["timed_out"],
                            "steps": result["steps"],
                            "waits": result["waits"],
                            "active_objectives": remaining,
                            "in_flight": result["in_flight"]},
                }
            finally:
                ro.close()
            with open(str(args.report), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1, sort_keys=True,
                          default=str)
            print("report written: %s" % args.report)
        return 0 if (result["stable"] and not remaining) else 2
    finally:
        rt.close()


def cmd_objectives(args):
    """List objectives, or close one of yours (cap-01: the objective is
    yours). Closing is refused while any of the objective's work is not
    COMPLETED, and while a driver holds the reasoning lease."""
    from autobot import SystemClock, instant
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        now = instant(SystemClock().now())
        if args.supersede_sequence:
            seq_id = args.supersede_sequence
            from autobot import Database
            authority_db_path, _ = _data_paths(args)
            db = Database(str(authority_db_path))
            cur = db.conn.cursor()
            row = cur.execute(
                "SELECT state FROM sequences WHERE sequence_id = ?",
                (seq_id,)).fetchone()
            if row is None:
                print("no sequence %r" % seq_id, file=sys.stderr)
                return 1
            state = row[0] if isinstance(row, tuple) else row["state"]
            if state == "SUPERSEDED":
                print("already SUPERSEDED: %s" % seq_id)
            elif state != "HALTED":
                print("sequence %s is %s, not HALTED — only HALTED"
                      " sequences can be superseded" % (seq_id, state),
                      file=sys.stderr)
                return 1
            else:
                cur.execute(
                    "UPDATE sequences SET state = 'SUPERSEDED',"
                    " updated_utc = ? WHERE sequence_id = ?"
                    " AND state = 'HALTED'",
                    (now, seq_id))
                if cur.rowcount == 1:
                    db.conn.commit()
                    print("superseded: %s (was HALTED, work completed"
                          " by replacement)" % seq_id)
                else:
                    print("no change (race?)", file=sys.stderr)
                    return 1
            # Also resolve any WAITING proposal for this sequence
            plan.conn.execute(
                "UPDATE proposals SET status = 'CLOSED'"
                " WHERE class = 'WAIT_FOR_AUTHORITY'"
                " AND status = 'WAITING'"
                " AND body LIKE ?",
                ("%%%s%%" % seq_id,))
            plan.conn.commit()
            resolved = plan.conn.total_changes
            if resolved:
                print("resolved WAIT_FOR_AUTHORITY proposal(s)")
            db.close()
        if args.close:
            row = plan.objective(args.close)
            if row is None:
                print("no objective %r" % args.close, file=sys.stderr)
                return 1
            lease = plan.conn.execute(
                "SELECT holder, expires_utc FROM driver_lease").fetchone()
            if lease is not None and lease["expires_utc"] > now:
                print("a driver (%s) holds the reasoning lease until %s;"
                      " close the objective when it is not running"
                      % (lease["holder"], lease["expires_utc"]),
                      file=sys.stderr)
                return 1
            from .closure import can_complete_objective
            eligible, reason = can_complete_objective(plan, ro, args.close)
            if not eligible and not args.force:
                print("refused: %s (use --force to override)"
                      % reason, file=sys.stderr)
                return 1
            if not eligible and args.force:
                print("warning: closing with unmet obligations"
                      " (operator override: %s)" % reason,
                      file=sys.stderr)
            if row["status"] == "ACTIVE":
                plan.complete_objective(args.close, now)
                print("closed: %s" % args.close)
            else:
                print("already %s: %s" % (row["status"], args.close))
        for row in plan.conn.execute(
                "SELECT objective_id, status, authored_by, created_utc,"
                " completed_utc, text FROM objectives ORDER BY"
                " created_utc, rowid").fetchall():
            print("%-8s %s  (by %s, %s%s)" % (
                row["status"], row["objective_id"], row["authored_by"],
                row["created_utc"],
                ", completed %s" % row["completed_utc"]
                if row["completed_utc"] else ""))
            print("         %s" % row["text"][:160])
    finally:
        ro.close()
        plan.close()
    return 0


def cmd_taskforce(args):
    """The engineering-organization state report."""
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        report = query.taskforce_report(plan)
        if args.json:
            print(json.dumps(report, indent=1, sort_keys=True,
                             default=str))
            return 0
        for line in _taskforce_lines(report):
            print(line)
        print("members:")
        for member in report["members"]:
            print("  %s  specialist %s (%s)  %s  joined %s by %s"
                  % (member["membership_id"], member["specialist_id"],
                     member["handle"], member["status"], member["joined"],
                     member["joined_by_decision"]))
        print("outside specialists (no retained seat for what they"
              " offer):")
        for specialist in report["outside_specialists"]:
            print("  %s (%s)%s: %s" % (
                specialist["specialist_id"], specialist["handle"],
                " [member through another seat]"
                if specialist["is_member"] else "",
                ", ".join("%s:%s [%d qualifying]"
                          % (o["capability"], o["role"], o["qualifying"])
                          for o in specialist["offers"])))
        print("why each retained member holds its seat:")
        for entry in report["why_retained"]:
            print("  seat %s (%s/%s, specialist %s): %s"
                  % (entry["seat_id"], entry["capability"], entry["role"],
                     entry["specialist_id"], entry["status"]))
            for decision in entry["decisions"]:
                print("    %s by %s at %s: %s"
                      % (decision["kind"], decision["decided_by"],
                         decision["decided_utc"], decision["rationale"]))
                for demo in decision["demonstrations"]:
                    print("      demonstration %s: %s on %s, sequence %s,"
                          " %d verification receipts"
                          % (demo["demonstration_id"][:20],
                             demo["outcome"], demo["environment"],
                             demo["sequence_id"],
                             len(demo["verification_refs"])))
        print("assignment history:")
        for entry in report["assignment_history"]:
            print("  %s  seq %s  attempt %s  %s (decided by %s)"
                  "  specialist %s  standing %s  -> %s"
                  % (entry["assignment_id"], entry["sequence_id"],
                     entry["attempt_ordinal"], entry["selection_path"],
                     entry["decided_by"], entry["specialist_id"],
                     entry["standing_at_assignment"],
                     entry["outcome"] or entry["status"]))
        print("projects and their teams:")
        for line in _project_lines(query.projects_report(
                plan, ro, _evidence_store(args))):
            print("  " + line)
    finally:
        ro.close()
        plan.close()
    return 0


def cmd_coordinator_report(args):
    """The Coordinator: every admission decision and its elevation mode."""
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        rows = plan.conn.execute(
            "SELECT d.decision_id, d.objective_id, d.policy_id,"
            " d.elevation_mode, d.features, d.route, d.created_utc,"
            " o.text AS objective_text, o.status AS objective_status"
            " FROM coordinator_decisions d"
            " LEFT JOIN objectives o ON o.objective_id = d.objective_id"
            " ORDER BY d.created_utc").fetchall()
        if args.json:
            print(json.dumps([dict(r) for r in rows], indent=1,
                             sort_keys=True, default=str))
            return 0
        if not rows:
            print("no coordinator decisions recorded")
            return 0

        # Summary
        modes = {}
        policies = {}
        routes = {}
        for r in rows:
            modes[r["elevation_mode"]] = modes.get(
                r["elevation_mode"], 0) + 1
            policies[r["policy_id"]] = policies.get(
                r["policy_id"], 0) + 1
            if r["route"]:
                routes[r["route"]] = routes.get(r["route"], 0) + 1
        print("Coordinator: %d admission decision(s)" % len(rows))
        print("  by elevation:  %s" % "  ".join(
            "%s=%d" % (k, v) for k, v in sorted(modes.items())))
        print("  by policy:     %s" % "  ".join(
            "%s=%d" % (k, v) for k, v in sorted(policies.items())))
        if routes:
            print("  by route:      %s" % "  ".join(
                "%s=%d" % (k, v) for k, v in sorted(routes.items())))
        print()

        # Each decision
        print("decisions:")
        for r in rows:
            features = json.loads(r["features"]) if r["features"] else {}
            feature_parts = []
            for k, v in sorted(features.items()):
                feature_parts.append("%s=%s" % (k, v))
            feature_str = ("  [%s]" % ", ".join(feature_parts)
                           if feature_parts else "")
            obj_status = (" [%s]" % r["objective_status"]
                          if r["objective_status"] else "")
            print("  %s" % r["decision_id"])
            print("    elevation:  %s" % r["elevation_mode"])
            print("    objective:  %s%s" % (r["objective_id"],
                                            obj_status))
            print("    policy:     %s" % r["policy_id"])
            print("    created:    %s" % r["created_utc"])
            if r["route"]:
                print("    route:      %s" % r["route"])
            if feature_str:
                print("    features:   %s" % feature_str.strip())
            if r["objective_text"]:
                print("    text:       %s" % r["objective_text"])
            print()
    finally:
        ro.close()
        plan.close()
    return 0


def cmd_orchestrator_report(args):
    """The Orchestrator: runs, nodes (the DAG), edges, and reasoning."""
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        runs = plan.conn.execute(
            "SELECT r.orchestration_id, r.objective_id, r.mode,"
            " r.status, r.created_utc, r.completed_utc,"
            " r.completion_policy, r.created_by,"
            " o.text AS objective_text, o.status AS objective_status"
            " FROM orchestration_runs r"
            " LEFT JOIN objectives o ON o.objective_id = r.objective_id"
            " ORDER BY r.created_utc").fetchall()
        if args.json:
            result = []
            for run in runs:
                run_id = run["orchestration_id"]
                nodes = [dict(n) for n in plan.conn.execute(
                    "SELECT * FROM orchestration_nodes"
                    " WHERE orchestration_id = ?"
                    " ORDER BY created_utc", (run_id,)).fetchall()]
                edges = [dict(e) for e in plan.conn.execute(
                    "SELECT * FROM orchestration_edges"
                    " WHERE orchestration_id = ?"
                    " ORDER BY rowid", (run_id,)).fetchall()]
                auth_bindings = [dict(b) for b in plan.conn.execute(
                    "SELECT * FROM orchestration_authority_bindings b"
                    " JOIN orchestration_nodes n ON n.node_id = b.node_id"
                    " WHERE n.orchestration_id = ?"
                    " ORDER BY b.bound_utc", (run_id,)).fetchall()]
                eng_bindings = [dict(b) for b in plan.conn.execute(
                    "SELECT * FROM orchestration_engineering_bindings"
                    " WHERE orchestration_id = ?"
                    " ORDER BY created_utc", (run_id,)).fetchall()]
                proj_bindings = [dict(b) for b in plan.conn.execute(
                    "SELECT pb.*, n.node_key"
                    " FROM orchestration_project_bindings pb"
                    " JOIN orchestration_nodes n"
                    "   ON n.node_id = pb.node_id"
                    " WHERE n.orchestration_id = ?",
                    (run_id,)).fetchall()]
                outputs = [dict(o) for o in plan.conn.execute(
                    "SELECT o.*, n.node_key"
                    " FROM orchestration_node_outputs o"
                    " JOIN orchestration_nodes n"
                    "   ON n.node_id = o.node_id"
                    " WHERE n.orchestration_id = ?",
                    (run_id,)).fetchall()]
                result.append({
                    **dict(run), "nodes": nodes, "edges": edges,
                    "authority_bindings": auth_bindings,
                    "engineering_bindings": eng_bindings,
                    "project_bindings": proj_bindings,
                    "outputs": outputs})
            reasoning = [dict(rr) for rr in plan.conn.execute(
                "SELECT * FROM orchestration_reasoning"
                " ORDER BY orchestration_cycle_id").fetchall()]
            result = {"runs": result,
                      "reasoning": reasoning}
            print(json.dumps(result, indent=1, sort_keys=True,
                             default=str))
            return 0

        if not runs:
            print("no orchestration runs recorded")
            return 0

        # Global summary
        reasoning_rows = plan.conn.execute(
            "SELECT orchestration_cycle_id, policy_id, recorded_utc"
            " FROM orchestration_reasoning"
            " ORDER BY orchestration_cycle_id").fetchall()
        run_states = {}
        total_nodes = 0
        for run in runs:
            run_states[run["status"]] = run_states.get(
                run["status"], 0) + 1
        print("Orchestrator: %d run(s) (%s), %d reasoning cycle(s)"
              % (len(runs),
                 "  ".join("%s=%d" % (k, v)
                           for k, v in sorted(run_states.items())),
                 len(reasoning_rows)))
        print()

        for run in runs:
            run_id = run["orchestration_id"]
            nodes = plan.conn.execute(
                "SELECT node_id, node_key, title, description, status,"
                " realization_kind, capability, work_profile,"
                " organizational_role, resource_scope,"
                " execution_profile, completion_policy,"
                " created_by_decision, created_utc, completed_utc"
                " FROM orchestration_nodes"
                " WHERE orchestration_id = ?"
                " ORDER BY created_utc", (run_id,)).fetchall()
            edges = plan.conn.execute(
                "SELECT e.source_node_id, e.target_node_id,"
                " e.dependency_kind, e.status, e.released_utc,"
                " e.authority_edge_kind, e.required_acceptance,"
                " s.node_key AS source_key, t.node_key AS target_key"
                " FROM orchestration_edges e"
                " JOIN orchestration_nodes s"
                "   ON s.node_id = e.source_node_id"
                " JOIN orchestration_nodes t"
                "   ON t.node_id = e.target_node_id"
                " WHERE e.orchestration_id = ?", (run_id,)).fetchall()
            auth_bindings = plan.conn.execute(
                "SELECT b.binding_id, b.node_id, b.sequence_id,"
                " b.status, b.proposal_id, b.contract_digest,"
                " b.record_hash, b.bound_utc, b.binding_ordinal,"
                " n.node_key"
                " FROM orchestration_authority_bindings b"
                " JOIN orchestration_nodes n ON n.node_id = b.node_id"
                " WHERE n.orchestration_id = ?"
                " ORDER BY b.bound_utc", (run_id,)).fetchall()
            eng_bindings = plan.conn.execute(
                "SELECT eb.engineering_request_id, eb.node_id,"
                " eb.question, eb.created_utc, eb.adopted_utc,"
                " eb.engineering_decision_id, n.node_key"
                " FROM orchestration_engineering_bindings eb"
                " JOIN orchestration_nodes n ON n.node_id = eb.node_id"
                " WHERE eb.orchestration_id = ?"
                " ORDER BY eb.created_utc", (run_id,)).fetchall()
            proj_bindings = plan.conn.execute(
                "SELECT pb.node_id, pb.project_id,"
                " pb.project_workstream_id, pb.bound_utc,"
                " pb.binding_reason, n.node_key"
                " FROM orchestration_project_bindings pb"
                " JOIN orchestration_nodes n ON n.node_id = pb.node_id"
                " WHERE n.orchestration_id = ?", (run_id,)).fetchall()
            outputs = plan.conn.execute(
                "SELECT o.output_id, o.node_id, o.output_kind,"
                " o.content_sha256, o.accepted_utc,"
                " o.sequence_id AS out_seq, n.node_key"
                " FROM orchestration_node_outputs o"
                " JOIN orchestration_nodes n ON n.node_id = o.node_id"
                " WHERE n.orchestration_id = ?"
                " ORDER BY o.accepted_utc", (run_id,)).fetchall()

            node_states = {}
            for n in nodes:
                node_states[n["status"]] = node_states.get(
                    n["status"], 0) + 1
            state_str = "  ".join(
                "%s=%d" % (k, v)
                for k, v in sorted(node_states.items()))
            total_nodes += len(nodes)

            obj_status = (" [%s]" % run["objective_status"]
                          if run["objective_status"] else "")
            print("=" * 72)
            print("Orchestration run: %s" % run_id)
            print("  status:            %s" % run["status"])
            print("  mode:              %s" % run["mode"])
            print("  objective:         %s%s"
                  % (run["objective_id"], obj_status))
            if run["objective_text"]:
                print("  objective text:    %s" % run["objective_text"])
            print("  created:           %s  by %s"
                  % (run["created_utc"], run["created_by"]))
            if run["completed_utc"]:
                print("  completed:         %s" % run["completed_utc"])
            if run["completion_policy"]:
                print("  completion policy: %s" % run["completion_policy"])
            print("  nodes:             %d (%s)" % (len(nodes), state_str))
            print("  edges:             %d" % len(edges))
            print("  authority bindings: %d" % len(auth_bindings))
            print("  engineering bindings: %d" % len(eng_bindings))
            print("  project bindings:  %d" % len(proj_bindings))
            print("  outputs:           %d" % len(outputs))
            print()

            # Nodes
            print("  nodes:")
            for n in nodes:
                node_bindings = [b for b in auth_bindings
                                 if b["node_id"] == n["node_id"]]
                print("    %s" % n["node_key"])
                print("      status:       %s" % n["status"])
                print("      kind:         %s"
                      % (n["realization_kind"] or "-"))
                print("      capability:   %s" % (n["capability"] or "-"))
                print("      work_profile: %s"
                      % (n["work_profile"] or "-"))
                if n["organizational_role"]:
                    print("      org_role:     %s"
                          % n["organizational_role"])
                if n["resource_scope"]:
                    print("      scope:        %s" % n["resource_scope"])
                if n["execution_profile"]:
                    print("      exec_profile: %s"
                          % n["execution_profile"])
                print("      created:      %s" % n["created_utc"])
                if n["completed_utc"]:
                    print("      completed:    %s" % n["completed_utc"])
                if n["created_by_decision"]:
                    print("      decision:     %s"
                          % n["created_by_decision"])
                if n["title"]:
                    print("      title:        %s" % n["title"])
                if n["description"]:
                    print("      description:  %s"
                          % n["description"][:200])
                for b in node_bindings:
                    print("      authority:    seq=%s  [%s]  ordinal=%d"
                          % (b["sequence_id"] or "-", b["status"],
                             b["binding_ordinal"]))
                    if b["contract_digest"]:
                        print("                    contract=%s"
                              % b["contract_digest"])
                print()

            # Edges
            if edges:
                print("  edges (DAG):")
                for e in edges:
                    released = ""
                    if e["released_utc"]:
                        released = "  released=%s" % e["released_utc"][:19]
                    auth_kind = ""
                    if e["authority_edge_kind"]:
                        auth_kind = ("  auth=%s"
                                     % e["authority_edge_kind"])
                    print("    %s -> %s" % (e["source_key"],
                                            e["target_key"]))
                    print("      %s [%s]  required_acceptance=%s%s%s"
                          % (e["dependency_kind"], e["status"],
                             e["required_acceptance"],
                             released, auth_kind))
                print()

            # Engineering bindings
            if eng_bindings:
                print("  engineering bindings:")
                for eb in eng_bindings:
                    print("    %s -> node %s"
                          % (eb["engineering_request_id"],
                             eb["node_key"]))
                    print("      question: %s"
                          % (eb["question"] or "-")[:200])
                    print("      created:  %s" % eb["created_utc"])
                    if eb["adopted_utc"]:
                        print("      adopted:  %s" % eb["adopted_utc"])
                    if eb["engineering_decision_id"]:
                        print("      decision: %s"
                              % eb["engineering_decision_id"])
                print()

            # Project bindings
            if proj_bindings:
                print("  project bindings:")
                for pb in proj_bindings:
                    print("    node %s -> project %s"
                          % (pb["node_key"], pb["project_id"]))
                    if pb["project_workstream_id"]:
                        print("      workstream: %s"
                              % pb["project_workstream_id"])
                    if pb["binding_reason"]:
                        print("      reason:     %s"
                              % pb["binding_reason"])
                print()

            # Outputs
            if outputs:
                print("  outputs:")
                for o in outputs:
                    print("    %s  node=%s  kind=%s  %s"
                          % (o["output_id"], o["node_key"],
                             o["output_kind"],
                             o["accepted_utc"] or "-"))
                    if o["content_sha256"]:
                        print("      sha256=%s" % o["content_sha256"])
                    if o["out_seq"]:
                        print("      sequence=%s" % o["out_seq"])
                print()

        # Reasoning cycles
        print("=" * 72)
        print("reasoning cycles: %d" % len(reasoning_rows))
        if reasoning_rows:
            for rr in reasoning_rows:
                print("  cycle %d  policy=%s  %s"
                      % (rr["orchestration_cycle_id"],
                         rr["policy_id"],
                         rr["recorded_utc"]))
    finally:
        ro.close()
        plan.close()
    return 0


def cmd_engineer_report(args):
    """The Engineer: cycles, reasoning records, proposals, and budgets."""
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        cycles = plan.conn.execute(
            "SELECT c.cycle_id, c.started_utc, c.status,"
            " c.snapshot_entries, c.closed_utc, c.note"
            " FROM cycles c ORDER BY c.cycle_id").fetchall()
        proposals = plan.conn.execute(
            "SELECT p.proposal_id, p.cycle_id, p.class, p.status,"
            " p.objective_id, p.sequence_id, p.body, p.rationale,"
            " p.contract_digest, p.task_contract_id,"
            " p.created_utc, p.decided_utc, p.note"
            " FROM proposals p ORDER BY p.created_utc").fetchall()
        reasoning = plan.conn.execute(
            "SELECT r.cycle_id, r.policy_id, r.recorded_utc,"
            " r.proposals"
            " FROM cycle_reasoning r"
            " ORDER BY r.cycle_id").fetchall()
        objectives = plan.conn.execute(
            "SELECT objective_id, status, text, authored_by,"
            " created_utc, completed_utc FROM objectives"
            " ORDER BY created_utc").fetchall()
        budgets = plan.conn.execute(
            "SELECT * FROM objective_budgets"
            " ORDER BY set_utc").fetchall()
        findings_rows = plan.conn.execute(
            "SELECT finding_id, sequence_id, role, finding_class,"
            " affected_component, significance, confidence,"
            " admitted_utc, consumed_in_cycle"
            " FROM findings ORDER BY admitted_utc").fetchall()
        events = plan.conn.execute(
            "SELECT journal_seq, event_kind, subject_id, rule,"
            " recorded_utc, consumed_in_cycle"
            " FROM planning_events"
            " ORDER BY journal_seq DESC LIMIT 20").fetchall()
        projects = plan.conn.execute(
            "SELECT project_id, objective_id, title, status,"
            " opened_by, opened_utc, closed_utc"
            " FROM projects ORDER BY opened_utc").fetchall()
        lease = plan.conn.execute(
            "SELECT * FROM driver_lease").fetchone()

        if args.json:
            result = {
                "cycles": [dict(c) for c in cycles],
                "proposals": [dict(p) for p in proposals],
                "reasoning_records": [dict(r) for r in reasoning],
                "objectives": [dict(o) for o in objectives],
                "budgets": [dict(b) for b in budgets],
                "findings": [dict(f) for f in findings_rows],
                "projects": [dict(p) for p in projects],
                "recent_events": [dict(e) for e in events],
                "lease": dict(lease) if lease else None,
            }
            print(json.dumps(result, indent=1, sort_keys=True,
                             default=str))
            return 0

        # Header
        cycle_states = {}
        for c in cycles:
            cycle_states[c["status"]] = cycle_states.get(
                c["status"], 0) + 1
        print("Engineer: %d cycle(s) (%s), %d reasoning record(s),"
              " %d proposal(s), %d finding(s)"
              % (len(cycles),
                 "  ".join("%s=%d" % (k, v)
                           for k, v in sorted(cycle_states.items()))
                 if cycle_states else "none",
                 len(reasoning), len(proposals),
                 len(findings_rows)))
        print()

        # Driver lease
        if lease:
            print("driver lease:")
            print("  holder:   %s" % lease["holder"])
            print("  acquired: %s" % lease["acquired_utc"])
            print("  expires:  %s" % lease["expires_utc"])
            print()

        # Objectives
        print("objectives: %d" % len(objectives))
        for o in objectives:
            print("  %s  [%s]  by %s  %s"
                  % (o["objective_id"], o["status"],
                     o["authored_by"], o["created_utc"]))
            if o["completed_utc"]:
                print("    completed: %s" % o["completed_utc"])
            print("    %s" % o["text"])
        print()

        # Projects
        if projects:
            print("projects: %d" % len(projects))
            for p in projects:
                print("  %s  [%s]  obj=%s  opened=%s  by=%s"
                      % (p["project_id"], p["status"],
                         p["objective_id"], p["opened_utc"],
                         p["opened_by"]))
                if p["closed_utc"]:
                    print("    closed: %s" % p["closed_utc"])
                print("    %s" % p["title"])
            print()

        # Proposal breakdown by class and status
        by_class = {}
        by_status = {}
        for p in proposals:
            by_class[p["class"]] = by_class.get(p["class"], 0) + 1
            by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        if by_class:
            print("proposals by class:")
            for cls, count in sorted(by_class.items()):
                print("  %-30s %d" % (cls, count))
            print()
            print("proposals by status:")
            for st, count in sorted(by_status.items()):
                print("  %-30s %d" % (st, count))
            print()

        # Budgets with usage
        if budgets:
            print("budgets:")
            for b in budgets:
                usage = plan.budget_usage(b["objective_id"])
                print("  %s" % b["objective_id"])
                print("    contracts:  %d / %d used"
                      % (usage.get("contracts", 0), b["max_contracts"]))
                print("    attempts:   %d / %d used"
                      % (usage.get("attempts", 0), b["max_attempts"]))
                print("    outside:    %d / %d used"
                      % (usage.get("outside_specialists", 0),
                         b["max_outside_specialists"]))
                print("    repairs:    %d / %d used"
                      % (usage.get("repairs", 0), b["max_repairs"]))
                print("    retention:  %d / %d used"
                      % (usage.get("retention_decisions", 0),
                         b["max_retention_decisions"]))
                print("    set %s by %s"
                      % (b["set_utc"], b["set_by"]))
            print()

        # Active/waiting proposals (expanded)
        active_proposals = [p for p in proposals
                            if p["status"] in ("EVALUATED", "WAITING",
                                               "QUEUED_FOR_YOU")]
        if active_proposals:
            print("active proposals: %d" % len(active_proposals))
            for p in active_proposals:
                body = json.loads(p["body"]) if p["body"] else {}
                print("  %s" % p["proposal_id"])
                print("    class:     %s" % p["class"])
                print("    status:    %s" % p["status"])
                print("    objective: %s" % (p["objective_id"] or "-"))
                if p["sequence_id"]:
                    print("    sequence:  %s" % p["sequence_id"])
                cap = body.get("capability")
                if cap:
                    print("    capability: %s" % cap)
                role = body.get("role")
                if role:
                    print("    role:      %s" % role)
                print("    rationale: %s" % (p["rationale"] or "-"))
                print("    created:   %s" % p["created_utc"])
                if p["note"]:
                    print("    note:      %s" % p["note"])
                print()
            print()

        # Findings
        if findings_rows:
            unconsumed = [f for f in findings_rows
                          if f["consumed_in_cycle"] is None]
            print("findings: %d total, %d unconsumed"
                  % (len(findings_rows), len(unconsumed)))
            by_class_f = {}
            by_significance = {}
            for f in findings_rows:
                by_class_f[f["finding_class"]] = by_class_f.get(
                    f["finding_class"], 0) + 1
                by_significance[f["significance"]] = by_significance.get(
                    f["significance"], 0) + 1
            print("  by class:        %s" % "  ".join(
                "%s=%d" % (k, v)
                for k, v in sorted(by_class_f.items())))
            print("  by significance: %s" % "  ".join(
                "%s=%d" % (k, v)
                for k, v in sorted(by_significance.items())))
            if unconsumed:
                print("  unconsumed:")
                for f in unconsumed:
                    print("    %s  %s  %s  %s  %s  %s"
                          % (f["finding_id"], f["finding_class"],
                             f["significance"], f["affected_component"],
                             f["confidence"], f["admitted_utc"]))
            print()

        # Recent cycles (last 20)
        recent = cycles[-20:] if len(cycles) > 20 else cycles
        if recent:
            print("recent cycles (last %d of %d):"
                  % (len(recent), len(cycles)))
            for c in recent:
                proposal_count = sum(
                    1 for p in proposals if p["cycle_id"] == c["cycle_id"])
                reasoning_row = None
                for r in reasoning:
                    if r["cycle_id"] == c["cycle_id"]:
                        reasoning_row = r
                        break
                note = ""
                if c["note"]:
                    note = "  (%s)" % c["note"][:80]
                policy = ""
                if reasoning_row:
                    policy = "  policy=%s" % reasoning_row["policy_id"]
                closed = ""
                if c["closed_utc"]:
                    closed = "  closed=%s" % c["closed_utc"][:19]
                print("  cycle %4d  %-6s  %s  snapshot=%d  "
                      "proposals=%d%s%s%s"
                      % (c["cycle_id"], c["status"],
                         c["started_utc"][:19], c["snapshot_entries"],
                         proposal_count, policy, closed, note))
            print()

        # Recent events
        if events:
            print("recent planning events (last %d):" % len(events))
            for e in events:
                consumed = ("  -> cycle %d" % e["consumed_in_cycle"]
                            if e["consumed_in_cycle"] else "  (pending)")
                print("  seq=%d  %s  %s  rule=%s  %s%s"
                      % (e["journal_seq"], e["event_kind"],
                         e["subject_id"], e["rule"],
                         e["recorded_utc"], consumed))
    finally:
        ro.close()
        plan.close()
    return 0


def cmd_query(args):
    """The engineering query layer, as JSON."""
    try:
        plan, ro = _open_read_only(args)
    except ConfigError as exc:
        print("cannot read state: %s" % exc, file=sys.stderr)
        return 1
    try:
        kind, value = args.kind, args.value
        if kind == "objective":
            result = query.objective_state(plan, ro, value)
        elif kind == "project":
            result = query.project_state(plan, ro, value,
                                         store=_evidence_store(args))
        elif kind == "projects":
            result = query.projects_report(plan, ro, _evidence_store(args))
        elif kind == "sequence":
            result = query.by_sequence(plan, ro, value)
        elif kind == "attempt":
            result = query.by_attempt(plan, ro, value)
        elif kind == "specialist":
            result = query.by_specialist(plan, value)
        elif kind == "capability":
            result = query.by_capability(plan, value)
        elif kind == "seat":
            result = query.by_seat(plan, value)
        elif kind == "subsystem":
            result = query.by_subsystem(plan, ro, value)
        elif kind == "finding":
            found = query.findings(plan, finding_id=value)
            result = {"finding": found[0] if found else None,
                      "consequences": query.finding_consequences(
                          plan, value)}
        elif kind == "dependencies":
            result = query.dependencies(plan, ro, value)
        elif kind == "verification-outcome":
            result = query.by_verification_outcome(plan, ro, value)
        elif kind == "accepted":
            result = query.accepted_results(plan, ro, value)
        elif kind == "graph":
            result = query.artifact_graph(plan, ro, value)
        elif kind == "why-selected":
            result = query.why_selected(plan, value)
        elif kind == "why-retained":
            result = query.why_retained(plan, value)
        elif kind == "why-created":
            result = query.why_created(plan, value)
        elif kind == "sessions":
            result = query.session_bridge(plan, ro, value)
        else:
            result = query.taskforce_report(plan)
        print(json.dumps(result, indent=1, sort_keys=True, default=str))
        return 0 if result is not None else 1
    finally:
        ro.close()
        plan.close()


def _add_run_controls(parser):
    parser.add_argument("--trace", action="store_true",
                        help="show real-time driver pipeline trace")
    parser.add_argument("--max-wall-seconds", type=float, default=3600.0,
                        help="stop waiting for specialists after this"
                             " long (default: 3600); a later run resumes")
    parser.add_argument("--poll-seconds", type=float, default=15.0,
                        help="how often to look again while specialists"
                             " are executing (default: 2)")
    parser.add_argument("--authorize-publication", action="store_true",
                        default=False,
                        help="your publication word (cap-36): grant one"
                             " use for each verified SOURCE_MODIFYING"
                             " sequence of this run")


def main():
    parser = argparse.ArgumentParser(
        prog="autobot-authority",
        description="Autobot production runtime")
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: auto-detect)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("seed",
                   help="admit canonical production objects")
    sub.add_parser("bootstrap",
                   help="validate assembly and print readiness")
    reset_p = sub.add_parser(
        "reset",
        help="full reset: delete all databases + store, re-seed"
             " the trust chain, validate assembly")
    reset_p.add_argument("--confirm", action="store_true",
                         help="required: confirms intent to destroy"
                              " all state and rebuild")
    sub.add_parser("status",
                   help="show objectives, sequences and the Taskforce")
    pre_p = sub.add_parser(
        "preflight", help="resolve the execution routes; with --probe,"
                          " exercise each with one small real session")
    pre_p.add_argument("--probe", action="store_true",
                       help="spend one small session per route role and"
                            " one Engineer call")
    pre_p.add_argument("--timeout", type=float, default=900.0,
                       help="seconds to wait for the probe sessions"
                            " (default: 900)")
    pre_p.add_argument("--report", type=Path, default=None,
                       help="write the preflight report as JSON to this"
                            " file")

    eng_p = sub.add_parser(
        "engineer", help="give the Engineer an objective and run"
                         " the organization to a stable quiet state")
    eng_p.add_argument("--objective", default=None,
                       help="objective text (omit to resume the active"
                            " objectives)")
    eng_p.add_argument("--objective-id", default=None,
                       help="objective id (default: auto-generated); on a"
                            " resume, the active objective the budget"
                            " flags apply to")
    eng_p.add_argument("--constraint", action="append", default=None,
                       dest="constraints",
                       help="a design constraint stated with the"
                            " objective (repeat for several)")
    eng_p.add_argument("--evidence-profile", default="BY_CLASS",
                       choices=EVIDENCE_PROFILE_CHOICES,
                       help="evidence profile (default: BY_CLASS)")
    for key, text in (
            ("max_contracts", "contracts the objective may create"),
            ("max_attempts", "attempts the objective may launch"),
            ("max_outside_specialists",
             "outside specialists the objective may source"),
            ("max_repairs", "repairs the objective may authorize"),
            ("max_retention_decisions",
             "retention decisions the objective may take")):
        eng_p.add_argument("--" + key.replace("_", "-"), type=int,
                           default=None, dest=key,
                           help="budget: " + text)
    eng_p.add_argument("--report", type=Path, default=None,
                       help="write the closing state as JSON to this file")
    eng_p.add_argument("--coordinator", action="store_true",
                       help="activate the production Coordinator"
                            " (ProductionCoordinatorPolicy)")
    eng_p.add_argument("--orchestrator", action="store_true",
                       help="activate the production Opus Orchestrator"
                            " (OpusOrchestratorPolicy)")
    _add_run_controls(eng_p)

    run_p = sub.add_parser("run", help="one operator-directed task")
    run_p.add_argument("--objective", required=True,
                       help="objective text")
    run_p.add_argument("--objective-id", default=None,
                       help="objective id (default: auto-generated)")
    run_p.add_argument("--guidance", default=None,
                       help="specialist guidance (default: objective text)")
    run_p.add_argument("--capability", default="research",
                       help="capability the task needs (default: research)")
    run_p.add_argument("--role", default="builder",
                       help="role the task needs (default: builder)")
    run_p.add_argument("--evidence-profile", default="READ_ONLY_REPORT",
                       choices=EVIDENCE_PROFILE_CHOICES,
                       help="evidence profile (default: READ_ONLY_REPORT)")
    run_p.add_argument("--finalization-class", default="READ_ONLY",
                       choices=["READ_ONLY", "SOURCE_MODIFYING"],
                       help="finalization class (default: READ_ONLY)")
    run_p.add_argument("--allow-path", action="append", default=None,
                       dest="allow_paths",
                       help="exact task mutation path (repeat for multiple)")
    run_p.add_argument("--dry-run", action="store_true",
                       help="assemble and add objective without executing")
    _add_run_controls(run_p)

    obj_p = sub.add_parser("objectives",
                           help="list objectives, or close one of yours")
    obj_p.add_argument("--close", default=None, metavar="OBJECTIVE_ID",
                       help="close an objective whose work is all"
                            " COMPLETED")
    obj_p.add_argument("--force", action="store_true",
                       help="operator override: close even when"
                            " organizational obligations remain")
    obj_p.add_argument("--supersede-sequence", default=None,
                       metavar="SEQUENCE_ID",
                       help="mark a HALTED sequence as SUPERSEDED"
                            " (work completed by a replacement)")

    tf_p = sub.add_parser("taskforce",
                          help="the engineering-organization report")
    tf_p.add_argument("--json", action="store_true",
                      help="print the full report as JSON")

    coord_p = sub.add_parser(
        "coordinator",
        help="the Coordinator report: admission decisions and"
             " elevation modes")
    coord_p.add_argument("--json", action="store_true",
                         help="print the full report as JSON")

    orch_p = sub.add_parser(
        "orchestrator",
        help="the Orchestrator report: runs, nodes (DAG), edges,"
             " reasoning cycles")
    orch_p.add_argument("--json", action="store_true",
                        help="print the full report as JSON")

    eng_report_p = sub.add_parser(
        "engineer-report",
        help="the Engineer report: cycles, reasoning, proposals,"
             " budgets")
    eng_report_p.add_argument("--json", action="store_true",
                              help="print the full report as JSON")

    q_p = sub.add_parser("query", help="the engineering query layer")
    q_p.add_argument("kind", choices=list(query.QUERIES))
    q_p.add_argument("value", nargs="?", default=None,
                     help="the identity to query (objective id, sequence"
                          " id, seat id, capability, ...)")

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 1

    commands = {
        "seed": cmd_seed,
        "bootstrap": cmd_bootstrap,
        "reset": cmd_reset,
        "preflight": cmd_preflight,
        "status": cmd_status,
        "engineer": cmd_engineer,
        "run": cmd_run,
        "objectives": cmd_objectives,
        "taskforce": cmd_taskforce,
        "coordinator": cmd_coordinator_report,
        "orchestrator": cmd_orchestrator_report,
        "engineer-report": cmd_engineer_report,
        "query": cmd_query,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
