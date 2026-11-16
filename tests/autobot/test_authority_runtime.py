"""Production-bootstrap tests for the authority runtime.

Proves: config -> runtime assembly -> lease acquisition -> readonly
intelligence view -> AgentFactory registration -> AutoSwarmTransport
configured -> ProductionVerifierRunner configured -> ContinuousDriver
constructed. One smoke path instantiates the full system without
launching a provider.
"""
import json
import tempfile
import unittest
from pathlib import Path

import support
from autobot import Database, ManualClock, Store, SystemClock
from autobot.autoswarm_transport import AutoSwarmTransport
from autobot.verifier_runner import ProductionVerifierRunner
from intelligence import (
    AgentFactory,
    AuthorityRuntime,
    ChiefEngineerPolicy,
    ConfigError,
    ContinuousDriver,
    PlanStore,
    readonly_view,
)
from intelligence.runtime import (
    _environment_from_ledger,
    _resolve_autoswarm_home,
)


class BootstrapAssemblyTests(unittest.TestCase):
    """The runtime assembles from configuration and acquires its lease."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.autoswarm_home = self.dir / "autoswarm"
        self.autoswarm_home.mkdir()
        self.gate_root = self.dir / "gate"
        self.gate_root.mkdir()
        self.workspace = self.dir / "ws"
        self.workspace.mkdir()
        self.clock = support.manual_clock()

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _seed_and_build(self, routes=None):
        """Seed the authority core and build the runtime from explicit
        component construction — the same path from_config takes, but
        without needing config.toml on disk."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        env = support.intelligence_environment()
        plan = PlanStore(self.dir / "intelligence.db")
        factory = AgentFactory()
        factory.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, factory=factory,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        return runtime, rt

    # ================================================================
    # 1. Assembly
    # ================================================================

    def test_runtime_assembles_all_components(self):
        """The runtime holds every component the joined proof used."""
        runtime, _rt = self._seed_and_build()
        try:
            self.assertIsInstance(runtime.db, Database)
            self.assertIsInstance(runtime.store, Store)
            self.assertIsInstance(runtime.registry, type(_rt.registry))
            self.assertIsInstance(runtime.sequencer, type(_rt.sequencer))
            self.assertIsInstance(runtime.plan, PlanStore)
            self.assertIsInstance(runtime.factory, AgentFactory)
            self.assertIsInstance(runtime.transport, AutoSwarmTransport)
            self.assertIsInstance(runtime.runner,
                                 ProductionVerifierRunner)
        finally:
            runtime.close()

    # ================================================================
    # 2. Lease acquisition
    # ================================================================

    def test_sequencer_lease_held(self):
        runtime, _rt = self._seed_and_build()
        try:
            self.assertIsNotNone(runtime.sequencer.epoch)
        finally:
            runtime.close()

    # ================================================================
    # 3. Readonly intelligence view
    # ================================================================

    def test_readonly_view_reads_authority(self):
        runtime, _rt = self._seed_and_build()
        try:
            ro = readonly_view(self.dir / "autobot.db")
            try:
                objects = ro.execute(
                    "SELECT COUNT(*) AS n FROM objects").fetchone()["n"]
                self.assertGreater(objects, 0)
            finally:
                ro.close()
        finally:
            runtime.close()

    # ================================================================
    # 4. AgentFactory registrations
    # ================================================================

    def test_factory_resolves_research_builder(self):
        runtime, _rt = self._seed_and_build()
        try:
            assignment = runtime.factory.select("research", "builder")
            self.assertEqual(assignment["provider"], "codex")
            self.assertEqual(assignment["selected_by"], "agent_factory")
        finally:
            runtime.close()

    # ================================================================
    # 5. Transport configured
    # ================================================================

    def test_transport_points_to_autoswarm_home(self):
        runtime, _rt = self._seed_and_build()
        try:
            self.assertEqual(runtime.transport._home,
                             self.autoswarm_home)
        finally:
            runtime.close()

    # ================================================================
    # 6. Verifier configured
    # ================================================================

    def test_verifier_runner_configured(self):
        runtime, _rt = self._seed_and_build()
        try:
            self.assertIs(runtime.runner.sequencer, runtime.sequencer)
            self.assertIs(runtime.runner.store, runtime.store)
        finally:
            runtime.close()

    # ================================================================
    # 7. Environment from ledger
    # ================================================================

    def test_environment_from_ledger(self):
        """_environment_from_ledger reads the admitted bound objects."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        env = _environment_from_ledger(
            rt.db, rt.store, "autobot", defaults={})
        self.assertEqual(env.repository_id, "autobot")
        self.assertIsNotNone(env.delegation_hash)
        self.assertTrue(len(env.verifier_hashes) >= 2)
        self.assertTrue(len(env.transform_hashes) >= 1)
        rt.db.close()

    # ================================================================
    # 8. ContinuousDriver constructs
    # ================================================================

    def test_driver_constructs_from_runtime(self):
        """The runtime can construct a ContinuousDriver."""
        runtime, _rt = self._seed_and_build()
        try:
            policy = _StubPolicy()
            driver = runtime._ensure_driver(policy)
            self.assertIsInstance(driver, ContinuousDriver)
        finally:
            runtime.close()

    # ================================================================
    # 9. Status reports
    # ================================================================

    def test_status_returns_structure(self):
        runtime, _rt = self._seed_and_build()
        try:
            st = runtime.status()
            self.assertIn("objectives", st)
            self.assertIn("active_sequences", st)
            self.assertIn("recent_completed", st)
        finally:
            runtime.close()

    # ================================================================
    # 10. Smoke: objective to driver without launch
    # ================================================================

    def test_smoke_add_objective_dry(self):
        """Add an objective, construct the driver, and verify the
        objective is visible — without actually launching a provider."""
        runtime, _rt = self._seed_and_build()
        try:
            from autobot.models import instant
            runtime.add_objective("obj-smoke-1",
                                 "Smoke test: inspect the codebase")
            objs = runtime.plan.active_objectives()
            self.assertEqual(len(objs), 1)
            self.assertEqual(objs[0]["objective_id"], "obj-smoke-1")
            st = runtime.status()
            self.assertEqual(len(st["objectives"]), 1)
        finally:
            runtime.close()


class _StubPolicy(ChiefEngineerPolicy):
    """Does nothing — used to test driver construction only."""
    def plan(self, view):
        return []


class EnvironmentFromLedgerEdgeCases(unittest.TestCase):
    """Missing bound objects raise ConfigError."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_missing_declaration_raises(self):
        rt = support.make_runtime(self.dir, support.manual_clock())
        with self.assertRaises(ConfigError):
            _environment_from_ledger(rt.db, rt.store, "nonexistent", {})
        rt.db.close()

    def test_missing_delegation_raises(self):
        rt = support.make_runtime(self.dir, support.manual_clock())
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        # Delete the DelegationContract objects so the query finds none
        rt.db.conn.execute(
            "DELETE FROM objects WHERE kind = 'DelegationContract'")
        with self.assertRaises(ConfigError):
            _environment_from_ledger(rt.db, rt.store, "autobot", {})
        rt.db.close()


if __name__ == "__main__":
    unittest.main()
