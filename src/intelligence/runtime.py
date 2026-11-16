"""The production authority runtime: assembly and lifecycle.

Constructs the complete ContinuousDriver stack from configuration,
acquiring leases and wiring every component the joined lifecycle proof
exercised — Database, Store, Registry, Sequencer, PlanStore,
AgentFactory, AutoSwarmTransport, ProductionVerifierRunner and the
RepositoryEnvironment — into a single object whose public surface is:

    runtime = AuthorityRuntime.from_config(config)
    runtime.add_objective(...)
    runtime.run_until_stable()
    runtime.status()
    runtime.close()

No new semantics: every component is the same instance the deterministic
proof already exercised. The runtime's job is assembly and lifecycle
management — connecting configuration to constructors.
"""

import json
import os
import sys

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

from pathlib import Path

from autobot import Database, Registry, Sequencer, Store, SystemClock, instant
from autobot.autoswarm_transport import AutoSwarmTransport
from autobot.verifier_runner import ProductionVerifierRunner

from .compiler import RepositoryEnvironment
from .driver import ContinuousDriver
from .planstore import PlanStore, readonly_view
from .roles import AgentFactory


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


def _environment_from_ledger(db, store, repository_id, defaults):
    """Build the RepositoryEnvironment from the admitted objects already
    in the authority database. The runtime reads the ledger it governs
    rather than maintaining a second source of bound-object payloads."""
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


class AuthorityRuntime:
    """The assembled production authority runtime.

    All components are the same classes the joined lifecycle proof
    exercised: Database, Store, Registry, Sequencer, PlanStore,
    AgentFactory, AutoSwarmTransport, ProductionVerifierRunner,
    RepositoryEnvironment, ContinuousDriver. The runtime only
    connects configuration to constructors."""

    def __init__(self, *, db, store, registry, sequencer, plan,
                 factory, transport, runner, environment, clock,
                 authority_db_path, repo_path=None,
                 holder="autobot-authority",
                 lease_seconds=3600):
        self.db = db
        self.store = store
        self.registry = registry
        self.sequencer = sequencer
        self.plan = plan
        self.factory = factory
        self.transport = transport
        self.runner = runner
        self.environment = environment
        self.clock = clock
        self._authority_db_path = authority_db_path
        self._repo_path = repo_path
        self._holder = holder
        self._lease_seconds = lease_seconds
        self._driver = None

    @classmethod
    def from_config(cls, project_root=None, config=None,
                    defaults=None, clock=None):
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
        routes_section = file_config.get("authority_routes",
                                         file_config.get("routes", {}))

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

        # --- agent factory ---
        factory = AgentFactory()
        for route_name, route_config in routes_section.items():
            if not isinstance(route_config, dict):
                continue
            agent = route_config.get("agent", route_name)
            for capability, role_map in route_config.items():
                if capability in ("agent", "model", "provider_args",
                                  "workspace", "remote_control",
                                  "effort"):
                    continue
                if isinstance(role_map, str):
                    factory.register(capability, role_map, agent)
                elif isinstance(role_map, dict):
                    for role, provider in role_map.items():
                        factory.register(capability, role, provider)

        # --- transport ---
        transport = AutoSwarmTransport(
            store, str(autoswarm_home), workspace,
            gate_root=str(gate_root) if gate_root else None,
            session_export_dir=session_export_dir)

        # --- verifier ---
        runner = ProductionVerifierRunner(sequencer, store)

        # --- environment ---
        if defaults is None:
            defaults = authority_section.get("defaults", {})
        environment = _environment_from_ledger(
            db, store, repository_id, defaults)

        return cls(
            db=db, store=store, registry=registry, sequencer=sequencer,
            plan=plan, factory=factory, transport=transport, runner=runner,
            environment=environment, clock=clock,
            authority_db_path=authority_db_path, repo_path=repo_path,
            holder=holder, lease_seconds=lease_ttl)

    @classmethod
    def bootstrap(cls, project_root=None, config=None,
                  defaults=None, clock=None):
        """Construct the runtime, validate every pilot dependency, and
        return the runtime ready for add_objective / run_until_stable.

        Raises ConfigError if any required component is missing or
        incomplete: research/builder seat, autoswarm_home, workspace,
        gate_root, repository declaration, delegation, at least two
        verifier registrations, identity-projection transform, and the
        compiler defaults needed to freeze a READ_ONLY pilot contract."""
        rt = cls.from_config(project_root=project_root, config=config,
                             defaults=defaults, clock=clock)
        try:
            rt._validate_pilot_readiness()
        except ConfigError:
            rt.close()
            raise
        return rt

    def _validate_pilot_readiness(self):
        """Check every dependency the first READ_ONLY pilot requires."""
        problems = []
        try:
            self.factory.select("research", "builder")
        except Exception:
            problems.append("no research/builder seat registered"
                            " in AgentFactory")
        if self.transport._gate_root is None:
            problems.append("gate_root is None — governance evidence"
                            " collection will be skipped")
        if not self.transport._home.is_dir():
            problems.append("autoswarm_home %s does not exist"
                            % self.transport._home)
        if len(self.environment.verifier_hashes) < 2:
            problems.append("fewer than 2 verifier registrations"
                            " in the ledger (need a1 + a2)")
        if len(self.environment.transform_hashes) < 1:
            problems.append("no transform registrations in the ledger"
                            " (need identity-projection)")
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
            self.runner, self.factory, self._authority_db_path,
            repo_path=self._repo_path, holder=self._holder,
            lease_seconds=self._lease_seconds)
        return self._driver

    # -------------------------------------------------------------- public

    def add_objective(self, objective_id, text, authored_by="operator"):
        now = instant(self.clock.now())
        return self.plan.add_objective(objective_id, text,
                                       authored_by, now)

    def run_until_stable(self, policy, **kwargs):
        driver = self._ensure_driver(policy)
        return driver.run_until_stable(**kwargs)

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
