"""Recognized correlation facts over producer-owned witness bytes (D-C3-5).

Producer bytes are admitted EXACTLY as produced (D-21/D-38): nothing here
rewrites, strips, normalizes or injects. Parsing is tolerant and exists
only to extract the RECOGNIZED correlation facts each registered witness
kind can carry — the fact families observed on the real producers (the
provider's task/handle/result record family for obj-11, the per-command
governance evidence record for obj-12, the session rollout/transcript for
obj-13). Unparseable bytes or absent keys contribute no facts and refuse
nothing; unknown fields grant nothing (INV-22). Recognized facts inform
the admission-time identity gate, the receipt's causal joins and the
contradiction rules (D-C3-6) — never mtime, never ordering, never agent
prose.
"""

import json

# kind -> recognized fact name -> producer spellings (first present wins).
_RECOGNIZED = {
    "obj-11": {
        "call_id": ("call_id",),
        "agent_id": ("agent_id",),
        "controller_id": ("controller_id",),
        "native_session_id": ("native_session_id",),
        "provider": ("provider",),
        "outcome": ("outcome",),
        "exit_code": ("exit_code",),
        "phase": ("phase",),
    },
    "obj-12": {
        "session_id": ("session_id",),
        "tool_use_id": ("tool_use_id",),
        "correlation_id": ("correlation_id",),
        "command_sha256": ("command_sha256",),
        "decision": ("decision",),
        "hook_event": ("hook_event",),
        "executed_utc": ("executed_utc",),
        "success": ("success",),
    },
    "obj-13": {
        "native_session_id": ("native_session_id", "session_id"),
        "call_id": ("call_id",),
        "provider": ("provider",),
    },
    "obj-14": {},
}

# The fact that names the provider-native session, per kind.
SESSION_FACT = {"obj-11": "native_session_id", "obj-12": "session_id",
                "obj-13": "native_session_id"}

def controller_well_formed(value):
    """The U-07 controller-join shape: (call|main)_<32 lowercase hex> —
    the provider contract guarantees the field; this validates it."""
    if not isinstance(value, str):
        return False
    prefix, sep, rest = value.partition("_")
    return (sep == "_" and prefix in ("call", "main") and len(rest) == 32
            and all(c in "0123456789abcdef" for c in rest))


def extract_facts(witness_kind, data):
    """The tolerant recognizer: exact producer bytes in, recognized facts
    out. Returns {} for non-JSON, non-object or fact-free payloads —
    tolerance is the contract, and nothing is ever rewritten."""
    recognized = _RECOGNIZED.get(witness_kind)
    if not recognized:
        return {}
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    source = payload
    if witness_kind == "obj-13" and isinstance(payload.get("payload"), dict):
        # The session rollout's first event nests identity under
        # payload (session_meta); recognize it without rewriting.
        merged = dict(payload["payload"])
        merged.update({k: v for k, v in payload.items() if k != "payload"})
        source = merged
    facts = {}
    for name, spellings in recognized.items():
        for spelling in spellings:
            value = source.get(spelling)
            if isinstance(value, (str, int, bool)) and value != "":
                facts[name] = value
                break
    if witness_kind == "obj-13" and "native_session_id" not in facts:
        value = source.get("id")
        if isinstance(value, str) and value:
            facts["native_session_id"] = value
    return facts


def identity_conflicts(witness_kind, facts, call_id, native_session_id):
    """Admission-gate conflicts: recognized facts that NAME the execution
    identity must match the ledger's bound call (absence conflicts with
    nothing — missing is never contradiction)."""
    conflicts = []
    named_call = facts.get("call_id")
    if isinstance(named_call, str) and named_call != call_id:
        conflicts.append(
            "the witness names call %s but this attempt's bound call is %s"
            % (named_call, call_id))
    session_fact = SESSION_FACT.get(witness_kind)
    if session_fact and native_session_id:
        named = facts.get(session_fact)
        if isinstance(named, str) and named != native_session_id:
            conflicts.append(
                "the witness names session %s but this attempt's bound"
                " session is %s" % (named, native_session_id))
    return conflicts


def join_verdict(witness_kind, facts, call_id, native_session_id):
    """JOINED when a recognized identity fact ties the witness to the
    bound execution; UNJOINED when no identity fact is present;
    CONTRADICTED when a present fact names another execution."""
    if identity_conflicts(witness_kind, facts, call_id, native_session_id):
        return "CONTRADICTED"
    if facts.get("call_id") == call_id:
        return "JOINED"
    session_fact = SESSION_FACT.get(witness_kind)
    if (session_fact and native_session_id
            and facts.get(session_fact) == native_session_id):
        return "JOINED"
    return "UNJOINED"


def deny_then_ran(entries):
    """The U-01 deterministic contradiction (D-C3-6): a governance DENY
    for (command_sha256, session) joined BY CONTENT HASH AND SESSION
    IDENTITY — never wall-clock proximity — to evidence that the same
    command executed in the same session. ``entries`` are
    (entry_key_tuple, witness_kind, facts) for one attempt's admitted set.
    Returns sorted contradiction dicts."""
    denies = []
    executions = []
    for _key, kind, facts in entries:
        if kind != "obj-12":
            continue
        sha = facts.get("command_sha256")
        if not isinstance(sha, str) or not sha:
            continue
        decision = facts.get("decision")
        if isinstance(decision, str) and decision.upper() == "DENY":
            denies.append((sha, facts.get("session_id")))
        elif facts.get("hook_event") == "PostToolUse" \
                or facts.get("success") is True:
            executions.append((sha, facts.get("session_id")))
    found = set()
    for sha, deny_session in denies:
        for run_sha, run_session in executions:
            if run_sha != sha:
                continue
            if (isinstance(deny_session, str)
                    and isinstance(run_session, str)
                    and deny_session != run_session):
                continue  # different sessions: no join
            found.add((sha, deny_session if isinstance(deny_session, str)
                       else (run_session if isinstance(run_session, str)
                             else "")))
    return [{"rule": "deny_then_ran", "command_sha256": sha,
             "session_id": session}
            for sha, session in sorted(found)]
