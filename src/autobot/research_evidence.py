"""Research evidence model for RESEARCH WorkProfile assignments.

Research work produces artifacts bound to source evidence. The evidence
chain tracks what was observed, where, when, and how the claims in the
research artifact connect to their sources.

A research assignment under luna-research/v1 may make provider-connector
observations that are authorized by its ExecutionProfile. The same
observation under luna-local/v1 would be a reach violation. The
distinction is entirely in the registered ExecutionProfile — the
observation itself is the same provider-native fact.

Evidence families:
    SOURCE_CAPTURE     — content-addressed bytes of an observed source
    SOURCE_METADATA    — retrieval time, identity, freshness, provider item
    CLAIM_ASSOCIATION  — which research claim cites which captured source
    RESEARCH_ARTIFACT  — the produced research output (report, analysis)
"""

SOURCE_EVIDENCE_KINDS = frozenset({
    "SOURCE_CAPTURE",
    "SOURCE_METADATA",
    "CLAIM_ASSOCIATION",
    "RESEARCH_ARTIFACT",
})

RESEARCH_VERIFICATION_RULES = {
    "source_identity": {
        "description": "Every cited source has a captured identity.",
        "required": True,
    },
    "source_capture": {
        "description": "Every cited source has content-addressed bytes.",
        "required": True,
    },
    "freshness": {
        "description": "Sources fall within the declared freshness window.",
        "required": False,
    },
    "source_diversity": {
        "description": "Claims supported by multiple independent sources.",
        "required": False,
    },
    "citation_coverage": {
        "description": "Every claim in the artifact cites at least one source.",
        "required": True,
    },
    "claim_to_source_linkage": {
        "description": "Every citation resolves to a captured source.",
        "required": True,
    },
    "artifact_completeness": {
        "description": "The research artifact addresses the research question.",
        "required": True,
    },
    "independent_review": {
        "description": "A separate reviewer evaluated the research.",
        "required": True,
    },
}


def build_source_record(source_identity, content_address, retrieval_utc,
                        provider_item_id=None, metadata=None):
    """Build a canonical source evidence record."""
    record = {
        "source_identity": str(source_identity),
        "content_address": str(content_address),
        "retrieval_utc": str(retrieval_utc),
    }
    if provider_item_id is not None:
        record["provider_item_id"] = str(provider_item_id)
    if metadata:
        record["metadata"] = dict(metadata)
    return record


def build_claim_association(claim_id, source_identities):
    """Build a claim-to-source association."""
    return {
        "claim_id": str(claim_id),
        "source_identities": sorted(str(s) for s in source_identities),
    }


def verify_citation_coverage(claims, associations):
    """Check that every claim has at least one associated source."""
    associated_claims = {a["claim_id"] for a in associations}
    missing = [c for c in claims if c not in associated_claims]
    return {
        "complete": len(missing) == 0,
        "missing_claims": missing,
        "total_claims": len(claims),
        "associated_claims": len(associated_claims),
    }


def verify_source_capture(associations, captured_sources):
    """Check that every cited source identity has captured bytes."""
    captured_ids = {s["source_identity"] for s in captured_sources}
    all_cited = set()
    for assoc in associations:
        all_cited.update(assoc["source_identities"])
    uncaptured = sorted(all_cited - captured_ids)
    return {
        "complete": len(uncaptured) == 0,
        "uncaptured_sources": uncaptured,
        "total_cited": len(all_cited),
        "total_captured": len(captured_ids),
    }


def build_research_receipt(research_question, artifact_address,
                           sources, associations, verification_results):
    """Build the research evidence receipt."""
    return {
        "kind": "ResearchEvidenceReceipt",
        "research_question": str(research_question),
        "artifact_address": str(artifact_address),
        "source_count": len(sources),
        "claim_count": sum(1 for _ in associations),
        "citation_coverage": verification_results.get(
            "citation_coverage", {}),
        "source_capture": verification_results.get(
            "source_capture", {}),
        "freshness": verification_results.get("freshness"),
    }
