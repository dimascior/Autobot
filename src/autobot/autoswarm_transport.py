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

    # The name prefix of every isolated worktree this transport creates.
    WORKTREE_PREFIX = "autobot-wt-"
    # Where the accepted results a call's work depends on are placed
    # inside its isolated worktree (never tracked: see _exclude_inputs).
    INPUTS_DIR = (".autobot", "inputs")

    def __init__(self, store, autoswarm_home, workspace, *,
                 gate_root=None, session_export_dir=None,
                 autoswarm_argv=None):
        self._staged = {}
        self._session = {}
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
        # AutoSwarm reads and writes its standard streams as UTF-8
        # whatever the platform's locale is; so does this side, or a
        # prompt with a character outside the locale's code page would
        # reach the provider as different bytes than were frozen.
        result = subprocess.run(
            [*self._argv, *args],
            input=stdin_text, capture_output=True, text=True, env=env,
            encoding="utf-8", errors="replace")
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
        worktree_dir = None
        try:
            projection_bytes = self._store.get(
                manifest["projection_address"])
            projection = json.loads(projection_bytes)
            prompt = projection["engineering_context"]["guidance"]
            model = manifest.get("provider_assignment", {}).get("model")
            workspace = str(self._workspace)
            constraints = manifest.get("constraints", {})
            # SOURCE_MODIFYING work always runs in an isolated worktree at
            # its frozen source revision. READ_ONLY work does too when the
            # manifest carries one: the reader then sees exactly that
            # revision, never whatever a shared checkout's files hold.
            source_rev = constraints.get("source_revision")
            if constraints.get("finalization_class") == "SOURCE_MODIFYING" \
                    and not source_rev:
                source_rev = "refs/heads/main"
            # A relaunch of a call AutoSwarm already holds (a restart
            # between dispatch and adoption) reuses the workspace that
            # call was given; a second worktree is never created for it.
            existing = None
            try:
                existing = _read_json(self._call_dir(call_id) / "task.json")
            except (ValueError, OSError):
                existing = None
            if isinstance(existing, dict) and existing.get("workspace"):
                workspace = str(existing["workspace"])
            elif source_rev:
                import tempfile as _tf
                worktree_dir = Path(_tf.mkdtemp(
                    prefix=self.WORKTREE_PREFIX, dir=str(self._workspace)))
                subprocess.run(
                    ["git", "-C", str(self._workspace), "worktree", "add",
                     "--detach", str(worktree_dir), source_rev],
                    capture_output=True, text=True, check=True)
                workspace = str(worktree_dir)
            # The accepted results this work depends on, staged by the
            # caller, go into the isolated worktree and nowhere else: a
            # shared workspace is never written.
            staged = self._staged.pop(call_id, None)
            if staged and self._is_own_worktree(Path(workspace)):
                self._write_inputs(Path(workspace), staged)
            # The rest of the route — its reasoning effort and its native
            # session arguments (a sandbox mode, an approval policy) — is
            # session configuration the caller staged for this call. The
            # arguments' placeholders are filled from the workspace this
            # session is actually launched in.
            effort, session_args = self._session_config(
                call_id, workspace, provider)
            args = ["run", "--agent", provider]
            if model:
                args += ["--model", model]
            if effort:
                args += ["--effort", effort]
            for native in session_args:
                args.append("--provider-arg=" + native)
            args += [
                "--no-parent",
                "--background",
                "--cwd", workspace,
                "--call-id", call_id,
                "--manifest", str(manifest_file),
                "--input", "-"]
            info = self._cli(args, stdin_text=prompt)
        finally:
            try:
                manifest_file.unlink()
            except OSError:
                pass
        return info.get("native_session_id") or info["call_id"]

    def stage_inputs(self, call_id, files):
        """Accept the ACCEPTED results one call's work depends on, to be
        placed in its isolated worktree when the session is launched.

        ``files`` is a list of (name, bytes). The caller read each one
        from the content store by the address the authority ledger holds
        for an accepted attempt's artifact; this adapter puts those exact
        bytes where the specialist can read them and originates nothing.
        Held in memory until that call's launch."""
        self._staged[call_id] = [(str(name), bytes(data))
                                 for name, data in files]

    # Placeholders a route's native arguments may carry. The command-gate
    # pending directory has two: the path as configured and the path it
    # resolves to (the gate root may sit behind a junction, and a sandbox
    # compares resolved paths).
    SESSION_PLACEHOLDERS = ("{repo_git}", "{gate_pending}",
                            "{gate_pending_real}", "{workspace}")

    def stage_session(self, call_id, provider_args=(), effort=None,
                      provider=None):
        """Accept the session configuration of the execution route one
        call is dispatched on — the route's reasoning effort and its
        native session arguments — to be passed to the provider when that
        call's session is launched. It configures the session (how deeply
        the model reasons, a sandbox mode, an approval policy); it is no
        part of the manifest or of any contract. ``provider`` names the
        provider the configuration is for: it is applied only to a
        manifest that names the same one. Held in memory until that
        call's launch."""
        self._session[call_id] = {
            "effort": str(effort) if effort else None,
            "args": [str(arg) for arg in provider_args or []],
            "provider": str(provider) if provider else None}

    def _session_config(self, call_id, workspace, provider=None):
        staged = self._session.pop(call_id, None) or {}
        intended = staged.get("provider")
        if intended and provider and intended != provider:
            # One route's sandbox and approval arguments are never handed
            # to another provider's session (F-TF-28).
            raise RuntimeError(
                "the session configuration staged for call %s is for"
                " provider %s; its manifest names %s"
                % (call_id, intended, provider))
        return staged.get("effort"), self._expand_session_args(
            staged.get("args") or [], workspace)

    def probe_route(self, route, prompt, source_rev="refs/heads/main"):
        """Launch one session on an execution route OUTSIDE any attempt:
        a route preflight. The session gets what a production session on
        that route gets — the provider, model, effort and native session
        arguments, an isolated worktree at ``source_rev`` — and no
        manifest, because there is no attempt behind it. Nothing it
        returns is evidence of engineering work. Returns the AutoSwarm
        call id and the worktree; ``release_workspace`` removes the
        worktree."""
        import tempfile
        worktree = Path(tempfile.mkdtemp(
            prefix=self.WORKTREE_PREFIX, dir=str(self._workspace)))
        subprocess.run(
            ["git", "-C", str(self._workspace), "worktree", "add",
             "--detach", str(worktree), source_rev],
            capture_output=True, text=True, check=True)
        args = ["run", "--agent", route["provider"]]
        if route.get("model"):
            args += ["--model", route["model"]]
        if route.get("effort"):
            args += ["--effort", route["effort"]]
        for native in self._expand_session_args(
                route.get("provider_args") or [], str(worktree)):
            args.append("--provider-arg=" + native)
        args += ["--no-parent", "--background", "--cwd", str(worktree),
                 "--input", "-"]
        info = self._cli(args, stdin_text=prompt)
        return {"call_id": info["call_id"], "workspace": str(worktree)}

    def probe_result(self, call_id):
        """AutoSwarm's own result record of one call, or None while the
        call has not ended."""
        return _read_json(self._call_dir(call_id) / "result.json")

    def providers(self):
        """Which providers AutoSwarm reports it can launch:
        {provider: bool}."""
        data = self._cli(["agents", "--json"])
        rows = data if isinstance(data, list) else (
            (data.get("agents") or data.get("providers") or [])
            if isinstance(data, dict) else [])
        return {row["provider"]: bool(row.get("available"))
                for row in rows
                if isinstance(row, dict) and row.get("provider")}

    def _expand_session_args(self, staged, workspace):
        values = {
            "{workspace}": str(workspace),
            "{repo_git}": str(self._workspace / ".git")
            if (self._workspace / ".git").is_dir() else None,
            "{gate_pending}": str(self._gate_root / "pending")
            if self._gate_root is not None else None,
            "{gate_pending_real}": os.path.realpath(
                str(self._gate_root / "pending"))
            if self._gate_root is not None else None,
        }
        out = []
        for arg in staged:
            for placeholder in self.SESSION_PLACEHOLDERS:
                if placeholder in arg:
                    value = values.get(placeholder)
                    if value is None:
                        raise RuntimeError(
                            "route argument %r needs %s, which this"
                            " transport has no value for"
                            % (arg, placeholder))
                    arg = arg.replace(placeholder, value)
            out.append(arg)
        return out

    def _is_own_worktree(self, path):
        return path.name.startswith(self.WORKTREE_PREFIX) \
            and os.path.normcase(str(path.parent)) == os.path.normcase(
                str(self._workspace)) and path.is_dir()

    def _write_inputs(self, worktree, staged):
        target = worktree.joinpath(*self.INPUTS_DIR)
        target.mkdir(parents=True, exist_ok=True)
        for name, data in staged:
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_"
                           for ch in name)[:80] or "input"
            (target / (safe + ".md")).write_bytes(data)
        self._exclude_inputs()

    def _exclude_inputs(self):
        """Keep the inputs directory out of Git's view of every worktree
        (the repository's own info/exclude, which is local and never
        committed): a SOURCE_MODIFYING specialist's worktree must still
        read as clean, and the files can never be staged by accident."""
        git_dir = self._workspace / ".git"
        if not git_dir.is_dir():
            return
        exclude = git_dir / "info" / "exclude"
        line = self.INPUTS_DIR[0] + "/"
        try:
            text = exclude.read_text(encoding="utf-8") \
                if exclude.is_file() else ""
            if line in text.splitlines():
                return
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with open(str(exclude), "a", encoding="utf-8") as fh:
                fh.write(("" if not text or text.endswith("\n") else "\n")
                         + line + "\n")
        except OSError:
            pass

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

    def release_workspace(self, call_id):
        """Remove the isolated worktree this transport created for one
        call, once the caller says the call's work has settled. Only a
        directory this transport made (its own prefix, directly under its
        workspace, recorded by AutoSwarm as the call's workspace) is ever
        removed; the shared workspace itself never is. Returns whether a
        worktree was removed."""
        task = _read_json(self._call_dir(call_id) / "task.json")
        if not isinstance(task, dict) or not task.get("workspace"):
            return False
        path = Path(task["workspace"])
        if not path.name.startswith(self.WORKTREE_PREFIX):
            return False
        if os.path.normcase(str(path.parent)) != os.path.normcase(
                str(self._workspace)):
            return False
        if not path.is_dir():
            return False
        proc = subprocess.run(
            ["git", "-C", str(self._workspace), "worktree", "remove",
             "--force", str(path)], capture_output=True, text=True)
        return proc.returncode == 0

    def pending_requests(self, call_id):
        """The session's undecided native permission requests, read from
        AutoSwarm's own durable records
        (``calls/<call_id>/approvals/approval_*/request.json``): a
        request is pending while it has neither a decision nor a
        resolution and the call has neither ended nor been cancelled. A
        record still being written is simply not seen until the next
        look."""
        directory = self._call_dir(call_id)
        if (directory / "result.json").is_file() \
                or (directory / "cancel.json").is_file():
            return []
        approvals = directory / "approvals"
        if not approvals.is_dir():
            return []
        pending = []
        for path in sorted(approvals.glob("approval_*")):
            if (path / "decision.json").is_file() \
                    or (path / "resolution.json").is_file():
                continue
            try:
                request = _read_json(path / "request.json")
            except (ValueError, OSError):
                continue
            if isinstance(request, dict) and request.get("request_id"):
                pending.append(request)
        return pending

    def decide_request(self, call_id, request_id, decision, reason=""):
        """Relay the caller's decision on one request through the
        AutoSwarm CLI (``approve`` / ``deny``, scope ``once``). AutoSwarm
        owns the record of the decision; this adapter originates none."""
        if decision not in ("allow", "deny"):
            raise ValueError("decision must be 'allow' or 'deny'")
        args = ["approve" if decision == "allow" else "deny",
                call_id, request_id, "--scope", "once"]
        if reason:
            args += ["--message", reason]
        return self._cli(args)

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
