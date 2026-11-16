"""Production AutoSwarm Transport (Phase B: replaces FakeTransport).

Launches specialist sessions through the AutoSwarm CLI with the
Sequencer's call_id as the idempotency key. Evidence is read directly
from AutoSwarm's durable call records and the Helios evidence tree —
the adapter never manufactures evidence or rewrites provider-owned bytes.

Evidence families and their actual locations:
    obj-11  AutoSwarm's own result.json or handle.json (producer: autoswarm)
    obj-12  Individual Helios decision files at
            <gate_root>/evidence/integrity/sessions/<session_id>/commands/
            Each file is a separate witness entry with its original bytes.
    obj-13  Provider's native session transcript (producer: agent_clis).
            NOT AutoSwarm's stdout.log pipe capture — that is a runner
            observation, not a provider-native export. obj-13 requires a
            provider-specific exporter or session export file.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from .transport import Transport, normalize_outcome
from .witnesses import WITNESS_KINDS


def _read_json(path):
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


class AutoSwarmTransport(Transport):
    """Transport over real AutoSwarm sessions.

    ``store`` is Autobot's content-addressed store (for resolving the
    frozen PromptProjection at launch time).

    ``autoswarm_home`` is the AutoSwarm records root ($AUTOSWARM_HOME).

    ``workspace`` is the working directory passed to AutoSwarm for
    every specialist session.

    ``gate_root`` is the Helios .command-gate directory whose evidence
    tree contains the specialist's governance records. When None,
    obj-12 collection is skipped (the evidence is not available).

    ``session_export_dir`` is the directory containing provider-native
    session export files (``<session_id>.session.json``). When None,
    obj-13 collection is skipped. The export must be a single JSON
    document with a ``session_id`` field for identity recognition.

    ``autoswarm_argv`` is the CLI invocation (default: the uv-tool
    installed autoswarm)."""

    def __init__(self, store, autoswarm_home, workspace, *,
                 gate_root=None, session_export_dir=None,
                 autoswarm_argv=None):
        self._store = store
        self._home = Path(autoswarm_home)
        self._workspace = Path(workspace)
        self._gate_root = Path(gate_root) if gate_root else None
        self._session_export_dir = (
            Path(session_export_dir) if session_export_dir else None)
        self._argv = autoswarm_argv or [
            sys.executable, "-m", "autoswarm"]

    def _call_dir(self, call_id):
        return self._home / "calls" / call_id

    def _cli(self, args, *, stdin_text=None):
        env = dict(os.environ)
        env["AUTOSWARM_HOME"] = str(self._home)
        result = subprocess.run(
            [*self._argv, *args],
            input=stdin_text, capture_output=True, text=True, env=env)
        if result.returncode != 0:
            payload = None
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                pass
            if payload and not payload.get("ok"):
                error = payload.get("error", {})
                raise RuntimeError(
                    "autoswarm %s: [%s] %s"
                    % (args[0], error.get("code", "?"),
                       error.get("message", result.stderr)))
            raise RuntimeError(
                "autoswarm %s exited %d: %s"
                % (args[0], result.returncode, result.stderr))
        return json.loads(result.stdout).get("data")

    def launch(self, projection_address, provider, role, call_id):
        projection_bytes = self._store.get(projection_address)
        projection = json.loads(projection_bytes)
        prompt = projection["engineering_context"]["guidance"]
        info = self._cli([
            "run",
            "--agent", provider,
            "--no-parent",
            "--background",
            "--cwd", str(self._workspace),
            "--call-id", call_id,
            "--input", "-"],
            stdin_text=prompt)
        return info.get("native_session_id") or info["call_id"]

    def launch_manifest(self, manifest_address, manifest, *,
                        manifest_bytes=None):
        """Launch from a canonical ExecutionManifest. Passes the exact
        manifest bytes to AutoSwarm through --manifest so AutoSwarm
        owns validation and persistence in its call directory.
        When ``manifest_bytes`` is the canonical bytes from the builder,
        those exact bytes cross the process boundary."""
        import tempfile
        call_id = manifest["call_id"]
        provider = manifest["provider_assignment"]["provider"]
        if manifest_bytes is None:
            manifest_bytes = json.dumps(
                manifest, sort_keys=True,
                ensure_ascii=False).encode("utf-8")
        manifest_file = Path(tempfile.mktemp(
            prefix="manifest-", suffix=".json",
            dir=str(self._home)))
        manifest_file.write_bytes(manifest_bytes)
        try:
            projection_bytes = self._store.get(
                manifest["projection_address"])
            projection = json.loads(projection_bytes)
            prompt = projection["engineering_context"]["guidance"]
            model = manifest.get("provider_assignment", {}).get("model")
            args = [
                "run",
                "--agent", provider,
                "--no-parent",
                "--background",
                "--cwd", str(self._workspace),
                "--call-id", call_id,
                "--manifest", str(manifest_file),
                "--input", "-"]
            if model:
                args.insert(args.index("--no-parent"), "--model")
                args.insert(args.index("--no-parent"), model)
            info = self._cli(args, stdin_text=prompt)
        finally:
            try:
                manifest_file.unlink()
            except OSError:
                pass
        return info.get("native_session_id") or info["call_id"]

    def find_session(self, call_id):
        directory = self._call_dir(call_id)
        task = _read_json(directory / "task.json")
        if task is None:
            return None
        handle = _read_json(directory / "handle.json")
        result = _read_json(directory / "result.json")
        session_id = None
        if result is not None:
            session_id = result.get("native_session_id")
        elif handle is not None:
            session_id = handle.get("native_session_id")
        if session_id is None:
            return None
        # obj-11 evidence: the original task.json is the AutoSwarm
        # producer record that carries the received-prompt identity
        # (task["prompt"]), the Sequencer-minted call_id and the
        # provider selection. The native_session_id is obtained
        # independently from handle.json or result.json — the adapter
        # reads task.json's exact bytes without rewriting.
        evidence_bytes = (directory / "task.json").read_bytes()
        return {"native_session_id": session_id,
                "evidence": evidence_bytes}

    def positive_launch_failure(self, call_id):
        directory = self._call_dir(call_id)
        result = _read_json(directory / "result.json")
        if result is None:
            return None
        if result.get("outcome") != "failed":
            return None
        phase = (result.get("diagnostics") or {}).get("phase", "")
        if phase in ("startup", "handshake"):
            return result.get("error") or "launch failed at %s" % phase
        if not result.get("native_session_id"):
            return result.get("error") or "failed without establishing a session"
        return None

    def collect(self, call_id):
        directory = self._call_dir(call_id)
        result = _read_json(directory / "result.json")
        if result is None:
            return None
        session_id = result.get("native_session_id")
        witness_files = []
        result_path = directory / "result.json"
        if result_path.is_file():
            witness_files.append(
                ("obj-11", WITNESS_KINDS["obj-11"]["producer"],
                 result_path.read_bytes()))
        for entry_bytes in self._collect_governance(session_id):
            witness_files.append(
                ("obj-12", WITNESS_KINDS["obj-12"]["producer"],
                 entry_bytes))
        transcript_bytes = self._collect_transcript(
            directory, session_id)
        if transcript_bytes is not None:
            witness_files.append(
                ("obj-13", WITNESS_KINDS["obj-13"]["producer"],
                 transcript_bytes))
        collected = {
            "witness_files": witness_files,
            "outcome": normalize_outcome(
                result.get("outcome", "failed")),
        }
        content = result.get("content")
        if isinstance(content, str) and content:
            collected["artifact_bytes"] = content.encode("utf-8")
        return collected

    def _collect_governance(self, session_id):
        """obj-12: individual Helios command-governance decision files
        from the session's evidence tree at the configured gate root.

        Path: <gate_root>/evidence/integrity/sessions/<session_id>/commands/
        Each .decision.json is a Helios-owned record written by
        Write-IntegrityDecision during the specialist's execution.

        Returns a list of raw file bytes, one per decision file —
        never a combined array. Each entry is admitted as a separate
        witness so that extract_facts, identity_conflicts and
        authorization_direction_conflict (D-C4-14) operate on the
        original single-record shape they expect."""
        if self._gate_root is None or not session_id:
            return []
        evidence_dir = (self._gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        if not evidence_dir.is_dir():
            return []
        entries = []
        for path in sorted(evidence_dir.glob("*.decision.json")):
            data = path.read_bytes()
            if data:
                entries.append(data)
        return entries

    def _collect_transcript(self, directory, session_id):
        """obj-13: a provider-native session transcript export.

        The registered producer is agent_clis (the CLI tool itself).
        AutoSwarm's stdout.log is a runner pipe capture, not a
        provider-native export, and is never used here.

        When ``session_export_dir`` is configured, the adapter reads
        the session export file at
        ``<session_export_dir>/<session_id>.session.json`` — a single
        JSON document whose ``session_id`` field the existing witness
        recognizer can extract for identity correlation.

        The file must be produced by the provider or a registered
        post-execution exporter. The adapter reads its raw bytes
        without rewriting, verifying only that the export resolves
        and is non-empty."""
        if self._session_export_dir is None or not session_id:
            return None
        export_path = (self._session_export_dir
                       / ("%s.session.json" % session_id))
        if not export_path.is_file():
            return None
        data = export_path.read_bytes()
        if not data:
            return None
        return data
