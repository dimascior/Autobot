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
  artifact closure — the python script+lock, the native executable or the
  packaged manifest's entry bytes at their content addresses (materialized
  from the content store, never a same-named workspace file), under the
  interpreter the closure pins. A missing leaf, a recompute-divergent
  ``artifact_root``, an unavailable pinned interpreter or a container
  ``image_digest`` form is an IntegrityHalt — never a substitution, never
  a PATH lookup (the registered t-s12 arm is
  ``bound_artifact_unavailable``). Since C4-M2 the resolver and
  materializer live in ``transforms``: ONE closure discipline shared by
  verifier executions and transform executions.
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
import tempfile

from contract_library import canonicalize, content_address

from . import environment_probe
from . import transforms
from . import verification_receipts
from .models import ConflictError
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

# The closure discipline (INV-53/INV-66/SEM-15 resolution plus
# materialization) lives in ``transforms`` since C4-M2 — one resolver for
# verifier closures and transform closures alike, with the
# packaged/manifest form supported in both. Re-exported under its M1 name
# for the chapter-4 call sites and proofs.
resolve_closure = transforms.resolve_closure


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
        # C4-M2 (INV-44/INV-49): when the frozen record declares a
        # transform for the DISPATCHED role, the role-transform check is
        # part of verification — executed before any assertion so a child
        # that received the wrong prompt is caught even when every
        # assertion would pass.
        transform = state.get("role_transform")
        if transform is not None:
            row_key = "role-transform/%s" % transform["role"]
            existing = self.sequencer.db.conn.execute(
                "SELECT outcome FROM attempt_verifications"
                " WHERE attempt_id = ? AND assertion_id = ?",
                (attempt_id, row_key)).fetchone()
            if existing is not None:
                if existing["outcome"] == "INCONCLUSIVE":
                    inconclusive = True
            else:
                outcome, receipt = self._check_role_transform(
                    state, transform, env_address)
                admitted = self.sequencer.register_transform_check(
                    attempt_id, transform["role"], outcome,
                    verification_receipts.RESULT_PRODUCER, receipt,
                    environment=env_bytes)
                if outcome == "INCONCLUSIVE":
                    inconclusive = True
                results.append({"assertion_id": row_key,
                                "outcome": outcome,
                                "verification_id":
                                    admitted["verification_id"],
                                "receipt_address":
                                    admitted["receipt_address"]})
        # C4-M2 (D-52/D-53): incorporate admitted obj-37 attestations for
        # HUMAN_ATTESTED assertions into canonical obj-19 BY_ATTESTATION
        # results. No attestation, no row — D-52's human act is never
        # synthesized; the runner only gives an ADMITTED one its canonical
        # receipt form.
        attestations = state.get("attestations", {})
        for entry in state["assertions"]:
            if entry.get("mode") != "HUMAN_ATTESTED":
                continue
            address = attestations.get(entry["assertion_id"])
            if address is None:
                continue
            existing = self.sequencer.db.conn.execute(
                "SELECT outcome FROM attempt_verifications"
                " WHERE attempt_id = ? AND assertion_id = ?",
                (attempt_id, entry["assertion_id"])).fetchone()
            if existing is not None:
                continue
            binding = {"sequence_id": state["sequence_id"],
                       "attempt_id": state["attempt_id"],
                       "fence": state["fence"],
                       "attempt_contract_hash":
                           state["attempt_contract_hash"],
                       "record_hash": state["record_hash"],
                       "candidate_receipt_sha256":
                           state["candidate_receipt_sha256"]}
            payload = verification_receipts.build_attested_result_payload(
                binding, entry["assertion_id"], address)
            admitted = self.sequencer.register_attested_result(
                attempt_id, entry["assertion_id"],
                verification_receipts.RESULT_PRODUCER,
                verification_receipts.receipt_bytes(payload))
            results.append({"assertion_id": entry["assertion_id"],
                            "outcome": "ATTESTED",
                            "verification_id":
                                admitted["verification_id"],
                            "receipt_address":
                                admitted["receipt_address"]})
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

    def _check_role_transform(self, state, transform, env_address):
        """INV-44/INV-49: execute the EXACT bound transform closure over
        the record's single declared specification input, derive the
        OBSERVED identity from admitted obj-11 producer bytes, and mint
        the canonical ROLE_TRANSFORM receipt. The admission seam
        re-derives every piece of this (D-C3-2 discipline, including
        re-executing the transform): the runner's job is honest assembly,
        never authority."""
        record_payload = state["record_payload"]
        inputs = record_payload.get("specification_inputs", {}).get(
            "name_to_exact_byte_sha256", {})
        if len(inputs) != 1:
            raise ConflictError(
                "the frozen record declares %d specification inputs; the"
                " v1 role-transform binding is defined over exactly ONE"
                % len(inputs))
        input_name = sorted(inputs)[0]
        input_address = inputs[input_name]
        input_bytes = self.store.get(input_address)
        registration = transform["payload"]
        output_bytes, execution = transforms.execute_transform(
            self.store, registration, input_bytes, workdir=self.workdir)
        # The OBSERVED side comes from admitted producer bytes alone. The
        # sequencer's derivation is the ONE implementation (the admission
        # re-derives with the same code over the same durable rows), so
        # the runner reads through it rather than growing a second one.
        observed = self.sequencer._observed_prompt_identity(
            self.sequencer.db.conn.cursor(), state["attempt_id"])
        check = {"input_name": input_name, "input_sha256": input_address}
        if output_bytes is None:
            outcome, basis = "INCONCLUSIVE", "verifier_execution_failed"
            if observed is not None:
                check["observed_sha256"] = observed
        else:
            expected = execution["output_sha256"]
            check["expected_sha256"] = expected
            if observed is None:
                outcome, basis = ("INCONCLUSIVE",
                                  "missing_producer_evidence")
            else:
                check["observed_sha256"] = observed
                if observed == expected:
                    outcome, basis = "PASS", None
                else:
                    outcome, basis = "FAIL", "transform_output_mismatch"
        binding = {"sequence_id": state["sequence_id"],
                   "attempt_id": state["attempt_id"],
                   "fence": state["fence"],
                   "attempt_contract_hash":
                       state["attempt_contract_hash"],
                   "record_hash": state["record_hash"],
                   "candidate_receipt_sha256":
                       state["candidate_receipt_sha256"],
                   "environment_sha256": env_address}
        payload = verification_receipts.build_transform_result_payload(
            binding, transform["role"], transform["transform_id"],
            registration, transform["object_sha256"], outcome, execution,
            check, conclusion_basis=basis)
        return outcome, verification_receipts.receipt_bytes(payload)

    # --------------------------------------------------------- execution

    def _execute(self, closure, registration, assertion, context):
        """Materialize the exact closure and the frozen subject into a
        fresh run directory, execute, and return (execution facts, stdout
        bytes). The subject's artifact bytes come from the content store
        by their admitted addresses — never from any workspace — so a
        verifier can never silently evaluate a newer filesystem version."""
        entrypoint = (closure["package"]["entrypoint"]
                      if closure["kind"] == "packaged"
                      else registration["entrypoint"])
        run_dir = tempfile.mkdtemp(prefix="autobot-verify-",
                                   dir=self.workdir)
        try:
            argv = transforms.materialize_closure(self.store, closure,
                                                  run_dir, entrypoint)
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
