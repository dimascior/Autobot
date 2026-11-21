"""CLI streamer — compact live dashboard in the terminal.

Polls the snapshot every N seconds and prints a fixed-height display.
Works with any terminal; no dependencies beyond the stdlib.
"""

import os
import sys
import time

from .views import ActivitySnapshot

STATUS_SYMBOLS = {
    "ACTIVE": "+", "COMPLETE": "*", "COMPLETED": "*",
    "PENDING": ".", "EXECUTING": ">", "FAILED": "!",
    "HALTED": "#", "BLOCKED": "~", "READY": "-",
    "CANCELLED": "x", "SUPERSEDED": "^",
}

MODE_LABEL = {
    "DIRECT": "DIR", "ENGINEER_ELEVATED": "ENG",
    "ORCHESTRATOR_ELEVATED": "ORC",
}


def _sym(status):
    return STATUS_SYMBOLS.get(status, "?")


def _trunc(text, width):
    if not text:
        return ""
    return text[:width] if len(text) <= width else text[:width - 1] + "…"


def _bar(label, width=72):
    pad = width - len(label) - 4
    return "══ %s %s" % (label, "═" * max(pad, 2))


def render(snapshot, width=80):
    """Render an ActivitySnapshot as a list of text lines."""
    lines = []
    w = width - 2

    lines.append(_bar("AUTOBOT", w))
    lines.append("  %s" % snapshot.captured_utc)
    lines.append("")

    # --- objectives ---
    lines.append(_bar("OBJECTIVES", w))
    for obj in snapshot.objectives:
        sym = _sym(obj.status)
        lines.append("  %s %-36s %-9s %s" % (
            sym, obj.objective_id[:36], obj.status,
            _trunc(obj.text, w - 52)))
    if not snapshot.objectives:
        lines.append("  (none)")
    lines.append("")

    # --- coordinator decisions ---
    if snapshot.coordinator_decisions:
        lines.append(_bar("COORDINATOR", w))
        for cd in snapshot.coordinator_decisions:
            feats = ", ".join(sorted(cd.features)) if cd.features else "-"
            lines.append("  %-36s %-24s %s" % (
                cd.objective_id[:36], cd.elevation_mode,
                _trunc(feats, w - 64)))
        lines.append("")

    # --- runs and nodes ---
    lines.append(_bar("RUNS / NODES", w))
    for run in snapshot.runs:
        mode = MODE_LABEL.get(run.mode, run.mode[:3])
        lines.append("  %s %s %-5s %-8s  obj=%s" % (
            _sym(run.status), run.orchestration_id[:16],
            mode, run.status[:8], run.objective_id[:28]))
        for node in run.nodes:
            kind_label = (node.realization_kind or "?")[:12]
            bound = ""
            if node.sequence_id:
                bound = " seq=%s" % node.sequence_id[:12]
            if node.output_sha256:
                bound += " out=%s" % node.output_sha256[:12]
            lines.append("    %s %-20s %-8s %-12s%s" % (
                _sym(node.status), node.node_key[:20],
                node.status[:8], kind_label, bound))
        for edge in run.edges:
            lines.append("      %s -> %s  %s [%s]" % (
                edge.source_node_id[:12], edge.target_node_id[:12],
                edge.dependency_kind[:16], edge.status[:8]))
    if not snapshot.runs:
        lines.append("  (no orchestration runs)")
    lines.append("")

    # --- authority ---
    lines.append(_bar("AUTHORITY", w))
    census = snapshot.authority
    parts = ["%s:%d" % (s, n)
             for s, n in sorted(census.sequences_by_state.items())]
    lines.append("  sequences  %s" % "  ".join(parts))
    parts = ["%s:%d" % (s, n)
             for s, n in sorted(census.attempts_by_state.items())]
    lines.append("  attempts   %s" % "  ".join(parts))
    lines.append("  CAS: %d objects   released_edges: %d" % (
        census.cas_object_count, census.released_edge_count))
    lines.append("")

    # --- taskforce ---
    lines.append(_bar("TASKFORCE", w))
    tf = snapshot.taskforce
    lines.append("  %d members, %d seats (%d established)" % (
        tf.member_count, tf.seat_count, tf.established_count))
    for seat in tf.seats:
        lines.append("    %-11s %-24s %-16s %d qual" % (
            seat.status[:11],
            "%s/%s" % (seat.capability[:12], seat.role[:10]),
            seat.specialist_handle[:16],
            seat.qualifying_count))
    lines.append("")

    # --- recent assignments ---
    if snapshot.recent_assignments:
        lines.append(_bar("ASSIGNMENTS", w))
        for a in snapshot.recent_assignments[:5]:
            lines.append("  %s %-16s %-12s %-10s %s" % (
                _sym(a.status), a.specialist_handle[:16],
                a.capability[:12], a.status[:10],
                a.sequence_id[:16]))
        lines.append("")

    # --- activity stream ---
    lines.append(_bar("ACTIVITY", w))
    for ev in snapshot.recent_events[:12]:
        ts = ev.timestamp[-8:] if len(ev.timestamp) >= 8 else ev.timestamp
        lines.append("  %s [%-12s] %s" % (
            ts, ev.layer[:12], _trunc(ev.summary, w - 22)))
    if not snapshot.recent_events:
        lines.append("  (no recent activity)")

    return lines


def stream(intelligence_path, authority_path, step_log_path=None,
           interval=3.0, once=False):
    """Poll and render the dashboard continuously."""
    from .readers import IntelligenceReader, AuthorityReader, StepLogReader
    from .model import build_snapshot

    intel = IntelligenceReader(intelligence_path)
    auth = AuthorityReader(authority_path)
    step = StepLogReader(step_log_path) if step_log_path else None

    try:
        while True:
            snapshot = build_snapshot(intel, auth, step_reader=step)
            lines = render(snapshot)

            if not once:
                os.system("cls" if os.name == "nt" else "clear")
            for line in lines:
                print(line)

            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        intel.close()
        auth.close()
