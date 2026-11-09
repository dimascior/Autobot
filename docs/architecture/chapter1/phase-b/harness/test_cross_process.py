"""D-58 oracle 5: a separate OS process (fresh interpreter, isolated mode)
recomputes every golden digest from the written fixtures; the values must be
byte-identical — the Registry and the Sequencer recomputing independently."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


class TestCrossProcess(unittest.TestCase):
    def test_child_process_agrees(self):
        golden = json.loads(
            (ROOT / "vectors" / "golden" / "golden_digests.json").read_text(encoding="utf-8"))
        result = subprocess.run(
            [sys.executable, "-I", str(HERE / "child_recompute.py"),
             str(ROOT / "src"), str(ROOT / "vectors" / "fixtures")],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        child = json.loads(result.stdout)
        self.assertEqual(child["object_hashes"], golden["object_hashes"])
        for vid in ("V-A", "V-C", "V-D", "V-E", "V-F", "V-H", "V-M", "V-N", "V-O", "V-P", "V-R"):
            for tier in ("requirement_digest", "contract_digest", "record_hash"):
                self.assertEqual(child[vid][tier], golden[vid][tier], "%s %s" % (vid, tier))


if __name__ == "__main__":
    unittest.main()
