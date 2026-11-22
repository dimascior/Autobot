"""Organizational closure predicates.

Authority determines whether each result satisfied its frozen contract.
Organization determines whether the collection of accepted results is
sufficient to finish the objective. These are separate questions.

This module answers the organizational question at the mutation boundary.
Every writer that can mark a node COMPLETED, a run COMPLETED, or an
objective COMPLETE calls these predicates first. The authority plane
(Sequencer, Verifier, acceptance, t-s41, publication permits) never
calls them and is never changed by them.

A NOT_EXAMINED review is valid authority work (VERIFIED, ACCEPTED,
COMPLETED). It does not satisfy the organization's review coverage.
"""

from .query import (
    completion_obligations_from_work,
    tasks,
    unexamined_reviews,
)


def can_complete_node(plan, ro, node_id):
    """Whether a node may be marked COMPLETED.

    A TASK_CONTRACT node is complete when its authority sequence is
    COMPLETED. An ENGINEER_REASONING node is complete when its
    engineering decision is adopted. A node with no authority binding
    and no engineering binding is not complete.

    Returns (eligible, reason)."""
    node = plan.orchestration_node(node_id) if hasattr(
        plan, "orchestration_node") else None
    if node is None:
        return True, "no node record"

    if node["status"] == "COMPLETED":
        return True, "already completed"

    kind = node.get("realization_kind")
    if kind == "TASK_CONTRACT":
        binding = plan.authority_binding_for_node(node_id)
        if binding is None:
            return False, "no authority binding"
        seq_id = binding.get("sequence_id")
        if not seq_id:
            return False, "no sequence"
        seq = ro.execute(
            "SELECT state FROM sequences WHERE sequence_id = ?",
            (seq_id,)).fetchone()
        if seq is None:
            return False, "sequence not found"
        if seq["state"] != "COMPLETED":
            return False, "sequence %s is %s" % (seq_id, seq["state"])
        return True, "sequence COMPLETED"

    if kind == "ENGINEER_REASONING":
        binding = plan.engineering_binding_for_node(node_id)
        if binding is None:
            return False, "no engineering binding"
        if not binding.get("adopted_utc"):
            return False, "engineering decision not adopted"
        return True, "decision adopted"

    return True, "realization kind %s has no completion gate" % kind


def can_complete_run(plan, ro, orchestration_id):
    """Whether a run may be marked COMPLETED.

    Derives the objective_id from the durable run record (never trusts
    the decision payload). Checks that every node is COMPLETED and that
    organizational obligations are satisfied.

    Returns (eligible, reason, objective_id)."""
    run = plan.orchestration_run(orchestration_id) if hasattr(
        plan, "orchestration_run") else None
    if run is None:
        return False, "run not found", None

    objective_id = run.get("objective_id")

    nodes = plan.orchestration_nodes(orchestration_id)
    for node in nodes:
        if node["status"] not in ("COMPLETED",):
            return (False,
                    "node %s is %s" % (node["node_id"], node["status"]),
                    objective_id)

    if objective_id:
        obligations = _full_obligations(plan, ro, objective_id)
        if not obligations["eligible"]:
            reasons = _format_reasons(obligations)
            return False, reasons, objective_id

    return True, "all nodes COMPLETED, obligations met", objective_id


def can_complete_objective(plan, ro, objective_id):
    """Whether an objective may be marked COMPLETE.

    Checks all organizational obligations: unfinished work, unexamined
    reviews, pending nodes. Returns (eligible, reason)."""
    obligations = _full_obligations(plan, ro, objective_id)
    if not obligations["eligible"]:
        return False, _format_reasons(obligations)
    return True, "eligible"


def _full_obligations(plan, ro, objective_id):
    """The complete organizational obligations for an objective,
    including both authority-admitted work and orchestration nodes."""
    work = tasks(plan, ro, objective_id)
    result = completion_obligations_from_work(work)

    pending_nodes = []
    if hasattr(plan, "active_orchestration_runs"):
        for run in plan.active_orchestration_runs(objective_id):
            for node in plan.orchestration_nodes(
                    run["orchestration_id"]):
                if node["status"] not in ("COMPLETED",):
                    pending_nodes.append({
                        "node_id": node["node_id"],
                        "node_key": node.get("node_key"),
                        "status": node["status"]})
    if pending_nodes:
        result["pending_nodes"] = pending_nodes
        result["eligible"] = False
    return result


def _format_reasons(obligations):
    reasons = []
    if obligations.get("unfinished_work"):
        reasons.append("%d unfinished" % len(obligations["unfinished_work"]))
    if obligations.get("unexamined_reviews"):
        reasons.append("%d unexamined review(s)" % len(
            obligations["unexamined_reviews"]))
    if obligations.get("pending_nodes"):
        reasons.append("%d pending node(s)" % len(
            obligations["pending_nodes"]))
    if not obligations.get("has_work") and not obligations.get("pending_nodes"):
        reasons.append("no admitted work")
    return "; ".join(reasons) or "not eligible"
