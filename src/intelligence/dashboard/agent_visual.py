"""Agent view: one box per principal over the organization's live state,
then the work executing now, the project work graph, and one activity
stream over every layer.

Every row comes from a recorded column or from a derived rule that names
itself (the review's field map and attention rules A1-A13):

- COORDINATOR: the last admission and, derived, the words each feature
  came from, then the active objectives with their age.
- ORCHESTRATOR: who plans (the Orchestrator policy, on the Engineer's
  model, and its reasoning cycles), and each run's recorded status next
  to what its objective and work are (working, stuck, waiting, done).
- ENGINEER: the newest cycle and its age, active projects and their
  progress, proposals waiting, staffing not yet applied, and whether the
  reasoning text is stored.
- TASKFORCE: every seat with its holder, standing, record through the
  seat and without it, and who works on what now, in a seat or not.
- AUTHORITY: states by full name, attempts, receipts, verifications,
  effects, witnesses, permissions, the sequencer lease and journal head.
- EXECUTING: one row per attempt with a live session (DISPATCHED or
  RUNNING): the route it runs on, who holds it, and what its own record
  says it is doing; a session that wrote its result has a stopped clock.
  Attempts past their session or not yet started are counted.
- DAG: each live project's workstreams joined by their frozen
  predecessor edges, and each Orchestrator plan's tasks by its own
  edges, with holder, age, and why halted or blocked.
- ACTIVITY: journal transitions named by workstream or task, planning,
  workforce, cycle, admission and permission events, times to the
  millisecond; a run of identical rows is one row with a count.

A line is built from whole parts. A part that does not fit moves to an
indented continuation line; only a part wider than the space left ends
in "…", and a list that does not fit ends in "+N more". From 200 columns
the four boxes stand in one row. The terminal runs in the alternate
screen with autowrap off; its size is re-read every tick, a resize
repaints the whole frame (and the next one), and so does every tenth
second.
"""

import os
import shutil
import sys
import time

from .cli_stream import (
    _parse_utc, _when, _precise, _seconds, _dur, _day, OUTCOME_LABEL,
    route_desc, seat_routes, earlier_groups)
from .model import SEQUENCE_TERMINAL
from .readers import ATTEMPT_TERMINAL
from .screen import (
    Screen, HIDE_CURSOR, SHOW_CURSOR, ALT_SCREEN_ON, ALT_SCREEN_OFF,
    CLEAR_SCREEN, WRAP_OFF, WRAP_ON, ALT_SCROLL_ON, ALT_SCROLL_OFF)

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

# Agent identities
AGENT = {
    "coord": ("◆", "COORDINATOR",  C["coord"]),
    "orch":  ("◈", "ORCHESTRATOR", C["orch"]),
    "eng":   ("⚙", "ENGINEER",     C["eng"]),
    "tf":    ("⛊", "TASKFORCE",    C["tf"]),
    "auth":  ("⬡", "AUTHORITY",    C["auth"]),
}

LAYER = {
    "coordinator": ("◆", C["coord"]), "orchestrator": ("◈", C["orch"]),
    "engineer": ("⚙", C["eng"]), "taskforce": ("⛊", C["tf"]),
    "authority": ("⬡", C["auth"]),
}

ROLE_ICON = {"investigator": "⊙", "builder": "⊕", "reviewer": "⊘"}
ROLE_SHORT = {"investigator": "inv", "builder": "bld", "reviewer": "rev"}
MODE_SHORT = {"DIRECT": "DIR", "ENGINEER_ELEVATED": "ENG",
              "ORCHESTRATOR_ELEVATED": "ORC"}
MODE_COLOR = {"DIRECT": C["dir"], "ENGINEER_ELEVATED": C["eng"],
              "ORCHESTRATOR_ELEVATED": C["orch"]}
SEAT_STATUS = {"ESTABLISHED": "EST", "RETAINED": "RET",
               "RELEASED": "RELEASED"}
HOW = {"RETAINED_SEAT": "seat", "OUTSIDE_SPECIALIST": "outside"}
_HELD = ("ESTABLISHED", "RETAINED")

EFFECTIVE = {  # a run's derived state: symbol, color, sort order
    "working": ("▶", C["accent"], 0), "stuck": ("✗", C["halted"], 1),
    "waiting": ("◫", C["blocked"], 2), "unknown": ("?", C["dim"], 3),
    "done": ("✦", C["success"], 4),
}

_COMPLETE = ("COMPLETE", "COMPLETED")
_BLOCKED = ("BLOCKED", "BLOCKED_UNSUPPORTED_FINALIZER")
_QUEUED = ("DECLARED", "READY")


def _state_mark(state):
    """Symbol and color of a work item's state: its sequence's, or, for
    an Orchestrator task with no sequence yet, PLANNED or DISPATCHING."""
    if not state:
        return "·", C["dim"]
    if state.startswith("HALTED"):
        return "✗", C["halted"]
    if state in _BLOCKED:
        return "◫", C["blocked"]
    if state == "COMPLETED":
        return "✦", C["success"]
    if state == "FAILED":
        return "!", C["error"]
    if state in SEQUENCE_TERMINAL or state == "CANCELLED":
        return "·", C["dim"]
    if state in _QUEUED or state == "PLANNED":
        return "○", C["dim"]
    return "▶", C["accent"]


def _short(text, width):
    """``text`` cut to ``width`` characters, a cut ending in "…"."""
    text = text or ""
    if width <= 0 or len(text) <= width:
        return text
    return text[:max(width - 1, 0)] + "…"


# ── segments, parts and lines ─────────────────────────────────────────
#
# A segment is (text, color, bold). A part is a list of segments that is
# never split across rows. A line is a list of parts; it wraps between
# parts.

def S(text, color=None, bold=False):
    return (text, C["text"] if color is None else color, bold)


def P(*segs):
    return [s for s in segs if s and s[0]]


def L(*parts):
    return [p for p in parts if p]


def _width(segs):
    return sum(len(t) for t, _, _ in segs)


def _clip(segs, width):
    """``segs`` cut to ``width`` cells; a cut ends in "…"."""
    if width <= 0:
        return []
    if _width(segs) <= width:
        return segs
    out, used, room = [], 0, width - 1
    for text, color, bold in segs:
        if used >= room:
            break
        take = text[:room - used]
        out.append((take, color, bold))
        used += len(take)
    out.append(("…", C["dim"], False))
    return out


def _lstrip_part(part):
    """A part moved to a continuation line loses its leading separator."""
    out, stripping = [], True
    for text, color, bold in part:
        if stripping:
            text = text.lstrip()
            if text.startswith("· "):
                text = text[2:]
            if not text:
                continue
            stripping = False
        out.append((text, color, bold))
    return out


def _wrap(line, width, indent=4):
    """One line's parts packed into rows of ``width`` cells."""
    rows, row, used = [], [], 0
    for part in line:
        w = _width(part)
        if not row or used + w <= width:
            row.extend(part)
            used += w
            continue
        rows.append(row)
        part = _lstrip_part(part)
        row = [S(" " * indent)] + part
        used = indent + _width(part)
    if row:
        rows.append(row)
    return rows


def _layout(lines, width, indent=4):
    out = []
    for line in lines:
        out.extend(_wrap(line, width, indent))
    return out


def _paint(scr, x, y, segs, width):
    cx = x
    for text, color, bold in _clip(segs, width):
        for ch in text:
            scr.put(cx, y, ch, color, bold=bold)
            cx += 1


def _ago(ts, now):
    seconds = _seconds(ts, now)
    return "?" if seconds is None else _dur(seconds) + " ago"


def _more(rows, limit):
    """At most ``limit`` rows, the last replaced by "+N more" when cut."""
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return rows
    keep = rows[:limit - 1]
    return keep + [[S("  + %d more rows" % (len(rows) - len(keep)),
                      C["dim"])]]


def _sep_parts(groups, first="", sep=" · ", color=None):
    """Parts from segment groups, each led by ``sep`` (the first by
    ``first``)."""
    parts = []
    for i, group in enumerate(g for g in groups if g):
        lead = S(first if i == 0 else sep,
                 C["dim"] if color is None else color)
        parts.append([lead] + list(group) if lead[0] else list(group))
    return parts


# ── header ────────────────────────────────────────────────────────────

ATTENTION_WORD = {
    "A1": "halted", "A2": "integrity failed", "A3": "effects unknown",
    "A4": "driver stopped", "A5": "blocked behind a halt",
    "A6": "waiting",
}


def _attention_parts(snapshot):
    counts = {}
    for a in snapshot.attention:
        if a.rule in ATTENTION_WORD:
            counts[a.rule] = counts.get(a.rule, 0) + 1
    if not counts:
        return [P(S("no attention needed", C["dim"]))]
    groups = []
    for rule in sorted(counts, key=lambda r: int(r[1:])):
        critical = rule in ("A1", "A2", "A3", "A4")
        groups.append([S("%d %s" % (counts[rule], ATTENTION_WORD[rule]),
                         C["error"] if critical else C["warn"], critical)])
    parts = _sep_parts(groups)
    parts[0] = [S("⚑ ", C["error"])] + parts[0]
    return parts


def _header_parts(snapshot, now, wide=True):
    lv = snapshot.liveness
    parts = [P(S("AUTOBOT ORGANIZATION" if wide else "AUTOBOT",
                 C["bright"], True)),
             P(S("   " + now.strftime(
                 "%Y-%m-%d %H:%M:%S UTC" if wide else "%H:%M:%S UTC"),
                 C["dim"]))]
    if lv.driver_step_utc:
        expires = _parse_utc(lv.driver_expires_utc)
        live = expires is not None and expires >= now
        parts.append(P(S("   driver "),
                       S("●" if live else "○",
                         C["success"] if live else C["warn"]),
                       S(" step %s" % _ago(lv.driver_step_utc, now)),
                       S(" · lease expired", C["warn"]) if not live
                       else None))
    else:
        # the driver deletes its lease when it stops
        parts.append(P(S("   driver ○ not running", C["dim"])))
    if lv.journal_seq is not None:
        parts.append(P(S("   journal #%d · %s" % (
            lv.journal_seq, _ago(lv.journal_utc, now)), C["dim"])))
    # last accepted work: newest objective completion
    completed = [o for o in snapshot.objectives
                 if o.completed_utc and o.status in _COMPLETE]
    if completed:
        newest = max(completed, key=lambda o: o.completed_utc)
        parts.append(P(S("   last accepted work %s"
                         % _ago(newest.completed_utc, now), C["dim"])))
    return parts, _attention_parts(snapshot)


# ── boxes ─────────────────────────────────────────────────────────────

def _box(scr, x, y, w, h, agent_key):
    icon, label, color = AGENT[agent_key]
    scr.put(x, y, "╔", color)
    scr.put(x + 1, y, "═", color)
    scr.put(x + 2, y, " ", color)
    scr.put(x + 3, y, icon, color)
    for i, ch in enumerate(label):
        scr.put(x + 5 + i, y, ch, color, bold=True)
    for cx in range(x + 5 + len(label), x + w - 1):
        scr.put(cx, y, "═", color)
    scr.put(x + w - 1, y, "╗", color)
    for ry in range(y + 1, y + h - 1):
        scr.put(x, ry, "║", C["frame"])
        scr.put(x + w - 1, ry, "║", C["frame"])
    scr.put(x, y + h - 1, "╚", color)
    for cx in range(x + 1, x + w - 1):
        scr.put(cx, y + h - 1, "═", color)
    scr.put(x + w - 1, y + h - 1, "╝", color)


def _draw_box(scr, x, y, w, h, key, rows, offset=0, focused=False):
    _box(scr, x, y, w, h, key)
    if focused:
        for ry in range(y + 1, y + h - 1):
            scr.put(x, ry, "║", C["accent"])
            scr.put(x + w - 1, ry, "║", C["accent"])
    visible = h - 2
    if offset > 0 and len(rows) > visible:
        offset = min(offset, max(0, len(rows) - visible))
        window = rows[offset:]
    else:
        window = rows
    for i, segs in enumerate(_more(window, visible)):
        _paint(scr, x + 2, y + 1 + i, segs, w - 4)


def _active_objectives(snapshot):
    active = [o for o in snapshot.objectives if o.status not in _COMPLETE]
    active.sort(key=lambda o: o.created_utc or "", reverse=True)
    return active


def _coord_lines(snapshot, now):
    active = _active_objectives(snapshot)
    done = len(snapshot.objectives) - len(active)
    decisions = snapshot.coordinator_decisions
    modes = {}
    policies = {}
    routes = {}
    for cd in decisions:
        modes[cd.elevation_mode] = modes.get(cd.elevation_mode, 0) + 1
        if cd.policy_id:
            policies[cd.policy_id] = policies.get(cd.policy_id, 0) + 1
        if cd.route:
            routes[cd.route] = routes.get(cd.route, 0) + 1
    lines = [L(P(S("%d decisions" % len(decisions), C["bright"], True),
                 S(" · %d active" % len(active), C["accent"]),
                 S(" · %d complete" % done, C["dim"])),
               *_sep_parts(
            [[S("%s %d" % (MODE_SHORT.get(m, m[:3]), n),
                MODE_COLOR.get(m, C["dim"]), True)]
             for m, n in sorted(modes.items())], first="  ", sep="  "))]
    if policies:
        lines.append(L(P(S("policy ", C["dim"])),
                       *_sep_parts(
            [[S("%s ×%d" % (k, n), C["dim"])]
             for k, n in sorted(policies.items())], first="", sep=" · ")))
    if routes:
        lines.append(L(P(S("routes ", C["dim"])),
                       *_sep_parts(
            [[S("%s ×%d" % (k, n), C["luna"])]
             for k, n in sorted(routes.items())], first="", sep=" · ")))
    decision = {cd.objective_id: cd for cd in decisions}
    working = [o for o in active if o.work_state == "working"]
    stale_obj = [o for o in active
                 if o.work_state in ("work finished", None)
                 or o.work_state == ""]
    if len(active) != len(working):
        lines[0] = L(P(S("%d decisions" % len(decisions), C["bright"], True),
                       S(" · %d active" % len(active), C["accent"]),
                       S(" (%d working, %d stale)" % (
                           len(working), len(stale_obj)), C["dim"])
                       if stale_obj else None,
                       S(" · %d complete" % done, C["dim"])),
                     *_sep_parts(
            [[S("%s %d" % (MODE_SHORT.get(m, m[:3]), n),
                MODE_COLOR.get(m, C["dim"]), True)]
             for m, n in sorted(modes.items())], first="  ", sep="  "))
    for o in active:
        cd = decision.get(o.objective_id)
        mode = cd.elevation_mode if cd else "?"
        feature_parts = []
        if cd and cd.features:
            for fk, fv in sorted(cd.features.items()):
                if fv:
                    feature_parts.append(fk)
        feature_str = (" [%s]" % ", ".join(feature_parts)
                       if feature_parts else "")
        route_str = ""
        if cd and cd.route:
            route_str = " → %s" % cd.route
        work_label = ""
        if o.work_state:
            wcolor = (C["accent"] if o.work_state == "working"
                      else C["halted"] if o.work_state == "HALTED"
                      else C["warn"] if o.work_state in (
                          "blocked", "queued")
                      else C["dim"])
            work_label_text = o.work_state
            if o.work_note:
                work_label_text = "%s: %s" % (o.work_state, o.work_note)
        else:
            wcolor = C["dim"]
            work_label_text = "no work recorded"
        lines.append(L(
            P(S("● ", C["active"]), S(o.objective_id, C["bright"]),
              S("  %s" % MODE_SHORT.get(mode, mode[:3]),
                MODE_COLOR.get(mode, C["dim"]), True),
              S(route_str, C["luna"]),
              S(feature_str, C["dim"]),
              S("  %s" % _dur(_seconds(o.created_utc, now)), C["dim"])),
            P(S("  %s" % work_label_text, wcolor))))
    stamped = [cd for cd in decisions if cd.created_utc]
    if stamped:
        last = max(stamped, key=lambda cd: cd.created_utc)
        lines.append(L(
            P(S("last ", C["dim"]),
              S(last.objective_id, C["bright"]),
              S(" %s" % MODE_SHORT.get(last.elevation_mode,
                                       last.elevation_mode),
                MODE_COLOR.get(last.elevation_mode, C["dim"]), True),
              S(" %s" % _ago(last.created_utc, now), C["dim"]))))
    return lines


def _effective_of(run):
    return EFFECTIVE.get(run.effective_state or "unknown",
                         EFFECTIVE["unknown"])


def _effective_groups(runs):
    counts, mark = {}, {}
    for r in runs:
        k = r.effective_state or "unknown"
        if k == "waiting" and r.effective_note:
            k = "waiting (%s)" % r.effective_note.split(":")[0].strip()
        counts[k] = counts.get(k, 0) + 1
        mark[k] = _effective_of(r)
    return [[S("%d %s" % (counts[k], k), mark.get(k, mark.get(
                k.split(" (")[0], EFFECTIVE["unknown"]))[1])]
            for k in sorted(counts, key=lambda k: mark.get(k, mark.get(
                k.split(" (")[0], EFFECTIVE["unknown"]))[2])]


def _orch_lines(snapshot, now, narrow=False):
    runs = list(snapshot.runs)
    if not runs:
        return [L(P(S("no orchestration runs", C["dim"])))]
    active_obj = {o.objective_id for o in snapshot.objectives
                  if o.status not in _COMPLETE}
    operational = [r for r in runs if r.objective_id in active_obj]
    stale_runs = [r for r in runs
                  if r.status == "ACTIVE"
                  and r.objective_id not in active_obj]
    total_nodes = sum(len(r.nodes) for r in runs)
    lines = [L(P(S("runs: %d total" % len(runs), C["bright"], True),
                 S(" · %d operational" % len(operational), C["accent"]),
                 S(" · %d stale" % len(stale_runs), C["warn"])
                 if stale_runs else None,
                 S(" · %d nodes" % total_nodes, C["dim"])),
               *_sep_parts(_effective_groups(runs), first=" → "))]
    o = snapshot.orchestrator
    if o is not None and o.cycles:
        lines.append(L(
            P(S("plans on ", C["dim"]), S(o.model or "?", C["luna"])),
            P(S(" · %s" % o.policy_id, C["dim"])),
            P(S(" · %d cycle%s" % (o.cycles, "" if o.cycles == 1 else "s"),
                C["dim"])),
            P(S(" · last %s (%s)" % (_when(o.last_utc, now),
                                     _ago(o.last_utc, now)), C["dim"]))
            if o.last_utc else None))
        bd = o.decision_breakdown
        if bd:
            parts = []
            for status in ("APPLIED", "CONVERGED", "SUPERSEDED",
                           "SKIPPED", "REJECTED", "FAILED", "INVALID"):
                n = bd.get(status, 0)
                if n:
                    color = (C["success"] if status == "APPLIED"
                             else C["dim"] if status in (
                                 "CONVERGED", "SUPERSEDED")
                             else C["warn"])
                    parts.append([S("%s %d" % (status.lower(), n), color)])
            if parts:
                lines.append(L(
                    P(S("decisions %d " % o.decisions, C["dim"])),
                    *_sep_parts(parts, first="", sep=" · ")))
        elif o.not_applied and narrow:
            lines.append(L(P(S("not applied: %d of %d decisions" % (
                sum(o.not_applied.values()), o.decisions), C["warn"]))))
        elif o.not_applied:
            lines.append(L(P(S("not applied: ", C["warn"])), *_sep_parts(
                [[S("%s ×%d" % (k, n), C["warn"])]
                 for k, n in sorted(o.not_applied.items())])))
    runs.sort(key=lambda r: r.created_utc or "", reverse=True)
    runs.sort(key=lambda r: _effective_of(r)[2])
    done = [r for r in runs if r.effective_state == "done"]
    shown = [r for r in runs if r.effective_state != "done"]
    ow = min(24 if narrow else 60,
             max(len(r.objective_id) for r in (shown or runs)))
    stale = any(r.status == "ACTIVE" and r.effective_state in ("done",
                                                               "stuck")
                for r in runs)
    for r in shown:
        sym, color, _ = _effective_of(r)
        since = r.effective_since_utc
        state = r.effective_state or "?"
        if r.effective_state == "working":
            note = r.effective_note or "in progress"
        elif r.effective_state == "stuck":
            note = "HALTED %s" % _when(since, now) if since else "HALTED"
        elif r.effective_state == "done":
            note = _when(since, now) if since else ""
        else:
            note = r.effective_note or ""
        head = [P(S(sym + " ", color),
                  S("%s " % MODE_SHORT.get(r.mode, r.mode[:3]),
                    MODE_COLOR.get(r.mode, C["dim"]), True),
                  S(_short(r.objective_id, ow).ljust(ow)))]
        if narrow and r.effective_state == "stuck":
            head.append(P(S("  " + note, color)))
        else:
            head.append(P(S("  %s" % state, color)))
            if note:
                head.append(P(S(" · " + note, C["dim"])))
        # node summary for this run
        if r.nodes:
            ns = {}
            for n in r.nodes:
                ns[n.status] = ns.get(n.status, 0) + 1
            node_str = " ".join("%d%s" % (v, k[0])
                                for k, v in sorted(ns.items()))
            head.append(P(S(" [%s]" % node_str, C["dim"])))
        else:
            head.append(P(S(" · 0 nodes", C["dim"])))
        lines.append(L(*head))
        # expand nodes for non-done runs (up to 8)
        nkw = 24 if narrow else 50
        if r.nodes and r.effective_state != "done":
            for n in r.nodes[:8]:
                nsym, ncolor = _state_mark(n.status)
                binding = ""
                if n.sequence_id:
                    binding = " seq=%s [%s]" % (
                        n.sequence_id, n.binding_status or "?")
                kind_str = ""
                if n.realization_kind:
                    kind_str = " %s" % n.realization_kind
                cap_str = ""
                if n.capability:
                    cap_str = " %s" % n.capability
                wp_str = ""
                if n.work_profile:
                    wp_str = "/%s" % n.work_profile
                specialist = ""
                if n.specialist_handle:
                    specialist = " · %s" % n.specialist_handle
                output = ""
                if n.output_kind:
                    output = " → %s" % n.output_kind
                lines.append(L(
                    P(S("  %s " % nsym, ncolor),
                      S(_short(n.node_key, nkw), C["bright"] if
                        n.status not in ("COMPLETED", "CANCELLED",
                                         "SUPERSEDED") else C["dim"])),
                    P(S(kind_str, C["dim"]),
                      S(cap_str + wp_str, C["dim"]),
                      S(specialist, C["accent"]),
                      S(output, C["success"] if n.output_kind else C["dim"]),
                      S(binding, C["accent"] if n.binding_status == "ACTIVE"
                        else C["dim"]))))
            if len(r.nodes) > 8:
                lines.append(L(P(S("  + %d more nodes" % (
                    len(r.nodes) - 8), C["dim"])))
                )
        # edges for non-done runs
        if r.edges and r.effective_state != "done" and not narrow:
            ekw = 16 if narrow else 40
            node_keys = {n.node_id: n.node_key for n in r.nodes}
            for e in r.edges[:6]:
                src = node_keys.get(e.source_node_id,
                                    e.source_node_id[:12])
                tgt = node_keys.get(e.target_node_id,
                                    e.target_node_id[:12])
                lines.append(L(
                    P(S("  ╰ ", C["flow_dim"]),
                      S("%s → %s" % (
                          _short(src, ekw), _short(tgt, ekw)), C["dim"]),
                      S(" %s" % e.dependency_kind, C["dim"]),
                      S(" [%s]" % e.status,
                        C["success"] if e.status == "RELEASED"
                        else C["dim"]))))
            if len(r.edges) > 4:
                lines.append(L(P(S("  + %d more edges" % (
                    len(r.edges) - 4), C["dim"]))))
    if done:
        newest = max(done, key=lambda r: r.effective_since_utc or "")
        lines.append(L(P(S("✦ ", C["success"]),
                         S("%d done" % len(done), C["success"])),
                       P(S(" · newest %s %s" % (
                           newest.objective_id,
                           _when(newest.effective_since_utc, now)),
                           C["dim"]))))
    if stale:
        lines.append(L(P(S("recorded ACTIVE until an Orchestrator "
                           "COMPLETE_RUN (O2)", C["dim"]))))
    return lines


def _eng_lines(snapshot, now, narrow=False):
    e = snapshot.engineer
    t = snapshot.trust
    lines = []
    planner = snapshot.routing.planner_model
    if e.cycles:
        c = e.cycles[0]
        is_open = c.status == "OPEN"
        line = L(
            P(S("cycle %s " % c.cycle_id, C["bright"], True),
              S(c.status, C["accent"] if is_open else C["dim"], is_open)),
            P(S(" %s" % _ago(c.started_utc, now), C["dim"])),
            P(S(" · %d proposal%s" % (c.proposals,
                                      "" if c.proposals == 1 else "s"))))
        if planner and planner != "?":
            line.append(P(S(" · %s" % planner, C["luna"])))
        if e.decisions_stored:
            line.append(P(S(" · %d/%d reasoning stored"
                            % (e.decisions_stored, e.decisions_total),
                            C["dim"])))
        lines.append(line)
    else:
        lines.append(L(P(S("no cycles recorded", C["dim"]))))

    if e.engineering_decisions:
        for d in e.engineering_decisions[-3:]:
            adopted = "adopted" if d.adopted_utc else "pending"
            lines.append(L(
                P(S("  eng ", C["dim"])),
                P(S(d.request_id[:20], C["bright"])),
                P(S(" %s" % adopted,
                    C["success"] if d.adopted_utc else C["warn"])),
                P(S(" %s" % _ago(d.adopted_utc or d.created_utc, now),
                    C["dim"]))))

    # Proposal breakdown
    if e.recent_proposals:
        by_class = {}
        for p in e.recent_proposals:
            by_class[p.proposal_class] = by_class.get(
                p.proposal_class, 0) + 1
        lines.append(L(P(S("recent ", C["dim"])),
                       *_sep_parts(
            [[S("%s ×%d" % (k, n), C["accent"])]
             for k, n in sorted(by_class.items())], first="", sep=" · ")))

    # Findings summary
    findings = t.findings_by_significance
    if findings:
        total_f = sum(findings.values())
        f_parts = []
        for sig in ("high", "medium", "low"):
            if sig in findings:
                color = (C["error"] if sig == "high" else
                         C["warn"] if sig == "medium" else C["dim"])
                f_parts.append([S("%d %s" % (findings[sig], sig), color)])
        lines.append(L(P(S("findings %d " % total_f, C["dim"])),
                       *_sep_parts(f_parts, first="", sep=" · ")))

    # Projects
    projects = [p for p in snapshot.projects if p.status == "ACTIVE"]
    with_work = [p for p in projects if p.items]
    closed = len(snapshot.projects) - len(projects)
    lines.append(L(P(S("projects %d active" % len(projects), C["dim"]),
                     S(" · %d closed" % closed, C["dim"]))))
    for p in sorted(with_work, key=lambda p: p.opened_utc or "",
                    reverse=True):
        done = sum(1 for it in p.items if it.state == "COMPLETED")
        total = len(p.items)
        halted = [it for it in p.items
                  if it.state and it.state.startswith("HALTED")]
        superseded = [it for it in p.items if it.state == "SUPERSEDED"]
        status_parts = "%d/%d" % (done, total)
        if halted:
            status_parts += " %d HALTED" % len(halted)
        if superseded:
            status_parts += " %d SUPERSEDED" % len(superseded)
        lines.append(L(
            P(S("  %s " % p.project_id[:7], C["dim"]),
              S(_short(p.title or "(untitled)", 40 if narrow else 50)),
              S("  %s" % status_parts,
                C["success"] if total and done == total
                else C["halted"] if halted else C["dim"]))))
        # closure blocker: show why project can't complete
        if not narrow and (halted or superseded) and done < total:
            for it in superseded:
                replacement = None
                for other in p.items:
                    if (other.state == "COMPLETED"
                            and other.workstream_key.startswith(
                                it.workstream_key.rsplit("-", 1)[0]
                                if "-" in it.workstream_key
                                else it.workstream_key)
                            and other.workstream_key != it.workstream_key):
                        replacement = other
                        break
                if replacement:
                    lines.append(L(
                        P(S("    ⚑ ", C["warn"]),
                          S(it.workstream_key, C["halted"]),
                          S(" SUPERSEDED → ", C["dim"]),
                          S(replacement.workstream_key, C["success"]),
                          S(" COMPLETED", C["success"]))))
                else:
                    lines.append(L(
                        P(S("    ⚑ ", C["warn"]),
                          S(it.workstream_key, C["halted"]),
                          S(" SUPERSEDED, authority resolution needed",
                            C["warn"]))))
            for it in halted:
                cause = it.halt_cause or it.halt_reason or ""
                lines.append(L(
                    P(S("    ⚑ ", C["warn"]),
                      S(it.workstream_key, C["halted"]),
                      S(" HALTED", C["halted"]),
                      S(" · %s" % cause, C["dim"]) if cause else None)))
    empty = len(projects) - len(with_work)
    if empty:
        lines.append(L(P(S("  + %d empty" % empty, C["dim"]))))

    # Blocked / waiting
    if e.waiting or e.staffing_open:
        parts = []
        if e.waiting:
            counts = {}
            for p in e.waiting:
                counts[p.proposal_class] = counts.get(
                    p.proposal_class, 0) + 1
            for k, n in sorted(counts.items()):
                parts.append([S("%s ×%d" % (k, n), C["warn"])])
        if e.staffing_open:
            parts.append([S("staffing ×%d" % len(e.staffing_open),
                            C["accent"])])
        lines.append(L(P(S("blocked ", C["warn"])),
                       *_sep_parts(parts, first="", sep=" · ")))
        # Expand waiting proposals with rationale
        if e.waiting and not narrow:
            for p in e.waiting[:4]:
                lines.append(L(
                    P(S("  ⚑ ", C["warn"]),
                      S(p.proposal_class, C["warn"]),
                      S(" obj=%s" % (p.objective_id or "?"), C["dim"])),
                    P(S(" %s" % _short(p.rationale or "", 60),
                        C["dim"]))))
    return lines


def _qn(q, n):
    parts = []
    if q:
        parts.append("%dQ" % q)
    if n:
        parts.append("%dNQ" % n)
    return " / ".join(parts)


def _working_now(snapshot):
    """(handle, work, how) for every live session, seat or not; a seat's
    in-flight assignment without a session counts too."""
    out, seen = [], set()
    for s in snapshot.sessions:
        if s.session_state != "running" or not s.specialist_handle:
            continue
        how = "" if s.selection_path == "RETAINED_SEAT" else HOW.get(
            s.selection_path, "")
        work = s.workstream_key or s.sequence_id[:16]
        out.append((s.specialist_handle, work, how))
        seen.add((s.specialist_handle, work))
    for seat in snapshot.taskforce.seats:
        for work in seat.current_list or (
                [seat.current_work] if seat.current_work else []):
            if (seat.specialist_handle, work) not in seen:
                out.append((seat.specialist_handle, work, ""))
                seen.add((seat.specialist_handle, work))
    return out


def _tf_lines(snapshot, now, narrow=False):
    tf = snapshot.taskforce
    counts = []
    if tf.established_count:
        counts.append("%d EST" % tf.established_count)
    if tf.retained_count:
        counts.append("%d RET" % tf.retained_count)
    outside_n = len(tf.outside_pool)
    lines = [L(P(S("%d members" % tf.member_count, C["bright"], True),
                 S(" · " + " · ".join(counts) if counts else "", C["dim"])),
               P(S(" · %d outside" % outside_n, C["dim"]))
               if outside_n else None)]
    main, on = seat_routes(tf.seats)
    if main:
        sample = next(s for s in tf.seats if (
            s.route_name or "%s/%s" % (s.provider or "?", s.model or "?"))
            == main)
        held = any(s.status in _HELD for s in tf.seats)
        lead = ("specialists run on " if not held else
                "every seat runs on " if len(on) == 1 else "seats run on ")
        line = L(P(S(lead, C["dim"])), P(S(route_desc(
            sample.route_name, snapshot.routing, sample.provider,
            sample.model), C["luna"])))
        escalation = snapshot.routing.escalation_route
        if escalation and escalation != "?":
            line.append(P(S(" · escalation %s" % escalation, C["dim"])))
        lines.append(line)
    working = _working_now(snapshot)
    busy_handles = {handle for handle, _, _ in working}
    if working:
        lines.append(L(P(S("working now ", C["dim"])), *_sep_parts(
            [[S(handle, C["bright"]),
              S(" → %s" % _short(work, 36), C["accent"]),
              S(" (%s)" % how if how else "", C["dim"])]
             for handle, work, how in working])))
    else:
        lines.append(L(P(S("working now ", C["dim"]),
                         S("nobody", C["dim"]))))
    def label(s):
        return "%s/%s" % (s.capability, ROLE_SHORT.get(s.role, s.role))

    held = [s for s in tf.seats if s.status in _HELD]
    released = [s for s in tf.seats if s.status not in _HELD]
    held.sort(key=lambda s: (0 if s.status == "ESTABLISHED" else 1,
                             s.capability))
    if held:
        cw = min(22, max(len(label(s)) for s in held))
        hw = min(18, max(len(s.specialist_handle) for s in held))
    for s in held:
        busy = bool(s.current_work) or s.specialist_handle in busy_handles
        record = _qn(s.seat_qualifying, s.seat_not_qualifying) or "-"
        route = s.route_name or ("%s/%s" % (s.provider or "?",
                                             s.model or "?"))
        lines.append(L(
            P(S(("▶" if busy else ROLE_ICON.get(s.role, "·")) + " ",
                C["accent"] if busy else C["tf"]),
              S(label(s)[:cw].ljust(cw))),
            P(S(" " + s.specialist_handle[:hw].ljust(hw), C["bright"])),
            P(S(" %s" % SEAT_STATUS.get(s.status, s.status[:3]),
                C["dim"])),
            P(S(" " + record, C["success"])),
            P(S(" on %s" % route, C["luna"]))))
    if released:
        total_q = sum(s.seat_qualifying or 0 for s in released)
        lines.append(L(P(S("%d released seat%s" % (
            len(released), "" if len(released) == 1 else "s"), C["dim"]),
            S(" · %dQ history" % total_q, C["dim"]) if total_q else None)))
    return lines


def _census_groups(census, terminal, open_color):
    """``census`` as groups: open states first, in color."""
    groups = []
    for s in sorted(x for x in census if x not in terminal):
        color = (C["halted"] if s.startswith("HALTED") else C["blocked"]
                 if s in _BLOCKED else open_color)
        groups.append([S("%s %d" % (s, census[s]), color, True)])
    for s in sorted(x for x in census if x in terminal):
        groups.append([S("%s %d" % (s, census[s]), C["dim"])])
    return groups


def _counts(census):
    return ", ".join("%d %s" % (n, k) for k, n in sorted(census.items()))


def _auth_lines(snapshot, now):
    a = snapshot.authority
    t = snapshot.trust
    lv = snapshot.liveness
    seqs = a.sequences_by_state
    total = sum(seqs.values())
    finished = sum(n for s, n in seqs.items() if s in SEQUENCE_TERMINAL)
    lines = []

    # provenance first — the trust chains operators need most
    prov = [p for p in snapshot.sequence_provenance
            if p.state not in ("COMPLETED", "SUPERSEDED", "ABANDONED",
                               "DELEGATION_REVOKED", "PUBLISHED_UNVERIFIED")]
    if not prov:
        prov = snapshot.sequence_provenance[-5:]
    else:
        prov = prov[:8]
    if prov:
        lines.append(L(P(S("provenance:", C["dim"]))))
        for pi, p in enumerate(prov):
            name = p.workstream_key or p.sequence_id[:20]
            is_last = pi == len(prov) - 1
            branch = "└─ " if is_last else "├─ "
            sym, color = _state_mark(p.state)
            lines.append(L(P(
                S("  %s" % branch, C["frame"]),
                S("%s " % sym, color),
                S(name, C["bright"]),
                S("  %s" % p.state, color))))
            indent = "     " if is_last else "  │  "
            proposed = "cycle %d" % p.cycle_id if p.cycle_id else "?"
            lines.append(L(P(
                S("%s├─ " % indent, C["frame"]),
                S("proposed by ", C["dim"]),
                S(proposed, C["accent"]),
                S("  %s" % p.proposal_class if p.proposal_class else "",
                  C["dim"]))))
            assigned = p.specialist_handle or "unassigned"
            path = p.selection_path or ""
            lines.append(L(P(
                S("%s├─ " % indent, C["frame"]),
                S("assigned  ", C["dim"]),
                S(assigned, C["accent"]),
                S("  %s" % path if path else "", C["dim"]),
                S("  decided by %s" % p.decided_by if p.decided_by
                  else "", C["dim"]))))
            if p.provider:
                lines.append(L(P(
                    S("%s├─ " % indent, C["frame"]),
                    S("route     ", C["dim"]),
                    S("%s/%s" % (p.provider, p.model or "?"), C["luna"]),
                    S("  %s/%s" % (p.capability or "?", p.role or "?"),
                      C["dim"]))))
            if p.projection_address:
                lines.append(L(P(
                    S("%s├─ " % indent, C["frame"]),
                    S("prompt    ", C["dim"]),
                    S(p.projection_address, C["dim"]))))
            if p.assignment_outcome:
                lines.append(L(P(
                    S("%s└─ " % indent, C["frame"]),
                    S("outcome   ", C["dim"]),
                    S(p.assignment_outcome,
                      C["success"] if "ACCEPT" in (
                          p.assignment_outcome or "") else C["halted"]))))
            else:
                lines.append(L(P(
                    S("%s└─ " % indent, C["frame"]),
                    S("outcome   ", C["dim"]),
                    S("pending", C["warn"]))))

    # machine state
    lines.append(L(P(S("sequences %d/%d finished" % (finished, total),
                    C["bright"])),
               *_sep_parts(_census_groups(seqs, SEQUENCE_TERMINAL,
                                          C["accent"]), first="  ")))
    attempts = _census_groups(a.attempts_by_state, ATTEMPT_TERMINAL,
                              C["accent"])
    lines.append(L(P(S("attempts  ", C["dim"])),
                   *(_sep_parts(attempts) if attempts
                     else [P(S("none", C["dim"]))])))
    trust = []
    if t.receipts_by_completeness:
        for k, n in sorted(t.receipts_by_completeness.items()):
            bad = k != "COMPLETE"
            trust.append([S("receipts " if not trust else "", C["dim"]),
                          S("%d %s" % (n, k), C["error"] if bad else C["text"],
                            bad), S(" ⚑" if bad else "", C["error"])])
    if t.verifications_by_outcome:
        trust.append([S("verifications ", C["dim"]),
                      S(_counts(t.verifications_by_outcome))])
    if t.effects_by_state:
        trust.append([S("effects ", C["dim"]), S(
            _counts(t.effects_by_state),
            C["error"] if "UNKNOWN_OUTCOME" in t.effects_by_state
            else C["text"])])
    trust.append([S("witnesses %d" % t.witnesses, C["dim"])])
    lines.append(L(*_sep_parts(trust)))
    org = []
    if t.approvals_by_decision:
        org.append([S("permissions ", C["dim"]),
                    S(_counts(t.approvals_by_decision))])
    if t.demonstrations_by_outcome:
        org.append([S("demonstrations ", C["dim"]),
                    S(_counts(t.demonstrations_by_outcome))])
    if t.findings_by_significance:
        org.append([S("findings %d" % sum(
            t.findings_by_significance.values()), C["dim"])])
    org.append([S("CAS %d objects" % a.cas_object_count, C["dim"])])
    org.append([S("%d dependency releases" % a.released_edge_count,
                  C["dim"])])
    lines.append(L(*_sep_parts(org)))
    if lv.sequencer_epoch is not None:
        expires = _parse_utc(lv.sequencer_expires_utc)
        left = (expires - now).total_seconds() if expires else None
        lease = ("lease until %s (%s left)" % (
            _when(lv.sequencer_expires_utc, now), _dur(left))
            if left is not None and left >= 0 else "lease expired %s" % (
                _when(lv.sequencer_expires_utc, now)))
        lines.append(L(
            P(S("sequencer epoch %d · %s" % (lv.sequencer_epoch, lease),
                C["dim"])),
            P(S(" · journal #%d at %s (%s)" % (
                lv.journal_seq, _when(lv.journal_utc, now),
                _ago(lv.journal_utc, now)), C["dim"]))
            if lv.journal_seq is not None else None))
    # health: actionable authority state
    health = []
    halted_n = seqs.get("HALTED", 0)
    if halted_n:
        health.append([S("%d HALTED" % halted_n, C["halted"], True)])
    blocked_n = sum(seqs.get(s, 0) for s in _BLOCKED)
    if blocked_n:
        health.append([S("%d BLOCKED" % blocked_n, C["blocked"])])
    incomplete = a.attempts_by_state
    ec = incomplete.get("EVIDENCE_COMPLETE", 0)
    if ec:
        health.append([S("%d awaiting reconciliation" % ec, C["warn"])])
    if health:
        lines.append(L(P(S("health ", C["dim"])), *_sep_parts(
            health, first="", sep=" · ")))

    # principal attribution — tree format
    _PRINCIPAL_LABEL = {
        "coordinator": ("Coordinator", C["coord"]),
        "orchestrator": ("Orchestrator", C["orch"]),
        "planner": ("Engineer", C["eng"]),
        "agent_factory": ("Taskforce dispatch", C["tf"]),
        "collector": ("Taskforce collection", C["tf"]),
    }
    principals = snapshot.principal_activity
    if principals:
        lines.append(L(P(S("principals:", C["dim"]))))
        pkeys = sorted(principals)
        for pi, principal in enumerate(pkeys):
            acts = principals[principal]
            is_last_p = pi == len(pkeys) - 1
            branch = "└─ " if is_last_p else "├─ "
            total = 0
            for v in acts.values():
                if isinstance(v, dict):
                    total += v.get("n", 0)
                else:
                    total += v
            label, lcolor = _PRINCIPAL_LABEL.get(
                principal, (principal, C["bright"]))
            lines.append(L(P(
                S("  %s" % branch, C["frame"]),
                S(label, lcolor, True),
                S("  %d total" % total, C["dim"]))))
            items = [(k, v) for k, v in sorted(acts.items())
                     if (isinstance(v, dict) and v.get("n"))
                     or (not isinstance(v, dict) and v)]
            for ai, (k, v) in enumerate(items):
                is_last_a = ai == len(items) - 1
                indent = "     " if is_last_p else "  │  "
                sub_branch = "└─ " if is_last_a else "├─ "
                if isinstance(v, dict):
                    n = v.get("n", 0)
                    detail_items = v.get("items", [])
                else:
                    n = v
                    detail_items = []
                lines.append(L(P(
                    S("%s%s" % (indent, sub_branch), C["frame"]),
                    S("%s" % k, C["dim"]),
                    S("  %d" % n, C["accent"]))))
                # third level: every item
                if detail_items:
                    sub_indent = (indent +
                                  ("     " if is_last_a else "  │  "))
                    for di, item in enumerate(detail_items):
                        is_last_d = di == len(detail_items) - 1
                        d_branch = "└─ " if is_last_d else "├─ "
                        lines.append(L(P(
                            S("%s%s" % (sub_indent, d_branch), C["frame"]),
                            S(str(item), C["dim"]))))

    # budget gauges — active objectives only
    active_obj = {o.objective_id for o in snapshot.objectives
                  if o.status not in _COMPLETE}
    active_budgets = [b for b in snapshot.budget_state
                      if b["objective_id"] in active_obj]
    if active_budgets:
        lines.append(L(P(S("budgets:", C["dim"]))))
        for bi, b in enumerate(active_budgets):
            cu, cm = b["contracts_used"], b["contracts_max"]
            au, am = b["attempts_used"], b["attempts_max"]
            is_last = bi == len(active_budgets) - 1
            branch = "└─ " if is_last else "├─ "
            lines.append(L(P(
                S("  %s" % branch, C["frame"]),
                S(b["objective_id"], C["bright"]))))
            indent = "     " if is_last else "  │  "
            lines.append(L(P(
                S("%s├─ " % indent, C["frame"]),
                S("contracts ", C["dim"]),
                S("%d/%d" % (cu, cm),
                  C["error"] if cu >= cm else
                  C["warn"] if cu >= cm - 1 else C["dim"]))))
            lines.append(L(P(
                S("%s└─ " % indent, C["frame"]),
                S("attempts  ", C["dim"]),
                S("%d/%d" % (au, am),
                  C["error"] if au >= am else
                  C["warn"] if au >= am - 2 else C["dim"]))))

    return lines


# ── bands ─────────────────────────────────────────────────────────────

def _activity_part(s, now):
    """What a live session has done, from its own record (a Codex
    rollout), else from its stdout, else that nothing is recorded yet."""
    if s.activity_events:
        bits = ["%d tool call%s" % (s.activity_tools,
                                    "" if s.activity_tools == 1 else "s")]
        if s.activity_done:
            bits.append("task complete")
        elif s.activity_doing:
            bits.append(_short(s.activity_doing, 40))
        if s.recent_tools:
            last_tools = s.recent_tools[-3:]
            bits.append("recent: %s" % ", ".join(last_tools))
        if s.recent_files:
            last_file = s.recent_files[-1]
            name = last_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            bits.append("wrote %s" % name)
        if s.activity_utc:
            bits.append("last %s" % _ago(s.activity_utc, now))
        return P(S(" · " + " · ".join(bits)))
    if s.last_line:
        return P(S(" · " + _short(s.last_line.strip(), 50), C["dim"]),
                 S(" (%s)" % _ago(s.output_utc, now), C["dim"]))
    return P(S(" · no activity recorded yet", C["dim"]))


def _exec_tail(snapshot):
    """The EXECUTING title's counts: sessions running, sessions ended but
    not collected, and attempts in flight with no live session."""
    ended = sum(1 for s in snapshot.sessions
                if s.session_state == "finished")
    bits = ["%d running" % (len(snapshot.sessions) - ended)]
    if ended:
        bits.append("%d ended, not collected" % ended)
    after = snapshot.after_session or {}
    if after:
        bits.append("%d past the session (%s)" % (
            sum(after.values()), ", ".join(
                "%s %d" % (k, n) for k, n in sorted(after.items()))))
    if snapshot.before_session:
        bits.append("%d not started" % snapshot.before_session)
    return " · ".join(bits)


def _exec_lines(snapshot, now):
    """One row per attempt with a live session: the work, the route its
    session runs on and who holds it, its state, and what it is doing.
    A session that wrote its result has a stopped clock."""
    lines = []
    for s in snapshot.sessions:
        how = HOW.get(s.selection_path, s.selection_path or "?")
        started = s.started_utc or s.attempt_since_utc
        ended = s.session_state == "finished"
        route = s.route_name or "%s/%s" % (s.provider or "?", s.model or "?")
        line = L(
            P(S("■ " if ended else "▶ ", C["dim"] if ended else C["accent"]),
              S(_short(s.workstream_key or s.sequence_id[:16], 44),
                C["bright"], True)),
            P(S("  " + route, C["luna"]),
              S(" · %s (%s)" % (s.specialist_handle or "?", how))),
            P(S("  %s since %s" % (s.attempt_state, _when(
                s.attempt_since_utc, now)), C["accent"])))
        if ended:
            line.append(P(S(" · session ended %s after %s" % (
                _when(s.finished_utc, now),
                _dur(_seconds(started, s.finished_utc))), C["dim"])))
            line.append(P(S(" · result not collected yet", C["warn"])))
        else:
            line.append(P(S(" · session %s" % _dur(_seconds(started, now)))))
            line.append(_activity_part(s, now))
            if s.pid:
                line.append(P(S(" · pid %s" % s.pid, C["dim"])))
        if s.finalization_class:
            line.append(P(S(" · " + s.finalization_class, C["dim"])))
        if s.objective_id:
            line.append(P(S(" · " + s.objective_id, C["dim"])))
        lines.append(line)
    if not lines:
        settled = [a for a in snapshot.recent_assignments
                   if a.status == "SETTLED" and a.settled_utc]
        line = L(P(S("nothing executing", C["dim"])))
        if settled:
            last = max(settled, key=lambda a: a.settled_utc)
            label = OUTCOME_LABEL.get(last.outcome, ("?", (
                last.outcome or "settled").lower()))[1]
            line.append(P(S(" · last: %s %s %s (%s)" % (
                last.workstream_key or last.sequence_id[:16], label,
                _when(last.settled_utc, now), _ago(last.settled_utc, now)),
                C["dim"])))
        lines.append(line)
    return lines


def _item_line(it, lead, kw, now, wide=True):
    sym, color = _state_mark(it.state)
    since = ""
    if it.since_utc:
        age = _dur(_seconds(it.since_utc, now))
        since = (" %s · %s" % (_when(it.since_utc, now), age) if wide
                 else " " + age)
    parts = [P(S("   " + lead, C["frame"]), S(sym + " ", color),
               S(_short(it.workstream_key, kw).ljust(kw) + " ", C["bright"]),
               S(it.state or "no sequence", color, True), S(since, C["dim"]))]
    if it.holder:
        how = HOW.get(it.selection_path, "")
        parts.append(P(S("   %s%s" % (it.holder, " · " + how if how else ""))))
    # orchestration node metadata — specific details
    if it.kind == "node":
        meta = []
        if it.realization_kind:
            _KIND_LABEL = {
                "TASK_CONTRACT": "contract",
                "ENGINEER_REASONING": "engineer reasoning",
                "CONTROL": "control node",
            }
            meta.append(_KIND_LABEL.get(it.realization_kind,
                                        it.realization_kind))
        if it.capability:
            meta.append(it.capability)
        if it.work_profile:
            _PROFILE_LABEL = {
                "RESEARCH": "read-only research",
                "IMPLEMENTATION": "source-modifying",
                "ENGINEERING": "engineering design",
            }
            meta.append(_PROFILE_LABEL.get(it.work_profile,
                                           it.work_profile))
        if it.output_kind:
            _OUTPUT_LABEL = {
                "RESEARCH_ARTIFACT": "produced research artifact",
                "ENGINEERING_DECISION": "produced engineering decision",
                "ACCEPTED_ARTIFACT": "produced accepted artifact",
                "REVIEW_RESULT": "produced review result",
                "EFFECT_RESULT": "produced effect result",
            }
            meta.append(_OUTPUT_LABEL.get(it.output_kind, it.output_kind))
        if it.output_sha256:
            meta.append("CAS %s" % it.output_sha256[:20])
        if it.sequence_id:
            meta.append("seq %s" % it.sequence_id[:20])
        if meta:
            parts.append(P(S("   [%s]" % " · ".join(meta), C["dim"])))
    state = it.state or ""
    if state == "PLANNED":
        parts.append(P(S(" · not started", C["dim"])))
    elif state.startswith("HALTED"):
        # the halting rule and the halt in words (the receipt state, or
        # what a reason code means); the recorded text stays in --json
        cause = it.halt_cause or ("receipt %s" % it.halt_completeness
                                  if it.halt_completeness else it.halt_reason)
        why = " ".join(x for x in (it.halt_rule, cause) if x)
        if why:
            parts.append(P(S(" · " + why, C["halted"])))
        if it.next_staffing:
            parts.append(P(S(" · next: %s" % it.next_staffing, C["dim"])))
    elif state in _BLOCKED:
        waits = [e for e in it.predecessors if not e.released_utc]
        if waits:
            parts.append(P(S(" · waits on " + ", ".join(
                "%s (%s)" % (e.predecessor_key or e.edge_id,
                             e.predecessor_state or "?") for e in waits),
                C["blocked"])))
    elif it.attempt_state:
        parts.append(P(S(" · attempt %s" % it.attempt_state, C["accent"])))
    elif state == "SUPERSEDED":
        parts.append(P(S(" · replaced", C["dim"])))
    elif state == "COMPLETED" and it.assignment_outcome:
        parts.append(P(S(" · " + OUTCOME_LABEL.get(it.assignment_outcome, (
            "", it.assignment_outcome.lower()))[1], C["success"])))
    elif state in _QUEUED:
        parts.append(P(S(" · next: %s" % (it.next_staffing or "staffing"),
                         C["dim"])))
    return parts


def _item_key(it):
    return it.item_id or it.sequence_id


def _find_replacement(superseded_it, items):
    """Find a replacement workstream for a SUPERSEDED item by name pattern."""
    base = superseded_it.workstream_key
    for it in items:
        if it.workstream_key == base:
            continue
        if (it.workstream_key.startswith(base)
                and it.state in ("COMPLETED", "EXECUTING", "READY",
                                 "DECLARED")):
            return it
        prefix = base.rsplit("-", 1)[0] if "-" in base else base
        if (it.workstream_key.startswith(prefix)
                and it.workstream_key != base
                and it.state in ("COMPLETED", "EXECUTING", "READY",
                                 "DECLARED")):
            return it
    return None


def _project_tree(project, now, wide=True):
    if not project.items:
        return []
    kw = min(36, max(len(it.workstream_key) for it in project.items))
    by_item = {_item_key(it): it for it in project.items if _item_key(it)}
    children, roots = {}, []
    for it in project.items:
        parents = [e.predecessor_item_id or e.predecessor_sequence_id
                   for e in it.predecessors
                   if (e.predecessor_item_id or e.predecessor_sequence_id)
                   in by_item
                   and (e.predecessor_item_id or e.predecessor_sequence_id)
                   != _item_key(it)]
        if parents:
            children.setdefault(parents[0], []).append(it)
        else:
            roots.append(it)
    lines, seen = [], set()
    replacement_shown = set()

    def emit(it, lead, is_last, depth):
        if id(it) in seen:
            return
        seen.add(id(it))
        branch = "" if depth == 0 else ("└▶ " if is_last else "├▶ ")
        lines.append(_item_line(it, lead + branch, kw, now, wide))
        if it.state == "SUPERSEDED":
            repl = _find_replacement(it, project.items)
            if repl:
                replacement_shown.add(id(repl))
                indent = lead + ("   " if is_last or depth == 0
                                 else "│  ")
                rsym, rcolor = _state_mark(repl.state)
                outcome = ""
                if repl.assignment_outcome:
                    outcome = " / %s" % OUTCOME_LABEL.get(
                        repl.assignment_outcome,
                        ("", repl.assignment_outcome.lower()))[1]
                lines.append(L(P(
                    S("   %s↳ " % indent, C["flow_lit"]),
                    S(repl.workstream_key, C["bright"]),
                    S(" %s%s" % (repl.state or "?", outcome),
                      rcolor))))
        kids = children.get(_item_key(it), [])
        for i, kid in enumerate(kids):
            emit(kid, lead + ("" if depth == 0 else (
                "   " if is_last else "│  ")), i == len(kids) - 1, depth + 1)

    for i, it in enumerate(roots):
        emit(it, "", i == len(roots) - 1, 0)
    for it in project.items:
        if id(it) not in seen:
            emit(it, "", True, 0)
    return lines


def _dag_lines(snapshot, now, wide=True):
    objectives = {o.objective_id: o for o in snapshot.objectives}
    live, closed = [], []
    for p in list(snapshot.projects) + list(snapshot.plans):
        o = objectives.get(p.objective_id)
        if p.status == "ACTIVE" or (o and o.status not in _COMPLETE):
            live.append(p)
        elif p.kind == "project":
            closed.append(p)

    def created(p):
        o = objectives.get(p.objective_id)
        return (o.created_utc if o else "") or ""

    live.sort(key=created, reverse=True)
    # a project with no work items next to a plan of the same objective is
    # one heading: the plan's tasks were never bound to it
    planned = {p.objective_id for p in live if p.kind == "plan" and p.items}
    unbound = {}
    for p in live:
        if p.kind == "project" and not p.items and p.objective_id in planned:
            unbound.setdefault(p.objective_id, []).append(p.project_id[:7])
    live = [p for p in live if not (p.kind == "project" and not p.items
                                    and p.objective_id in planned)]
    lines = []
    # a project heading is one row: the title is free text and comes last,
    # so it is what a narrow row cuts
    for p in live:
        done = sum(1 for it in p.items if it.state == "COMPLETED")
        if p.kind == "plan" and unbound.get(p.objective_id):
            where = "%s, not bound to %s" % (
                p.title, ", ".join(unbound[p.objective_id]))
        elif p.kind == "plan":
            where = "%s, no project" % p.title
        else:
            where = p.project_id[:7]
        lines.append(L(P(
            S("◆ ", C["coord"]), S(p.objective_id, C["bright"], True),
            S("  %d/%d done · %s opened %s" % (
                done, len(p.items), where, _when(p.opened_utc, now)),
                C["dim"]),
            S("  " + p.title if p.kind == "project" else ""))))
        planned = [it for it in p.items if it.state == "PLANNED"]
        if p.items and len(planned) == len(p.items):
            # nothing started: one row names the tasks
            lines.append(L(P(S("   ○ %d task%s planned, none started" % (
                len(planned), "" if len(planned) == 1 else "s"), C["dim"])),
                *_sep_parts([[S(_short(it.workstream_key, 36), C["dim"])]
                             for it in planned], first=": ", sep=", ")))
            continue
        lines.extend(_project_tree(p, now, wide) or [
            L(P(S("   no workstreams yet", C["dim"])))])
        tasks = [it for it in p.items if it.kind == "node"]
        if len(tasks) > 1 and not any(it.predecessors for it in tasks):
            # the plan says nothing about order: its tasks all run at once
            lines.append(L(P(S("   no dependencies recorded between these"
                               " %d tasks" % len(tasks), C["warn"]))))
    closed.sort(key=lambda p: p.closed_utc or p.opened_utc or "",
                reverse=True)
    for p in closed[:2]:
        done = sum(1 for it in p.items if it.state == "COMPLETED")
        lines.append(L(P(
            S("✦ ", C["success"]), S(p.objective_id),
            S("  %d/%d done · %s%s" % (
                done, len(p.items), p.status.lower(),
                " " + _when(p.closed_utc, now) if p.closed_utc else ""),
                C["dim"]),
            S("  " + p.title, C["dim"]))))
    if len(closed) > 2:
        oldest = min(p.opened_utc or "" for p in closed[2:])
        lines.append(L(P(S("  + %d more closed projects, since %s" % (
            len(closed) - 2, _day(oldest, None)), C["dim"]))))
    if not lines:
        lines.append(L(P(S("no projects", C["dim"]))))
    return lines


def _event_lines(events, now):
    lines = []
    for ev in events:
        icon, color = LAYER.get(ev.layer, ("·", C["dim"]))
        subject, _, change = (ev.summary or "").partition(" ")
        lines.append(L(P(S(_precise(ev.timestamp, now), C["dim"]),
                         S(" "), S(icon, color), S(" " + subject)),
                       P(S(" " + change)) if change else None))
    return lines


def _activity_lines(snapshot, now):
    return _event_lines(snapshot.recent_events, now) or [
        L(P(S("no activity recorded", C["dim"])))]


# ── the ACTIVITY band: a window over the history, scrolled by keys ──────

SECTION_NAMES = ("tf", "eng", "orch", "coord", "auth", "dag", "activity")
SECTION_KEYS = {"1": "tf", "2": "eng", "3": "orch", "4": "coord",
                "5": "auth", "6": "dag", "7": "activity"}


class ActivityView:
    """What the viewer has done to the ACTIVITY band of the live view: how
    far back it is scrolled (the event at its top, counted from the
    newest) and whether it fills the screen. Scrolled back, the band keeps
    the same event at its top while new ones arrive; Home follows the
    newest again."""

    def __init__(self):
        self.top = 0          # index of the event at the band's top
        self.anchor = None    # (timestamp, layer, summary) of that event
        self.expanded = False
        self.page = 10        # events the band showed in the last frame
        self.focus = None     # focused section name or None
        self.section_offsets = {s: 0 for s in SECTION_NAMES}

    def scroll(self, events):
        if self.focus == "activity":
            self.top = max(0, self.top + events)
            self.anchor = None
        elif self.focus and self.focus in self.section_offsets:
            self.section_offsets[self.focus] = max(
                0, self.section_offsets[self.focus] + events)
        else:
            self.top = max(0, self.top + events)
            self.anchor = None

    def newest(self):
        if self.focus == "activity":
            self.top, self.anchor = 0, None
        elif self.focus:
            self.section_offsets[self.focus] = 0
        else:
            self.top, self.anchor = 0, None

    def section_offset(self, name):
        return self.section_offsets.get(name, 0)


KEY_HELP = ("1-7 focus section · ↑↓ scroll "
            "· a expand · Esc back · q quit")


def apply_key(view, key):
    """Change ``view`` for one key. Returns "quit" for q."""
    if key in ("q", "Q"):
        return "quit"
    if key in SECTION_KEYS:
        name = SECTION_KEYS[key]
        if view.focus == name:
            view.focus = None
        else:
            view.focus = name
        return None
    if key in ("escape",):
        view.focus = None
        return None
    if key in ("down", "j"):
        view.scroll(1)
    elif key in ("up", "k"):
        view.scroll(-1)
    elif key in ("pgdn", " "):
        view.scroll(max(1, view.page - 1))
    elif key == "pgup":
        view.scroll(-max(1, view.page - 1))
    elif key in ("home", "g"):
        view.newest()
    elif key in ("end", "G"):
        if view.focus:
            view.section_offsets[view.focus] = 10 ** 6
        else:
            view.top, view.anchor = 10 ** 9, None
    elif key in ("a", "A"):
        view.expanded = not view.expanded
    return None


def _activity_window(snapshot, now, view, width, rows):
    """The band's rows from the view's top event; (rows, events shown,
    events in the history). The last row counts the older events when
    they do not all fit."""
    events = snapshot.recent_events
    total = len(events)
    top = 0
    if view is not None and total:
        top = view.top
        if top and view.anchor is not None:
            for i, ev in enumerate(events):
                if (ev.timestamp, ev.layer, ev.summary) == view.anchor:
                    top = i
                    break
        top = max(0, min(top, total - 1))
        view.top = top
        ev = events[top]
        view.anchor = (ev.timestamp, ev.layer, ev.summary) if top else None
    lines = (_event_lines(events[top:], now) if total else
             [L(P(S("no activity recorded", C["dim"])))])

    def lay(limit):
        out, shown = [], 0
        for line in lines:
            laid = _wrap(line, width, indent=16)
            if len(out) + len(laid) > limit:
                break
            out.extend(laid)
            shown += 1
        return out, shown

    out, shown = lay(rows)
    if total and top + shown < total and rows > 1:
        out, shown = lay(rows - 1)
        older = total - top - shown
        out.append([S("  ↓ %d older event%s%s" % (
            older, "" if older == 1 else "s",
            " · PgDn" if view is not None else ""), C["dim"])])
    if view is not None:
        view.page = max(1, shown)
    return out, (shown if total else 0), total


def _activity_tail(snapshot, now, view, shown, total):
    events = snapshot.recent_events
    if not events:
        return None
    if view is not None and view.top:
        return "events %d–%d of %d · back to %s · PgUp newer · Home newest" \
            " · a %s" % (view.top + 1, view.top + shown, total,
                         _when(events[view.top].timestamp, now),
                         "back" if view.expanded else "expand")
    newest = events[0].timestamp
    tail = "newest %s · %s" % (_when(newest, now), _ago(newest, now))
    if view is not None:
        tail += " · %d events · %s" % (total, KEY_HELP if not view.expanded
                                       else KEY_HELP.replace("a expand",
                                                             "a back"))
    return tail


def _activity_band(scr, y, w, snapshot, now, view, rows):
    band_w = w - 5
    body, shown, total = _activity_window(snapshot, now, view, band_w,
                                          max(rows, 0))
    _band_title(scr, y, w, "ACTIVITY", C["accent"],
                _activity_tail(snapshot, now, view, shown, total))
    for i, segs in enumerate(body[:max(rows, 0)]):
        _paint(scr, 3, y + 1 + i, segs, band_w)


def _summary_lines(snapshot, now):
    """One line per principal, for terminals too small for the boxes."""
    active = _active_objectives(snapshot)
    stamped = [cd for cd in snapshot.coordinator_decisions if cd.created_utc]
    coord = L(P(S("◆ coord  ", C["coord"], True),
                S("%d active" % len(active), C["bright"])))
    if stamped:
        last = max(stamped, key=lambda cd: cd.created_utc)
        coord.append(P(S(" · last %s %s → %s" % (
            _when(last.created_utc, now), last.objective_id,
            MODE_SHORT.get(last.elevation_mode, "?")), C["dim"])))
    orch = L(P(S("◈ orch   ", C["orch"], True),
               S("%d runs recorded ACTIVE" % sum(
                   1 for r in snapshot.runs if r.status == "ACTIVE"),
                 C["dim"])),
             *_sep_parts(_effective_groups(snapshot.runs), first=" → "))
    e = snapshot.engineer
    eng = L(P(S("⚙ eng    ", C["eng"], True)))
    if e.cycles:
        c = e.cycles[0]
        eng.append(P(S("cycle %s %s %s (%s)" % (
            c.cycle_id, c.status, _when(c.started_utc, now),
            _ago(c.started_utc, now)))))
    eng.append(P(S(" · %d projects" % sum(
        1 for p in snapshot.projects if p.status == "ACTIVE"), C["dim"])))
    eng.append(P(S(" · %d waiting" % len(e.waiting),
                   C["warn"] if e.waiting else C["dim"])))
    eng.append(P(S(" · %d staffing open" % len(e.staffing_open), C["dim"])))
    tf = snapshot.taskforce
    working = _working_now(snapshot)
    main, _ = seat_routes(tf.seats)
    tfl = L(P(S("⛊ tf     ", C["tf"], True),
              S("%d members" % tf.member_count)),
            P(S(" · on %s" % main, C["luna"])) if main else None,
            P(S(" · working: %s" % (", ".join(
                "%s → %s" % (handle, _short(work, 30))
                for handle, work, _ in working) or "nobody"),
                C["accent"] if working else C["dim"])),
            P(S(" · outside: %s" % ", ".join(tf.outside_pool), C["dim"]))
            if tf.outside_pool else None)
    auth = L(P(S("⬡ auth   ", C["auth"], True)), *_sep_parts(_census_groups(
        snapshot.authority.sequences_by_state, SEQUENCE_TERMINAL,
        C["accent"])))
    bad = sum(n for k, n in snapshot.trust.receipts_by_completeness.items()
              if k != "COMPLETE")
    if bad:
        auth.append(P(S(" · %d receipt%s not COMPLETE" % (
            bad, "" if bad == 1 else "s"), C["error"])))
    return [coord, orch, eng, tfl, auth]


def _band_title(scr, y, w, title, color, tail=None):
    for cx in range(2, w - 2):
        scr.put(cx, y, "━", C["frame"])
    _paint(scr, 4, y, [S(" %s " % title, color, True)], w - 6)
    if tail:
        _paint(scr, 5 + len(title) + 2, y, [S(" %s " % tail, C["dim"])],
               w - 9 - len(title))


def _split_bands(rows, exec_n, dag_n, act_n):
    """Rows for the three band bodies (each band also takes a title row):
    EXECUTING first (every live session while it can), then the work
    graph; activity takes what is left, and never less than half."""
    body = rows - 3
    if body <= 0:
        return 0, 0, 0
    e = min(max(exec_n, 1), max(1, body * 2 // 5))
    rest = body - e
    act_floor = min(max(act_n, 1), max(4, (rest + 1) // 2), rest)
    d = min(max(dag_n, 1), max(rest - act_floor, 0))
    return e, d, rest - d


# At this width the four principals' boxes fit side by side in one row,
# which leaves their second row of height to the live bands.
FOUR_ACROSS = 200


# ── compose frame ─────────────────────────────────────────────────────

def _primary_objective_lines(snapshot, now):
    """Cross-layer status of the newest active objective."""
    active = _active_objectives(snapshot)
    if not active:
        return []
    obj = active[0]
    oid = obj.objective_id
    lines = []
    lines.append([S("PRIMARY OBJECTIVE  ", C["bright"], True),
                  S(oid, C["accent"], True)])

    # Coordinator layer
    cd = None
    for d in snapshot.coordinator_decisions:
        if d.objective_id == oid:
            cd = d
    if cd:
        lines.append([S("  Coordinator     ", C["coord"]),
                      S(MODE_SHORT.get(cd.elevation_mode,
                                       cd.elevation_mode), C["bright"]),
                      S(" → %s" % cd.route if cd.route else "", C["luna"])])

    # Orchestrator layer
    obj_runs = [r for r in snapshot.runs if r.objective_id == oid]
    if obj_runs:
        r = obj_runs[0]
        ns = {}
        for n in r.nodes:
            ns[n.status] = ns.get(n.status, 0) + 1
        node_parts = " ".join("%d %s" % (v, k)
                              for k, v in sorted(ns.items()))
        lines.append([S("  Orchestrator    ", C["orch"]),
                      S("%d/%d nodes" % (ns.get("COMPLETED", 0),
                                         len(r.nodes)), C["bright"]),
                      S("  %s" % node_parts if len(ns) > 1 else "",
                        C["dim"])])

    # Engineer layer
    obj_projects = [p for p in snapshot.projects
                    if p.objective_id == oid and p.status == "ACTIVE"]
    if obj_projects:
        prj = obj_projects[0]
        done = sum(1 for it in prj.items if it.state == "COMPLETED")
        halted = sum(1 for it in prj.items
                     if it.state and it.state.startswith("HALTED"))
        superseded = sum(1 for it in prj.items if it.state == "SUPERSEDED")
        parts = ["%d COMPLETED" % done] if done else []
        if superseded:
            parts.append("%d SUPERSEDED" % superseded)
        if halted:
            parts.append("%d HALTED" % halted)
        lines.append([S("  Engineer        ", C["eng"]),
                      S(" · ".join(parts) if parts else "no workstreams",
                        C["bright"] if not halted else C["halted"])])

    # Taskforce layer
    working = _working_now(snapshot)
    obj_working = []
    for handle, work, how in working:
        for prj in obj_projects:
            if any(it.workstream_key == work or
                   (it.holder and it.holder == handle)
                   for it in prj.items):
                obj_working.append(handle)
                break
    if obj_working:
        lines.append([S("  Taskforce       ", C["tf"]),
                      S(", ".join(obj_working) + " working", C["accent"])])
    else:
        lines.append([S("  Taskforce       ", C["tf"]),
                      S("idle", C["dim"])])

    # Closure status
    waiting = [p for p in snapshot.engineer.waiting
               if p.objective_id == oid]
    if waiting:
        reasons = set(p.proposal_class for p in waiting)
        if "WAIT_FOR_AUTHORITY" in reasons:
            lines.append([S("  Closure         ", C["warn"], True),
                          S("WAITING FOR AUTHORITY", C["halted"], True)])
        else:
            lines.append([S("  Closure         ", C["warn"], True),
                          S("BLOCKED: %s" % ", ".join(reasons),
                            C["warn"])])
    elif obj.work_state == "work finished":
        lines.append([S("  Closure         ", C["success"]),
                      S("ready to close", C["success"])])
    else:
        state_label = obj.work_state or "in progress"
        lines.append([S("  Closure         ", C["dim"]),
                      S(state_label, C["dim"])])

    return [L(P(*row)) for row in lines]


def compose(scr, snapshot, tick=0, routing=None, view=None):
    """Write the whole frame into the screen's back buffer. ``view`` is the
    live loop's ActivityView (scroll position, expanded): None draws the
    newest activity with no key hints. ``tick`` and ``routing`` are
    accepted for compatibility: liveness now comes from recorded
    heartbeats, and each seat shows its own route."""
    w, h = scr.w, scr.h
    now = _parse_utc(snapshot.captured_utc)
    if now is None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

    left, right = _header_parts(snapshot, now, wide=w >= 100)
    room = w - 4
    head = _wrap(left, room, indent=2)
    tail_w = sum(_width(p) for p in right)
    y = 0
    if len(head) == 1 and _width(head[0]) + 3 + tail_w <= room:
        _paint(scr, 2, 0, head[0], room)
        _paint(scr, 2 + room - tail_w, 0, [s for p in right for s in p],
               tail_w)
        y = 1
    else:
        for row in head + _wrap(right, room, indent=2):
            _paint(scr, 2, y, row, room)
            y += 1
    for cx in range(2, w - 2):
        scr.put(cx, y, "━", C["frame"])
    y += 1

    if view is not None and view.expanded:
        # ACTIVITY fills the screen under the header; a puts the boxes back
        _activity_band(scr, y, w, snapshot, now, view, h - y - 1)
        return

    band_w = w - 5
    exec_rows = _layout(_exec_lines(snapshot, now), band_w)
    dag_rows = _layout(_dag_lines(snapshot, now, wide=w >= 100), band_w,
                       indent=8)
    act_rows = _layout(_activity_lines(snapshot, now), band_w, indent=16)
    # what the bands keep before the boxes get their full height: every
    # live session (up to 8), and more of the rest on a taller terminal
    k = max(3, (h - 30) // 4)
    band_floor = (1 + min(max(len(exec_rows), 1), 8)) + \
        (1 + min(max(len(dag_rows), 1), k)) + \
        (1 + min(max(len(act_rows), 1), k))

    boxes = None
    aw = w - 8
    fw = w - 4  # full-width box width
    if w >= FOUR_ACROSS:
        pw = (w - 8) // 3
        iw = pw - 4
        trio = [_layout(_tf_lines(snapshot, now, narrow=True), iw),
                _layout(_eng_lines(snapshot, now, narrow=True), iw),
                _layout(_orch_lines(snapshot, now, narrow=True), iw)]
        coord = _layout(_coord_lines(snapshot, now), aw)
        auth = _layout(_auth_lines(snapshot, now), aw)
        for tcap, ccap, acap in ((16, 8, 6), (14, 7, 5), (12, 6, 5),
                                  (10, 5, 4), (8, 5, 4), (6, 4, 3),
                                  (4, 3, 3)):
            heights = [2 + min(max(len(b) for b in trio), tcap),
                       2 + min(len(coord), ccap),
                       2 + min(len(auth), acap)]
            if y + sum(heights) + band_floor <= h:
                boxes = ("trio", pw, trio, coord, auth, heights)
                break
    if boxes is None and w >= 100:
        pw = (w - 6) // 2
        iw = pw - 4
        pair = (_layout(_tf_lines(snapshot, now), iw),
                _layout(_eng_lines(snapshot, now), iw))
        orch_rows = _layout(_orch_lines(snapshot, now), aw)
        coord = _layout(_coord_lines(snapshot, now), aw)
        auth = _layout(_auth_lines(snapshot, now), aw)
        for pcap, ocap, ccap, acap in (
                (10, 10, 8, 6), (8, 8, 7, 5), (6, 6, 6, 5),
                (5, 5, 5, 4), (4, 4, 4, 4), (3, 3, 3, 3)):
            heights = [2 + min(max(len(pair[0]), len(pair[1])), pcap),
                       2 + min(len(orch_rows), ocap),
                       2 + min(len(coord), ccap),
                       2 + min(len(auth), acap)]
            if y + sum(heights) + band_floor <= h:
                boxes = ("stack", pw, pair, orch_rows, coord, auth, heights)
                break

    def _soff(name):
        return view.section_offset(name) if view else 0

    def _sfoc(name):
        return view is not None and getattr(view, "focus", None) == name

    if boxes and boxes[0] == "trio":
        _, pw, trio, coord, auth, heights = boxes
        for i, (key, rows) in enumerate(zip(("tf", "eng", "orch"), trio)):
            _draw_box(scr, 2 + i * (pw + 2), y, pw, heights[0], key, rows,
                       offset=_soff(key), focused=_sfoc(key))
        y += heights[0]
        _draw_box(scr, 2, y, fw, heights[1], "coord", coord,
                   offset=_soff("coord"), focused=_sfoc("coord"))
        y += heights[1]
        _draw_box(scr, 2, y, fw, heights[2], "auth", auth,
                   offset=_soff("auth"), focused=_sfoc("auth"))
        y += heights[2]
    elif boxes and boxes[0] == "stack":
        _, pw, pair, orch_rows, coord, auth, heights = boxes
        x1, x2 = 2, 2 + pw + 2
        _draw_box(scr, x1, y, pw, heights[0], "tf", pair[0],
                   offset=_soff("tf"), focused=_sfoc("tf"))
        _draw_box(scr, x2, y, pw, heights[0], "eng", pair[1],
                   offset=_soff("eng"), focused=_sfoc("eng"))
        y += heights[0]
        _draw_box(scr, 2, y, fw, heights[1], "orch", orch_rows,
                   offset=_soff("orch"), focused=_sfoc("orch"))
        y += heights[1]
        _draw_box(scr, 2, y, fw, heights[2], "coord", coord,
                   offset=_soff("coord"), focused=_sfoc("coord"))
        y += heights[2]
        _draw_box(scr, 2, y, fw, heights[3], "auth", auth,
                   offset=_soff("auth"), focused=_sfoc("auth"))
        y += heights[3]
    else:
        for row in _layout(_summary_lines(snapshot, now), w - 4, indent=9):
            if y >= h:
                break
            _paint(scr, 2, y, row, w - 4)
            y += 1

    e_rows, d_rows, a_rows = _split_bands(h - y, len(exec_rows),
                                          len(dag_rows), len(act_rows))
    if y < h:
        _band_title(scr, y, w, "EXECUTING", C["trail1"],
                    _exec_tail(snapshot))
        y += 1
        for segs in _more(exec_rows, e_rows):
            _paint(scr, 3, y, segs, band_w)
            y += 1
    if y < h:
        live = sum(1 for p in snapshot.projects if p.status == "ACTIVE")
        plans = sum(1 for p in snapshot.plans if p.status == "ACTIVE")
        _band_title(scr, y, w, "DAG", C["accent"],
                    "work graph · %d active project%s%s" % (
                        live, "" if live == 1 else "s",
                        " · %d run%s without a project" % (
                            plans, "" if plans == 1 else "s")
                        if plans else ""))
        y += 1
        dag_off = _soff("dag")
        dag_window = dag_rows[dag_off:] if dag_off > 0 else dag_rows
        for segs in _more(dag_window, d_rows):
            _paint(scr, 3, y, segs, band_w)
            y += 1
    if y < h:
        _activity_band(scr, y, w, snapshot, now, view,
                       min(a_rows, h - y - 1))


# ── stream loop ───────────────────────────────────────────────────────

FULL_REPAINT_SECONDS = 10.0

# Keys: a Windows console reports a special key as "\x00" or "\xe0" and a
# scan code; a VT terminal as an escape sequence.
_WIN_KEYS = {"H": "up", "P": "down", "I": "pgup", "Q": "pgdn",
             "G": "home", "O": "end"}
_VT_KEYS = (("\x1b[5~", "pgup"), ("\x1b[6~", "pgdn"), ("\x1b[1~", "home"),
            ("\x1b[4~", "end"), ("\x1b[H", "home"), ("\x1b[F", "end"),
            ("\x1bOH", "home"), ("\x1bOF", "end"), ("\x1b[A", "up"),
            ("\x1b[B", "down"), ("\x1bOA", "up"), ("\x1bOB", "down"))


def _parse_vt(data):
    """Key names from what a VT terminal sent: named keys for the escape
    sequences above, characters for the rest (other sequences dropped)."""
    keys, i = [], 0
    while i < len(data):
        for seq, name in _VT_KEYS:
            if data.startswith(seq, i):
                keys.append(name)
                i += len(seq)
                break
        else:
            if data[i] == "\x1b":
                j = i + 1
                if j < len(data) and data[j] in "[O":
                    j += 1
                    while j < len(data) and not data[j].isalpha() \
                            and data[j] != "~":
                        j += 1
                    i = j + 1
                    continue
                keys.append("escape")
                i = j
                continue
            keys.append(data[i])
            i += 1
    return keys


def _read_keys(timeout):
    """The keys pressed within ``timeout`` seconds, returned as soon as one
    arrives; [] once the time is up, or when no keys can be read."""
    if timeout <= 0:
        timeout = 0
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError:
            time.sleep(timeout)
            return []
        end = time.monotonic() + timeout
        keys = []
        while True:
            try:
                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        keys.append(_WIN_KEYS.get(msvcrt.getwch()))
                    elif ch == "\x1b":
                        keys.append("escape")
                    else:
                        keys.append(ch)
            except OSError:
                time.sleep(max(0.0, end - time.monotonic()))
                return []
            if keys or time.monotonic() >= end:
                return [k for k in keys if k]
            time.sleep(0.03)
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return []
        return _parse_vt(os.read(sys.stdin.fileno(), 64).decode(
            "utf-8", "replace"))
    except (OSError, ValueError, ImportError):
        time.sleep(timeout)
        return []


def _cbreak():
    """Put a POSIX terminal's input in cbreak mode (keys arrive one by one,
    unechoed); returns the restore function, or None."""
    if os.name == "nt":
        return None
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except Exception:
        return None
    return lambda: termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _terminal_size():
    size = shutil.get_terminal_size((146, 53))
    return max(size.columns, 40), max(size.lines, 12)


def _write(text):
    """Write to stdout, replacing what its encoding cannot carry."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        sys.stdout.write(text.encode(enc, "replace").decode(enc))
    sys.stdout.flush()


def stream(intelligence_path, authority_path, step_log_path=None,
           interval=0.5, once=False, ground=False, project_root=None,
           history=None):
    from pathlib import Path
    from .readers import (IntelligenceReader, AuthorityReader,
                          StepLogReader, ConfigReader, AutoSwarmReader,
                          CASReader)
    from .model import build_snapshot

    intel = IntelligenceReader(intelligence_path)
    auth = AuthorityReader(authority_path)
    step = StepLogReader(step_log_path) if step_log_path else None
    root = project_root or Path(intelligence_path).parent
    config = ConfigReader(root)
    autoswarm_home = os.environ.get("AUTOSWARM_HOME")
    if not autoswarm_home and config:
        autoswarm_home = config._load().get(
            "paths", {}).get("autoswarm_home")
    autoswarm = AutoSwarmReader(autoswarm_home) if autoswarm_home else None
    cas = CASReader(Path(authority_path).parent / "store" / "objects")

    interactive = not once and sys.stdout.isatty()
    tw, th = _terminal_size()
    scr = Screen(tw, th)
    tick = 0
    # the viewer's scroll position and expansion of ACTIVITY (live only)
    view = ActivityView() if interactive else None
    # frames still to draw whole: the first one; after a resize, that frame
    # and the next (a terminal can redraw its old content late); and one
    # every FULL_REPAINT_SECONDS of snapshots, so a cell the terminal kept
    # from an earlier frame cannot stay (the diff rewrites only what it
    # thinks changed)
    repaint = 1
    full_every = max(1, int(round(FULL_REPAINT_SECONDS / interval))) \
        if interval > 0 else 1
    since_full = 0
    restore = None

    try:
        if interactive:
            restore = _cbreak()
            _write(ALT_SCREEN_ON + CLEAR_SCREEN + HIDE_CURSOR + WRAP_OFF
                   + ALT_SCROLL_ON)
        snapshot, rebuild, due = None, True, 0.0
        while True:
            # a new snapshot every interval; a key redraws the last one at
            # once, so scrolling does not wait for the next poll
            built = False
            if rebuild or snapshot is None or time.monotonic() >= due:
                snapshot = build_snapshot(intel, auth, step_reader=step,
                                          config_reader=config,
                                          autoswarm_reader=autoswarm,
                                          cas_reader=cas, history=history)
                due = time.monotonic() + interval
                built = True
                since_full += 1
            if interactive:
                size = _terminal_size()
                if size != (tw, th):
                    tw, th = size
                    scr = Screen(tw, th)
                    repaint = 2
            scr.clear()
            compose(scr, snapshot, tick, view=view)

            if not interactive:
                _write("\n".join(scr.text_lines(
                    color=sys.stdout.isatty())) + "\n")
                if ground:
                    _ground(snapshot)
                break
            if repaint or (built and since_full >= full_every):
                _write(CLEAR_SCREEN + scr.full_render())
                repaint = max(repaint - 1, 0)
                since_full = 0
            else:
                scr.flush(scr.render_diff(scr.diff()))
            scr.swap()
            tick += 1
            keys = _read_keys(max(0.0, due - time.monotonic()))
            rebuild = not keys
            if any(apply_key(view, k) == "quit" for k in keys):
                break
    except KeyboardInterrupt:
        pass
    finally:
        if interactive:
            _write(ALT_SCROLL_OFF + WRAP_ON + SHOW_CURSOR + ALT_SCREEN_OFF)
        if restore is not None:
            restore()
        intel.close()
        auth.close()


def _ground(snapshot):
    sys.stdout.write("=== GROUND ===\n")
    sys.stdout.write("objectives: %d active, %d complete\n" % (
        sum(1 for o in snapshot.objectives if o.status == "ACTIVE"),
        sum(1 for o in snapshot.objectives
            if o.status in ("COMPLETE", "COMPLETED"))))
    sys.stdout.write("runs: %d  nodes: %d  CAS: %d\n" % (
        len(snapshot.runs),
        sum(len(r.nodes) for r in snapshot.runs),
        snapshot.authority.cas_object_count))
