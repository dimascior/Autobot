"""The Chapter 2 verification surface (M4): the typed verifier-runner
interface and the pure evaluation helpers the Sequencer's verification
transitions consume.

Ownership boundaries (the M4 directive's): Chapter 2 owns the runtime
transitions (t-s07, t-s09..t-s12, t-s45, t-a07/t-a08/t-a12) and THIS typed
interface; Chapter 4 owns the complete verifier-runner implementation and
its receipt schemas; Chapter 3 supplies receipt-admission authority where
its transitions require it. The helpers here are pure functions over frozen
contract parts and admitted evidence — they grant nothing and mutate
nothing.

- SEM-16 (D-57): a registration declaring any ``service:*`` execution
  requirement is nondeterministic and may never satisfy a DETERMINISTIC
  assertion. The freeze already refuses such a binding (validate_freeze);
  ``verifier_eligibility`` re-evaluates it at verification time as
  defense in depth, from the registered requirements themselves.
- INV-53: an unavailable bound artifact HALTs and is never substituted —
  the Sequencer raises IntegrityHalt when a bound registration or its
  bytes cannot be resolved, and t-s12 is the sequence-level HALT.
- D-52: eligibility is derived (HUMAN_REQUIRED iff any assertion's mode is
  HUMAN_ATTESTED, SEM-13); a HUMAN_ATTESTED assertion passes only through
  your ManualAttestation.ASSERTION with durable identity, provenance and
  scope binding (``validate_assertion_attestation``).
- Environment binding (obj-17 -> obj-18): a declared constraint is matched
  against the probe's captured facts; an unsupported constraint section is
  a MISMATCH (fail closed), never ignored.
"""

import json
from pathlib import Path

from contract_library.schema_profile import check_profile
from contract_library.schema_profile import validate as schema_validate

from .models import ConflictError

# The verifier_runner's registered result vocabulary (obj-19 on PASS,
# obj-20 on FAIL; INCONCLUSIVE carries its receipt but satisfies nothing —
# it routes to t-s12's "unknown" arm, never to VERIFIED or REJECTED).
VERIFIER_OUTCOMES = ("PASS", "FAIL", "INCONCLUSIVE")

# The frozen Chapter 1 obj-18 schema, used without weakening (D-56).
_ENVIRONMENT_SCHEMA_PATH = (Path(__file__).resolve().parent / "schemas"
                            / "actual_verification_environment.schema.json")
_ENVIRONMENT_SCHEMA_CACHE = []


def environment_schema():
    """The frozen obj-18 schema, profile-checked once (D-43)."""
    if not _ENVIRONMENT_SCHEMA_CACHE:
        with open(str(_ENVIRONMENT_SCHEMA_PATH), "r", encoding="utf-8") as fh:
            schema = json.load(fh)
        check_profile(schema)
        _ENVIRONMENT_SCHEMA_CACHE.append(schema)
    return _ENVIRONMENT_SCHEMA_CACHE[0]

# Registered minters (objects.json): obj-19/obj-20 by verifier_runner,
# obj-18 by environment_probe, obj-37 by you.
RESULT_PRODUCER = "verifier_runner"
ENVIRONMENT_PRODUCER = "environment_probe"
ATTESTATION_PRODUCER = "you"


class VerifierRunner:
    """The typed contract a Chapter 4 verifier runner must honor.

    The runner executes one registered verifier closure (INV-66: the
    registered artifacts only) against one assertion and returns the
    outcome with the exact receipt bytes it minted (obj-19/obj-20). The
    Sequencer never executes anything: it admits the runner's results
    through ``register_verifier_result`` and applies the registered
    transitions. The deterministic fake in the tests implements this same
    contract."""

    producer_id = RESULT_PRODUCER

    def capture_environment(self, attempt_identity):
        """Return the obj-18 ActualVerificationEnvironment payload BYTES
        for this attempt (minted by the environment probe): the exact
        bytes the admission stores and the constraint match reads."""
        raise NotImplementedError

    def run_assertion(self, assertion, registration, context):
        """Execute the registered closure for one DETERMINISTIC assertion;
        returns ``(outcome, receipt_bytes)`` with outcome in
        VERIFIER_OUTCOMES and receipt_bytes the runner's exact obj-19/
        obj-20 payload bytes."""
        raise NotImplementedError


def verifier_eligibility(assertion, registration):
    """SEM-16/D-57, evaluated from the governing registered requirements.

    Returns None when the registration may satisfy the assertion's mode;
    otherwise the refusal text. A DETERMINISTIC assertion may only be
    satisfied by a registration with no ``service:*`` execution
    requirement; HUMAN_ATTESTED assertions are not executed at all (they
    pass only by attestation), so no registration is eligible to SATISFY
    one mechanically."""
    requirements = registration.get("execution_requirements", [])
    if not isinstance(requirements, list):
        return "registration execution_requirements is not a list"
    services = [r for r in requirements
                if isinstance(r, str) and r.startswith("service:")]
    if assertion.get("mode") == "DETERMINISTIC" and services:
        return ("SEM-16 (D-57): registration %r declares %s and is"
                " nondeterministic — ineligible for DETERMINISTIC"
                " assertion %r"
                % (registration.get("registration_id"),
                   ", ".join(sorted(services)),
                   assertion.get("assertion_id")))
    if assertion.get("mode") == "HUMAN_ATTESTED":
        return ("HUMAN_ATTESTED assertion %r is satisfied only by your"
                " ManualAttestation.ASSERTION (D-52), never by a verifier"
                " execution" % assertion.get("assertion_id"))
    return None


def _membership(section, field, captured_value, allowed):
    """One D-56 list rule: the captured value must be among the declared
    exact values. A list that is not a list fails closed."""
    if not isinstance(allowed, list) or not allowed:
        return ("%s constraint %r is not a non-empty list: failing closed"
                % (section, field))
    if captured_value not in allowed:
        return ("%s %s %r is not among the declared %r"
                % (section, field, captured_value, allowed))
    return None


def environment_satisfies(constraint, actual):
    """obj-17 -> obj-18 binding, the COMPLETE D-56 vocabulary (C4-M1):

    - ``interpreter``: implementation list, exact version list;
    - ``platform``: os list, architecture list;
    - ``tools``: named tool presence, each with an optional exact version
      list — a tool the capture does not carry is a mismatch;
    - ``env_vars``: named variable exact values — a variable the capture
      does not carry is a mismatch.

    Every declared section and every rule key inside it must be understood;
    an unsupported section or rule key fails CLOSED (never substituted or
    ignored). A mismatch is a verification-environment authority problem —
    the registered t-s12 env_mismatch HALT arm — never an ordinary failed
    assertion. Returns None on satisfaction, else the mismatch text."""
    if not isinstance(constraint, dict):
        return "environment constraint is not a mapping"
    if not isinstance(actual, dict):
        return "captured environment is not a mapping"
    for section in sorted(constraint):
        rule = constraint[section]
        if not isinstance(rule, dict):
            return ("environment constraint section %r is not a mapping:"
                    " failing closed" % section)
        if section == "interpreter":
            captured = actual.get("interpreter", {})
            for key in sorted(rule):
                if key == "implementations":
                    refusal = _membership("interpreter", "implementation",
                                          captured.get("implementation"),
                                          rule[key])
                elif key == "versions":
                    refusal = _membership("interpreter", "version",
                                          captured.get("version"), rule[key])
                else:
                    return ("interpreter constraint key %r is not supported"
                            " by this runtime: failing closed" % key)
                if refusal is not None:
                    return refusal
        elif section == "platform":
            captured = actual.get("platform", {})
            for key in sorted(rule):
                if key == "os":
                    refusal = _membership("platform", "os",
                                          captured.get("os"), rule[key])
                elif key == "architectures":
                    refusal = _membership("platform", "architecture",
                                          captured.get("architecture"),
                                          rule[key])
                else:
                    return ("platform constraint key %r is not supported by"
                            " this runtime: failing closed" % key)
                if refusal is not None:
                    return refusal
        elif section == "tools":
            captured = actual.get("tools", {})
            for name in sorted(rule):
                tool_rule = rule[name]
                if not isinstance(tool_rule, dict):
                    return ("tool constraint %r is not a mapping: failing"
                            " closed" % name)
                tool = captured.get(name)
                if not isinstance(tool, dict):
                    return ("required tool %r was not captured: presence"
                            " cannot be established" % name)
                for key in sorted(tool_rule):
                    if key != "versions":
                        return ("tool constraint key %r is not supported by"
                                " this runtime: failing closed" % key)
                    refusal = _membership("tool %s" % name, "version",
                                          tool.get("version"),
                                          tool_rule[key])
                    if refusal is not None:
                        return refusal
        elif section == "env_vars":
            captured = actual.get("env_vars", {})
            for name in sorted(rule):
                expected = rule[name]
                if not isinstance(expected, str):
                    return ("env var constraint %r is not an exact string"
                            " value: failing closed" % name)
                if name not in captured:
                    return ("required env var %r was not captured" % name)
                if captured[name] != expected:
                    return ("env var %s value does not equal the declared"
                            " exact value" % name)
        else:
            return ("environment constraint section %r is not supported by"
                    " this runtime: failing closed (INV-53 discipline, never"
                    " substituted or ignored)" % section)
    return None


def validate_environment_capture(payload, att):
    """The full obj-18 admission check (C4-M1): the payload must validate
    against the FROZEN Chapter 1 schema (used without weakening, D-56) and
    its attempt identity must bind THIS attempt. Returns None when valid,
    else the refusal text."""
    if not isinstance(payload, dict):
        return "not a v1 ActualVerificationEnvironment (obj-18)"
    issues = schema_validate(payload, environment_schema())
    if issues:
        return ("obj-18 failed the frozen Chapter 1 schema: %s"
                % "; ".join(repr(issue) for issue in issues))
    identity = payload.get("attempt", {})
    if (identity.get("attempt_id") != att["attempt_id"]
            or identity.get("sequence_id") != att["sequence_id"]
            or identity.get("fence") != att["fence"]):
        return ("obj-18 attempt identity %r does not bind this attempt"
                " (scope binding)" % (identity,))
    return None


def validate_assertion_attestation(attestation, att, assertion_id):
    """D-52: the authorized HUMAN_REQUIRED attestation path. The obj-37
    ManualAttestation (kind ASSERTION) must carry durable identity (minted
    by you), provenance (the attested assertion and verdict) and scope
    binding (the exact attempt and its contract hash). Returns None when
    valid, else the refusal text. The D-29 kind-grammar finding recorded on
    t-i07 applies here identically: the dotted registry kind cannot be
    object-hashed under Chapter 1 as frozen, so the attestation is
    preserved by exact bytes (D-31) and referenced from the journal."""
    if not isinstance(attestation, dict):
        return "attestation is not a mapping"
    if attestation.get("kind") != "ManualAttestation.ASSERTION":
        return "not a ManualAttestation.ASSERTION (obj-37, D-52)"
    if attestation.get("schema_version") != "v1":
        return "attestation schema_version must be v1"
    if attestation.get("attested_by") != ATTESTATION_PRODUCER:
        return "obj-37 is minted by you alone (D-52 identity)"
    if attestation.get("assertion_id") != assertion_id:
        return ("attestation binds assertion %r, not %r (D-52 scope)"
                % (attestation.get("assertion_id"), assertion_id))
    if attestation.get("attempt_id") != att["attempt_id"]:
        return ("attestation binds attempt %r, not %s (D-52 scope)"
                % (attestation.get("attempt_id"), att["attempt_id"]))
    if attestation.get("attempt_contract_hash") != att["attempt_contract_hash"]:
        return ("attestation attempt_contract_hash does not match the"
                " durable contract (D-52 scope binding)")
    if attestation.get("attests") != "PASS":
        return ("attestation attests %r; only PASS is an attestable verdict"
                " (a failing assertion is a FAIL receipt, not an"
                " attestation)" % attestation.get("attests"))
    return None
