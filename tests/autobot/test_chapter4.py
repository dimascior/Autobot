"""C4-M1 conformance: production verification authority.

The registered chapter-4 runtime proofs this module closes:
- CH4-llm-output-never-pass (INV-06): no prose, transcript claim, crash or
  model output ever becomes PASS — only the typed conclusion of the exact
  executed registered closure, under the registered result protocol;
- CH4-mandatory-assertions-required (INV-07): t-s10 fires only when every
  frozen assertion has an authoritative qualifying result — one PASS never
  masks a missing one, duplicates and cross-assertion results satisfy
  nothing;
- CH4-verification-binding (INV-17): every obj-19/obj-20 binds the exact
  candidate, environment, assertion part, verifier registration and
  Attempt, each axis attacked independently;
- CH4-evidence-not-reused (INV-18): a receipt minted for one attempt is
  inert for every other attempt and fence.

Plus the production planes those proofs stand on: the git_adapter's
obj-16 (READ_ONLY shape, SOURCE_MODIFYING tree binding, freeze-admission
negatives, candidate immutability under workspace drift), the
environment_probe's obj-18 (full D-56 constraint vocabulary, named-only
capture, one immutable capture per attempt), the D-57/INV-53/INV-66
closure discipline (missing leaf, interpreter identity, packaged
closures, no PATH substitution), the obj-19/obj-20 canonical schemas with
their byte-identical chapter mirrors, t-s10/t-s11/t-s12 over production
receipts, and the 8h recovery pairing with the crash-matrix resume
points.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import support
from fake_verifier import bound_receipt_bytes, environment_bytes
from autobot import ProductionVerifierRunner, mint_candidate_receipt
from autobot import verification_receipts
from autobot.environment_probe import (
    capture_environment,
    constraint_names,
    interpreter_identity,
)
from autobot.git_adapter import build_candidate_payload, capture_tree
from autobot.models import ConflictError, IntegrityHalt
from autobot.verification import environment_satisfies
from autobot.verifier_runner import resolve_closure
from contract_library import canonicalize, content_address, object_hash

CH4_SCHEMAS = ("candidate_receipt.schema.json",
               "verification_receipt.schema.json",
               "verification_failure_receipt.schema.json")


class Chapter4TestCase(unittest.TestCase):
    """A runtime with the live verification plane seeded and one admitted
    live-bound SL record; helpers drive the production evidence plane."""

    record_kwargs = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clock = support.manual_clock()
        self.rt, _ = support.admitted_runtime(self.dir, clock=self.clock)
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["declaration_local"],
                                      submitted_by="you")
        self.admitted = self.rt.registry.admit_payload(
            support.local_record_payload(**self.record_kwargs),
            submitted_by="planner")
        self.assertEqual(self.admitted["outcome"], "NEW_RECORD")
        self.sequence_id = self.admitted["sequence_id"]
        self.seq = self.rt.sequencer
        self.seq.acquire_lease(ttl_seconds=360000)

    def tearDown(self):
        self.rt.db.close()
        self.tmp.cleanup()

    def _runner(self, **kwargs):
        return ProductionVerifierRunner(self.seq, self.rt.store, **kwargs)

    def _to_evidence_complete(self, admitted=None):
        info = support.drive_to_evidence_complete(
            self.rt, admitted or self.admitted)
        self.attempt_id = info["attempt_id"]
        self.fence = info["fence"]
        self.attempt_contract_hash = info["attempt_contract_hash"]
        return info

    def _attempt_state(self):
        return self.rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["state"]

    def _sequence_state(self, sequence_id=None):
        return self.rt.db.conn.execute(
            "SELECT state, repairs_used FROM sequences WHERE sequence_id"
            " = ?", (sequence_id or self.sequence_id,)).fetchone()

    def _journal_count(self):
        return self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def _rules(self):
        return [json.loads(r["payload"])["rule"] for r in
                self.rt.db.conn.execute(
                    "SELECT payload FROM transition_journal ORDER BY seq"
                ).fetchall()]

    def _candidate_row(self):
        return self.rt.db.conn.execute(
            "SELECT * FROM attempt_candidates WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()

    def _environment_row(self):
        return self.rt.db.conn.execute(
            "SELECT * FROM attempt_environments WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()

    def _verification_rows(self):
        return [dict(r) for r in self.rt.db.conn.execute(
            "SELECT * FROM attempt_verifications WHERE attempt_id = ?"
            " ORDER BY assertion_id", (self.attempt_id,)).fetchall()]


class SchemaAndRegistryPinningTests(Chapter4TestCase):
    def test_chapter4_schema_mirrors_are_byte_identical(self):
        """The D-65 mirror discipline extended to chapter 4: the runtime
        schemas equal the chapter-4 provenance copies byte for byte."""
        chapter4 = support.REPO / "docs" / "architecture" / "chapter4"
        mirror_dir = support.REPO / "src" / "autobot" / "schemas_chapter4"
        for name in CH4_SCHEMAS:
            self.assertEqual(
                (chapter4 / "schemas" / name).read_bytes(),
                (mirror_dir / name).read_bytes(),
                "chapter-4 schema mirror drift in %s" % name)

    def test_minters_pin_the_chapter0_registry(self):
        """obj-16/18/19/20 minters and attempt binding, exactly as
        objects.json registers them; the schema consts enforce the same
        minters on every payload."""
        entries = support.load_json(
            support.CH0_REGISTRIES / "objects.json")["objects"]
        by_id = {o["id"]: o for o in entries}
        self.assertEqual(by_id["obj-16"]["minter"], "git_adapter")
        self.assertEqual(by_id["obj-18"]["minter"], "environment_probe")
        self.assertEqual(by_id["obj-19"]["minter"], "verifier_runner")
        self.assertEqual(by_id["obj-20"]["minter"], "verifier_runner")
        for obj in ("obj-16", "obj-18", "obj-19", "obj-20"):
            self.assertTrue(by_id[obj]["attempt_bound"])
        self.assertEqual(
            verification_receipts.candidate_schema()["properties"][
                "minter"]["const"], "git_adapter")
        # C4-M2: obj-19/obj-20 are oneOf arms; EVERY arm pins the
        # registered minter, and the arm vocabulary is exactly the
        # registered result sources (obj-19 adds BY_ATTESTATION).
        obj19 = verification_receipts.verification_receipt_schema()
        obj20 = verification_receipts.failure_receipt_schema()
        for schema in (obj19, obj20):
            for arm in schema["oneOf"]:
                self.assertEqual(arm["properties"]["minter"]["const"],
                                 "verifier_runner")
        self.assertEqual(
            {arm["properties"]["result_source"]["const"]
             for arm in obj19["oneOf"]},
            {"BY_VERIFIER", "BY_ATTESTATION"})
        self.assertEqual(
            {arm["properties"]["result_source"]["const"]
             for arm in obj20["oneOf"]},
            {"BY_VERIFIER"})

    def test_registered_capabilities_forbid_the_taskforce(self):
        """cap-32 and cap-35 FORBID task_force; the intelligence plane's
        source never constructs a chapter-4 receipt payload — it calls the
        registered seats (the F-C4 Taskforce-minting axes)."""
        caps = support.load_json(
            support.CH0_REGISTRIES / "capabilities.json")["capabilities"]
        by_id = {c["id"]: c for c in caps}
        self.assertEqual(by_id["cap-32"]["owner"], "git_adapter")
        self.assertIn("task_force", by_id["cap-32"]["forbidden"])
        self.assertEqual(by_id["cap-35"]["owner"], "verifier_runner")
        self.assertIn("task_force", by_id["cap-35"]["forbidden"])
        intelligence_dir = support.REPO / "src" / "intelligence"
        for path in sorted(intelligence_dir.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for token in ('"kind": "CandidateReceipt"',
                          '"kind": "VerificationReceipt"',
                          '"kind": "VerificationFailureReceipt"',
                          "build_result_payload",
                          "build_candidate_payload"):
                self.assertNotIn(
                    token, source,
                    "%s constructs a chapter-4 proof object (%s): the"
                    " task_force is forbidden cap-32/cap-35"
                    % (path.name, token))

    def test_no_mtime_or_path_selection_in_the_chapter4_plane(self):
        """INV-04/INV-22 carried into chapter 4: candidate identity and
        verifier selection never read mtime or pick 'latest' files — the
        chapter-4 modules are source-blind to those tokens."""
        autobot_dir = support.REPO / "src" / "autobot"
        for name in ("git_adapter.py", "environment_probe.py",
                     "verifier_runner.py", "verification_receipts.py"):
            source = (autobot_dir / name).read_text(encoding="utf-8")
            # API tokens, not prose: the docstrings legitimately NAME the
            # forbidden facts while forbidding them.
            for token in ("getmtime", "st_mtime", ".stat(", "glob(",
                          "listdir"):
                self.assertNotIn(token, source,
                                 "%s consults the filesystem's clock or"
                                 " listing (%s)" % (name, token))


class CandidateReceiptTests(Chapter4TestCase):
    def test_source_modifying_binds_the_exact_tree(self):
        """cap-42: the bound candidate tree IS what Git says the tree-ish
        peels to; minting without the repository is refused."""
        self._to_evidence_complete()
        row = self._candidate_row()
        repo, _sha_old, sha_new = support.ensure_candidate_repo(self.rt)
        self.assertEqual(row["candidate_tree"],
                         capture_tree(repo, sha_new))
        self.assertEqual(row["finalization_class"], "SOURCE_MODIFYING")
        payload = json.loads(self.rt.store.get(
            row["content_sha256"]).decode("utf-8"))
        self.assertEqual(payload["candidate_tree"]["git_tree_sha"],
                         row["candidate_tree"])
        self.assertEqual(payload["minter"], "git_adapter")

    def test_source_modifying_without_tree_source_is_refused(self):
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="SM without a tree"),
            submitted_by="planner")
        self.seq.mark_ready(admitted["sequence_id"])
        info = self.seq.start_execution(admitted["sequence_id"])
        self.seq.reserve_attempt(info["attempt_id"], info["fence"])
        projection = self.rt.store.put(b"projection\n")
        dispatched = self.seq.dispatch_attempt(info["attempt_id"],
                                               "builder", "fake",
                                               projection)
        start = self.seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        self.seq.attempt_running(info["attempt_id"], "native-sess-9",
                                 start["content_address"])
        self.seq.admit_witness(info["attempt_id"], "obj-12", "helios",
                               b"governance\n")
        end = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                     "agent_clis", b"transcript\n")
        self.seq.finish_attempt(info["attempt_id"], "builder", "success",
                                end["content_address"])
        with self.assertRaises(ConflictError) as ctx:
            mint_candidate_receipt(self.seq, self.rt.store,
                                   info["attempt_id"])
        self.assertIn("tree", str(ctx.exception))

    def test_freeze_admission_refuses_forged_bindings(self):
        """Every ledger-derivable obj-16 member is validated at the freeze:
        a forged attempt identity, fence, contract hash, record tier,
        class, source state or artifact set is refused with nothing
        committed."""
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Freeze negatives"),
            submitted_by="planner")
        self.seq.mark_ready(admitted["sequence_id"])
        info = self.seq.start_execution(admitted["sequence_id"])
        self.seq.reserve_attempt(info["attempt_id"], info["fence"])
        projection = self.rt.store.put(b"projection\n")
        dispatched = self.seq.dispatch_attempt(info["attempt_id"],
                                               "builder", "fake",
                                               projection)
        start = self.seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        self.seq.attempt_running(info["attempt_id"], "native-sess-9",
                                 start["content_address"])
        self.seq.admit_witness(info["attempt_id"], "obj-12", "helios",
                               b"governance\n")
        end = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                     "agent_clis", b"transcript\n")
        self.seq.finish_attempt(info["attempt_id"], "builder", "success",
                                end["content_address"])
        # one admitted provenance event, so an emptied artifact set IS a
        # forgery against the ledger
        self.seq.admit_artifact(info["attempt_id"],
                                "artifact/engineering-notes",
                                b"forgery-test artifact\n")
        repo, _sha_old, sha_new = support.ensure_candidate_repo(self.rt)
        state = self.seq.candidate_state(info["attempt_id"])
        tree = capture_tree(repo, sha_new)
        good = build_candidate_payload(state, self.clock, tree)

        def freeze(mutate):
            payload = json.loads(json.dumps(good))
            mutate(payload)
            address = self.rt.store.put(canonicalize(payload))
            before = self._journal_count()
            with self.assertRaises(ConflictError):
                self.seq.freeze_candidate(info["attempt_id"],
                                          info["fence"], address)
            self.assertEqual(self._journal_count(), before)
            self.assertIsNone(self.rt.db.conn.execute(
                "SELECT 1 FROM attempt_candidates WHERE attempt_id = ?",
                (info["attempt_id"],)).fetchone())

        freeze(lambda p: p["attempt"].update(attempt_id="at_" + "0" * 32))
        freeze(lambda p: p["attempt"].update(fence=p["attempt"]["fence"] + 1))
        freeze(lambda p: p.update(attempt_contract_hash="sha256:" + "0" * 64))
        freeze(lambda p: p.update(record_hash="sha256:" + "1" * 64))
        freeze(lambda p: p.update(finalization_class="READ_ONLY"))
        freeze(lambda p: p["source_state"].update(repository_id="forged"))
        freeze(lambda p: p.update(artifacts=[]))
        freeze(lambda p: p.pop("candidate_tree"))
        # non-canonical bytes of an otherwise valid payload
        address = self.rt.store.put(json.dumps(good, indent=2,
                                               sort_keys=True
                                               ).encode("utf-8"))
        with self.assertRaises(ConflictError) as ctx:
            self.seq.freeze_candidate(info["attempt_id"], info["fence"],
                                      address)
        self.assertIn("canonical", str(ctx.exception))
        # the untouched payload freezes
        self.seq.freeze_candidate(
            info["attempt_id"], info["fence"],
            self.rt.store.put(canonicalize(good)))

    def test_candidate_is_immutable_one_per_attempt(self):
        self._to_evidence_complete()
        first = self._candidate_row()
        with self.assertRaises(ConflictError):
            # the state gate alone forecloses a second freeze
            self.seq.freeze_candidate(self.attempt_id, self.fence,
                                      first["content_sha256"])
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_candidates"
            " WHERE attempt_id = ?", (self.attempt_id,)).fetchone()["n"], 1)


class ReadOnlyCandidateTests(Chapter4TestCase):
    record_kwargs = {"finalization_class": "READ_ONLY",
                     "finalization_requirements": []}

    def test_read_only_candidate_has_no_invented_tree(self):
        """The READ_ONLY candidate is the artifact/observation set: no
        candidate tree exists, minting WITH a repository is refused, and
        the frozen payload carries the admitted artifact triples."""
        self._to_evidence_complete()
        row = self._candidate_row()
        self.assertEqual(row["finalization_class"], "READ_ONLY")
        self.assertIsNone(row["candidate_tree"])
        payload = json.loads(self.rt.store.get(
            row["content_sha256"]).decode("utf-8"))
        self.assertNotIn("candidate_tree", payload)
        self.assertEqual(
            [a["logical_path"] for a in payload["artifacts"]],
            ["artifact/engineering-notes"])
        runner = self._runner()
        outcome = runner.verify_attempt(self.attempt_id)
        self.assertFalse(outcome["inconclusive"])
        self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(self._sequence_state()["state"], "VERIFIED")

    def test_read_only_mint_refuses_a_repo(self):
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(
                objective="RO with a repo refused",
                finalization_class="READ_ONLY",
                finalization_requirements=[]),
            submitted_by="planner")
        self.seq.mark_ready(admitted["sequence_id"])
        info = self.seq.start_execution(admitted["sequence_id"])
        self.seq.reserve_attempt(info["attempt_id"], info["fence"])
        projection = self.rt.store.put(b"projection\n")
        dispatched = self.seq.dispatch_attempt(info["attempt_id"],
                                               "builder", "fake",
                                               projection)
        start = self.seq.admit_witness(
            info["attempt_id"], "obj-11", "autoswarm",
            support.session_record_bytes(dispatched["call_id"]))
        self.seq.attempt_running(info["attempt_id"], "native-sess-9",
                                 start["content_address"])
        self.seq.admit_witness(info["attempt_id"], "obj-12", "helios",
                               b"governance\n")
        end = self.seq.admit_witness(info["attempt_id"], "obj-13",
                                     "agent_clis", b"transcript\n")
        self.seq.finish_attempt(info["attempt_id"], "builder", "success",
                                end["content_address"])
        repo, _sha_old, sha_new = support.ensure_candidate_repo(self.rt)
        with self.assertRaises(ConflictError):
            mint_candidate_receipt(self.seq, self.rt.store,
                                   info["attempt_id"], repo_path=repo,
                                   treeish=sha_new)


class CandidateImmutabilityTests(Chapter4TestCase):
    def test_workspace_drift_cannot_reach_the_verified_subject(self):
        """The chapter's major adversarial proof: candidate captured →
        repository and workspace change → the verifier still verifies the
        BOUND candidate. The live verifier re-hashes every subject
        artifact against its content address, so any drift into the
        subject would FAIL — PASS proves the runner materialized the
        frozen bytes; the receipts bind the original obj-16, whose tree
        is no longer the repository's head."""
        self._to_evidence_complete()
        row = self._candidate_row()
        frozen_tree = row["candidate_tree"]
        repo, _sha_old, _sha_new = support.ensure_candidate_repo(self.rt)
        # drift: the workspace gains a new commit and main moves
        (Path(repo) / "drift.txt").write_text("drifted\n", encoding="utf-8")
        import subprocess
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "main"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "add", "drift.txt"],
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        "workspace drift"], capture_output=True)
        drifted_tree = capture_tree(repo, "main")
        self.assertNotEqual(frozen_tree, drifted_tree)
        outcome = self._runner().verify_attempt(self.attempt_id)
        self.assertFalse(outcome["inconclusive"])
        self.assertEqual(
            sorted(r["outcome"] for r in outcome["results"]),
            ["PASS", "PASS", "PASS"])  # a1, a2 and the role-transform check
        # the receipts bind the ORIGINAL candidate, not the drifted tree
        unchanged = self._candidate_row()
        self.assertEqual(unchanged["candidate_tree"], frozen_tree)
        for vrow in self._verification_rows():
            payload = json.loads(self.rt.store.get(
                vrow["receipt_sha256"]).decode("utf-8"))
            self.assertEqual(payload["candidate_receipt_sha256"],
                             row["content_sha256"])
        self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])

    def test_receipt_for_another_candidate_is_refused(self):
        self._to_evidence_complete()
        env = self.rt.store.get(
            self.seq.verification_state(self.attempt_id).get(
                "environment_sha256")) if self._environment_row() else None
        if env is None:
            env = self._runner().capture_environment(
                {"sequence_id": self.sequence_id,
                 "attempt_id": self.attempt_id,
                 "version": 1, "fence": self.fence})
        forged = bound_receipt_bytes(
            self.seq, self.attempt_id, "a2", "PASS", env,
            candidate_receipt_sha256="sha256:" + "ab" * 32)
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner", forged,
                environment=env)
        self.assertIn("candidate", str(ctx.exception))
        self.assertEqual(self._journal_count(), before)


class EnvironmentPlaneTests(Chapter4TestCase):
    def test_capture_names_only_what_constraints_name(self):
        """D-56: env_vars and tools carry ONLY constraint-named entries —
        never the process environment."""
        os.environ["AUTOBOT_C4_TEST_VAR"] = "expected-value"
        try:
            # F-C4-2: the FROZEN constraint section for variables is
            # spelled ``env`` (task_contract_record / obj-10 schemas); the
            # obj-18 CAPTURE field it feeds is ``env_vars``.
            constraints = [{"env": {"AUTOBOT_C4_TEST_VAR":
                                    "expected-value",
                                    "AUTOBOT_C4_UNSET_VAR": "x"},
                            "tools": {"c4tool": {"versions": ["9.9.9"]}}}]
            payload = capture_environment(
                {"sequence_id": "sq_" + "0" * 32,
                 "attempt_id": "at_" + "0" * 32, "version": 1, "fence": 1},
                self.clock, constraints,
                tool_prober=lambda name: ("9.9.9", "sha256:" + "0" * 64))
            self.assertEqual(sorted(payload["env_vars"]),
                             ["AUTOBOT_C4_TEST_VAR"])  # unset var omitted
            self.assertEqual(sorted(payload["tools"]), ["c4tool"])
            self.assertEqual(payload["interpreter"]["version"],
                             interpreter_identity().split("-")[1])
            names = constraint_names(constraints)
            self.assertEqual(names, (["AUTOBOT_C4_TEST_VAR",
                                      "AUTOBOT_C4_UNSET_VAR"], ["c4tool"]))
        finally:
            del os.environ["AUTOBOT_C4_TEST_VAR"]

    def test_full_d56_constraint_vocabulary(self):
        """Every registered D-56 section both satisfied and mismatched;
        unsupported sections and rule keys fail closed."""
        actual = {"interpreter": {"implementation": "cpython",
                                  "version": "3.12.4",
                                  "executable_sha256": "sha256:" + "0" * 64},
                  "platform": {"os": "windows", "os_version": "10.0",
                               "architecture": "x86_64"},
                  "env_vars": {"CI": "true"},
                  "tools": {"git": {"version": "2.47.0",
                                    "executable_sha256":
                                        "sha256:" + "0" * 64}}}
        good = {"interpreter": {"implementations": ["cpython"],
                                "versions": ["3.12.4"]},
                "platform": {"os": ["windows", "linux"],
                             "architectures": ["x86_64"]},
                "tools": {"git": {"versions": ["2.47.0"]}},
                "env": {"CI": "true"}}
        self.assertIsNone(environment_satisfies(good, actual))
        cases = [
            ({"interpreter": {"versions": ["3.8.10"]}}, "version"),
            ({"interpreter": {"implementations": ["pypy"]}},
             "implementation"),
            ({"platform": {"os": ["linux"]}}, "os"),
            ({"platform": {"architectures": ["arm64"]}}, "architecture"),
            ({"tools": {"uv": {"versions": ["1.0"]}}}, "uv"),
            ({"tools": {"git": {"versions": ["9.9"]}}}, "version"),
            ({"env": {"UNSET_NAME": "x"}}, "UNSET_NAME"),
            ({"env": {"CI": "false"}}, "CI"),
            ({"container": {"image": "x"}}, "not supported"),
            ({"interpreter": {"builds": ["x"]}}, "not supported"),
            ({"platform": {"kernels": ["x"]}}, "not supported"),
            ({"tools": {"git": {"paths": ["x"]}}}, "not supported"),
            # F-C4-2's other half: a constraint SECTION spelled env_vars is
            # not the frozen vocabulary — it fails closed, never guessed.
            ({"env_vars": {"CI": "true"}}, "not supported"),
        ]
        for constraint, marker in cases:
            refusal = environment_satisfies(constraint, actual)
            self.assertIsNotNone(refusal, constraint)
            self.assertIn(marker, refusal)

    def test_mismatch_executes_nothing_and_capture_stays_durable(self):
        """A declared constraint the live environment cannot satisfy: the
        runner captures and ADMITS the obj-18 (durable before any launch),
        then refuses with the env_mismatch marker WITHOUT executing — no
        result rows exist, and the registered t-s12 arm makes the halt
        durable. Restart does not recapture: the admitted capture is the
        attempt's one environment."""
        # impossibility: rewrite a1's constraint to demand a version no
        # interpreter reports
        record = support.local_record_payload(
            objective="Impossible environment")
        record["acceptance_assertions"]["ids_and_environment_constraints"][
            0]["environment_constraint"] = {
            "interpreter": {"versions": ["0.0.0"]}}
        record["task_contract_id"] = "tc_" + "c4e0" * 8
        admitted = self.rt.registry.admit_payload(record,
                                                  submitted_by="planner")
        info = self._to_evidence_complete(admitted)
        runner = self._runner()
        with self.assertRaises(ConflictError) as ctx:
            runner.verify_attempt(info["attempt_id"])
        self.assertIn("env_mismatch", str(ctx.exception))
        env_row = self.rt.db.conn.execute(
            "SELECT * FROM attempt_environments WHERE attempt_id = ?",
            (info["attempt_id"],)).fetchone()
        self.assertIsNotNone(env_row)  # capture durable before launch
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_verifications"
            " WHERE attempt_id = ?",
            (info["attempt_id"],)).fetchone()["n"], 0)  # nothing executed
        first_capture = env_row["content_sha256"]
        with self.assertRaises(ConflictError):
            runner.verify_attempt(info["attempt_id"])  # resume: no recapture
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT content_sha256 FROM attempt_environments"
            " WHERE attempt_id = ?",
            (info["attempt_id"],)).fetchone()["content_sha256"],
            first_capture)
        self.seq.halt_verification(admitted["sequence_id"], "env_mismatch")
        self.assertEqual(
            self._sequence_state(admitted["sequence_id"])["state"],
            "HALTED")

    def test_one_environment_per_attempt(self):
        self._to_evidence_complete()
        outcome = self._runner().verify_attempt(self.attempt_id)
        self.assertFalse(outcome["inconclusive"])
        env_row = self._environment_row()
        again = self.seq.admit_environment_capture(
            self.attempt_id, self.rt.store.get(env_row["content_sha256"]))
        self.assertTrue(again["duplicate"])
        different = environment_bytes(
            {"sequence_id": self.sequence_id,
             "attempt_id": self.attempt_id, "version": 1,
             "fence": self.fence})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.admit_environment_capture(self.attempt_id, different)
        self.assertIn("never replaced", str(ctx.exception))


class ClosureDisciplineTests(Chapter4TestCase):
    def test_missing_leaf_halts_never_substitutes(self):
        """INV-53/INV-66: the registered script bytes vanish from the
        content store → IntegrityHalt (no PATH lookup, no same-named
        file), nothing admitted; the registered durable halt is t-s12
        bound_artifact_unavailable."""
        self._to_evidence_complete()
        script_address = content_address(support.LIVE_PASS_SCRIPT)
        Path(str(self.rt.store._path_for(script_address))).unlink()
        before = self._journal_count()
        with self.assertRaises(IntegrityHalt):
            self._runner().verify_attempt(self.attempt_id)
        # only the durable obj-18 admission and the role-transform check
        # (C4-M2: the identity transform's leaves are intact) committed;
        # the missing VERIFIER leaf halted before any assertion result.
        self.assertEqual(self._journal_count(), before + 2)
        self.assertEqual(self._sequence_state()["state"], "VERIFYING")
        self.seq.halt_verification(self.sequence_id,
                                   "bound_artifact_unavailable")
        self.assertEqual(self._sequence_state()["state"], "HALTED")

    def test_tampered_leaf_bytes_halt(self):
        """A same-length tamper of the registered script bytes dies at the
        D-66 re-hash on materialization — the closure executes the exact
        registered bytes or nothing."""
        self._to_evidence_complete()
        script_address = content_address(support.LIVE_PASS_SCRIPT)
        path = Path(str(self.rt.store._path_for(script_address)))
        tampered = bytearray(support.LIVE_PASS_SCRIPT)
        tampered[-2:] = b"##"
        path.write_bytes(bytes(tampered))
        with self.assertRaises(IntegrityHalt):
            self._runner().verify_attempt(self.attempt_id)

    def test_interpreter_identity_mismatch_halts(self):
        registration = support.live_registration("vector-checker", "PASS")
        registration = json.loads(json.dumps(registration))
        registration["closure"]["interpreter_id"] = \
            "cpython-0.0.1-nowhere-x86_64"
        from contract_library import artifact_root
        registration["artifact_root"] = artifact_root(
            registration["closure"])
        with self.assertRaises(IntegrityHalt) as ctx:
            resolve_closure(self.rt.store, registration)
        self.assertIn("interpreter", str(ctx.exception))

    def test_packaged_image_form_and_missing_manifest_halt(self):
        """D-C4-10 (C4-M2 reshapes the C4-M1 refusal): the packaged
        MANIFEST form is now executable — so a manifest whose bytes are
        absent HALTs at resolution (INV-53) — while the image_digest form
        names a container image no part of this runtime's frozen
        environment can execute, and HALTs explicitly rather than being
        approximated by another closure kind."""
        from contract_library import artifact_root
        image = {"kind": "packaged",
                 "image_digest": "sha256:" + "cd" * 32}
        registration = {"registration_id": "rg_" + "f" * 32,
                        "closure": image,
                        "artifact_root": artifact_root(image)}
        with self.assertRaises(IntegrityHalt) as ctx:
            resolve_closure(self.rt.store, registration)
        self.assertIn("container", str(ctx.exception))
        closure = {"kind": "packaged",
                   "manifest_sha256": "sha256:" + "ab" * 32}
        registration = {"registration_id": "rg_" + "f" * 32,
                        "closure": closure,
                        "artifact_root": artifact_root(closure)}
        with self.assertRaises(IntegrityHalt):
            resolve_closure(self.rt.store, registration)

    def test_forged_artifact_root_is_refused_at_admission_and_dispatch(self):
        """SEM-15 at both gates: the Registry refuses a registration whose
        artifact_root does not recompute; and were one smuggled in, the
        runner recomputes before executing."""
        from autobot.models import AdmissionError
        forged = json.loads(json.dumps(
            support.live_registration("vector-checker", "PASS")))
        forged["registration_id"] = "rg_" + "e" * 32
        forged["artifact_root"] = "sha256:" + "9" * 64
        with self.assertRaises(AdmissionError):
            self.rt.registry.admit_object(forged, submitted_by="you")
        with self.assertRaises(IntegrityHalt):
            resolve_closure(self.rt.store, forged)

    def test_service_dependent_registration_never_verifies_deterministic(self):
        """SEM-16 (CH4-llm-output-never-pass's eligibility arm): a
        service:* registration cannot even freeze against a DETERMINISTIC
        assertion, and the runner's own eligibility check refuses it as
        defense in depth."""
        from autobot.verification import verifier_eligibility
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["verifier_llm_v1"],
                                      submitted_by="you")
        record = support.local_record_payload(
            objective="Service-dependent verifier on DETERMINISTIC")
        record["task_contract_id"] = "tc_" + "c4e1" * 8
        entries = record["acceptance_assertions"][
            "ids_and_environment_constraints"]
        entries[1]["verifier_id"] = "llm-reviewer"
        llm_hash = object_hash("VerifierRegistration",
                               graph["verifier_llm_v1"])
        record["resolved_bindings"]["verifier_registrations"] = sorted([
            support.live_registration_hash("registry-checker", "PASS"),
            llm_hash])
        result = self.rt.registry.admit_payload(record,
                                                submitted_by="planner")
        self.assertEqual(result["outcome"], "REJECTED")
        refusal = verifier_eligibility(
            {"assertion_id": "aX", "mode": "DETERMINISTIC",
             "verifier_id": "llm-reviewer"}, graph["verifier_llm_v1"])
        self.assertIn("SEM-16", refusal)


class ResultProtocolTests(Chapter4TestCase):
    def test_prose_output_never_becomes_pass(self):
        """CH4-llm-output-never-pass (INV-06): a verifier that prints
        model-style prose ('All tests passed!') and exits 0 yields
        INCONCLUSIVE with conclusion_basis malformed_verifier_result —
        obj-20 bytes, never obj-19, never PASS, never FAIL — and the
        sequence halts on the registered unknown arm with the repair
        budget untouched."""
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Prose is not a receipt",
                                         vector_behavior="PROSE"),
            submitted_by="planner")
        info = self._to_evidence_complete(admitted)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertTrue(outcome["inconclusive"])
        by_assertion = {r["assertion_id"]: r for r in outcome["results"]}
        self.assertEqual(by_assertion["a1"]["outcome"], "PASS")
        self.assertEqual(by_assertion["a2"]["outcome"], "INCONCLUSIVE")
        payload = json.loads(self.rt.store.get(
            by_assertion["a2"]["receipt_address"]).decode("utf-8"))
        self.assertEqual(payload["kind"], "VerificationFailureReceipt")
        self.assertEqual(payload["conclusion_basis"],
                         "malformed_verifier_result")
        with self.assertRaises(ConflictError):
            self.seq.conclude_verification(info["attempt_id"],
                                           info["fence"])
        self.seq.halt_verification(admitted["sequence_id"], "unknown")
        row = self._sequence_state(admitted["sequence_id"])
        self.assertEqual((row["state"], row["repairs_used"]),
                         ("HALTED", 0))

    def test_verifier_crash_is_execution_failure_not_fail(self):
        """A nonzero exit without a typed conclusion is authority
        uncertainty (verifier_execution_failed) — never a FAIL that would
        consume repair budget."""
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Crash is uncertainty",
                                         vector_behavior="CRASH"),
            submitted_by="planner")
        info = self._to_evidence_complete(admitted)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertTrue(outcome["inconclusive"])
        by_assertion = {r["assertion_id"]: r for r in outcome["results"]}
        payload = json.loads(self.rt.store.get(
            by_assertion["a2"]["receipt_address"]).decode("utf-8"))
        self.assertEqual(payload["outcome"], "INCONCLUSIVE")
        self.assertEqual(payload["conclusion_basis"],
                         "verifier_execution_failed")
        self.assertEqual(payload["execution"]["exit_code"], 3)
        self.assertEqual(
            self._sequence_state(admitted["sequence_id"])["repairs_used"],
            0)

    def test_production_fail_drives_repair_then_budget_halt(self):
        """t-s11 and t-s12 over PRODUCTION obj-20: a genuine reported FAIL
        rejects the attempt (t-a08) and repairs while budget remains, and
        the second genuine FAIL halts on budget_exhausted — no verifier
        ever initiates a retry itself (the Sequencer transitions drive)."""
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Genuine FAIL repairs",
                                         vector_behavior="FAIL"),
            submitted_by="planner")
        info = self._to_evidence_complete(admitted)
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        by_assertion = {r["assertion_id"]: r for r in outcome["results"]}
        payload = json.loads(self.rt.store.get(
            by_assertion["a2"]["receipt_address"]).decode("utf-8"))
        self.assertEqual((payload["kind"], payload["outcome"],
                          payload["conclusion_basis"]),
                         ("VerificationFailureReceipt", "FAIL",
                          "verifier_reported_fail"))
        result = self.seq.conclude_verification(info["attempt_id"],
                                                info["fence"])
        self.assertEqual(result, {"attempt": "REJECTED",
                                  "sequence": "READY"})
        row = self._sequence_state(admitted["sequence_id"])
        self.assertEqual((row["state"], row["repairs_used"]), ("READY", 1))
        info2 = self._to_evidence_complete(admitted)
        self._runner().verify_attempt(info2["attempt_id"])
        result = self.seq.conclude_verification(info2["attempt_id"],
                                                info2["fence"])
        self.assertEqual(result, {"attempt": "REJECTED",
                                  "sequence": "HALTED"})
        rules = self._rules()
        self.assertIn("t-s11", rules)
        self.assertIn("t-s12", rules)

    def test_t_s10_consumes_the_production_obj19_path(self):
        """The full production PASS arc: executed live closures, canonical
        obj-19 bytes schema-valid and fully bound, t-a07 + t-s10 from the
        admitted rows, recovery green over the new proof objects."""
        info = support.drive_to_verified(self.rt, self.admitted)
        self.attempt_id = info["attempt_id"]
        rows = self._verification_rows()
        self.assertEqual([r["outcome"] for r in rows],
                         ["PASS", "PASS", "PASS"])  # a1, a2, role-transform
        candidate = self._candidate_row()
        environment = self._environment_row()
        for vrow in rows:
            data = self.rt.store.get(vrow["receipt_sha256"])
            payload = json.loads(data.decode("utf-8"))
            self.assertEqual(
                verification_receipts.validate_result_receipt(payload), [])
            self.assertEqual(verification_receipts.receipt_bytes(payload),
                             data)
            self.assertEqual(payload["kind"], "VerificationReceipt")
            self.assertEqual(payload["result_source"], "BY_VERIFIER")
            self.assertEqual(payload["candidate_receipt_sha256"],
                             candidate["content_sha256"])
            self.assertEqual(payload["environment_sha256"],
                             environment["content_sha256"])
            output = self.rt.store.get(
                payload["execution"]["output_sha256"])
            if vrow["assertion_id"].startswith("role-transform/"):
                # C4-M2: the INV-49 check rides the same INV-17 binding;
                # its durable execution output IS the transform's exact
                # output bytes (identity over the declared input), and the
                # admitted producer identity equals the derived expected.
                self.assertEqual(payload["assertion"]["part"]["part_kind"],
                                 "ROLE_TRANSFORM")
                self.assertEqual(output, support.VA_INPUT.encode("utf-8"))
                check = payload["transform_check"]
                self.assertEqual(check["expected_sha256"],
                                 check["observed_sha256"])
            else:
                self.assertEqual(payload["assertion"]["part"]["part_kind"],
                                 "ASSERTION")
                # durable output carrying the registered protocol line
                self.assertIn(b"autobot_verifier_result", output)
        rules = self._rules()
        self.assertIn("t-a07", rules)
        self.assertIn("t-s10", rules)
        self.assertIn("ch4:admit-environment", rules)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


class BindingAttackTests(Chapter4TestCase):
    """CH4-verification-binding (INV-17) and CH4-evidence-not-reused
    (INV-18): every binding axis attacked independently; each forged
    receipt dies at the admission seam with nothing committed."""

    def _env(self):
        state = self.seq.verification_state(self.attempt_id)
        if "environment_sha256" in state:
            return self.rt.store.get(state["environment_sha256"])
        env = self._runner().capture_environment(
            {"sequence_id": state["sequence_id"],
             "attempt_id": state["attempt_id"],
             "version": state["version"], "fence": state["fence"]})
        self.seq.admit_environment_capture(self.attempt_id, env)
        return env

    def _refused(self, marker, assertion_id="a2", outcome="PASS",
                 env=None, **overrides):
        env = env if env is not None else self._env()
        forged = bound_receipt_bytes(self.seq, self.attempt_id,
                                     assertion_id, outcome, env,
                                     **overrides)
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                self.attempt_id, assertion_id, outcome, "verifier_runner",
                forged, environment=env)
        self.assertIn(marker, str(ctx.exception))
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_verifications"
            " WHERE attempt_id = ? AND assertion_id = ?",
            (self.attempt_id, assertion_id)).fetchone()["n"], 0)

    def test_every_binding_axis_fails_closed(self):
        self._to_evidence_complete()
        env = self._env()
        state = self.seq.verification_state(self.attempt_id)
        bound = state["registrations"]["vector-checker"]
        # wrong attempt (INV-18: evidence is attempt-bound)
        self._refused("attempt identity", env=env, attempt={
            "sequence_id": self.sequence_id,
            "attempt_id": "at_" + "0" * 32, "fence": self.fence})
        # stale fence
        self._refused("attempt identity", env=env, attempt={
            "sequence_id": self.sequence_id,
            "attempt_id": self.attempt_id, "fence": self.fence + 1})
        # wrong contract hash
        self._refused("attempt_contract_hash", env=env,
                      attempt_contract_hash="sha256:" + "0" * 64)
        # wrong record
        self._refused("record_hash", env=env,
                      record_hash="sha256:" + "1" * 64)
        # wrong candidate
        self._refused("candidate", env=env,
                      candidate_receipt_sha256="sha256:" + "2" * 64)
        # wrong environment address inside the receipt
        self._refused("environment", env=env,
                      environment_sha256="sha256:" + "3" * 64)
        # wrong assertion part (a result for B presented as A)
        self._refused("part", env=env, assertion={
            "part": {"record": state["record_hash"],
                     "part_kind": "ASSERTION", "part_id": "a1"},
            "verifier_id": "vector-checker", "mode": "DETERMINISTIC"})
        # wrong registration identity
        self._refused("registration", env=env, registration={
            "object_sha256": "sha256:" + "4" * 64,
            "registration_id": bound["payload"]["registration_id"],
            "artifact_root": bound["payload"]["artifact_root"]})
        # a registration changed after freeze: the real v2 fixture is
        # admitted, but it is not the BOUND one
        graph = support.load_fixture("graph.json")
        self.rt.registry.admit_object(graph["verifier_checker_v2"],
                                      submitted_by="you")
        v2_hash = object_hash("VerifierRegistration",
                              graph["verifier_checker_v2"])
        self._refused("registration", env=env, registration={
            "object_sha256": v2_hash,
            "registration_id":
                graph["verifier_checker_v2"]["registration_id"],
            "artifact_root": graph["verifier_checker_v2"]["artifact_root"]})
        # outcome disagreement: declared PASS, payload FAIL bytes
        fail_bytes = bound_receipt_bytes(self.seq, self.attempt_id, "a2",
                                         "FAIL", env)
        before = self._journal_count()
        with self.assertRaises(ConflictError):
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner",
                fail_bytes, environment=env)
        self.assertEqual(self._journal_count(), before)
        # non-canonical bytes
        payload = json.loads(bound_receipt_bytes(
            self.seq, self.attempt_id, "a2", "PASS", env).decode("utf-8"))
        spaced = json.dumps(payload, indent=2, sort_keys=True
                            ).encode("utf-8")
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner", spaced,
                environment=env)
        self.assertIn("canonical", str(ctx.exception))

    def test_outcome_must_agree_with_execution_evidence(self):
        """F-C4-1 (found in the chapter's adversarial review): a FULLY
        BOUND verifier-shaped receipt claiming PASS is refused when its
        own durable execution evidence does not support the claim — the
        output bytes must resolve from the content store and conclude
        PASS under the registered result protocol at exit 0. An outcome
        without evidence has no authority."""
        self._to_evidence_complete()
        env = self._env()
        state = self.seq.verification_state(self.attempt_id)
        registration = state["registrations"]["vector-checker"]["payload"]
        fail_line = (json.dumps({"autobot_verifier_result": {
            "conclusion": "FAIL", "detail": "the evidence says FAIL"}})
            + "\n").encode("utf-8")
        fail_output = self.rt.store.put(fail_line)
        before = self._journal_count()
        forged = bound_receipt_bytes(
            self.seq, self.attempt_id, "a2", "PASS", env,
            execution={"closure_kind": "python",
                       "entrypoint": registration["entrypoint"],
                       "exit_code": 0, "output_sha256": fail_output})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner", forged,
                environment=env)
        self.assertIn("execution evidence", str(ctx.exception))
        # evidence that does not even resolve
        phantom = bound_receipt_bytes(
            self.seq, self.attempt_id, "a2", "PASS", env,
            execution={"closure_kind": "python",
                       "entrypoint": registration["entrypoint"],
                       "exit_code": 0,
                       "output_sha256": "sha256:" + "6" * 64})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner", phantom,
                environment=env)
        self.assertIn("does not resolve", str(ctx.exception))
        # a crash exit dressed up as FAIL
        crash_output = self.rt.store.put(b"traceback, no conclusion\n")
        dressed = bound_receipt_bytes(
            self.seq, self.attempt_id, "a2", "FAIL", env,
            execution={"closure_kind": "python",
                       "entrypoint": registration["entrypoint"],
                       "exit_code": 3, "output_sha256": crash_output})
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "FAIL", "verifier_runner", dressed,
                environment=env)
        self.assertIn("execution evidence", str(ctx.exception))
        self.assertEqual(self._journal_count(), before)
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_verifications"
            " WHERE attempt_id = ?",
            (self.attempt_id,)).fetchone()["n"], 0)

    def test_receipt_replay_across_attempts_is_inert(self):
        """CH4-evidence-not-reused (INV-18): the repair attempt cannot be
        satisfied by the first attempt's receipt — the old bytes bind the
        old attempt and fence and die at the seam."""
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Replay is inert",
                                         vector_behavior="FAIL"),
            submitted_by="planner")
        info1 = self._to_evidence_complete(admitted)
        self._runner().verify_attempt(info1["attempt_id"])
        old_rows = [dict(r) for r in self.rt.db.conn.execute(
            "SELECT * FROM attempt_verifications WHERE attempt_id = ?",
            (info1["attempt_id"],)).fetchall()]
        old_pass = [r for r in old_rows if r["outcome"] == "PASS"][0]
        old_receipt = self.rt.store.get(old_pass["receipt_sha256"])
        self.seq.conclude_verification(info1["attempt_id"],
                                       info1["fence"])  # t-s11 repair
        info2 = self._to_evidence_complete(admitted)
        self.attempt_id = info2["attempt_id"]
        self.fence = info2["fence"]
        env = self._env()
        before = self._journal_count()
        with self.assertRaises(ConflictError) as ctx:
            self.seq.register_verifier_result(
                info2["attempt_id"], "a1", "PASS", "verifier_runner",
                old_receipt, environment=env)
        self.assertIn("attempt identity", str(ctx.exception))
        self.assertEqual(self._journal_count(), before)


class MandatoryAssertionTests(Chapter4TestCase):
    """CH4-mandatory-assertions-required (INV-07): the frozen record's
    mandatory floor (a1) is a subset of its declared assertions (a1, a2),
    and conclusion demands an authoritative result for EVERY declared
    assertion — strictly stronger than the floor, so no optional result
    can substitute and no PASS masks a missing one."""

    def test_one_pass_never_masks_a_missing_assertion(self):
        self._to_evidence_complete()
        state = self.seq.verification_state(self.attempt_id)
        env = self.rt.store.get(state["environment_sha256"]) \
            if "environment_sha256" in state else None
        if env is None:
            env = self._runner().capture_environment(
                {"sequence_id": self.sequence_id,
                 "attempt_id": self.attempt_id,
                 "version": state["version"], "fence": self.fence})
        receipt = bound_receipt_bytes(self.seq, self.attempt_id, "a1",
                                      "PASS", env)
        self.seq.register_verifier_result(
            self.attempt_id, "a1", "PASS", "verifier_runner", receipt,
            environment=env)
        floor = support.load_fixture("va_record.json")[
            "resolved_bindings"]["mandatory_floor"][
            "mandatory_assertion_ids"]
        self.assertEqual(floor, ["a1"])  # the floor is satisfied...
        with self.assertRaises(ConflictError) as ctx:
            # ...and conclusion still refuses: a2 has no result
            self.seq.conclude_verification(self.attempt_id, self.fence)
        self.assertIn("a2", str(ctx.exception))
        self.assertEqual(self._attempt_state(), "EVIDENCE_COMPLETE")

    def test_a_result_for_a_cannot_satisfy_b(self):
        """The one-row-per-assertion ledger index plus the D-47 part
        binding: a2's result registered under a1's name is refused, and
        the a1 row cannot be duplicated onto a2."""
        self._to_evidence_complete()
        state = self.seq.verification_state(self.attempt_id)
        env = self._runner().capture_environment(
            {"sequence_id": self.sequence_id,
             "attempt_id": self.attempt_id,
             "version": state["version"], "fence": self.fence})
        self.seq.admit_environment_capture(self.attempt_id, env)
        a1_receipt = bound_receipt_bytes(self.seq, self.attempt_id, "a1",
                                         "PASS", env)
        with self.assertRaises(ConflictError) as ctx:
            # the bytes bind part a1; presenting them as a2's result dies
            # on the part reference
            self.seq.register_verifier_result(
                self.attempt_id, "a2", "PASS", "verifier_runner",
                a1_receipt, environment=env)
        self.assertIn("part", str(ctx.exception))
        self.assertEqual(self.rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM attempt_verifications").fetchone()[
            "n"], 0)


class RecoveryChapter4Tests(Chapter4TestCase):
    """The 8h recovery pairing over obj-16/obj-18/obj-19/obj-20 plus the
    crash-matrix resume points that live between transactions."""

    def test_candidate_pairing_and_binding_revalidation(self):
        info = support.drive_to_verified(self.rt, self.admitted)
        self.attempt_id = info["attempt_id"]
        row = self._candidate_row()
        # row/payload drift
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_candidates SET candidate_tree = ?"
                       " WHERE admission_id = ?",
                       ("deadbeef" * 5, row["admission_id"]))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_candidates SET candidate_tree = ?"
                       " WHERE admission_id = ?",
                       (row["candidate_tree"], row["admission_id"]))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        # a VERIFIED attempt without its candidate row is forged state
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_candidates WHERE admission_id"
                       " = ?", (row["admission_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_candidate_bytes_tamper_halts(self):
        self._to_evidence_complete()
        row = self._candidate_row()
        path = Path(str(self.rt.store._path_for(row["content_sha256"])))
        data = bytearray(path.read_bytes())
        data[-2:] = b"!}"
        path.write_bytes(bytes(data))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_environment_pairing_and_verification_binding(self):
        info = support.drive_to_verified(self.rt, self.admitted)
        self.attempt_id = info["attempt_id"]
        env_row = self._environment_row()
        # journal names the environment admission; deleting the row halts
        with self.rt.db.transaction() as tx:
            tx.execute("DELETE FROM attempt_environments WHERE"
                       " admission_id = ?", (env_row["admission_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()
        with self.rt.db.transaction() as tx:
            tx.execute(
                "INSERT INTO attempt_environments (admission_id,"
                " attempt_id, content_sha256, admitted_utc)"
                " VALUES (?, ?, ?, ?)",
                (env_row["admission_id"], env_row["attempt_id"],
                 env_row["content_sha256"], env_row["admitted_utc"]))
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        # a verification row whose environment column drifts from the
        # attempt's one admitted capture halts (INV-17 re-validated)
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_verifications SET"
                       " environment_sha256 = ? WHERE attempt_id = ?"
                       " AND assertion_id = 'a1'",
                       ("sha256:" + "5" * 64, self.attempt_id))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_rejected_without_fail_and_verified_with_fail_halt(self):
        admitted = self.rt.registry.admit_payload(
            support.local_record_payload(objective="Row forgery halts",
                                         vector_behavior="FAIL"),
            submitted_by="planner")
        info = self._to_evidence_complete(admitted)
        self.attempt_id = info["attempt_id"]
        self._runner().verify_attempt(info["attempt_id"])
        self.seq.conclude_verification(info["attempt_id"], info["fence"])
        # REJECTED attempt: forging its FAIL row into PASS halts (the
        # obj-20 authority behind t-a08 disappears)
        with self.rt.db.transaction() as tx:
            tx.execute("UPDATE attempt_verifications SET outcome = 'PASS'"
                       " WHERE attempt_id = ? AND assertion_id = 'a2'",
                       (info["attempt_id"],))
        with self.assertRaises(IntegrityHalt):
            self.seq.recover()

    def test_crash_points_between_transactions_converge(self):
        """The crash matrix's seam points: candidate bytes without
        admission are harmless orphans; an admitted environment with no
        results resumes without recapture; an admitted result without
        conclusion concludes after restart; repeated recovery converges."""
        info = self._to_evidence_complete()
        # candidate bytes for a NEVER-frozen sibling attempt: just bytes
        # in the CAS — recovery reports an orphan and trusts nothing
        stray = self.rt.store.put(b'{"stray": "candidate bytes"}')
        report = self.seq.recover()
        self.assertIn(stray, report["orphans"]["unreferenced_objects"])
        # admitted environment, zero results (crash before launch):
        state = self.seq.verification_state(info["attempt_id"])
        env = self._runner().capture_environment(
            {"sequence_id": self.sequence_id,
             "attempt_id": info["attempt_id"],
             "version": state["version"], "fence": info["fence"]})
        self.seq.admit_environment_capture(info["attempt_id"], env)
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        outcome = self._runner().verify_attempt(info["attempt_id"])
        self.assertFalse(outcome["inconclusive"])
        # the runner REUSED the admitted capture (no recapture):
        self.assertEqual(self._environment_row()["content_sha256"],
                         content_address(env))
        # admitted results, no conclusion (crash before t-a07/t-s10):
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        self.seq.conclude_verification(info["attempt_id"], info["fence"])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])
        self.assertEqual(self.seq.recover()["intents_in_doubt"], [])


if __name__ == "__main__":
    unittest.main()
