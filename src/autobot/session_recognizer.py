"""Codex session-record recognizer for obj-13 (SESSION_TRANSCRIPT).

Reads the provider-native session JSONL and extracts:
    1. Session identity (session_meta)
    2. Item vocabulary (every execution-relevant event type)
    3. Command identity correlation keys (CommandExecution.id for Helios join)
    4. Execution-reach classification per item
    5. Terminal state

The recognizer is TOLERANT and READ-ONLY. It never rewrites producer
bytes, refuses nothing, and returns empty results for unrecognizable
input. Unknown event types contribute no facts and produce no failure.

Reach classification is a property of the item type, not the item's
content. A CommandExecution is MACHINE_LOCAL regardless of what the
command does; an McpToolCall is PROVIDER_CONNECTOR regardless of what
the server provides. The execution-reach POLICY (what a profile
authorizes) is a separate question resolved by the caller.
"""

import json

# Event types that carry execution-relevant activity.
# These are the item types observed across retained production sessions
# (Pilots 1-8, probes, and scratch sessions on Codex CLI 0.158.0-alpha.15.2).
EXECUTION_RELEVANT_TYPES = frozenset((
    "CommandExecution",
    "FileChange",
    "McpToolCall",
    "CollabAgentToolCall",
))

# Every known event type and its reach classification.
# MACHINE_LOCAL:       activity on the assignment's machine
# PROVIDER_CONNECTOR:  activity through a provider-owned connector/MCP
# PROVIDER_SUBAGENT:   activity delegated to another agent session
# MODEL_INTERNAL:      model reasoning, telemetry, lifecycle (no reach)
# IDENTITY:            session identity metadata
REACH_CLASSIFICATION = {
    "session_meta":         "IDENTITY",
    "CommandExecution":     "MACHINE_LOCAL",
    "FileChange":           "MACHINE_LOCAL",
    "McpToolCall":          "PROVIDER_CONNECTOR",
    "CollabAgentToolCall":  "PROVIDER_SUBAGENT",
    "Reasoning":            "MODEL_INTERNAL",
    "AgentMessage":         "MODEL_INTERNAL",
    "UserMessage":          "MODEL_INTERNAL",
    "token_usage_record":   "MODEL_INTERNAL",
    "event_msg":            "MODEL_INTERNAL",
    "world_state":          "MODEL_INTERNAL",
    "turn_context":         "MODEL_INTERNAL",
    "response_item":        "MODEL_INTERNAL",
    "compacted":            "MODEL_INTERNAL",
    "inter_agent_communication_metadata": "MODEL_INTERNAL",
}

# Reach classes that represent external activity beyond machine-local.
EXTERNAL_REACH = frozenset(("PROVIDER_CONNECTOR", "PROVIDER_SUBAGENT"))


def _parse_lines(data):
    """Parse JSONL from raw bytes, tolerantly."""
    try:
        text = bytes(data).decode("utf-8-sig")
    except (UnicodeDecodeError, ValueError):
        return []
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except (ValueError, json.JSONDecodeError):
            continue
    return lines


def recognize_session(data):
    """Extract recognized facts from a Codex session JSONL record.

    Returns a dict with:
        session_id:         str or None
        call_id:            str or None (from session_meta payload)
        provider:           str or None
        model:              str or None
        cli_version:        str or None
        cwd:                str or None
        git_commit:         str or None
        item_counts:        dict[str, int] (type -> count)
        execution_items:    list of dicts (execution-relevant items)
        command_ids:        list of str (CommandExecution.id values)
        reach_observed:     set of str (observed reach classes)
        external_activity:  list of dicts (items with external reach)
        terminal:           bool (whether the session has a terminal event)
        terminal_outcome:   str or None
        line_count:         int
    """
    lines = _parse_lines(data)
    if not lines:
        return None

    result = {
        "session_id": None,
        "call_id": None,
        "provider": None,
        "model": None,
        "cli_version": None,
        "cwd": None,
        "git_commit": None,
        "item_counts": {},
        "execution_items": [],
        "command_ids": [],
        "reach_observed": set(),
        "external_activity": [],
        "terminal": False,
        "terminal_outcome": None,
        "line_count": len(lines),
    }

    for entry in lines:
        if not isinstance(entry, dict):
            continue

        event_type = entry.get("type", "")
        payload = entry.get("payload") or {}

        # Count every event type
        result["item_counts"][event_type] = (
            result["item_counts"].get(event_type, 0) + 1)

        # Session identity from session_meta
        if event_type == "session_meta":
            if isinstance(payload, dict):
                result["session_id"] = payload.get("session_id") or payload.get("id")
                result["cli_version"] = payload.get("cli_version")
                result["cwd"] = payload.get("cwd")
                bi = payload.get("base_instructions") or {}
                prov = bi.get("provenance") if isinstance(bi, dict) else None
                result["model"] = prov.get("model") if isinstance(prov, dict) else None
                git = payload.get("git") or {}
                result["git_commit"] = git.get("commit_hash")

        # Execution-relevant items
        if event_type == "event_msg" and isinstance(payload, dict):
            item = payload.get("item") or {}
            item_type = item.get("type", "")

            if item_type in EXECUTION_RELEVANT_TYPES:
                reach = REACH_CLASSIFICATION.get(item_type, "UNCLASSIFIED")
                result["reach_observed"].add(reach)

                item_record = {
                    "item_type": item_type,
                    "item_id": item.get("id", ""),
                    "reach": reach,
                    "ordinal": entry.get("ordinal"),
                    "timestamp": entry.get("timestamp"),
                }

                if item_type == "CommandExecution":
                    item_record["command"] = item.get("command", [])
                    item_record["exit_code"] = item.get("exit_code")
                    item_record["status"] = item.get("status")
                    item_record["cwd"] = item.get("cwd")
                    if item.get("id"):
                        result["command_ids"].append(item["id"])

                elif item_type == "FileChange":
                    item_record["path"] = ""
                    for key in ("file_path", "path", "filename"):
                        if item.get(key):
                            item_record["path"] = item[key]
                            break

                elif item_type == "McpToolCall":
                    item_record["server_name"] = item.get("server_name", "")
                    item_record["tool_name"] = item.get("tool_name", "")
                    result["external_activity"].append(item_record)

                elif item_type == "CollabAgentToolCall":
                    item_record["agent_name"] = item.get("agent_name", "")
                    result["external_activity"].append(item_record)

                result["execution_items"].append(item_record)

            # Terminal: event_msg with item phase "final_answer"
            if item.get("phase") == "final_answer":
                result["terminal"] = True

        # Terminal: response_item with phase "final_answer"
        if event_type == "response_item" and isinstance(payload, dict):
            if payload.get("phase") == "final_answer":
                result["terminal"] = True

    return result


def classify_reach(recognized):
    """Classify the session's observed execution reach.

    Returns:
        "MACHINE_LOCAL"       — only machine-local activity observed
        "PROVIDER_CONNECTOR"  — provider connector activity observed
        "PROVIDER_SUBAGENT"   — provider sub-agent activity observed
        "MIXED_EXTERNAL"      — both connector and sub-agent observed
        "NO_EXECUTION"        — no execution-relevant activity
        None                  — unrecognizable input
    """
    if recognized is None:
        return None
    observed = recognized.get("reach_observed", set())
    has_connector = "PROVIDER_CONNECTOR" in observed
    has_subagent = "PROVIDER_SUBAGENT" in observed
    has_local = "MACHINE_LOCAL" in observed

    if has_connector and has_subagent:
        return "MIXED_EXTERNAL"
    if has_connector:
        return "PROVIDER_CONNECTOR"
    if has_subagent:
        return "PROVIDER_SUBAGENT"
    if has_local:
        return "MACHINE_LOCAL"
    return "NO_EXECUTION"


def execution_reach_violations(recognized, authorized_reach):
    """Check whether observed reach exceeds authorized reach.

    authorized_reach is a set of reach classes the execution profile
    permits (e.g. {"MACHINE_LOCAL"} for luna-local).

    Returns a list of violation dicts, each naming the item that
    exceeded the authorized reach. Empty list means no violation.
    """
    if recognized is None:
        return []
    violations = []
    for item in recognized.get("execution_items", []):
        reach = item.get("reach", "")
        if reach and reach not in authorized_reach and reach != "MODEL_INTERNAL":
            violations.append({
                "item_type": item["item_type"],
                "item_id": item.get("item_id", ""),
                "observed_reach": reach,
                "detail": item,
            })
    return violations
