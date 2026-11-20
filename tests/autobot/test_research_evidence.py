"""Research evidence model tests."""

import unittest

import support

from autobot.research_evidence import (
    SOURCE_EVIDENCE_KINDS,
    RESEARCH_VERIFICATION_RULES,
    build_source_record,
    build_claim_association,
    verify_citation_coverage,
    verify_source_capture,
    build_research_receipt,
)


class SourceEvidenceKindsTests(unittest.TestCase):

    def test_four_kinds(self):
        self.assertEqual(SOURCE_EVIDENCE_KINDS,
                         frozenset({"SOURCE_CAPTURE", "SOURCE_METADATA",
                                    "CLAIM_ASSOCIATION", "RESEARCH_ARTIFACT"}))


class VerificationRulesTests(unittest.TestCase):

    def test_required_rules(self):
        required = [name for name, rule in RESEARCH_VERIFICATION_RULES.items()
                    if rule["required"]]
        self.assertIn("source_identity", required)
        self.assertIn("source_capture", required)
        self.assertIn("citation_coverage", required)
        self.assertIn("claim_to_source_linkage", required)
        self.assertIn("artifact_completeness", required)
        self.assertIn("independent_review", required)

    def test_optional_rules(self):
        optional = [name for name, rule in RESEARCH_VERIFICATION_RULES.items()
                    if not rule["required"]]
        self.assertIn("freshness", optional)
        self.assertIn("source_diversity", optional)


class SourceRecordTests(unittest.TestCase):

    def test_minimal_record(self):
        r = build_source_record("docs.example.com/api",
                                "sha256:abc123",
                                "2026-11-20T10:00:00Z")
        self.assertEqual(r["source_identity"], "docs.example.com/api")
        self.assertEqual(r["content_address"], "sha256:abc123")
        self.assertNotIn("provider_item_id", r)

    def test_record_with_provider_item(self):
        r = build_source_record("src", "sha256:def",
                                "2026-11-20T10:00:00Z",
                                provider_item_id="mcp-web-001")
        self.assertEqual(r["provider_item_id"], "mcp-web-001")

    def test_record_with_metadata(self):
        r = build_source_record("src", "sha256:ghi",
                                "2026-11-20T10:00:00Z",
                                metadata={"content_type": "text/html"})
        self.assertEqual(r["metadata"]["content_type"], "text/html")


class ClaimAssociationTests(unittest.TestCase):

    def test_single_source(self):
        a = build_claim_association("claim-1", ["source-a"])
        self.assertEqual(a["claim_id"], "claim-1")
        self.assertEqual(a["source_identities"], ["source-a"])

    def test_multiple_sources_sorted(self):
        a = build_claim_association("claim-2", ["z-src", "a-src"])
        self.assertEqual(a["source_identities"], ["a-src", "z-src"])


class CitationCoverageTests(unittest.TestCase):

    def test_complete_coverage(self):
        claims = ["c1", "c2"]
        assocs = [
            build_claim_association("c1", ["s1"]),
            build_claim_association("c2", ["s2"]),
        ]
        result = verify_citation_coverage(claims, assocs)
        self.assertTrue(result["complete"])
        self.assertEqual(result["missing_claims"], [])

    def test_missing_claim(self):
        claims = ["c1", "c2", "c3"]
        assocs = [build_claim_association("c1", ["s1"])]
        result = verify_citation_coverage(claims, assocs)
        self.assertFalse(result["complete"])
        self.assertIn("c2", result["missing_claims"])
        self.assertIn("c3", result["missing_claims"])

    def test_empty(self):
        result = verify_citation_coverage([], [])
        self.assertTrue(result["complete"])


class SourceCaptureTests(unittest.TestCase):

    def test_all_captured(self):
        assocs = [build_claim_association("c1", ["s1", "s2"])]
        sources = [
            build_source_record("s1", "sha256:a", "2026-11-20T10:00:00Z"),
            build_source_record("s2", "sha256:b", "2026-11-20T10:00:00Z"),
        ]
        result = verify_source_capture(assocs, sources)
        self.assertTrue(result["complete"])
        self.assertEqual(result["uncaptured_sources"], [])

    def test_uncaptured_source(self):
        assocs = [build_claim_association("c1", ["s1", "s2"])]
        sources = [
            build_source_record("s1", "sha256:a", "2026-11-20T10:00:00Z"),
        ]
        result = verify_source_capture(assocs, sources)
        self.assertFalse(result["complete"])
        self.assertEqual(result["uncaptured_sources"], ["s2"])


class ResearchReceiptTests(unittest.TestCase):

    def test_receipt_structure(self):
        sources = [
            build_source_record("s1", "sha256:a", "2026-11-20T10:00:00Z"),
        ]
        assocs = [build_claim_association("c1", ["s1"])]
        verification = {
            "citation_coverage": {"complete": True},
            "source_capture": {"complete": True},
        }
        receipt = build_research_receipt(
            "What are the options?", "sha256:artifact",
            sources, assocs, verification)
        self.assertEqual(receipt["kind"], "ResearchEvidenceReceipt")
        self.assertEqual(receipt["research_question"], "What are the options?")
        self.assertEqual(receipt["source_count"], 1)
        self.assertEqual(receipt["claim_count"], 1)
        self.assertTrue(receipt["citation_coverage"]["complete"])


class ResearchReachSemantics(unittest.TestCase):
    """Research reach: same observation, different profile = different judgment."""

    def test_connector_authorized_under_research(self):
        from autobot.execution_reach import authorized_reach
        reach = authorized_reach("luna-research/v1")
        self.assertIn("PROVIDER_CONNECTOR", reach)

    def test_connector_violation_under_local(self):
        from autobot.execution_reach import authorized_reach
        reach = authorized_reach("luna-local/v1")
        self.assertNotIn("PROVIDER_CONNECTOR", reach)

    def test_same_observation_different_judgment(self):
        from autobot.execution_reach import authorized_reach
        local = authorized_reach("luna-local/v1")
        research = authorized_reach("luna-research/v1")
        self.assertIn("PROVIDER_CONNECTOR", research - local)


if __name__ == "__main__":
    unittest.main()
