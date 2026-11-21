"""Bridge from organizational workstreams to executable authority proposals.

An Orchestrator-created workstream carries enough information to produce
a typed proposal that the existing compiler and Registry admission path
can process. This module converts a durable workstream definition into
a proposal dict compatible with the Engineer's disposal path.

The chain:
    Orchestrator CREATE_WORKSTREAM
        → durable project_workstreams row
        → workstream_to_proposal()
        → typed proposal
        → compile_proposal()
        → Registry.admit_payload()
        → sequence
        → workstream ↔ sequence binding

For ENGINEERING workstreams the Orchestrator can either generate the
proposal directly or route through REQUEST_ENGINEERING_DECISION to let
the Engineer refine the technical approach. For RESEARCH and
GENERAL_OPERATION workstreams the bridge produces the proposal without
requiring Engineer participation.
"""

from autobot.work_profile import default_execution_profile, default_finalization_class


def workstream_to_proposal(workstream, objective_text, project_id,
                           predecessors=None):
    """Convert a durable workstream into a typed proposal requirement.

    The workstream carries its generalized dimensions from the
    Orchestrator's CREATE_WORKSTREAM decision. The proposal is a dict
    compatible with the Engineer's disposal path.

    Returns a dict with 'class' and 'requirement' ready for
    compile_proposal."""
    wp = workstream.get("work_profile")
    ep = workstream.get("execution_profile")
    rs = workstream.get("resource_scope")
    role = workstream.get("organizational_role", "SPECIALIST")
    key = workstream.get("workstream_key", "work")
    cp = workstream.get("completion_policy")

    fin_class = default_finalization_class(wp) if wp else "READ_ONLY"
    if ep is None and wp:
        ep = default_execution_profile(wp)

    capability = _infer_capability(wp, role)
    roles = _build_roles(role, capability, cp)

    requirement = {
        "objective": objective_text,
        "capability": capability,
        "roles": roles,
        "subsystem": key,
    }

    if wp is not None:
        requirement["work_profile"] = wp
    if ep is not None:
        requirement["execution_profile"] = ep
    if rs is not None:
        requirement["resource_scope"] = rs
    if fin_class and fin_class != "READ_ONLY":
        requirement["finalization_class"] = fin_class
    if predecessors:
        requirement["predecessors"] = list(predecessors)

    is_review = role.lower() == "reviewer" and predecessors
    proposal_class = "REQUEST_REVIEW" if is_review else \
        ("INVESTIGATE" if fin_class == "READ_ONLY" else "CREATE_TASK")

    return {
        "class": proposal_class,
        "objective_id": workstream.get("objective_id"),
        "rationale": "Orchestrator workstream %s" % key,
        "requirement": requirement,
        "workstream": key,
        "snapshot": {"entries": 0, "head_hash": "sha256:" + "0" * 64},
    }


def _infer_capability(work_profile, role):
    """Infer the capability from work profile and role."""
    role_lower = (role or "specialist").lower()
    if role_lower == "reviewer":
        return "verification"
    if work_profile == "RESEARCH":
        return "research"
    if work_profile == "GENERAL_OPERATION":
        return "operations"
    return "documentation" if role_lower == "builder" else "research"


def _build_roles(role, capability, completion_policy):
    """Build the roles dict for the proposal requirement."""
    role_key = (role or "specialist").lower()
    if role_key == "specialist":
        if capability == "research":
            role_key = "investigator"
        elif capability == "operations":
            role_key = "builder"
        else:
            role_key = "builder"

    success_outcomes = ["success"]
    if completion_policy and isinstance(completion_policy, dict):
        so = completion_policy.get("success_outcomes")
        if so:
            success_outcomes = list(so)

    return {role_key: {"required": True, "success_outcomes": success_outcomes}}


def ready_workstreams_needing_proposals(plan, project_id):
    """Find workstreams that have no executable proposal yet.

    Returns workstreams that:
    1. Belong to the project
    2. Have status ACTIVE or None
    3. Have no proposal_id that maps to an admitted contract
    """
    workstreams = plan.workstreams(project_id)
    needing = []
    for ws in workstreams:
        proposal_id = ws.get("proposal_id")
        if proposal_id:
            existing = plan.proposal(proposal_id)
            if existing and existing.get("status") in (
                    "ADMITTED", "DUPLICATE", "WAITING", "QUEUED_FOR_YOU"):
                continue
        needing.append(ws)
    return needing
