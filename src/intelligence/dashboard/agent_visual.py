"""Agent view: one box per principal over the organization's live state,
then the work executing now, the project work graph, and one activity
stream over every layer.

Every row comes from a recorded column or from a derived rule that names
itself (the review's field map and attention rules A1-A13):

- COORDINATOR: active objectives with their admission, the last
  admission and, derived, the words each feature came from.
- ORCHESTRATOR: each run's recorded status next to what its objective
  and work are (working, stuck, waiting, done).
- ENGINEER: the newest cycle and its age, active projects and their
  progress, proposals waiting, staffing not yet applied, and whether the
  reasoning text is stored.
- TASKFORCE: every seat with its holder, standing, record through the
  seat and without it, and who works on what now.
- AUTHORITY: states by full name, attempts, receipts, verifications,
  effects, witnesses, permissions, the sequencer lease and journal head.
- EXECUTING: one row per attempt the authority plane holds in flight.
- DAG: each live project's workstreams joined by their frozen
  predecessor edges, with holder, age, and why halted or blocked.
- ACTIVITY: journal transitions named by workstream, planning, workforce,
  cycle, admission and permission events, times to the millisecond.

A line is built from whole parts. A part that does not fit moves to an
indented continuation line; only a part wider than the space left ends
in "…", and a list that does not fit ends in "+N more". The terminal runs
in the alternate screen with autowrap off; its size is re-read every
tick, and a resize repaints the whole frame.
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
    CLEAR_SCREEN, WRAP_OFF, WRAP_ON)

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
SEAT_STATUS = {"ESTABLISHED": "EST", "RETAINED": "RET"}
HOW = {"RETAINED_SEAT": "seat", "OUTSIDE_SPECIALIST": "outside"}

EFFECTIVE = {  # a run's derived state: symbol, color, sort order
    "working": ("▶", C["accent"], 0), "stuck": ("✗", C["halted"], 1),
    "waiting": ("◫", C["blocked"], 2), "unknown": ("?", C["dim"], 3),
    "done": ("✦", C["success"], 4),
}

_COMPLETE = ("COMPLETE", "COMPLETED")
_BLOCKED = ("BLOCKED", "BLOCKED_UNSUPPORTED_FINALIZER")
_QUEUED = ("DECLARED", "READY")


def _state_mark(state):
    """Symbol and color of a sequence state."""
    if not state:
        return "·", C["dim"]
    if state.startswith("HALTED"):
        return "✗", C["halted"]
    if state in _BLOCKED:
        return "◫", C["blocked"]
    if state == "COMPLETED":
        return "✦", C["success"]
    if state in SEQUENCE_TERMINAL:
        return "·", C["dim"]
    if state in _QUEUED:
        return "○", C["dim"]
    return "▶", C["accent"]


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


def _draw_box(scr, x, y, w, h, key, rows):
    _box(scr, x, y, w, h, key)
    for i, segs in enumerate(_more(rows, h - 2)):
        _paint(scr, x + 2, y + 1 + i, segs, w - 4)


def _active_objectives(snapshot):
    active = [o for o in snapshot.objectives if o.status not in _COMPLETE]
    active.sort(key=lambda o: o.created_utc or "", reverse=True)
    return active


def _coord_lines(snapshot, now):
    active = _active_objectives(snapshot)
    done = len(snapshot.objectives) - len(active)
    modes = {}
    for cd in snapshot.coordinator_decisions:
        modes[cd.elevation_mode] = modes.get(cd.elevation_mode, 0) + 1
    lines = [L(P(S("%d active" % len(active), C["bright"], True),
                 S(" · %d complete objectives" % done, C["dim"])))]
    if modes:
        lines.append(L(P(S("modes", C["dim"])), *_sep_parts(
            [[S("%s %d" % (MODE_SHORT.get(m, m[:3]), n),
                MODE_COLOR.get(m, C["dim"]), True)]
             for m, n in sorted(modes.items())], first=" ", sep="  ")))
    decision = {cd.objective_id: cd for cd in snapshot.coordinator_decisions}
    for o in active:
        cd = decision.get(o.objective_id)
        mode = cd.elevation_mode if cd else "?"
        lines.append(L(
            P(S("● ", C["active"]), S(o.objective_id, C["bright"]),
              S("  %s" % MODE_SHORT.get(mode, mode[:3]),
                MODE_COLOR.get(mode, C["dim"]), True),
              S("  %s" % _dur(_seconds(o.created_utc, now)), C["dim"]))))
    stamped = [cd for cd in snapshot.coordinator_decisions if cd.created_utc]
    if stamped:
        last = max(stamped, key=lambda cd: cd.created_utc)
        lines.append(L(
            P(S("last admitted ", C["dim"]), S(_when(last.created_utc, now)),
              S(" (%s)" % _dur(_seconds(last.created_utc, now)), C["dim"])),
            P(S(" " + last.objective_id, C["bright"]),
              S(" → %s" % MODE_SHORT.get(last.elevation_mode,
                                         last.elevation_mode),
                MODE_COLOR.get(last.elevation_mode, C["dim"]), True))))
        for name, source in sorted(last.feature_sources.items()):
            derived = not source.startswith('"')
            lines.append(L(P(S("  %s ← " % name, C["dim"])),
                           P(S(source, C["warn"] if derived else C["text"]))))
    return lines


def _effective_of(run):
    return EFFECTIVE.get(run.effective_state or "unknown",
                         EFFECTIVE["unknown"])


def _effective_groups(runs):
    counts, mark = {}, {}
    for r in runs:
        k = r.effective_state or "unknown"
        counts[k] = counts.get(k, 0) + 1
        mark[k] = _effective_of(r)
    return [[S("%d %s" % (counts[k], k), mark[k][1])]
            for k in sorted(counts, key=lambda k: mark[k][2])]


def _orch_lines(snapshot, now):
    runs = list(snapshot.runs)
    if not runs:
        return [L(P(S("no orchestration runs", C["dim"])))]
    recorded = {}
    for r in runs:
        recorded[r.status] = recorded.get(r.status, 0) + 1
    # the recorded status, then what the runs are (the footer says why
    # the two differ)
    status = (" " + next(iter(recorded)) if len(recorded) == 1 else
              ": " + " ".join("%d %s" % (n, s)
                              for s, n in sorted(recorded.items())))
    lines = [L(P(S("%d runs" % len(runs), C["bright"], True),
                 S(status, C["dim"])),
               *_sep_parts(_effective_groups(runs), first=" → "))]
    runs.sort(key=lambda r: r.created_utc or "", reverse=True)
    runs.sort(key=lambda r: _effective_of(r)[2])
    ow = min(30, max(len(r.objective_id) for r in runs))
    stale = False
    for r in runs:
        sym, color, _ = _effective_of(r)
        since = r.effective_since_utc
        if r.effective_state == "working":
            note = r.effective_note or "in progress"
        elif r.effective_state == "stuck":
            note = "HALTED %s" % _when(since, now) if since else "HALTED"
        elif r.effective_state == "done":  # when the objective completed
            note = _when(since, now) if since else ""
        else:
            note = r.effective_note or ""
        # one run per objective: the objective names the run here, the
        # run id stays in the compact view and --json
        lines.append(L(
            P(S(sym + " ", color),
              S("%s " % MODE_SHORT.get(r.mode, r.mode[:3]),
                MODE_COLOR.get(r.mode, C["dim"]), True),
              S(r.objective_id[:ow].ljust(ow))),
            P(S("  %s" % (r.effective_state or "?"), color)),
            P(S(" · " + note, C["dim"])) if note else None,
            P(S(" · 0 nodes", C["dim"])) if not r.nodes else None))
        if r.status == "ACTIVE" and r.effective_state in ("done", "stuck"):
            stale = True
    if stale:
        lines.append(L(P(S("recorded ACTIVE until an Orchestrator "
                           "COMPLETE_RUN (O2)", C["dim"]))))
    return lines


def _eng_lines(snapshot, now):
    e = snapshot.engineer
    lines = []
    if e.cycles:
        c = e.cycles[0]
        is_open = c.status == "OPEN"
        lines.append(L(
            P(S("cycle %s " % c.cycle_id, C["bright"], True),
              S(c.status, C["accent"] if is_open else C["dim"], is_open)),
            P(S(" · started %s (%s)" % (_when(c.started_utc, now),
                                        _ago(c.started_utc, now)), C["dim"])),
            P(S(" · %d proposal%s" % (c.proposals,
                                      "" if c.proposals == 1 else "s")))))
    else:
        lines.append(L(P(S("no cycles recorded", C["dim"]))))
    planner = snapshot.routing.planner_model
    if planner and planner != "?":
        lines.append(L(P(S("reasons on ", C["dim"]), S(planner, C["luna"]))))
    projects = [p for p in snapshot.projects if p.status == "ACTIVE"]
    closed = len(snapshot.projects) - len(projects)
    lines.append(L(P(S("projects ", C["dim"]),
                     S("%d active" % len(projects), C["bright"]),
                     S(" · %d closed" % closed, C["dim"]))))
    for p in sorted(projects, key=lambda p: p.opened_utc or "",
                    reverse=True):
        done = sum(1 for it in p.items if it.state == "COMPLETED")
        lines.append(L(
            P(S("  %s " % p.project_id[:7], C["dim"]),
              S(p.title or "(untitled)")),
            P(S("  %d/%d done" % (done, len(p.items)),
                C["success"] if p.items and done == len(p.items)
                else C["dim"]))))
    if e.waiting:
        # a wait names its sequence; one that does not is shown by the
        # halted work of its objective, else by the objective
        halted = {}
        for p in snapshot.projects:
            for it in p.items:
                if (it.state or "").startswith("HALTED"):
                    halted.setdefault(p.objective_id, []).append(
                        it.workstream_key)
        by_class = {}
        for p in e.waiting:
            subjects = by_class.setdefault(p.proposal_class, [])
            for name in ([p.workstream_key] if p.workstream_key else
                         halted.get(p.objective_id)
                         or [p.objective_id or "?"]):
                if name not in subjects:
                    subjects.append(name)
        counts = {}
        for p in e.waiting:
            counts[p.proposal_class] = counts.get(p.proposal_class, 0) + 1
        for klass, subjects in sorted(by_class.items()):
            lines.append(L(P(S("⚑ waiting ", C["warn"]),
                             S("%s ×%d" % (klass, counts[klass]), C["warn"])),
                           *_sep_parts([[S(s, C["dim"])] for s in subjects],
                                       first=" · ")))
    if e.staffing_open:
        lines.append(L(P(S("staffing open %d" % len(e.staffing_open))),
                       *_sep_parts([[S("%s → %s" % (
                           s.workstream_key or s.sequence_id[:12],
                           s.specialist_handle or s.mode.replace("_", " ")),
                           C["dim"])] for s in e.staffing_open],
                           first=" · ")))
    if e.decisions_total:
        missing = e.decisions_total - e.decisions_stored
        lines.append(L(
            P(S("reasoning %d decisions" % e.decisions_total, C["dim"])),
            P(S(" · %d text not stored (O4)" % missing, C["warn"])
              if missing else S(" · all stored", C["dim"]))))
    return lines


def _qn(q, n):
    parts = []
    if q:
        parts.append("%dQ" % q)
    if n:
        parts.append("%dN" % n)
    return " ".join(parts)


def _tf_lines(snapshot, now):
    tf = snapshot.taskforce
    counts = []
    if tf.established_count:
        counts.append("%d EST" % tf.established_count)
    if tf.retained_count:
        counts.append("%d RET" % tf.retained_count)
    lines = [L(P(S("%d members" % tf.member_count, C["bright"], True),
                 S(" · " + " · ".join(counts) if counts else "", C["dim"])),
               P(S(" · outside: %s" % ", ".join(tf.outside_pool), C["dim"]))
               if tf.outside_pool else None)]
    main, on = seat_routes(tf.seats)
    if main:
        sample = next(s for s in tf.seats if (
            s.route_name or "%s/%s" % (s.provider or "?", s.model or "?"))
            == main)
        lead = "every seat runs on " if len(on) == 1 else "seats run on "
        line = L(P(S(lead, C["dim"]), S(route_desc(
            sample.route_name, snapshot.routing, sample.provider,
            sample.model), C["luna"])))
        escalation = snapshot.routing.escalation_route
        if escalation and escalation != "?":
            line.append(P(S(" · escalation %s" % escalation, C["dim"])))
        lines.append(line)
    working = [s for s in tf.seats if s.current_work]
    if working:
        lines.append(L(P(S("working now ", C["dim"])), *_sep_parts(
            [[S(s.specialist_handle, C["bright"]),
              S(" → %s" % s.current_work, C["accent"])] for s in working])))
    else:
        lines.append(L(P(S("working now ", C["dim"]),
                         S("nobody", C["dim"]))))
    seats = sorted(tf.seats, key=lambda s: s.since_utc or "")
    seats.sort(key=lambda s: 0 if s.status == "ESTABLISHED" else 1)
    def label(s):
        return "%s/%s" % (s.capability, ROLE_SHORT.get(s.role, s.role))

    if seats:
        cw = min(22, max(len(label(s)) for s in seats))
        hw = min(18, max(len(s.specialist_handle) for s in seats))
        rw = max(len(_qn(s.seat_qualifying, s.seat_not_qualifying) or "-")
                 for s in seats)
    for s in seats:
        busy = bool(s.current_work)
        record = _qn(s.seat_qualifying, s.seat_not_qualifying) or "-"
        extra = _qn(s.other_qualifying, s.other_not_qualifying)
        lines.append(L(
            P(S(("▶" if busy else ROLE_ICON.get(s.role, "·")) + " ",
                C["accent"] if busy else C["tf"]),
              S(label(s)[:cw].ljust(cw))),
            P(S(" " + s.specialist_handle[:hw].ljust(hw), C["bright"])),
            P(S(" %s %s" % (SEAT_STATUS.get(s.status, s.status[:3]),
                            _day(s.since_utc, now)), C["dim"])),
            P(S(" " + record.ljust(rw), C["success"])),
            P(S(" (+%s)" % extra, C["dim"])) if extra else None,
            P(S("  # %d halted" % s.stuck_projects, C["halted"]))
            if s.stuck_projects else None,
            P(S("  ▲ on %s" % (s.route_name or "%s/%s" % (
                s.provider or "?", s.model or "?")), C["warn"]))
            if len(on) > 1 and (s.route_name or "%s/%s" % (
                s.provider or "?", s.model or "?")) != main else None))
    if seats:
        # one row here; --compact lists each holder's records by model
        q = n = 0
        holders, last = set(), None
        for s in seats:
            for _label, eq, en, elast in s.earlier_records or []:
                q, n = q + eq, n + en
                holders.add(s.specialist_handle)
                if elast and (last is None or elast > last):
                    last = elast
        if holders:
            lines.append(L(P(S("made on other models%s: " % (
                " until %s" % _day(last, None) if last else ""), C["dim"]),
                S("%s by %d holder%s" % (_qn(q, n), len(holders),
                                         "" if len(holders) == 1 else "s"),
                  C["warn"]))))
        lines.append(L(P(S("names are handles, not models", C["dim"])),
                       P(S(" · Q/N via the seat", C["dim"])),
                       P(S(" · (+n) without it", C["dim"]))))
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
    filled = int(20 * finished / total) if total else 0
    lines = [L(P(S("sequences ", C["dim"]), S("█" * filled, C["bar_fill"]),
                 S("░" * (20 - filled), C["bar_empty"]),
                 S(" %d/%d finished" % (finished, total), C["bright"])),
               *_sep_parts(_census_groups(seqs, SEQUENCE_TERMINAL,
                                          C["accent"]), first="  "))]
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
    return lines


# ── bands ─────────────────────────────────────────────────────────────

def _exec_lines(snapshot, now):
    lines = []
    for s in snapshot.sessions:
        how = HOW.get(s.selection_path, s.selection_path or "?")
        started = s.started_utc or s.attempt_since_utc
        lines.append(L(
            P(S("▶ ", C["accent"]),
              S(s.workstream_key or s.sequence_id[:16], C["bright"], True),
              S("  %s (%s)" % (s.specialist_handle or "?", how)),
              S(" → %s" % (s.route_name or "%s/%s" % (
                  s.provider or "?", s.model or "?")), C["luna"])),
            P(S("  %s since %s" % (s.attempt_state, _when(
                s.attempt_since_utc, now)), C["accent"])),
            P(S(" · session %s" % _dur(_seconds(started, now)))),
            P(S(" · pid %s" % s.pid, C["dim"])) if s.pid else None,
            P(S(" · " + s.finalization_class, C["dim"]))
            if s.finalization_class else None,
            P(S(" · " + s.objective_id, C["dim"])) if s.objective_id
            else None))
        if s.last_line:
            lines.append(L(P(S("    " + s.last_line.strip(), C["dim"])),
                           P(S("  (%s)" % _ago(s.output_utc, now),
                               C["dim"]))))
        elif s.call_id:
            lines.append(L(P(S("    %s · no output yet" % s.call_id[:20],
                               C["dim"]))))
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
               S(it.workstream_key[:kw].ljust(kw) + " ", C["bright"]),
               S(it.state or "no sequence", color, True), S(since, C["dim"]))]
    if it.holder:
        how = HOW.get(it.selection_path, "")
        parts.append(P(S("   %s%s" % (it.holder, " · " + how if how else ""))))
    state = it.state or ""
    if state.startswith("HALTED"):
        # the halting rule and the receipt state it acted on; the recorded
        # reason text stays in --json
        cause = ("receipt %s" % it.halt_completeness
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
    elif state == "COMPLETED" and it.assignment_outcome:
        parts.append(P(S(" · " + OUTCOME_LABEL.get(it.assignment_outcome, (
            "", it.assignment_outcome.lower()))[1], C["success"])))
    elif state in _QUEUED:
        parts.append(P(S(" · next: %s" % (it.next_staffing or "staffing"),
                         C["dim"])))
    return parts


def _project_tree(project, now, wide=True):
    if not project.items:
        return []
    kw = min(28, max(len(it.workstream_key) for it in project.items))
    by_sequence = {it.sequence_id: it for it in project.items
                   if it.sequence_id}
    children, roots = {}, []
    for it in project.items:
        parents = [e.predecessor_sequence_id for e in it.predecessors
                   if e.predecessor_sequence_id in by_sequence
                   and e.predecessor_sequence_id != it.sequence_id]
        if parents:
            children.setdefault(parents[0], []).append(it)
        else:
            roots.append(it)
    lines, seen = [], set()

    def emit(it, lead, is_last, depth):
        if id(it) in seen:
            return
        seen.add(id(it))
        branch = "" if depth == 0 else ("└▶ " if is_last else "├▶ ")
        lines.append(_item_line(it, lead + branch, kw, now, wide))
        kids = children.get(it.sequence_id, [])
        for i, kid in enumerate(kids):
            emit(kid, lead + ("" if depth == 0 else (
                "   " if is_last else "│  ")), i == len(kids) - 1, depth + 1)

    for i, it in enumerate(roots):
        emit(it, "", i == len(roots) - 1, 0)
    for it in project.items:  # whatever a cycle of edges left out
        if id(it) not in seen:
            emit(it, "", True, 0)
    return lines


def _dag_lines(snapshot, now, wide=True):
    objectives = {o.objective_id: o for o in snapshot.objectives}
    live, closed = [], []
    for p in snapshot.projects:
        o = objectives.get(p.objective_id)
        if p.status == "ACTIVE" or (o and o.status not in _COMPLETE):
            live.append(p)
        else:
            closed.append(p)

    def created(p):
        o = objectives.get(p.objective_id)
        return (o.created_utc if o else "") or ""

    live.sort(key=created, reverse=True)
    lines = []
    # a project heading is one row: the title is free text and comes last,
    # so it is what a narrow row cuts
    for p in live:
        done = sum(1 for it in p.items if it.state == "COMPLETED")
        lines.append(L(P(
            S("◆ ", C["coord"]), S(p.objective_id, C["bright"], True),
            S("  %d/%d done · %s opened %s" % (
                done, len(p.items), p.project_id[:7],
                _when(p.opened_utc, now)), C["dim"]),
            S("  " + p.title))))
        lines.extend(_project_tree(p, now, wide) or [
            L(P(S("   no workstreams yet", C["dim"])))])
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


def _activity_lines(snapshot, now):
    lines = []
    for ev in snapshot.recent_events:
        icon, color = LAYER.get(ev.layer, ("·", C["dim"]))
        subject, _, change = (ev.summary or "").partition(" ")
        lines.append(L(P(S(_precise(ev.timestamp, now), C["dim"]),
                         S(" "), S(icon, color), S(" " + subject)),
                       P(S(" " + change)) if change else None))
    if not lines:
        lines.append(L(P(S("no activity recorded", C["dim"]))))
    return lines


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
    working = [s for s in tf.seats if s.current_work]
    main, _ = seat_routes(tf.seats)
    tfl = L(P(S("⛊ tf     ", C["tf"], True),
              S("%d members" % tf.member_count)),
            P(S(" · on %s" % main, C["luna"])) if main else None,
            P(S(" · working: %s" % (", ".join(
                "%s → %s" % (s.specialist_handle, s.current_work)
                for s in working) or "nobody"),
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
    EXECUTING first, then the work graph, activity takes what is left."""
    body = rows - 3
    if body <= 0:
        return 0, 0, 0
    e = min(max(exec_n, 1), max(1, body // 4))
    rest = body - e
    act_floor = min(max(act_n, 1), max(4, rest * 2 // 5), rest)
    d = min(max(dag_n, 1), max(rest - act_floor, 0))
    return e, d, rest - d


# ── compose frame ─────────────────────────────────────────────────────

def compose(scr, snapshot, tick=0, routing=None):
    """Write the whole frame into the screen's back buffer. ``tick`` and
    ``routing`` are accepted for compatibility: liveness now comes from
    recorded heartbeats, and each seat shows its own route."""
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

    band_w = w - 5
    exec_rows = _layout(_exec_lines(snapshot, now), band_w)
    dag_rows = _layout(_dag_lines(snapshot, now, wide=w >= 100), band_w,
                       indent=8)
    act_rows = _layout(_activity_lines(snapshot, now), band_w, indent=16)
    # what the bands keep before the boxes get their full height: more
    # on a taller terminal
    k = max(3, (h - 30) // 4)
    band_floor = (1 + min(max(len(exec_rows), 1), 2)) + \
        (1 + min(max(len(dag_rows), 1), k)) + \
        (1 + min(max(len(act_rows), 1), k))

    boxes = None
    if w >= 100:
        pw = (w - 6) // 2
        iw, aw = pw - 4, w - 8
        pairs = ((_layout(_coord_lines(snapshot, now), iw),
                  _layout(_orch_lines(snapshot, now), iw)),
                 (_layout(_eng_lines(snapshot, now), iw),
                  _layout(_tf_lines(snapshot, now), iw)))
        auth = _layout(_auth_lines(snapshot, now), aw)
        for caps in ((10, 13, 6), (8, 11, 5), (6, 8, 5), (5, 6, 4),
                     (4, 5, 4), (3, 4, 3)):
            heights = [2 + min(max(len(a), len(b)), caps[i])
                       for i, (a, b) in enumerate(pairs)]
            heights.append(2 + min(len(auth), caps[2]))
            if y + sum(heights) + band_floor <= h:
                boxes = (pw, pairs, auth, heights)
                break

    if boxes:
        pw, pairs, auth, heights = boxes
        x1, x2 = 2, 2 + pw + 2
        spare = h - (y + sum(heights) + band_floor)
        keys = (("coord", "orch"), ("eng", "tf"))
        for i, (a, b) in enumerate(pairs):
            _draw_box(scr, x1, y, pw, heights[i], keys[i][0], a)
            _draw_box(scr, x2, y, pw, heights[i], keys[i][1], b)
            y += heights[i]
            if spare > 8:  # the flow connector between the layers
                scr.put(x1 + pw // 2, y, "│", C["flow_dim"])
                scr.put(x2 + pw // 2, y, "│", C["flow_dim"])
                y += 1
                spare -= 1
        _draw_box(scr, 2, y, w - 4, heights[2], "auth", auth)
        y += heights[2]
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
                    "%d in flight" % len(snapshot.sessions))
        y += 1
        for segs in _more(exec_rows, e_rows):
            _paint(scr, 3, y, segs, band_w)
            y += 1
    if y < h:
        live = sum(1 for p in snapshot.projects if p.status == "ACTIVE")
        _band_title(scr, y, w, "DAG", C["accent"],
                    "work graph · %d active project%s" % (
                        live, "" if live == 1 else "s"))
        y += 1
        for segs in _more(dag_rows, d_rows):
            _paint(scr, 3, y, segs, band_w)
            y += 1
    if y < h:
        newest = snapshot.recent_events[0].timestamp \
            if snapshot.recent_events else None
        _band_title(scr, y, w, "ACTIVITY", C["accent"],
                    "newest %s · %s" % (_when(newest, now),
                                        _ago(newest, now))
                    if newest else None)
        y += 1
        for segs in _more(act_rows, a_rows):
            if y >= h:
                break
            _paint(scr, 3, y, segs, band_w)
            y += 1


# ── stream loop ───────────────────────────────────────────────────────

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
           interval=0.5, once=False, ground=False, project_root=None):
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
    repaint = True

    try:
        if interactive:
            _write(ALT_SCREEN_ON + CLEAR_SCREEN + HIDE_CURSOR + WRAP_OFF)
        while True:
            snapshot = build_snapshot(intel, auth, step_reader=step,
                                      config_reader=config,
                                      autoswarm_reader=autoswarm,
                                      cas_reader=cas)
            if interactive:
                size = _terminal_size()
                if size != (tw, th):
                    tw, th = size
                    scr = Screen(tw, th)
                    repaint = True
            scr.clear()
            compose(scr, snapshot, tick)

            if not interactive:
                _write("\n".join(scr.text_lines(
                    color=sys.stdout.isatty())) + "\n")
                if ground:
                    _ground(snapshot)
                break
            if repaint:
                _write(CLEAR_SCREEN + scr.full_render())
                repaint = False
            else:
                scr.flush(scr.render_diff(scr.diff()))
            scr.swap()
            tick += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if interactive:
            _write(WRAP_ON + SHOW_CURSOR + ALT_SCREEN_OFF)
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
