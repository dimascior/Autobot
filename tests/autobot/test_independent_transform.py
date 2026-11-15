"""TI-1 WP3: the transform oracle and the real provider bytes.

(a) In-repo: ``transform2`` restates the calibrated A1 ingestion rule
    (strict UTF-8, BOM kept, universal newlines, nothing appended or
    stripped) through two mechanisms unlike the registered script's; the
    REGISTERED ``autoswarm-input-v1`` closure must agree with them over
    the adversarial byte families and over seeded random byte strings,
    valid and invalid UTF-8 alike.

(b) Local-only real boundary (the live_g2 convention): the operator's
    RETAINED AutoSwarm task records and prompt files, read in place. No
    provider byte is in the repository - the committed manifest holds
    sha256 identities only - and the arm's run/skip/fail policy is
    explicit: it runs when the evidence is present, skips visibly when it
    is absent, and FAILS on absence when ``AUTOBOT_REAL_BYTES=require``
    (set on the host that holds the evidence, so it cannot silently skip
    there).
"""

import json
import os
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path

import real_bytes
import support
import transform2
from test_independent_identity import assert_oracle_isolated
from autobot import Store, transforms
from autobot import witness_semantics
from autobot.models import ConflictError
from contract_library import content_address

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / real_bytes.MANIFEST_NAME

SEED = 20261113

BOM = "\ufeff".encode("utf-8")

# The adversarial byte families (the U-12 probe's, extended): every one is
# a distinct way newline, BOM or encoding handling can go wrong.
FAMILIES = (
    ("empty", b""),
    ("lf-only", support.VA_INPUT.encode("utf-8")),
    ("crlf", b"line one\r\nline two\r\n"),
    ("lone-cr", b"line one\rline two\r"),
    ("mixed", b"a\r\nb\rc\nd"),
    ("cr-cr-lf", b"a\r\r\nb"),
    ("lf-cr", b"a\n\rb"),
    ("trailing-cr", b"no final newline\r"),
    ("only-newlines", b"\r\n\r\r\n\n"),
    ("bom-crlf", BOM + b"do the thing\r\nline2\rline3\n"),
    ("bom-only", BOM),
    ("double-bom", BOM + BOM + b"x\r\n"),
    ("bom-mid", b"head\r\n" + BOM + b"tail"),
    ("no-final-newline", b"ends without one"),
    ("multibyte", "caf\u00e9 \u4e2d\u6587 \U0001f600\r\n".encode("utf-8")),
    ("nel-ls-ps", "a\u0085b\u2028c\u2029d\x0be\x0cf\r\n".encode("utf-8")),
    ("nul", b"a\x00b\r\nc"),
    ("long-crlf", b"0123456789abcdef\r\n" * 600),
    ("cr-at-slice-edges", (b"abcd\r" + b"\nxy\r") * 50),
    ("utf-16", "prompt text".encode("utf-16")),
    ("latin-1", b"caf\xe9\n"),
    ("truncated-multibyte", "ok \u4e2d".encode("utf-8")[:-1]),
    ("overlong", b"\xc0\xaf\r\n"),
    ("encoded-surrogate", b"\xed\xa0\x80\r\n"),
    ("stray-continuation", b"a\x80b"),
)
REFUSED_FAMILIES = ("utf-16", "latin-1", "truncated-multibyte", "overlong",
                    "encoded-surrogate", "stray-continuation")


def setUpModule():
    sys.stderr.write("test_independent_transform: SEED=%d\n" % SEED)


def run_registered_script(script, data, workdir):
    """Execute the EXACT registered script bytes in this interpreter under
    the closure's argv protocol (``transform.py <input> <output>``):
    returns (output bytes or None, exit code). Used for the large random
    corpus; ``test_in_process_execution_is_the_closure`` proves it is the
    registered closure's behavior."""
    input_path = os.path.join(workdir, "input.bin")
    output_path = os.path.join(workdir, "output.bin")
    with open(input_path, "wb") as handle:
        handle.write(data)
    if os.path.exists(output_path):
        os.remove(output_path)
    saved = sys.argv
    sys.argv = ["transform.py", input_path, output_path]
    try:
        exec(compile(script, "transform.py", "exec"),
             {"__name__": "__main__"})
        exit_code = 0
    except SystemExit as stop:
        exit_code = stop.code if isinstance(stop.code, int) else (
            0 if stop.code is None else 1)
    finally:
        sys.argv = saved
    output = None
    if exit_code == 0 and os.path.exists(output_path):
        with open(output_path, "rb") as handle:
            output = handle.read()
    return output, exit_code


def random_bytes_case(rng):
    """One seeded input: valid text, damaged text, or raw noise."""
    alphabet = ("abc XYZ 019\t.,-" + "\r" * 4 + "\n" * 4
                + "\ufeff\u00e9\u4e2d\u0085\u2028\U0001f600\x00")
    text = "".join(rng.choice(alphabet)
                   for _ in range(rng.randint(0, 40)))
    data = bytearray(text.encode("utf-8"))
    shape = rng.randrange(6)
    if shape == 0 and data:  # truncate (often mid-sequence)
        del data[rng.randrange(len(data)):]
    elif shape == 1:  # insert one arbitrary byte
        data.insert(rng.randint(0, len(data)), rng.randrange(256))
    elif shape == 2:  # raw noise
        data = bytearray(rng.randrange(256)
                         for _ in range(rng.randint(1, 24)))
    return bytes(data)


class ReferenceTransformTests(unittest.TestCase):
    """WP3(a): the registered closure against two independent references."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "store")
        for transform_id in sorted(support.LIVE_TRANSFORM_SPECS):
            cls.store.put(support.LIVE_TRANSFORM_SPECS[transform_id][1])
        cls.store.put(support.LIVE_LOCK)
        cls.script = support.LIVE_TRANSFORM_SPECS["autoswarm-input-v1"][1]
        cls.workdir = str(Path(cls.tmp.name) / "exec")
        os.makedirs(cls.workdir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _closure(self, data, transform_id="autoswarm-input-v1"):
        """The REAL registered closure: resolved, materialized from the
        content store and executed in its own process."""
        output, execution = transforms.execute_transform(
            self.store, support.live_transform_registration(transform_id),
            data)
        return output, execution["exit_code"]

    def test_references_import_only_the_standard_library(self):
        assert_oracle_isolated(self, HERE / "transform2.py",
                               allowed=("codecs", "io"))
        assert_oracle_isolated(self, HERE / "real_bytes.py",
                               allowed=("hashlib", "json", "os", "re",
                                        "sys"))

    def test_registered_closure_equals_both_references_on_the_families(self):
        for name, data in FAMILIES:
            with self.subTest(family=name):
                expected = transform2.ingest(data)
                self.assertEqual(transform2.ingest_automaton(data),
                                 expected)
                output, exit_code = self._closure(data)
                self.assertEqual(output, expected)
                if name in REFUSED_FAMILIES:
                    self.assertIsNone(expected)
                    self.assertEqual(exit_code, 3)  # refused, never guessed
                else:
                    self.assertIsNotNone(expected)
                    self.assertEqual(exit_code, 0)
                    # the rule's visible properties, on the reference
                    self.assertNotIn(b"\r", expected)
                    self.assertEqual(expected.startswith(BOM),
                                     data.startswith(BOM))
                    self.assertEqual(
                        expected, data.replace(b"\r\n", b"\n").replace(
                            b"\r", b"\n"))

    def test_identity_projection_is_byte_identity_on_every_family(self):
        """The other registered transform decodes nothing: bytes in, the
        same bytes out - invalid UTF-8 included."""
        for name, data in FAMILIES[::4]:
            with self.subTest(family=name):
                output, exit_code = self._closure(data,
                                                  "identity-projection")
                self.assertEqual((output, exit_code), (data, 0))

    def test_in_process_execution_is_the_closure(self):
        """The bridge that licenses the large corpus below: the exact
        registered script bytes executed in-process behave exactly as the
        materialized closure does in its own process."""
        rng = random.Random(SEED)
        cases = [data for _name, data in FAMILIES[::3]]
        cases += [random_bytes_case(rng) for _ in range(12)]
        for index, data in enumerate(cases):
            self.assertEqual(
                run_registered_script(self.script, data, self.workdir),
                self._closure(data),
                "seed %d case %d: %r" % (SEED, index, data))

    def test_registered_script_equals_references_on_seeded_random_bytes(self):
        rng = random.Random(SEED + 1)
        accepted = refused = changed = 0
        for index in range(800):
            data = random_bytes_case(rng)
            expected = transform2.ingest(data)
            self.assertEqual(
                transform2.ingest_automaton(data), expected,
                "the two references disagree: seed %d iteration %d: %r"
                % (SEED + 1, index, data))
            output, exit_code = run_registered_script(
                self.script, data, self.workdir)
            self.assertEqual(
                output, expected,
                "seed %d iteration %d: %r" % (SEED + 1, index, data))
            if expected is None:
                refused += 1
                self.assertEqual(exit_code, 3)
            else:
                accepted += 1
                self.assertEqual(exit_code, 0)
                if expected != data:
                    changed += 1
        # the corpus is not vacuous in any direction
        self.assertGreater(accepted, 200)
        self.assertGreater(refused, 100)
        self.assertGreater(changed, 150)

    def test_ingestion_is_idempotent_and_one_byte_sensitive(self):
        """Applying the rule to its own output changes nothing, and a
        single appended or removed byte is a different output identity."""
        rng = random.Random(SEED + 2)
        for index in range(200):
            data = random_bytes_case(rng)
            once = transform2.ingest(data)
            if once is None:
                continue
            self.assertEqual(transform2.ingest(once), once,
                             "seed %d iteration %d" % (SEED + 2, index))
            self.assertNotEqual(transform2.ingest(once + b"x"), once)
            if once:
                self.assertNotEqual(transform2.ingest(once[:-1]), once)


class RealBytesPolicyTests(unittest.TestCase):
    """What holds on EVERY machine: the run/skip/fail policy itself and the
    committed manifest's shape (identities only)."""

    def test_absence_is_a_skip_unless_the_operator_requires_the_arm(self):
        self.assertEqual(real_bytes.decide(True, None), "run")
        self.assertEqual(real_bytes.decide(True, "require"), "run")
        self.assertEqual(real_bytes.decide(False, None), "skip")
        self.assertEqual(real_bytes.decide(False, "anything-else"), "skip")
        self.assertEqual(real_bytes.decide(False, "require"), "fail")

    def test_locations_follow_the_environment(self):
        calls, prompts = real_bytes.locations(
            {"AUTOSWARM_HOME": "X:/home",
             "AUTOBOT_PROMPT_EVIDENCE_DIR": "Y:/prompts"})
        self.assertEqual((calls.replace("\\", "/"), prompts),
                         ("X:/home/calls", "Y:/prompts"))
        calls, prompts = real_bytes.locations({})
        self.assertEqual(prompts, real_bytes.DEFAULT_PROMPT_DIR)
        self.assertTrue(calls.startswith(real_bytes.DEFAULT_AUTOSWARM_HOME))

    def test_reader_over_a_synthetic_directory(self):
        """The reader and classifier, exercised with bytes written HERE
        (so this logic is proven even where the real evidence is absent):
        a file-identical prompt, a PS 5.1-style composed prompt, an inline
        prompt, an unreadable record."""
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp) / "prompts"
            calls = Path(tmp) / "home" / "calls"
            prompts.mkdir()
            body = "line one\nline two\n"
            (prompts / "luna.txt").write_bytes(body.encode("utf-8"))
            (prompts / "monitor.txt").write_bytes(b"watch it\n")
            composed = ("\ufeffLUNA_CALL_ID=call_" + "a" * 32 + "\n"
                        + "watch it\n\n")
            for call_id, prompt in (("call_" + "1" * 32, body),
                                    ("call_" + "2" * 32, composed),
                                    ("call_" + "3" * 32, "do one thing")):
                (calls / call_id).mkdir(parents=True)
                (calls / call_id / "task.json").write_text(
                    json.dumps({"call_id": call_id, "prompt": prompt},
                               indent=2), encoding="utf-8")
            (calls / "call_broken").mkdir()
            (calls / "call_broken" / "task.json").write_bytes(b"\xff{")
            records = real_bytes.load_task_records(str(calls))
            prompt_files = real_bytes.load_prompt_files(str(prompts))
            manifest = real_bytes.build_manifest(str(calls), str(prompts))
        first, second, third = ("call_" + digit * 32 for digit in "123")
        # the unreadable record is not a record
        self.assertEqual(sorted(records), [first, second, third])
        # the LOCAL detail the arm works with (never committed)
        local = dict((name, real_bytes.classify(records[name]["prompt"],
                                                prompt_files))
                     for name in records)
        self.assertEqual((local[first]["class"], local[first]["input_file"]),
                         (real_bytes.CLASS_FILE, "luna.txt"))
        self.assertEqual(
            (local[second]["class"], local[second]["header_key"],
             local[second]["header_call_id"], local[second]["body_file"]),
            (real_bytes.CLASS_COMPOSED, "LUNA_CALL_ID", "call_" + "a" * 32,
             "monitor.txt"))
        self.assertEqual(local[third]["class"], real_bytes.CLASS_INLINE)
        # the COMMITTED projection: identities and class tokens only
        luna = real_bytes.sha256_text(body.encode("utf-8"))
        monitor = real_bytes.sha256_text(b"watch it\n")
        by_identity = dict((entry["task_record_sha256"], entry)
                           for entry in manifest["task_records"])
        self.assertEqual(len(by_identity), 3)

        def projected(name):
            return by_identity[real_bytes.sha256_text(records[name]["bytes"])]

        self.assertEqual(projected(first), {
            "task_record_sha256": real_bytes.sha256_text(
                records[first]["bytes"]),
            "prompt_sha256": luna, "class": real_bytes.CLASS_FILE,
            "input_sha256": luna})
        self.assertEqual(projected(second), {
            "task_record_sha256": real_bytes.sha256_text(
                records[second]["bytes"]),
            "prompt_sha256": real_bytes.sha256_text(
                composed.encode("utf-8")),
            "class": real_bytes.CLASS_COMPOSED, "body_sha256": monitor})
        self.assertEqual(sorted(projected(third)),
                         ["class", "prompt_sha256", "task_record_sha256"])
        self.assertEqual(manifest["prompt_files"], sorted([luna, monitor]))
        self.assertEqual(
            [entry["task_record_sha256"]
             for entry in manifest["task_records"]],
            sorted(by_identity))
        text = real_bytes.manifest_text(manifest)
        for leaked in ("call_", "luna.txt", "monitor.txt", "LUNA_CALL_ID",
                       "watch it", "line one"):
            self.assertNotIn(leaked, text)

    def test_committed_manifest_holds_identities_only(self):
        """TI-1 section 7c, literally: the committed manifest is sha256
        identities and class tokens - no call id, no file name, no size,
        no provider or prompt text."""
        manifest = support.load_json(MANIFEST_PATH)
        self.assertEqual(manifest["schema"], real_bytes.MANIFEST_SCHEMA)
        self.assertEqual(sorted(manifest),
                         ["note", "prompt_files", "schema", "task_records"])
        allowed = {"task_record_sha256", "prompt_sha256", "class",
                   "input_sha256", "body_sha256"}
        tokens = (real_bytes.CLASS_FILE, real_bytes.CLASS_COMPOSED,
                  real_bytes.CLASS_INLINE)
        digest = re.compile("^sha256:[0-9a-f]{64}$")
        for entry in manifest["task_records"]:
            self.assertEqual(set(entry) - allowed, set())
            for member, value in sorted(entry.items()):
                if member == "class":
                    self.assertIn(value, tokens)
                else:
                    self.assertTrue(digest.match(value), member)
        self.assertTrue(manifest["prompt_files"])
        for identity in manifest["prompt_files"]:
            self.assertTrue(digest.match(identity))
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        for leaked in ("call_", ".txt", "CALL_ID", "bytes\":"):
            self.assertNotIn(leaked, text)
        classes = [entry["class"] for entry in manifest["task_records"]]
        self.assertEqual(classes.count(real_bytes.CLASS_FILE), 4)
        self.assertEqual(classes.count(real_bytes.CLASS_COMPOSED), 6)
        self.assertEqual(len(classes), 19)


class RealProviderBytesTests(unittest.TestCase):
    """WP3(b): the A1 chain over REAL bytes, where they exist."""

    @classmethod
    def setUpClass(cls):
        cls.calls_dir, cls.prompt_dir = real_bytes.locations()
        present = (os.path.isdir(cls.calls_dir)
                   and os.path.isdir(cls.prompt_dir))
        decision = real_bytes.decide(
            present, os.environ.get(real_bytes.MARKER_ENV))
        if decision == "fail":
            raise AssertionError(
                "%s=require but the retained provider evidence is absent"
                " (%s, %s): this arm must RUN on the host that holds it"
                % (real_bytes.MARKER_ENV, cls.calls_dir, cls.prompt_dir))
        if decision == "skip":
            raise unittest.SkipTest(
                "the retained AutoSwarm records are not on this machine"
                " (%s); set %s=require to make their absence a failure"
                % (cls.calls_dir, real_bytes.MARKER_ENV))
        cls.manifest = support.load_json(MANIFEST_PATH)
        cls.records = real_bytes.load_task_records(cls.calls_dir)
        cls.prompt_files = real_bytes.load_prompt_files(cls.prompt_dir)
        # The manifest names nothing but identities, so the live evidence
        # is looked up BY identity: record bytes and prompt-file bytes.
        cls.by_identity = {}
        for directory, record in cls.records.items():
            record["directory"] = directory
            cls.by_identity[real_bytes.sha256_text(record["bytes"])] = record
        cls.files_by_identity = dict(
            (real_bytes.sha256_text(data), data)
            for data in cls.prompt_files.values())
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.tmp.name) / "store")
        for transform_id in sorted(support.LIVE_TRANSFORM_SPECS):
            cls.store.put(support.LIVE_TRANSFORM_SPECS[transform_id][1])
        cls.store.put(support.LIVE_LOCK)
        cls.registration = support.live_transform_registration(
            "autoswarm-input-v1")

    @classmethod
    def tearDownClass(cls):
        tmp = getattr(cls, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def _entries(self, record_class):
        entries = [entry for entry in self.manifest["task_records"]
                   if entry["class"] == record_class]
        self.assertTrue(entries)
        return entries

    def _prompt(self, entry):
        """The live record for one manifest entry: it must still be the
        bytes the manifest identifies."""
        record = self.by_identity.get(entry["task_record_sha256"])
        self.assertIsNotNone(
            record, "no retained record has the identity %s any more; if"
                    " the evidence was pruned or rewritten on purpose,"
                    " regenerate the manifest (python"
                    " tests/autobot/real_bytes.py tests/autobot/%s)"
                    % (entry["task_record_sha256"], real_bytes.MANIFEST_NAME))
        self.assertEqual(
            real_bytes.sha256_text(record["prompt"].encode("utf-8")),
            entry["prompt_sha256"])
        return record

    def test_every_manifest_record_is_present_and_unchanged(self):
        """The census: all 19 identified records exist with their exact
        bytes (newer records on disk are simply not part of this proof)."""
        self.assertEqual(len(self.manifest["task_records"]), 19)
        self.assertEqual(
            len(set(entry["task_record_sha256"]
                    for entry in self.manifest["task_records"])), 19)
        for entry in self.manifest["task_records"]:
            self._prompt(entry)
        for identity in self.manifest["prompt_files"]:
            self.assertIn(identity, self.files_by_identity)
        live = real_bytes.build_manifest(self.calls_dir, self.prompt_dir)
        known = set(entry["task_record_sha256"]
                    for entry in self.manifest["task_records"])
        self.assertEqual(
            [entry for entry in live["task_records"]
             if entry["task_record_sha256"] in known],
            self.manifest["task_records"])

    def test_recorded_prompt_is_the_registered_closure_output(self):
        """INV-44/INV-49 over real bytes: for each Luna record whose input
        file is retained, the REGISTERED closure executed over the exact
        file bytes outputs exactly the prompt the provider recorded; the
        independent references agree; and the production observed-side
        derivation over the exact task.json bytes yields that identity."""
        entries = self._entries(real_bytes.CLASS_FILE)
        self.assertEqual(len(entries), 4)
        for entry in entries:
            with self.subTest(record=entry["task_record_sha256"]):
                record = self._prompt(entry)
                source = self.files_by_identity[entry["input_sha256"]]
                output, execution = transforms.execute_transform(
                    self.store, self.registration, source)
                self.assertEqual(execution["exit_code"], 0)
                recorded = record["prompt"].encode("utf-8")
                self.assertEqual(output, recorded)
                self.assertEqual(execution["output_sha256"],
                                 entry["prompt_sha256"])
                self.assertEqual(content_address(output),
                                 entry["prompt_sha256"])
                self.assertEqual(transform2.ingest(source), recorded)
                self.assertEqual(transform2.ingest_automaton(source),
                                 recorded)
                facts = witness_semantics.extract_facts("obj-11",
                                                        record["bytes"])
                self.assertEqual(facts["prompt_sha256"],
                                 entry["prompt_sha256"])
                self.assertEqual(facts["call_id"], record["directory"])

    def test_ps51_composed_prompts_show_the_calibrated_signature(self):
        """The six PowerShell 5.1-composed monitor prompts: the BOM is
        KEPT as U+FEFF, no CR survives ingestion, the PS-appended
        terminator is present, the registered closure is a fixed point on
        the recorded bytes and maps their CRLF form back to them - and
        where the body's prompt file is retained, the recorded prompt is
        exactly BOM + header + that file's bytes + the terminator."""
        entries = self._entries(real_bytes.CLASS_COMPOSED)
        self.assertEqual(len(entries), 6)
        with_body = 0
        for entry in entries:
            with self.subTest(record=entry["task_record_sha256"]):
                record = self._prompt(entry)
                prompt = record["prompt"]
                recorded = prompt.encode("utf-8")
                self.assertTrue(recorded.startswith(BOM))
                self.assertNotIn(b"\r", recorded)
                self.assertTrue(recorded.endswith(b"\n"))
                # the header is read from the LIVE record, never committed
                local = real_bytes.classify(prompt, self.prompt_files)
                self.assertEqual(local["class"], real_bytes.CLASS_COMPOSED)
                header = "\ufeff%s=%s\n" % (local["header_key"],
                                            local["header_call_id"])
                self.assertTrue(prompt.startswith(header))
                # the header names a retained sibling execution
                self.assertIn(local["header_call_id"], self.records)
                output, execution = transforms.execute_transform(
                    self.store, self.registration, recorded)
                self.assertEqual((output, execution["exit_code"]),
                                 (recorded, 0))
                as_ps51_emits = recorded.replace(b"\n", b"\r\n")
                self.assertNotEqual(as_ps51_emits, recorded)
                output, _execution = transforms.execute_transform(
                    self.store, self.registration, as_ps51_emits)
                self.assertEqual(output, recorded)
                self.assertEqual(transform2.ingest(as_ps51_emits), recorded)
                self.assertEqual(local.get("body_sha256"),
                                 entry.get("body_sha256"))
                if "body_sha256" in entry:
                    with_body += 1
                    body = self.files_by_identity[entry["body_sha256"]]
                    self.assertEqual(
                        recorded,
                        header.encode("utf-8") + body + b"\n")
        self.assertEqual(with_body, 5)

    def test_a_real_record_cannot_be_admitted_into_another_execution(self):
        """D-C3-5 over real producer bytes: a retained task record names
        ITS OWN call, so the witness seam refuses it for any other
        attempt - real evidence cannot be replayed into a synthetic
        execution (which is also why the positive proofs above stop at the
        derivation seams rather than a fabricated admission)."""
        entry = self._entries(real_bytes.CLASS_FILE)[0]
        record = self._prompt(entry)
        with tempfile.TemporaryDirectory() as tmp:
            rt, admitted = support.admitted_runtime(Path(tmp))
            try:
                seq = rt.sequencer
                seq.acquire_lease(ttl_seconds=3600)
                seq.mark_ready(admitted["sequence_id"])
                info = seq.start_execution(admitted["sequence_id"])
                seq.reserve_attempt(info["attempt_id"], info["fence"])
                projection = rt.store.put(b"projection for the real-bytes"
                                          b" replay negative\n")
                seq.dispatch_attempt(info["attempt_id"], "builder", "fake",
                                     projection)
                with self.assertRaises(ConflictError) as caught:
                    seq.admit_witness(info["attempt_id"], "obj-11",
                                      "autoswarm", record["bytes"])
                self.assertIn("cross-execution identity",
                              str(caught.exception))
            finally:
                rt.db.close()


if __name__ == "__main__":
    unittest.main()
