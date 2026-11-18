"""Typed Engineer proposals (cap-13).

The Engineer's output is never free-form orchestration: it is a list of
typed proposals, each binding the objective it serves, the exact
ProjectState snapshot it reasoned over, a rationale, and provenance to the
findings and events that motivated it. Proposals PROPOSE; the Registry and
Sequencer establish authority (cap-08, cap-51), the Taskforce owns its own
membership — nothing here transitions anything.

The Engineer speaks in two roles, and the classes keep them apart
(``ACTING_AS``):

AS THE ENGINEER — technical work.

- ``CREATE_TASK`` / ``INVESTIGATE`` / ``REQUEST_REVIEW`` state a work
  requirement and compile to one canonical TaskContractRecord each
  (``compiler.compile_proposal``). INVESTIGATE and REQUEST_REVIEW are
  READ_ONLY by construction; REQUEST_REVIEW additionally binds a RECORD
  edge on the reviewed record.
- ``DECOMPOSE_TASK`` carries ordered parts; the service expands it into
  per-part contract proposals whose cross-part edges the compiler resolves
  by computing each part's frozen tiers first. A part may be an
  independent review of an earlier part (``review_of_part``), so a whole
  work graph — the work and its review — can be laid out at once.
- ``REPAIR_TASK`` authorizes another attempt of an EXISTING sequence (a
  repair after t-s11 returned it to READY). It never mints authority: the
  Sequencer's budgets (retry_policy, repair_policy) still decide whether
  another attempt may exist.
- ``WAIT_FOR_AUTHORITY`` records that the registered authority path is
  yours (a t-s13 budget grant, a cap-36 permit, a QUEUED_FOR_YOU
  admission, cap-10 supersession): the Engineer stops, visibly.

AS THE PROJECT MANAGER — the temporary team and the persistent
organization.

- ``OPEN_PROJECT`` opens the project that realizes the Engineer's
  solution to one objective. Work the objective needs becomes that
  project's workstreams.
- ``ASSIGN_SPECIALIST`` is the PROJECT ASSIGNMENT decision for one
  workstream's next attempt: request the capability from the Taskforce
  (it resolves a retained member), or explicitly select an outside
  specialist for the project.
- ``RETAIN_SPECIALIST`` / ``RELEASE_SPECIALIST`` are WORKFORCE decisions:
  retain a specialist into Taskforce membership and a retained seat on the
  strength of named capability demonstrations, or release one. They change
  Taskforce state only — future staffing eligibility — and grant no
  authority-plane capability of any kind.
- ``PROJECT_COMPLETE`` closes the project and its objective once the
  ledger shows the work COMPLETED: the temporary team is dissolved;
  retained membership persists.

What may name whom.

    work requirement / TaskContractRecord:
        provider forbidden, model forbidden, specialist identity forbidden
        (it may name a capability and a role)
    project assignment (``staffing``):
        specialist identity ALLOWED — staffing is not contract identity
        provider and model stay execution-routing details, resolved
        downstream of the staffing decision

Identity: a contract-class proposal's identity IS its candidate's
Chapter 1 contract_digest, so the same engineering intent re-derived
after a restart converges on the same proposal row, and resubmission
converges at the Registry as a designed DUPLICATE (obj-32). Every other
class hashes its class core plus the events that motivated it: the same
durable change never yields two.
"""

import re

from .capabilities import CAPABILITIES
from .proposals_util import canonical_json_digest
from .routing import ESCALATION_CLASSES
from .workforce import RELEASE_REASONS, STAFFING_MODES

PROPOSAL_CLASSES = (
    "CREATE_TASK",
    "DECOMPOSE_TASK",
    "REPAIR_TASK",
    "INVESTIGATE",
    "OPEN_PROJECT",
    "ASSIGN_SPECIALIST",
    "REQUEST_REVIEW",
    "RETAIN_SPECIALIST",
    "RELEASE_SPECIALIST",
    "WAIT_FOR_AUTHORITY",
    "PROJECT_COMPLETE",
)

# Technical work creation: each compiles to one TaskContractRecord.
CONTRACT_CLASSES = ("CREATE_TASK", "INVESTIGATE", "REQUEST_REVIEW")
# Classes that address the NEXT ATTEMPT of an existing sequence.
SEAT_CLASSES = ("REPAIR_TASK", "ASSIGN_SPECIALIST")
WORKFORCE_CLASSES = ("RETAIN_SPECIALIST", "RELEASE_SPECIALIST")

# The role the Engineer speaks in for each class.
ENGINEER, PROJECT_MANAGER = "ENGINEER", "PROJECT_MANAGER"
ACTING_AS = {
    "CREATE_TASK": ENGINEER,
    "DECOMPOSE_TASK": ENGINEER,
    "INVESTIGATE": ENGINEER,
    "REQUEST_REVIEW": ENGINEER,
    "REPAIR_TASK": ENGINEER,
    "WAIT_FOR_AUTHORITY": ENGINEER,
    "OPEN_PROJECT": PROJECT_MANAGER,
    "ASSIGN_SPECIALIST": PROJECT_MANAGER,
    "RETAIN_SPECIALIST": PROJECT_MANAGER,
    "RELEASE_SPECIALIST": PROJECT_MANAGER,
    "PROJECT_COMPLETE": PROJECT_MANAGER,
}

# What a WORK REQUIREMENT may never carry — and so what a
# TaskContractRecord can never contain: how the work is routed, and who
# does it.
ROUTING_FIELDS = ("provider", "model")
SPECIALIST_FIELDS = ("specialist_id", "specialist", "seat_id", "handle")

_EDGE_FIELDS = ("edge_id", "edge_kind", "target", "observation_requirement")
_EDGE_KINDS = ("RECORD", "CONTRACT", "REQUIREMENT")
_OBS = ("ACCEPTANCE", "ACCEPTANCE_FRESH")
_FIN_CLASSES = ("READ_ONLY", "SOURCE_MODIFYING")
WORKSTREAM_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ProposalError(Exception):
    pass


def _require(condition, message):
    if not condition:
        raise ProposalError(message)


def _refuse_execution_identity(mapping, what):
    """A work requirement (and the envelope that carries it) names no
    provider, no model and no specialist."""
    named = [key for key in ROUTING_FIELDS + SPECIALIST_FIELDS
             if key in mapping]
    _require(not named,
             "%s names %s: a work requirement states a capability and a"
             " role and never who executes or on what — it is frozen into"
             " the TaskContractRecord, which is independent of provider,"
             " model and specialist identity. Who does the work is a"
             " project assignment (ASSIGN_SPECIALIST)"
             % (what, ", ".join(named)))
    _require("staffing" not in mapping,
             "%s carries staffing: staffing is a project assignment"
             " (ASSIGN_SPECIALIST), never part of a work requirement"
             % what)


def _refuse_routing(mapping, what):
    """A project assignment or workforce decision may name a specialist;
    it still never names a provider or a model."""
    named = [key for key in ROUTING_FIELDS if key in mapping]
    _require(not named,
             "%s names %s: provider and model are execution-routing"
             " details resolved downstream of staffing — a decision names"
             " a specialist, never how it is routed"
             % (what, ", ".join(named)))


def _validate_requirement(req, forced_read_only):
    _require(isinstance(req, dict), "requirement must be a mapping")
    _refuse_execution_identity(req, "requirement")
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
        _refuse_execution_identity(policy, "roles[%r]" % (role,))
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


def _validate_workstream_key(mapping, what):
    key = mapping.get("workstream")
    if key is not None:
        _require(isinstance(key, str) and WORKSTREAM_KEY_RE.match(key),
                 "%s workstream must be a short lowercase key" % what)


def validate_staffing(staffing, what):
    """One staffing word: how a workstream's next attempt is staffed.
    It may name a specialist (``outside_specialist``); it never names a
    provider or a model."""
    _require(isinstance(staffing, dict)
             and staffing.get("mode") in STAFFING_MODES,
             "%s staffing.mode must be one of %r" % (what, STAFFING_MODES))
    _refuse_routing(staffing, "%s staffing" % what)
    _require(set(staffing) <= {"mode", "specialist_id", "escalation"},
             "%s staffing carries a mode, for an outside specialist the"
             " specialist_id, and at most an escalation — nothing else"
             % what)
    if "escalation" in staffing:
        # The Project Manager's ask for more than standard execution
        # depth on THIS assignment. A word about the engineering need;
        # how it is routed is resolved downstream.
        _require(staffing["escalation"] in ESCALATION_CLASSES,
                 "%s staffing.escalation must be one of %r: it states"
                 " what the problem needs and never names a provider or"
                 " a model" % (what, ESCALATION_CLASSES))
    if staffing["mode"] == "outside_specialist":
        _require(isinstance(staffing.get("specialist_id"), str)
                 and staffing["specialist_id"],
                 "%s staffing mode outside_specialist names the specialist"
                 " the Project Manager selects (specialist_id)" % what)
    else:
        _require("specialist_id" not in staffing,
                 "%s staffing mode %r does not name a specialist: only"
                 " outside_specialist does (the Taskforce resolves"
                 " retained members)" % (what, staffing["mode"]))
    return staffing


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
             "every proposal binds the objective it serves")
    snapshot = proposal.get("snapshot")
    _require(isinstance(snapshot, dict)
             and isinstance(snapshot.get("entries"), int),
             "snapshot must carry the journal entry count it reasoned over")
    provenance = proposal.get("provenance", {})
    _require(isinstance(provenance, dict),
             "provenance must be a mapping of findings/events lists")

    if cls in CONTRACT_CLASSES:
        _refuse_execution_identity(proposal, cls)
        _validate_workstream_key(proposal, cls)
        _validate_requirement(
            proposal.get("requirement"),
            forced_read_only=(cls in ("INVESTIGATE", "REQUEST_REVIEW")))
        if cls == "REQUEST_REVIEW":
            review_of = proposal.get("review_of")
            _require(isinstance(review_of, dict)
                     and isinstance(review_of.get("record_hash"), str),
                     "REQUEST_REVIEW binds review_of.record_hash")
    elif cls == "DECOMPOSE_TASK":
        _refuse_execution_identity(proposal, cls)
        parts = proposal.get("parts")
        _require(isinstance(parts, list) and parts,
                 "DECOMPOSE_TASK carries ordered parts")
        seen = set()
        for part in parts:
            _require(isinstance(part, dict)
                     and isinstance(part.get("part_id"), str)
                     and part["part_id"], "each part carries a part_id")
            _require(part["part_id"] not in seen, "part_id values are unique")
            # A part may be an independent review of an EARLIER part: it
            # waits for that part's acceptance and is staffed
            # independently of whoever did it.
            reviewed = part.get("review_of_part")
            _require(reviewed is None or reviewed in seen,
                     "part %r reviews %r, which must come earlier in the"
                     " list" % (part["part_id"], reviewed))
            seen.add(part["part_id"])
            _refuse_execution_identity(part, "part %r" % (part["part_id"],))
            _validate_requirement(part.get("requirement"),
                                  forced_read_only=reviewed is not None)
            for dep in part.get("depends_on_parts", []):
                _require(dep in seen,
                         "part %r depends on %r, which must come earlier"
                         " in the list (cycles are unrepresentable)"
                         % (part["part_id"], dep))
    elif cls in SEAT_CLASSES:
        _refuse_routing(proposal, cls)
        named = [key for key in SPECIALIST_FIELDS if key in proposal]
        _require(not named,
                 "%s names %s at the top level: a specialist is named in"
                 " staffing.specialist_id" % (cls, ", ".join(named)))
        _validate_workstream_key(proposal, cls)
        sequence_id = proposal.get("sequence_id")
        _require((isinstance(sequence_id, str) and sequence_id)
                 or proposal.get("workstream"),
                 "%s binds the workstream it staffs (sequence_id, or the"
                 " workstream key)" % cls)
        capability = proposal.get("capability")
        guidance = proposal.get("guidance")
        if cls == "REPAIR_TASK":
            _require(capability in CAPABILITIES,
                     "REPAIR_TASK needs a registered capability")
            _require(isinstance(guidance, str) and guidance,
                     "REPAIR_TASK needs non-empty guidance")
        else:
            _require(capability is None or capability in CAPABILITIES,
                     "ASSIGN_SPECIALIST capability must be a registered"
                     " capability")
            _require(guidance is None or isinstance(guidance, str),
                     "ASSIGN_SPECIALIST guidance must be text")
            _require(isinstance(proposal.get("staffing"), dict),
                     "ASSIGN_SPECIALIST carries the staffing decision")
        if proposal.get("staffing") is not None:
            validate_staffing(proposal["staffing"], cls)
        ordinal = proposal.get("attempt_ordinal")
        _require(ordinal is None or (isinstance(ordinal, int)
                                     and ordinal >= 1),
                 "%s attempt_ordinal must be a positive integer" % cls)
    elif cls in WORKFORCE_CLASSES:
        _refuse_routing(proposal, cls)
        _require(isinstance(proposal.get("specialist_id"), str)
                 and proposal["specialist_id"],
                 "%s binds the specialist it decides about" % cls)
        _require(proposal.get("capability") in CAPABILITIES,
                 "%s names the registered capability it decides about"
                 % cls)
        _require(isinstance(proposal.get("role"), str)
                 and proposal["role"],
                 "%s names the role it decides about" % cls)
        if cls == "RETAIN_SPECIALIST":
            demos = proposal.get("demonstrations")
            _require(isinstance(demos, list) and demos and all(
                isinstance(d, str) and d for d in demos),
                "RETAIN_SPECIALIST binds the capability demonstrations"
                " that support it (retention rests on verified evidence)")
        else:
            _require(proposal.get("reason") in RELEASE_REASONS,
                     "RELEASE_SPECIALIST reason must be one of %r"
                     % (RELEASE_REASONS,))
            _require(proposal.get("disposition", "RELEASE") in (
                "RELEASE", "DEACTIVATE"),
                "RELEASE_SPECIALIST disposition is RELEASE or DEACTIVATE")
            demos = proposal.get("demonstrations")
            _require(demos is None or (isinstance(demos, list) and all(
                isinstance(d, str) and d for d in demos)),
                "RELEASE_SPECIALIST demonstrations, when given, name the"
                " work the decision considered")
    elif cls == "OPEN_PROJECT":
        _refuse_execution_identity(proposal, cls)
        project = proposal.get("project")
        _require(isinstance(project, dict), "OPEN_PROJECT carries a project")
        _refuse_execution_identity(project, "project")
        for field in ("title", "solution"):
            _require(isinstance(project.get(field), str) and project[field],
                     "OPEN_PROJECT project.%s must be non-empty text (the"
                     " project realizes a stated solution)" % field)
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
        core["capability"] = proposal.get("capability")
        staffing = proposal.get("staffing")
        if staffing:
            # The staffing decision is part of what was decided: a second
            # decision for the same workstream is a second proposal.
            core["staffing"] = {"mode": staffing["mode"],
                                "specialist_id":
                                    staffing.get("specialist_id")}
            if staffing.get("escalation"):
                # Asking for escalated execution is part of the decision
                # too (absent, the identity is what it always was).
                core["staffing"]["escalation"] = staffing["escalation"]
        if proposal.get("attempt_ordinal") is not None:
            core["attempt_ordinal"] = proposal["attempt_ordinal"]
        workforce = provenance.get("workforce") or {}
        if workforce.get("events"):
            core["workforce_events"] = sorted(workforce["events"])
    elif cls in WORKFORCE_CLASSES:
        # The decision IS its identity: the same retention or release
        # re-derived after a restart converges on one row.
        core["specialist_id"] = proposal["specialist_id"]
        core["capability"] = proposal["capability"]
        core["role"] = proposal["role"]
        if cls == "RETAIN_SPECIALIST":
            core["demonstrations"] = sorted(proposal["demonstrations"])
        else:
            core["reason"] = proposal["reason"]
            core["disposition"] = proposal.get("disposition", "RELEASE")
            if proposal.get("demonstrations"):
                # Letting a specialist go after one piece of work and
                # again after another are two decisions.
                core["demonstrations"] = sorted(proposal["demonstrations"])
    elif cls == "OPEN_PROJECT":
        core["title"] = proposal["project"]["title"]
        core["solution"] = proposal["project"]["solution"]
    elif cls == "WAIT_FOR_AUTHORITY":
        core["reason"] = proposal["reason"]
        core["subject_id"] = proposal["subject_id"]
    return "prop:" + canonical_json_digest(core)
