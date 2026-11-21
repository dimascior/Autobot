"""Agent-centric streaming dashboard with ANSI color and Unicode icons.

Renders each organizational agent as a distinct visual entity with its
role icon, status color, and structured action stream. Double-buffered
output to a terminal using 24-bit ANSI color and Unicode symbols.

Inspired by the VirtualWorld render engine: frame pipeline, buffer swap,
ground-truth state dump per tick.

Usage:
    python -m intelligence.dashboard.agent_stream
    python -m intelligence.dashboard.agent_stream --once
    python -m intelligence.dashboard.agent_stream --ground  # state dump per tick
"""

import os
import sys
import time
from datetime import datetime, timezone

from .views import ActivitySnapshot, RunView, NodeView

# ── ANSI helpers ──────────────────────────────────────────────────────

ESC = "\033["
RESET = ESC + "0m"

def fg(r, g, b):
    return "%s38;2;%d;%d;%dm" % (ESC, r, g, b)

def bg(r, g, b):
    return "%s48;2;%d;%d;%dm" % (ESC, r, g, b)

def bold(text):
    return ESC + "1m" + text + RESET

# ── Palette ───────────────────────────────────────────────────────────

C_HEADER   = fg(180, 200, 255)
C_DIM      = fg(100, 100, 120)
C_BRIGHT   = fg(230, 230, 240)
C_ACCENT   = fg(120, 180, 255)
C_SUCCESS  = fg(100, 220, 140)
C_WARN     = fg(255, 200, 80)
C_ERROR    = fg(255, 100, 100)
C_ACTIVE   = fg(80, 200, 255)
C_PENDING  = fg(160, 160, 180)
C_HALTED   = fg(200, 100, 60)

# ── Agent icons ───────────────────────────────────────────────────────
# Each organizational agent gets a distinct icon + color

AGENTS = {
    "coordinator": {"icon": "◆", "label": "COORDINATOR",    "color": fg(255, 180, 60)},
    "orchestrator": {"icon": "◈", "label": "ORCHESTRATOR",   "color": fg(180, 120, 255)},
    "engineer":     {"icon": "⚙", "label": "ENGINEER",       "color": fg(100, 200, 255)},
    "taskforce":    {"icon": "⛊", "label": "TASKFORCE",      "color": fg(80, 220, 160)},
    "registry":     {"icon": "⬡", "label": "REGISTRY",       "color": fg(200, 200, 220)},
    "sequencer":    {"icon": "⧫", "label": "SEQUENCER",      "color": fg(220, 180, 100)},
    "verifier":     {"icon": "✓", "label": "VERIFIER",       "color": fg(100, 220, 100)},
    "cas":          {"icon": "▣", "label": "CAS",            "color": fg(180, 180, 200)},
}

# Specialist role icons
ROLE_ICONS = {
    "investigator":  "🔍",
    "builder":       "🔧",
    "reviewer":      "📋",
    "researcher":    "📡",
    "specialist":    "⬢",
}

# Status icons
STATUS_ICONS = {
    "ACTIVE":     "●",
    "COMPLETE":   "✦",
    "COMPLETED":  "✦",
    "PENDING":    "○",
    "EXECUTING":  "▶",
    "RUNNING":    "▶",
    "VERIFIED":   "✓",
    "ACCEPTED":   "★",
    "BLOCKED":    "◫",
    "HALTED":     "⏸",
    "FAILED":     "✗",
    "READY":      "◎",
    "SETTLED":    "✦",
}

STATUS_COLORS = {
    "ACTIVE":     C_ACTIVE,
    "COMPLETE":   C_SUCCESS,
    "COMPLETED":  C_SUCCESS,
    "PENDING":    C_PENDING,
    "EXECUTING":  C_ACCENT,
    "RUNNING":    C_ACCENT,
    "VERIFIED":   C_SUCCESS,
    "ACCEPTED":   C_SUCCESS,
    "BLOCKED":    C_WARN,
    "HALTED":     C_HALTED,
    "FAILED":     C_ERROR,
    "READY":      C_ACTIVE,
}

# Elevation mode badges
MODE_BADGES = {
    "DIRECT":               fg(80, 200, 160) + "DIR" + RESET,
    "ENGINEER_ELEVATED":    fg(100, 180, 255) + "ENG" + RESET,
    "ORCHESTRATOR_ELEVATED": fg(180, 120, 255) + "ORC" + RESET,
}

# ── Rendering ─────────────────────────────────────────────────────────

def _status_badge(status):
    icon = STATUS_ICONS.get(status, "?")
    color = STATUS_COLORS.get(status, C_DIM)
    return color + icon + " " + status + RESET


def _agent_header(agent_key, extra=""):
    a = AGENTS.get(agent_key, {"icon": "?", "label": agent_key, "color": C_DIM})
    s = a["color"] + a["icon"] + " " + bold(a["label"]) + RESET
    if extra:
        s += "  " + C_DIM + extra + RESET
    return s


def _trunc(text, w):
    if not text:
        return ""
    return text if len(text) <= w else text[:w - 1] + "…"


def _bar(width=78):
    return C_DIM + "─" * width + RESET


def _double_bar(label, width=78):
    pad = width - len(label) - 2
    return C_HEADER + "═" * 2 + " " + bold(label) + " " + C_HEADER + "═" * max(pad - 2, 1) + RESET


def render_agents(snapshot, width=80):
    """Render the agent-centric streaming view."""
    lines = []
    w = width - 2

    # ── Title bar ─────────────────────────────────────────────────
    now = snapshot.captured_utc
    lines.append("")
    lines.append(C_HEADER + "  ╔" + "═" * (w - 2) + "╗" + RESET)
    lines.append(C_HEADER + "  ║" + RESET +
                 bold("  AUTOBOT ORGANIZATION") +
                 " " * (w - 28 - len(now)) + C_DIM + now + RESET +
                 C_HEADER + "  ║" + RESET)
    lines.append(C_HEADER + "  ╚" + "═" * (w - 2) + "╝" + RESET)
    lines.append("")

    # ── Coordinator ───────────────────────────────────────────────
    lines.append(_agent_header("coordinator",
                               "admission / elevation"))
    lines.append(_bar(w))
    for cd in snapshot.coordinator_decisions:
        badge = MODE_BADGES.get(cd.elevation_mode, cd.elevation_mode)
        feats = ", ".join(sorted(cd.features)) if cd.features else "—"
        lines.append("    %s  %-32s  %s" % (
            badge, _trunc(cd.objective_id, 32),
            C_DIM + feats + RESET))
    if not snapshot.coordinator_decisions:
        lines.append("    " + C_DIM + "(no decisions)" + RESET)
    lines.append("")

    # ── Orchestrator ──────────────────────────────────────────────
    lines.append(_agent_header("orchestrator",
                               "decomposition / DAG"))
    lines.append(_bar(w))
    for run in snapshot.runs:
        badge = MODE_BADGES.get(run.mode, run.mode[:3])
        status_b = _status_badge(run.status)
        obj_text = ""
        for o in snapshot.objectives:
            if o.objective_id == run.objective_id:
                obj_text = _trunc(o.text, 40)
                break
        lines.append("    %s  %s  %s  %s" % (
            badge, C_ACCENT + run.orchestration_id[:16] + RESET,
            status_b, C_DIM + obj_text + RESET))

        for node in run.nodes:
            kind = (node.realization_kind or "?")[:14]
            if kind == "TASK_CONTRACT":
                kind_c = fg(100, 200, 180) + "TASK" + RESET
            elif kind == "ENGINEER_REA":
                kind_c = fg(100, 180, 255) + "ENGR" + RESET
            else:
                kind_c = C_DIM + kind[:4] + RESET
            n_status = _status_badge(node.status)
            bound = ""
            if node.sequence_id:
                bound = C_DIM + " seq=" + node.sequence_id[:12] + RESET
            if node.output_sha256:
                bound += C_SUCCESS + " ✦" + RESET
            lines.append("      %s %-18s %s %s%s" % (
                "├─" if node != run.nodes[-1] else "└─",
                C_BRIGHT + node.node_key[:18] + RESET,
                n_status, kind_c, bound))

        for edge in run.edges:
            st_color = C_SUCCESS if edge.status == "RELEASED" else C_PENDING
            lines.append("         %s%s → %s  %s  %s%s" % (
                C_DIM, edge.source_node_id[:10],
                edge.target_node_id[:10],
                edge.dependency_kind[:14],
                st_color + edge.status + RESET, RESET))

        if not run.nodes:
            lines.append("      " + C_DIM + "(awaiting Opus decomposition)" + RESET)
    if not snapshot.runs:
        lines.append("    " + C_DIM + "(no runs)" + RESET)
    lines.append("")

    # ── Engineer ──────────────────────────────────────────────────
    eng_nodes = [n for r in snapshot.runs for n in r.nodes
                 if n.realization_kind and "ENGINEER" in n.realization_kind]
    lines.append(_agent_header("engineer",
                               "technical reasoning"))
    lines.append(_bar(w))
    if eng_nodes:
        for n in eng_nodes:
            sha = n.output_sha256[:16] if n.output_sha256 else "—"
            lines.append("    %s  %s  out=%s" % (
                _status_badge(n.status),
                C_BRIGHT + n.node_key[:24] + RESET,
                C_DIM + sha + RESET))
    else:
        lines.append("    " + C_DIM + "(no engineer reasoning nodes)" + RESET)
    lines.append("")

    # ── Taskforce ─────────────────────────────────────────────────
    tf = snapshot.taskforce
    lines.append(_agent_header("taskforce",
                               "%d members, %d seats (%d established)"
                               % (tf.member_count, tf.seat_count,
                                  tf.established_count)))
    lines.append(_bar(w))
    for seat in tf.seats:
        role_icon = ROLE_ICONS.get(seat.role, "⬢")
        st_color = C_SUCCESS if seat.status == "ESTABLISHED" else C_ACTIVE
        lines.append("    %s %s  %-24s %-16s  %s%d qual%s" % (
            role_icon, st_color + seat.status[:11] + RESET,
            C_BRIGHT + "%s/%s" % (seat.capability[:12],
                                   seat.role[:10]) + RESET,
            C_DIM + seat.specialist_handle[:16] + RESET,
            C_ACCENT, seat.qualifying_count, RESET))
    lines.append("")

    # ── Authority ─────────────────────────────────────────────────
    lines.append(_agent_header("registry", "authority state") +
                 "  " + _agent_header("sequencer") +
                 "  " + _agent_header("cas"))
    lines.append(_bar(w))
    census = snapshot.authority
    seq_parts = []
    for state, count in sorted(census.sequences_by_state.items()):
        c = STATUS_COLORS.get(state, C_DIM)
        seq_parts.append("%s%s:%d%s" % (c, state, count, RESET))
    lines.append("    sequences  " + "  ".join(seq_parts))
    att_parts = []
    for state, count in sorted(census.attempts_by_state.items()):
        c = STATUS_COLORS.get(state, C_DIM)
        att_parts.append("%s%s:%d%s" % (c, state, count, RESET))
    lines.append("    attempts   " + "  ".join(att_parts))
    lines.append("    %s▣ CAS: %d objects%s   %srel_edges: %d%s" % (
        C_DIM, census.cas_object_count, RESET,
        C_DIM, census.released_edge_count, RESET))
    lines.append("")

    # ── Recent assignments (specialist actions) ───────────────────
    if snapshot.recent_assignments:
        lines.append(_double_bar("SPECIALIST ACTIONS", w))
        for a in snapshot.recent_assignments[:6]:
            role_icon = ROLE_ICONS.get(a.role, "⬢")
            st = _status_badge(a.status)
            path = C_DIM + a.selection_path[:12] + RESET
            lines.append("    %s %s  %-16s  %s  %s  %s" % (
                role_icon, st,
                C_BRIGHT + a.specialist_handle[:16] + RESET,
                a.capability[:12],
                a.sequence_id[:16],
                path))
        lines.append("")

    # ── Activity stream ───────────────────────────────────────────
    lines.append(_double_bar("ACTIVITY STREAM", w))
    for ev in snapshot.recent_events[:15]:
        ts = ev.timestamp[-12:-4] if len(ev.timestamp) > 12 else ev.timestamp[:8]
        a = AGENTS.get(ev.layer, {"icon": "·", "color": C_DIM})
        lines.append("    %s%s%s %s%s%s  %s" % (
            C_DIM, ts, RESET,
            a["color"], a["icon"], RESET,
            _trunc(ev.summary, w - 24)))
    if not snapshot.recent_events:
        lines.append("    " + C_DIM + "· · ·" + RESET)
    lines.append("")

    return lines


def _ground_dump(snapshot):
    """VirtualWorld-style ground-truth state dump."""
    lines = []
    lines.append("=== GROUND STATE %s ===" % snapshot.captured_utc)
    lines.append("objectives: %d active, %d complete" % (
        sum(1 for o in snapshot.objectives if o.status == "ACTIVE"),
        sum(1 for o in snapshot.objectives if o.status in ("COMPLETE", "COMPLETED"))))
    lines.append("coordinator_decisions: %d" % len(snapshot.coordinator_decisions))
    lines.append("runs: %d (%s)" % (
        len(snapshot.runs),
        ", ".join("%s:%s" % (r.orchestration_id[:8], r.status[:4])
                  for r in snapshot.runs)))
    total_nodes = sum(len(r.nodes) for r in snapshot.runs)
    lines.append("nodes: %d" % total_nodes)
    for r in snapshot.runs:
        for n in r.nodes:
            lines.append("  %s key=%s kind=%s status=%s seq=%s" % (
                n.node_id[:16], n.node_key,
                n.realization_kind or "?", n.status,
                n.sequence_id[:12] if n.sequence_id else "-"))
    lines.append("authority: %s" % snapshot.authority.sequences_by_state)
    lines.append("taskforce: %d members, %d seats" % (
        snapshot.taskforce.member_count,
        snapshot.taskforce.seat_count))
    lines.append("cas: %d objects" % snapshot.authority.cas_object_count)
    return lines


def stream(intelligence_path, authority_path, step_log_path=None,
           interval=3.0, once=False, ground=False):
    """Stream the agent-centric dashboard."""
    from .readers import IntelligenceReader, AuthorityReader, StepLogReader
    from .model import build_snapshot

    intel = IntelligenceReader(intelligence_path)
    auth = AuthorityReader(authority_path)
    step = StepLogReader(step_log_path) if step_log_path else None

    tick = 0
    try:
        while True:
            snapshot = build_snapshot(intel, auth, step_reader=step)
            lines = render_agents(snapshot)

            if not once:
                os.system("cls" if os.name == "nt" else "clear")

            for line in lines:
                print(line)

            if ground:
                print("")
                for line in _ground_dump(snapshot):
                    print(line)

            tick += 1
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        intel.close()
        auth.close()
