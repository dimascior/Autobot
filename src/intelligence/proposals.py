"""Typed Chief Engineer proposals (cap-13).

The Chief Engineer's output is never free-form orchestration: it is a
list of typed proposals, each binding the project objective, the exact
ProjectState snapshot it reasoned over, a rationale, and provenance to
the findings and planning events that motivated it. Proposals PROPOSE
engineering action; the Registry and Sequencer establish authority
(cap-08, cap-51) — nothing here transitions anything.

Classes:

- ``CREATE_TASK`` / ``INVESTIGATE`` / ``REQUEST_REVIEW`` compile to one
  canonical TaskContractRecord each (``compiler.compile_proposal``).
  INVESTIGATE and REQUEST_REVIEW are READ_ONLY by construction;
  REQUEST_REVIEW additionally binds a RECORD edge on the reviewed
  record.
- ``DECOMPOSE_TASK`` carries ordered parts; the Chief Engineer service
  expands it into per-part contract proposals whose cross-part edges the
  compiler resolves by computing each part's frozen tiers first.
- ``REPAIR_TASK`` / ``ASSIGN_SPECIALIST`` authorize a Taskforce seat on
  an EXISTING sequence (a repair attempt after t-s11 returned it to
  READY, or a chosen specialist for work the Chief Engineer gates).
  They never mint authority: the Sequencer's budgets (retry_policy,
  repair_policy) still decide whether another attempt may exist.
- ``WAIT_FOR_AUTHORITY`` records that the registered authority path is
  yours (a t-s13 budget grant, a cap-36 permit, a QUEUED_FOR_YOU
  admission, cap-10 supersession): the planner stops, visibly.
- ``PROJECT_COMPLETE`` closes an objective once the ledger shows its
  work COMPLETED and nothing actionable remains.

Identity: a contract-class proposal's identity IS its candidate's
Chapter 1 contract_digest, so the same engineering intent re-derived
after a restart converges on the same proposal row, and resubmission
converges at the Registry as a designed DUPLICATE (obj-32). Seat and
authority-wait proposals hash their class core plus the planning events
that motivated them: the same durable change never yields two.
"""

from .proposals_util import canonical_json_digest
from .roles import CAPABILITIES

PROPOSAL_CLASSES = (
    "CREATE_TASK",
    "DECOMPOSE_TASK",
    "REPAIR_TASK",
    "INVESTIGATE",
    "ASSIGN_SPECIALIST",
    "REQUEST_REVIEW",
    "WAIT_FOR_AUTHORITY",
    "PROJECT_COMPLETE",
)

CONTRACT_CLASSES = ("CREATE_TASK", "INVESTIGATE", "REQUEST_REVIEW")
SEAT_CLASSES = ("REPAIR_TASK", "ASSIGN_SPECIALIST")

_EDGE_FIELDS = ("edge_id", "edge_kind", "target", "observation_requirement")
_EDGE_KINDS = ("RECORD", "CONTRACT", "REQUIREMENT")
_OBS = ("ACCEPTANCE", "ACCEPTANCE_FRESH")
_FIN_CLASSES = ("READ_ONLY", "SOURCE_MODIFYING")


class ProposalError(Exception):
    pass


def _require(condition, message):
    if not condition:
        raise ProposalError(message)


def _validate_requirement(req, forced_read_only):
    _require(isinstance(req, dict), "requirement must be a mapping")
    _require(isinstance(req.get("objective"), str) and req["objective"],
             "requirement.objective must be non-empty text")
    fin = req.get("finalization_class")
    if forced_read_only:
        _require(fin in (None, "READ_ONLY"),
                 "this class is READ_ONLY by construction")
    else:
        _require(fin in _FIN_CLASSES,
                 "requirement.finalization_class must be one of %r"
                 % (_FIN_CLASSES,))
    roles = req.get("roles")
    _require(isinstance(roles, dict) and roles,
             "requirement.roles must name at least one role")
    for role, policy in roles.items():
        _require(isinstance(role, str) and role, "role names are strings")
        _require(isinstance(policy, dict)
                 and isinstance(policy.get("required"), bool)
                 and isinstance(policy.get("success_outcomes"), list)
                 and policy["success_outcomes"],
                 "roles[%r] needs required: bool and non-empty"
                 " success_outcomes" % (role,))
    _require(req.get("capability") in CAPABILITIES,
             "requirement.capability must be a registered capability")
    _require(isinstance(req.get("guidance"), str) and req["guidance"],
             "requirement.guidance must be non-empty (the projection's"
             " engineering context requires it)")
    for edge in req.get("predecessors", []):
        _require(isinstance(edge, dict)
                 and sorted(edge.keys()) == sorted(_EDGE_FIELDS),
                 "each predecessor edge carries exactly %r" % (_EDGE_FIELDS,))
        _require(edge["edge_kind"] in _EDGE_KINDS,
                 "edge_kind must be a frozen obj-07 tier")
        _require(edge["observation_requirement"] in _OBS,
                 "observation_requirement must be a frozen value")


def validate_proposal(proposal):
    """Structural validation of one typed proposal. Raises ProposalError;
    returns the proposal unchanged when well-formed."""
    _require(isinstance(proposal, dict), "a proposal is a mapping")
    cls = proposal.get("class")
    _require(cls in PROPOSAL_CLASSES,
             "class %r is not a registered proposal class" % (cls,))
    _require(isinstance(proposal.get("rationale"), str)
             and proposal["rationale"], "rationale must be non-empty")
    _require(isinstance(proposal.get("objective_id"), str)
             and proposal["objective_id"],
             "every proposal binds the project objective it serves")
    snapshot = proposal.get("snapshot")
    _require(isinstance(snapshot, dict)
             and isinstance(snapshot.get("entries"), int),
             "snapshot must carry the journal entry count it reasoned over")
    provenance = proposal.get("provenance", {})
    _require(isinstance(provenance, dict),
             "provenance must be a mapping of findings/events lists")

    if cls in ("CREATE_TASK", "INVESTIGATE"):
        _validate_requirement(proposal.get("requirement"),
                              forced_read_only=(cls == "INVESTIGATE"))
    elif cls == "REQUEST_REVIEW":
        _validate_requirement(proposal.get("requirement"),
                              forced_read_only=True)
        review_of = proposal.get("review_of")
        _require(isinstance(review_of, dict)
                 and isinstance(review_of.get("record_hash"), str),
                 "REQUEST_REVIEW binds review_of.record_hash")
    elif cls == "DECOMPOSE_TASK":
        parts = proposal.get("parts")
        _require(isinstance(parts, list) and parts,
                 "DECOMPOSE_TASK carries ordered parts")
        seen = set()
        for part in parts:
            _require(isinstance(part, dict)
                     and isinstance(part.get("part_id"), str)
                     and part["part_id"], "each part carries a part_id")
            _require(part["part_id"] not in seen, "part_id values are unique")
            seen.add(part["part_id"])
            _validate_requirement(part.get("requirement"),
                                  forced_read_only=False)
            for dep in part.get("depends_on_parts", []):
                _require(dep in seen,
                         "part %r depends on %r, which must come earlier"
                         " in the list (cycles are unrepresentable)"
                         % (part["part_id"], dep))
    elif cls in SEAT_CLASSES:
        _require(isinstance(proposal.get("sequence_id"), str)
                 and proposal["sequence_id"],
                 "%s binds the existing sequence it allocates" % cls)
        _require(proposal.get("capability") in CAPABILITIES,
                 "%s needs a registered capability" % cls)
        _require(isinstance(proposal.get("guidance"), str)
                 and proposal["guidance"],
                 "%s needs non-empty guidance" % cls)
    elif cls == "WAIT_FOR_AUTHORITY":
        _require(isinstance(proposal.get("reason"), str)
                 and proposal["reason"], "WAIT_FOR_AUTHORITY needs a reason")
        _require(isinstance(proposal.get("subject_id"), str)
                 and proposal["subject_id"],
                 "WAIT_FOR_AUTHORITY names its subject")
    return proposal


def proposal_identity(proposal, contract_digest=None):
    """The deterministic proposal id (see the module docstring)."""
    cls = proposal["class"]
    if cls in CONTRACT_CLASSES:
        if contract_digest is None:
            raise ProposalError(
                "contract-class identity requires the candidate's"
                " contract_digest")
        return "prop:" + contract_digest
    provenance = proposal.get("provenance", {})
    core = {"class": cls, "objective_id": proposal.get("objective_id"),
            "events": sorted(provenance.get("events", []))}
    if cls in SEAT_CLASSES:
        core["sequence_id"] = proposal["sequence_id"]
        core["capability"] = proposal["capability"]
    elif cls == "WAIT_FOR_AUTHORITY":
        core["reason"] = proposal["reason"]
        core["subject_id"] = proposal["subject_id"]
    return "prop:" + canonical_json_digest(core)
