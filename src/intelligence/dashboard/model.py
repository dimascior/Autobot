"""Assembles one coherent ActivitySnapshot from all readers.

Pure function: readers in, frozen snapshot out. No I/O of its own.

Derived fields (an objective's work state, a run's effective state, a
seat's current work) are computed here from recorded rows only: project
workstreams, the sequences their proposals opened, and the attempts the
authority plane still holds in flight.
"""

import dataclasses
import json
from datetime import datetime, timezone

from .views import (
    ActivitySnapshot, AuthorityCensus, ObjectiveView,
    CoordinatorDecisionView, RunView, NodeView, EdgeView,
    SequenceView, AttemptView, AssignmentView, SeatView,
    TaskforceView, RoutingView, ActiveSessionView, SessionView,
    LivenessView, CycleView, ProposalView, StaffingView, EngineerView,
    EdgeRefView, WorkItemView, ProjectView, TrustView, AttentionItem,
)
from .events import (
    events_from_transitions, events_from_workforce, events_from_cycles,
    events_from_admissions, events_from_approvals, planning_note,
)

try:
    from autobot.models import SEQUENCE_TERMINAL
except ImportError:  # render without the authority package
    SEQUENCE_TERMINAL = frozenset((
        "COMPLETED", "SUPERSEDED", "ABANDONED", "DELEGATION_REVOKED",
        "PUBLISHED_UNVERIFIED"))

_QUEUED_STATES = frozenset(("DECLARED", "READY"))
_BLOCKED_STATES = frozenset(("BLOCKED", "BLOCKED_UNSUPPORTED_FINALIZER"))
_COMPLETE_OBJECTIVE = frozenset(("COMPLETE", "COMPLETED"))


def _parse_features(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _is_halted(state):
    return bool(state) and state.startswith("HALTED")


def _call(reader, name, default, *args):
    """Call an optional reader method; readers built for older stores, or
    test doubles, may not have it."""
    method = getattr(reader, name, None) if reader is not None else None
    if method is None:
        return default
    return method(*args)


def _parse_utc(ts):
    if not ts or not isinstance(ts, str):
        return None
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# The Coordinator's own keyword sets, so a feature's source is derived by
# the same substring rule that produced it (O3) rather than re-typed here.
try:
    from intelligence.coordinator import ProductionCoordinatorPolicy as _Policy
    _FEATURE_WORDS = {
        "research_required": _Policy.RESEARCH_KEYWORDS,
        "engineering_design": _Policy.ENGINEER_KEYWORDS,
        "multi_domain": _Policy.MULTI_DOMAIN_KEYWORDS,
        "organizational_complexity": _Policy.COMPLEXITY_KEYWORDS,
        "external_effects": frozenset((
            "deploy", "operate", "effect", "filesystem", "publish")),
    }
except ImportError:  # render without the policy module
    _FEATURE_WORDS = {}

# Where a feature can come from when no keyword matched (coordinator.py).
_FEATURE_CONTEXT = {
    "multi_domain": "context: two domains",
    "organizational_complexity": "context: more than 2 active runs",
    "deep_dependencies": "context: more than 3 constraints",
}


def _feature_sources(text, features):
    """Derived: for each recorded feature, the policy keywords found in the
    objective text (substring match, as the policy does), else the
    context the policy also reads. Never stored, always labelled."""
    if not _FEATURE_WORDS:
        return {}
    lowered = (text or "").lower()
    out = {}
    for name, on in (features or {}).items():
        if not on:
            continue
        words = sorted(w for w in _FEATURE_WORDS.get(name, ()) if w in lowered)
        if words:
            shown = ", ".join('"%s"' % w for w in words[:3])
            if len(words) > 3:
                shown += " +%d" % (len(words) - 3)
            out[name] = shown
        else:
            out[name] = _FEATURE_CONTEXT.get(name, "no word in the text")
    return out


def _objective_work(objective_id, projects_by_objective, workstreams_by_project,
                    sequence_states, in_flight_by_sequence):
    """(state, note, since_utc) for an objective's work, from its project
    workstreams' sequences: HALTED beats working beats blocked beats
    queued."""
    items = []
    for project in projects_by_objective.get(objective_id, []):
        for ws in workstreams_by_project.get(project["project_id"], []):
            sid = ws.get("sequence_id")
            if sid and sid in sequence_states:
                items.append((ws.get("workstream_key") or sid, sid,
                              sequence_states[sid]))
    if not items:
        return "no work", None, None

    halted = [i for i in items if _is_halted(i[2].get("state"))]
    if halted:
        key, _, info = min(halted, key=lambda i: i[2].get("updated_utc") or "")
        return "HALTED", key, info.get("updated_utc")

    running = [i for i in items if i[1] in in_flight_by_sequence]
    busy = [i for i in items
            if i[2].get("state") not in SEQUENCE_TERMINAL
            and i[2].get("state") not in _QUEUED_STATES
            and i[2].get("state") not in _BLOCKED_STATES]
    if running or busy:
        keys = []
        for i in running + busy:
            if i[0] not in keys:
                keys.append(i[0])
        return "working", ", ".join(keys), None

    blocked = [i for i in items if i[2].get("state") in _BLOCKED_STATES]
    if blocked:
        return "blocked", ", ".join(i[0] for i in blocked), min(
            i[2].get("updated_utc") or "" for i in blocked) or None

    queued = [i for i in items if i[2].get("state") in _QUEUED_STATES]
    if queued:
        return "queued", ", ".join(i[0] for i in queued), None

    return "work finished", None, max(
        i[2].get("updated_utc") or "" for i in items) or None


def _route_name(provider, model, routes):
    """The configured route ([routing.routes.*]) a provider and model
    resolve to, or None."""
    if not provider or not model:
        return None
    for name, spec in sorted((routes or {}).items()):
        if spec.get("agent") == provider and spec.get("model") == model:
            return name
    return None


def _spec_route_name(raw):
    """The route name a specialist row records (specialists.route)."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None
    return value.get("route_name") if isinstance(value, dict) else None


def _earlier_records(rows, current, routes):
    """A specialist's demonstrations made on another provider/model than
    the one it runs on now: [label, qualifying, not qualifying, last]."""
    out = {}
    for d in rows:
        if not d.get("model") or (d.get("provider"), d.get("model")) == current:
            continue
        label = _route_name(d.get("provider"), d.get("model"), routes) \
            or d.get("model")
        entry = out.setdefault(label, [label, 0, 0, None])
        if d.get("outcome") == "QUALIFYING":
            entry[1] += d.get("n") or 0
        else:
            entry[2] += d.get("n") or 0
        if d.get("last_utc") and (entry[3] is None
                                  or d["last_utc"] > entry[3]):
            entry[3] = d["last_utc"]
    return sorted(out.values(), key=lambda e: e[0])


def _transition_sequence(t, attempt_sequences):
    """The sequence a journal transition belongs to, when it says: the
    subject of a sequence transition, the sequence of an attempt, or the
    sequence named in an admitted record's facts."""
    machine = t.get("machine")
    if machine == "sequence":
        return t.get("subject_id")
    facts = t.get("facts") or {}
    if facts.get("sequence_id"):
        return facts["sequence_id"]
    if machine == "attempt":
        return attempt_sequences.get(t.get("subject_id"))
    if facts.get("attempt_id"):
        return attempt_sequences.get(facts["attempt_id"])
    return None


def _run_effective(objective, work):
    """What a run's objective and work actually are, whatever the run's
    recorded status says."""
    if objective is None:
        return "unknown", "objective not found", None
    if objective.get("status") in _COMPLETE_OBJECTIVE:
        return "done", "objective COMPLETE", objective.get("completed_utc")
    state, note, since = work
    if state == "HALTED":
        return "stuck", "work HALTED: %s" % note, since
    if state == "working":
        return "working", "%s in progress" % note, since
    if state in ("blocked", "queued"):
        return "waiting", "%s: %s" % (state, note), since
    return "waiting", state, since


def build_snapshot(intelligence, authority, step_reader=None,
                   config_reader=None, autoswarm_reader=None,
                   cas_reader=None, now=None):
    """Build an ActivitySnapshot from reader instances.

    ``intelligence``: IntelligenceReader (intelligence.db)
    ``authority``: AuthorityReader (autobot.db)
    ``step_reader``: optional StepLogReader for real-time events
    ``autoswarm_reader``: optional AutoSwarmReader for call directories
    ``cas_reader``: optional CASReader, for the frozen records'
    predecessor edges and the Engineer's stored decisions
    ``now``: the capture instant (ISO UTC); the derived rules judge ages
    and expiries against it. Defaults to the current time.
    """
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- indices shared by the derived fields ---
    sequence_states = _call(authority, "sequence_states", {})
    in_flight = _call(authority, "in_flight_attempts", [])
    in_flight_by_sequence = {a["sequence_id"]: a for a in in_flight}
    projects = _call(intelligence, "projects", [])
    projects_by_objective = {}
    for p in projects:
        projects_by_objective.setdefault(p["objective_id"], []).append(p)
    workstreams = _call(intelligence, "workstreams", [])
    workstreams_by_project = {}
    workstream_key_by_sequence = {}
    for ws in workstreams:
        workstreams_by_project.setdefault(ws["project_id"], []).append(ws)
        if ws.get("sequence_id"):
            workstream_key_by_sequence[ws["sequence_id"]] = ws.get(
                "workstream_key")
    halted_projects = set()
    for pid, items in workstreams_by_project.items():
        if any(_is_halted(sequence_states.get(ws.get("sequence_id"), {})
                          .get("state")) for ws in items):
            halted_projects.add(pid)

    # --- objectives ---
    objectives = []
    objective_rows = {}
    work_by_objective = {}
    for o in intelligence.objectives():
        oid = o["objective_id"]
        objective_rows[oid] = o
        work = (None, None, None)
        if o.get("status") not in _COMPLETE_OBJECTIVE:
            work = _objective_work(oid, projects_by_objective,
                                   workstreams_by_project, sequence_states,
                                   in_flight_by_sequence)
        work_by_objective[oid] = work
        objectives.append(ObjectiveView(
            objective_id=oid,
            text=o.get("text", ""),
            status=o.get("status", "?"),
            created_utc=o.get("created_utc", ""),
            completed_utc=o.get("completed_utc"),
            work_state=work[0],
            work_note=work[1],
            work_since_utc=work[2]))

    # --- coordinator decisions ---
    coord_decisions = []
    for cd in intelligence.coordinator_decisions():
        features = _parse_features(cd.get("features"))
        text = (objective_rows.get(cd["objective_id"]) or {}).get("text")
        coord_decisions.append(CoordinatorDecisionView(
            decision_id=cd["decision_id"],
            objective_id=cd["objective_id"],
            elevation_mode=cd["elevation_mode"],
            features=features,
            route=cd.get("route"),
            created_utc=cd.get("created_utc"),
            policy_id=cd.get("policy_id"),
            feature_sources=_feature_sources(text, features)))

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

        objective = objective_rows.get(run["objective_id"])
        work = work_by_objective.get(run["objective_id"],
                                     ("no work", None, None))
        effective = _run_effective(objective, work)
        runs.append(RunView(
            orchestration_id=rid,
            objective_id=run["objective_id"],
            mode=run.get("mode", "?"),
            status=run.get("status", "?"),
            nodes=nodes,
            edges=edges,
            created_utc=run.get("created_utc"),
            objective_status=objective.get("status") if objective else None,
            effective_state=effective[0],
            effective_note=effective[1],
            effective_since_utc=effective[2]))

    # --- authority census ---
    auth_census = AuthorityCensus(
        sequences_by_state=authority.sequence_census(),
        attempts_by_state=authority.attempt_census(),
        cas_object_count=authority.cas_object_count(),
        released_edge_count=authority.released_edge_count())

    # --- taskforce ---
    work_by_seat = {}
    for w in _call(intelligence, "seat_work", []):
        work_by_seat.setdefault(w["seat_id"], []).append(
            w.get("workstream_key") or w.get("sequence_id"))
    teams_by_specialist = {}
    for t in _call(intelligence, "active_project_teams", []):
        teams_by_specialist.setdefault(t["specialist_id"], set()).add(
            t["project_id"])

    # a handle is a name, not a model: what a seat runs on is the route
    # its holder's sessions use (execution state, config [routing])
    routes_dict = config_reader.routes() if config_reader else {}
    last_routes = _call(intelligence, "last_assignment_routes", {})
    demos_by_model = {}
    for d in _call(intelligence, "demonstrations_by_model", []):
        demos_by_model.setdefault(d["specialist_id"], []).append(d)

    seats = []
    for s in intelligence.taskforce_seats():
        last = last_routes.get(s["specialist_id"]) or {}
        route_name = (last.get("route_name")
                      or _route_name(last.get("provider"), last.get("model"),
                                     routes_dict)
                      or _spec_route_name(s.get("spec_route"))
                      or _route_name(s.get("spec_provider"),
                                     s.get("spec_model"), routes_dict))
        current_pm = (last.get("provider") or s.get("spec_provider"),
                      last.get("model") or s.get("spec_model"))
        seat_q = s.get("seat_q")
        if seat_q is None:  # older store: only the capability-wide count
            qualifying = s.get("qualifying", 0)
        else:
            qualifying = (s.get("seat_q") or 0) + (s.get("other_q") or 0)
        current = work_by_seat.get(s["seat_id"])
        seats.append(SeatView(
            seat_id=s["seat_id"],
            specialist_id=s["specialist_id"],
            specialist_handle=s.get("specialist_handle", "?"),
            capability=s.get("capability", "?"),
            role=s.get("role", "?"),
            status=s.get("status", "?"),
            qualifying_count=qualifying,
            seat_qualifying=s.get("seat_q") or 0,
            seat_not_qualifying=s.get("seat_nq") or 0,
            other_qualifying=s.get("other_q") or 0,
            other_not_qualifying=s.get("other_nq") or 0,
            since_utc=s.get("since_utc") or s.get("retained_since_utc"),
            provider=current_pm[0],
            model=current_pm[1],
            current_work=", ".join(current) if current else None,
            stuck_projects=len(teams_by_specialist.get(
                s["specialist_id"], set()) & halted_projects),
            route_name=route_name,
            earlier_records=_earlier_records(
                demos_by_model.get(s["specialist_id"], []), current_pm,
                routes_dict)))

    specialists = _call(intelligence, "specialists", [])
    taskforce = TaskforceView(
        member_count=intelligence.taskforce_member_count(),
        seat_count=len(seats),
        established_count=sum(1 for s in seats
                              if s.status == "ESTABLISHED"),
        retained_count=sum(1 for s in seats if s.status == "RETAINED"),
        outside_pool=[sp["handle"] for sp in specialists
                      if not sp.get("member")
                      and sp.get("status", "ACTIVE") == "ACTIVE"],
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
            model=a.get("model"),
            outcome=a.get("outcome"),
            workstream_key=a.get("workstream_key")
            or workstream_key_by_sequence.get(a["sequence_id"]),
            selected_utc=a.get("selected_utc"),
            settled_utc=a.get("settled_utc"))
        for a in raw_assignments]

    # --- activity: one stream over every layer, journal rows named by the
    # work they belong to and annotated by their planning events ---
    transitions = authority.recent_transitions(limit=20)
    attempt_sequences = _call(authority, "attempt_sequences", {})
    events = events_from_transitions(
        transitions,
        name_of=lambda t: workstream_key_by_sequence.get(
            _transition_sequence(t, attempt_sequences)))
    planning = _call(intelligence, "planning_events_for", {},
                     [t.get("seq") for t in transitions])
    annotated = []
    for ev in events:
        row = planning.get(ev.details.get("seq"))
        note = planning_note(row) if row else None
        annotated.append(dataclasses.replace(
            ev, summary="%s · %s" % (ev.summary, note)) if note else ev)
    events = annotated
    assignment_names = {}
    for a in _call(intelligence, "recent_assignments", [], 40):
        assignment_names[a["assignment_id"]] = (
            a.get("specialist_handle"),
            a.get("workstream_key")
            or workstream_key_by_sequence.get(a.get("sequence_id")))
    cycle_rows = _call(intelligence, "cycles_recent", [], 3)
    events.extend(events_from_workforce(
        _call(intelligence, "workforce_events_recent", [], 10),
        assignment_names))
    events.extend(events_from_cycles(cycle_rows))
    events.extend(events_from_admissions(coord_decisions[-5:]))
    events.extend(events_from_approvals(
        _call(intelligence, "approvals_recent", [], 5)))

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

    # --- sessions: attempts the authority plane holds in flight ---
    by_attempt = _call(intelligence, "assignments_by_attempt", {})
    sessions = []
    for a in in_flight:
        asg = by_attempt.get(a["attempt_id"], {})
        files = {}
        if autoswarm_reader is not None and a.get("call_id"):
            files = autoswarm_reader.call_files(a["call_id"]) or {}
        provider = (files.get("provider") or asg.get("provider")
                    or a.get("provider"))
        model = files.get("model") or asg.get("model")
        sessions.append(SessionView(
            attempt_id=a["attempt_id"],
            sequence_id=a["sequence_id"],
            attempt_state=a["state"],
            attempt_since_utc=a.get("updated_utc"),
            call_id=a.get("call_id"),
            provider=provider,
            model=model,
            route_name=_route_name(provider, model, routes_dict),
            role=asg.get("role") or a.get("role"),
            capability=asg.get("capability"),
            specialist_handle=asg.get("specialist_handle"),
            selection_path=asg.get("selection_path"),
            workstream_key=asg.get("workstream_key")
            or workstream_key_by_sequence.get(a["sequence_id"]),
            objective_id=asg.get("objective_id"),
            pid=files.get("pid"),
            started_utc=files.get("started_utc"),
            phase=files.get("phase"),
            finalization_class=files.get("finalization_class"),
            output_lines=files.get("output_lines") or 0,
            last_line=files.get("last_line"),
            output_utc=files.get("output_utc")))

    # --- active AutoSwarm sessions (call directories) ---
    active_sessions = []
    if autoswarm_reader:
        for s in autoswarm_reader.active_sessions():
            active_sessions.append(ActiveSessionView(
                call_id=s.get("call_id", "?"),
                sequence_id=s.get("sequence_id"),
                native_session_id=s.get("native_session_id"),
                provider=s.get("provider"),
                model=s.get("model"),
                status="RUNNING",
                started_utc=s.get("started_utc"),
                workspace=s.get("workspace"),
                stdout_lines=s.get("stdout_lines", 0),
                stdout_tail=s.get("stdout_tail", [])))

    # --- liveness: heartbeats of the driver, the sequencer, the journal ---
    lease = _call(intelligence, "driver_lease", None) or {}
    instance = _call(authority, "instance_lease", None) or {}
    head = _call(authority, "journal_head", None) or {}
    liveness = LivenessView(
        driver_holder=lease.get("holder"),
        driver_step_utc=lease.get("acquired_utc"),
        driver_expires_utc=lease.get("expires_utc"),
        sequencer_holder=instance.get("holder"),
        sequencer_epoch=instance.get("epoch"),
        sequencer_expires_utc=instance.get("expires_utc"),
        journal_seq=head.get("seq"),
        journal_utc=head.get("appended_utc"))

    # --- the Engineer (Project Manager) ---
    staffing = [StaffingView(
        decision_id=s["decision_id"], sequence_id=s["sequence_id"],
        mode=s.get("mode") or "?", objective_id=s.get("objective_id"),
        project_id=s.get("project_id"),
        attempt_ordinal=s.get("attempt_ordinal"),
        specialist_handle=s.get("specialist_handle"),
        decided_utc=s.get("decided_utc"),
        workstream_key=workstream_key_by_sequence.get(s["sequence_id"]))
        for s in _call(intelligence, "open_staffing", [])]
    decisions = _call(intelligence, "engineering_decisions", [])
    stored = 0
    for d in decisions:
        sha = d.get("content_sha256")
        if sha and cas_reader is not None and cas_reader.exists(
                sha if sha.startswith("sha256:") else "sha256:" + sha):
            stored += 1
    engineer = EngineerView(
        cycles=[CycleView(
            cycle_id=c["cycle_id"], started_utc=c.get("started_utc") or "",
            status=c.get("status") or "?", closed_utc=c.get("closed_utc"),
            proposals=c.get("proposals") or 0, note=c.get("note"))
            for c in cycle_rows],
        waiting=[ProposalView(
            proposal_id=p["proposal_id"], proposal_class=p.get("class") or "?",
            status=p.get("status") or "?",
            objective_id=p.get("objective_id"),
            rationale=p.get("rationale") or "",
            sequence_id=p.get("sequence_id"),
            created_utc=p.get("created_utc"),
            workstream_key=workstream_key_by_sequence.get(
                p.get("sequence_id")))
            for p in _call(intelligence, "open_proposals", [])],
        staffing_open=staffing,
        decisions_total=len(decisions),
        decisions_stored=stored)

    # --- the work graph: each project's workstreams with their sequence,
    # holder, frozen predecessor edges and, when halted, why ---
    identity = {}
    record_address = {}
    for s in _call(authority, "sequence_index", []):
        sid = s["sequence_id"]
        for kind, key in (("RECORD", "record_hash"),
                          ("CONTRACT", "contract_digest"),
                          ("REQUIREMENT", "requirement_digest")):
            if s.get(key):
                identity.setdefault((kind, s[key]), sid)
        record_address[sid] = s.get("content_address")
    released = {}
    for e in _call(authority, "released_edges", []):
        released[(e["edge_kind"], e["edge_value"])] = e["released_utc"]
        released[(e["predecessor_sequence_id"], e["edge_kind"])] = (
            e["released_utc"])
    staffing_by_sequence = {s.sequence_id: s for s in staffing}
    objective_status = {o.objective_id: o.status for o in objectives}

    project_views = []
    for p in projects:
        live = (p.get("status") == "ACTIVE"
                or objective_status.get(p["objective_id"])
                not in _COMPLETE_OBJECTIVE)
        items = []
        for ws in workstreams_by_project.get(p["project_id"], []):
            sid = ws.get("sequence_id")
            st = sequence_states.get(sid, {}) if sid else {}
            edges = []
            record = (cas_reader.read_json(record_address.get(sid))
                      if live and cas_reader is not None and sid else None)
            for e in (record or {}).get("predecessors") or []:
                if not isinstance(e, dict):
                    continue
                kind = e.get("edge_kind") or "RECORD"
                target = e.get("target")
                pred = identity.get((kind, target)) or identity.get(
                    ("RECORD", target))
                edges.append(EdgeRefView(
                    edge_id=e.get("edge_id") or "?", edge_kind=kind,
                    predecessor_sequence_id=pred,
                    predecessor_key=workstream_key_by_sequence.get(pred),
                    predecessor_state=sequence_states.get(pred, {}).get(
                        "state"),
                    released_utc=released.get((kind, target))
                    or released.get((pred, kind))))
            halt = None
            if live and sid and _is_halted(st.get("state")):
                halt = _call(authority, "halt_facts", None, sid,
                             st.get("updated_utc"))
            a = assignments_idx.get(sid, {}) if sid else {}
            flight = in_flight_by_sequence.get(sid) if sid else None
            nxt = staffing_by_sequence.get(sid)
            if nxt is None:
                next_staffing = None
            elif nxt.specialist_handle:
                next_staffing = "%s (%s)" % (
                    nxt.specialist_handle, nxt.mode.replace("_", " "))
            else:
                next_staffing = "%s attempt %s" % (
                    nxt.mode.replace("_", " "), nxt.attempt_ordinal or "?")
            items.append(WorkItemView(
                workstream_key=ws.get("workstream_key") or sid or "?",
                sequence_id=sid, proposal_class=ws.get("proposal_class"),
                state=st.get("state"), since_utc=st.get("updated_utc"),
                holder=a.get("specialist_handle"),
                selection_path=a.get("selection_path"),
                assignment_status=a.get("status"),
                assignment_outcome=a.get("outcome"),
                attempt_state=flight["state"] if flight else None,
                call_id=(flight or {}).get("call_id") or a.get("call_id"),
                predecessors=edges,
                halt_rule=(halt or {}).get("rule"),
                halt_reason=(halt or {}).get("reason"),
                halt_completeness=(halt or {}).get("completeness"),
                next_staffing=next_staffing))
        project_views.append(ProjectView(
            project_id=p["project_id"], objective_id=p["objective_id"],
            title=p.get("title") or "", status=p.get("status") or "?",
            opened_utc=p.get("opened_utc"), closed_utc=p.get("closed_utc"),
            items=items))

    # --- trust ---
    trust = TrustView(
        receipts_by_completeness=_call(authority, "receipt_census", {}),
        verifications_by_outcome=_call(authority, "verification_census", {}),
        effects_by_state=_call(authority, "effect_census", {}),
        witnesses=_call(authority, "witness_count", 0),
        approvals_by_decision=_call(intelligence, "approval_census", {}),
        demonstrations_by_outcome=_call(
            intelligence, "demonstration_census", {}),
        findings_by_significance=_call(intelligence, "findings_census", {}))

    # --- attention: derived flags, each naming its rule ---
    attention = []
    halted_seen = set()
    for pv in project_views:
        for it in pv.items:
            if _is_halted(it.state):
                halted_seen.add(it.sequence_id)
                attention.append(AttentionItem(
                    rule="A1", severity="critical", subject=it.workstream_key,
                    detail=" · ".join(x for x in (
                        it.halt_rule, it.halt_reason) if x) or "halted",
                    since_utc=it.since_utc, whose_move="operator"))
            elif it.state in _BLOCKED_STATES:
                open_edges = [e for e in it.predecessors
                              if not e.released_utc]
                if open_edges and all(_is_halted(e.predecessor_state)
                                      for e in open_edges):
                    attention.append(AttentionItem(
                        rule="A5", severity="warning",
                        subject=it.workstream_key,
                        detail="waits on %s (HALTED)" % ", ".join(
                            e.predecessor_key or e.edge_id
                            for e in open_edges),
                        since_utc=it.since_utc,
                        whose_move="follows the halt"))
    for s in active_sequences:  # halted work outside any project
        if _is_halted(s.state) and s.sequence_id not in halted_seen:
            halted_seen.add(s.sequence_id)
            facts = _call(authority, "halt_facts", None, s.sequence_id,
                          sequence_states.get(s.sequence_id, {}).get(
                              "updated_utc")) or {}
            attention.append(AttentionItem(
                rule="A1", severity="critical",
                subject=workstream_key_by_sequence.get(s.sequence_id)
                or s.sequence_id,
                detail=" · ".join(x for x in (
                    facts.get("rule"), facts.get("reason")) if x) or "halted",
                since_utc=sequence_states.get(s.sequence_id, {}).get(
                    "updated_utc"), whose_move="operator"))
    for f in _call(authority, "integrity_failures", []):
        sid = f.get("sequence_id")
        state = sequence_states.get(sid, {}).get("state")
        if sid in halted_seen or state in SEQUENCE_TERMINAL:
            continue
        attention.append(AttentionItem(
            rule="A2", severity="critical",
            subject=workstream_key_by_sequence.get(sid) or sid,
            detail="receipt INTEGRITY_FAILED",
            since_utc=f.get("admitted_utc"), whose_move="operator"))
    unknown = trust.effects_by_state.get("UNKNOWN_OUTCOME", 0)
    if unknown:
        attention.append(AttentionItem(
            rule="A3", severity="critical", subject="effects",
            detail="%d effect outcome%s unknown" % (
                unknown, "" if unknown == 1 else "s"),
            whose_move="operator"))
    expires = _parse_utc(liveness.driver_expires_utc)
    if (sessions or in_flight) and expires is not None and expires < (
            _parse_utc(now) or expires):
        attention.append(AttentionItem(
            rule="A4", severity="critical", subject="driver",
            detail="lease expired with work in flight",
            since_utc=liveness.driver_expires_utc, whose_move="operator"))
    for p in engineer.waiting:
        attention.append(AttentionItem(
            rule="A6", severity="warning",
            subject=p.workstream_key or p.objective_id or p.proposal_class,
            detail="%s · %s" % (p.proposal_class, p.rationale),
            since_utc=p.created_utc,
            whose_move="you" if p.status == "QUEUED_FOR_YOU"
            else "authority"))
    stale = sum(1 for r in runs if r.status == "ACTIVE"
                and r.effective_state in ("done", "stuck"))
    if stale:
        attention.append(AttentionItem(
            rule="A10", severity="info", subject="runs",
            detail="%d recorded ACTIVE run%s finished or stuck" % (
                stale, "" if stale == 1 else "s"), whose_move="code (O2)"))
    if decisions and cas_reader is not None and stored < len(decisions):
        attention.append(AttentionItem(
            rule="A11", severity="info", subject="engineer",
            detail="%d of %d decision texts not stored" % (
                len(decisions) - stored, len(decisions)),
            whose_move="code (O4)"))
    budgeted = _call(intelligence, "budgeted_objectives", None)
    if budgeted is not None:
        for o in objectives:
            if o.status not in _COMPLETE_OBJECTIVE \
                    and o.objective_id not in budgeted:
                attention.append(AttentionItem(
                    rule="A12", severity="info", subject=o.objective_id,
                    detail="no budget set", whose_move="operator"))
    active_ids = {o.objective_id for o in objectives
                  if o.status not in _COMPLETE_OBJECTIVE}
    for cd in coord_decisions:
        if cd.objective_id in active_ids:
            context = sorted(k for k, v in cd.feature_sources.items()
                             if not v.startswith('"'))
            if context:
                attention.append(AttentionItem(
                    rule="A13", severity="info", subject=cd.objective_id,
                    detail="%s with no word in the text" % ", ".join(
                        context), since_utc=cd.created_utc,
                    whose_move="code (O3)"))

    # --- routing ---
    routing = RoutingView()
    if config_reader:
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
        sessions=sessions,
        recent_events=events,
        liveness=liveness,
        engineer=engineer,
        projects=project_views,
        trust=trust,
        attention=attention)
