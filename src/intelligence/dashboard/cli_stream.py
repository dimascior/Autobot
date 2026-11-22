"""CLI streamer — compact live dashboard in the terminal.

Polls the snapshot every N seconds and prints a fixed-height display.
Works with any terminal; no dependencies beyond the stdlib.

Every time shown is UTC: HH:MM:SS when it falls on the snapshot's day,
MM-DD HH:MM otherwise. Ages and durations are measured against the
snapshot's capture time.
"""

import os
import shutil
import time
from datetime import datetime, timezone

from .views import ActivitySnapshot

STATUS_SYMBOLS = {
    "ACTIVE": "+", "COMPLETE": "*", "COMPLETED": "*",
    "PENDING": ".", "EXECUTING": ">", "RUNNING": ">", "FAILED": "!",
    "HALTED": "#", "BLOCKED": "~", "READY": "-",
    "CANCELLED": "x", "SUPERSEDED": "^",
}

MODE_LABEL = {
    "DIRECT": "DIR", "ENGINEER_ELEVATED": "ENG",
    "ORCHESTRATOR_ELEVATED": "ORC",
}

# A settled assignment is shown by its outcome, not by "SETTLED".
OUTCOME_LABEL = {
    "VERIFIED_ACCEPTED": ("*", "accepted"),
    "ATTEMPT_FAILED": ("!", "failed"),
}

EFFECTIVE_SYMBOL = {
    "working": ">", "stuck": "#", "waiting": "~", "done": "*",
    "unknown": "?",
}
EFFECTIVE_ORDER = {"working": 0, "stuck": 1, "waiting": 2, "unknown": 3,
                   "done": 4}

WORK_SYMBOL = {
    "HALTED": "#", "working": ">", "blocked": "~", "queued": "-",
    "planned": ".", "no work": ".", "work finished": "*",
}

SEAT_STATUS = {"ESTABLISHED": "EST", "RETAINED": "RET",
               "RELEASED": "RELEASED"}

_COMPLETE = ("COMPLETE", "COMPLETED")


def _sym(status):
    return STATUS_SYMBOLS.get(status, "?")


def _trunc(text, width):
    if not text:
        return ""
    return text[:width] if len(text) <= width else text[:width - 1] + "…"


def _bar(label, width=72):
    label = _trunc(label, max(width - 6, 8))
    pad = width - len(label) - 4
    return "══ %s %s" % (label, "═" * max(pad, 2))


# ------------------------------------------------------------------ time

def _parse_utc(ts):
    """An ISO-8601 instant as an aware UTC datetime, or None."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if not ts or not isinstance(ts, str):
        return None
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _when(ts, now):
    """HH:MM:SS on the snapshot's day, MM-DD HH:MM otherwise."""
    dt = _parse_utc(ts)
    if dt is None:
        return "?"
    if now is not None and dt.date() == now.date():
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%m-%d %H:%M")


def _day(ts, now):
    """HH:MM on the snapshot's day, MM-DD otherwise."""
    dt = _parse_utc(ts)
    if dt is None:
        return "?"
    if now is not None and dt.date() == now.date():
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d")


def _precise(ts, now):
    """HH:MM:SS.mmm on the snapshot's day, MM-DD HH:MM:SS otherwise."""
    dt = _parse_utc(ts)
    if dt is None:
        return (ts or "?")[-12:]
    if now is not None and dt.date() == now.date():
        return dt.strftime("%H:%M:%S.") + "%03d" % (dt.microsecond // 1000)
    return dt.strftime("%m-%d %H:%M:%S")


def _seconds(start, end):
    a, b = _parse_utc(start), _parse_utc(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def _dur(seconds):
    """2m27s, 23h25m, 1d00h18m."""
    if seconds is None:
        return "?"
    s = int(max(seconds, 0))
    if s < 60:
        return "%ds" % s
    m, s = divmod(s, 60)
    if m < 60:
        return "%dm%02ds" % (m, s)
    h, m = divmod(m, 60)
    if h < 24:
        return "%dh%02dm" % (h, m)
    d, h = divmod(h, 24)
    return "%dd%02dh%02dm" % (d, h, m)


def _with_tail(head, tail, width, indent=6):
    """``head`` (already padded to its columns) and ``tail`` on one line
    when they fit, else ``tail`` on its own indented line."""
    if not tail:
        return [_trunc(head.rstrip(), width)]
    if len(head) + 1 + len(tail) <= width:
        return [head + " " + tail]
    return [_trunc(head.rstrip(), width),
            _trunc(" " * indent + tail, width)]


def _pack(first, parts, width, indent=6, sep=" · "):
    """``first`` followed by ``parts`` joined with ``sep``. A part that does
    not fit moves whole to an indented continuation line; only a part
    wider than a whole line is cut, with "…". Callers pass states, times,
    ids and counts as their own short parts, so those are never cut."""
    lines = []
    line = first
    fresh = True    # nothing after the prefix on this line yet
    for part in parts:
        if not part:
            continue
        joiner = "" if fresh else sep
        if len(line) + len(joiner) + len(part) <= width:
            line += joiner + part
            fresh = False
            continue
        if not fresh:
            lines.append(line)
            line = " " * indent
        line += _trunc(part, max(width - len(line), 1))
        fresh = False
    lines.append(line)
    return [_trunc(l.rstrip(), width) for l in lines]


# -------------------------------------------------------------- sections

def _objectives(lines, snapshot, w, now, complete_shown=3):
    active = [o for o in snapshot.objectives if o.status not in _COMPLETE]
    done = [o for o in snapshot.objectives if o.status in _COMPLETE]
    active.sort(key=lambda o: o.created_utc or "", reverse=True)
    done.sort(key=lambda o: o.completed_utc or o.created_utc or "",
              reverse=True)
    lines.append(_bar("OBJECTIVES  %d active · %d complete"
                      % (len(active), len(done)), w))
    if not snapshot.objectives:
        lines.append("  (none)")
    idw = max(24, min(34, w - 44))
    for o in active:
        lines.append(_trunc("  %s %-*s %-8s since %-11s %9s" % (
            _sym(o.status), idw, o.objective_id[:idw], o.status,
            _when(o.created_utc, now), _dur(_seconds(o.created_utc, now))),
            w))
        note = o.work_state or "?"
        if o.work_note:
            note += ": %s" % o.work_note
        if o.work_since_utc:
            note += " since %s" % _when(o.work_since_utc, now)
        # the title is free text: it moves to its own line before it is cut
        lines.extend(_pack("      %s " % WORK_SYMBOL.get(o.work_state, "?"),
                           [note, o.text], w, indent=8))
    for o in done[:complete_shown]:
        tail = "%s → %s %9s" % (
            _when(o.created_utc, now), _when(o.completed_utc, now),
            _dur(_seconds(o.created_utc, o.completed_utc)))
        width = max(16, min(idw, w - 15 - len(tail)))
        lines.append(_trunc("  %s %-*s %-8s %s" % (
            _sym(o.status), width, o.objective_id[:width], "COMPLETE",
            tail), w))
    rest = done[complete_shown:]
    if rest:
        oldest = min(o.created_utc or "" for o in rest)
        newest = max(o.completed_utc or o.created_utc or "" for o in rest)
        lines.append("    + %d more complete, %s to %s" % (
            len(rest), _day(oldest, None), _day(newest, None)))
    lines.append("")


def _activity_bits(s, now):
    """What a live session has done: its own record (a Codex rollout),
    else its stdout's last line, else that nothing is recorded yet."""
    if s.activity_events:
        bits = ["%d tool call%s" % (s.activity_tools,
                                    "" if s.activity_tools == 1 else "s")]
        if s.activity_done:
            bits.append("task complete")
        elif s.activity_doing:
            bits.append(_trunc(s.activity_doing, 40))
        if s.activity_utc:
            bits.append("last %s ago" % _dur(_seconds(s.activity_utc, now)))
        return bits
    if s.last_line:
        return [_trunc(s.last_line.strip(), 60),
                "%s ago" % _dur(_seconds(s.output_utc, now))]
    return ["no activity recorded yet"]


def _sessions(lines, snapshot, w, now):
    """The attempts with a live session (DISPATCHED or RUNNING); those past
    their session or not yet started are counted in the title."""
    sessions = list(snapshot.sessions)
    ended = sum(1 for s in sessions if s.session_state == "finished")
    head = "SESSIONS  %d running" % (len(sessions) - ended)
    if ended:
        head += " · %d ended, not collected" % ended
    after = snapshot.after_session or {}
    if after:
        head += " · %d past the session (%s)" % (
            sum(after.values()),
            ", ".join("%s %d" % (k, n) for k, n in sorted(after.items())))
    if snapshot.before_session:
        head += " · %d not started" % snapshot.before_session
    lines.append(_bar(head, w))
    if not sessions:
        lines.append("  none running")
    for s in sessions:
        how = {"RETAINED_SEAT": "seat",
               "OUTSIDE_SPECIALIST": "outside"}.get(
                   s.selection_path, s.selection_path or "?")
        work = s.workstream_key or s.sequence_id[:16]
        finished = s.session_state == "finished"
        # the route first: a handle is a name, not a model
        lines.extend(_pack("  %s " % ("." if finished else ">"), [
            work, s.objective_id or "?",
            route_desc(s.route_name, snapshot.routing, s.provider, s.model),
            "%s (%s)" % (s.specialist_handle or "?", how)], w))
        detail = []
        if s.call_id:
            detail.append(_trunc(s.call_id, 15))
        else:
            detail.append("not dispatched")
        detail.append("%s since %s" % (
            s.attempt_state, _when(s.attempt_since_utc, now)))
        started = s.started_utc or s.attempt_since_utc
        if finished:
            detail.append("session ended %s after %s" % (
                _when(s.finished_utc, now),
                _dur(_seconds(started, s.finished_utc))))
            detail.append("result not collected yet")
        else:
            detail.append("session %s" % _dur(_seconds(started, now)))
            if s.pid:
                detail.append("pid %s" % s.pid)
        if s.finalization_class:
            detail.append(s.finalization_class)
        lines.extend(_pack("      ", detail, w))
        if not finished:
            lines.extend(_pack("      ", _activity_bits(s, now), w))
    lines.append("")


def _coordinator(lines, snapshot, w):
    if not snapshot.coordinator_decisions:
        return
    lines.append(_bar("COORDINATOR", w))
    for cd in snapshot.coordinator_decisions:
        feats = ", ".join(sorted(k for k, v in cd.features.items() if v)) \
            if cd.features else "-"
        head = "  %-34s %-22s" % (cd.objective_id[:34], cd.elevation_mode)
        lines.extend(_with_tail(head, feats, w))
    lines.append("")


def _runs(lines, snapshot, w, now):
    runs = list(snapshot.runs)
    recorded = {}
    effective = {}
    for r in runs:
        recorded[r.status] = recorded.get(r.status, 0) + 1
        key = r.effective_state or "unknown"
        effective[key] = effective.get(key, 0) + 1
    rec = " · ".join("%d recorded %s" % (n, s)
                     for s, n in sorted(recorded.items()))
    eff = " · ".join("%d %s" % (effective[k], k)
                     for k in sorted(effective, key=lambda k:
                                     EFFECTIVE_ORDER.get(k, 9)))
    lines.append(_bar("RUNS  %s → %s" % (rec, eff) if runs else "RUNS", w))
    o = snapshot.orchestrator
    if o is not None and o.cycles:
        # the Orchestrator policy runs on the Engineer's configured model
        lines.extend(_pack("  planned by ", [
            o.policy_id, "on %s" % (o.model or "?"),
            "%d reasoning cycles, %d decisions" % (o.cycles, o.decisions),
            "last %s" % _when(o.last_utc, now)], w))
        if o.not_applied:
            lines.extend(_pack("  not applied: ", [
                "%s x%d" % (k, n) for k, n in sorted(o.not_applied.items())],
                w))
    if not runs:
        lines.append("  (no orchestration runs)")
    runs.sort(key=lambda r: r.created_utc or "", reverse=True)
    runs.sort(key=lambda r: EFFECTIVE_ORDER.get(r.effective_state, 9))
    stale = False
    for r in runs:
        mode = MODE_LABEL.get(r.mode, r.mode[:3])
        since = (_when(r.effective_since_utc, now)
                 if r.effective_since_utc else None)
        note = r.effective_note or ""
        short = {"done": "COMPLETE", "stuck": "HALTED"}.get(
            r.effective_state, note)
        if since:
            note += (" " if r.effective_state == "done" else " since ") + since
            if r.effective_state in ("done", "stuck"):
                short += " " + since
        if not r.nodes:
            note += " · 0 nodes"
            short += " · 0 nodes"
        head = "  %s %-12s %-3s %-30s %-7s" % (
            EFFECTIVE_SYMBOL.get(r.effective_state, "?"),
            r.orchestration_id[:12], mode, r.objective_id[:30],
            r.effective_state or "?")
        # the full note when it fits; else the short one when that fits on
        # the row; else the full note on its own line
        if len(head) + 1 + len(note) > w and len(head) + 1 + len(short) <= w:
            note = short
        lines.extend(_with_tail(head, note, w))
        if r.status == "ACTIVE" and r.effective_state in ("done", "stuck"):
            stale = True
    if stale:
        lines.append(_trunc("  recorded status: set at admission, cleared"
                            " only by Orchestrator COMPLETE_RUN", w))
    lines.append("")


def _authority(lines, snapshot, w):
    lines.append(_bar("AUTHORITY", w))
    census = snapshot.authority
    lines.extend(_pack("  sequences  ", [
        "%s:%d" % (s, n) for s, n in sorted(
            census.sequences_by_state.items())], w, indent=13, sep="  "))
    lines.extend(_pack("  attempts   ", [
        "%s:%d" % (s, n) for s, n in sorted(
            census.attempts_by_state.items())], w, indent=13, sep="  "))
    lines.append("  CAS: %d objects   released_edges: %d" % (
        census.cas_object_count, census.released_edge_count))
    lines.append("")


def _qn(qualifying, not_qualifying):
    parts = []
    if qualifying:
        parts.append("%dQ" % qualifying)
    if not_qualifying:
        parts.append("%dN" % not_qualifying)
    return " ".join(parts)


# A specialist's handle is a name, not a model (config.local.toml keeps the
# first handles from when each worker was pinned to the model in its name).
# What a seat runs on is its holder's route: execution state, [routing].

def route_desc(name, routing, provider=None, model=None):
    """'luna-6-max (codex/gpt-6-luna, effort max)' from the configured
    routes; the bare provider/model when no route matches."""
    spec = (routing.routes or {}).get(name) if name else None
    if spec:
        effort = spec.get("effort")
        return "%s (%s/%s%s)" % (name, spec.get("agent", "?"),
                                 spec.get("model", "?"),
                                 ", effort %s" % effort if effort else "")
    if name:
        return name
    return "%s/%s" % (provider or "?", model or "?")


def seat_label(seat):
    return seat.route_name or "%s/%s" % (seat.provider or "?",
                                         seat.model or "?")


def seat_routes(seats):
    """(most common route label, {label: number of seats})."""
    on = {}
    for s in seats:
        on[seat_label(s)] = on.get(seat_label(s), 0) + 1
    main = max(sorted(on), key=lambda k: on[k]) if on else None
    return main, on


def earlier_groups(seats):
    """Records each holder made on another model than the one it runs on
    now: 'claude-sonnet 3Q 1N on sonnet until 11-18'."""
    groups = []
    for s in seats:
        for label, q, n, last in s.earlier_records or []:
            text = "%s %s on %s" % (s.specialist_handle, _qn(q, n) or "0",
                                    label)
            if last:
                text += " until %s" % _day(last, None)
            groups.append(text)
    return groups


def _taskforce(lines, snapshot, w, now):
    tf = snapshot.taskforce
    counts = []
    if tf.established_count:
        counts.append("%d ESTABLISHED" % tf.established_count)
    if tf.retained_count:
        counts.append("%d RETAINED" % tf.retained_count)
    other = tf.seat_count - tf.established_count - tf.retained_count
    if other:
        counts.append("%d other" % other)
    head = "TASKFORCE  %d members · %s" % (
        tf.member_count, " · ".join(counts) or "no seats")
    if tf.outside_pool:
        head += " · outside: %s" % ", ".join(tf.outside_pool)
    lines.append(_bar(head, w))
    main, on = seat_routes(tf.seats)
    if main:
        sample = next(s for s in tf.seats if seat_label(s) == main)
        parts = [route_desc(sample.route_name, snapshot.routing,
                            sample.provider, sample.model)]
        escalation = snapshot.routing.escalation_route
        if escalation and escalation != "?":
            parts.append("escalation %s" % escalation)
        held = any(s.status in ("ESTABLISHED", "RETAINED") for s in tf.seats)
        lines.extend(_pack("  %s" % (
            "specialists run on " if not held else
            "every seat runs on " if len(on) == 1 else "seats run on "),
            parts, w))
    seats = sorted(tf.seats, key=lambda s: s.since_utc or "")
    seats.sort(key=lambda s: 0 if s.status == "ESTABLISHED" else 1)
    for s in seats:
        seat = "%s/%s" % (s.capability, s.role)
        standing = "%s %s" % (SEAT_STATUS.get(s.status, s.status[:3]),
                              _day(s.since_utc, now))
        mine = _qn(s.seat_qualifying, s.seat_not_qualifying) or "-"
        extra = _qn(s.other_qualifying, s.other_not_qualifying)
        record = "%-6s %s" % (mine, "(+%s)" % extra if extra else "")
        tail = []
        if s.current_work:
            tail.append("> %s" % s.current_work)
        if s.stuck_projects:
            tail.append("# %d halted" % s.stuck_projects)
        if len(on) > 1 and seat_label(s) != main:
            tail.append("^ on %s" % seat_label(s))
        head = "  %-24s %-18s %-9s %-13s" % (
            seat[:24], s.specialist_handle[:18], standing, record)
        lines.extend(_with_tail(head, "  ".join(tail), w))
    if tf.seats:
        earlier = earlier_groups(tf.seats)
        if earlier:
            lines.extend(_pack("  made on other models: ", earlier, w,
                               indent=4))
        lines.extend(_pack("  ", [
            "names are handles, not models", "Q/N: through the seat",
            "(+n): without it", "# n: teams with HALTED work"], w))
    lines.append("")


def _assignments(lines, snapshot, w, now):
    if not snapshot.recent_assignments:
        return
    lines.append(_bar("ASSIGNMENTS  newest first", w))
    wide = w >= 96   # room for the capability and the staffing path
    fixed = 2 + 2 + 19 + 10 + 8 + (16 + 9 if wide else 0)
    kw = max(16, min(36, w - fixed))
    for a in snapshot.recent_assignments[:5]:
        if a.status == "SETTLED":
            sym, label = OUTCOME_LABEL.get(
                a.outcome, ("?", (a.outcome or "settled").lower()))
            when = a.settled_utc or a.selected_utc
        else:
            sym = ">" if a.status == "EXECUTING" else "."
            label = a.status.lower()
            when = a.selected_utc
        work = a.workstream_key or a.sequence_id
        row = "  %s %-18s " % (sym, a.specialist_handle[:18])
        if wide:
            row += "%-15s " % a.capability[:15]
        row += "%-*s %-9s %s" % (kw, _trunc(work, kw), label,
                                  _when(when, now))
        if wide:
            row += "  %s" % ("outside" if a.selection_path
                             == "OUTSIDE_SPECIALIST" else "seat")
        lines.append(_trunc(row, w))
    lines.append("")


def _activity(lines, snapshot, w, now):
    lines.append(_bar("ACTIVITY", w))
    for ev in snapshot.recent_events[:12]:
        # the subject stays on the row; the transition moves to its own
        # line rather than lose its end state
        subject, _, change = (ev.summary or "").partition(" ")
        lines.extend(_with_tail("  %s [%-12s] %s" % (
            _precise(ev.timestamp, now), ev.layer[:12], subject), change, w))
    if not snapshot.recent_events:
        lines.append("  (no recent activity)")


def render(snapshot, width=80):
    """Render an ActivitySnapshot as a list of text lines."""
    lines = []
    w = width - 2
    now = _parse_utc(snapshot.captured_utc) or datetime.now(timezone.utc)

    lines.append(_bar("AUTOBOT", w))
    lines.append("  %s UTC" % now.strftime("%Y-%m-%d %H:%M:%S"))
    routing = snapshot.routing
    models = []
    if routing.planner_model and routing.planner_model != "?":
        models.append("Engineer %s" % routing.planner_model)
    if routing.default_route and routing.default_route != "?":
        models.append("specialists %s" % routing.default_route)
    if routing.escalation_route and routing.escalation_route != "?":
        models.append("escalation %s" % routing.escalation_route)
    if models:
        lines.extend(_pack("  models: ", models, w, indent=10))
    lines.append("")

    _objectives(lines, snapshot, w, now)
    _sessions(lines, snapshot, w, now)
    _coordinator(lines, snapshot, w)
    _runs(lines, snapshot, w, now)
    _authority(lines, snapshot, w)
    _taskforce(lines, snapshot, w, now)
    _assignments(lines, snapshot, w, now)
    _activity(lines, snapshot, w, now)
    return lines


def _terminal_width():
    """The terminal's width, kept between 80 and 160 columns."""
    columns = shutil.get_terminal_size((80, 24)).columns
    return max(80, min(columns, 160))


def stream(intelligence_path, authority_path, step_log_path=None,
           interval=3.0, once=False, project_root=None):
    """Poll and render the dashboard continuously."""
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
    home = os.environ.get("AUTOSWARM_HOME") or config._load().get(
        "paths", {}).get("autoswarm_home")
    autoswarm = AutoSwarmReader(home) if home else None
    cas = CASReader(Path(authority_path).parent / "store" / "objects")

    try:
        while True:
            snapshot = build_snapshot(intel, auth, step_reader=step,
                                      config_reader=config,
                                      autoswarm_reader=autoswarm,
                                      cas_reader=cas)
            lines = render(snapshot, width=_terminal_width())

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
