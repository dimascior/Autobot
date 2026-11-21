"""Extract ActivityEvents from journal transitions and step reports."""

from .views import ActivityEvent


def events_from_transitions(transitions):
    """Convert authority journal transitions into ActivityEvents."""
    events = []
    for t in transitions:
        machine = t.get("machine", "")
        subject = t.get("subject_id", "?")
        from_s = t.get("from_state", "?")
        to_s = t.get("to_state", "?")
        ts = t.get("recorded_utc", "?")
        rule = t.get("rule", "")

        if machine == "sequence":
            rule_tag = " [%s]" % rule if rule else ""
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind="transition", subject_id=subject,
                summary="%s %s -> %s%s" % (
                    subject[:16], from_s, to_s, rule_tag),
                details=dict(t)))
        elif machine == "attempt":
            kind = "verified" if to_s == "VERIFIED" else "transition"
            events.append(ActivityEvent(
                timestamp=ts, layer="authority",
                kind=kind, subject_id=subject,
                summary="%s %s -> %s" % (subject[:16], from_s, to_s),
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
