"""Assembles one coherent ActivitySnapshot from all readers.

Pure function: readers in, frozen snapshot out. No I/O of its own.
"""

import json
from datetime import datetime, timezone

from .views import (
    ActivitySnapshot, AuthorityCensus, ObjectiveView,
    CoordinatorDecisionView, RunView, NodeView, EdgeView,
    SequenceView, AttemptView, AssignmentView, SeatView,
    TaskforceView, RoutingView, ActiveSessionView,
)
from .events import events_from_transitions


def _parse_features(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def build_snapshot(intelligence, authority, step_reader=None,
                   config_reader=None, autoswarm_reader=None):
    """Build an ActivitySnapshot from reader instances.

    ``intelligence``: IntelligenceReader (intelligence.db)
    ``authority``: AuthorityReader (autobot.db)
    ``step_reader``: optional StepLogReader for real-time events
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- objectives ---
    objectives = [
        ObjectiveView(
            objective_id=o["objective_id"],
            text=o.get("text", ""),
            status=o.get("status", "?"),
            created_utc=o.get("created_utc", ""),
            completed_utc=o.get("completed_utc"))
        for o in intelligence.objectives()]

    # --- coordinator decisions ---
    coord_decisions = [
        CoordinatorDecisionView(
            decision_id=cd["decision_id"],
            objective_id=cd["objective_id"],
            elevation_mode=cd["elevation_mode"],
            features=_parse_features(cd.get("features")),
            route=cd.get("route"))
        for cd in intelligence.coordinator_decisions()]

    # --- authority bindings index ---
    bindings_by_node = {}
    for ab in intelligence.authority_bindings():
        bindings_by_node[ab["node_id"]] = ab

    # --- node outputs index ---
    outputs_by_node = {}
    for out in intelligence.node_outputs():
        outputs_by_node.setdefault(out["node_id"], []).append(out)

    # --- assignment index (sequence → provider/model) ---
    assignments_idx = intelligence.assignments_by_sequence()

    # --- runs with nodes and edges ---
    runs = []
    for run in intelligence.orchestration_runs():
        rid = run["orchestration_id"]
        raw_nodes = intelligence.orchestration_nodes(rid)
        raw_edges = intelligence.orchestration_edges(rid)

        nodes = []
        for n in raw_nodes:
            nid = n["node_id"]
            ab = bindings_by_node.get(nid, {})
            outs = outputs_by_node.get(nid, [])
            latest_out = outs[-1] if outs else {}
            seq_id = ab.get("sequence_id")
            asgn = assignments_idx.get(seq_id, {}) if seq_id else {}
            nodes.append(NodeView(
                node_id=nid,
                orchestration_id=rid,
                node_key=n.get("node_key", "?"),
                status=n.get("status", "?"),
                realization_kind=n.get("realization_kind"),
                work_profile=n.get("work_profile"),
                capability=n.get("capability"),
                sequence_id=seq_id,
                record_hash=ab.get("record_hash"),
                binding_status=ab.get("status"),
                output_kind=latest_out.get("output_kind"),
                output_sha256=latest_out.get("content_sha256"),
                provider=asgn.get("provider"),
                model=asgn.get("model"),
                specialist_handle=asgn.get("specialist_handle")))

        edges = [
            EdgeView(
                edge_id=e["edge_id"],
                source_node_id=e["source_node_id"],
                target_node_id=e["target_node_id"],
                dependency_kind=e.get("dependency_kind", "?"),
                status=e.get("status", "?"),
                acceptance_sha256=e.get("acceptance_receipt_sha256"))
            for e in raw_edges]

        runs.append(RunView(
            orchestration_id=rid,
            objective_id=run["objective_id"],
            mode=run.get("mode", "?"),
            status=run.get("status", "?"),
            nodes=nodes,
            edges=edges))

    # --- authority census ---
    auth_census = AuthorityCensus(
        sequences_by_state=authority.sequence_census(),
        attempts_by_state=authority.attempt_census(),
        cas_object_count=authority.cas_object_count(),
        released_edge_count=authority.released_edge_count())

    # --- taskforce ---
    raw_seats = intelligence.taskforce_seats()
    seats = [
        SeatView(
            seat_id=s["seat_id"],
            specialist_id=s["specialist_id"],
            specialist_handle=s.get("specialist_handle", "?"),
            capability=s.get("capability", "?"),
            role=s.get("role", "?"),
            status=s.get("status", "?"),
            qualifying_count=s.get("qualifying", 0))
        for s in raw_seats]

    taskforce = TaskforceView(
        member_count=intelligence.taskforce_member_count(),
        seat_count=len(seats),
        established_count=sum(1 for s in seats
                              if s.status == "ESTABLISHED"),
        seats=seats)

    # --- recent assignments ---
    raw_assignments = intelligence.recent_assignments()
    recent_assignments = [
        AssignmentView(
            assignment_id=a["assignment_id"],
            specialist_id=a["specialist_id"],
            specialist_handle=a.get("specialist_handle", "?"),
            sequence_id=a["sequence_id"],
            capability=a.get("capability", "?"),
            role=a.get("role", "?"),
            status=a.get("status", "?"),
            selection_path=a.get("selection_path", "?"),
            provider=a.get("provider"),
            model=a.get("model"))
        for a in raw_assignments]

    # --- activity events ---
    transitions = authority.recent_transitions(limit=20)
    events = events_from_transitions(transitions)

    if step_reader:
        from .events import events_from_step
        for entry in step_reader.new_entries():
            events.extend(events_from_step(entry))

    events.sort(key=lambda e: e.timestamp or "", reverse=True)
    events = events[:30]

    # --- active sequences and attempts ---
    active_sequences = [
        SequenceView(
            sequence_id=s["sequence_id"],
            state=s["state"],
            record_hash=s.get("record_hash"),
            contract_digest=s.get("contract_digest"))
        for s in authority.active_sequences()]

    active_attempts = [
        AttemptView(
            attempt_id=a["attempt_id"],
            sequence_id=a["sequence_id"],
            state=a["state"])
        for a in authority.running_attempts()]

    # --- active AutoSwarm sessions ---
    active_sessions = []
    if autoswarm_reader:
        for s in autoswarm_reader.active_sessions():
            active_sessions.append(ActiveSessionView(
                call_id=s.get("call_id", "?"),
                native_session_id=s.get("native_session_id"),
                provider=s.get("provider"),
                model=s.get("model"),
                status="RUNNING",
                started_utc=s.get("started_utc"),
                workspace=s.get("workspace")))

    # --- routing ---
    routing = RoutingView()
    if config_reader:
        routes_dict = config_reader.routes()
        routing = RoutingView(
            default_route=config_reader.default_route(),
            escalation_route=config_reader.escalation_route(),
            planner_model=config_reader.planner_model(),
            routes=routes_dict)

    return ActivitySnapshot(
        captured_utc=now,
        objectives=objectives,
        coordinator_decisions=coord_decisions,
        runs=runs,
        authority=auth_census,
        active_sequences=active_sequences,
        active_attempts=active_attempts,
        taskforce=taskforce,
        recent_assignments=recent_assignments,
        active_sessions=active_sessions,
        routing=routing,
        recent_events=events)
