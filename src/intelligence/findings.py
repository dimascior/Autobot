"""Structured specialist findings: engineering information, never work.

A finding is what a specialist learned, bound to the attempt that
produced it and to the evidence addresses that back it. It creates NO
authority and NO work: only a Engineer proposal (its own record,
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
    statement = finding.get("statement")
    if statement is not None and not (
            isinstance(statement, str) and statement):
        raise FindingError("finding.statement must be non-empty text")
    identity = {k: finding.get(k) for k in
                _REQUIRED + ("call_id", "evidence_refs",
                             "suggested_follow_up")}
    if statement is not None:
        # Part of identity only when present, so findings admitted before
        # statements existed keep the ids they already have.
        identity["statement"] = statement
    finding_id = "fnd:" + canonical_json_digest(identity)
    with plan.transaction() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO findings (finding_id, attempt_id,"
            " sequence_id, call_id, role, finding_class,"
            " affected_component, evidence_refs, significance,"
            " suggested_follow_up, confidence, relation_to_requirement,"
            " admitted_utc, statement) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?)",
            (finding_id, finding["attempt_id"], finding["sequence_id"],
             finding.get("call_id"), finding["role"],
             finding["finding_class"], finding["affected_component"],
             json.dumps(sorted(refs)), finding["significance"],
             finding.get("suggested_follow_up"), finding["confidence"],
             finding["relation_to_requirement"], now, statement))
    return finding_id


def extract_review_verdict(text):
    """What a reviewer said about its own review, from the line the
    review contract names (``prompts.REVIEW_CONTRACT``): ``"EXAMINED"``
    or ``"NOT_EXAMINED"``. None when the response carries no such line,
    a line with any other word, or lines that disagree — a review that
    says both has said neither. Deterministic, and never inferred from
    prose: a reviewer that does not say it examined the work is not
    recorded as having done so."""
    from .prompts import REVIEW_MARK, REVIEW_VERDICTS
    if not isinstance(text, str):
        return None
    stated = set()
    for line in text.splitlines():
        line = line.strip().strip("*`>").strip()
        if not line.startswith(REVIEW_MARK):
            continue
        rest = line[len(REVIEW_MARK):].lstrip()
        if not rest.startswith(":"):
            continue
        value = rest[1:].strip().strip("*`.").strip().upper()
        if value not in REVIEW_VERDICTS:
            return None
        stated.add(value)
    return stated.pop() if len(stated) == 1 else None


def extract_findings(text):
    """The structured findings a specialist reported in its final
    response, from the block the findings contract names
    (``prompts.FINDINGS_CONTRACT``). Deterministic and strict about
    shape, lenient about absence: no block, or a block that is not a JSON
    array of objects, yields nothing — a specialist's prose is never
    promoted into engineering information.

    Returns ``(findings, problem)``: the parsed finding mappings (not yet
    validated against the vocabulary — ``admit_finding`` does that) and a
    short description of why a present block could not be read, or
    None."""
    from .prompts import FINDINGS_BEGIN, FINDINGS_END
    if not isinstance(text, str):
        return [], None
    start = text.rfind(FINDINGS_BEGIN)
    if start < 0:
        return [], None
    end = text.find(FINDINGS_END, start)
    if end < 0:
        return [], "findings block has no end marker"
    body = text[start + len(FINDINGS_BEGIN):end].strip()
    if body.startswith("```"):
        # A fenced block inside the markers: drop the fence lines.
        lines = [line for line in body.splitlines()
                 if not line.strip().startswith("```")]
        body = "\n".join(lines).strip()
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        return [], "findings block is not JSON: %s" % exc
    if not isinstance(parsed, list):
        return [], "findings block is not a JSON array"
    findings = []
    for entry in parsed:
        if not isinstance(entry, dict):
            return [], "findings block holds a non-object entry"
        clean = {}
        for key, value in entry.items():
            if key == "suggested_follow_up" and value == "":
                continue  # "no follow-up" is the absence of one
            clean[key] = value
        findings.append(clean)
    return findings, None
