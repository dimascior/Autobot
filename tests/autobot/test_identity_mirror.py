"""D-65: the packaged runtime mirrors are byte-identical to the frozen
Chapter 1 artifacts — contract_library file for file, and the six v1 schema
files. The frozen chapter copy is the provenance source; this test is the
executable guarantee the runtime imports exactly those bytes."""

import unittest

import support


class MirrorIdentityTests(unittest.TestCase):
    def _assert_tree_identical(self, frozen_dir, mirror_dir, pattern):
        frozen = {p.name: p for p in sorted(frozen_dir.glob(pattern))}
        mirror = {p.name: p for p in sorted(mirror_dir.glob(pattern))}
        self.assertEqual(
            sorted(frozen), sorted(mirror),
            "file sets differ between %s and %s" % (frozen_dir, mirror_dir),
        )
        for name in sorted(frozen):
            self.assertEqual(
                frozen[name].read_bytes(), mirror[name].read_bytes(),
                "byte drift in %s (D-65 identity broken)" % name,
            )

    def test_contract_library_mirror_is_byte_identical(self):
        self._assert_tree_identical(
            support.CH1 / "contract_library",
            support.REPO / "src" / "contract_library",
            "*.py",
        )

    def test_contract_library_mirror_has_no_extra_files(self):
        mirror = support.REPO / "src" / "contract_library"
        extras = [
            p.name for p in mirror.rglob("*")
            if p.is_file() and p.suffix != ".py" and "__pycache__" not in p.parts
        ]
        self.assertEqual(extras, [], "unexpected files in the mirror")

    def test_schema_mirror_is_byte_identical(self):
        self._assert_tree_identical(
            support.CH1 / "schemas",
            support.REPO / "src" / "autobot" / "schemas",
            "*.schema.json",
        )


if __name__ == "__main__":
    unittest.main()
