"""The AutoSwarm transport CONTRACT and the launch/restart reconciliation
protocol (M3).

The transport itself is integration-layer code (D-63: it consumes Autobot
APIs and never writes authoritative tables). This module owns two things the
protocol requires to be stated once, executably:

1. ``Transport`` — the contract any implementation (the deterministic fake,
   then the real AutoSwarm adapter) must honor:

   * **Durable dispatch correlation**: the Sequencer mints the ``call_id``
     and commits the ``attempt_calls`` row (t-a02) BEFORE ``launch`` is
     called — the correlation exists durably before any external process.
   * **Idempotent launch**: ``call_id`` is the provider-side idempotency
     key. A second ``launch`` with the same ``call_id`` returns the
     existing session and NEVER creates a duplicate execution.
   * **Honest observation**: ``find_session(call_id)`` returning ``None``
     is NOT positive proof the launch failed — the session may exist and
     be unobservable (provider delay, partition). Failure is established
     only by ``positive_launch_failure``: a provider-side error record, or
     the transport's registered bounded rule that a session with this
     ``call_id`` can no longer come into existence.

2. ``reconcile_dispatched`` — the restart protocol over that contract, per
   M3_SPEC: for each DISPATCHED attempt (which, by t-a03's atomicity, has
   no native session id yet), ask ``find_session(call_id)``:

   * a session exists → ADOPT it: the provider's own session record is
     admitted as an obj-11 execution-lifecycle witness and t-a03 binds the
     native id;
   * the transport positively determines failure → t-a10;
   * otherwise the outcome is UNCERTAIN and the attempt HOLDS in
     DISPATCHED — absence is proven, never inferred (the same principle as
     the effect outcome rules).

   The reconciler mutates nothing directly: every state change goes through
   a public Sequencer transition.
"""

from .witnesses import WITNESS_KINDS

# The adoption evidence is the provider's own session record: an obj-11
# execution-lifecycle witness, whose registered minter the transport
# impersonates never — producer_id must be that registered minter.
_ADOPTION_KIND = "obj-11"

# D-C3-4 (U-06 resolved from the provider source): AutoSwarm's TERMINAL
# outcome vocabulary is exactly completed / failed / cancelled. A result
# record with a recognized terminal outcome proves that provider-reported
# terminal outcome; an ABSENT result record proves only that NO terminal
# provider outcome has been observed — never that the call is running.
# Running, stale or interrupted status comes only from separate durable
# lifecycle/session evidence (the handle/session witnesses behind t-a03;
# staleness is the caller's t-a12 member lease): absence of an
# authoritative observation is not an observation of the opposite state
# (the same G3 rule that keeps find_session(None) from proving failure).
# The frozen mapping normalizes the provider's success spelling onto the
# completion vocabulary the frozen contracts use; failed and cancelled
# pass through, and ANY unrecognized provider string also passes through
# VERBATIM — it can satisfy a role only if the frozen
# completion_policies' success_outcomes lists it. The provider reports;
# the contract decides.
AUTOSWARM_OUTCOME_TO_COMPLETION = {
    "completed": "success",
    "failed": "failed",
    "cancelled": "cancelled",
}


def normalize_outcome(provider_outcome):
    """Map one provider-reported outcome into the completion vocabulary
    (D-C3-4): known spellings map, everything else passes verbatim and is
    judged by the frozen role policy alone — never promoted to success."""
    return AUTOSWARM_OUTCOME_TO_COMPLETION.get(provider_outcome,
                                               provider_outcome)


class Transport:
    """The transport contract. Implementations override all four methods.

    ``producer_id`` names the principal whose original bytes the transport
    hands over as evidence; for AutoSwarm session records that is the
    registered obj-11 minter."""

    producer_id = WITNESS_KINDS[_ADOPTION_KIND]["producer"]  # "autoswarm"

    def launch(self, projection_address, provider, role, call_id):
        """Start (or find) the provider session for ``call_id``; returns the
        native session id. MUST be idempotent on ``call_id``."""
        raise NotImplementedError

    def find_session(self, call_id):
        """Return ``{"native_session_id": ..., "evidence": bytes}`` when the
        provider session is observable, else ``None``. ``None`` means
        UNOBSERVED, never FAILED."""
        raise NotImplementedError

    def positive_launch_failure(self, call_id):
        """Return the positive failure determination (a reason string) when
        the transport can PROVE the launch failed, else ``None``."""
        raise NotImplementedError

    def collect(self, call_id):
        """Return ``{"witness_files": [(witness_kind, producer, bytes),
        ...], "outcome": str}`` for a session's results — exact producer
        originals, ready for the admission seam."""
        raise NotImplementedError


def reconcile_dispatched(sequencer, transport):
    """Apply the launch/restart protocol to every DISPATCHED attempt.

    Returns a report list, one entry per attempt: ``action`` is
    ``"adopted"`` (t-a03 with the provider's native id, evidence admitted),
    ``"failed"`` (t-a10 on the transport's positive determination), or
    ``"held"`` (outcome UNCERTAIN: no transition — the attempt stays
    DISPATCHED)."""
    cur = sequencer.db.conn.cursor()
    rows = cur.execute(
        "SELECT a.attempt_id, c.call_id FROM attempts a"
        " JOIN attempt_calls c ON c.attempt_id = a.attempt_id"
        " WHERE a.state = 'DISPATCHED' ORDER BY a.attempt_id").fetchall()
    report = []
    for row in rows:
        found = transport.find_session(row["call_id"])
        if found is not None:
            admitted = sequencer.admit_witness(
                row["attempt_id"], _ADOPTION_KIND, transport.producer_id,
                found["evidence"])
            sequencer.attempt_running(
                row["attempt_id"], found["native_session_id"],
                admitted["content_address"])
            report.append({"attempt_id": row["attempt_id"],
                           "call_id": row["call_id"], "action": "adopted",
                           "native_session_id": found["native_session_id"]})
            continue
        reason = transport.positive_launch_failure(row["call_id"])
        if reason is not None:
            sequencer.fail_dispatched_attempt(row["attempt_id"],
                                              reason=reason)
            report.append({"attempt_id": row["attempt_id"],
                           "call_id": row["call_id"], "action": "failed",
                           "reason": reason})
        else:
            # UNCERTAIN: find_session gave no session and the transport has
            # no positive failure proof. The attempt holds in DISPATCHED.
            report.append({"attempt_id": row["attempt_id"],
                           "call_id": row["call_id"], "action": "held"})
    return report
