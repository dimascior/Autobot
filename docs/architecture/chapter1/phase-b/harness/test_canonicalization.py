"""Canonicalization conformance: RFC 8785 behavior on the D-24 subset,
dual-oracle agreement (D-58 oracle 1), and the property tests (oracle 3)."""

import random
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from autobot_contract import CanonicalizationError, canonicalize  # noqa: E402
from jcs2 import canonicalize2  # noqa: E402

SEED = 20261108


class TestJcsBehavior(unittest.TestCase):
    def test_rfc_property_name_sorting(self):
        # RFC 8785 section 3.2.3's sorting example (string values dropped):
        # expected order by UTF-16 code units, with the surrogate-pair emoji
        # before U+FB33.
        keys = ["€", "\r", "דּ", "1", "\U0001f600", "\u0080", "ö"]
        value = {k: 1 for k in keys}
        text = canonicalize(value).decode("utf-8")
        expected_order = ["\\r", '"1"', "\u0080", "ö", "€", "\U0001f600", "דּ"]
        positions = [text.index(marker) for marker in expected_order]
        self.assertEqual(positions, sorted(positions), "UTF-16 code-unit order violated: %r" % text)
        self.assertEqual(canonicalize(value), canonicalize2(value))

    def test_escaping(self):
        value = {"s": "a\"b\\c\bd\te\nf\fg\rh\x01i"}
        out = canonicalize(value).decode("utf-8")
        self.assertIn('"a\\"b\\\\c\\bd\\te\\nf\\fg\\rh\\u0001i"', out)
        self.assertEqual(canonicalize(value), canonicalize2(value))

    def test_non_bmp_literal(self):
        out = canonicalize({"emoji": "\U0001f600"})
        self.assertIn("\U0001f600".encode("utf-8"), out)

    def test_integers(self):
        self.assertEqual(canonicalize(9007199254740991), b"9007199254740991")
        self.assertEqual(canonicalize(0), b"0")
        for bad in (9007199254740992, -1):
            with self.assertRaises(CanonicalizationError):
                canonicalize(bad)
            with self.assertRaises(ValueError):
                canonicalize2(bad)

    def test_null_and_float_forbidden(self):
        for bad in (None, 1.5, {"a": None}, [0.0]):
            with self.assertRaises(CanonicalizationError):
                canonicalize(bad)
            with self.assertRaises(ValueError):
                canonicalize2(bad)

    def test_booleans_are_not_integers(self):
        self.assertEqual(canonicalize({"b": True, "i": 1}), b'{"b":true,"i":1}')

    def test_absence_vs_null(self):
        with_key = canonicalize({"a": 1, "b": "x"})
        without = canonicalize({"a": 1})
        self.assertNotEqual(with_key, without)
        self.assertEqual(without, b'{"a":1}')

    def test_key_order_invariance(self):
        a = {"x": [1, 2], "a": {"n": 1, "m": 2}}
        b = {"a": {"m": 2, "n": 1}, "x": [1, 2]}
        self.assertEqual(canonicalize(a), canonicalize(b))

    def test_array_order_preserved(self):
        self.assertNotEqual(canonicalize(["a", "b"]), canonicalize(["b", "a"]))


class TestDualOracleProperties(unittest.TestCase):
    """Oracle 3: seeded randomized values; the two implementations must agree."""

    def _random_string(self, rng):
        alphabet = (
            "abcXYZ019 _-./\\\"'\t\n\r"
            "éö€\u0080Α中דּ"
            "\U0001f600\U0001d11e"
        )
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))

    def _random_value(self, rng, depth):
        choices = ["str", "int", "bool"]
        if depth < 3:
            choices += ["dict", "list"]
        kind = rng.choice(choices)
        if kind == "str":
            return self._random_string(rng)
        if kind == "int":
            return rng.choice([0, 1, 7, 2 ** 31, 2 ** 53 - 1, rng.randint(0, 2 ** 53 - 1)])
        if kind == "bool":
            return rng.random() < 0.5
        if kind == "list":
            return [self._random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
        return {
            self._random_string(rng): self._random_value(rng, depth + 1)
            for _ in range(rng.randint(0, 5))
        }

    def test_random_agreement(self):
        rng = random.Random(SEED)
        for i in range(300):
            value = self._random_value(rng, 0)
            self.assertEqual(
                canonicalize(value), canonicalize2(value),
                "dual-oracle disagreement on iteration %d: %r" % (i, value),
            )

    def test_roundtrip_stability(self):
        import json
        rng = random.Random(SEED + 1)
        for _ in range(100):
            value = self._random_value(rng, 0)
            once = canonicalize(value)
            again = canonicalize(json.loads(once.decode("utf-8")))
            self.assertEqual(once, again, "canonical form is not a fixed point")


if __name__ == "__main__":
    unittest.main()
