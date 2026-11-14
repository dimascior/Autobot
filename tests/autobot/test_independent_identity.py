"""TI-1 WP2: the independent identity oracles (D-58 carried from
canonicalization to the preimage layer).

``preimage2`` is a second implementation of content_address, the domained
object hash, the three identity tiers, artifact_root and the journal entry
hash, written from the frozen chapter-1 documentation. Four proofs:

1. isolation - the oracle imports nothing from the production packages
   (AST import allowlist, a dynamic-import token ban, and a fresh ``-I``
   interpreter's import graph);
2. frozen goldens - the oracle ALONE reproduces every golden digest and the
   V-A canonical preimage texts from the written fixture files, in-process
   and in a fresh interpreter (so its layouts are the documented ones, not a
   copy of the library's);
3. production equality - the library and the oracle agree on every
   chapter-1 fixture object, on the live registrations and records the
   runtime suite binds, and on seeded randomized payloads;
4. documented refusals - both sides refuse what the frozen documentation
   says is refused.

Any divergence here is a finding (F-TI-n), never a reason to align the
oracle to the library.
"""

import ast
import copy
import importlib.util
import json
import random
import subprocess
import sys
import unittest
from pathlib import Path

import support
import preimage2
from test_verification import human_record_payload
from contract_library import (
    CanonicalizationError,
    DigestError,
    artifact_root,
    canonicalize,
    compute_tiers,
    content_address,
    journal_entry_hash,
    object_hash,
)

HERE = Path(__file__).resolve().parent
PREIMAGE2_PATH = HERE / "preimage2.py"

# One fixed seed for every randomized arm in this module; printed once per
# run (setUpModule) and carried in every failure message.
SEED = 20261113

PRODUCTION_PACKAGES = ("autobot", "contract_library", "intelligence",
                       "coordinator")

# API tokens that would let an oracle reach production code without an
# import statement. (Tokens, not prose: the oracle docstrings legitimately
# NAME the packages they refuse to import.)
DYNAMIC_IMPORT_TOKENS = ("__import__", "importlib", "exec(", "eval(",
                         "runpy", "pkgutil")

# Documented fixture bytes (chapter1/harness/fixtures.py), restated so the
# golden content addresses are recomputed from bytes, not copied.
VA_INPUT_LF = support.VA_INPUT.encode("utf-8")
VA_INPUT_CRLF = support.VA_INPUT.replace("\n", "\r\n").encode("utf-8")
CHECKER_SCRIPT_V1 = b"registry-checker script v1\n"

VECTOR_IDS = ("V-C", "V-D", "V-E", "V-F", "V-H", "V-M", "V-N", "V-O",
              "V-P", "V-R")


def setUpModule():
    sys.stderr.write("test_independent_identity: SEED=%d\n" % SEED)


def imported_modules(path):
    """Every module an oracle source imports, at any nesting depth."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.add("<relative>")
            else:
                found.add((node.module or "").split(".")[0])
    return found


_IMPORT_GRAPH_PROBE = (
    "import importlib.util, json, sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "spec = importlib.util.spec_from_file_location('oracle', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "print(json.dumps(sorted(set(n.split('.')[0] for n in sys.modules))))\n"
)


def fresh_interpreter_import_graph(path):
    """Every top-level module a fresh, isolated (``-I``) interpreter holds
    after importing one oracle BY PATH - what the oracle really pulls in,
    transitively, whatever its source says."""
    result = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_GRAPH_PROBE, str(path),
         str(Path(path).parent)],
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise AssertionError("%s does not import in a fresh interpreter:\n%s"
                             % (Path(path).name, result.stderr))
    return set(json.loads(result.stdout.strip().splitlines()[-1]))


def assert_oracle_isolated(case, path, allowed):
    """The import ban, the same three checks for every TI-1 oracle: its
    imports are exactly an allowlist of stdlib modules (plus sibling
    oracles); no dynamic-import token appears at all; and a fresh ``-I``
    interpreter that imports it holds no production package - the
    transitive half, which a source scan alone cannot see."""
    modules = imported_modules(path)
    case.assertEqual(
        modules - set(allowed), set(),
        "%s imports outside its allowlist" % Path(path).name)
    for package in PRODUCTION_PACKAGES:
        case.assertNotIn(package, modules)
    source = Path(path).read_text(encoding="utf-8")
    for token in DYNAMIC_IMPORT_TOKENS:
        case.assertNotIn(
            token, source,
            "%s carries the dynamic-import token %r"
            % (Path(path).name, token))
    loaded = fresh_interpreter_import_graph(path)
    # the probe really executed the oracle: everything it imports is held
    case.assertEqual(set(allowed) - loaded, set())
    case.assertEqual(
        loaded & set(PRODUCTION_PACKAGES), set(),
        "%s pulls a production package into a fresh interpreter"
        % Path(path).name)


def load_jcs2():
    """The frozen chapter-1 second JCS implementation, loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "ch1_harness_jcs2", str(support.CH1 / "harness" / "jcs2.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def random_string(rng, low=0, high=12):
    # BMP-low, the last code point below the surrogates, BMP-high (private
    # use, a presentation form, the replacement character) and two
    # supplementary characters: every band the UTF-16 key order separates.
    alphabet = (
        "abcXYZ019 _-./\\\"'\t\n\r\x00\x1f"
        "\u00e9\u00f6\u20ac\u0080\u0391\u4e2d\ud7ff"
        "\ue000\ufb33\ufffd"
        "\U0001f600\U0001d11e\U00010000\U0010ffff"
    )
    return "".join(rng.choice(alphabet)
                   for _ in range(rng.randint(low, high)))


def random_value(rng, depth=0):
    choices = ["str", "int", "bool"]
    if depth < 3:
        choices += ["dict", "list"]
    kind = rng.choice(choices)
    if kind == "str":
        return random_string(rng)
    if kind == "int":
        return rng.choice([0, 1, 7, 2 ** 31, 2 ** 53 - 1,
                           rng.randint(0, 2 ** 53 - 1)])
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "list":
        return [random_value(rng, depth + 1)
                for _ in range(rng.randint(0, 4))]
    return dict((random_string(rng), random_value(rng, depth + 1))
                for _ in range(rng.randint(0, 5)))


def random_digest(rng):
    return "sha256:" + "".join(rng.choice("0123456789abcdef")
                               for _ in range(64))


class OracleIsolationTests(unittest.TestCase):
    def test_preimage2_imports_only_the_standard_library(self):
        assert_oracle_isolated(
            self, PREIMAGE2_PATH,
            allowed=("binascii", "hashlib", "json", "os", "sys"))

    def test_no_production_object_is_reachable_from_the_module(self):
        """The transitive-import attack: nothing bound in the oracle's
        namespace was defined by a production module."""
        for name, value in sorted(vars(preimage2).items()):
            origin = getattr(value, "__module__", None) or ""
            self.assertFalse(
                origin.split(".")[0] in PRODUCTION_PACKAGES,
                "preimage2.%s comes from %s" % (name, origin))

    def test_tier_fields_pin_the_chapter0_registry(self):
        """The restated tier field lists ARE identity.json's."""
        tiers = dict(
            (tier["id"], tuple(tier["fields"])) for tier in
            support.load_json(support.CH0_REGISTRIES / "identity.json")[
                "tiers"])
        self.assertEqual(preimage2.REQUIREMENT_FIELDS,
                         tiers["requirement_digest"])
        self.assertEqual(preimage2.CONTRACT_FIELDS,
                         tiers["contract_digest"])
        self.assertEqual(preimage2.RECORD_FIELDS, tiers["record_hash"])
        self.assertEqual((len(preimage2.REQUIREMENT_FIELDS),
                          len(preimage2.CONTRACT_FIELDS),
                          len(preimage2.RECORD_FIELDS)), (9, 16, 7))


class FrozenGoldenTests(unittest.TestCase):
    """The oracle alone against chapter 1's frozen goldens: no production
    function is called anywhere in this class."""

    @classmethod
    def setUpClass(cls):
        cls.golden = support.load_golden()
        cls.recomputed = preimage2.recompute_goldens(str(support.FIXTURES))
        cls.graph = support.load_fixture("graph.json")

    def test_object_hashes(self):
        self.assertEqual(self.recomputed["object_hashes"],
                         self.golden["object_hashes"])
        self.assertEqual(
            self.recomputed["actual_verification_environment_hash"],
            self.golden["actual_verification_environment_hash"])

    def test_artifact_roots(self):
        for name, root in sorted(self.golden["artifact_roots"].items()):
            self.assertEqual(self.recomputed["artifact_roots"][name], root)
        # every registration fixture also CARRIES its root (SEM-15)
        for name, payload in sorted(self.graph.items()):
            if "closure" in payload:
                self.assertEqual(
                    self.recomputed["artifact_roots"][name],
                    payload["artifact_root"], name)
        self.assertEqual(len(self.recomputed["artifact_roots"]), 6)

    def test_tier_digests_and_canonical_preimages(self):
        va = self.golden["V-A"]
        for member in ("requirement_digest", "contract_digest",
                       "record_hash", "canonical_requirement_preimage",
                       "canonical_contract_preimage",
                       "canonical_record_preimage"):
            self.assertEqual(self.recomputed["V-A"][member], va[member],
                             member)
        self.assertEqual(
            self.recomputed["canonical_record_payload_sha256"],
            va["canonical_record_payload_sha256"])
        for vector_id in VECTOR_IDS:
            for tier in ("requirement_digest", "contract_digest",
                         "record_hash"):
                self.assertEqual(
                    self.recomputed[vector_id][tier],
                    self.golden[vector_id][tier],
                    "%s %s" % (vector_id, tier))

    def test_domain_separation_vector(self):
        for member in ("payload_canonical", "as_delegation_contract",
                       "as_repository_authority_declaration"):
            self.assertEqual(self.recomputed["V-L"][member],
                             self.golden["V-L"][member])

    def test_journal_chain_vector(self):
        self.assertEqual(self.recomputed["JH-1"],
                         self.golden["JH-1"]["entries"])

    def test_content_address_vectors(self):
        golden = self.golden["content_addresses"]
        self.assertEqual(preimage2.content_address2(VA_INPUT_LF),
                         golden["va_input_lf"])
        self.assertEqual(preimage2.content_address2(VA_INPUT_CRLF),
                         golden["va_input_crlf"])
        self.assertEqual(preimage2.content_address2(CHECKER_SCRIPT_V1),
                         golden["checker_script_v1"])

    def test_duplicate_admission_fixture_vector(self):
        """obj-32's fixture (harness/fixtures.py), rebuilt from V-A's own
        admission facts: its canonical bytes' content address is frozen."""
        base = support.load_fixture("va_record.json")
        va = self.golden["V-A"]
        fixture = {
            "schema_version": "v1",
            "kind": "DuplicateAdmissionRecord",
            "duplicate_admission_id": "da_00000000000000000000000000000001",
            "payload_contract_digest": va["contract_digest"],
            "existing_record_hash": va["record_hash"],
            "payload_content_address": preimage2.content_address2(
                preimage2.canonical_bytes(base)),
            "submitted_by": "planner",
            "received_utc": "2026-11-08T22:10:00.000Z",
        }
        self.assertEqual(
            preimage2.content_address2(preimage2.canonical_bytes(fixture)),
            self.golden["duplicate_admission_fixture_content"])

    def test_fresh_interpreter_recompute(self):
        """D-58 oracle 5 for the second implementation: an isolated
        interpreter that never saw the production packages recomputes the
        same values from the written fixtures."""
        result = subprocess.run(
            [sys.executable, "-I", str(PREIMAGE2_PATH),
             str(support.FIXTURES)],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        child = json.loads(result.stdout)
        loaded = set(child.pop("loaded_modules"))
        self.assertEqual(loaded & set(PRODUCTION_PACKAGES), set())
        self.assertEqual(child, self.recomputed)
        self.assertEqual(child["object_hashes"],
                         self.golden["object_hashes"])


class ProductionEqualityTests(unittest.TestCase):
    """The library and the oracle, side by side."""

    def _same_tiers(self, record, label):
        tiers = tuple(compute_tiers(record))
        self.assertEqual(tiers, preimage2.compute_tiers2(record), label)
        # obj-04's object hash IS the record_hash tier (D-29/D-33). The
        # library's flat object_hash is a different function - F-TI-1,
        # pinned in RecordedDivergenceTests.
        self.assertEqual(
            preimage2.object_hash2("TaskContractRecord", record), tiers[2],
            label)

    def _same_registration(self, registration, label):
        self.assertEqual(
            object_hash(registration["kind"], registration),
            preimage2.object_hash2(registration["kind"], registration),
            label)
        self.assertEqual(artifact_root(registration["closure"]),
                         preimage2.artifact_root2(registration["closure"]),
                         label)
        self.assertEqual(registration["artifact_root"],
                         preimage2.artifact_root2(registration["closure"]),
                         label)

    def test_every_chapter1_fixture_object(self):
        graph = support.load_fixture("graph.json")
        for name, payload in sorted(graph.items()):
            self.assertEqual(
                object_hash(payload["kind"], payload),
                preimage2.object_hash2(payload["kind"], payload), name)
            if "closure" in payload:
                self._same_registration(payload, name)
        environment = support.load_fixture("env_fixture.json")
        self.assertEqual(
            object_hash(environment["kind"], environment),
            preimage2.object_hash2(environment["kind"], environment))
        self._same_tiers(support.load_fixture("va_record.json"), "V-A")
        payloads = support.load_fixture("vector_payloads.json")
        for vector_id in sorted(payloads):
            self._same_tiers(payloads[vector_id], vector_id)

    def test_live_registrations_and_records(self):
        """The identities the runtime suite itself binds (support.py):
        every live verifier and transform registration, their script and
        lock bytes, and every record family the drives admit."""
        for verifier_id, behavior in sorted(support.LIVE_SPECS):
            self._same_registration(
                support.live_registration(verifier_id, behavior),
                "%s/%s" % (verifier_id, behavior))
            script = support.LIVE_SPECS[(verifier_id, behavior)][1]
            self.assertEqual(content_address(script),
                             preimage2.content_address2(script))
        for transform_id in sorted(support.LIVE_TRANSFORM_SPECS):
            self._same_registration(
                support.live_transform_registration(transform_id),
                transform_id)
            script = support.LIVE_TRANSFORM_SPECS[transform_id][1]
            self.assertEqual(content_address(script),
                             preimage2.content_address2(script))
        self.assertEqual(content_address(support.LIVE_LOCK),
                         preimage2.content_address2(support.LIVE_LOCK))
        records = {
            "local": support.local_record_payload(),
            "local-fail": support.local_record_payload(
                vector_behavior="FAIL"),
            "local-read-only": support.local_record_payload(
                finalization_class="READ_ONLY",
                finalization_requirements=[]),
            "local-edge": support.local_record_payload(
                task_contract_id="tc_" + "b00b" * 8,
                predecessors=[{"edge_id": "on-a", "edge_kind": "RECORD",
                               "target": "sha256:" + "1" * 64,
                               "observation_requirement": "ACCEPTANCE"}]),
            "remote": support.remote_record_payload(),
            "human": human_record_payload(),
        }
        for label, record in sorted(records.items()):
            self._same_tiers(record, label)

    def test_closure_kinds_beyond_the_fixtures(self):
        """native and both packaged forms (the fixtures are all python)."""
        manifests = (
            {"kind": "native", "executable_sha256": "sha256:" + "a" * 64},
            {"kind": "packaged", "manifest_sha256": "sha256:" + "b" * 64},
            {"kind": "packaged", "image_digest": "sha256:" + "c" * 64},
            {"kind": "python", "script_sha256": "sha256:" + "d" * 64,
             "interpreter_id": "cpython-3.8.10-windows-x86_64",
             "dependency_lock_sha256": "sha256:" + "e" * 64},
        )
        roots = set()
        for manifest in manifests:
            root = preimage2.artifact_root2(manifest)
            self.assertEqual(artifact_root(manifest), root, manifest)
            roots.add(root)
        self.assertEqual(len(roots), len(manifests))

    def test_runtime_minted_object_shapes(self):
        """obj-09 as the Sequencer mints it, and a journal chain over the
        runtime's own TransitionRecord vocabulary."""
        contract = {
            "schema_version": "v1", "kind": "AttemptContract",
            "attempt": {"sequence_id": "sq_" + "1" * 32,
                        "attempt_id": "at_" + "2" * 32,
                        "version": 1, "fence": 3},
            "record_hash": "sha256:" + "3" * 64,
            "requirement_digest": "sha256:" + "4" * 64,
            "contract_digest": "sha256:" + "5" * 64,
            "source_state": {"record_hash": "sha256:" + "3" * 64,
                             "repository_id": "autobot",
                             "authority_location": "LOCAL",
                             "target_ref": "refs/heads/main"},
            "created_utc": "2026-11-13T00:00:00.000Z",
        }
        self.assertEqual(object_hash("AttemptContract", contract),
                         preimage2.object_hash2("AttemptContract",
                                                contract))
        previous = None
        for index, rule in enumerate(("t-c01", "t-c02", "t-s03", "t-s06")):
            payload = {"schema_version": "v1", "kind": "TransitionRecord",
                       "machine": "sequence",
                       "subject_id": "sq_" + "1" * 32, "rule": rule,
                       "to_state": "READY",
                       "recorded_utc": "2026-11-13T00:00:0%d.000Z" % index,
                       "facts": {"version": index + 1, "fence": 0}}
            ours = preimage2.journal_entry_hash2(payload, previous)
            self.assertEqual(journal_entry_hash(payload, previous), ours)
            previous = ours

    def test_documented_refusals_agree(self):
        """SEM-17 (kind and version bind through the domain) and the D-24/
        D-37 value domain: refused on both sides."""
        declaration = support.load_fixture("graph.json")[
            "declaration_remote"]
        with self.assertRaises(DigestError):
            object_hash("DelegationContract", declaration)
        with self.assertRaises(preimage2.Preimage2Error):
            preimage2.object_hash2("DelegationContract", declaration)
        wrong_version = dict(declaration, schema_version="v2")
        with self.assertRaises(DigestError):
            object_hash("RepositoryAuthorityDeclaration", wrong_version)
        with self.assertRaises(preimage2.Preimage2Error):
            preimage2.object_hash2("RepositoryAuthorityDeclaration",
                                   wrong_version)
        for bad in (None, 1.5, -1, 2 ** 53, {"a": None}, [0.0]):
            with self.assertRaises(CanonicalizationError):
                canonicalize(bad)
            with self.assertRaises(preimage2.Preimage2Error):
                preimage2.canonical_bytes(bad)
        # D-32 enumerates native/python/packaged: no other closure kind
        # has an artifact root.
        unknown = {"kind": "container", "image_digest": "sha256:" + "a" * 64}
        with self.assertRaises(DigestError):
            artifact_root(unknown)
        with self.assertRaises(preimage2.Preimage2Error):
            preimage2.artifact_root2(unknown)

    def test_top_level_fields_outside_every_tier_change_no_digest(self):
        """identity.json's excluded top-level fields (V-G's property):
        neither side lets them into any preimage."""
        base = support.load_fixture("va_record.json")
        expected = preimage2.compute_tiers2(base)
        noisy = copy.deepcopy(base)
        noisy["machine_paths"] = {"workspace": "C:/somewhere"}
        noisy["operational_config"] = {"verbose": True}
        self.assertEqual(preimage2.compute_tiers2(noisy), expected)
        self.assertEqual(tuple(compute_tiers(noisy)), expected)


class RecordedDivergenceTests(unittest.TestCase):
    """What the oracle exposed, pinned so it stays visible. Each test is a
    numbered TI-1 finding (docs/architecture/chapter4/INDEPENDENCE.md);
    none was resolved by aligning the oracle to the library, and each
    states what keeps it from reaching a frozen ledger identity."""

    def setUp(self):
        self.base = support.load_fixture("va_record.json")
        self.golden = support.load_golden()["V-A"]

    def test_f_ti_1_flat_object_hash_is_not_obj04s_identity(self):
        """F-TI-1. D-29: 'obj-04's object hash is the record_hash'; spec
        section 4: the full-payload construction is for 'every OTHER
        protocol object'. The library's object_hash is flat for every
        kind, so over a TaskContractRecord it returns a second, different
        digest under the same domain label. The oracle follows the
        documentation. The reachable consequence (Registry.admit_object
        indexing a record under the flat value, which recovery rejects) is
        closed at the Registry seam - see
        test_independent_state.RegistrySeamTests."""
        tiers = tuple(compute_tiers(self.base))
        self.assertEqual(tiers[2], self.golden["record_hash"])
        self.assertEqual(
            preimage2.object_hash2("TaskContractRecord", self.base),
            self.golden["record_hash"])
        flat = object_hash("TaskContractRecord", self.base)
        self.assertNotEqual(flat, self.golden["record_hash"])
        # exactly the generic construction over the full payload:
        self.assertEqual(flat, preimage2.domained_digest2(
            preimage2.object_label("TaskContractRecord"), self.base))

    def test_f_ti_2_unlisted_nested_members_enter_the_library_preimage(self):
        """F-TI-2. D-33: the preimage is 'the record's shape restricted to
        exactly the nine requirement fields' and identity.json lists them
        as dotted paths (and excludes specification_inputs.resolution_path
        by name). The library restricts at the TOP level only, so a member
        nested under a tiered parent enters its preimage; the documented
        construction ignores it. Unreachable for a frozen record: the
        closed chapter-1 schema refuses every such payload before any
        identity is minted - asserted here for each parent."""
        from contract_library import load_schemas, validate_payload
        schemas = load_schemas(str(support.REPO / "src" / "autobot"
                                   / "schemas"))
        expected = tuple(compute_tiers(self.base))
        self.assertEqual(preimage2.compute_tiers2(self.base), expected)
        self.assertTrue(validate_payload(
            "TaskContractRecord", self.base, schemas).ok)  # the control
        cases = (("source_domain", "unlisted_member", 0),
                 ("specification_inputs", "resolution_path", 0),
                 ("allowed_scope", "unlisted_member", 0),
                 ("acceptance_assertions", "unlisted_member", 0),
                 ("resolved_bindings", "unlisted_member", 1))
        for parent, member, first_moved_tier in cases:
            noisy = copy.deepcopy(self.base)
            noisy[parent][member] = "outside every tier"
            self.assertEqual(preimage2.compute_tiers2(noisy), expected,
                             parent)
            library = tuple(compute_tiers(noisy))
            for tier in range(3):
                if tier < first_moved_tier:
                    self.assertEqual(library[tier], expected[tier], parent)
                else:
                    self.assertNotEqual(library[tier], expected[tier],
                                        parent)
            refusal = validate_payload("TaskContractRecord", noisy, schemas)
            self.assertFalse(refusal.ok, parent)
            self.assertIn("ERR_UNKNOWN_FIELD", refusal.codes(), parent)

    def test_f_ti_3_kind_grammar_is_enforced_but_undocumented(self):
        """F-TI-3. The library refuses an object kind outside a grammar
        (ERR_DOMAIN_KIND) that the frozen chapter-1 registries and
        specification never state; SEM-17 documents only kind/label and
        version agreement. The oracle implements the documented refusals
        and therefore hashes a kind the library refuses. Equality between
        the two is claimed over the kinds the library accepts."""
        payload = {"kind": "Manual_Attestation", "schema_version": "v1"}
        with self.assertRaises(DigestError) as caught:
            object_hash("Manual_Attestation", payload)
        self.assertEqual(caught.exception.code, "ERR_DOMAIN_KIND")
        self.assertTrue(preimage2.is_digest_text(
            preimage2.object_hash2("Manual_Attestation", payload)))


class SeededPropertyTests(unittest.TestCase):
    """D-58 oracle 3 at the preimage layer: seeded randomized inputs, the
    library and the oracle must agree on every one."""

    def test_canonical_bytes_three_way(self):
        """library, chapter-1 jcs2 and preimage2: three implementations,
        one byte string."""
        jcs2 = load_jcs2()
        rng = random.Random(SEED)
        for index in range(400):
            value = random_value(rng)
            ours = preimage2.canonical_bytes(value)
            self.assertEqual(canonicalize(value), ours,
                             "seed %d iteration %d: %r"
                             % (SEED, index, value))
            self.assertEqual(jcs2.canonicalize2(value), ours,
                             "seed %d iteration %d: %r"
                             % (SEED, index, value))

    def test_rfc8785_sorting_example(self):
        """RFC 8785 section 3.2.3's example order, through the rank remap:
        the surrogate-pair emoji sorts BEFORE U+FB33."""
        keys = ["\u20ac", "\r", "\ufb33", "1", "\U0001f600", "\u0080",
                "\u00f6"]
        ordered = sorted(keys, key=preimage2.utf16_rank)
        self.assertEqual(ordered, ["\r", "1", "\u0080", "\u00f6", "\u20ac",
                                   "\U0001f600", "\ufb33"])
        value = dict((key, 1) for key in keys)
        self.assertEqual(preimage2.canonical_bytes(value),
                         canonicalize(value))

    def test_content_addresses(self):
        rng = random.Random(SEED + 1)
        for index in range(300):
            data = bytes(bytearray(rng.randrange(256)
                                   for _ in range(rng.randint(0, 96))))
            self.assertEqual(content_address(data),
                             preimage2.content_address2(data),
                             "seed %d iteration %d" % (SEED + 1, index))
        large = bytes(bytearray(rng.randrange(256) for _ in range(200000)))
        self.assertEqual(content_address(large),
                         preimage2.content_address2(large))

    def test_object_hashes(self):
        kinds = ("DelegationContract", "RepositoryAuthorityDeclaration",
                 "VerifierRegistration", "TransformRegistration",
                 "AttemptContract", "ActualVerificationEnvironment",
                 "DuplicateAdmissionRecord", "TransitionRecord")
        rng = random.Random(SEED + 2)
        for index in range(300):
            kind = rng.choice(kinds)
            payload = dict((random_string(rng, 1), random_value(rng, 1))
                           for _ in range(rng.randint(0, 6)))
            payload["kind"] = kind
            payload["schema_version"] = "v1"
            self.assertEqual(
                object_hash(kind, payload),
                preimage2.object_hash2(kind, payload),
                "seed %d iteration %d: %r" % (SEED + 2, index, payload))

    def test_artifact_roots(self):
        rng = random.Random(SEED + 3)
        for index in range(300):
            shape = rng.choice(("native", "python", "manifest", "image"))
            if shape == "native":
                manifest = {"kind": "native",
                            "executable_sha256": random_digest(rng)}
            elif shape == "python":
                manifest = {
                    "kind": "python",
                    "script_sha256": random_digest(rng),
                    "interpreter_id": "cpython-3.%d.%d-windows-x86_64"
                                      % (rng.randint(8, 13),
                                         rng.randint(0, 20)),
                    "dependency_lock_sha256": random_digest(rng)}
            elif shape == "manifest":
                manifest = {"kind": "packaged",
                            "manifest_sha256": random_digest(rng)}
            else:
                manifest = {"kind": "packaged",
                            "image_digest": random_digest(rng)}
            self.assertEqual(
                artifact_root(manifest),
                preimage2.artifact_root2(manifest),
                "seed %d iteration %d: %r" % (SEED + 3, index, manifest))

    def test_identity_tiers(self):
        """Random walks over V-A's shape: every tier field varied, the
        optional members present and absent."""
        base = support.load_fixture("va_record.json")
        rng = random.Random(SEED + 4)
        seen = set()
        for index in range(300):
            record = copy.deepcopy(base)
            for _ in range(rng.randint(1, 6)):
                move = rng.randrange(12)
                if move == 0:
                    record["objective"] = random_string(rng, 1, 24)
                elif move == 1:
                    record["retry_policy"]["max_retries"] = rng.randint(
                        0, 2 ** 53 - 1)
                elif move == 2:
                    record["force_new_sequence"] = rng.random() < 0.5
                elif move == 3:
                    record.pop("delegation_id", None)
                    record["resolved_bindings"].pop("delegation_contract",
                                                    None)
                elif move == 4:
                    record["supersession_provenance"] = {
                        "supersedes": random_digest(rng),
                        "reason": random_string(rng, 1, 16)}
                elif move == 5:
                    record.pop("remote_verification_mode", None)
                elif move == 6:
                    record["frozen_utc"] = (
                        "2026-11-%02dT%02d:00:00.000Z"
                        % (rng.randint(1, 28), rng.randint(0, 23)))
                elif move == 7:
                    record["specification_inputs"][
                        "name_to_exact_byte_sha256"][
                        random_string(rng, 1, 8)] = random_digest(rng)
                elif move == 8:
                    record["source_domain"].pop("target_ref", None)
                elif move == 9:
                    record["resolved_bindings"][
                        "verifier_registrations"] = sorted(
                        random_digest(rng)
                        for _ in range(rng.randint(0, 3)))
                elif move == 10:
                    record["revision"] = rng.randint(1, 2 ** 31)
                else:
                    record["allowed_scope"]["paths"] = sorted(set(
                        "docs/" + random_string(rng, 1, 6).replace(
                            "\x00", "0") for _ in range(rng.randint(0, 3))))
            ours = preimage2.compute_tiers2(record)
            self.assertEqual(tuple(compute_tiers(record)), ours,
                             "seed %d iteration %d: %r"
                             % (SEED + 4, index, record))
            seen.add(ours)
        self.assertGreater(len(seen), 250)  # the walk really moves tiers

    def test_journal_chains(self):
        rng = random.Random(SEED + 5)
        for index in range(60):
            previous = None
            for _ in range(rng.randint(1, 6)):
                payload = dict(
                    (random_string(rng, 1), random_value(rng, 1))
                    for _ in range(rng.randint(1, 5)))
                ours = preimage2.journal_entry_hash2(payload, previous)
                self.assertEqual(
                    journal_entry_hash(payload, previous), ours,
                    "seed %d chain %d: %r" % (SEED + 5, index, payload))
                previous = ours


if __name__ == "__main__":
    unittest.main()
