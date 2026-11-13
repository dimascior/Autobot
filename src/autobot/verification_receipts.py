"""The registered Chapter 4 verification proof-object semantics: obj-16
CandidateReceipt, obj-19 VerificationReceipt, obj-20
VerificationFailureReceipt (C4-M1).

objects.json registers obj-16 with minter ``git_adapter`` (cap-32,
task_force FORBIDDEN) and obj-19/obj-20 with minter ``verifier_runner``
(cap-35, task_force FORBIDDEN); all three are immutable, attempt-bound
receipts. This module holds the frozen payload shapes (the mirrored
Chapter 4 schemas), the canonical-bytes rule (a receipt's identity is the
content address of its canonical bytes, exactly as obj-15), and the PURE
payload builders both sides of each authority boundary share:

- the git_adapter builds obj-16 and the freeze admission re-validates
  every ledger-derivable binding (``Sequencer.freeze_candidate``);
- the verifier_runner builds obj-19/obj-20 and the result admission
  re-validates every binding against the durable ledger
  (``Sequencer.register_verifier_result``) — the INV-17
  candidate/environment/assertion/registration/Attempt binding, each
  member an equality against a fact the ledger already holds, so a forged
  or replayed receipt dies at the seam (CH4-verification-binding).

INV-06 is structural here: nothing in this module (or its consumers)
translates prose — a transcript, an agent claim, a model's "tests
passed" — into a receipt. obj-19 exists only over a typed conclusion from
an executed registered closure (verifier_runner.py), and obj-20 carries
the registered conclusion_basis vocabulary so authority uncertainty is
never dressed up as an ordinary FAIL (CH4-llm-output-never-pass).
"""

import json
from pathlib import Path

from contract_library import canonicalize
from contract_library.schema_profile import check_profile
from contract_library.schema_profile import validate as schema_validate

RESULT_PRODUCER = "verifier_runner"

# outcome -> the registered receipt kind its bytes must carry. INCONCLUSIVE
# rides obj-20 (it is a failure-plane observation) but satisfies nothing:
# it routes to t-s12's unknown arm, never to VERIFIED or REJECTED.
RESULT_KINDS = {
    "PASS": "VerificationReceipt",
    "FAIL": "VerificationFailureReceipt",
    "INCONCLUSIVE": "VerificationFailureReceipt",
}

# The registered obj-20 conclusion_basis vocabulary per outcome (section 12
# of the C4-M1 directive): only a genuine assertion FAIL follows the
# repair path; execution failures and malformed results are authority
# uncertainty and never a FAIL conclusion.
CONCLUSION_BASES = {
    "FAIL": ("verifier_reported_fail",),
    "INCONCLUSIVE": ("verifier_execution_failed",
                     "malformed_verifier_result"),
}

_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas_chapter4"
_SCHEMA_CACHE = {}


def _schema(name):
    if name not in _SCHEMA_CACHE:
        with open(str(_SCHEMAS_DIR / name), "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        check_profile(schema)
        _SCHEMA_CACHE[name] = schema
    return _SCHEMA_CACHE[name]


def candidate_schema():
    """The mirrored obj-16 schema, profile-checked once (D-43)."""
    return _schema("candidate_receipt.schema.json")


def verification_receipt_schema():
    """The mirrored obj-19 schema."""
    return _schema("verification_receipt.schema.json")


def failure_receipt_schema():
    """The mirrored obj-20 schema."""
    return _schema("verification_failure_receipt.schema.json")


def validate_candidate(payload):
    """Issues against the mirrored obj-16 schema (empty when valid)."""
    return schema_validate(payload, candidate_schema())


def validate_result_receipt(payload):
    """Issues against the mirrored obj-19/obj-20 schema chosen by the
    payload's kind; a payload of neither kind is one issue."""
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind == "VerificationReceipt":
        return schema_validate(payload, verification_receipt_schema())
    if kind == "VerificationFailureReceipt":
        return schema_validate(payload, failure_receipt_schema())
    return ["payload kind %r is not a registered verification receipt"
            % (kind,)]


def receipt_bytes(payload):
    """The canonical bytes whose content address is the receipt's
    identity (the same identity rule as obj-15)."""
    return canonicalize(payload)


# The registered verifier result protocol (fixed by the chapter-4
# documentation): the verifier process exits 0 when it reached a typed
# conclusion, and its LAST stdout line is JSON carrying this key.
RESULT_KEY = "autobot_verifier_result"
CONCLUSIONS = ("PASS", "FAIL")


def parse_conclusion(stdout_bytes):
    """The typed conclusion from a verifier's exact output bytes, or None
    when the output is not the registered protocol."""
    try:
        text = bytes(stdout_bytes).decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        parsed = json.loads(lines[-1])
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    result = parsed.get(RESULT_KEY)
    if not isinstance(result, dict):
        return None
    conclusion = result.get("conclusion")
    return conclusion if conclusion in CONCLUSIONS else None


def outcome_consistency(payload, output_bytes):
    """F-C4-1: the receipt's CLAIMED outcome must agree with its durable
    execution evidence — the exact output bytes at execution.output_sha256
    under the registered result protocol, and the exit code. A conclusion
    exists only at exit 0; authority uncertainty is never spelled PASS or
    FAIL. Returns None when consistent, else the refusal text. Shared by
    the admission seam and recovery, so a verifier-shaped receipt whose
    outcome its own evidence does not support dies at the boundary."""
    execution = payload.get("execution", {})
    exit_code = execution.get("exit_code")
    conclusion = parse_conclusion(output_bytes) if exit_code == 0 else None
    outcome = payload.get("outcome")
    if outcome == "PASS":
        if conclusion != "PASS":
            return ("claimed PASS is not supported by the execution"
                    " evidence (exit %r, conclusion %r)"
                    % (exit_code, conclusion))
        return None
    if outcome == "FAIL":
        if conclusion != "FAIL":
            return ("claimed FAIL is not supported by the execution"
                    " evidence (exit %r, conclusion %r): authority"
                    " uncertainty is never an ordinary FAIL"
                    % (exit_code, conclusion))
        return None
    basis = payload.get("conclusion_basis")
    if basis == "verifier_execution_failed":
        if exit_code == 0:
            return ("conclusion_basis verifier_execution_failed requires a"
                    " nonzero exit; the evidence shows exit 0")
        return None
    if basis == "malformed_verifier_result":
        if exit_code != 0 or conclusion is not None:
            return ("conclusion_basis malformed_verifier_result requires"
                    " exit 0 with no typed conclusion (exit %r,"
                    " conclusion %r)" % (exit_code, conclusion))
        return None
    return "unrecognized conclusion_basis %r" % (basis,)


def assertion_part_ref(record_hash, assertion_id):
    """The D-47 typed part reference naming one frozen assertion: the
    immutable record hash pins the bytes, the selector names the part —
    never a mutable list position."""
    return {"record": record_hash, "part_kind": "ASSERTION",
            "part_id": assertion_id}


def build_result_payload(binding, assertion_id, verifier_id,
                         registration, registration_hash, outcome,
                         execution, conclusion_basis=None):
    """The canonical obj-19/obj-20 payload for one DETERMINISTIC
    assertion's result: a pure function of the binding facts, the
    registration identity, the outcome and the execution provenance.

    ``binding`` carries the ledger facts the admission re-validates:
    sequence_id, attempt_id, fence, attempt_contract_hash, record_hash,
    candidate_receipt_sha256, environment_sha256."""
    kind = RESULT_KINDS.get(outcome)
    if kind is None:
        raise ValueError("outcome %r has no registered receipt kind"
                         % (outcome,))
    payload = {
        "schema_version": "v1",
        "kind": kind,
        "minter": RESULT_PRODUCER,
        "attempt": {"sequence_id": binding["sequence_id"],
                    "attempt_id": binding["attempt_id"],
                    "fence": binding["fence"]},
        "attempt_contract_hash": binding["attempt_contract_hash"],
        "record_hash": binding["record_hash"],
        "candidate_receipt_sha256": binding["candidate_receipt_sha256"],
        "environment_sha256": binding["environment_sha256"],
        "assertion": {
            "part": assertion_part_ref(binding["record_hash"],
                                       assertion_id),
            "verifier_id": verifier_id,
            "mode": "DETERMINISTIC",
        },
        "registration": {
            "object_sha256": registration_hash,
            "registration_id": registration["registration_id"],
            "artifact_root": registration["artifact_root"],
        },
        "result_source": "BY_VERIFIER",
        "outcome": outcome,
        "execution": dict(execution),
    }
    if kind == "VerificationFailureReceipt":
        allowed = CONCLUSION_BASES[outcome]
        if conclusion_basis not in allowed:
            raise ValueError(
                "conclusion_basis %r is not registered for outcome %s"
                " (allowed: %s)" % (conclusion_basis, outcome,
                                    ", ".join(allowed)))
        payload["conclusion_basis"] = conclusion_basis
    elif conclusion_basis is not None:
        raise ValueError("obj-19 carries no conclusion_basis")
    return payload
