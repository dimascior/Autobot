"""The production runtime: assembly and lifecycle.

Constructs the complete ContinuousDriver stack from configuration,
acquiring leases and wiring every component — Database, Store, Registry,
Sequencer, PlanStore, the specialist catalog (SpecialistPool),
AutoSwarmTransport, ProductionVerifierRunner and the
RepositoryEnvironment — into a single object whose public surface is:

    runtime = AuthorityRuntime.from_config(config)
    runtime.add_objective(...)
    runtime.run(policy)            # waits for work in flight
    runtime.status()
    runtime.close()

No new semantics: every component is the same class the deterministic
suites exercise. The runtime's job is assembly and lifecycle management —
connecting configuration to constructors.

The specialist catalog comes from configuration:

    [specialists.<handle>]
    agent  = "claude-code"          # the provider AutoSwarm launches
    model  = "sonnet"
    offers = ["research:investigator", "documentation:builder"]

    [taskforce]
    established_threshold = 3

Each ``[specialists.<handle>]`` table is one engineering worker: a stable
identity, how it is routed, and the (capability, role) pairs it may do.
Being in the catalog makes a specialist AVAILABLE to projects — an
outside specialist a Project Manager can select. It does not make it a
Taskforce member: membership exists only in ``intelligence.db``, earned
by an explicit retention decision. ``[taskforce]`` configures the
persistent organization itself.

Two earlier spellings are still read: ``[taskforce.specialists.<handle>]``
(the same table under ``[taskforce]``) and ``[authority_routes.<name>]``
(``<capability> = "<role>"`` keys, each route one worker named after it).
"""

import json
import os
import shutil
import sys

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

from pathlib import Path

from autobot import Database, Registry, Sequencer, Store, SystemClock, instant
from autobot.autoswarm_transport import AutoSwarmTransport
from autobot.verifier_runner import ProductionVerifierRunner

from . import query
from .capabilities import CAPABILITIES
from .compiler import RepositoryEnvironment
from .driver import ContinuousDriver
from .planstore import PlanStore, readonly_view
from .workforce import SpecialistPool

_ROUTE_KEYS = ("agent", "model", "provider_args", "workspace",
               "remote_control", "effort")


def _which(name):
    return shutil.which(name)


def build_pool(file_config):
    """The specialist catalog from configuration (see the module
    docstring for the accepted forms). Registration order is
    configuration order, so every listing over it is reproducible."""
    pool = SpecialistPool()
    specialists = {}
    for section in (file_config.get("specialists", {}),
                    file_config.get("taskforce", {}).get("specialists",
                                                         {})):
        if not isinstance(section, dict):
            raise ConfigError("[specialists] must be a table of workers")
        for handle, spec in section.items():
            specialists.setdefault(handle, spec)
    for handle, spec in specialists.items():
        if not isinstance(spec, dict):
            continue
        agent = spec.get("agent") or spec.get("provider")
        if not agent:
            raise ConfigError("[specialists.%s] needs an agent" % handle)
        offers = spec.get("offers", [])
        if not (isinstance(offers, list) and offers):
            raise ConfigError(
                "[specialists.%s] offers must list at least one"
                " \"capability:role\"" % handle)
        for offer in offers:
            capability, _sep, role = str(offer).partition(":")
            if not role or capability not in CAPABILITIES:
                raise ConfigError(
                    "[specialists.%s] offer %r is not \"capability:role\""
                    " over the capability vocabulary" % (handle, offer))
            pool.register(
                capability, role, agent, model=spec.get("model"),
                handle=handle, effort=spec.get("effort"),
                tool_requirements=spec.get("tool_requirements", ()),
                context_budget=spec.get("context_budget"),
                expected_output=spec.get("expected_output", "artifacts"),
                origin=spec.get("origin", "outside_pool"),
                max_concurrent=int(spec.get("max_concurrent", 1)))
    routes = file_config.get("authority_routes",
                             file_config.get("routes", {}))
    for route_name, route_config in routes.items():
        if not isinstance(route_config, dict):
            continue
        agent = route_config.get("agent", route_name)
        model = route_config.get("model", None)
        for capability, role_map in route_config.items():
            if capability in _ROUTE_KEYS:
                continue
            if isinstance(role_map, str):
                pool.register(capability, role_map, agent, model=model,
                              handle=route_name)
            elif isinstance(role_map, dict):
                for role, provider in role_map.items():
                    pool.register(capability, role, provider, model=model,
                                  handle="%s/%s" % (route_name, provider))
    return pool


class ConfigError(Exception):
    """A required configuration value is missing or invalid."""


def _resolve_autoswarm_home(explicit):
    if explicit:
        return Path(explicit)
    env = os.environ.get("AUTOSWARM_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            probe = Path(local) / "autoswarm"
            if probe.is_dir():
                return probe
    home = Path.home() / ".autoswarm"
    if home.is_dir():
        return home
    raise ConfigError(
        "cannot resolve autoswarm_home: set it in config, set"
        " $AUTOSWARM_HOME, or create the conventional directory")


def _resolve_gate_root(explicit):
    if explicit:
        return Path(explicit)
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text("utf-8"))
            for hook in settings.get("hooks", {}).get("PreToolUse", []):
                cmd = hook.get("command", "")
                for part in cmd.replace("\\", "/").split():
                    if ".command-gate" in part:
                        candidate = Path(part.split(".command-gate")[0]
                                         + ".command-gate")
                        if candidate.is_dir():
                            return candidate
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _load_config(project_root):
    if tomllib is None:
        return {}
    base_path = project_root / "config.toml"
    local_path = project_root / "config.local.toml"
    config = {}
    if base_path.is_file():
        with open(str(base_path), "rb") as fh:
            config = tomllib.load(fh)
    if local_path.is_file():
        with open(str(local_path), "rb") as fh:
            local = tomllib.load(fh)
        for section, values in local.items():
            if isinstance(values, dict):
                config.setdefault(section, {}).update(values)
            else:
                config[section] = values
    return config


def _environment_from_ledger(db, store, repository_id, defaults,
                            delegation_id=None):
    """Build the RepositoryEnvironment from the admitted objects already
    in the authority database. The runtime reads the ledger it governs
    rather than maintaining a second source of bound-object payloads.

    When ``delegation_id`` is given, the exact DelegationContract with
    that ID is selected; otherwise the most recently admitted delegation
    is used (test convenience — production always configures an ID)."""
    decl_row = db.conn.execute(
        "SELECT o.content_address FROM declarations d"
        " JOIN objects o ON d.object_hash = o.object_hash"
        " WHERE d.repository_id = ?", (repository_id,)).fetchone()
    if decl_row is None:
        raise ConfigError(
            "no RepositoryAuthorityDeclaration for %r in the ledger"
            % repository_id)
    declaration = json.loads(store.get(decl_row["content_address"]))

    deleg_rows = db.conn.execute(
        "SELECT content_address FROM objects"
        " WHERE kind = 'DelegationContract'"
        " ORDER BY minted_utc DESC").fetchall()
    if not deleg_rows:
        raise ConfigError("no DelegationContract admitted in the ledger")

    delegation = None
    if delegation_id:
        for r in deleg_rows:
            payload = json.loads(store.get(r["content_address"]))
            if payload.get("delegation_id") == delegation_id:
                delegation = payload
                break
        if delegation is None:
            raise ConfigError(
                "configured delegation_id %r not found in the ledger"
                % delegation_id)
    else:
        delegation = json.loads(store.get(deleg_rows[0]["content_address"]))

    verifier_rows = db.conn.execute(
        "SELECT content_address FROM objects"
        " WHERE kind = 'VerifierRegistration'"
        " ORDER BY minted_utc").fetchall()
    verifier_registrations = [
        json.loads(store.get(r["content_address"])) for r in verifier_rows]

    transform_rows = db.conn.execute(
        "SELECT content_address FROM objects"
        " WHERE kind = 'TransformRegistration'"
        " ORDER BY minted_utc").fetchall()
    transform_registrations = [
        json.loads(store.get(r["content_address"])) for r in transform_rows]

    return RepositoryEnvironment(
        declaration=declaration,
        delegation=delegation,
        verifier_registrations=verifier_registrations,
        transform_registrations=transform_registrations,
        defaults=defaults)


def _admitted_ids(db, store, kind, id_key):
    """Return the set of registered IDs of a given ``kind`` from the
    authority database (e.g. 'VerifierRegistration' / 'verifier_id')."""
    rows = db.conn.execute(
        "SELECT content_address FROM objects WHERE kind = ?"
        " ORDER BY minted_utc", (kind,)).fetchall()
    ids = set()
    for r in rows:
        payload = json.loads(store.get(r["content_address"]))
        v = payload.get(id_key)
        if v:
            ids.add(v)
    return ids


class AuthorityRuntime:
    """The assembled production runtime.

    All components are the same classes the deterministic suites
    exercise: Database, Store, Registry, Sequencer, PlanStore, the
    Taskforce's SpecialistPool, AutoSwarmTransport,
    ProductionVerifierRunner, RepositoryEnvironment, ContinuousDriver.
    The runtime only connects configuration to constructors."""

    def __init__(self, *, db, store, registry, sequencer, plan,
                 pool, transport, runner, environment, clock,
                 authority_db_path, repo_path=None,
                 holder="autobot-authority",
                 lease_seconds=3600, established_threshold=3,
                 engineer_model=None, repository_context=None,
                 isolate_read_only=True, chief_engineer_model=None):
        self.isolate_read_only = isolate_read_only
        self.db = db
        self.store = store
        self.registry = registry
        self.sequencer = sequencer
        self.plan = plan
        self.pool = pool
        self.transport = transport
        self.runner = runner
        self.environment = environment
        self.clock = clock
        self.established_threshold = established_threshold
        # The model the Engineer reasons with (``chief_engineer_model`` is
        # the name it had before the organizational model was aligned).
        self.engineer_model = engineer_model or chief_engineer_model
        self.chief_engineer_model = self.engineer_model
        self.repository_context = repository_context
        self._authority_db_path = authority_db_path
        self._repo_path = repo_path
        self._holder = holder
        self._lease_seconds = lease_seconds
        self._driver = None

    @classmethod
    def from_config(cls, project_root=None, config=None,
                    defaults=None, clock=None, evidence_profile=None):
        """Assemble the full runtime from project configuration.

        ``project_root`` is the checkout directory containing config.toml
        and the authority data files. ``config`` overrides file-based
        configuration. ``defaults`` is the engineering profile the
        compiler uses for proposals that do not restate every field.
        ``clock`` overrides the system clock (tests inject ManualClock).
        """
        if project_root is None:
            project_root = Path(__file__).resolve().parents[2]
        project_root = Path(project_root)

        file_config = _load_config(project_root)
        if config:
            for section, values in config.items():
                if isinstance(values, dict):
                    file_config.setdefault(section, {}).update(values)
                else:
                    file_config[section] = values

        paths = file_config.get("paths", {})
        authority_section = file_config.get("authority", {})
        taskforce_section = file_config.get("taskforce", {})
        engineer_section = file_config.get("engineer", {})
        chief_section = file_config.get("chief_engineer", {})

        # --- paths ---
        data_dir = Path(authority_section.get("data_dir",
                                              str(project_root)))
        authority_db_path = data_dir / authority_section.get(
            "authority_db", "autobot.db")
        intelligence_db_path = data_dir / authority_section.get(
            "intelligence_db", "intelligence.db")
        store_root = data_dir / authority_section.get(
            "store_root", "store")
        autoswarm_home = _resolve_autoswarm_home(
            paths.get("autoswarm_home", ""))
        workspace = authority_section.get("workspace", "")
        if not workspace:
            workspace = str(project_root)
        gate_root = _resolve_gate_root(paths.get("gate_root", ""))
        session_export_dir = authority_section.get(
            "session_export_dir", None)
        repo_path = authority_section.get("repo_path", None)
        holder = authority_section.get("holder", "autobot-authority")
        lease_ttl = int(authority_section.get("lease_ttl", 3600))
        repository_id = authority_section.get("repository_id", "autobot")
        delegation_id = authority_section.get("delegation_id", None)

        # --- clock ---
        clock = clock or SystemClock()

        # --- authority core ---
        db = Database(authority_db_path)
        store = Store(store_root)
        registry = Registry(db, store, clock)
        sequencer = Sequencer(db, store, clock, holder=holder)
        sequencer.acquire_lease(ttl_seconds=lease_ttl)

        # --- intelligence ---
        plan = PlanStore(intelligence_db_path)

        # --- the Taskforce's specialist catalog ---
        pool = build_pool(file_config)
        established_threshold = int(taskforce_section.get(
            "established_threshold", 3))
        # The Engineer's reasoning model is configuration ([engineer]
        # model; [chief_engineer] model is the earlier spelling), falling
        # back to the organization's planner model. Nothing here picks
        # one.
        engineer_model = engineer_section.get("model") or \
            chief_section.get("model") or \
            file_config.get("models", {}).get("planner")

        # --- transport ---
        autoswarm_cli = authority_section.get("autoswarm_cli", None)
        autoswarm_argv = None
        if autoswarm_cli:
            autoswarm_argv = [autoswarm_cli]
        elif _which("autoswarm"):
            autoswarm_argv = [_which("autoswarm")]
        transport = AutoSwarmTransport(
            store, str(autoswarm_home), workspace,
            gate_root=str(gate_root) if gate_root else None,
            session_export_dir=session_export_dir,
            autoswarm_argv=autoswarm_argv)

        # --- verifier ---
        runner = ProductionVerifierRunner(sequencer, store)

        # --- environment ---
        if defaults is None:
            defaults = authority_section.get("defaults", {})
        if not defaults:
            from .production_seed import production_defaults
            ep = evidence_profile or "READ_ONLY_REPORT"
            defaults = production_defaults(project_root,
                                           evidence_profile=ep)
        environment = _environment_from_ledger(
            db, store, repository_id, defaults,
            delegation_id=delegation_id)

        return cls(
            db=db, store=store, registry=registry, sequencer=sequencer,
            plan=plan, pool=pool, transport=transport, runner=runner,
            environment=environment, clock=clock,
            authority_db_path=authority_db_path, repo_path=repo_path,
            holder=holder, lease_seconds=lease_ttl,
            established_threshold=established_threshold,
            engineer_model=engineer_model,
            isolate_read_only=bool(authority_section.get(
                "isolate_read_only", True)),
            repository_context=authority_section.get(
                "repository_context",
                "repository %s, canonical ref %s"
                % (repository_id,
                   environment.declaration.get("canonical_ref"))))

    @classmethod
    def bootstrap(cls, project_root=None, config=None,
                  defaults=None, clock=None, evidence_profile=None):
        """Construct the runtime, validate every pilot dependency, and
        return the runtime ready for add_objective / run_until_stable.

        Raises ConfigError if any required component is missing or
        incomplete: a non-empty Taskforce specialist catalog, autoswarm_home,
        workspace, gate_root, repository declaration, delegation, at least
        two verifier registrations, identity-projection transform, and the
        compiler defaults needed to freeze a contract."""
        rt = cls.from_config(project_root=project_root, config=config,
                             defaults=defaults, clock=clock,
                             evidence_profile=evidence_profile)
        try:
            rt._validate_pilot_readiness()
        except ConfigError:
            rt.close()
            raise
        return rt

    REQUIRED_VERIFIER_IDS = ("registry-checker", "vector-checker")
    REQUIRED_TRANSFORM_IDS = ("identity-projection",)

    def _validate_pilot_readiness(self):
        """Check every dependency a production run requires."""
        problems = []
        if not self.pool.profiles():
            problems.append(
                "the specialist catalog is empty: no specialist can be"
                " assigned to a project (configure [specialists.<handle>])")
        if self.transport._gate_root is None:
            problems.append("gate_root is None — governance evidence"
                            " collection will be skipped")
        elif not Path(self.transport._gate_root).is_dir():
            problems.append("gate_root %s does not exist"
                            % self.transport._gate_root)
        if not self.transport._home.is_dir():
            problems.append("autoswarm_home %s does not exist"
                            % self.transport._home)
        if not self.transport._workspace.is_dir():
            problems.append("workspace %s does not exist"
                            % self.transport._workspace)
        decl = self.environment.declaration
        if decl.get("authority_location") != "LOCAL":
            problems.append(
                "declaration authority_location is %r, expected 'LOCAL'"
                % decl.get("authority_location"))
        if decl.get("canonical_ref") != "refs/heads/main":
            problems.append(
                "declaration canonical_ref is %r, expected"
                " 'refs/heads/main'" % decl.get("canonical_ref"))
        deleg = self.environment.delegation
        repos = deleg.get("repository_ids", [])
        if "autobot" not in repos:
            problems.append(
                "delegation repository_ids %r does not include 'autobot'"
                % repos)
        if deleg.get("delegated_to") != "planner":
            problems.append(
                "delegation delegated_to is %r, expected 'planner'"
                % deleg.get("delegated_to"))
        now_str = instant(self.clock.now())
        created = deleg.get("created_utc", "")
        expires = deleg.get("expires_utc", "")
        if created and now_str < created:
            problems.append(
                "delegation created_utc %s is in the future" % created)
        if expires and now_str >= expires:
            problems.append(
                "delegation expires_utc %s has passed" % expires)
        scope_paths = deleg.get("allowed_scope", {}).get("paths", [])
        for required in ("src/autobot", "src/intelligence", "docs"):
            if required not in scope_paths:
                problems.append(
                    "delegation scope missing required path %r"
                    % required)
        verifier_ids = _admitted_ids(
            self.db, self.store, "VerifierRegistration", "verifier_id")
        for vid in self.REQUIRED_VERIFIER_IDS:
            if vid not in verifier_ids:
                problems.append(
                    "verifier %r not registered in the ledger" % vid)
        transform_ids = _admitted_ids(
            self.db, self.store, "TransformRegistration", "transform_id")
        for tid in self.REQUIRED_TRANSFORM_IDS:
            if tid not in transform_ids:
                problems.append(
                    "transform %r not registered in the ledger" % tid)
        defaults = self.environment.defaults
        for key in ("acceptance_assertions", "allowed_scope",
                    "capability_requirements", "evidence_requirements",
                    "role_transform_id", "retry_policy", "repair_policy",
                    "reuse_policy", "effect_policy"):
            if key not in defaults:
                problems.append("compiler default %r is missing" % key)
        if defaults.get("role_transform_id") != "identity-projection":
            problems.append(
                "role_transform_id is %r, expected 'identity-projection'"
                % defaults.get("role_transform_id"))
        if problems:
            raise ConfigError(
                "bootstrap preflight failed:\n  - "
                + "\n  - ".join(problems))

    # -------------------------------------------------------------- driver

    def _ensure_driver(self, policy):
        if self._driver is not None:
            return self._driver
        self._driver = ContinuousDriver(
            self.plan, self.registry, self.sequencer, self.store,
            self.environment, self.clock, policy, self.transport,
            self.runner, self.pool, self._authority_db_path,
            repo_path=self._repo_path,
            repository_context=self.repository_context,
            holder=self._holder,
            lease_seconds=self._lease_seconds,
            # Production contracts freeze their specialist prompt as a
            # declared specification input (prompts.py), and every
            # specialist — READ_ONLY included — works from a frozen source
            # revision in an isolated worktree.
            freeze_prompts=True,
            isolate_read_only=self.isolate_read_only,
            established_threshold=self.established_threshold,
            gate_root=getattr(self.transport, "_gate_root", None))
        return self._driver

    # -------------------------------------------------------------- public

    def add_objective(self, objective_id, text, authored_by="operator",
                      constraints=None, budget=None):
        """Add one objective (cap-01: yours). ``constraints`` are the
        design constraints stated with it; ``budget`` sets its
        engineering-work budget limits (cap-47), the conservative default
        otherwise."""
        now = instant(self.clock.now())
        self.plan.add_objective(objective_id, text, authored_by, now,
                                constraints=constraints)
        if budget:
            self.plan.set_budget(objective_id, now, set_by=authored_by,
                                 **budget)
        return objective_id

    def set_objective_budget(self, objective_id, budget,
                             set_by="operator"):
        """Change one ACTIVE objective's engineering-work budget (cap-47:
        the limits are yours). The named limits replace the current ones
        and the others stay as they are. The Engineer is told
        (``budget_granted``), so intent that was refused for budget is
        proposed again. Returns the limits now in force."""
        objective = self.plan.objective(objective_id)
        if objective is None:
            raise ConfigError("no objective %r" % (objective_id,))
        if objective["status"] != "ACTIVE":
            raise ConfigError("objective %s is %s: only an active"
                              " objective has a budget to change"
                              % (objective_id, objective["status"]))
        now = instant(self.clock.now())
        limits = dict(self.plan.budget_limits(objective_id))
        try:
            limits.update(budget)
            limits = self.plan.set_budget(objective_id, now, set_by=set_by,
                                          **limits)
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc))
        with self.plan.transaction() as cur:
            self.plan.emit_workforce_event(
                cur, "budget_granted:%s:%s" % (objective_id, now),
                "budget_granted", objective_id, {"limits": limits}, now)
        return limits

    def signal_resume(self):
        """Tell the Engineer you resumed the loop: one workforce event
        per active objective. A loop that went quiet with a decision
        still open (work finished but not declared complete, a workstream
        nobody staffed) reasons again over the current state instead of
        staying quiet. Returns the objectives signalled."""
        now = instant(self.clock.now())
        signalled = []
        objectives = self.plan.active_objectives()
        with self.plan.transaction() as cur:
            for objective in objectives:
                objective_id = objective["objective_id"]
                if self.plan.emit_workforce_event(
                        cur, "operator_resumed:%s:%s" % (objective_id, now),
                        "operator_resumed", objective_id,
                        {"objective_id": objective_id}, now):
                    signalled.append(objective_id)
        return signalled

    def run_until_stable(self, policy, **kwargs):
        driver = self._ensure_driver(policy)
        return driver.run_until_stable(**kwargs)

    def run(self, policy, **kwargs):
        """Run the loop to a stable quiet state, waiting for specialists
        that are still executing (``ContinuousDriver.run``)."""
        driver = self._ensure_driver(policy)
        return driver.run(**kwargs)

    def engineer_policy(self, reasoner=None):
        """The production Engineer policy for this runtime: the reasoning
        policy over the configured model, given the environment facts the
        model should know (plain data only)."""
        from .policy import ClaudeCliReasoner, ReasoningEngineerPolicy
        if reasoner is None:
            if not self.engineer_model:
                raise ConfigError(
                    "no Engineer model is configured: set [engineer]"
                    " model or [models] planner")
            reasoner = ClaudeCliReasoner(self.engineer_model)
        delegation = self.environment.delegation
        context = {
            "repository_id": self.environment.repository_id,
            "canonical_ref": self.environment.declaration.get(
                "canonical_ref"),
            "delegated_scope_paths": delegation.get(
                "allowed_scope", {}).get("paths", []),
            "note": "SOURCE_MODIFYING allowed_paths must lie inside the"
                    " delegated scope; anything outside it is queued for"
                    " the operator instead of executed.",
        }
        return ReasoningEngineerPolicy(reasoner, context=context)

    # The name this method had before the organizational model was
    # aligned.
    chief_engineer_policy = engineer_policy

    def taskforce_report(self):
        """The persistent organization's state report (query layer)."""
        return query.taskforce_report(self.plan)

    def projects_report(self):
        """Every project with its temporary team (query layer)."""
        ro = readonly_view(self._authority_db_path)
        try:
            return query.projects_report(self.plan, ro)
        finally:
            ro.close()

    def status(self):
        """Current state: active objectives, running sequences, lease."""
        objectives = self.plan.active_objectives()
        ro = readonly_view(self._authority_db_path)
        try:
            sequences = [dict(r) for r in ro.execute(
                "SELECT sequence_id, state, contract_digest"
                " FROM sequences WHERE state NOT IN"
                " ('COMPLETED','ABANDONED','REJECTED')"
                " ORDER BY created_utc").fetchall()]
            completed = [dict(r) for r in ro.execute(
                "SELECT sequence_id, state, contract_digest"
                " FROM sequences WHERE state = 'COMPLETED'"
                " ORDER BY updated_utc DESC LIMIT 10").fetchall()]
        finally:
            ro.close()
        return {
            "objectives": objectives,
            "active_sequences": sequences,
            "recent_completed": completed,
        }

    def close(self):
        if self._driver is not None:
            self._driver.close()
            self._driver = None
        self.plan.close()
        self.db.close()
