"""The engineering query layer: read-only projections over structured
engineering artifacts.

Autobot reasons over ARTIFACTS, not chat history. Every relation the
Engineer needs already exists as a durable identity somewhere, and
this module joins them without inventing any:

    Objective -> Engineering Decision (typed proposal)
      -> Task / Requirement -> TaskContractRecord -> Assignment
      -> Specialist -> Attempt -> Artifact / Candidate -> Verification
      -> Accepted Result -> Finding / Demonstration -> next Decision

    Specialist -> Capability Demonstration -> Membership -> Seat
      -> Assignment

Protocol objects keep their protocol identities (sequence_id, attempt_id,
call_id, record_hash, contract_digest, receipt content addresses) and are
read from the authority ledger through the READ-ONLY connection.
Intelligence-plane objects (objectives, proposals, findings, specialists,
seats, assignments, demonstrations, decisions) are read from the plan
store. Nothing here writes anything, and nothing here hands out a
database handle: the Engineer receives the PlanningView these
functions build, as plain data.

Queries are available by objective, task/contract, subsystem, finding,
dependency, specialist, capability, seat, sequence/attempt, verification
outcome and accepted result (``QUERIES`` names them for the CLI).
"""

import json

from .demonstrations import provider_report
from .proposals import (
    ACTING_AS,
    CONTRACT_CLASSES,
    SEAT_CLASSES,
    WORKFORCE_CLASSES,
)
from .workforce import (
    MEMBER_STATES,
    OPEN_STATES,
    PM_SELECTED_MODES,
    SpecialistPool,
    TaskforceRoster,
    TaskforceSeatResolver,
)

ACCEPTED_STATES = ("ACCEPTED", "DEPENDENCIES_RELEASED", "COMPLETED")


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def _plan(plan, sql, args=()):
    return [dict(r) for r in plan.conn.execute(sql, args).fetchall()]


def _ro(ro, sql, args=()):
    return [dict(r) for r in ro.execute(sql, args).fetchall()]


def _ro_one(ro, sql, args=()):
    row = ro.execute(sql, args).fetchone()
    return dict(row) if row else None


def _roster(plan):
    """A read-only roster over the plan store (no pool mirror is run)."""
    return TaskforceRoster(plan, SpecialistPool())


def _marks(values):
    return ",".join("?" for _ in values)


# ------------------------------------------------------------ primitives

def _requirement_of(proposal_row):
    body = _loads(proposal_row.get("body"), {})
    return body.get("requirement", {}), body


def _finalization_class(proposal_row, requirement):
    if proposal_row["class"] in ("INVESTIGATE", "REQUEST_REVIEW"):
        return "READ_ONLY"
    return requirement.get("finalization_class") or "READ_ONLY"


def contract_proposals(plan, objective_id=None, sequence_id=None):
    sql = ("SELECT * FROM proposals WHERE class IN (%s)"
           % _marks(CONTRACT_CLASSES))
    args = list(CONTRACT_CLASSES)
    if objective_id is not None:
        sql += " AND objective_id = ?"
        args.append(objective_id)
    if sequence_id is not None:
        sql += " AND sequence_id = ?"
        args.append(sequence_id)
    return _plan(plan, sql + " ORDER BY created_utc, rowid", tuple(args))


def objective_of_sequence(plan, sequence_id):
    row = plan.conn.execute(
        "SELECT objective_id FROM proposals WHERE sequence_id = ? AND"
        " status = 'ADMITTED' AND class IN (%s) ORDER BY created_utc"
        " LIMIT 1" % _marks(CONTRACT_CLASSES),
        (sequence_id,) + CONTRACT_CLASSES).fetchone()
    return row["objective_id"] if row else None


def work_requirement(plan, sequence_id):
    """The work requirement a sequence froze, as the proposal that created
    it stated it: the capability, the required role, the subsystem. It
    names WHAT the work needs and never who does it."""
    row = plan.conn.execute(
        "SELECT * FROM proposals WHERE sequence_id = ? AND status ="
        " 'ADMITTED' ORDER BY created_utc, rowid LIMIT 1",
        (sequence_id,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    body = _loads(row["body"], {})
    requirement = body.get("requirement", {})
    roles = requirement.get("roles", {})
    required = sorted(r for r in roles if roles[r].get("required")) \
        or sorted(roles)
    return {"proposal": row, "body": body,
            "task": requirement.get("objective"),
            "capability": requirement.get("capability"),
            "role": required[0] if required else None,
            "subsystem": requirement.get("subsystem"),
            "finalization_class": _finalization_class(row, requirement)}


def review_authors(plan, ro, work):
    """The specialists whose work a REQUEST_REVIEW examines: whoever was
    assigned to the reviewed record's sequence. A review is independent
    of its subject — neither the Taskforce nor the Project Manager may
    staff one of these onto it."""
    if not work or ro is None:
        return []
    if work["proposal"]["class"] != "REQUEST_REVIEW":
        return []
    record_hash = (work["body"].get("review_of") or {}).get("record_hash")
    if not record_hash:
        return []
    authors = set()
    for row in ro.execute(
            "SELECT sequence_id FROM sequences WHERE record_hash = ?",
            (record_hash,)).fetchall():
        for assignment in plan.conn.execute(
                "SELECT specialist_id FROM project_assignments WHERE"
                " sequence_id = ? AND status != 'ABANDONED'",
                (row["sequence_id"],)).fetchall():
            authors.add(assignment["specialist_id"])
    return sorted(authors)


def _assignment_summary(row):
    execution = _loads(row["execution"], {}) or {}
    return {"escalation": execution.get("escalation"),
            # The execution route the attempt was dispatched on (routing
            # detail: removed from what the Engineer reasons over).
            "route": execution.get("route"),
            # What a REVIEW said about itself: {"verdict": "EXAMINED" |
            # "NOT_EXAMINED" | None}, or absent for work that is not a
            # collected review.
            "review": execution.get("review"),
            "assignment_id": row["assignment_id"],
            "project_id": row["project_id"],
            "workstream_id": row["workstream_id"],
            "attempt_ordinal": row["attempt_ordinal"],
            "attempt_id": row["attempt_id"], "call_id": row["call_id"],
            "seat_id": row["seat_id"],
            "specialist_id": row["specialist_id"],
            "capability": row["capability"], "role": row["role"],
            "provider": row["provider"], "model": row["model"],
            "selection_path": row["selection_path"],
            "decided_by": row["decided_by"],
            "standing_at_assignment": row["standing_at_assignment"],
            "status": row["status"], "outcome": row["outcome"]}


REVIEW_NOT_STATED = "NOT_STATED"


def review_verdict(assignments):
    """What a review workstream's latest collected attempt said about
    itself (D-TF-32): ``EXAMINED``, ``NOT_EXAMINED``, ``NOT_STATED`` when
    the reviewer's response carried no verdict line, or None while no
    attempt of the review has been collected."""
    verdict = None
    for assignment in assignments:
        review = assignment.get("review")
        if isinstance(review, dict):
            verdict = review.get("verdict") or REVIEW_NOT_STATED
    return verdict


def unexamined_reviews(workstreams):
    """The reviewed records whose reviews are finished and none of which
    examined the work: for each such record, the review workstreams that
    said NOT_EXAMINED. A record with any EXAMINED review is reviewed; a
    record whose reviews only say nothing is the Engineer's to judge and
    is not listed. ``workstreams`` are task rows (``task`` /
    ``engineering_view``)."""
    by_record = {}
    for row in workstreams:
        if row.get("class") != "REQUEST_REVIEW" or \
                row.get("state") != "COMPLETED":
            continue
        by_record.setdefault(row.get("reviews_record"), []).append(row)
    out = []
    for record_hash, reviews in by_record.items():
        verdicts = [r.get("review_verdict") for r in reviews]
        if "EXAMINED" in verdicts:
            continue
        stopped = [r for r in reviews
                   if r.get("review_verdict") == "NOT_EXAMINED"]
        if stopped:
            out.append({"reviews_record": record_hash,
                        "reviews": [r.get("sequence_id") for r in stopped]})
    return out


def task(plan, ro, proposal_row):
    """One task as the engineering organization sees it: the decision that
    created it, its frozen identity, where authority has it, who was
    staffed onto it, and what verification concluded."""
    requirement, body = _requirement_of(proposal_row)
    sequence_id = proposal_row["sequence_id"]
    sequence = _ro_one(
        ro, "SELECT s.sequence_id, s.state, s.record_hash,"
            " s.contract_digest, s.requirement_digest, s.repairs_used,"
            " r.task_contract_id FROM sequences s JOIN records r ON"
            " r.record_hash = s.record_hash WHERE s.sequence_id = ?",
        (sequence_id,)) if sequence_id else None
    attempts = _ro(
        ro, "SELECT a.attempt_id, a.state, c.call_id, c.role, c.provider"
            " FROM attempts a LEFT JOIN attempt_calls c ON c.attempt_id ="
            " a.attempt_id WHERE a.sequence_id = ? ORDER BY a.rowid",
        (sequence_id,)) if sequence_id else []
    verifications = []
    for attempt in attempts:
        for row in _ro(
                ro, "SELECT assertion_id, outcome, receipt_sha256 FROM"
                    " attempt_verifications WHERE attempt_id = ?"
                    " ORDER BY assertion_id", (attempt["attempt_id"],)):
            row["attempt_id"] = attempt["attempt_id"]
            verifications.append(row)
    assignments = [_assignment_summary(r) for r in _plan(
        plan, "SELECT * FROM project_assignments WHERE sequence_id = ?"
              " ORDER BY attempt_ordinal", (sequence_id,))] \
        if sequence_id else []
    predecessors = list(requirement.get("predecessors", []))
    reviewed = (body.get("review_of") or {}).get("record_hash")
    if proposal_row["class"] == "REQUEST_REVIEW" and reviewed:
        # The edge the compiler adds for a review: it waits on the
        # reviewed record's acceptance.
        predecessors.append({
            "edge_id": "review-of", "edge_kind": "RECORD",
            "target": reviewed, "observation_requirement": "ACCEPTANCE"})
    return {
        "review_verdict": review_verdict(assignments)
        if proposal_row["class"] == "REQUEST_REVIEW" else None,
        "proposal_id": proposal_row["proposal_id"],
        "class": proposal_row["class"],
        "proposal_status": proposal_row["status"],
        "objective_id": proposal_row["objective_id"],
        "rationale": proposal_row["rationale"],
        "task": requirement.get("objective"),
        "capability": requirement.get("capability"),
        "roles": sorted(requirement.get("roles", {})),
        "finalization_class": _finalization_class(proposal_row,
                                                  requirement),
        "subsystem": requirement.get("subsystem"),
        "predecessors": predecessors,
        "reviews_record": reviewed
        if proposal_row["class"] == "REQUEST_REVIEW" else None,
        "provenance": body.get("provenance", {}),
        "sequence_id": sequence_id,
        "task_contract_id": (sequence or {}).get("task_contract_id")
                            or proposal_row["task_contract_id"],
        "record_hash": (sequence or {}).get("record_hash"),
        "contract_digest": (sequence or {}).get("contract_digest")
                           or proposal_row["contract_digest"],
        "state": (sequence or {}).get("state"),
        "repairs_used": (sequence or {}).get("repairs_used"),
        "attempts": attempts,
        "assignments": assignments,
        "verifications": verifications,
    }


def tasks(plan, ro, objective_id):
    return [task(plan, ro, row) for row in contract_proposals(
        plan, objective_id=objective_id)
        if row["status"] in ("ADMITTED", "QUEUED_FOR_YOU", "REJECTED")]


def completion_obligations_from_work(work):
    """Organizational closure predicate over pre-fetched work items.

    Returns a dict of outstanding obligations. The ``eligible`` flag
    is True only when every obligation is empty. This answers
    organizational questions only — never whether a sequence verified,
    whether an artifact is correct, or whether t-s41 should release.

    Authority determines whether each result satisfied its frozen
    contract. Organization determines whether the collection of
    accepted results is sufficient to finish the objective."""
    unfinished = [w for w in work
                  if w.get("sequence_id") and w["state"] != "COMPLETED"]
    unexamined = unexamined_reviews(work)
    result = {
        "unfinished_work": [
            {"sequence_id": w.get("sequence_id"), "state": w["state"]}
            for w in unfinished],
        "unexamined_reviews": unexamined,
        "has_work": bool(work),
    }
    result["eligible"] = (
        result["has_work"]
        and not result["unfinished_work"]
        and not result["unexamined_reviews"])
    return result


def completion_obligations(plan, ro, objective_id):
    """Organizational closure predicate for an objective.

    Fetches the objective's work AND checks for pending orchestration
    nodes that haven't become proposals yet. Used by Orchestrator
    COMPLETE_RUN and ``objectives --close``."""
    result = completion_obligations_from_work(
        tasks(plan, ro, objective_id))
    pending_nodes = []
    if hasattr(plan, "active_orchestration_runs"):
        for run in plan.active_orchestration_runs(objective_id):
            for node in plan.orchestration_nodes(
                    run["orchestration_id"]):
                if node["status"] in ("PENDING", "PLANNED"):
                    pending_nodes.append({
                        "node_id": node["node_id"],
                        "node_key": node.get("node_key"),
                        "status": node["status"]})
    if pending_nodes:
        result["pending_nodes"] = pending_nodes
        result["eligible"] = False
    return result


def dependencies(plan, ro, objective_id):
    """The objective's dependency graph: each frozen predecessor edge,
    resolved to the task it points at, with whether authority has
    released it (released_edges is the ONLY truth for that)."""
    rows = [t for t in tasks(plan, ro, objective_id) if t["sequence_id"]]
    by_value = {}
    for row in rows:
        for value in (row["record_hash"], row["contract_digest"]):
            if value:
                by_value[value] = row["sequence_id"]
    edges = []
    for row in rows:
        for edge in row["predecessors"]:
            predecessor = by_value.get(edge.get("target"))
            released = _ro_one(
                ro, "SELECT release_id FROM released_edges WHERE"
                    " edge_kind = ? AND edge_value = ?",
                (edge.get("edge_kind"), edge.get("target")))
            edges.append({
                "sequence_id": row["sequence_id"],
                "depends_on_sequence_id": predecessor,
                "edge_id": edge.get("edge_id"),
                "edge_kind": edge.get("edge_kind"),
                "target": edge.get("target"),
                "observation_requirement":
                    edge.get("observation_requirement"),
                "released": released is not None})
    return edges


def findings(plan, objective_id=None, sequence_id=None, specialist_id=None,
             finding_id=None):
    """Findings with the provenance that makes them usable: the attempt
    and call that produced them, and — through the assignment — the
    specialist."""
    rows = _plan(plan, "SELECT * FROM findings ORDER BY admitted_utc,"
                       " rowid")
    out = []
    for row in rows:
        if finding_id is not None and row["finding_id"] != finding_id:
            continue
        if sequence_id is not None and row["sequence_id"] != sequence_id:
            continue
        owner = objective_of_sequence(plan, row["sequence_id"])
        if objective_id is not None and owner != objective_id:
            continue
        assignment = plan.conn.execute(
            "SELECT specialist_id, seat_id FROM project_assignments"
            " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
        if specialist_id is not None and (
                assignment is None
                or assignment["specialist_id"] != specialist_id):
            continue
        out.append({
            "finding_id": row["finding_id"],
            "objective_id": owner,
            "sequence_id": row["sequence_id"],
            "attempt_id": row["attempt_id"], "call_id": row["call_id"],
            "specialist_id": assignment["specialist_id"]
            if assignment else None,
            "role": row["role"],
            "finding_class": row["finding_class"],
            "affected_component": row["affected_component"],
            "statement": row.get("statement"),
            "significance": row["significance"],
            "confidence": row["confidence"],
            "relation_to_requirement": row["relation_to_requirement"],
            "suggested_follow_up": row["suggested_follow_up"],
            "evidence_refs": _loads(row["evidence_refs"], []),
            "consumed_in_cycle": row["consumed_in_cycle"]})
    return out


def decisions(plan, objective_id):
    """Every typed Engineer decision for one objective, with the
    provenance preserved at decision time: the role it was made in
    (``acting_as``: ENGINEER or PROJECT_MANAGER), the snapshot it reasoned
    over, its rationale, and the findings, planning events and workforce
    information it used."""
    out = []
    for row in _plan(
            plan, "SELECT * FROM proposals WHERE objective_id = ?"
                  " ORDER BY created_utc, rowid", (objective_id,)):
        body = _loads(row["body"], {})
        entry = {
            "proposal_id": row["proposal_id"], "class": row["class"],
            "acting_as": ACTING_AS.get(row["class"]),
            "status": row["status"], "cycle_id": row["cycle_id"],
            "rationale": row["rationale"],
            "snapshot": {"entries": row["snapshot_entries"],
                         "head_hash": row["snapshot_head"]},
            "provenance": body.get("provenance", {}),
            "sequence_id": row["sequence_id"],
            "created_utc": row["created_utc"], "note": row["note"]}
        if row["class"] in WORKFORCE_CLASSES:
            entry["specialist_id"] = body.get("specialist_id")
            entry["capability"] = body.get("capability")
            entry["role"] = body.get("role")
            entry["demonstrations"] = body.get("demonstrations", [])
            entry["reason"] = body.get("reason")
        if row["class"] in SEAT_CLASSES:
            entry["staffing"] = body.get("staffing")
            entry["capability"] = body.get("capability")
        if row["class"] == "OPEN_PROJECT":
            entry["project"] = body.get("project")
        if row["class"] in CONTRACT_CLASSES:
            entry["workstream"] = body.get("workstream")
        out.append(entry)
    return out


def finding_consequences(plan, finding_id):
    """Which decisions a finding triggered (their provenance names it)."""
    out = []
    for row in _plan(plan, "SELECT proposal_id, class, status,"
                           " objective_id, sequence_id, rationale, body"
                           " FROM proposals ORDER BY created_utc"):
        body = _loads(row["body"], {})
        if finding_id in body.get("provenance", {}).get("findings", []):
            out.append({k: row[k] for k in (
                "proposal_id", "class", "status", "objective_id",
                "sequence_id", "rationale")})
    return out


def accepted_results(plan, ro, objective_id):
    """What the objective has actually had accepted by authority, with
    the acceptance and release receipt references."""
    out = []
    for row in tasks(plan, ro, objective_id):
        if row["state"] not in ACCEPTED_STATES:
            continue
        released = _ro_one(
            ro, "SELECT acceptance_sha256, release_sha256, released_utc"
                " FROM released_edges WHERE predecessor_sequence_id = ?"
                " ORDER BY release_id LIMIT 1", (row["sequence_id"],))
        candidate = None
        for attempt in reversed(row["attempts"]):
            if attempt["state"] == "VERIFIED":
                candidate = _ro_one(
                    ro, "SELECT content_sha256, candidate_tree FROM"
                        " attempt_candidates WHERE attempt_id = ?",
                    (attempt["attempt_id"],))
                break
        out.append({
            "sequence_id": row["sequence_id"], "task": row["task"],
            "state": row["state"],
            "finalization_class": row["finalization_class"],
            "task_contract_id": row["task_contract_id"],
            "record_hash": row["record_hash"],
            "candidate": candidate, "release": released})
    return out


def open_questions(plan, ro, objective_id):
    """What still needs an engineering answer: findings that suggest
    follow-up no decision has taken up, sequences authority has halted,
    and work waiting on YOUR registered authority."""
    cited = set()
    for row in _plan(plan, "SELECT body FROM proposals WHERE objective_id"
                           " = ?", (objective_id,)):
        cited.update(_loads(row["body"], {}).get(
            "provenance", {}).get("findings", []))
    unaddressed = [f for f in findings(plan, objective_id=objective_id)
                   if f["suggested_follow_up"]
                   and f["finding_id"] not in cited]
    rows = tasks(plan, ro, objective_id)
    halted = [{"sequence_id": t["sequence_id"], "task": t["task"],
               "state": t["state"]} for t in rows
              if t["state"] in ("HALTED", "HALTED_AFTER_VERIFICATION")]
    waiting = [{"proposal_id": r["proposal_id"], "class": r["class"],
                "status": r["status"], "note": r["note"]}
               for r in _plan(
                   plan, "SELECT * FROM proposals WHERE objective_id = ?"
                         " AND status IN ('WAITING','QUEUED_FOR_YOU')"
                         " ORDER BY created_utc", (objective_id,))]
    return {"unaddressed_findings": unaddressed, "halted": halted,
            "waiting_on_you": waiting}


def risks(plan, objective_id):
    """System risks on record: high-significance defect or security
    findings."""
    return [f for f in findings(plan, objective_id=objective_id)
            if f["finding_class"] in ("architectural_defect",
                                      "security_issue",
                                      "implementation_defect")
            and str(f["significance"]).lower().startswith("high")]


# ------------------------------------------------------------ by objective

def objective_state(plan, ro, objective_id):
    row = plan.objective(objective_id)
    if row is None:
        return None
    task_rows = tasks(plan, ro, objective_id)
    states = {}
    for entry in task_rows:
        states[entry["state"] or entry["proposal_status"]] = states.get(
            entry["state"] or entry["proposal_status"], 0) + 1
    return {
        "objective_id": objective_id, "text": row["text"],
        "status": row["status"], "authored_by": row["authored_by"],
        "constraints": _loads(row.get("constraints"), []),
        "created_utc": row["created_utc"],
        "completed_utc": row["completed_utc"],
        "budget": {"limits": plan.budget_limits(objective_id),
                   "used": plan.budget_usage(objective_id)},
        "task_states": states,
        "projects": [project_state(plan, ro, p["project_id"])
                     for p in plan.projects(objective_id)],
        "tasks": task_rows,
        "dependencies": dependencies(plan, ro, objective_id),
        "decisions": decisions(plan, objective_id),
        "findings": findings(plan, objective_id=objective_id),
        "accepted_results": accepted_results(plan, ro, objective_id),
        "open_questions": open_questions(plan, ro, objective_id),
        "risks": risks(plan, objective_id),
    }


def engineering_view(plan, ro, objectives):
    """The compact per-objective engineering projection the Engineer
    reasons over (PlanningView.engineering): active workstreams, the
    dependency graph, verified and accepted results, failed assertions,
    open questions and the decisions already taken."""
    view = {}
    for objective in objectives:
        objective_id = objective["objective_id"]
        task_rows = tasks(plan, ro, objective_id)
        workstreams = []
        failed = []
        for entry in task_rows:
            workstreams.append({
                "sequence_id": entry["sequence_id"],
                "proposal_id": entry["proposal_id"],
                "task": entry["task"], "class": entry["class"],
                "capability": entry["capability"],
                "roles": entry["roles"],
                "finalization_class": entry["finalization_class"],
                "subsystem": entry["subsystem"],
                "state": entry["state"] or entry["proposal_status"],
                "record_hash": entry["record_hash"],
                "reviews_record": entry["reviews_record"],
                "review_verdict": entry["review_verdict"],
                "attempts": len(entry["attempts"]),
                "repairs_used": entry["repairs_used"],
                "staffing": [
                    {"specialist_id": a["specialist_id"],
                     "seat_id": a["seat_id"],
                     "selection_path": a["selection_path"],
                     "decided_by": a["decided_by"],
                     "status": a["status"], "outcome": a["outcome"]}
                    for a in entry["assignments"]]})
            for verification in entry["verifications"]:
                if verification["outcome"] in ("FAIL", "INCONCLUSIVE"):
                    failed.append({
                        "sequence_id": entry["sequence_id"],
                        "attempt_id": verification["attempt_id"],
                        "assertion_id": verification["assertion_id"],
                        "outcome": verification["outcome"]})
        view[objective_id] = {
            "constraints": _loads(objective.get("constraints"), []),
            "workstreams": workstreams,
            "dependencies": dependencies(plan, ro, objective_id),
            "accepted_results": [
                {"sequence_id": r["sequence_id"], "task": r["task"],
                 "finalization_class": r["finalization_class"]}
                for r in accepted_results(plan, ro, objective_id)],
            "failed_assertions": failed,
            "open_questions": open_questions(plan, ro, objective_id),
            "decisions": [
                {"proposal_id": d["proposal_id"], "class": d["class"],
                 "status": d["status"], "rationale": d["rationale"]}
                for d in decisions(plan, objective_id)],
            "risks": [{"finding_id": f["finding_id"],
                       "statement": f["statement"],
                       "affected_component": f["affected_component"]}
                      for f in risks(plan, objective_id)],
        }
    return view


# ------------------------------------------------- by sequence / attempt

def by_sequence(plan, ro, sequence_id):
    rows = contract_proposals(plan, sequence_id=sequence_id)
    admitted = [r for r in rows if r["status"] == "ADMITTED"]
    base = task(plan, ro, admitted[0]) if admitted else {
        "sequence_id": sequence_id}
    base["findings"] = findings(plan, sequence_id=sequence_id)
    base["demonstrations"] = _plan(
        plan, "SELECT demonstration_id, specialist_id, seat_id, outcome,"
              " attempt_id FROM capability_demonstrations WHERE"
              " sequence_id = ? ORDER BY recorded_utc", (sequence_id,))
    base["repair_decisions"] = [
        {"proposal_id": r["proposal_id"], "class": r["class"],
         "status": r["status"], "rationale": r["rationale"],
         "staffing": _loads(r["body"], {}).get("staffing")}
        for r in _plan(
            plan, "SELECT * FROM proposals WHERE sequence_id = ? AND class"
                  " IN (%s) ORDER BY created_utc" % _marks(SEAT_CLASSES),
            (sequence_id,) + SEAT_CLASSES)]
    return base


def by_attempt(plan, ro, attempt_id):
    attempt = _ro_one(ro, "SELECT * FROM attempts WHERE attempt_id = ?",
                      (attempt_id,))
    if attempt is None:
        return None
    call = _ro_one(ro, "SELECT * FROM attempt_calls WHERE attempt_id = ?",
                   (attempt_id,))
    assignment = plan.conn.execute(
        "SELECT * FROM project_assignments WHERE attempt_id = ?",
        (attempt_id,)).fetchone()
    return {
        "attempt_id": attempt_id, "sequence_id": attempt["sequence_id"],
        "state": attempt["state"],
        "attempt_contract_hash": attempt["attempt_contract_hash"],
        "call": call,
        "assignment": _assignment_summary(dict(assignment))
        if assignment else None,
        "witnesses": _ro(
            ro, "SELECT witness_kind, producer, content_sha256 FROM"
                " attempt_witnesses WHERE attempt_id = ? ORDER BY rowid",
            (attempt_id,)),
        "receipts": _ro(
            ro, "SELECT content_sha256, completeness FROM attempt_receipts"
                " WHERE attempt_id = ? ORDER BY rowid", (attempt_id,)),
        "candidate": _ro_one(
            ro, "SELECT content_sha256, finalization_class,"
                " candidate_tree FROM attempt_candidates WHERE attempt_id"
                " = ?", (attempt_id,)),
        "verifications": _ro(
            ro, "SELECT assertion_id, verifier_id, outcome,"
                " receipt_sha256 FROM attempt_verifications WHERE"
                " attempt_id = ? ORDER BY assertion_id", (attempt_id,)),
        "approvals": _plan(
            plan, "SELECT request_id, tool_name, subject, decision, reason"
                  " FROM assignment_approvals WHERE call_id = ? ORDER BY"
                  " decided_utc, request_id",
            ((call or {}).get("call_id"),)),
        "findings": [f for f in findings(
            plan, sequence_id=attempt["sequence_id"])
            if f["attempt_id"] == attempt_id],
    }


# -------------------------------------- by specialist / capability / seat

def _demonstration_view(row):
    return {"demonstration_id": row["demonstration_id"],
            "specialist_id": row["specialist_id"],
            "seat_id": row["seat_id"],
            "standing": row["standing"],
            "project_id": row["project_id"],
            "assignment_id": row["assignment_id"],
            "capability": row["capability"], "role": row["role"],
            "environment": row["environment"],
            "system_domains": _loads(row["system_domains"], []),
            "sequence_id": row["sequence_id"],
            "attempt_id": row["attempt_id"], "call_id": row["call_id"],
            "task_contract_id": row["task_contract_id"],
            "record_hash": row["record_hash"],
            "contract_digest": row["contract_digest"],
            "verification_refs": _loads(row["verification_refs"], []),
            "evidence_refs": _loads(row["evidence_refs"], {}),
            "criteria": _loads(row["criteria"], {}),
            "outcome": row["outcome"],
            "observed_findings": _loads(row["observed_findings"], []),
            "recorded_utc": row["recorded_utc"]}


def by_seat(plan, seat_id):
    """One retained seat with the full evidence trail behind its status:
    what it represents, the membership it belongs to, every transition
    and its cause, the demonstrations, and the decision that retained (or
    released) it."""
    roster = _roster(plan)
    seat = roster.seat(seat_id)
    if seat is None:
        return None
    triple = (seat["specialist_id"], seat["capability"], seat["role"])
    view = roster.seat_view(seat)
    view["membership"] = roster.membership(seat["specialist_id"])
    view["history"] = roster.history(seat_id)
    view["demonstrations"] = [
        _demonstration_view(d) for d in roster.demonstrations(
            specialist_id=triple[0], capability=triple[1], role=triple[2])]
    view["decisions"] = [
        dict(d, demonstration_ids=_loads(d["demonstration_ids"], []))
        for d in roster.decisions(specialist_id=triple[0],
                                  capability=triple[1], role=triple[2])]
    view["assignments"] = [_assignment_summary(a) for a in
                           roster.assignments(seat_id=seat_id)]
    return view


def by_specialist(plan, specialist_id):
    """One specialist: how it is routed, whether it is a Taskforce member
    and in which seats, every project it took part in, every assignment,
    what it demonstrated and what was decided about it."""
    roster = _roster(plan)
    specialist = roster.specialist(specialist_id)
    if specialist is None:
        return None
    seats = roster.seats(specialist_id=specialist_id)
    return {
        "specialist_id": specialist_id,
        "handle": specialist["handle"],
        "provider": specialist["provider"], "model": specialist["model"],
        "route": _loads(specialist["route"], {}),
        "offers": _loads(specialist["offers"], []),
        "origin": specialist["origin"],
        "first_seen": specialist["first_seen_utc"],
        "status": specialist["status"],
        "taskforce_member": roster.is_member(specialist_id),
        "membership": roster.membership(specialist_id),
        "seats": [roster.seat_view(s) for s in seats],
        "projects": _plan(
            plan, "SELECT * FROM project_team WHERE specialist_id = ?"
                  " ORDER BY joined_utc, rowid", (specialist_id,)),
        "assignments": [_assignment_summary(a) for a in
                        roster.assignments(specialist_id=specialist_id)],
        "demonstrations": [_demonstration_view(d) for d in
                           roster.demonstrations(
                               specialist_id=specialist_id)],
        "decisions": [
            dict(d, demonstration_ids=_loads(d["demonstration_ids"], []))
            for d in roster.decisions(specialist_id=specialist_id)],
        "findings": findings(plan, specialist_id=specialist_id),
    }


def by_capability(plan, capability):
    """Who holds a retained seat for, has demonstrated, or could be
    selected as an outside specialist for one capability."""
    roster = _roster(plan)
    seats = [roster.seat_view(s) for s in roster.seats(
        capability=capability)]
    demos = roster.demonstrations(capability=capability)
    return {
        "capability": capability,
        "retained": [s for s in seats if s["status"] in MEMBER_STATES],
        "other_seats": [s for s in seats
                        if s["status"] not in MEMBER_STATES],
        "demonstrated_by": sorted({d["specialist_id"] for d in demos
                                   if d["outcome"] == "QUALIFYING"}),
        "demonstrations": [_demonstration_view(d) for d in demos],
        "outside_specialists": [
            s for s in roster.view()["outside_specialists"]
            if any(o["capability"] == capability for o in s["offers"])],
    }


def by_subsystem(plan, ro, subsystem):
    """Everything engineered against one subsystem: its tasks, the
    findings that name it, and the seats with experience in it."""
    rows = []
    for row in contract_proposals(plan):
        requirement, _body = _requirement_of(row)
        if requirement.get("subsystem") == subsystem:
            rows.append(task(plan, ro, row))
    named = [f for f in findings(plan)
             if subsystem in (f["affected_component"] or "")]
    roster = _roster(plan)
    experienced = []
    for demo in roster.demonstrations(outcome="QUALIFYING"):
        if subsystem in _loads(demo["system_domains"], []):
            experienced.append({"seat_id": demo["seat_id"],
                                "specialist_id": demo["specialist_id"],
                                "demonstration_id":
                                    demo["demonstration_id"]})
    return {"subsystem": subsystem, "tasks": rows, "findings": named,
            "experienced_seats": experienced}


def by_verification_outcome(plan, ro, outcome):
    """Every admitted verification row with one outcome, joined to the
    task and the specialist whose work it judged."""
    out = []
    for row in _ro(
            ro, "SELECT v.attempt_id, v.assertion_id, v.verifier_id,"
                " v.outcome, v.receipt_sha256, a.sequence_id FROM"
                " attempt_verifications v JOIN attempts a ON a.attempt_id"
                " = v.attempt_id WHERE v.outcome = ? ORDER BY v.rowid",
            (outcome,)):
        assignment = plan.conn.execute(
            "SELECT specialist_id, seat_id, capability FROM"
            " project_assignments WHERE attempt_id = ?",
            (row["attempt_id"],)).fetchone()
        row["objective_id"] = objective_of_sequence(plan,
                                                    row["sequence_id"])
        row["specialist_id"] = assignment["specialist_id"] \
            if assignment else None
        row["seat_id"] = assignment["seat_id"] if assignment else None
        row["capability"] = assignment["capability"] \
            if assignment else None
        out.append(row)
    return out


# ------------------------------------------------------------ provenance

def why_selected(plan, assignment_id):
    """Why this specialist executes this attempt: the request, who
    decided (the Taskforce resolving a retained seat, or the Project
    Manager selecting an outside specialist — with the decision that says
    so), the candidates on record and — for a retained seat — the outside
    specialist a Project Manager would have had to select without it."""
    row = plan.conn.execute(
        "SELECT * FROM project_assignments WHERE assignment_id = ?",
        (assignment_id,)).fetchone()
    if row is None:
        return None
    row = dict(row)
    summary = _assignment_summary(row)
    summary["selected_reason"] = _loads(row["selected_reason"], {})
    summary["authorized_by_proposal"] = row["authorized_by_proposal"]
    summary["assignment_decision"] = row["assignment_decision"]
    if row["assignment_decision"]:
        decision = plan.proposal(row["assignment_decision"]) or {}
        summary["assignment_decision_rationale"] = decision.get("rationale")
    summary["selected_utc"] = row["selected_utc"]
    summary["sequence_id"] = row["sequence_id"]
    return summary


def why_retained(plan, seat_id):
    """The evidence that justified a seat's status: the retention
    decision that created the membership and the seat, its rationale and
    the verified demonstrations it bound."""
    roster = _roster(plan)
    seat = roster.seat(seat_id)
    if seat is None:
        return None
    out = []
    for decision in roster.decisions(specialist_id=seat["specialist_id"],
                                     capability=seat["capability"],
                                     role=seat["role"]):
        ids = _loads(decision["demonstration_ids"], [])
        out.append({
            "decision_id": decision["decision_id"],
            "kind": decision["kind"], "reason": decision["reason"],
            "rationale": decision["rationale"],
            "decided_by": decision["decided_by"],
            "decided_utc": decision["decided_utc"],
            "proposal_id": decision["proposal_id"],
            "demonstrations": [
                _demonstration_view(d) for d in (
                    roster.demonstration(i) for i in ids)
                if d is not None]})
    membership = roster.membership(seat["specialist_id"]) or {}
    return {"seat_id": seat_id,
            "status": seat["status"],
            "membership_id": seat["membership_id"],
            "membership_status": membership.get("status"),
            "membership_joined_by_decision":
                membership.get("joined_by_decision"),
            "specialist_id": seat["specialist_id"],
            "capability": seat["capability"], "role": seat["role"],
            "decisions": out}


def why_created(plan, sequence_id):
    """Why a task exists: the decision that created it, the snapshot it
    reasoned over, and the findings and events it cited."""
    out = []
    for row in contract_proposals(plan, sequence_id=sequence_id):
        body = _loads(row["body"], {})
        provenance = body.get("provenance", {})
        reasoning = plan.conn.execute(
            "SELECT policy_id, input_digest FROM cycle_reasoning WHERE"
            " cycle_id = ?", (row["cycle_id"],)).fetchone()
        out.append({
            "proposal_id": row["proposal_id"], "class": row["class"],
            "status": row["status"], "objective_id": row["objective_id"],
            "rationale": row["rationale"], "cycle_id": row["cycle_id"],
            "policy_id": reasoning["policy_id"] if reasoning else None,
            "snapshot": {"entries": row["snapshot_entries"],
                         "head_hash": row["snapshot_head"]},
            "provenance": provenance,
            "triggering_findings": [
                f for finding_id in provenance.get("findings", [])
                for f in findings(plan, finding_id=finding_id)]})
    return out


# ------------------------------------------------------- artifact graph

def artifact_graph(plan, ro, objective_id):
    """The objective's engineering artifact graph as nodes and typed
    edges. Every node id is an existing identity; the graph adds only the
    relations between them."""
    nodes, edges = {}, []

    def node(kind, identity, **attrs):
        if identity is None:
            return None
        key = "%s:%s" % (kind, identity)
        entry = nodes.setdefault(key, {"kind": kind, "id": identity})
        entry.update({k: v for k, v in attrs.items() if v is not None})
        return key

    def edge(source, relation, target):
        if source and target:
            item = {"from": source, "relation": relation, "to": target}
            if item not in edges:
                edges.append(item)

    objective = plan.objective(objective_id)
    if objective is None:
        return {"nodes": [], "edges": []}
    root = node("objective", objective_id, status=objective["status"])
    roster = _roster(plan)
    workstream_of = {}
    for project in plan.projects(objective_id):
        p = node("project", project["project_id"],
                 status=project["status"], opened_by=project["opened_by"])
        edge(root, "realized_by", p)
        if project["opened_by_proposal"]:
            edge(node("decision", project["opened_by_proposal"]),
                 "opened", p)
        for workstream in plan.workstreams(project["project_id"]):
            w = node("workstream", workstream["workstream_id"],
                     key=workstream["workstream_key"])
            edge(p, "has_workstream", w)
            workstream_of[workstream["proposal_id"]] = w
        for member in roster.team(project["project_id"]):
            edge(node("specialist", member["specialist_id"]),
                 "takes_part_in", p)
    for decision in decisions(plan, objective_id):
        d = node("decision", decision["proposal_id"],
                 decision_class=decision["class"],
                 acting_as=decision["acting_as"],
                 status=decision["status"])
        edge(root, "decided", d)
        for finding_id in decision["provenance"].get("findings", []):
            edge(node("finding", finding_id), "informed", d)
        if decision["class"] in WORKFORCE_CLASSES:
            edge(d, "decided_about",
                 node("specialist", decision.get("specialist_id")))
    for entry in tasks(plan, ro, objective_id):
        if not entry["sequence_id"]:
            continue
        d = node("decision", entry["proposal_id"])
        t = node("task", entry["sequence_id"], state=entry["state"],
                 capability=entry["capability"],
                 finalization_class=entry["finalization_class"])
        record = node("record", entry["record_hash"],
                      task_contract_id=entry["task_contract_id"])
        edge(d, "created", t)
        edge(workstream_of.get(entry["proposal_id"]), "is_work", t)
        edge(t, "frozen_as", record)
        for assignment in entry["assignments"]:
            a = node("assignment", assignment["assignment_id"],
                     selection_path=assignment["selection_path"],
                     decided_by=assignment["decided_by"],
                     status=assignment["status"])
            specialist = node("specialist", assignment["specialist_id"])
            seat = node("seat", assignment["seat_id"])
            attempt = node("attempt", assignment["attempt_id"])
            edge(t, "staffed_by", a)
            edge(a, "assigns", specialist)
            edge(seat, "resolved_to", a)
            edge(a, "executes_as", attempt)
        for attempt in entry["attempts"]:
            at = node("attempt", attempt["attempt_id"],
                      state=attempt["state"], call_id=attempt["call_id"])
            edge(t, "attempted_by", at)
            candidate = _ro_one(
                ro, "SELECT content_sha256 FROM attempt_candidates WHERE"
                    " attempt_id = ?", (attempt["attempt_id"],))
            if candidate:
                c = node("candidate", candidate["content_sha256"])
                edge(at, "produced", c)
        for verification in entry["verifications"]:
            v = node("verification", verification["receipt_sha256"],
                     assertion_id=verification["assertion_id"],
                     outcome=verification["outcome"])
            edge(node("attempt", verification["attempt_id"]),
                 "verified_by", v)
        if entry["state"] in ACCEPTED_STATES:
            edge(t, "accepted_as", node("accepted_result",
                                        entry["sequence_id"]))
    for finding in findings(plan, objective_id=objective_id):
        f = node("finding", finding["finding_id"],
                 finding_class=finding["finding_class"])
        edge(node("attempt", finding["attempt_id"]), "reported", f)
    for assignment in roster.assignments(objective_id=objective_id):
        for demo in roster.demonstrations(
                specialist_id=assignment["specialist_id"]):
            if demo["assignment_id"] != assignment["assignment_id"]:
                continue
            m = node("demonstration", demo["demonstration_id"],
                     outcome=demo["outcome"], standing=demo["standing"],
                     capability=demo["capability"])
            edge(node("specialist", demo["specialist_id"]),
                 "demonstrated", m)
            edge(node("attempt", demo["attempt_id"]), "evidenced", m)
            # A demonstration supports a seat only through the retention
            # decision that bound it.
            for decision in roster.decisions(
                    specialist_id=demo["specialist_id"],
                    capability=demo["capability"], role=demo["role"]):
                if decision["seat_id"] and demo["demonstration_id"] in \
                        _loads(decision["demonstration_ids"], []):
                    edge(m, "bound_by_%s" % decision["kind"].lower(),
                         node("seat", decision["seat_id"]))
    for key, entry in list(nodes.items()):
        if entry["kind"] == "seat":
            seat = roster.seat(entry["id"])
            if seat:
                entry["status"] = seat["status"]
                membership = node("membership", seat["membership_id"])
                edge(node("specialist", seat["specialist_id"]),
                     "member_through", membership)
                edge(membership, "holds_seat", key)
    return {"nodes": sorted(nodes.values(),
                            key=lambda n: (n["kind"], n["id"])),
            "edges": edges}


# ------------------------------------------------------ taskforce report

def taskforce_report(plan):
    """The PERSISTENT organization's state report: the Taskforce's
    members and the seats they hold (and, for every one, the evidence it
    was retained on), established and inactive seats, available retained
    capabilities, the outside specialists a project may draw on, the
    specialists whose verified work awaits a retention decision, recent
    demonstrations, every workforce decision, and the assignment history.

    Project state is NOT in here: who is on which project's team is
    ``projects_report`` — the two are separate records."""
    roster = _roster(plan)
    view = roster.view(recent=50)
    earned = [why_retained(plan, seat["seat_id"])
              for seat in view["retained_seats"]]
    history = [why_selected(plan, a["assignment_id"])
               for a in roster.assignments()]
    return {
        "members": view["members"],
        "retained_members": view["retained_seats"],
        "established_seats": [s for s in view["retained_seats"]
                              if s["status"] == "ESTABLISHED"],
        "available_capabilities": view["available_capabilities"],
        "inactive_seats": view["inactive_seats"],
        "outside_specialists": view["outside_specialists"],
        "retention_candidates": view["retention_candidates"],
        "active_assignments": view["active_assignments"],
        "recent_demonstrations": view["recent_demonstrations"],
        "assignment_history": history,
        "why_retained": earned,
        "workforce_decisions": [
            dict(d, demonstration_ids=_loads(d["demonstration_ids"], []))
            for d in roster.decisions()],
        "specialists": [
            {"specialist_id": s["specialist_id"], "handle": s["handle"],
             "provider": s["provider"], "model": s["model"],
             "origin": s["origin"], "first_seen": s["first_seen_utc"],
             "status": s["status"],
             "taskforce_member": roster.is_member(s["specialist_id"])}
            for s in roster.specialists()],
        "established_threshold": view["established_threshold"],
        "open_assignments": len(roster.assignments(statuses=OPEN_STATES)),
    }


# -------------------------------------------------------------- projects

def staffing_needs(plan, ro, objective_id, roster):
    """Workstreams that are READY to start and that the Taskforce cannot
    staff from an available retained seat. Each waits for the Project
    Manager: it lists the outside specialists that could be selected, in
    catalog order, with what makes each eligible or not.

    A workstream the Project Manager already decided (an open explicit
    selection waiting for its specialist) is not a need, and neither is
    work that already ran and has no word to run again."""
    if roster is None:
        return []
    resolver = TaskforceSeatResolver(roster)
    needs = []
    for row in contract_proposals(plan, objective_id=objective_id):
        sequence_id = row["sequence_id"]
        if row["status"] != "ADMITTED" or not sequence_id:
            continue
        sequence = _ro_one(
            ro, "SELECT state, current_attempt_id FROM sequences WHERE"
                " sequence_id = ?", (sequence_id,))
        if sequence is None or sequence["state"] != "READY" \
                or sequence["current_attempt_id"] is not None:
            continue
        ordinal = _ro_one(
            ro, "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
            (sequence_id,))["n"] + 1
        if ordinal > 1 and plan.open_authorization(sequence_id) is None:
            continue
        if roster.assignment_for(sequence_id, ordinal) is not None:
            continue
        decision = plan.open_staffing_decision(sequence_id)
        if decision is not None and decision["mode"] in PM_SELECTED_MODES:
            continue
        work = work_requirement(plan, sequence_id)
        if not work or not work["capability"]:
            continue
        independent = review_authors(plan, ro, work)
        directive = None
        if decision is not None and decision["mode"] == "different_member":
            previous = roster.assignment_for(sequence_id, ordinal - 1) \
                if ordinal > 1 else None
            directive = {"mode": "different_member",
                         "previous_specialist_id":
                             previous["specialist_id"] if previous
                             else None}
        why = resolver.explain_unavailable(
            work["capability"], work["role"], directive, independent)
        if any(c["available"] for c in why["retained"]):
            continue  # the Taskforce will resolve a retained member
        workstream = plan.workstream_by_proposal(row["proposal_id"]) or {}
        needs.append({
            "sequence_id": sequence_id,
            "workstream": workstream.get("workstream_key"),
            "task": work["task"],
            "capability": work["capability"], "role": work["role"],
            "attempt_ordinal": ordinal,
            "cause": why["cause"],
            "independent_of": independent,
            "retained_seats": [
                {"seat_id": c["seat_id"],
                 "specialist_id": c["specialist_id"],
                 "available": c["available"]} for c in why["retained"]],
            "outside_candidates": why["outside_candidates"]})
    return needs


def failed_attempts(ro, attempts, store=None):
    """The attempts of one workstream that did not produce accepted work,
    in order: FAILED or STALE (the session failed at the provider — with
    the provider's own report when a content store is given) and REJECTED
    (verification failed). They stay part of the workstream's history."""
    out = []
    for attempt in attempts:
        if attempt["state"] not in ("FAILED", "STALE", "REJECTED"):
            continue
        entry = {"attempt_id": attempt["attempt_id"],
                 "state": attempt["state"], "call_id": attempt["call_id"]}
        if attempt["state"] != "REJECTED":
            reported = provider_report(ro, store, attempt["attempt_id"])
            if reported is not None:
                entry["provider_report"] = reported
        out.append(entry)
    return out


def project_state(plan, ro, project_id, roster=None, store=None):
    """One project as a temporary undertaking: the solution it realizes,
    its workstreams and their dependencies (each REFERENCING the work's
    authority state, never copying it), who is assigned to what and on
    which staffing path, and the team — everyone taking part, as a
    retained member or an outside specialist, and whether their
    participation is still open. ``store`` (the content store) lets a
    failed attempt carry what the provider itself reported."""
    project = plan.project(project_id)
    if project is None:
        return None
    roster = roster or _roster(plan)
    objective_id = project["objective_id"]
    key_of_sequence = {}
    entries = []
    for workstream in plan.workstreams(project_id):
        row = plan.proposal(workstream["proposal_id"])
        if row is None:
            continue
        entry = task(plan, ro, row)
        if entry["sequence_id"]:
            key_of_sequence[entry["sequence_id"]] = \
                workstream["workstream_key"]
        entries.append((workstream, entry))
    depends = {}
    for edge in dependencies(plan, ro, objective_id):
        depends.setdefault(edge["sequence_id"], []).append({
            "workstream": key_of_sequence.get(
                edge["depends_on_sequence_id"]),
            "sequence_id": edge["depends_on_sequence_id"],
            "released": edge["released"]})
    key_of_record = {entry["record_hash"]: workstream["workstream_key"]
                     for workstream, entry in entries
                     if entry["record_hash"]}
    workstreams = []
    for workstream, entry in entries:
        sequence_id = entry["sequence_id"]
        workstreams.append({
            "workstream": workstream["workstream_key"],
            "workstream_id": workstream["workstream_id"],
            "proposal_id": entry["proposal_id"],
            "class": entry["class"], "task": entry["task"],
            # For a review: the workstream it reviews, and what the
            # reviewer said about its own review once it was collected.
            "reviews": key_of_record.get(entry["reviews_record"])
            if entry["reviews_record"] else None,
            "review_verdict": entry["review_verdict"],
            "capability": entry["capability"],
            "role": entry["roles"][0] if entry["roles"] else None,
            "finalization_class": entry["finalization_class"],
            "subsystem": entry["subsystem"],
            "sequence_id": sequence_id,
            "state": entry["state"] or entry["proposal_status"],
            "record_hash": entry["record_hash"],
            "attempts": len(entry["attempts"]),
            "failed_attempts": failed_attempts(ro, entry["attempts"],
                                               store),
            "depends_on": depends.get(sequence_id, []),
            "assignments": [
                {"assignment_id": a["assignment_id"],
                 "attempt_ordinal": a["attempt_ordinal"],
                 "specialist_id": a["specialist_id"],
                 "seat_id": a["seat_id"],
                 "selection_path": a["selection_path"],
                 "decided_by": a["decided_by"],
                 "standing_at_assignment": a["standing_at_assignment"],
                 "escalation": a.get("escalation"),
                 "status": a["status"], "outcome": a["outcome"]}
                for a in entry["assignments"]],
            "assignment_decisions": [
                {"decision_id": d["decision_id"], "mode": d["mode"],
                 "specialist_id": d["specialist_id"],
                 "escalation": d.get("escalation"),
                 "status": d["status"], "note": d["note"]}
                for d in plan.assignment_decisions(
                    sequence_id=sequence_id)] if sequence_id else [],
        })
    team = []
    for member in roster.team(project_id):
        specialist_id = member["specialist_id"]
        team.append({
            "specialist_id": specialist_id, "handle": member["handle"],
            "joined_as": member["joined_as"],
            "participation": member["status"],
            "joined_utc": member["joined_utc"],
            "ended_utc": member["ended_utc"],
            "end_reason": member["end_reason"],
            # What the specialist is to the Taskforce NOW — a separate
            # fact from how it joined this project.
            "taskforce_member": roster.is_member(specialist_id),
            "assignments": [
                a["assignment_id"] for a in roster.assignments(
                    specialist_id=specialist_id, project_id=project_id)]})
    open_assignments = roster.assignments(statuses=OPEN_STATES,
                                          project_id=project_id)
    return {
        "project_id": project_id, "objective_id": objective_id,
        "title": project["title"], "solution": project["solution"],
        "status": project["status"], "opened_by": project["opened_by"],
        "opened_by_proposal": project["opened_by_proposal"],
        "opened_utc": project["opened_utc"],
        "closed_utc": project["closed_utc"],
        "closed_by": project["closed_by"],
        "workstreams": workstreams,
        "team": team,
        "active_team": [m["specialist_id"] for m in team
                        if m["participation"] == "ACTIVE"],
        "open_assignments": [a["assignment_id"] for a in open_assignments],
    }


def project_views(plan, ro, objectives, roster=None, store=None):
    """The Engineer's project state, per active objective
    (PlanningView.projects): the current project — or none yet — and the
    workstreams that wait for a staffing decision."""
    views = {}
    for objective in objectives:
        objective_id = objective["objective_id"]
        project = plan.active_project(objective_id)
        if project is None:
            views[objective_id] = {"project_id": None, "workstreams": [],
                                   "team": [], "needs_staffing": []}
            continue
        state = project_state(plan, ro, project["project_id"], roster,
                              store)
        state["needs_staffing"] = staffing_needs(plan, ro, objective_id,
                                                 roster)
        views[objective_id] = state
    return views


def projects_report(plan, ro, store=None):
    """Every project on record with its team: the TEMPORARY side of the
    organization, beside ``taskforce_report``'s persistent one."""
    return [project_state(plan, ro, p["project_id"], store=store)
            for p in plan.projects()]


QUERIES = ("objective", "project", "projects", "sequence", "attempt",
           "specialist", "capability", "seat", "subsystem", "finding",
           "dependencies", "verification-outcome", "accepted", "graph",
           "why-selected", "why-retained", "why-created", "taskforce")
