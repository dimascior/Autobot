"""Double-buffered agent dashboard with Screen-based diff rendering.

Uses the Screen from screen.py: structured cell buffer, front/back
swap, diff-only ANSI output. Same pattern as RAMVISION's alt-screen
VRAM renderer — no full redraws, only changed cells get cursor-moved.

Each agent is a box drawn into the screen buffer. Flow indicators
animate between boxes. Active items get trail colors.
"""

import os
import sys
import time

from .screen import Screen, HIDE_CURSOR, SHOW_CURSOR, HOME, CLEAR_BELOW
from .views import ActivitySnapshot

# ── 256-color palette ─────────────────────────────────────────────────

C = {
    "frame":    240, "dim":     243, "text":    252, "bright": 255,
    "accent":   75,  "success": 78,  "warn":    214, "error":  196,
    "active":   39,  "coord":   214, "orch":    135, "eng":    75,
    "tf":       78,  "auth":    252, "cas":     248, "dir":    78,
    "halted":   166, "blocked": 214, "trail1":  196, "trail2": 208,
    "trail3":   220, "trail4":  228, "flow_dim": 236, "flow_lit": 75,
    "luna":     214, "bar_fill": 78, "bar_empty": 236,
}

SPIN = list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
FLOW = list("·∙•●•∙·")

# Agent identities
AGENT = {
    "coord": ("◆", "COORDINATOR",  C["coord"]),
    "orch":  ("◈", "ORCHESTRATOR", C["orch"]),
    "eng":   ("⚙", "ENGINEER",     C["eng"]),
    "tf":    ("⛊", "TASKFORCE",    C["tf"]),
    "auth":  ("⬡", "AUTHORITY",    C["auth"]),
}

ROLE_ICON = {"investigator": "⊙", "builder": "⊕", "reviewer": "⊘"}
STATUS_ICON = {
    "ACTIVE": "●", "COMPLETE": "✦", "COMPLETED": "✦", "PENDING": "○",
    "EXECUTING": None, "RUNNING": None, "BLOCKED": "◫",
    "HALTED": "⏸", "FAILED": "✗", "READY": "◎",
}
STATUS_COLOR = {
    "ACTIVE": C["active"], "COMPLETE": C["success"], "COMPLETED": C["success"],
    "PENDING": C["dim"], "EXECUTING": C["accent"], "RUNNING": C["accent"],
    "BLOCKED": C["blocked"], "HALTED": C["halted"], "FAILED": C["error"],
}
MODE_SHORT = {"DIRECT": "DIR", "ENGINEER_ELEVATED": "ENG", "ORCHESTRATOR_ELEVATED": "ORC"}
MODE_COLOR = {"DIRECT": C["dir"], "ENGINEER_ELEVATED": C["eng"], "ORCHESTRATOR_ELEVATED": C["orch"]}


def _si(status, tick):
    icon = STATUS_ICON.get(status)
    if icon is None:
        icon = SPIN[tick % len(SPIN)]
    return icon, STATUS_COLOR.get(status, C["dim"])


# ── Box drawing into Screen ──────────────────────────────────────────

def _box(scr, x, y, w, h, agent_key, tick):
    icon, label, color = AGENT[agent_key]
    # top
    scr.put(x, y, "╔", color); scr.put(x+1, y, "═", color)
    scr.put(x+2, y, " ", color); scr.put(x+3, y, icon, color)
    for i, ch in enumerate(label):
        scr.put(x + 5 + i, y, ch, color, bold=True)
    for cx in range(x + 5 + len(label), x + w - 1):
        scr.put(cx, y, "═", color)
    scr.put(x + w - 1, y, "╗", color)
    # sides
    for ry in range(y + 1, y + h - 1):
        scr.put(x, ry, "║", C["frame"])
        scr.put(x + w - 1, ry, "║", C["frame"])
    # bottom
    scr.put(x, y + h - 1, "╚", color)
    for cx in range(x + 1, x + w - 1):
        scr.put(cx, y + h - 1, "═", color)
    scr.put(x + w - 1, y + h - 1, "╝", color)


def _text(scr, x, y, text, fg=252, bold=False):
    for i, ch in enumerate(text):
        scr.put(x + i, y, ch, fg, bold=bold)


def _flow_v(scr, x, y, tick):
    idx = tick % len(FLOW)
    ch = FLOW[idx]
    c = C["flow_lit"] if ch in ("●", "•") else C["flow_dim"]
    scr.put(x, y, "│", c)


def _bar(scr, x, y, filled, total, w):
    if total == 0:
        ratio = 0
    else:
        ratio = filled / total
    fill_w = int(ratio * w)
    for i in range(w):
        if i < fill_w:
            scr.put(x + i, y, "█", C["bar_fill"])
        else:
            scr.put(x + i, y, "░", C["bar_empty"])


# ── Compose frame ─────────────────────────────────────────────────────

def compose(scr, snapshot, tick, routing=None):
    """Write all agents into the screen back buffer."""
    w = scr.w
    pw = min(37, (w - 6) // 2)  # panel width
    gap = 2
    x1 = 2          # left panel x
    x2 = x1 + pw + gap  # right panel x

    # Row 0-1: title
    _text(scr, 2, 0, "AUTOBOT ORGANIZATION", C["bright"], bold=True)
    _text(scr, w - 22, 0, snapshot.captured_utc[:19], C["dim"])
    for cx in range(2, w - 2):
        scr.put(cx, 1, "━", C["frame"])

    # Row 2-7: Coordinator (left) | Orchestrator (right)
    bh = 6
    _box(scr, x1, 2, pw, bh, "coord", tick)
    _box(scr, x2, 2, pw, bh, "orch", tick)

    # Coordinator content
    counts = {"DIR": 0, "ENG": 0, "ORC": 0}
    for cd in snapshot.coordinator_decisions:
        k = MODE_SHORT.get(cd.elevation_mode, "?")
        counts[k] = counts.get(k, 0) + 1
    cy = 3
    for mode_key, count in counts.items():
        mc = MODE_COLOR.get({"DIR": "DIRECT", "ENG": "ENGINEER_ELEVATED",
                             "ORC": "ORCHESTRATOR_ELEVATED"}.get(mode_key, ""), C["dim"])
        _text(scr, x1 + 2, cy, mode_key, mc, bold=True)
        _text(scr, x1 + 6, cy, str(count), C["bright"])
        cy += 1
    # Last decision
    if snapshot.coordinator_decisions:
        cd = snapshot.coordinator_decisions[-1]
        _text(scr, x1 + 2, cy, cd.objective_id[:pw - 4], C["dim"])

    # Orchestrator content
    runs = snapshot.runs
    _text(scr, x2 + 2, 3, "%d runs" % len(runs), C["bright"])
    active = sum(1 for r in runs if r.status == "ACTIVE")
    _text(scr, x2 + 10, 3, "(%d active)" % active, C["dim"])
    ry = 4
    for run in runs[-3:]:
        ms = MODE_SHORT.get(run.mode, "?")
        mc = MODE_COLOR.get(run.mode, C["dim"])
        _text(scr, x2 + 2, ry, ms, mc, bold=True)
        nc = len(run.nodes)
        ec = sum(1 for n in run.nodes if n.status in ("EXECUTING", "RUNNING"))
        cc = sum(1 for n in run.nodes if n.status in ("COMPLETED", "COMPLETE"))
        info = " %d" % nc if nc else " ···"
        _text(scr, x2 + 6, ry, info, C["dim"])
        if ec:
            _text(scr, x2 + 10, ry, SPIN[tick % len(SPIN)] + str(ec), C["accent"])
        if cc:
            _text(scr, x2 + 13, ry, "✦" + str(cc), C["success"])
        ry += 1

    # Flow connectors (row 8)
    _flow_v(scr, x1 + pw // 2, 8, tick)
    _flow_v(scr, x2 + pw // 2, 8, tick)

    # Row 9-14: Engineer (left) | Taskforce (right)
    _box(scr, x1, 9, pw, bh, "eng", tick)
    _box(scr, x2, 9, pw, bh, "tf", tick)

    # Engineer content
    eng_nodes = [n for r in snapshot.runs for n in r.nodes
                 if n.realization_kind and "ENGINEER" in n.realization_kind]
    if eng_nodes:
        completed = sum(1 for n in eng_nodes if n.status in ("COMPLETED", "COMPLETE"))
        _text(scr, x1 + 2, 10, "%d decisions" % completed, C["bright"])
        for i, n in enumerate(eng_nodes[-2:]):
            sha = n.output_sha256[:14] if n.output_sha256 else "···"
            si, sc = _si(n.status, tick)
            scr.put(x1 + 2, 11 + i, si, sc)
            _text(scr, x1 + 4, 11 + i, sha, C["dim"])
    else:
        _text(scr, x1 + 2, 10, "no reasoning nodes", C["dim"])

    # Taskforce content
    tf = snapshot.taskforce
    _text(scr, x2 + 2, 10, "%d members" % tf.member_count, C["bright"])
    _text(scr, x2 + 14, 10, "%d estab" % tf.established_count, C["dim"])
    # Route
    if routing and routing.default_route != "?":
        rd = routing.routes.get(routing.default_route, {})
        route_str = "%s/%s" % (rd.get("agent", "?"), rd.get("model", "?"))
        _text(scr, x2 + 2, 11, routing.default_route[:12], C["luna"], bold=True)
        _text(scr, x2 + 15, 11, route_str[:pw - 17], C["dim"])
    for i, seat in enumerate(tf.seats[:2]):
        ri = ROLE_ICON.get(seat.role, "·")
        sc = C["success"] if seat.status == "ESTABLISHED" else C["active"]
        scr.put(x2 + 2, 12 + i, ri[0], sc)
        cap_role = "%s/%s" % (seat.capability[:10], seat.role[:6])
        _text(scr, x2 + 4, 12 + i, cap_role, C["text"])
        _text(scr, x2 + 22, 12 + i, "%dq" % seat.qualifying_count, C["accent"])

    # Flow (row 15)
    _flow_v(scr, x1 + pw // 2, 15, tick)
    _flow_v(scr, x2 + pw // 2, 15, tick)

    # Row 16-20: Authority (full width)
    aw = pw * 2 + gap
    _box(scr, x1, 16, aw, 5, "auth", tick)
    census = snapshot.authority
    total_seq = sum(census.sequences_by_state.values())
    completed = census.sequences_by_state.get("COMPLETED", 0)
    blocked = census.sequences_by_state.get("BLOCKED", 0)
    halted = census.sequences_by_state.get("HALTED", 0)
    _bar(scr, x1 + 2, 17, completed, max(total_seq, 1), 20)
    _text(scr, x1 + 23, 17, "%d/%d seq" % (completed, total_seq), C["bright"])
    if blocked:
        _text(scr, x1 + 35, 17, "◫%d" % blocked, C["blocked"])
    if halted:
        _text(scr, x1 + 40, 17, "⏸%d" % halted, C["halted"])
    _text(scr, x1 + 2, 18, "▣ %d CAS" % census.cas_object_count, C["cas"])
    _text(scr, x1 + 15, 18, "%d edges" % census.released_edge_count, C["dim"])
    att_parts = []
    for state, count in sorted(census.attempts_by_state.items()):
        att_parts.append("%s:%d" % (state[:4], count))
    _text(scr, x1 + 2, 19, " ".join(att_parts)[:aw - 4], C["dim"])

    # Active execution section
    exec_y = 21
    has_active = (snapshot.active_sessions or snapshot.active_attempts
                  or snapshot.active_sequences)
    if has_active:
        for cx in range(2, w - 2):
            scr.put(cx, exec_y, "━", C["frame"])
        _text(scr, 4, exec_y, " EXECUTING ", C["trail1"], bold=True)
        exec_y += 1
        for sess in snapshot.active_sessions[:3]:
            scr.put(3, exec_y, SPIN[tick % len(SPIN)], C["accent"])
            provider_str = ""
            if sess.provider:
                provider_str = " %s" % sess.provider[:8]
                if sess.model:
                    provider_str += "/%s" % sess.model[:12]
            _text(scr, 5, exec_y, sess.call_id[:16], C["bright"])
            if sess.native_session_id:
                _text(scr, 22, exec_y, sess.native_session_id[:16], C["dim"])
            _text(scr, 40, exec_y, provider_str[:20], C["luna"])
            exec_y += 1
        for att in snapshot.active_attempts[:3]:
            scr.put(3, exec_y, SPIN[tick % len(SPIN)], C["accent"])
            _text(scr, 5, exec_y, att.attempt_id[:16], C["bright"])
            _text(scr, 22, exec_y,
                  "→ %s" % att.sequence_id[:14], C["dim"])
            exec_y += 1
        for seq in snapshot.active_sequences[:3]:
            if seq.state in ("EXECUTING", "VERIFYING"):
                scr.put(3, exec_y, SPIN[tick % len(SPIN)], C["accent"])
            else:
                si, sc = _si(seq.state, tick)
                scr.put(3, exec_y, si, sc)
            _text(scr, 5, exec_y, seq.sequence_id[:16], C["bright"])
            _text(scr, 22, exec_y, seq.state[:12], C["dim"])
            exec_y += 1
        exec_y += 1
    else:
        exec_y = 21

    # DAG separator
    for cx in range(2, w - 2):
        scr.put(cx, exec_y, "━", C["frame"])
    _text(scr, 4, exec_y, " DAG ", C["accent"], bold=True)

    # Node DAG
    dag_y = exec_y + 1
    for run in snapshot.runs:
        if not run.nodes:
            continue
        ms = MODE_SHORT.get(run.mode, "?")
        mc = MODE_COLOR.get(run.mode, C["dim"])
        _text(scr, 3, dag_y, ms, mc, bold=True)
        _text(scr, 7, dag_y, run.orchestration_id[:12], C["dim"])
        dag_y += 1
        for i, node in enumerate(run.nodes):
            connector = "└" if i == len(run.nodes) - 1 else "├"
            scr.put(4, dag_y, connector[0], C["frame"])
            scr.put(5, dag_y, "─", C["frame"])
            si, sc = _si(node.status, tick)
            scr.put(7, dag_y, si, sc)
            _text(scr, 9, dag_y, node.node_key[:16], C["bright"])
            kind = {"TASK_CONTRACT": "TASK", "ENGINEER_REASONING": "ENGR",
                    "CONTROL": "CTRL"}.get(node.realization_kind or "", "?")
            kc = {"TASK": C["dir"], "ENGR": C["eng"]}.get(kind, C["dim"])
            _text(scr, 26, dag_y, kind, kc)
            if node.sequence_id:
                _text(scr, 31, dag_y, "→" + node.sequence_id[:10], C["dim"])
            if node.output_sha256:
                scr.put(43, dag_y, "✦", C["success"])
            dag_y += 1
        if dag_y >= scr.h - 10:
            break

    # Activity stream at bottom
    act_y = max(dag_y + 1, scr.h - 9)
    for cx in range(2, w - 2):
        scr.put(cx, act_y - 1, "━", C["frame"])
    _text(scr, 4, act_y - 1, " ACTIVITY ", C["accent"], bold=True)

    for i, ev in enumerate(snapshot.recent_events[:7]):
        y = act_y + i
        if y >= scr.h:
            break
        ts = ev.timestamp[-8:] if len(ev.timestamp) >= 8 else ev.timestamp[:8]
        _text(scr, 3, y, ts, C["dim"])
        layer_icon = {"coordinator": "◆", "orchestrator": "◈",
                      "engineer": "⚙", "taskforce": "⛊",
                      "authority": "⬡"}.get(ev.layer, "·")
        lc = {"coordinator": C["coord"], "orchestrator": C["orch"],
              "engineer": C["eng"], "taskforce": C["tf"],
              "authority": C["auth"]}.get(ev.layer, C["dim"])
        scr.put(12, y, layer_icon[0], lc)
        summary = ev.summary[:w - 16]
        _text(scr, 14, y, summary, C["text"])

    if not snapshot.recent_events:
        _text(scr, 3, act_y, "· · · awaiting activity · · ·", C["dim"])


# ── Stream loop ───────────────────────────────────────────────────────

def stream(intelligence_path, authority_path, step_log_path=None,
           interval=0.5, once=False, ground=False, project_root=None):
    from .readers import (IntelligenceReader, AuthorityReader,
                          StepLogReader, ConfigReader, AutoSwarmReader)
    from .model import build_snapshot
    from pathlib import Path

    intel = IntelligenceReader(intelligence_path)
    auth = AuthorityReader(authority_path)
    step = StepLogReader(step_log_path) if step_log_path else None
    root = project_root or Path(intelligence_path).parent
    config = ConfigReader(root)
    # Resolve AutoSwarm home from config or environment
    autoswarm_home = os.environ.get("AUTOSWARM_HOME")
    if not autoswarm_home and config:
        autoswarm_home = config._load().get(
            "paths", {}).get("autoswarm_home")
    autoswarm = AutoSwarmReader(autoswarm_home) if autoswarm_home else None

    # Terminal size
    try:
        sz = os.get_terminal_size()
        tw, th = sz.columns, sz.lines
    except Exception:
        tw, th = 80, 40

    scr = Screen(tw, th)
    tick = 0

    try:
        if not once:
            sys.stdout.write(HIDE_CURSOR + HOME)
            sys.stdout.flush()

        while True:
            snapshot = build_snapshot(intel, auth, step_reader=step,
                                     config_reader=config,
                                     autoswarm_reader=autoswarm)
            scr.clear()
            compose(scr, snapshot, tick, routing=snapshot.routing)

            if tick == 0 or once:
                out = scr.full_render()
                sys.stdout.write(out)
                sys.stdout.flush()
            else:
                changes = scr.diff()
                out = scr.render_diff(changes)
                scr.flush(out)

            scr.swap()

            if once:
                sys.stdout.write("\n")
                if ground:
                    _ground(snapshot)
                break

            tick += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if not once:
            sys.stdout.write(SHOW_CURSOR + "\n")
            sys.stdout.flush()
        intel.close()
        auth.close()


def _ground(snapshot):
    sys.stdout.write("\033[2m=== GROUND ===\033[0m\n")
    sys.stdout.write("objectives: %d active, %d complete\n" % (
        sum(1 for o in snapshot.objectives if o.status == "ACTIVE"),
        sum(1 for o in snapshot.objectives
            if o.status in ("COMPLETE", "COMPLETED"))))
    sys.stdout.write("runs: %d  nodes: %d  CAS: %d\n" % (
        len(snapshot.runs),
        sum(len(r.nodes) for r in snapshot.runs),
        snapshot.authority.cas_object_count))
