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

Two providers' request shapes are understood; any other shape is denied.

Claude Code asks before every tool use it has not been pre-authorized
for (``can_use_tool``): the table above is applied to each one.

Codex runs inside an operating-system sandbox and asks only to LEAVE it.
The route's sandbox already holds everything a contract may write (the
isolated worktree and the command-gate pending directory), and every
command it runs there is still admitted or refused by Helios. So a Codex
request is an escalation: the command it names would run with no sandbox
around it. The policy grants exactly one kind, and judges the command
that would RUN, not a description of it:

    a command escalation   allowed for SOURCE_MODIFYING work, and only for
                           the two commands a specialist-owned candidate
                           commit needs (a linked worktree's index and
                           objects live in the repository's Git directory,
                           outside the sandbox):

                               git add [--] <path in the frozen scope>...
                               git commit -m <message>

                           The request must be the system shell running
                           that one command and nothing else (no second
                           command, no pipe, redirection, substitution or
                           variable), from the ROOT of the assignment's
                           worktree, and the worktree must still be the
                           one the repository created (its ``.git``
                           pointer leads into the repository's own
                           worktree registry, so Git reads only the
                           repository's configuration and hooks).
    a file change          denied: a write the sandbox refused is a write
                           outside the assignment's environment
    a permissions change   denied: an assignment's permissions are fixed
                           by its contract for the whole session

A location is always judged by where it RESOLVES: a link or junction
inside the workspace that leads out of it is outside.
"""

import os
import shlex

READ_TOOLS = ("Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite",
              "TodoRead")
SHELL_TOOLS = ("Bash", "PowerShell")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

GATE_SUFFIX = ".gate.json"

# Codex (app-server) permission requests, by method.
CODEX_COMMAND = "item/commandExecution/requestApproval"
CODEX_FILE_CHANGE = "item/fileChange/requestApproval"
CODEX_PERMISSIONS = "item/permissions/requestApproval"
# The names these are recorded under (beside Claude Code's tool names).
CODEX_TOOLS = {CODEX_COMMAND: "codex.command",
               CODEX_FILE_CHANGE: "codex.fileChange",
               CODEX_PERMISSIONS: "codex.permissions"}
# The only Git commands that may leave a sandbox: the two a
# specialist-owned candidate commit needs (prompts.py, steps 3 and 4).
GIT_ESCALATIONS = ("add", "commit")
ESCALATION_RULE = (
    "only two commands may leave the sandbox, each on its own, from the"
    " root of the assignment's worktree: `git add [--] <path in the"
    " frozen scope>...` and `git commit -m \"<message>\"`")
_ADD_FLAGS = frozenset(("-A", "--all", "-u", "--update", "-v", "--verbose"))
_COMMIT_FLAGS = frozenset(("-q", "--quiet", "-v", "--verbose", "-s",
                           "--signoff"))
# The shells a command request may be wrapped in, by program name, with
# the quoting rules their scripts follow; then, per family, the flags
# that change nothing and the flags that introduce the script.
_SHELLS = {"powershell": "powershell", "pwsh": "powershell",
           "bash": "posix", "sh": "posix", "zsh": "posix", "dash": "posix"}
_SHELL_FLAGS = {
    "powershell": (("-noprofile", "-nologo", "-noninteractive"),
                   ("-command", "-c")),
    "posix": ((), ("-c", "-lc")),
}
# What a word of a one-command script may be made of outside quotes.
# Everything a shell gives a meaning to is absent: ; & | < > ` $ ( ) { }
# [ ] * ? ! # % ^ ~ , @ and every other character.
_BARE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  "0123456789_-./\\:=")
# Characters PowerShell reads as quotation marks besides ' and ".
_OTHER_QUOTES = frozenset(u"‘’‚‛“”„")


def _norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def _resolved(path):
    """A path as it RESOLVES: junctions and symbolic links followed, case
    and separators normalized."""
    text = str(path)
    try:
        text = os.path.realpath(text)
    except (OSError, ValueError):
        pass
    return _norm(text)


def _relative_inside(path, root):
    """The path relative to ``root`` (forward slashes) when the location
    it resolves to lies inside the location ``root`` resolves to, else
    None. Both sides are resolved, so two spellings of one place (a
    junction and its target) agree, and a link under the root that leads
    out of it is outside."""
    candidate, base = _resolved(path), _resolved(root)
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


def _trusted_shell(program):
    """Is this shell one the operating system supplies? A command that
    leaves the sandbox must not start with a program the session could
    have put there. On Windows a bare name is refused as well: process
    creation there may find it in a current directory."""
    text = str(program)
    bare = "/" not in text and "\\" not in text
    if os.name == "nt":
        if bare:
            return False
        roots = [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32")]
        for key in ("ProgramFiles", "ProgramW6432"):
            if os.environ.get(key):
                roots.append(os.path.join(os.environ[key], "PowerShell"))
    else:
        if bare:
            return True
        roots = ["/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    return os.path.isabs(text) and any(
        _relative_inside(text, root) for root in roots)


def _wrapped_script(command):
    """(quoting family, script) when ``command`` is a system shell
    started to run exactly one script — ``powershell.exe -Command
    '<script>'``, ``bash -lc '<script>'`` — which is what a command
    request executes. Anything else is None: another program, further
    arguments, a string that does not parse."""
    if isinstance(command, str):
        try:
            argv = shlex.split(command, posix=True)
        except ValueError:
            return None
    elif isinstance(command, (list, tuple)) and all(
            isinstance(word, str) for word in command):
        argv = list(command)
    else:
        return None
    if len(argv) < 3 or not _trusted_shell(argv[0]):
        return None
    program = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if program.endswith(".exe"):
        program = program[:-4]
    family = _SHELLS.get(program)
    if family is None:
        return None
    harmless, introduces = _SHELL_FLAGS[family]
    flags = [flag.lower() if family == "powershell" else flag
             for flag in argv[1:-1]]
    if flags[-1] not in introduces or any(
            flag not in harmless for flag in flags[:-1]):
        return None
    return family, argv[-1]


def _script_argv(script, family):
    """The words of a script that is ONE plain command, else None.

    Words are separated by blanks. A word is made of bare characters
    (_BARE) and quoted stretches. Single quotes are literal; double
    quotes are literal as long as they hold nothing the shell would
    expand ($ and the backtick, and for a POSIX shell the backslash).
    Every other character outside quotes — every operator, separator,
    redirection, substitution, glob, comment and line break a shell
    knows — makes the script something other than one plain command. A
    quote doubled to escape itself is refused too: the two families
    read it differently. And PowerShell hands a native program its
    arguments through Windows' own command-line rules, where a double
    quote or a trailing backslash inside an argument moves the
    boundaries between arguments: there a quoted stretch may hold
    neither."""
    if not isinstance(script, str) or any(
            ch in _OTHER_QUOTES for ch in script):
        return None
    argv, word, i = [], None, 0
    while i < len(script):
        ch = script[i]
        if ch in " \t":
            if word is not None:
                argv.append(word)
                word = None
            i += 1
        elif ch in "'\"":
            end = script.find(ch, i + 1)
            if end < 0 or script[end + 1:end + 2] == ch:
                return None
            body = script[i + 1:end]
            if ch == '"' and any(c in body for c in "$`"):
                return None
            if family == "posix":
                if ch == '"' and "\\" in body:
                    return None
            elif '"' in body or body.endswith("\\"):
                return None
            word = (word or "") + body
            i = end + 1
        elif ch in _BARE and not (family == "posix" and ch == "\\"):
            word = (word or "") + ch
            i += 1
        else:
            return None
    if word is not None:
        argv.append(word)
    return argv


def _pathspec_in_scope(spec, scope_paths):
    """A pathspec that is a plain path inside the frozen scope: relative
    to the worktree root, no drive, no ``..``, no pathspec magic, no
    wildcard."""
    spec = spec.replace("\\", "/")
    while spec.startswith("./"):
        spec = spec[2:]
    spec = spec.rstrip("/")
    if not spec or spec.startswith(("/", "-")) or any(
            c in spec for c in ":*?["):
        return False
    if any(part in ("", ".", "..") for part in spec.split("/")):
        return False
    return _in_scope(os.path.normcase(spec).replace("\\", "/"),
                     scope_paths)


def _git_escalation(argv, scope_paths):
    """Why ``argv`` is NOT one of the two Git commands that may leave a
    sandbox, or None when it is one."""
    if len(argv) < 2 or argv[0].lower() not in ("git", "git.exe") \
            or argv[1] not in GIT_ESCALATIONS:
        return "this is neither"
    command, rest = argv[1], argv[2:]
    flags = _ADD_FLAGS if command == "add" else _COMMIT_FLAGS
    specs, messages, only_paths, i = [], 0, False, 0
    while i < len(rest):
        word = rest[i]
        if only_paths:
            specs.append(word)
        elif word == "--":
            only_paths = True
        elif command == "commit" and word in ("-m", "--message"):
            if i + 1 >= len(rest):
                return "`%s` needs the message" % word
            messages += 1
            i += 1
        elif command == "commit" and word.startswith("--message="):
            messages += 1
        elif word.startswith("-"):
            if word not in flags:
                return "`git %s` does not take `%s` here" % (command, word)
        else:
            specs.append(word)
        i += 1
    if command == "add" and not specs:
        return "name the paths to stage"
    if command == "commit" and not messages:
        return "give the commit message with -m"
    for spec in specs:
        if not _pathspec_in_scope(spec, scope_paths):
            return "`%s` is not a path inside the frozen scope (%s)" % (
                spec, ", ".join(scope_paths) or "none")
    return None


def _own_worktree(workspace, repo_path):
    """Is ``workspace`` still the linked worktree the repository made?
    Its ``.git`` must be the pointer FILE Git wrote, leading into the
    repository's own worktree registry — which a sandboxed session
    cannot write. A session that replaced the pointer could hand Git a
    configuration of its own (hooks, an fsmonitor command) to run
    outside the sandbox."""
    if not (workspace and repo_path):
        return False
    pointer = os.path.join(str(workspace), ".git")
    if os.path.islink(pointer) or not os.path.isfile(pointer):
        return False
    try:
        with open(pointer, "r", encoding="utf-8") as handle:
            text = handle.read(4096).strip()
    except (OSError, ValueError):
        return False
    if not text.startswith("gitdir:") or "\n" in text:
        return False
    target = text[len("gitdir:"):].strip()
    if not os.path.isabs(target):
        target = os.path.join(str(workspace), target)
    relative = _relative_inside(
        target, os.path.join(str(repo_path), ".git", "worktrees"))
    return bool(relative) and "/" not in relative


def _decide_codex(native, context):
    """A Codex session asking to leave its sandbox (see the module
    docstring)."""
    method = native.get("method")
    tool = CODEX_TOOLS[method]
    if method == CODEX_PERMISSIONS:
        return {"decision": "deny", "tool": tool, "subject": None,
                "reason": "an assignment's permissions are fixed by its"
                          " contract; nothing widens them during a"
                          " session"}
    if method == CODEX_FILE_CHANGE:
        return {"decision": "deny", "tool": tool,
                "subject": native.get("grantRoot"),
                "reason": "the sandbox already holds every location this"
                          " assignment may write (its worktree and the"
                          " command-gate pending directory); a write it"
                          " refused is outside the assignment"}
    command = native.get("command")
    wrapped = _wrapped_script(command)
    subject = wrapped[1] if wrapped else (
        command if isinstance(command, str) else None)

    def deny(reason):
        return {"decision": "deny", "tool": tool, "subject": subject,
                "reason": reason}

    if not context.get("shell_allowed") or \
            context.get("finalization_class") != "SOURCE_MODIFYING":
        return deny("this assignment grants no command outside the"
                    " sandbox; run commands that only read, inside it")
    if wrapped is None:
        return deny(ESCALATION_RULE + "; this request is not the system"
                    " shell running one command")
    family, script = wrapped
    actions = native.get("commandActions")
    if actions not in (None, []) and not (
            isinstance(actions, list) and len(actions) == 1
            and isinstance(actions[0], dict)
            and isinstance(actions[0].get("command"), str)
            and actions[0]["command"].strip() == script.strip()):
        return deny(ESCALATION_RULE + "; this request describes something"
                    " other than the one command it would run")
    workspace = context.get("workspace")
    cwd = native.get("cwd")
    if not (workspace and isinstance(cwd, str)
            and _relative_inside(cwd, workspace) == ""):
        return deny(ESCALATION_RULE + "; this one would not run from the"
                    " root of the assignment's own workspace")
    if not _own_worktree(workspace, context.get("repo_path")):
        return deny("the workspace is not the worktree the repository"
                    " created for this assignment (its .git pointer does"
                    " not lead into the repository's worktree registry)")
    argv = _script_argv(script, family)
    if argv is None:
        return deny(ESCALATION_RULE + "; this script is more than one"
                    " plain command (an operator, a substitution, a"
                    " variable or an escaped quote)")
    refusal = _git_escalation(argv, context.get("scope_paths", []))
    if refusal:
        return deny(ESCALATION_RULE + "; " + refusal)
    return {"decision": "allow", "tool": tool, "subject": subject,
            "reason": "git %s for the assignment's own worktree, inside"
                      " the frozen scope; Helios governs the command"
                      " itself (cap-20)" % argv[1]}


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
    if native.get("method") in CODEX_TOOLS:
        return _decide_codex(native, context)
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
