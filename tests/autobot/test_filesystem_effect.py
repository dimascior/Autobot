"""Filesystem effect tests."""

import os
import tempfile
import unittest

import support

from autobot.filesystem_effect import (
    content_address,
    observe_destination,
    place_artifact,
    build_placement_receipt,
)


class ContentAddressTests(unittest.TestCase):

    def test_deterministic(self):
        data = b"hello world"
        a1 = content_address(data)
        a2 = content_address(data)
        self.assertEqual(a1, a2)
        self.assertTrue(a1.startswith("sha256:"))

    def test_different_data(self):
        self.assertNotEqual(content_address(b"a"), content_address(b"b"))

    def test_empty(self):
        addr = content_address(b"")
        self.assertTrue(addr.startswith("sha256:"))


class ObserveDestinationTests(unittest.TestCase):

    def test_nonexistent(self):
        obs = observe_destination("/nonexistent/path/xyz")
        self.assertFalse(obs["exists"])
        self.assertIsNone(obs["content_address"])

    def test_existing_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"test content")
            path = f.name
        try:
            obs = observe_destination(path)
            self.assertTrue(obs["exists"])
            self.assertEqual(obs["content_address"],
                             content_address(b"test content"))
            self.assertEqual(obs["size"], 12)
        finally:
            os.unlink(path)


class PlaceArtifactTests(unittest.TestCase):

    def test_place_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "output", "result.txt")
            result = place_artifact(b"artifact data", dest)
            self.assertEqual(result["outcome"], "HAPPENED")
            self.assertEqual(result["source_address"],
                             content_address(b"artifact data"))
            self.assertTrue(os.path.isfile(dest))
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"artifact data")

    def test_place_with_cas_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "file.txt")
            with open(dest, "wb") as f:
                f.write(b"old content")
            old_addr = content_address(b"old content")
            result = place_artifact(b"new content", dest,
                                    expected_prior_address=old_addr)
            self.assertEqual(result["outcome"], "HAPPENED")

    def test_place_with_cas_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "file.txt")
            with open(dest, "wb") as f:
                f.write(b"actual content")
            result = place_artifact(
                b"new content", dest,
                expected_prior_address="sha256:wrong")
            self.assertEqual(result["outcome"], "NOT_HAPPENED")
            self.assertEqual(result["reason"], "prior_mismatch")
            with open(dest, "rb") as f:
                self.assertEqual(f.read(), b"actual content")

    def test_place_to_nonexistent_with_expected_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "new.txt")
            result = place_artifact(b"data", dest,
                                    expected_prior_address=None)
            self.assertEqual(result["outcome"], "HAPPENED")

    def test_verified_post_observation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "verified.txt")
            result = place_artifact(b"verified data", dest)
            self.assertEqual(
                result["post_observation"]["content_address"],
                result["source_address"])


class PlacementReceiptTests(unittest.TestCase):

    def test_receipt_from_happened(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "r.txt")
            result = place_artifact(b"bytes", dest)
            receipt = build_placement_receipt("intent-001", result)
            self.assertEqual(receipt["kind"],
                             "FilesystemPlacementReceipt")
            self.assertEqual(receipt["intent_id"], "intent-001")
            self.assertEqual(receipt["outcome"], "HAPPENED")
            self.assertTrue(receipt["verified"])
            self.assertEqual(receipt["post_address"],
                             receipt["source_address"])

    def test_receipt_from_not_happened(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "nr.txt")
            with open(dest, "wb") as f:
                f.write(b"existing")
            result = place_artifact(
                b"new", dest,
                expected_prior_address="sha256:wrong")
            receipt = build_placement_receipt("intent-002", result)
            self.assertEqual(receipt["outcome"], "NOT_HAPPENED")
            self.assertFalse(receipt["verified"])

    def test_receipt_prior_address(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "prior.txt")
            with open(dest, "wb") as f:
                f.write(b"old")
            old_addr = content_address(b"old")
            result = place_artifact(b"new", dest,
                                    expected_prior_address=old_addr)
            receipt = build_placement_receipt("intent-003", result)
            self.assertEqual(receipt["prior_address"], old_addr)


if __name__ == "__main__":
    unittest.main()
