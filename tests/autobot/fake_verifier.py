"""The deterministic fake verifier runner (M4's conformance driver).

Implements the Chapter 2 typed ``VerifierRunner`` contract with scripted
outcomes — the Chapter 4 runner executes real registered closures; this fake
produces byte-exact obj-18/obj-19/obj-20 payloads so the Sequencer's
admission seams and transitions are exercised deterministically. Not a test
module; the verification suite drives it.
"""

import json

from autobot.verification import VerifierRunner


def environment_bytes(attempt_identity, implementation="cpython",
                      version="3.12.4"):
    """A minimal obj-18 ActualVerificationEnvironment payload bound to the
    given attempt identity (the fields the runtime binds plus the schema's
    required shape)."""
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
        "env_vars": {},
        "tools": {},
    }
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def receipt_bytes(assertion_id, verifier_id, outcome, attempt_id):
    """The runner's obj-19 (PASS) / obj-20 (FAIL) payload bytes."""
    kind = ("VerificationReceipt" if outcome == "PASS"
            else "VerificationFailureReceipt")
    payload = {"schema_version": "v1", "kind": kind,
               "assertion_id": assertion_id, "verifier_id": verifier_id,
               "outcome": outcome, "attempt_id": attempt_id}
    return json.dumps(payload, sort_keys=True).encode("utf-8")


class FakeVerifierRunner(VerifierRunner):
    def __init__(self, outcomes=None):
        self.outcomes = dict(outcomes or {})  # assertion_id -> outcome

    def capture_environment(self, attempt_identity):
        return environment_bytes(attempt_identity)

    def run_assertion(self, assertion, registration, context):
        outcome = self.outcomes.get(assertion["assertion_id"], "PASS")
        return outcome, receipt_bytes(
            assertion["assertion_id"], assertion.get("verifier_id"),
            outcome, context["attempt_id"])
