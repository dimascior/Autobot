"""Production AutoSwarm Transport (Phase B: replaces FakeTransport).

Launches specialist sessions through the AutoSwarm CLI with the
Sequencer's call_id as the idempotency key. Evidence is read directly
from AutoSwarm's durable call records and the Helios evidence tree —
the adapter never manufactures evidence or rewrites provider-owned bytes.

Evidence families and their actual locations:
    obj-11  AutoSwarm's own result.json (producer: autoswarm)
    obj-12  Helios decision files at
            <gate_root>/evidence/integrity/sessions/<session_id>/commands/
    obj-13  AutoSwarm's stdout.log (raw provider pipe capture)
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

    ``autoswarm_argv`` is the CLI invocation (default: the uv-tool
    installed autoswarm)."""

    def __init__(self, store, autoswarm_home, workspace, *,
                 gate_root=None, autoswarm_argv=None):
        self._store = store
        self._home = Path(autoswarm_home)
        self._workspace = Path(workspace)
        self._gate_root = Path(gate_root) if gate_root else None
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
        # obj-11 evidence: prefer the original result.json when
        # available (terminal producer file); fall back to handle.json
        # (running session record). Both are AutoSwarm-owned files read
        # byte-identically — the adapter adds nothing.
        evidence_path = directory / "result.json"
        if result is None:
            evidence_path = directory / "handle.json"
        evidence_bytes = evidence_path.read_bytes()
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
            raise RuntimeError(
                "collect(%s): no result.json (session not terminal)"
                % call_id)
        session_id = result.get("native_session_id")
        witness_files = []
        governance_bytes = self._collect_governance(session_id)
        if governance_bytes is not None:
            witness_files.append(
                ("obj-12", WITNESS_KINDS["obj-12"]["producer"],
                 governance_bytes))
        transcript_bytes = self._collect_transcript(directory)
        if transcript_bytes is not None:
            witness_files.append(
                ("obj-13", WITNESS_KINDS["obj-13"]["producer"],
                 transcript_bytes))
        return {
            "witness_files": witness_files,
            "outcome": normalize_outcome(
                result.get("outcome", "failed")),
        }

    def _collect_governance(self, session_id):
        """obj-12: original Helios command-governance decision files from
        the session's evidence tree at the configured gate root.

        Path: <gate_root>/evidence/integrity/sessions/<session_id>/commands/
        Each .decision.json is a Helios-owned record written by
        Write-IntegrityDecision during the specialist's execution."""
        if self._gate_root is None or not session_id:
            return None
        evidence_dir = (self._gate_root / "evidence" / "integrity"
                        / "sessions" / session_id / "commands")
        if not evidence_dir.is_dir():
            return None
        records = []
        for path in sorted(evidence_dir.glob("*.decision.json")):
            record = _read_json(path)
            if record is not None:
                records.append(record)
        if not records:
            return None
        return json.dumps(records, sort_keys=True).encode("utf-8")

    @staticmethod
    def _collect_transcript(directory):
        """obj-13: the original provider stdout pipe capture. This is
        the raw byte stream AutoSwarm's runner wrote from the provider
        process — not a reconstructed summary or adapter envelope."""
        stdout_log = directory / "stdout.log"
        if stdout_log.is_file():
            data = stdout_log.read_bytes()
            if data:
                return data
        return None
