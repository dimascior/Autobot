"""D-59: the contract package depends on the standard library only.
Two proofs: a static import scan of the sources, and a fresh -I subprocess
import that must pull in no networking, process, or storage module."""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "contract_library"

ALLOWED_IMPORTS = {"json", "hashlib", "re", "unicodedata", "pathlib", "copy"}
# urllib is deliberately absent: on Python 3.8 pathlib itself imports
# urllib.parse (for as_uri), which performs no I/O; the library never
# imports urllib directly (the static scan proves that).
FORBIDDEN_TOPLEVEL = {
    "socket", "ssl", "http", "subprocess", "sqlite3", "ctypes",
    "multiprocessing", "asyncio", "requests",
}
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)", re.MULTILINE)


class TestIsolation(unittest.TestCase):
    def test_static_import_scan(self):
        for path in sorted(SRC.glob("*.py")):
            for module in _IMPORT_RE.findall(path.read_text(encoding="utf-8")):
                top = module.split(".")[0]
                if top in ("contract_library",) or module.startswith("."):
                    continue
                self.assertIn(top, ALLOWED_IMPORTS,
                              "%s imports %r outside the D-59 allowlist" % (path.name, module))

    def test_runtime_import_graph(self):
        code = (
            "import sys, json\n"
            "sys.path.insert(0, %r)\n"
            "import contract_library\n"
            "mods = sorted(set(m.split('.')[0] for m in sys.modules))\n"
            "json.dump(mods, sys.stdout)\n" % str(ROOT)
        )
        result = subprocess.run([sys.executable, "-I", "-c", code],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = set(json.loads(result.stdout))
        hit = loaded & FORBIDDEN_TOPLEVEL
        self.assertFalse(hit, "forbidden modules loaded by import: %s" % sorted(hit))


if __name__ == "__main__":
    unittest.main()
