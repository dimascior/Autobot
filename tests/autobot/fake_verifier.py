"""The deterministic fake verifier runner (the typed-interface
conformance driver, upgraded for C4-M1).

The production runner (autobot.verifier_runner) executes real registered
closures; this fake produces byte-exact, FULLY BOUND canonical obj-18/
obj-19/obj-20 payloads with scripted outcomes and no execution, so the
Sequencer's admission seams and transitions are exercised deterministically
— including the strengthened C4-M1 binding admission, which refuses any
receipt that does not bind the attempt's admitted candidate, environment,
assertion part and exact bound registration. Not a test module; the
verification suite drives it.
"""

import json
import platform

from contract_library import canonicalize, content_address

from autobot import verification_receipts
from autobot.verification import VerifierRunner


def environment_bytes(attempt_identity, implementation=None,
                      version=None, env_vars=None, tools=None):
    """A canonical obj-18 ActualVerificationEnvironment payload bound to
    the given attempt identity (the frozen Chapter 1 schema's required
    shape). The interpreter defaults are the LIVE interpreter's identity
    since C4-M2 — the live-bound records' a1 constraint names it — and a
    test that wants V-A's frozen 3.12.4 constraint (or a mismatch) passes
    explicit values."""
    if implementation is None:
        implementation = platform.python_implementation().lower()
    if version is None:
        version = platform.python_version()
    payload = {
        "schema_version": "v1",
        "kind": "ActualVerificationEnvironment",
        "environment_id": "ve_" + "0" * 32,
        "attempt": dict(attempt_identity),
        "probed_utc": "2026-11-09T12:00:00.000Z",
        "interpreter": {"implementation": implementation, "version": version,
                        "executable_sha256": "sha256:" + "0" * 64},
        "platform": {"os": "windows", "os_version": "10.0.19045",
                     "architecture": "x86_64"},
        "env_vars": dict(env_vars or {}),
        "tools": dict(tools or {}),
    }
    return canonicalize(payload)


def fake_execution(sequencer, registration, outcome):
    """Scripted execution provenance for a receipt minted without any
    execution — with DURABLE, outcome-consistent output evidence, because
    the admission seam verifies the claimed outcome against the exact
    output bytes under the registered result protocol (F-C4-1)."""
    if outcome == "PASS":
        output = (json.dumps({"autobot_verifier_result": {
            "conclusion": "PASS", "detail": "scripted"}}) + "\n"
        ).encode("utf-8")
        exit_code = 0
    elif outcome == "FAIL":
        output = (json.dumps({"autobot_verifier_result": {
            "conclusion": "FAIL", "detail": "scripted"}}) + "\n"
        ).encode("utf-8")
        exit_code = 0
    else:  # INCONCLUSIVE: a crash before any typed conclusion
        output = b"scripted crash before any conclusion\n"
        exit_code = 3
    address = sequencer.store.put(output)
    return {"closure_kind": registration["closure"]["kind"],
            "entrypoint": registration["entrypoint"],
            "exit_code": exit_code,
            "output_sha256": address}


def bound_receipt_bytes(sequencer, attempt_id, assertion_id, outcome,
                        environment, conclusion_basis=None, **overrides):
    """Canonical obj-19/obj-20 bytes that BIND the attempt's durable facts
    (read through the Sequencer's verification_state surface) — the exact
    bytes the strengthened admission accepts. ``environment`` is the obj-18
    bytes the caller will also hand the admission seam. ``overrides``
    replace top-level payload members AFTER the bound build, for the
    binding-attack negatives (the result is re-canonicalized)."""
    state = sequencer.verification_state(attempt_id)
    entry = None
    for candidate in state["assertions"]:
        if candidate.get("assertion_id") == assertion_id:
            entry = candidate
            break
    if entry is None:
        raise AssertionError("assertion %r is not declared" % assertion_id)
    bound = state["registrations"][entry.get("verifier_id")]
    if conclusion_basis is None and outcome in \
            verification_receipts.CONCLUSION_BASES:
        conclusion_basis = verification_receipts.CONCLUSION_BASES[
            outcome][0]
    binding = {"sequence_id": state["sequence_id"],
               "attempt_id": state["attempt_id"],
               "fence": state["fence"],
               "attempt_contract_hash": state["attempt_contract_hash"],
               "record_hash": state["record_hash"],
               "candidate_receipt_sha256":
                   state["candidate_receipt_sha256"],
               "environment_sha256": content_address(bytes(environment))}
    payload = verification_receipts.build_result_payload(
        binding, assertion_id, entry.get("verifier_id"), bound["payload"],
        bound["object_sha256"], outcome,
        fake_execution(sequencer, bound["payload"], outcome),
        conclusion_basis=conclusion_basis)
    payload.update(overrides)
    return verification_receipts.receipt_bytes(payload)


def transform_receipt_bytes(sequencer, attempt_id, environment,
                            outcome="PASS", conclusion_basis=None,
                            observed=None, **overrides):
    """Canonical ROLE_TRANSFORM obj-19/obj-20 bytes binding the attempt's
    durable facts (C4-M2, INV-49), scripted like ``bound_receipt_bytes``.
    Built around the live identity transform's semantics: the expected
    output identity IS the declared input's address (so the admission's
    re-execution of the real closure re-derives exactly it, and the output
    evidence resolves — the input bytes are in the store at that same
    address). ``observed`` overrides the claimed observed identity for the
    mismatch/forgery scenarios; ``overrides`` forge top-level members for
    the binding negatives."""
    state = sequencer.verification_state(attempt_id)
    transform = state["role_transform"]
    registration = transform["payload"]
    inputs = state["record_payload"]["specification_inputs"][
        "name_to_exact_byte_sha256"]
    input_name = sorted(inputs)[0]
    input_address = inputs[input_name]
    check = {"input_name": input_name, "input_sha256": input_address}
    execution = {"closure_kind": registration["closure"]["kind"],
                 "entrypoint": registration["entrypoint"],
                 "exit_code": 0, "output_sha256": input_address}
    if outcome == "PASS":
        check["expected_sha256"] = input_address
        check["observed_sha256"] = observed or input_address
    elif outcome == "FAIL":
        conclusion_basis = conclusion_basis or "transform_output_mismatch"
        check["expected_sha256"] = input_address
        check["observed_sha256"] = observed or ("sha256:" + "f" * 64)
    else:  # INCONCLUSIVE
        if conclusion_basis is None:
            conclusion_basis = "missing_producer_evidence"
        if conclusion_basis == "missing_producer_evidence":
            check["expected_sha256"] = input_address
        else:  # verifier_execution_failed: no completed transform
            diagnostics = sequencer.store.put(
                b"scripted transform crash before any output\n")
            execution["exit_code"] = 3
            execution["output_sha256"] = diagnostics
            if observed is not None:
                check["observed_sha256"] = observed
    binding = {"sequence_id": state["sequence_id"],
               "attempt_id": state["attempt_id"],
               "fence": state["fence"],
               "attempt_contract_hash": state["attempt_contract_hash"],
               "record_hash": state["record_hash"],
               "candidate_receipt_sha256":
                   state["candidate_receipt_sha256"],
               "environment_sha256": content_address(bytes(environment))}
    payload = verification_receipts.build_transform_result_payload(
        binding, transform["role"], transform["transform_id"],
        registration, transform["object_sha256"], outcome, execution,
        check, conclusion_basis=conclusion_basis)
    payload.update(overrides)
    return verification_receipts.receipt_bytes(payload)


def attested_receipt_bytes(sequencer, attempt_id, assertion_id,
                           attestation_sha256, **overrides):
    """Canonical obj-19 BY_ATTESTATION bytes binding the attempt's durable
    facts and the admitted obj-37's address (C4-M2, D-52/D-53)."""
    state = sequencer.verification_state(attempt_id)
    binding = {"sequence_id": state["sequence_id"],
               "attempt_id": state["attempt_id"],
               "fence": state["fence"],
               "attempt_contract_hash": state["attempt_contract_hash"],
               "record_hash": state["record_hash"],
               "candidate_receipt_sha256":
                   state["candidate_receipt_sha256"]}
    payload = verification_receipts.build_attested_result_payload(
        binding, assertion_id, attestation_sha256)
    payload.update(overrides)
    return verification_receipts.receipt_bytes(payload)


class FakeVerifierRunner(VerifierRunner):
    """Scripted outcomes over REAL bindings: the typed M4 contract,
    admission-valid under C4-M1, with no execution. The default outcome is
    PASS; ``outcomes`` maps assertion_id to PASS/FAIL/INCONCLUSIVE."""

    def __init__(self, sequencer=None, outcomes=None,
                 environment_overrides=None):
        self.sequencer = sequencer
        self.outcomes = dict(outcomes or {})
        self.environment_overrides = dict(environment_overrides or {})

    def capture_environment(self, attempt_identity, constraints=()):
        return environment_bytes(attempt_identity,
                                 **self.environment_overrides)

    def run_assertion(self, assertion, registration, context):
        outcome = self.outcomes.get(assertion["assertion_id"], "PASS")
        receipt = bound_receipt_bytes(
            self.sequencer, context["attempt_id"],
            assertion["assertion_id"], outcome, context["environment"])
        return outcome, receipt

    def verify_attempt(self, attempt_id):
        """The production runner's high-level contract, scripted: one
        durable obj-18 capture, then one bound result per DETERMINISTIC
        assertion without an existing row."""
        state = self.sequencer.verification_state(attempt_id)
        identity = {"sequence_id": state["sequence_id"],
                    "attempt_id": state["attempt_id"],
                    "version": state["version"],
                    "fence": state["fence"]}
        if "environment_sha256" in state:
            env = self.sequencer.store.get(state["environment_sha256"])
        else:
            env = self.capture_environment(identity)
            self.sequencer.admit_environment_capture(attempt_id, env)
        results = []
        inconclusive = False
        # C4-M2: the scripted role-transform check (the admission still
        # re-executes the real bound closure) and the incorporation of
        # admitted attestations — mirroring the production contract.
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
                outcome = self.outcomes.get(row_key, "PASS")
                receipt = transform_receipt_bytes(
                    self.sequencer, attempt_id, env, outcome=outcome)
                admitted = self.sequencer.register_transform_check(
                    attempt_id, transform["role"], outcome,
                    "verifier_runner", receipt, environment=env)
                if outcome == "INCONCLUSIVE":
                    inconclusive = True
                results.append({"assertion_id": row_key,
                                "outcome": outcome,
                                "verification_id":
                                    admitted["verification_id"]})
        for entry in state["assertions"]:
            if entry.get("mode") != "HUMAN_ATTESTED":
                continue
            address = state.get("attestations", {}).get(
                entry["assertion_id"])
            if address is None:
                continue
            existing = self.sequencer.db.conn.execute(
                "SELECT outcome FROM attempt_verifications"
                " WHERE attempt_id = ? AND assertion_id = ?",
                (attempt_id, entry["assertion_id"])).fetchone()
            if existing is not None:
                continue
            receipt = attested_receipt_bytes(
                self.sequencer, attempt_id, entry["assertion_id"], address)
            admitted = self.sequencer.register_attested_result(
                attempt_id, entry["assertion_id"], "verifier_runner",
                receipt)
            results.append({"assertion_id": entry["assertion_id"],
                            "outcome": "ATTESTED",
                            "verification_id":
                                admitted["verification_id"]})
        for entry in state["assertions"]:
            if entry.get("mode") != "DETERMINISTIC":
                continue
            existing = self.sequencer.db.conn.execute(
                "SELECT outcome FROM attempt_verifications"
                " WHERE attempt_id = ? AND assertion_id = ?",
                (attempt_id, entry["assertion_id"])).fetchone()
            if existing is not None:
                if existing["outcome"] == "INCONCLUSIVE":
                    inconclusive = True
                continue
            outcome, receipt = self.run_assertion(
                entry, state["registrations"][entry.get(
                    "verifier_id")]["payload"],
                {"attempt_id": attempt_id, "environment": env})
            admitted = self.sequencer.register_verifier_result(
                attempt_id, entry["assertion_id"], outcome,
                "verifier_runner", receipt, environment=env)
            if outcome == "INCONCLUSIVE":
                inconclusive = True
            results.append({"assertion_id": entry["assertion_id"],
                            "outcome": outcome,
                            "verification_id":
                                admitted["verification_id"]})
        return {"results": results, "inconclusive": inconclusive}
