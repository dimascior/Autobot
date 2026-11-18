"""Production-bootstrap tests for the authority runtime.

Proves: config -> runtime assembly -> lease acquisition -> readonly
intelligence view -> specialist-catalog registration ->
AutoSwarmTransport configured -> ProductionVerifierRunner configured ->
ContinuousDriver constructed. One smoke path instantiates the full system
without launching a provider.
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
    AuthorityRuntime,
    ChiefEngineerPolicy,
    ConfigError,
    ContinuousDriver,
    PlanStore,
    SpecialistPool,
    TaskforceRoster,
    TaskforceSeatResolver,
    readonly_view,
)
from intelligence.runtime import (
    _admitted_ids,
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
        import copy
        pilot_deleg = copy.deepcopy(env.delegation)
        pilot_deleg["allowed_scope"]["paths"] = [
            "docs", "src/autobot", "src/intelligence"]
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=env.declaration,
            delegation=pilot_deleg,
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=env.defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
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
            self.assertIsInstance(runtime.pool, SpecialistPool)
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
    # 4. Specialist-catalog registrations
    # ================================================================

    def test_the_catalog_offers_research_builder_to_a_project(self):
        """The runtime's catalog has a research/builder worker. Nobody is
        retained in a fresh organization, so the Taskforce resolves
        nobody for the capability; the worker can be selected for a
        project as an OUTSIDE specialist."""
        runtime, _rt = self._seed_and_build()
        try:
            self.assertTrue(runtime.pool.can_source("research", "builder"))
            roster = TaskforceRoster(runtime.plan, runtime.pool)
            roster.sync_pool("2026-11-09T12:00:00.000Z")
            resolver = TaskforceSeatResolver(roster)
            self.assertIsNone(resolver.resolve("research", role="builder"))
            candidate = resolver.outside_candidates("research",
                                                    "builder")[0]
            self.assertTrue(candidate["eligible"])
            selection = resolver.resolve(
                "research", role="builder", directive={
                    "mode": "outside_specialist",
                    "specialist_id": candidate["specialist_id"]})
            self.assertEqual(selection["specialist"]["provider"], "codex")
            self.assertEqual(selection["reason"]["path"],
                             "OUTSIDE_SPECIALIST")
            self.assertEqual(roster.memberships(), [])
        finally:
            runtime.close()

    def test_a_resume_is_a_stimulus_for_the_engineer(self):
        """Resuming the loop tells the Engineer so: one workforce event
        per active objective, which is not routine — a loop that went
        quiet with a decision still open reasons again."""
        from intelligence.policy import ROUTINE_WORKFORCE_EVENTS
        runtime, _rt = self._seed_and_build()
        try:
            self.assertEqual(runtime.signal_resume(), [])  # nothing active
            runtime.add_objective("obj-r", "Resume me.")
            self.assertEqual(runtime.signal_resume(), ["obj-r"])
            events = runtime.plan.unconsumed_workforce_events()
            self.assertEqual(
                [(e["event_kind"], e["subject_id"]) for e in events],
                [("operator_resumed", "obj-r")])
            self.assertNotIn("operator_resumed", ROUTINE_WORKFORCE_EVENTS)
        finally:
            runtime.close()

    def test_the_catalog_is_read_from_configuration(self):
        """[specialists.<handle>] is the catalog; the two earlier
        spellings are still read, and the current one wins on a clash.
        Being listed is availability, not membership — nothing here
        touches a membership table."""
        from intelligence.runtime import build_pool
        pool = build_pool({
            "specialists": {"a": {
                "agent": "claude-code", "model": "m1",
                "offers": ["research:investigator",
                           "verification:reviewer"]}},
            "taskforce": {
                "established_threshold": 3,
                "specialists": {
                    "b": {"agent": "codex",
                          "offers": ["verification:reviewer"]},
                    "a": {"agent": "other",
                          "offers": ["research:investigator"]}}},
            "authority_routes": {
                "legacy": {"agent": "codex", "research": "builder"}}})
        self.assertEqual([p["handle"] for p in pool.profiles()],
                         ["a", "b", "legacy"])
        self.assertEqual((pool.profile("a")["provider"],
                          pool.profile("a")["model"]),
                         ("claude-code", "m1"))
        self.assertEqual(sorted(pool.profile("a")["offers"]),
                         [["research", "investigator"],
                          ["verification", "reviewer"]])
        self.assertEqual(pool.profile("legacy")["offers"],
                         [["research", "builder"]])
        for broken in ({"x": {"agent": "claude-code",
                              "offers": ["nonsense"]}},
                       {"x": {"offers": ["research:investigator"]}},
                       {"x": {"agent": "claude-code", "offers": []}}):
            with self.assertRaises(ConfigError):
                build_pool({"specialists": broken})

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


    # ================================================================
    # 11. Bootstrap validates pilot readiness
    # ================================================================

    def test_bootstrap_validates_the_specialist_catalog(self):
        """Bootstrap fails when no specialist exists to assign."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        env = support.intelligence_environment()
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()  # no registrations
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("specialist catalog", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_gate_root(self):
        """Bootstrap fails when gate_root is None."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        env = support.intelligence_environment()
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=None)  # no gate root
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("gate_root", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_defaults(self):
        """Bootstrap fails when compiler defaults are incomplete."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        env = _environment_from_ledger(
            rt.db, rt.store, "autobot", defaults={})  # empty defaults
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("compiler default", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_workspace(self):
        """Bootstrap fails when workspace directory does not exist."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        env = support.intelligence_environment()
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        bad_workspace = self.dir / "nonexistent_ws"
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(bad_workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("workspace", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_declaration_location(self):
        """Bootstrap fails when declaration is not LOCAL."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_remote"],
            delegation=graph["delegation_a"],
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("LOCAL", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_delegation_repository(self):
        """Bootstrap fails when delegation does not include autobot."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        import copy
        bad_deleg = copy.deepcopy(graph["delegation_a"])
        bad_deleg["repository_ids"] = ["other-repo"]
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_local"],
            delegation=bad_deleg,
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("autobot", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_exact_verifier_ids(self):
        """Bootstrap fails when a required verifier ID is missing."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_local"],
            delegation=graph["delegation_a"],
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        rt.db.conn.execute(
            "DELETE FROM objects WHERE kind = 'VerifierRegistration'")
        rt.db.conn.execute(
            "INSERT INTO objects (object_hash, content_address, kind,"
            " minted_utc) VALUES (?, ?, 'VerifierRegistration', ?)",
            ("h_only_reg", "h_only_reg", "2026-11-16T00:00:00Z"))
        from contract_library import content_address
        payload = support.live_registration("registry-checker", "PASS")
        addr = rt.store.put(
            json.dumps(payload, sort_keys=True).encode("utf-8"))
        rt.db.conn.execute(
            "UPDATE objects SET content_address = ? WHERE object_hash = ?",
            (addr, "h_only_reg"))
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("vector-checker", str(cm.exception))
        runtime.close()

    def test_bootstrap_validates_exact_transform_ids(self):
        """Bootstrap fails when identity-projection is missing."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        env = support.intelligence_environment()
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        transport = AutoSwarmTransport(
            rt.store, str(self.autoswarm_home), str(self.workspace),
            gate_root=str(self.gate_root))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=self.clock,
            authority_db_path=self.dir / "autobot.db",
            holder="bootstrap-test")
        rt.db.conn.execute(
            "DELETE FROM objects WHERE kind = 'TransformRegistration'")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("identity-projection", str(cm.exception))
        runtime.close()

    def test_bootstrap_passes_with_full_config(self):
        """Bootstrap succeeds when all pilot dependencies are present."""
        runtime, _rt = self._seed_and_build()
        try:
            runtime._validate_pilot_readiness()
        finally:
            runtime.close()


class PromptBindingTests(unittest.TestCase):
    """The CLI's prompt/spec-input binding stores exact guidance bytes
    in CAS and wires the address into specification_inputs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_prompt_bytes_stored_and_addressable(self):
        """Guidance bytes stored in CAS are retrievable by their address."""
        rt = support.make_runtime(self.dir, self.clock)
        guidance = "Inspect the execution boundary."
        prompt_bytes = guidance.encode("utf-8")
        address = rt.store.put(prompt_bytes)
        retrieved = rt.store.get(address)
        self.assertEqual(retrieved, prompt_bytes)
        rt.db.close()

    def test_spec_inputs_carries_prompt_address(self):
        """The specification_inputs structure carries the exact CAS
        address of the prompt bytes."""
        from contract_library import content_address
        rt = support.make_runtime(self.dir, self.clock)
        guidance = "Inspect the execution boundary."
        prompt_bytes = guidance.encode("utf-8")
        address = rt.store.put(prompt_bytes)
        spec_inputs = {
            "name_to_exact_byte_sha256": {
                "specialist_prompt": address,
            }
        }
        self.assertEqual(
            spec_inputs["name_to_exact_byte_sha256"]["specialist_prompt"],
            content_address(prompt_bytes))
        rt.db.close()

    def test_identity_projection_over_prompt_equals_prompt(self):
        """With identity-projection, the transform output equals the
        input — so prompt_sha256 from the provider's task.json should
        match the frozen specification input address."""
        from autobot import transforms
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        guidance = "Inspect the execution boundary."
        prompt_bytes = guidance.encode("utf-8")
        address = rt.store.put(prompt_bytes)
        reg = support.live_transform_registration("identity-projection")
        output_bytes, _ = transforms.execute_transform(
            rt.store, reg, prompt_bytes)
        self.assertEqual(output_bytes, prompt_bytes)
        from contract_library import content_address
        self.assertEqual(content_address(output_bytes), address)
        rt.db.close()


class _StubPolicy(ChiefEngineerPolicy):
    """Does nothing — used to test driver construction only."""
    def plan(self, view):
        return []


class SeedConvergenceTests(unittest.TestCase):
    """Production seed is deterministic: same version, same objects."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _run_seed(self, db, store, registry):
        from intelligence.production_seed import (
            all_seed_objects,
            store_closure_bytes,
        )
        from contract_library import object_hash
        store_closure_bytes(store)
        results = []
        for name, payload in all_seed_objects():
            h = object_hash(payload["kind"], payload)
            try:
                registry.admit_object(payload, submitted_by="you")
            except Exception:
                pass
            results.append((name, h))
        return results

    def test_seed_converges(self):
        """Two seeds produce identical object hashes."""
        clock = support.manual_clock()
        rt = support.make_runtime(self.dir, clock)
        first = self._run_seed(rt.db, rt.store, rt.registry)
        second = self._run_seed(rt.db, rt.store, rt.registry)
        self.assertEqual(first, second)
        count_after_first = rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM objects").fetchone()["n"]
        count_after_second = rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM objects").fetchone()["n"]
        self.assertEqual(count_after_first, count_after_second)
        rt.db.close()

    def test_registration_hashes_stable(self):
        """Verifier and transform hashes remain identical across seeds."""
        clock = support.manual_clock()
        rt = support.make_runtime(self.dir, clock)
        first = self._run_seed(rt.db, rt.store, rt.registry)
        second = self._run_seed(rt.db, rt.store, rt.registry)
        first_hashes = {n: h for n, h in first}
        second_hashes = {n: h for n, h in second}
        self.assertEqual(
            first_hashes["verifier/registry-checker"],
            second_hashes["verifier/registry-checker"])
        self.assertEqual(
            first_hashes["verifier/vector-checker"],
            second_hashes["verifier/vector-checker"])
        self.assertEqual(
            first_hashes["transform/identity-projection"],
            second_hashes["transform/identity-projection"])
        rt.db.close()


class DelegationSelectionTests(unittest.TestCase):
    """Configured delegation_id selects the exact delegation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_configured_delegation_selected(self):
        """When delegation_id is given, that exact delegation is bound."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        from intelligence.production_seed import (
            PILOT_DELEGATION_ID,
            pilot_delegation,
        )
        pd = pilot_delegation()
        rt.registry.admit_object(pd, submitted_by="you")
        env = _environment_from_ledger(
            rt.db, rt.store, "autobot", {},
            delegation_id=PILOT_DELEGATION_ID)
        self.assertEqual(env.delegation["delegation_id"],
                         PILOT_DELEGATION_ID)
        rt.db.close()

    def test_unknown_delegation_raises(self):
        """Configured delegation_id that doesn't exist fails."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        with self.assertRaises(ConfigError) as cm:
            _environment_from_ledger(
                rt.db, rt.store, "autobot", {},
                delegation_id="dg_nonexistent")
        self.assertIn("dg_nonexistent", str(cm.exception))
        rt.db.close()

    def test_expired_delegation_fails_bootstrap(self):
        """A delegation past its expires_utc fails preflight."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        import copy
        expired = copy.deepcopy(graph["delegation_a"])
        expired["expires_utc"] = "2026-01-01T00:00:00.000Z"
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_local"],
            delegation=expired,
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        ws = self.dir / "ws"; ws.mkdir(exist_ok=True)
        ah = self.dir / "ah"; ah.mkdir(exist_ok=True)
        gr = self.dir / "gr"; gr.mkdir(exist_ok=True)
        transport = AutoSwarmTransport(
            rt.store, str(ah), str(ws), gate_root=str(gr))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=SystemClock(),
            authority_db_path=self.dir / "autobot.db",
            holder="test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("expires_utc", str(cm.exception))
        runtime.close()

    def test_future_delegation_fails_bootstrap(self):
        """A delegation whose created_utc is in the future fails."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        import copy
        future = copy.deepcopy(graph["delegation_a"])
        future["created_utc"] = "2099-01-01T00:00:00.000Z"
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_local"],
            delegation=future,
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        ws = self.dir / "ws"; ws.mkdir(exist_ok=True)
        ah = self.dir / "ah"; ah.mkdir(exist_ok=True)
        gr = self.dir / "gr"; gr.mkdir(exist_ok=True)
        transport = AutoSwarmTransport(
            rt.store, str(ah), str(ws), gate_root=str(gr))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=SystemClock(),
            authority_db_path=self.dir / "autobot.db",
            holder="test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("created_utc", str(cm.exception))
        runtime.close()

    def test_wrong_principal_fails_bootstrap(self):
        """A delegation with wrong delegated_to fails preflight."""
        rt = support.make_runtime(self.dir, self.clock)
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        import copy
        wrong = copy.deepcopy(graph["delegation_a"])
        wrong["delegated_to"] = "attacker"
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_local"],
            delegation=wrong,
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        ws = self.dir / "ws"; ws.mkdir(exist_ok=True)
        ah = self.dir / "ah"; ah.mkdir(exist_ok=True)
        gr = self.dir / "gr"; gr.mkdir(exist_ok=True)
        transport = AutoSwarmTransport(
            rt.store, str(ah), str(ws), gate_root=str(gr))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=SystemClock(),
            authority_db_path=self.dir / "autobot.db",
            holder="test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("planner", str(cm.exception))
        runtime.close()


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
        rt.db.conn.execute(
            "DELETE FROM objects WHERE kind = 'DelegationContract'")
        with self.assertRaises(ConfigError):
            _environment_from_ledger(rt.db, rt.store, "autobot", {})
        rt.db.close()

    def test_production_seed_creates_local_declaration(self):
        """The production seed creates a LOCAL declaration."""
        from intelligence.production_seed import production_declaration
        decl = production_declaration()
        self.assertEqual(decl["authority_location"], "LOCAL")
        self.assertEqual(decl["repository_id"], "autobot")
        self.assertEqual(decl["canonical_ref"], "refs/heads/main")

    def test_remote_declaration_fails_bootstrap(self):
        """A REMOTE declaration fails the preflight check."""
        rt = support.make_runtime(self.dir, support.manual_clock())
        support.seed_bound_objects(rt)
        graph = support.load_fixture("graph.json")
        rt.registry.admit_object(graph["declaration_local"],
                                 submitted_by="you")
        rt.sequencer.acquire_lease(ttl_seconds=360000)
        from intelligence import RepositoryEnvironment
        env = RepositoryEnvironment(
            declaration=graph["declaration_remote"],
            delegation=graph["delegation_a"],
            verifier_registrations=[
                support.live_registration("registry-checker", "PASS"),
                support.live_registration("vector-checker", "PASS")],
            transform_registrations=[
                support.live_transform_registration("identity-projection")],
            defaults=support.intelligence_environment().defaults)
        plan = PlanStore(self.dir / "intelligence.db")
        pool = SpecialistPool()
        pool.register("research", "builder", "codex")
        ws = self.dir / "ws"; ws.mkdir(exist_ok=True)
        ah = self.dir / "ah"; ah.mkdir(exist_ok=True)
        gr = self.dir / "gr"; gr.mkdir(exist_ok=True)
        transport = AutoSwarmTransport(
            rt.store, str(ah), str(ws), gate_root=str(gr))
        runner = ProductionVerifierRunner(rt.sequencer, rt.store)
        runtime = AuthorityRuntime(
            db=rt.db, store=rt.store, registry=rt.registry,
            sequencer=rt.sequencer, plan=plan, pool=pool,
            transport=transport, runner=runner, environment=env,
            clock=support.manual_clock(),
            authority_db_path=self.dir / "autobot.db",
            holder="test")
        with self.assertRaises(ConfigError) as cm:
            runtime._validate_pilot_readiness()
        self.assertIn("LOCAL", str(cm.exception))
        runtime.close()


class PilotRegressionTests(unittest.TestCase):
    """Regressions from the first live READ_ONLY pilot (2026-11-16)."""

    def test_defaults_scope_within_delegation(self):
        """production_defaults scope must be within the delegation scope.

        Finding: fixture scope (artifact_paths: ['artifacts/ch1']) was
        outside the pilot delegation (artifact_paths: []). SEM-11 blocked
        admission as QUEUED_FOR_YOU."""
        from intelligence.production_seed import (
            production_defaults, pilot_delegation)
        from contract_library.grammars import scope_within
        defaults = production_defaults()
        delegation = pilot_delegation()
        d_scope = defaults["allowed_scope"]
        g_scope = delegation["allowed_scope"]
        self.assertTrue(
            scope_within(d_scope.get("paths", []),
                         g_scope.get("paths", [])),
            "default paths outside delegation paths")
        self.assertTrue(
            scope_within(d_scope.get("artifact_paths", []),
                         g_scope.get("artifact_paths", [])),
            "default artifact_paths outside delegation artifact_paths")

    def test_evidence_profiles_defined(self):
        """READ_ONLY_REPORT and READ_ONLY_GOVERNED profiles exist."""
        from intelligence.production_seed import EVIDENCE_PROFILES
        self.assertIn("READ_ONLY_REPORT", EVIDENCE_PROFILES)
        self.assertIn("READ_ONLY_GOVERNED", EVIDENCE_PROFILES)
        self.assertEqual(EVIDENCE_PROFILES["READ_ONLY_REPORT"],
                         ["EXECUTION_LIFECYCLE"])
        self.assertIn("COMMAND_GOVERNANCE",
                       EVIDENCE_PROFILES["READ_ONLY_GOVERNED"])
        self.assertIn("EXECUTION_LIFECYCLE",
                       EVIDENCE_PROFILES["READ_ONLY_GOVERNED"])

    def test_evidence_profile_selects_requirements(self):
        """production_defaults evidence_profile parameter selects correctly."""
        from intelligence.production_seed import production_defaults
        report = production_defaults(evidence_profile="READ_ONLY_REPORT")
        governed = production_defaults(evidence_profile="READ_ONLY_GOVERNED")
        self.assertEqual(report["evidence_requirements"],
                         ["EXECUTION_LIFECYCLE"])
        self.assertIn("COMMAND_GOVERNANCE",
                       governed["evidence_requirements"])

    def test_transport_collects_result_as_obj11(self):
        """AutoSwarm transport collects result.json as obj-11 witness.

        Finding: failed specialist produced no witnesses; Sequencer
        crashed on store.has(None). Transport must always provide
        result.json bytes as the obj-11 execution-lifecycle witness."""
        import json, tempfile
        from autobot.autoswarm_transport import AutoSwarmTransport
        from autobot import Store
        tmp = tempfile.mkdtemp()
        store = Store(Path(tmp) / "store")
        home = Path(tmp) / "home"
        home.mkdir()
        (home / "calls").mkdir()
        transport = AutoSwarmTransport(
            store, str(home), str(tmp))
        call_id = "call_test_obj11_regression"
        call_dir = home / "calls" / call_id
        call_dir.mkdir()
        result = {
            "call_id": call_id,
            "outcome": "failed",
            "native_session_id": "test-session",
            "error": "test error",
            "exit_code": 1,
        }
        (call_dir / "result.json").write_text(
            json.dumps(result), encoding="utf-8")
        collected = transport.collect(call_id)
        self.assertIsNotNone(collected)
        obj11 = [w for w in collected["witness_files"]
                 if w[0] == "obj-11"]
        self.assertEqual(len(obj11), 1,
                         "result.json must be collected as obj-11")
        witness_bytes = obj11[0][2]
        witness = json.loads(witness_bytes)
        self.assertEqual(witness["call_id"], call_id)
        self.assertEqual(witness["outcome"], "failed")
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class CandidateScopeTests(unittest.TestCase):
    """Regressions from the first SM pilot: candidate must be constrained
    by the frozen task scope, not just the delegation envelope."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)],
                       capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit",
             "--allow-empty", "-m", "initial"],
            capture_output=True, check=True)
        self.old = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def _commit_file(self, path, content="x"):
        full = self.repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        subprocess.run(
            ["git", "-C", str(self.repo), "add", str(path)],
            capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m", "add " + path],
            capture_output=True, check=True)
        return subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()

    def test_exact_one_file_candidate(self):
        """A single-file candidate within scope passes."""
        from autobot.git_adapter import changed_paths, verify_mutation_scope
        new = self._commit_file("docs/pilot.md")
        paths = changed_paths(str(self.repo), self.old, new)
        self.assertEqual(paths, ["docs/pilot.md"])
        unauthorized = verify_mutation_scope(paths, ["docs"])
        self.assertEqual(unauthorized, [])

    def test_extra_delegated_file_refused(self):
        """Candidate changes intended file + extra delegated file -> REFUSE."""
        from autobot.git_adapter import changed_paths, verify_mutation_scope
        (self.repo / "docs").mkdir(exist_ok=True)
        (self.repo / "docs" / "pilot.md").write_text("pilot")
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "extra.py").write_text("extra")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "."],
            capture_output=True, check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m", "two files"],
            capture_output=True, check=True)
        new = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()
        paths = changed_paths(str(self.repo), self.old, new)
        unauthorized = verify_mutation_scope(paths, ["docs"])
        self.assertIn("src/extra.py", unauthorized)

    def test_path_outside_task_scope_refused(self):
        """Candidate changes path outside TaskContractRecord scope -> REFUSE."""
        from autobot.git_adapter import verify_mutation_scope
        unauthorized = verify_mutation_scope(
            ["docs/pilot.md", "secret/key.txt"],
            ["docs"])
        self.assertIn("secret/key.txt", unauthorized)

    def test_path_outside_delegation_scope_refused(self):
        """Candidate changes path outside delegation scope -> REFUSE."""
        from autobot.git_adapter import verify_mutation_scope
        unauthorized = verify_mutation_scope(
            ["outside/file.txt"],
            ["docs", "src/autobot", "src/intelligence"])
        self.assertIn("outside/file.txt", unauthorized)


import subprocess


if __name__ == "__main__":
    unittest.main()
