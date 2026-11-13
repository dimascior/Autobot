"""The production verifier runner — the registered ``verifier_runner``
seat (C4-M1).

principals.json registers ``verifier_runner``; cap-35 makes it the owner
of "Verification, failure and remote-verification receipts" with the
sequencer as consumer and the task_force FORBIDDEN; cap-34 keeps assertion
evaluation itself with ``registered_verifiers`` — the runner executes the
EXACT registered closure and translates its typed conclusion, never
evaluating anything in its own voice. Like every other seat, it GRANTS NO
PROTOCOL AUTHORITY: it reads frozen and admitted facts through the
Sequencer's public read surface (``verification_state``), executes under
the frozen registration's closure identity, mints the canonical obj-19 or
obj-20 bytes, and submits them through the existing admission seam
(``register_verifier_result``), which re-validates every binding. The
Sequencer still executes nothing (the chapter-0 actor model); only its
registered transitions move state.

The authority discipline, per registered rule:

- INV-66/INV-53 (D-57): the runner executes exactly the registered
  artifact closure — the script and lock bytes at their content addresses
  (materialized from the content store, never a same-named workspace
  file), under the interpreter the registration's ``interpreter_id``
  names. A missing leaf, a recompute-divergent ``artifact_root`` or an
  unavailable registered interpreter is an IntegrityHalt — never a
  substitution, never a PATH lookup (the registered t-s12 arm is
  ``bound_artifact_unavailable``).
- SEM-16 (D-57): a ``service:*`` registration never executes for a
  DETERMINISTIC assertion.
- D-56: the obj-18 environment is captured ONCE per attempt by the
  environment probe (named env vars and tools only) and matched against
  every declared constraint BEFORE any execution; a mismatch executes
  nothing and is a HALT-grade refusal (t-s12 ``env_mismatch``), never a
  failed assertion.
- Candidate immutability: the verification subject is the admitted obj-16
  — the runner materializes the candidate's artifact bytes from the
  content store and passes the frozen candidate facts to the verifier; a
  workspace that drifted after the freeze cannot reach the subject.
- INV-06 (CH4-llm-output-never-pass): a conclusion exists ONLY as the
  registered result protocol — the verifier process must exit 0 and emit,
  as its last stdout line, ``{"autobot_verifier_result": {"conclusion":
  "PASS"|"FAIL", ...}}``. Prose ("tests passed"), a nonzero exit, or a
  malformed line is INCONCLUSIVE — authority uncertainty on the t-s12
  unknown arm, never PASS and never an ordinary FAIL.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from contract_library import artifact_root, canonicalize, content_address

from . import environment_probe
from . import verification_receipts
from .models import ConflictError, IntegrityHalt
from .verification import (
    VerifierRunner,
    environment_satisfies,
    verifier_eligibility,
)

# The registered verifier result protocol lives in verification_receipts
# (RESULT_KEY, parse_conclusion, outcome_consistency): one source of truth
# for the runner's translation, the admission seam and recovery (F-C4-1).
RESULT_KEY = verification_receipts.RESULT_KEY
_parse_conclusion = verification_receipts.parse_conclusion


def resolve_closure(store, registration):
    """INV-53/INV-66/SEM-15 at dispatch: recompute the artifact_root from
    the closure manifest, prove every required leaf's exact bytes exist in
    the content store, and prove the registered interpreter is the one
    actually available. Anything missing or divergent is an IntegrityHalt
    (never substituted); the sequence-level HALT is t-s12
    bound_artifact_unavailable. Returns the closure dict."""
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
    elif kind == "native":
        store.get(closure["executable_sha256"])
    else:
        raise IntegrityHalt(
            "closure kind %r is not executable by this runtime (v1"
            " executes reproducibly pinned python and native closures;"
            " a packaged closure HALTs rather than being approximated)"
            % kind)
    return closure


class ProductionVerifierRunner(VerifierRunner):
    """The Chapter 4 production implementation of the typed M4 contract.

    ``tool_prober`` and ``workdir`` are injection points for deterministic
    tests; neither changes the authority rules above."""

    def __init__(self, sequencer, store, tool_prober=None, workdir=None):
        self.sequencer = sequencer
        self.store = store
        self.tool_prober = tool_prober
        self.workdir = str(workdir) if workdir is not None else None

    # ---------------------------------------------------- typed interface

    def capture_environment(self, attempt_identity, constraints=()):
        """obj-18 bytes for this attempt (cap-33, D-56): canonical bytes of
        the probe's capture, constraint-named env vars and tools only."""
        payload = environment_probe.capture_environment(
            attempt_identity, self.sequencer.clock, constraints,
            tool_prober=self.tool_prober)
        return canonicalize(payload)

    def run_assertion(self, assertion, registration, context):
        """Execute the registered closure for one DETERMINISTIC assertion
        (cap-34 stays with the registered verifier: the closure concludes,
        the runner translates). ``context`` carries the binding facts and
        the frozen subject:
        attempt_id, sequence_id, fence, attempt_contract_hash, record_hash,
        candidate_receipt_sha256, candidate_payload, environment_sha256,
        environment_payload, registration_sha256.
        Returns ``(outcome, receipt_bytes)``."""
        refusal = verifier_eligibility(assertion, registration)
        if refusal is not None:
            raise ConflictError(refusal)
        closure = resolve_closure(self.store, registration)
        execution, stdout_bytes = self._execute(closure, registration,
                                                assertion, context)
        conclusion = None
        if execution["exit_code"] == 0:
            conclusion = _parse_conclusion(stdout_bytes)
        if conclusion == "PASS":
            outcome, basis = "PASS", None
        elif conclusion == "FAIL":
            outcome, basis = "FAIL", "verifier_reported_fail"
        elif execution["exit_code"] != 0:
            outcome, basis = "INCONCLUSIVE", "verifier_execution_failed"
        else:
            outcome, basis = "INCONCLUSIVE", "malformed_verifier_result"
        binding = {"sequence_id": context["sequence_id"],
                   "attempt_id": context["attempt_id"],
                   "fence": context["fence"],
                   "attempt_contract_hash":
                       context["attempt_contract_hash"],
                   "record_hash": context["record_hash"],
                   "candidate_receipt_sha256":
                       context["candidate_receipt_sha256"],
                   "environment_sha256": context["environment_sha256"]}
        payload = verification_receipts.build_result_payload(
            binding, assertion["assertion_id"],
            assertion.get("verifier_id"), registration,
            context["registration_sha256"], outcome, execution,
            conclusion_basis=basis)
        return outcome, verification_receipts.receipt_bytes(payload)

    # ------------------------------------------------------ the full pass

    def verify_attempt(self, attempt_id):
        """Steps 1..11 of the registered production flow for every
        DETERMINISTIC assertion of one EVIDENCE_COMPLETE attempt that has
        no admitted result yet: resolve the frozen assertion and its exact
        bound registration, verify eligibility and the registered closure,
        bind the admitted obj-16 candidate, capture-or-reuse the attempt's
        ONE obj-18 environment, validate every declared constraint BEFORE
        executing anything, execute the exact closure, translate the typed
        conclusion, mint canonical obj-19/obj-20 bytes and submit them
        through the Sequencer's admission seam. HUMAN_ATTESTED assertions
        are never touched (they pass only by your obj-37, D-52).

        Returns ``{"results": [...], "inconclusive": bool}`` — an
        INCONCLUSIVE member satisfies nothing and the caller applies the
        registered t-s12 unknown arm."""
        state = self.sequencer.verification_state(attempt_id)
        deterministic = [a for a in state["assertions"]
                        if a.get("mode") == "DETERMINISTIC"]
        constraints = [a["environment_constraint"] for a in deterministic
                       if isinstance(a.get("environment_constraint"), dict)]
        identity = {"sequence_id": state["sequence_id"],
                    "attempt_id": state["attempt_id"],
                    "version": state["version"],
                    "fence": state["fence"]}
        if "environment_sha256" in state:
            env_bytes = self.store.get(state["environment_sha256"])
        else:
            # Capture once and make it durable BEFORE any verifier launch
            # (the crash matrix's capture point): restart resumes from the
            # admitted capture and never recaptures.
            env_bytes = self.capture_environment(identity, constraints)
            self.sequencer.admit_environment_capture(attempt_id, env_bytes)
        env_payload = json.loads(env_bytes.decode("utf-8"))
        env_address = content_address(env_bytes)
        # Every declared constraint is matched BEFORE any execution: a
        # wrong environment runs nothing and fails nothing (D-56).
        for entry in deterministic:
            constraint = entry.get("environment_constraint")
            if constraint is None:
                continue
            refusal = environment_satisfies(constraint, env_payload)
            if refusal is not None:
                raise ConflictError(
                    "environment mismatch (t-s12 env_mismatch): %s"
                    % refusal)
        results = []
        inconclusive = False
        for entry in deterministic:
            existing = self.sequencer.db.conn.execute(
                "SELECT outcome FROM attempt_verifications"
                " WHERE attempt_id = ? AND assertion_id = ?",
                (attempt_id, entry["assertion_id"])).fetchone()
            if existing is not None:
                if existing["outcome"] == "INCONCLUSIVE":
                    inconclusive = True
                continue
            bound = state["registrations"][entry.get("verifier_id")]
            context = {
                "attempt_id": state["attempt_id"],
                "sequence_id": state["sequence_id"],
                "fence": state["fence"],
                "attempt_contract_hash": state["attempt_contract_hash"],
                "record_hash": state["record_hash"],
                "candidate_receipt_sha256":
                    state["candidate_receipt_sha256"],
                "candidate_payload": state["candidate_payload"],
                "environment_sha256": env_address,
                "environment_payload": env_payload,
                "registration_sha256": bound["object_sha256"],
            }
            outcome, receipt = self.run_assertion(entry, bound["payload"],
                                                  context)
            admitted = self.sequencer.register_verifier_result(
                attempt_id, entry["assertion_id"], outcome,
                verification_receipts.RESULT_PRODUCER, receipt,
                environment=env_bytes)
            if outcome == "INCONCLUSIVE":
                inconclusive = True
            results.append({"assertion_id": entry["assertion_id"],
                            "outcome": outcome,
                            "verification_id":
                                admitted["verification_id"],
                            "receipt_address":
                                admitted["receipt_address"]})
        return {"results": results, "inconclusive": inconclusive}

    # --------------------------------------------------------- execution

    def _execute(self, closure, registration, assertion, context):
        """Materialize the exact closure and the frozen subject into a
        fresh run directory, execute, and return (execution facts, stdout
        bytes). The subject's artifact bytes come from the content store
        by their admitted addresses — never from any workspace — so a
        verifier can never silently evaluate a newer filesystem version."""
        entrypoint = registration["entrypoint"]
        if ".." in entrypoint.split("/"):
            raise ConflictError(
                "registration entrypoint %r escapes its closure"
                % entrypoint)
        run_dir = tempfile.mkdtemp(prefix="autobot-verify-",
                                   dir=self.workdir)
        try:
            script_path = os.path.join(run_dir,
                                       *entrypoint.split("/"))
            parent = os.path.dirname(script_path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            if closure["kind"] == "python":
                script_bytes = self.store.get(closure["script_sha256"])
                lock_bytes = self.store.get(
                    closure["dependency_lock_sha256"])
                with open(script_path, "wb") as fh:
                    fh.write(script_bytes)
                with open(os.path.join(run_dir, "dependency.lock"),
                          "wb") as fh:
                    fh.write(lock_bytes)
                argv = [sys.executable, "-I", script_path]
            else:  # native (resolve_closure admitted only python|native)
                executable_bytes = self.store.get(
                    closure["executable_sha256"])
                with open(script_path, "wb") as fh:
                    fh.write(executable_bytes)
                argv = [script_path]
            subject_dir = os.path.join(run_dir, "subject")
            os.makedirs(subject_dir)
            manifest = []
            for artifact in context["candidate_payload"]["artifacts"]:
                data = self.store.get(artifact["content_sha256"])
                name = artifact["content_sha256"].split(":", 1)[1]
                file_path = os.path.join(subject_dir, name)
                with open(file_path, "wb") as fh:
                    fh.write(data)
                manifest.append({
                    "logical_path": artifact["logical_path"],
                    "content_sha256": artifact["content_sha256"],
                    "file": file_path})
            context_path = os.path.join(run_dir, "context.json")
            with open(context_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "assertion": assertion,
                    "candidate": context["candidate_payload"],
                    "environment": context["environment_payload"],
                    "registration": {
                        "registration_id":
                            registration["registration_id"],
                        "verifier_id": registration.get("verifier_id"),
                        "version": registration.get("version"),
                    },
                    "subject": {"dir": subject_dir,
                                "artifacts": manifest},
                }, fh, sort_keys=True)
            argv.append(context_path)
            proc = subprocess.run(argv, capture_output=True, cwd=run_dir,
                                  timeout=600)
            stdout_bytes = proc.stdout or b""
            execution = {
                "closure_kind": closure["kind"],
                "entrypoint": entrypoint,
                "exit_code": proc.returncode,
                "output_sha256": self.store.put(stdout_bytes),
            }
            return execution, stdout_bytes
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
