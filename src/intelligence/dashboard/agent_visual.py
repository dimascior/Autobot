"""Animated agent visual dashboard with ASCII art and frame pipeline.

Each organizational agent is an ASCII art box with live status.
Connections flow between agents. Active items spin. The DAG
renders as a visual tree.

Frame-based: redraws on a timer using ANSI cursor control.
"""

import os
import sys
import time
from datetime import datetime, timezone

from .views import ActivitySnapshot

# ── ANSI ──────────────────────────────────────────────────────────────

ESC = "\033["
RESET = ESC + "0m"
BOLD = ESC + "1m"
DIM = ESC + "2m"
HIDE_CURSOR = ESC + "?25l"
SHOW_CURSOR = ESC + "?25h"
HOME = ESC + "H"

def fg(r, g, b): return "%s38;2;%d;%d;%dm" % (ESC, r, g, b)
def bg(r, g, b): return "%s48;2;%d;%d;%dm" % (ESC, r, g, b)

# ── Palette ───────────────────────────────────────────────────────────

P = {
    "frame":    fg(60, 70, 90),
    "title":    fg(200, 220, 255),
    "dim":      fg(80, 85, 100),
    "text":     fg(170, 175, 190),
    "bright":   fg(220, 225, 240),
    "accent":   fg(100, 170, 255),
    "success":  fg(80, 210, 130),
    "warn":     fg(255, 190, 60),
    "error":    fg(255, 90, 80),
    "active":   fg(60, 190, 255),
    "coord":    fg(255, 170, 50),
    "orch":     fg(170, 110, 255),
    "eng":      fg(80, 190, 255),
    "tf":       fg(60, 210, 150),
    "auth":     fg(190, 190, 210),
    "seq":      fg(210, 170, 90),
    "cas":      fg(160, 165, 185),
    "dir":      fg(80, 200, 160),
    "halted":   fg(200, 90, 50),
    "blocked":  fg(200, 170, 60),
    "flow":     fg(50, 60, 80),
    "flowlit":  fg(100, 140, 200),
}

# ── Spinners ──────────────────────────────────────────────────────────

SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
PULSE = ["░", "▒", "▓", "█", "▓", "▒"]
FLOW_CHARS = ["·", "·", "∙", "•", "●", "•", "∙", "·"]

def spinner(tick):
    return SPIN[tick % len(SPIN)]

def pulse(tick):
    return PULSE[tick % len(PULSE)]

def flow_char(tick, offset=0):
    return FLOW_CHARS[(tick + offset) % len(FLOW_CHARS)]

# ── Status rendering ──────────────────────────────────────────────────

STATUS_ART = {
    "ACTIVE":    ("●", "active"),
    "COMPLETE":  ("✦", "success"),
    "COMPLETED": ("✦", "success"),
    "PENDING":   ("○", "dim"),
    "EXECUTING": (None, "accent"),    # uses spinner
    "RUNNING":   (None, "accent"),
    "VERIFIED":  ("✓", "success"),
    "ACCEPTED":  ("★", "success"),
    "BLOCKED":   ("◫", "blocked"),
    "HALTED":    ("⏸", "halted"),
    "FAILED":    ("✗", "error"),
    "READY":     ("◎", "active"),
}

def status_icon(status, tick=0):
    art = STATUS_ART.get(status, ("?", "dim"))
    icon = art[0] if art[0] else spinner(tick)
    color = P.get(art[1], P["dim"])
    return color + icon + RESET

# ── Box drawing ───────────────────────────────────────────────────────

def box(lines, width, color_key="frame", title=None, icon=None):
    """Draw a box with content lines. Returns list of strings."""
    c = P[color_key]
    out = []
    top = "╔" + "═" * (width - 2) + "╗"
    if title:
        t = " %s%s%s %s%s%s " % (
            P.get(color_key, c), icon or "", RESET,
            BOLD, title, RESET)
        # Visual width of title (sans ANSI)
        tw = len(icon or "") + 1 + len(title) + 2
        pad = width - 2 - tw
        top = "╔═" + t + c + "═" * max(pad, 0) + "╗"
    out.append(c + top + RESET)
    for line in lines:
        # Pad content to box width (visual)
        out.append(c + "║" + RESET + " " + line)
    out.append(c + "╚" + "═" * (width - 2) + "╝" + RESET)
    return out


def hflow(tick, length=5):
    """Horizontal animated flow: ····●····"""
    s = ""
    for i in range(length):
        ch = flow_char(tick, i)
        c = P["flowlit"] if ch in ("●", "•") else P["flow"]
        s += c + ch + RESET
    return s


def vflow(tick):
    """Vertical flow character."""
    ch = flow_char(tick)
    c = P["flowlit"] if ch in ("●", "•") else P["flow"]
    return c + "│" + RESET

# ── Agent panels ──────────────────────────────────────────────────────

def panel_coordinator(snapshot, tick, w=36):
    decisions = snapshot.coordinator_decisions
    counts = {"DIRECT": 0, "ENGINEER_ELEVATED": 0, "ORCHESTRATOR_ELEVATED": 0}
    for cd in decisions:
        counts[cd.elevation_mode] = counts.get(cd.elevation_mode, 0) + 1
    lines = []
    lines.append(
        P["dir"] + "DIR" + RESET + " " + P["bright"] + str(counts["DIRECT"]) + RESET +
        "  " +
        P["eng"] + "ENG" + RESET + " " + P["bright"] + str(counts["ENGINEER_ELEVATED"]) + RESET +
        "  " +
        P["orch"] + "ORC" + RESET + " " + P["bright"] + str(counts["ORCHESTRATOR_ELEVATED"]) + RESET)
    for cd in decisions[-3:]:
        badge = {"DIRECT": P["dir"] + "▸DIR", "ENGINEER_ELEVATED": P["eng"] + "▸ENG",
                 "ORCHESTRATOR_ELEVATED": P["orch"] + "▸ORC"}.get(cd.elevation_mode, "???")
        lines.append(badge + RESET + " " + P["dim"] + cd.objective_id[:22] + RESET)
    return box(lines, w, "coord", "COORDINATOR", "◆")


def panel_orchestrator(snapshot, tick, w=36):
    runs = snapshot.runs
    active = [r for r in runs if r.status == "ACTIVE"]
    lines = []
    lines.append(P["bright"] + "%d runs" % len(runs) + RESET +
                 P["dim"] + " (%d active)" % len(active) + RESET)
    for run in runs[-3:]:
        node_count = len(run.nodes)
        executing = sum(1 for n in run.nodes if n.status in ("EXECUTING", "RUNNING"))
        completed = sum(1 for n in run.nodes if n.status in ("COMPLETED", "COMPLETE"))
        badge = {"DIRECT": P["dir"] + "DIR", "ENGINEER_ELEVATED": P["eng"] + "ENG",
                 "ORCHESTRATOR_ELEVATED": P["orch"] + "ORC"}.get(run.mode, "???")
        node_txt = ""
        if node_count:
            node_txt = " %s%d%s" % (P["dim"], node_count, RESET)
            if executing:
                node_txt += " %s%s%d exec%s" % (P["accent"], spinner(tick), executing, RESET)
            if completed:
                node_txt += " %s✦%d%s" % (P["success"], completed, RESET)
        else:
            node_txt = " %s···%s" % (P["dim"], RESET)
        lines.append(badge + RESET + " " + P["dim"] + run.orchestration_id[:10] + RESET + node_txt)
    return box(lines, w, "orch", "ORCHESTRATOR", "◈")


def panel_engineer(snapshot, tick, w=36):
    eng_nodes = [n for r in snapshot.runs for n in r.nodes
                 if n.realization_kind and "ENGINEER" in n.realization_kind]
    lines = []
    if eng_nodes:
        completed = sum(1 for n in eng_nodes if n.status in ("COMPLETED", "COMPLETE"))
        active = sum(1 for n in eng_nodes if n.status in ("EXECUTING", "PENDING"))
        lines.append(P["bright"] + "%d decisions" % completed + RESET +
                     ((" %s%s%d active%s" % (P["accent"], spinner(tick), active, RESET)) if active else ""))
        for n in eng_nodes[-2:]:
            sha = n.output_sha256[:12] if n.output_sha256 else "···"
            lines.append(status_icon(n.status, tick) + " " +
                         P["dim"] + sha + RESET)
    else:
        lines.append(P["dim"] + "no reasoning nodes" + RESET)
    return box(lines, w, "eng", "ENGINEER", "⚙")


def panel_taskforce(snapshot, tick, w=36):
    tf = snapshot.taskforce
    lines = []
    lines.append(P["bright"] + "%d" % tf.member_count + RESET + P["text"] + " members  " + RESET +
                 P["bright"] + "%d" % tf.established_count + RESET + P["text"] + " established" + RESET)
    role_icons = {"investigator": "🔍", "builder": "🔧", "reviewer": "📋"}
    for seat in tf.seats[:3]:
        ri = role_icons.get(seat.role, "⬢")
        st_c = P["success"] if seat.status == "ESTABLISHED" else P["active"]
        lines.append("%s %s%s%s %-14s %s%d%s" % (
            ri, st_c, seat.status[:4], RESET,
            P["dim"] + seat.specialist_handle[:14] + RESET,
            P["accent"], seat.qualifying_count, RESET))
    if tf.seat_count > 3:
        lines.append(P["dim"] + "+ %d more" % (tf.seat_count - 3) + RESET)
    return box(lines, w, "tf", "TASKFORCE", "⛊")


def panel_authority(snapshot, tick, w=36):
    c = snapshot.authority
    lines = []
    total_seq = sum(c.sequences_by_state.values())
    completed = c.sequences_by_state.get("COMPLETED", 0)
    blocked = c.sequences_by_state.get("BLOCKED", 0)
    halted = c.sequences_by_state.get("HALTED", 0)
    bar_w = 20
    if total_seq:
        filled = int(completed / total_seq * bar_w)
        bar = P["success"] + "█" * filled + P["dim"] + "░" * (bar_w - filled) + RESET
    else:
        bar = P["dim"] + "░" * bar_w + RESET
    lines.append(bar + " %s%d%s/%d" % (P["success"], completed, RESET, total_seq))
    parts = []
    if blocked:
        parts.append(P["blocked"] + "◫%d blocked" % blocked + RESET)
    if halted:
        parts.append(P["halted"] + "⏸%d halted" % halted + RESET)
    if parts:
        lines.append("  ".join(parts))
    lines.append(P["cas"] + "▣" + RESET + P["dim"] + " %d CAS objects" % c.cas_object_count + RESET +
                 "  " + P["dim"] + "%d edges" % c.released_edge_count + RESET)
    return box(lines, w, "auth", "AUTHORITY", "⬡")


# ── DAG visual ────────────────────────────────────────────────────────

def render_dag(snapshot, tick, w=78):
    """Render the node DAG as a visual tree."""
    lines = []
    all_nodes = [(r, n) for r in snapshot.runs for n in r.nodes]
    if not all_nodes:
        return [P["dim"] + "  (no nodes)" + RESET]

    for run in snapshot.runs:
        if not run.nodes:
            continue
        badge = {"DIRECT": P["dir"] + "DIR", "ENGINEER_ELEVATED": P["eng"] + "ENG",
                 "ORCHESTRATOR_ELEVATED": P["orch"] + "ORC"}.get(run.mode, "???")
        lines.append("  " + badge + RESET + " " +
                     P["dim"] + run.orchestration_id[:12] + RESET)

        for i, node in enumerate(run.nodes):
            is_last = (i == len(run.nodes) - 1)
            connector = "└" if is_last else "├"
            kind_short = {"TASK_CONTRACT": "TASK", "ENGINEER_REASONING": "ENGR",
                          "CONTROL": "CTRL"}.get(node.realization_kind or "", "????")
            kind_c = {"TASK": P["dir"], "ENGR": P["eng"], "CTRL": P["dim"]}.get(kind_short, P["dim"])

            # Build node visual
            si = status_icon(node.status, tick)
            bound_info = ""
            if node.sequence_id:
                bound_info += P["dim"] + " ⇒" + node.sequence_id[:10] + RESET
            if node.output_sha256:
                bound_info += P["success"] + " ✦" + RESET

            lines.append("  %s%s─%s %s%-16s%s %s%s%s%s" % (
                P["frame"], connector, RESET,
                si, "",
                P["bright"] + node.node_key[:16] + RESET,
                kind_c, kind_short, RESET,
                bound_info))

        # Edges
        for edge in run.edges:
            st_c = P["success"] if edge.status == "RELEASED" else P["dim"]
            flow = hflow(tick, 3) if edge.status == "RELEASED" else P["dim"] + "···" + RESET
            lines.append("     %s%s%s %s→%s %s  %s" % (
                P["dim"], edge.source_node_id[:8], RESET,
                flow, P["dim"], edge.target_node_id[:8] + RESET,
                st_c + edge.dependency_kind[:10] + RESET))
    return lines


# ── Activity ticker ───────────────────────────────────────────────────

def render_activity(snapshot, tick, w=78, max_lines=8):
    lines = []
    events = snapshot.recent_events[:max_lines]
    if not events:
        lines.append("  " + P["dim"] + "· · · awaiting activity · · ·" + RESET)
        return lines
    for ev in events:
        ts = ev.timestamp[-12:-4] if len(ev.timestamp) > 12 else ev.timestamp[:8]
        layer_c = {
            "coordinator": P["coord"], "orchestrator": P["orch"],
            "engineer": P["eng"], "taskforce": P["tf"],
            "authority": P["auth"],
        }.get(ev.layer, P["dim"])
        layer_icon = {
            "coordinator": "◆", "orchestrator": "◈",
            "engineer": "⚙", "taskforce": "⛊",
            "authority": "⬡",
        }.get(ev.layer, "·")
        summary = ev.summary
        if len(summary) > w - 22:
            summary = summary[:w - 23] + "…"
        lines.append("  %s%s%s %s%s%s %s%s%s" % (
            P["dim"], ts, RESET,
            layer_c, layer_icon, RESET,
            P["text"], summary, RESET))
    return lines


# ── Compose frame ─────────────────────────────────────────────────────

def render_frame(snapshot, tick, width=80):
    lines = []
    pw = min(38, (width - 4) // 2)
    now = snapshot.captured_utc

    # Title
    lines.append("")
    title_line = ("  %s%s AUTOBOT ORGANIZATION %s" % (BOLD, P["title"], RESET) +
                  " " * max(1, width - 42 - len(now)) +
                  P["dim"] + now + RESET)
    lines.append(title_line)
    lines.append("  " + P["frame"] + "━" * (width - 4) + RESET)
    lines.append("")

    # Top row: Coordinator | Orchestrator
    coord_box = panel_coordinator(snapshot, tick, pw)
    orch_box = panel_orchestrator(snapshot, tick, pw)
    gap = "  "
    max_h = max(len(coord_box), len(orch_box))
    for i in range(max_h):
        left = coord_box[i] if i < len(coord_box) else " " * pw
        right = orch_box[i] if i < len(orch_box) else " " * pw
        lines.append("  " + left + gap + right)

    # Flow: Coordinator → DAG
    lines.append("  " + " " * (pw // 2) + vflow(tick) +
                 " " * (pw // 2 + len(gap)) + vflow(tick))
    lines.append("")

    # Middle row: Engineer | Taskforce
    eng_box = panel_engineer(snapshot, tick, pw)
    tf_box = panel_taskforce(snapshot, tick, pw)
    max_h = max(len(eng_box), len(tf_box))
    for i in range(max_h):
        left = eng_box[i] if i < len(eng_box) else " " * pw
        right = tf_box[i] if i < len(tf_box) else " " * pw
        lines.append("  " + left + gap + right)

    # Flow: → Authority
    lines.append("  " + " " * (pw // 2) + vflow(tick) +
                 " " * (pw // 2 + len(gap)) + vflow(tick))
    lines.append("")

    # Authority panel (full width)
    auth_box = panel_authority(snapshot, tick, width - 4)
    for line in auth_box:
        lines.append("  " + line)
    lines.append("")

    # DAG section
    lines.append("  " + P["frame"] + "━" * 3 + RESET + " " +
                 BOLD + P["accent"] + "NODE DAG" + RESET + " " +
                 P["frame"] + "━" * (width - 18) + RESET)
    dag_lines = render_dag(snapshot, tick, width - 4)
    for line in dag_lines:
        lines.append(line)
    lines.append("")

    # Activity stream
    lines.append("  " + P["frame"] + "━" * 3 + RESET + " " +
                 BOLD + P["accent"] + "ACTIVITY" + RESET + " " +
                 P["frame"] + "━" * (width - 18) + RESET)
    act_lines = render_activity(snapshot, tick, width - 4)
    for line in act_lines:
        lines.append(line)
    lines.append("")

    return lines


# ── Stream loop ───────────────────────────────────────────────────────

def stream(intelligence_path, authority_path, step_log_path=None,
           interval=2.0, once=False, ground=False):
    from .readers import IntelligenceReader, AuthorityReader, StepLogReader
    from .model import build_snapshot

    intel = IntelligenceReader(intelligence_path)
    auth = AuthorityReader(authority_path)
    step = StepLogReader(step_log_path) if step_log_path else None

    tick = 0
    try:
        if not once:
            sys.stdout.write(HIDE_CURSOR)
            sys.stdout.flush()
        while True:
            snapshot = build_snapshot(intel, auth, step_reader=step)
            lines = render_frame(snapshot, tick)

            if once:
                for line in lines:
                    print(line)
                if ground:
                    _ground(snapshot)
                break

            # Clear and redraw
            sys.stdout.write(HOME)
            os.system("cls" if os.name == "nt" else "true")
            sys.stdout.write(HOME)
            for line in lines:
                # Pad to clear previous longer lines
                sys.stdout.write(line + ESC + "K" + "\n")
            sys.stdout.write(ESC + "J")  # clear below
            sys.stdout.flush()

            if ground:
                _ground(snapshot)

            tick += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if not once:
            sys.stdout.write(SHOW_CURSOR)
            sys.stdout.flush()
        intel.close()
        auth.close()


def _ground(snapshot):
    print("\n%s=== GROUND ===%s" % (P["dim"], RESET))
    print("%sobjectives: %d active, %d complete%s" % (
        P["dim"],
        sum(1 for o in snapshot.objectives if o.status == "ACTIVE"),
        sum(1 for o in snapshot.objectives if o.status in ("COMPLETE", "COMPLETED")),
        RESET))
    print("%sruns: %d  nodes: %d  CAS: %d%s" % (
        P["dim"], len(snapshot.runs),
        sum(len(r.nodes) for r in snapshot.runs),
        snapshot.authority.cas_object_count, RESET))
