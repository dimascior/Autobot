"""Production AutoSwarm Transport (Phase B: replaces FakeTransport).

Launches specialist sessions through the AutoSwarm CLI with the
Sequencer's call_id as the idempotency key. Evidence is read directly
from AutoSwarm's durable call records — the adapter never manufactures
or rewrites provider-owned bytes.

Evidence families:
    obj-11  AutoSwarm session record (admitted at adoption via find_session)
    obj-12  Helios governance evidence (collected from the session's gate chain)
    obj-13  Agent transcript (collected from AutoSwarm's session content)
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
    The call directory tree under it is the durable state the adapter
    reads for find_session, positive_launch_failure and collect.

    ``workspace`` is the working directory passed to AutoSwarm for
    every specialist session.

    ``autoswarm_argv`` is the CLI invocation (default: the uv-tool
    installed autoswarm)."""

    def __init__(self, store, autoswarm_home, workspace, *,
                 autoswarm_argv=None):
        self._store = store
        self._home = Path(autoswarm_home)
        self._workspace = Path(workspace)
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
        evidence_record = {
            "schema_version": "v1",
            "kind": "AutoSwarmSessionRecord",
            "call_id": call_id,
            "native_session_id": session_id,
            "provider": task.get("provider"),
            "workspace": task.get("workspace"),
            "created_at": task.get("created_at"),
        }
        if handle:
            evidence_record["started_at"] = handle.get("started_at")
            evidence_record["phase"] = handle.get("phase")
        if result:
            evidence_record["outcome"] = result.get("outcome")
            evidence_record["finished_at"] = result.get("finished_at")
        evidence_bytes = json.dumps(
            evidence_record, sort_keys=True).encode("utf-8")
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
        witness_files = []
        governance_bytes = self._collect_governance(directory)
        if governance_bytes is not None:
            witness_files.append(
                ("obj-12", WITNESS_KINDS["obj-12"]["producer"],
                 governance_bytes))
        transcript_bytes = self._collect_transcript(directory, result)
        if transcript_bytes is not None:
            witness_files.append(
                ("obj-13", WITNESS_KINDS["obj-13"]["producer"],
                 transcript_bytes))
        return {
            "witness_files": witness_files,
            "outcome": normalize_outcome(
                result.get("outcome", "failed")),
        }

    @staticmethod
    def _collect_governance(directory):
        """obj-12: Helios governance evidence from the session's gate
        chain, if present."""
        gate_dir = directory / "governance"
        if not gate_dir.is_dir():
            return None
        records = []
        for path in sorted(gate_dir.glob("*.json")):
            record = _read_json(path)
            if record is not None:
                records.append(record)
        if not records:
            return None
        return json.dumps(records, sort_keys=True).encode("utf-8")

    @staticmethod
    def _collect_transcript(directory, result):
        """obj-13: the agent's session transcript (content from the
        terminal result, or stdout.log if available)."""
        content = result.get("content")
        if content:
            transcript = {
                "schema_version": "v1",
                "kind": "AgentTranscript",
                "call_id": result.get("call_id"),
                "native_session_id": result.get("native_session_id"),
                "content": content,
            }
            return json.dumps(
                transcript, sort_keys=True).encode("utf-8")
        stdout_log = directory / "stdout.log"
        if stdout_log.is_file():
            return stdout_log.read_bytes()
        return None
