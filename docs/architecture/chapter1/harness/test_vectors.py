"""The 19 vector dispositions, P-1/P-2 and JH-1: rebuild everything and
compare against the frozen goldens on disk (D-58 oracle 2)."""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import gen_goldens  # noqa: E402

GOLDEN_PATH = ROOT / "vectors" / "golden" / "golden_digests.json"
VECTOR_IDS = ["V-%s" % c for c in "ABCDEFGHIJKLMNOPQRS"]


class TestGoldenVectors(unittest.TestCase):
    def test_rebuild_matches_frozen_goldens(self):
        self.assertTrue(GOLDEN_PATH.exists(), "run gen_goldens first (run_all.py does)")
        frozen = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        rebuilt, _, _, _ = gen_goldens.build()
        self.assertEqual(rebuilt, frozen, "recomputation diverged from the frozen goldens")

    def test_every_vector_dispositioned(self):
        frozen = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        for vid in VECTOR_IDS + ["P-1", "P-2", "JH-1"]:
            self.assertIn(vid, frozen, "missing %s" % vid)
            self.assertIn(frozen[vid].get("disposition"), ("executable", "rejection"), vid)

    def test_resolution_registry_agrees(self):
        frozen = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        resolution = json.loads(
            (ROOT / "registries" / "vectors_resolution.json").read_text(encoding="utf-8"))
        by_id = {v["id"]: v for v in resolution["vectors"]}
        for vid in VECTOR_IDS:
            self.assertIn(vid, by_id)
            self.assertEqual(by_id[vid]["disposition"], frozen[vid]["disposition"], vid)
            if by_id[vid]["disposition"] == "rejection":
                self.assertEqual(by_id[vid]["code"], frozen[vid]["code"], vid)

    def test_tier_cascade_property(self):
        # INV-29 / D-33: a requirement change moves all three; a contract-tier
        # change never moves the requirement digest; a record-tier change
        # moves only the record hash. Read off the frozen vectors.
        frozen = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        base = frozen["V-A"]
        for vid, expected in (("V-H", True), ("V-M", True), ("V-D", True)):
            self.assertNotEqual(frozen[vid]["requirement_digest"], base["requirement_digest"], vid)
            self.assertNotEqual(frozen[vid]["contract_digest"], base["contract_digest"], vid)
            self.assertNotEqual(frozen[vid]["record_hash"], base["record_hash"], vid)
        for vid in ("V-C", "V-F", "V-N", "V-P", "V-R"):
            self.assertEqual(frozen[vid]["requirement_digest"], base["requirement_digest"], vid)
            self.assertNotEqual(frozen[vid]["contract_digest"], base["contract_digest"], vid)
            self.assertNotEqual(frozen[vid]["record_hash"], base["record_hash"], vid)
        for vid in ("V-E", "V-O"):
            self.assertEqual(frozen[vid]["contract_digest"], base["contract_digest"], vid)
            self.assertNotEqual(frozen[vid]["record_hash"], base["record_hash"], vid)


if __name__ == "__main__":
    unittest.main()
