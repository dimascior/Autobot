"""The deterministic fake AutoSwarm transport (M3's first acceptance gate).

Honors the full ``Transport`` contract with scripted, reproducible behavior
and explicit injection controls:

* idempotent launch — one session per ``call_id``, however many launches;
* ``hidden`` — sessions that exist but are unobservable to
  ``find_session`` (eventual consistency / partition injection), proving
  the UNCERTAIN-outcome rule;
* ``launch_errors`` — launches that raise at the transport boundary
  WITHOUT creating a session;
* ``failures`` — positive launch-failure determinations (the only path
  that may justify t-a10);
* ``scripted_collect`` — witness files (exact producer originals) and the
  role outcome per ``call_id``.

Not a test module; the acceptance scenario drives it. The bounded
real-provider test (after this gate) implements the same contract over the
actual AutoSwarm CLI per docs/architecture/chapter2/TRANSPORT_GUARANTEES.md.
"""

import json

from autobot.transport import Transport


class FakeTransport(Transport):
    def __init__(self):
        self.sessions = {}          # call_id -> {"native_session_id", "record"}
        self.launch_log = []        # every launch() invocation, in order
        self.hidden = set()         # call_ids find_session cannot observe
        self.launch_errors = {}     # call_id -> message: launch raises, no session
        self.failures = {}          # call_id -> positive failure reason
        self.scripted_collect = {}  # call_id -> {"witness_files", "outcome"}
        self._counter = 0

    def launch(self, projection_address, provider, role, call_id):
        self.launch_log.append(
            {"call_id": call_id, "provider": provider, "role": role,
             "projection_address": projection_address})
        if call_id in self.launch_errors:
            raise RuntimeError(self.launch_errors[call_id])
        if call_id not in self.sessions:
            self._counter += 1
            native = "fake-sess-%04d" % self._counter
            self.sessions[call_id] = {
                "native_session_id": native,
                "record": {"schema_version": "v1",
                           "kind": "FakeSessionRecord",
                           "call_id": call_id,
                           "native_session_id": native,
                           "provider": provider, "role": role,
                           "projection_address": projection_address}}
        return self.sessions[call_id]["native_session_id"]

    def find_session(self, call_id):
        if call_id in self.hidden or call_id not in self.sessions:
            return None
        session = self.sessions[call_id]
        return {"native_session_id": session["native_session_id"],
                "evidence": json.dumps(
                    session["record"], sort_keys=True).encode("utf-8")}

    def positive_launch_failure(self, call_id):
        return self.failures.get(call_id)

    def collect(self, call_id):
        return self.scripted_collect[call_id]
