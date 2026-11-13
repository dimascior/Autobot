"""Registered closure resolution and transform execution (C4-M2).

One home for the D-57/INV-53/INV-66 closure discipline, shared by the
verifier runner (obj-19/obj-20 executions) and the transform plane
(INV-44/INV-49): a registration's closure resolves to EXACT content-
addressed artifacts or the runtime HALTs — never a PATH lookup, never a
same-named file, never a substitution.

Closure kinds (D-32, the frozen forms):
- ``python``: script + dependency lock content addresses + the pinned
  interpreter identity;
- ``native``: one executable content address;
- ``packaged`` with ``manifest_sha256``: the manifest bytes are a
  canonical PackageManifest v1 naming every entry {path, content_sha256}
  plus the entrypoint (one of the entries) and an optional pinned
  interpreter identity — all entries materialize from the content store
  at their relative paths (grammar-checked: no absolute paths, no ``..``,
  no backslashes), and only the bound entrypoint executes;
- ``packaged`` with ``image_digest``: NOT executable on this runtime —
  no container engine is part of the frozen environment, so it HALTs with
  an explicit classification rather than being approximated by another
  closure kind (D-C4-10).

Transforms (INV-44, the calibrated A1 family): a TransformRegistration's
closure maps exact input bytes to exact output bytes through the argv
protocol ``<entrypoint> <input_path> <output_path>`` — exit 0 means the
output file holds the transform's exact output; any nonzero exit is an
execution failure with no output. One byte of input difference may
produce any output difference; nothing here decodes, normalizes or
compares text — identity is bytes (INV-21/INV-44).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from contract_library import artifact_root, content_address

from . import environment_probe
from .models import ConflictError, IntegrityHalt

PACKAGE_MANIFEST_KIND = "PackageManifest"


def _safe_relative_path(path):
    """The packaged-entry path grammar: relative, slash-separated, no
    empty segments, no ``.``/``..``, no backslash, no leading slash."""
    if not isinstance(path, str) or not path or "\\" in path \
            or path.startswith("/"):
        return False
    segments = path.split("/")
    return all(segment and segment not in (".", "..")
               for segment in segments)


def parse_package_manifest(data):
    """The canonical PackageManifest v1 the ``packaged``/manifest closure
    form names: entries, entrypoint membership, optional interpreter
    identity. Anything malformed is an IntegrityHalt — the manifest IS
    part of the registered closure identity."""
    try:
        manifest = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise IntegrityHalt(
            "packaged closure manifest bytes are not UTF-8 JSON")
    if not isinstance(manifest, dict) \
            or manifest.get("kind") != PACKAGE_MANIFEST_KIND \
            or manifest.get("schema_version") != "v1":
        raise IntegrityHalt(
            "packaged closure manifest is not a v1 PackageManifest")
    entries = manifest.get("entries")
    if not (isinstance(entries, list) and entries):
        raise IntegrityHalt(
            "packaged closure manifest names no entries")
    paths = set()
    for entry in entries:
        if not (isinstance(entry, dict)
                and _safe_relative_path(entry.get("path"))
                and isinstance(entry.get("content_sha256"), str)):
            raise IntegrityHalt(
                "packaged closure manifest entry is malformed (relative"
                " slash path + content_sha256 required)")
        if entry["path"] in paths:
            raise IntegrityHalt(
                "packaged closure manifest repeats entry path %r"
                % entry["path"])
        paths.add(entry["path"])
    entrypoint = manifest.get("entrypoint")
    if entrypoint not in paths:
        raise IntegrityHalt(
            "packaged closure entrypoint %r is not among the manifest"
            " entries" % (entrypoint,))
    interpreter_id = manifest.get("interpreter_id")
    if interpreter_id is not None and not isinstance(interpreter_id, str):
        raise IntegrityHalt(
            "packaged closure interpreter_id is malformed")
    return {"entries": entries, "entrypoint": entrypoint,
            "interpreter_id": interpreter_id}


def resolve_closure(store, registration):
    """INV-53/INV-66/SEM-15 at dispatch: recompute the artifact_root from
    the closure manifest, prove every required leaf's exact bytes exist in
    the content store, and prove any pinned interpreter is the one
    actually available. Anything missing or divergent is an IntegrityHalt
    (never substituted); the sequence-level HALT is t-s12
    bound_artifact_unavailable. Returns the closure dict (for packaged
    manifests, with the parsed manifest under ``package``)."""
    closure = registration.get("closure")
    if not isinstance(closure, dict):
        raise IntegrityHalt(
            "registration %r carries no closure manifest (INV-53)"
            % registration.get("registration_id"))
    recomputed = artifact_root(closure)
    if recomputed != registration.get("artifact_root"):
        raise IntegrityHalt(
            "registration %r artifact_root does not recompute from its"
            " closure manifest (SEM-15/D-32: forged or corrupted"
            " registration)" % registration.get("registration_id"))
    kind = closure.get("kind")
    if kind == "python":
        for leaf in ("script_sha256", "dependency_lock_sha256"):
            store.get(closure[leaf])  # D-66: missing/altered leaf HALTs
        live = environment_probe.interpreter_identity()
        if closure["interpreter_id"] != live:
            raise IntegrityHalt(
                "registered interpreter %r is not available (live identity"
                " %r): the closure executes the EXACT registered"
                " interpreter or HALTs (INV-66, never a PATH substitute)"
                % (closure["interpreter_id"], live))
        return closure
    if kind == "native":
        store.get(closure["executable_sha256"])
        return closure
    if kind == "packaged":
        if "image_digest" in closure:
            raise IntegrityHalt(
                "packaged closure %r names a container image digest: no"
                " container engine is part of this runtime's frozen"
                " environment, so the image form is not executable here"
                " and HALTs explicitly (D-C4-10) — never approximated by"
                " another closure kind"
                % registration.get("registration_id"))
        manifest_bytes = store.get(closure["manifest_sha256"])
        package = parse_package_manifest(manifest_bytes)
        if registration.get("entrypoint") != package["entrypoint"]:
            raise IntegrityHalt(
                "packaged closure %r: the registration's entrypoint %r and"
                " the manifest's %r disagree — one bound entry executes or"
                " nothing does"
                % (registration.get("registration_id"),
                   registration.get("entrypoint"), package["entrypoint"]))
        for entry in package["entries"]:
            store.get(entry["content_sha256"])  # every leaf, exact bytes
        if package["interpreter_id"] is not None:
            live = environment_probe.interpreter_identity()
            if package["interpreter_id"] != live:
                raise IntegrityHalt(
                    "packaged closure pins interpreter %r but the live"
                    " identity is %r (INV-66)"
                    % (package["interpreter_id"], live))
        resolved = dict(closure)
        resolved["package"] = package
        return resolved
    raise IntegrityHalt(
        "closure kind %r is not a registered D-32 form" % (kind,))


def materialize_closure(store, closure, run_dir, entrypoint):
    """Write the resolved closure's exact artifact bytes into ``run_dir``
    and return the argv prefix that executes the bound entry — the ONLY
    thing that runs. ``entrypoint`` is the registration's (python/native)
    or the package manifest's (packaged)."""
    if ".." in entrypoint.split("/"):
        raise ConflictError(
            "registration entrypoint %r escapes its closure" % entrypoint)
    kind = closure["kind"]
    if kind == "packaged":
        package = closure["package"]
        entry_path = None
        for entry in package["entries"]:
            target = os.path.join(run_dir, *entry["path"].split("/"))
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(target, "wb") as fh:
                fh.write(store.get(entry["content_sha256"]))
            if entry["path"] == package["entrypoint"]:
                entry_path = target
        if package["interpreter_id"] is not None:
            return [sys.executable, "-I", entry_path]
        return [entry_path]
    script_path = os.path.join(run_dir, *entrypoint.split("/"))
    parent = os.path.dirname(script_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    if kind == "python":
        with open(script_path, "wb") as fh:
            fh.write(store.get(closure["script_sha256"]))
        with open(os.path.join(run_dir, "dependency.lock"), "wb") as fh:
            fh.write(store.get(closure["dependency_lock_sha256"]))
        return [sys.executable, "-I", script_path]
    with open(script_path, "wb") as fh:
        fh.write(store.get(closure["executable_sha256"]))
    return [script_path]


def execute_transform(store, registration, input_bytes, workdir=None):
    """Execute the EXACT registered transform closure over exact input
    bytes (the argv protocol above). Returns ``(output_bytes, execution)``
    — ``output_bytes`` is None on a nonzero exit, and ``execution`` is the
    provenance block an obj-19/obj-20 ROLE_TRANSFORM receipt binds:
    closure kind, entrypoint, exit code, and the content address of the
    exact output bytes (or of the captured diagnostics when the transform
    failed). Deterministic over (registration, input bytes) for a
    deterministic registered script."""
    closure = resolve_closure(store, registration)
    entrypoint = (closure["package"]["entrypoint"]
                  if closure["kind"] == "packaged"
                  else registration["entrypoint"])
    run_dir = tempfile.mkdtemp(prefix="autobot-transform-", dir=workdir)
    try:
        argv = materialize_closure(store, closure, run_dir, entrypoint)
        input_path = os.path.join(run_dir, "input.bin")
        output_path = os.path.join(run_dir, "output.bin")
        with open(input_path, "wb") as fh:
            fh.write(bytes(input_bytes))
        proc = subprocess.run(argv + [input_path, output_path],
                              capture_output=True, cwd=run_dir,
                              timeout=600)
        output_bytes = None
        if proc.returncode == 0 and os.path.exists(output_path):
            with open(output_path, "rb") as fh:
                output_bytes = fh.read()
        if output_bytes is not None:
            evidence = store.put(output_bytes)
        else:
            evidence = store.put((proc.stdout or b"")
                                 + (proc.stderr or b""))
        execution = {"closure_kind": closure["kind"],
                     "entrypoint": entrypoint,
                     "exit_code": proc.returncode,
                     "output_sha256": evidence}
        return output_bytes, execution
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def transform_output_identity(output_bytes):
    """The transform output's content identity (D-31) — the ``expected``
    side of every INV-49 comparison."""
    return content_address(output_bytes)


def closure_leaf_addresses(store, closure):
    """Every LOCAL content address a registered closure binds — INV-58's
    retention roots: python = script + dependency lock; native = the
    executable; packaged/manifest = the manifest plus every entry it
    names. When the manifest bytes are absent or unparseable the entries
    cannot be known, so only the manifest address is returned — dispatch
    (``resolve_closure``) is the fail-closed act and recovery REPORTS the
    gap; retention stays conservative, never inventive. The image_digest
    form binds no local bytes."""
    kind = closure.get("kind") if isinstance(closure, dict) else None
    if kind == "python":
        return [closure["script_sha256"], closure["dependency_lock_sha256"]]
    if kind == "native":
        return [closure["executable_sha256"]]
    if kind == "packaged":
        if "image_digest" in closure:
            return []
        leaves = [closure["manifest_sha256"]]
        if store.has(closure["manifest_sha256"]):
            try:
                package = parse_package_manifest(
                    store.get(closure["manifest_sha256"]))
            except IntegrityHalt:
                return leaves
            leaves.extend(entry["content_sha256"]
                          for entry in package["entries"])
        return leaves
    return []
