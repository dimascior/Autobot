"""The environment probe — the registered ``environment_probe`` seat
(C4-M1, D-56).

principals.json registers ``environment_probe``; cap-33 makes it the owner
of "Environment capture" with the verifier_runner as consumer; objects.json
registers obj-18 ActualVerificationEnvironment with minter
``environment_probe``, class receipt, immutable, attempt-bound. The payload
shape is the FROZEN Chapter 1 schema
(``schemas/actual_verification_environment.schema.json``), used without
weakening.

D-56's capture rule, implemented literally:
- interpreter: implementation, exact version, and the SHA-256 of the
  interpreter executable's bytes (a content address; the path itself is
  never the durable identity);
- platform: os, os_version, architecture;
- env_vars: ONLY the variables the frozen constraints name — never the
  full process environment (secrets stay out of receipts); an unset named
  variable is omitted (D-37), which the constraint match then fails
  closed;
- tools: ONLY the tools the frozen constraints name — presence, exact
  version and executable SHA-256; a tool that cannot be found or whose
  version cannot be established is omitted, which the constraint match
  then fails closed.

The probe GRANTS NOTHING: a capture is evidence, bound by the verifier
runner into obj-19/obj-20, and an environment mismatch is a HALT-grade
authority problem (t-s12 env_mismatch), never an assertion failure.
"""

import os
import platform
import re
import subprocess
import sys

from contract_library import content_address

from .models import instant, mint_id

ENVIRONMENT_KIND = "ActualVerificationEnvironment"

# obj-18's registered minter (objects.json; cap-33).
ENVIRONMENT_MINTER = "environment_probe"

_ARCH_NORMALIZATION = {"amd64": "x86_64", "aarch64": "arm64"}

_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")

_TOKEN_STRIP_RE = re.compile(r"[^A-Za-z0-9_.+-]+")


def _token(text):
    """Collapse free text into the frozen schema's token grammar
    (``^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$``); 'unknown' when nothing
    survives. Capture never fails on a strange platform string — it
    captures the recognizable identity and the schema stays satisfied."""
    cleaned = _TOKEN_STRIP_RE.sub(".", str(text)).strip("._+-")
    return cleaned[:128] if cleaned else "unknown"


def os_name():
    """The platform's os token: windows / macos / linux (or the raw
    sys.platform token for anything else)."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def architecture():
    """The machine architecture token, normalized so one physical machine
    has one spelling (amd64 -> x86_64, aarch64 -> arm64)."""
    machine = platform.machine().lower()
    return _ARCH_NORMALIZATION.get(machine, machine)


def interpreter_identity():
    """The D-32/D-57 interpreter identity string a python ClosureManifest
    pins: ``<implementation>-<version>-<os>-<architecture>`` (the
    registered fixtures' spelling, e.g. cpython-3.12.4-windows-x86_64)."""
    return "%s-%s-%s-%s" % (platform.python_implementation().lower(),
                            platform.python_version(), os_name(),
                            architecture())


def interpreter_executable_sha256():
    """The content address of the running interpreter's executable bytes
    (D-56: the binary's identity, never its path)."""
    with open(sys.executable, "rb") as fh:
        return content_address(fh.read())


def _probe_tool_version(executable):
    """Default tool-version probe: the first dotted number on the first
    line of ``<tool> --version``; None when no version can be established
    (the capture then omits the tool and the match fails closed)."""
    try:
        proc = subprocess.run([executable, "--version"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stdout or proc.stderr or "").splitlines()
    match = _VERSION_RE.search(text[0]) if text else None
    return match.group(0) if match else None


def default_tool_prober(name):
    """Resolve one constraint-named tool on this machine: (version,
    executable_sha256) or None when absent/unprobeable. Injection point
    for deterministic tests; the capture rule (named tools only) is the
    caller's."""
    try:
        import shutil
        path = shutil.which(name)
    except Exception:
        path = None
    if path is None:
        return None
    version = _probe_tool_version(path)
    if version is None:
        return None
    with open(path, "rb") as fh:
        digest = content_address(fh.read())
    return (version, digest)


def constraint_names(constraints):
    """The env-var and tool names the frozen constraints declare — the
    ONLY names the probe may capture (D-56)."""
    env_names = set()
    tool_names = set()
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        env_rule = constraint.get("env_vars")
        if isinstance(env_rule, dict):
            env_names.update(k for k in env_rule if isinstance(k, str))
        tool_rule = constraint.get("tools")
        if isinstance(tool_rule, dict):
            tool_names.update(k for k in tool_rule if isinstance(k, str))
    return sorted(env_names), sorted(tool_names)


def capture_environment(attempt_identity, clock, constraints=(),
                        tool_prober=None):
    """The obj-18 payload for one attempt: the frozen Chapter 1 shape,
    bound to the exact attempt identity, capturing only what D-56 names.
    ``constraints`` is the iterable of the attempt's declared
    environment-constraint sections (obj-17 values)."""
    env_names, tool_names = constraint_names(constraints)
    prober = tool_prober or default_tool_prober
    env_vars = {}
    for name in env_names:
        value = os.environ.get(name)
        if value is not None:  # D-37: an unset variable is omitted
            env_vars[name] = value
    tools = {}
    for name in tool_names:
        probed = prober(name)
        if probed is None:  # absence is omission; the match fails closed
            continue
        version, digest = probed
        tools[name] = {"version": version, "executable_sha256": digest}
    return {
        "schema_version": "v1",
        "kind": ENVIRONMENT_KIND,
        "environment_id": mint_id("ve"),
        "attempt": dict(attempt_identity),
        "probed_utc": instant(clock.now()),
        "interpreter": {
            "implementation": platform.python_implementation().lower(),
            "version": platform.python_version(),
            "executable_sha256": interpreter_executable_sha256(),
        },
        "platform": {
            "os": os_name(),
            "os_version": _token(platform.version()),
            "architecture": architecture(),
        },
        "env_vars": env_vars,
        "tools": tools,
    }
