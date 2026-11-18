"""Route preflight: what production will execute on, resolved and — when
asked — exercised, before any engineering work is dispatched.

Two levels:

``routing_report``   resolves the configured policy without spending a
                     model call: the Engineer's model, every declared
                     route with the provider's own identifiers, which
                     route each class of work resolves to, how every
                     specialist in the catalog is routed, and whether
                     AutoSwarm can launch each provider.

``probe_routes``     launches one small real session per role on the
                     routes production uses — a specialist session and an
                     independent reviewer session on the default route (two
                     sessions, at the same time), one on the escalation
                     route — and asks the Engineer's model one question.
                     Each specialist session runs in an isolated worktree
                     on the same READ_ONLY contract text production work
                     gets, with its permission requests answered by the
                     same supervision policy. The report records the
                     identifiers each provider was launched with and what
                     it answered.

A preflight is outside the authority plane: it creates no objective, no
contract and no attempt, and nothing it produces is accepted state.
"""

import time

from . import supervision
from .prompts import compose_specialist_prompt
from .routing import route_identity

PROBE_FILE = "README.md"
PROBE_MARK = "PREFLIGHT"


def _first_line(path):
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    return line
    except OSError:
        return None
    return None


def probe_prompt():
    """The preflight task, on the READ_ONLY contract production work
    runs on (so the session meets the same instructions and the same
    command governance)."""
    return compose_specialist_prompt(
        "This is a route preflight, not an engineering task. Read the"
        " first non-empty line of %s in your working directory and reply"
        " with exactly one line: %s followed by a space and that line."
        % (PROBE_FILE, PROBE_MARK), "READ_ONLY")


def routing_report(pool, engineer_model, providers=None):
    """The routing policy as production will apply it. ``providers`` is
    AutoSwarm's own availability listing ({provider: bool}) when it was
    asked; None when it was not."""
    routing = getattr(pool, "routing", None)
    report = {
        "engineer": {"provider": "claude-code", "model": engineer_model,
                     "roles": ["ENGINEER", "PROJECT_MANAGER"]},
        "policy": routing.describe() if routing is not None else None,
        "resolved": {},
        "specialists": [],
        "providers": providers,
        "problems": [],
    }
    if not engineer_model:
        report["problems"].append("the Engineer's model is not configured")
    if routing is not None:
        for route_class in ("default", "escalation", "utility"):
            try:
                report["resolved"][route_class] = route_identity(
                    routing.resolve(route_class))
            except Exception as exc:  # RoutingError: reported, not raised
                if route_class == "default" or getattr(
                        routing, route_class, None) is not None:
                    report["problems"].append(str(exc))
    for profile in pool.profiles():
        report["specialists"].append({
            "handle": profile["handle"],
            "specialist_id": profile["specialist_id"],
            "route": profile.get("route_name"),
            "provider": profile["provider"],
            "model": profile["model"],
            "effort": profile["effort"],
            "offers": ["%s:%s" % tuple(o) for o in profile["offers"]],
        })
    if providers is not None:
        used = {s["provider"] for s in report["specialists"]}
        for route in (report["policy"] or {}).get("routes", []):
            used.add(route["provider"])
        for provider in sorted(used):
            if not providers.get(provider):
                report["problems"].append(
                    "AutoSwarm cannot launch the provider %r" % provider)
    return report


def _probe_plan(pool):
    """Which sessions a probe launches: (label, route)."""
    routing = getattr(pool, "routing", None)
    if routing is None:
        seen, plan = set(), []
        for profile in pool.profiles():
            key = (profile["provider"], profile["model"], profile["effort"])
            if key in seen:
                continue
            seen.add(key)
            plan.append(("specialist %s" % profile["handle"], {
                "name": profile["handle"], "provider": profile["provider"],
                "model": profile["model"], "effort": profile["effort"],
                "provider_args": list(profile.get("provider_args") or [])}))
        return plan
    default = routing.resolve("default")
    plan = [("specialist", default), ("independent reviewer", default)]
    if routing.escalation is not None:
        plan.append(("escalation", routing.resolve("escalation")))
    return plan


def probe_routes(pool, transport, engineer_model, reasoner=None,
                 gate_root=None, repo_path=None, timeout_seconds=900,
                 poll_seconds=3.0, sleep=time.sleep, clock=time.time,
                 on_event=None):
    """Exercise the routes. ``transport`` provides ``probe_route``,
    ``probe_result``, ``pending_requests``, ``decide_request`` and
    ``release_workspace``; ``reasoner`` is the Engineer's reasoner (a
    callable taking a prompt), or None to leave the Engineer unprobed.
    Returns the list of probe records; a record's ``ok`` says whether that
    route did what was asked."""
    say = on_event or (lambda text: None)
    expected = None
    records = []
    for label, route in _probe_plan(pool):
        record = {"label": label, "route": route_identity(route),
                  "ok": False, "requests": []}
        started = clock()
        try:
            launched = transport.probe_route(route, probe_prompt())
        except Exception as exc:
            record["error"] = "launch failed: %s" % exc
            records.append(record)
            say("%s: %s" % (label, record["error"]))
            continue
        record.update({"call_id": launched["call_id"],
                       "workspace": launched.get("workspace"),
                       "started": started})
        if expected is None and launched.get("workspace"):
            expected = _first_line("%s/%s" % (launched["workspace"],
                                              PROBE_FILE))
        say("%s: launched %s on %s" % (label, launched["call_id"],
                                       record["route"]))
        records.append(record)
    open_records = [r for r in records if r.get("call_id")
                    and "error" not in r]
    deadline = clock() + timeout_seconds
    while open_records and clock() < deadline:
        for record in list(open_records):
            call_id = record["call_id"]
            context = {"finalization_class": "READ_ONLY",
                       "workspace": record.get("workspace"),
                       "scope_paths": [], "gate_root": gate_root,
                       "repo_path": repo_path, "shell_allowed": False}
            for request in transport.pending_requests(call_id):
                decision = supervision.decide(request, context)
                if decision["decision"] is None:
                    continue
                transport.decide_request(
                    call_id, request["request_id"], decision["decision"],
                    decision["reason"])
                record["requests"].append({
                    "tool": decision["tool"],
                    "decision": decision["decision"]})
            result = transport.probe_result(call_id)
            if result is None:
                continue
            record["seconds"] = round(clock() - record.pop("started"), 1)
            record["outcome"] = result.get("outcome")
            record["native_session_id"] = result.get("native_session_id")
            record["launched_with"] = result.get("requested_selection")
            content = result.get("content") or ""
            record["answer"] = content.strip()[:300]
            record["ok"] = (
                result.get("outcome") == "completed"
                and PROBE_MARK in content
                and (expected is None or expected in content))
            if not record["ok"] and result.get("error"):
                record["error"] = str(result["error"])[:300]
            try:
                transport.release_workspace(call_id)
            except Exception:
                pass
            open_records.remove(record)
            say("%s: %s in %ss — %s" % (
                record["label"], "OK" if record["ok"] else "FAILED",
                record["seconds"], record["answer"][:120]))
        if open_records:
            sleep(poll_seconds)
    for record in open_records:
        record.pop("started", None)
        record["error"] = "no result within %d seconds" % timeout_seconds
        say("%s: %s" % (record["label"], record["error"]))
    for record in records:
        record.pop("started", None)
    engineer = {"label": "engineer", "ok": False,
                "route": {"provider": "claude-code",
                          "model": engineer_model}}
    if reasoner is not None:
        started = clock()
        try:
            answer = reasoner(
                "This is a route preflight. Reply with exactly:"
                " %s ENGINEER" % PROBE_MARK)
            engineer["answer"] = str(answer).strip()[:200]
            engineer["ok"] = PROBE_MARK in str(answer)
            meta = getattr(reasoner, "last_meta", None) or {}
            if meta.get("models"):
                engineer["served_by"] = meta["models"]
            if meta.get("session_id"):
                engineer["native_session_id"] = meta["session_id"]
        except Exception as exc:
            engineer["error"] = str(exc)[:300]
        engineer["seconds"] = round(clock() - started, 1)
        say("engineer: %s — %s" % (
            "OK" if engineer["ok"] else "FAILED",
            engineer.get("answer") or engineer.get("error")))
        records.append(engineer)
    return records


def independent_sessions(records):
    """Whether the specialist and the independent reviewer probes ran as
    two distinct sessions (distinct calls and distinct native sessions),
    whatever route they shared."""
    pair = [r for r in records
            if r.get("label") in ("specialist", "independent reviewer")]
    if len(pair) != 2:
        return None
    first, second = pair
    return bool(
        first.get("call_id") and second.get("call_id")
        and first["call_id"] != second["call_id"]
        and first.get("native_session_id")
        and second.get("native_session_id")
        and first["native_session_id"] != second["native_session_id"])
