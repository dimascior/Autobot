"""Assignment supervision: the Taskforce's answers to a running
specialist's permission requests.

A specialist runs with its provider's own permission prompts enabled, so
every tool use the provider does not pre-authorize arrives here as a
request. The Taskforce — which staffed the assignment and watches it —
answers each one with a DETERMINISTIC policy bounded by the frozen
contract the assignment executes: nothing is approved because a model
asked for it, and nothing is approved that the contract's finalization
class and frozen scope do not grant.

    file-reading tools     allowed inside the assignment's environment
                           (its workspace, the repository, the command-gate
                           tree)
    shell tools            allowed where the contract is SOURCE_MODIFYING or
                           its evidence requirements demand command
                           governance; each command is then admitted or
                           denied by Helios (cap-20), never by this policy.
                           A READ_ONLY assignment with no governance
                           requirement gets file-reading tools only.
    file-writing tools     allowed for command-gate files (the governance
                           handshake) and, for SOURCE_MODIFYING work,
                           inside the isolated worktree AND the frozen
                           mutation scope. Everything else is denied.
    anything else          denied (fail closed), with the reason

This grants no authority. The frozen scope is still enforced after the
fact by the candidate scope check and the verifier; supervision only
stops an out-of-contract action from happening at all, and records every
decision so a demonstration can show how the specialist behaved inside
its environment.
"""

import os

READ_TOOLS = ("Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite",
              "TodoRead")
SHELL_TOOLS = ("Bash", "PowerShell")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

GATE_SUFFIX = ".gate.json"


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def _variants(path):
    """A path as written and as it resolves (junctions, symlinks): a
    request is judged against both spellings of the same location."""
    values = {_norm(path)}
    try:
        values.add(_norm(os.path.realpath(str(path))))
    except OSError:
        pass
    return values


def _relative_inside(path, root):
    """The path relative to ``root`` (forward slashes) when it lies
    inside it under either spelling, else None."""
    for candidate in _variants(path):
        for base in _variants(root):
            if candidate == base:
                return ""
            prefix = base.rstrip("\\/") + os.sep
            if candidate.startswith(prefix):
                return candidate[len(prefix):].replace("\\", "/")
    return None


def _in_scope(relative, scope_paths):
    """The frozen mutation-scope rule (git_adapter.verify_mutation_scope):
    a path is covered when it IS a scope path or lies under one."""
    relative = relative.replace("\\", "/")
    for scope in scope_paths:
        scope = os.path.normcase(scope.replace("\\", "/")).rstrip("/")
        scope = scope.replace("\\", "/")
        if relative == scope or relative.startswith(scope + "/"):
            return True
    return False


def assignment_context(record, workspace, gate_root=None, repo_path=None):
    """The supervision context of one assignment, from its FROZEN record
    and the workspace its session was launched in."""
    fin = record["finalization_class"]
    governed = "COMMAND_GOVERNANCE" in record.get(
        "evidence_requirements", [])
    return {
        "finalization_class": fin,
        "workspace": str(workspace) if workspace else None,
        "scope_paths": list(record.get("allowed_scope", {}).get(
            "paths", [])),
        "gate_root": str(gate_root) if gate_root else None,
        "repo_path": str(repo_path) if repo_path else None,
        "shell_allowed": fin == "SOURCE_MODIFYING" or governed,
    }


def _target_path(tool_input):
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def decide(request, context):
    """One permission request -> one decision.

    Returns {"decision": "allow"|"deny"|None, "reason", "tool",
    "subject"}. ``None`` means the request is not a permission at all (a
    native question or form): the Taskforce never invents an answer to
    one."""
    if request.get("kind") != "approval_required":
        return {"decision": None, "tool": None, "subject": None,
                "reason": "a native question or form, not a permission:"
                          " left unanswered"}
    native = request.get("native_request") or {}
    if native.get("subtype") != "can_use_tool":
        return {"decision": "deny", "tool": None, "subject": None,
                "reason": "unrecognized native permission request shape"
                          " (fail closed)"}
    tool = native.get("tool_name")
    tool_input = native.get("input") or {}
    workspace = context.get("workspace")
    gate_root = context.get("gate_root")
    repo_path = context.get("repo_path")
    path = _target_path(tool_input)

    if tool in READ_TOOLS:
        if path is None:
            return {"decision": "allow", "tool": tool, "subject": None,
                    "reason": "file-reading tool inside the assignment"
                              " environment"}
        for root in (workspace, repo_path, gate_root):
            if root and _relative_inside(path, root) is not None:
                return {"decision": "allow", "tool": tool, "subject": path,
                        "reason": "read inside the assignment environment"}
        return {"decision": "deny", "tool": tool, "subject": path,
                "reason": "read outside the assignment environment (the"
                          " workspace, the repository and the command-gate"
                          " tree)"}

    if tool in SHELL_TOOLS:
        subject = tool_input.get("command")
        if context.get("shell_allowed"):
            return {"decision": "allow", "tool": tool, "subject": subject,
                    "reason": "the contract grants a governed shell; each"
                              " command is admitted by Helios (cap-20)"}
        return {"decision": "deny", "tool": tool, "subject": subject,
                "reason": "this READ_ONLY assignment grants file-reading"
                          " tools only (Read, Glob, Grep); complete the"
                          " task from the files"}

    if tool in WRITE_TOOLS:
        if path is None:
            return {"decision": "deny", "tool": tool, "subject": None,
                    "reason": "a write with no target path"}
        if gate_root and context.get("shell_allowed"):
            pending = os.path.join(gate_root, "pending")
            relative = _relative_inside(path, pending)
            if relative and "/" not in relative and \
                    relative.lower().endswith(GATE_SUFFIX):
                return {"decision": "allow", "tool": tool, "subject": path,
                        "reason": "command-governance gate file (the"
                                  " Helios handshake)"}
        if context.get("finalization_class") == "SOURCE_MODIFYING" \
                and workspace:
            relative = _relative_inside(path, workspace)
            if relative:
                if _in_scope(relative, context.get("scope_paths", [])):
                    return {"decision": "allow", "tool": tool,
                            "subject": path,
                            "reason": "inside the isolated worktree and"
                                      " the frozen task scope"}
                return {"decision": "deny", "tool": tool, "subject": path,
                        "reason": "outside the frozen task scope: only %s"
                                  " may change"
                                  % ", ".join(context.get(
                                      "scope_paths", []))}
        return {"decision": "deny", "tool": tool, "subject": path,
                "reason": "this assignment grants no write to that"
                          " location"}

    return {"decision": "deny", "tool": tool, "subject": None,
            "reason": "tool %s is not granted to Taskforce assignments"
                      % (tool,)}
