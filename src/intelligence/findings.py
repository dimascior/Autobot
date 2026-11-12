"""Structured specialist findings: engineering information, never work.

A finding is what a specialist learned, bound to the attempt that
produced it and to the evidence addresses that back it. It creates NO
authority and NO work: only a Chief Engineer proposal (its own record,
with provenance back to the finding) turns engineering information into
a contract — the adversarial axis "findings automatically becoming
work" is excluded by construction, and a conformance test pins it.

``finding_class`` vocabulary (intelligence-plane, extendable by
registration here, not a Chapter 1 enum):
architectural_defect, implementation_defect, missing_requirement,
external_dependency, verification_gap, performance_issue,
security_issue, documentation_mismatch, newly_discovered_work.
"""

import json

from .proposals_util import canonical_json_digest

FINDING_CLASSES = (
    "architectural_defect",
    "implementation_defect",
    "missing_requirement",
    "external_dependency",
    "verification_gap",
    "performance_issue",
    "security_issue",
    "documentation_mismatch",
    "newly_discovered_work",
)

_REQUIRED = ("attempt_id", "sequence_id", "role", "finding_class",
             "affected_component", "significance", "confidence",
             "relation_to_requirement")


class FindingError(Exception):
    pass


def admit_finding(plan, finding, now):
    """Validate and persist one finding; idempotent on content identity.

    ``finding`` carries the _REQUIRED fields plus optional ``call_id``,
    ``evidence_refs`` (content addresses) and ``suggested_follow_up``.
    Returns the finding_id (deterministic over the content, so the same
    finding re-admitted after a restart converges on one row)."""
    if not isinstance(finding, dict):
        raise FindingError("a finding is a mapping")
    for field in _REQUIRED:
        value = finding.get(field)
        if not (isinstance(value, str) and value):
            raise FindingError("finding.%s must be non-empty text" % field)
    if finding["finding_class"] not in FINDING_CLASSES:
        raise FindingError(
            "finding_class %r is not registered" % (finding["finding_class"],))
    refs = finding.get("evidence_refs", [])
    if not isinstance(refs, list) or any(
            not (isinstance(r, str) and r) for r in refs):
        raise FindingError("evidence_refs must be a list of addresses")
    finding_id = "fnd:" + canonical_json_digest(
        {k: finding.get(k) for k in
         _REQUIRED + ("call_id", "evidence_refs", "suggested_follow_up")})
    with plan.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO findings (finding_id, attempt_id,"
            " sequence_id, call_id, role, finding_class,"
            " affected_component, evidence_refs, significance,"
            " suggested_follow_up, confidence, relation_to_requirement,"
            " admitted_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (finding_id, finding["attempt_id"], finding["sequence_id"],
             finding.get("call_id"), finding["role"],
             finding["finding_class"], finding["affected_component"],
             json.dumps(sorted(refs)), finding["significance"],
             finding.get("suggested_follow_up"), finding["confidence"],
             finding["relation_to_requirement"], now))
    return finding_id
