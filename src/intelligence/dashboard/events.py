"""Extract ActivityEvents from journal transitions, the organization's own
event streams (planning events, workforce events, Engineer cycles,
Coordinator admissions, permission answers) and step reports."""

import dataclasses
import json

from .views import ActivityEvent


def collapse_repeats(events):
    """Events in time order with each run of identical neighbours (a
    session's witness admissions, typically: half the journal) folded
    into its newest one, its summary ending in "×N"."""
    out = []
    for ev in events:
        if out and ev.summary == out[-1].summary \
                and ev.layer == out[-1].layer:
            prev = out[-1]
            details = dict(prev.details)
            details["repeat"] = details.get("repeat", 1) + 1
            out[-1] = dataclasses.replace(prev, details=details)
            continue
        out.append(ev)
    return [dataclasses.replace(e, summary="%s ×%d" % (
        e.summary, e.details["repeat"]))
        if e.details.get("repeat", 1) > 1 else e for e in out]


def _facts(raw):
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def planning_note(row):
    """What a planning event adds to the journal row it was derived from,
    or None when it adds nothing the transition does not already say."""
    kind = row.get("event_kind") or ""
    facts = _facts(row.get("facts"))
    if kind == "work_blocked":
        edges = facts.get("unsatisfied_edge_ids") or []
        return "blocked on %s" % ", ".join(edges) if edges else "blocked"
    if kind == "work_ready":
        edges = [e.get("edge_id") for e in facts.get("satisfied_edges") or []
                 if isinstance(e, dict) and e.get("edge_id")]
        return "ready: %s satisfied" % ", ".join(edges) if edges else None
    if kind == "dependencies_released":
        known = facts.get("known_successor_sequence_ids") or []
        return "released to %d successor%s" % (
            len(known), "" if len(known) == 1 else "s") if known else None
    if kind in ("sequence_completed", "sequence_accepted",
                "sequence_verified"):
        return None
    return kind.replace("_", " ") if kind else None


def events_from_workforce(rows, assignment_names=None,
                          specialist_names=None):
    """Taskforce events. ``assignment_names`` maps an assignment id to
    (specialist handle, workstream key) when known; ``specialist_names``
    maps a specialist id to its handle, for events about a specialist."""
    names = assignment_names or {}
    handles = specialist_names or {}
    events = []
    for r in rows:
        kind = r.get("event_kind") or "?"
        facts = _facts(r.get("facts"))
        cap_role = "%s/%s" % (facts.get("capability", "?"),
                              facts.get("role", "?"))
        if kind == "retained_seat_assigned":
            handle, key = names.get(r.get("subject_id"), (None, None))
            summary = ("seat assigned: %s -> %s" % (handle, key)
                       if handle and key else "seat assigned: %s" % cap_role)
        elif kind == "demonstration_recorded":
            summary = "demonstration %s %s" % (
                facts.get("outcome", "?"), cap_role)
        else:
            subject = r.get("subject_id") or ""
            summary = "%s %s" % (kind.replace("_", " "),
                                 handles.get(subject) or subject[:16])
        events.append(ActivityEvent(
            timestamp=r.get("recorded_utc") or "", layer="taskforce",
            kind=kind, subject_id=r.get("subject_id") or "",
            summary=summary, details={"facts": facts}))
    return events


def events_from_cycles(rows):
    """Engineer cycles: each one is a planning pass over new stimuli."""
    events = []
    for r in rows:
        n = r.get("proposals") or 0
        state = "open" if r.get("status") == "OPEN" else "ran"
        events.append(ActivityEvent(
            timestamp=r.get("started_utc") or "", layer="engineer",
            kind="cycle", subject_id=str(r.get("cycle_id")),
            summary="cycle %s %s · %d proposal%s" % (
                r.get("cycle_id"), state, n, "" if n == 1 else "s"),
            details=dict(r)))
    return events


def events_from_admissions(decisions):
    """Coordinator admissions (CoordinatorDecisionView rows)."""
    return [ActivityEvent(
        timestamp=d.created_utc or "", layer="coordinator", kind="admitted",
        subject_id=d.objective_id,
        summary="admitted %s -> %s" % (d.objective_id, d.elevation_mode),
        details={"decision_id": d.decision_id})
        for d in decisions if d.created_utc]


def events_from_approvals(rows):
    """The Taskforce's answers to a running specialist's permission
    requests."""
    events = []
    for r in rows:
        subject = (r.get("subject") or "").strip()
        summary = "permission %s · %s" % (r.get("decision", "?"),
                                          r.get("tool_name") or "?")
        if subject:
            summary += " " + subject[:48]
        events.append(ActivityEvent(
            timestamp=r.get("decided_utc") or "", layer="taskforce",
            kind="permission", subject_id=r.get("call_id") or "",
            summary=summary, details={"decision": r.get("decision")}))
    return events


def events_from_transitions(transitions, name_of=None):
    """Convert authority journal transitions into ActivityEvents.

    ``name_of(transition)`` may return the name of the work a transition
    belongs to (its workstream key); the summary then leads with that name
    instead of the record id. A transition with no ``from_state`` admits a
    new record (absent fields are omitted, never null: D-37), so it reads
    ``new -> STATE``.
    """
    events = []
    for t in transitions:
        machine = t.get("machine", "")
        subject = t.get("subject_id", "?")
        from_s = t.get("from_state")
        if from_s is None or from_s == "?":
            from_s = "new"
        to_s = t.get("to_state", "?")
        ts = t.get("recorded_utc", "?")
        rule = t.get("rule", "")
        name = name_of(t) if name_of is not None else None

        if machine == "sequence":
            rule_tag = " [%s]" % rule if rule else ""
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind="transition", subject_id=subject,
                summary="%s %s -> %s%s" % (
                    name or subject[:16], from_s, to_s, rule_tag),
                details=dict(t)))
        elif machine == "attempt":
            kind = "verified" if to_s == "VERIFIED" else "transition"
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind=kind, subject_id=subject,
                summary=("%s attempt %s -> %s" % (name, from_s, to_s)
                         if name else
                         "%s %s -> %s" % (subject[:16], from_s, to_s)),
                details=dict(t)))
        elif name and machine.startswith("attempt_"):
            # receipts, environments, verifications, witnesses, candidates
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind="transition", subject_id=subject,
                summary="%s %s %s -> %s" % (
                    name, machine[len("attempt_"):], from_s, to_s),
                details=dict(t)))
        elif machine == "acceptance":
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind="accepted", subject_id=subject,
                summary="accepted %s" % subject[:16],
                details=dict(t)))
        elif machine == "release":
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind="released", subject_id=subject,
                summary="released %s" % subject[:16],
                details=dict(t)))
        else:
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind="transition", subject_id=subject,
                summary="%s.%s %s -> %s" % (
                    machine, subject[:12], from_s, to_s),
                details=dict(t)))
    return events


def events_from_step(step_report):
    """Extract ActivityEvents from one driver step report."""
    events = []
    coord = step_report.get("coordinator", {})
    for admission in coord.get("admitted", []):
        events.append(ActivityEvent(
            timestamp="", layer="coordinator",
            kind="admitted",
            subject_id=admission.get("objective_id", "?"),
            summary="admitted %s -> %s" % (
                admission.get("objective_id", "?"),
                admission.get("mode", "?")),
            details=admission))

    orch = step_report.get("orchestration", {})
    for d in orch.get("decisions", []):
        events.append(ActivityEvent(
            timestamp="", layer="orchestrator",
            kind="decided",
            subject_id=d.get("class", "?"),
            summary="%s %s" % (
                d.get("class", "?"),
                d.get("node_key", d.get("node_id", ""))[:20]),
            details=d))

    for na in orch.get("admitted_nodes", []):
        events.append(ActivityEvent(
            timestamp="", layer="orchestrator",
            kind="node_admitted",
            subject_id=na.get("node_id", "?"),
            summary="node admitted %s" % na.get("node_id", "?")[:16],
            details=na))

    planning = step_report.get("planning", {})
    if planning.get("cycle_id") is not None:
        events.append(ActivityEvent(
            timestamp="", layer="engineer",
            kind="cycle",
            subject_id=str(planning.get("cycle_id", "")),
            summary="cycle %s" % planning.get("cycle_id", "?"),
            details=planning))

    sweep = step_report.get("sweep", {})
    for key in ("allocated", "adopted", "collected", "verified",
                "supervised", "settled"):
        items = sweep.get(key)
        if items:
            count = len(items) if isinstance(items, list) else items
            events.append(ActivityEvent(
                timestamp="", layer="taskforce",
                kind=key,
                subject_id=key,
                summary="%s: %s" % (key, count),
                details={key: items} if isinstance(items, list) else {}))

    for rel in step_report.get("edge_releases", []):
        events.append(ActivityEvent(
            timestamp="", layer="authority",
            kind="edge_released",
            subject_id=rel.get("edge_id", "?"),
            summary="edge %s released" % rel.get("edge_id", "?")[:16],
            details=rel))

    return events
