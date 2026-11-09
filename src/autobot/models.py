"""Shared runtime vocabulary for the Autobot authority core.

Machine states mirror ``docs/architecture/chapter0/registries/states.json``
exactly (a test cross-checks them against the registry file, so the runtime
cannot drift from Chapter 0). Identifier minting follows D-22, instants
follow D-27, the injected clock is D-69, and integer domains are D-24.

Stdlib only: the authority core adds no third-party dependency (D-62..D-64).
"""

import secrets
from datetime import datetime, timedelta, timezone

# D-24: every protocol integer lives in [0, 2**53 - 1].
INT_MAX = 2 ** 53 - 1

# ------------------------------------------------------------------ states
# chapter0 states.json, machine by machine. Tuples preserve registry order.

SEQUENCE_STATES = (
    "DECLARED", "BLOCKED", "BLOCKED_UNSUPPORTED_FINALIZER", "READY",
    "EXECUTING", "CANDIDATE_FROZEN", "VERIFYING", "VERIFIED", "COMMITTING",
    "COMMITTED", "STAGING_PUBLISHING", "STAGED", "REMOTE_VERIFYING",
    "REMOTE_VERIFIED", "PUBLISHING", "PUBLISHED",
    "POST_PUBLICATION_OBSERVING", "ACCEPTED", "DEPENDENCIES_RELEASED",
    "COMPLETED", "HALTED", "HALTED_AFTER_VERIFICATION", "SUPERSEDED",
    "ABANDONED", "DELEGATION_REVOKED", "PUBLISHED_UNVERIFIED",
)
SEQUENCE_INITIAL = "DECLARED"
SEQUENCE_TERMINAL = frozenset((
    "COMPLETED", "SUPERSEDED", "ABANDONED", "DELEGATION_REVOKED",
    "PUBLISHED_UNVERIFIED",
))

ATTEMPT_STATES = (
    "DECLARED", "RESERVED", "DISPATCHED", "RUNNING", "FINISHED",
    "CANDIDATE_FROZEN", "EVIDENCE_COMPLETE", "VERIFIED", "REJECTED",
    "FAILED", "STALE", "FENCED", "RECONCILED",
)
ATTEMPT_INITIAL = "DECLARED"
ATTEMPT_TERMINAL = frozenset((
    "VERIFIED", "REJECTED", "FAILED", "STALE", "FENCED", "RECONCILED",
))

EFFECT_INTENT_STATES = (
    "PENDING", "HAPPENED", "ABSENT", "UNKNOWN_OUTCOME",
    "SUPERSEDED_BY_RETRY", "HUMAN_RESOLVED",
)
EFFECT_INTENT_INITIAL = "PENDING"
EFFECT_INTENT_TERMINAL = frozenset((
    "HAPPENED", "ABSENT", "SUPERSEDED_BY_RETRY", "HUMAN_RESOLVED",
))

PERMIT_RESERVATION_STATES = ("RESERVED", "CONSUMED", "RELEASED")
PERMIT_RESERVATION_INITIAL = "RESERVED"
PERMIT_RESERVATION_TERMINAL = frozenset(("CONSUMED", "RELEASED"))

ADMISSION_STATES = (
    "RECEIVED", "DRAFT", "QUEUED_FOR_YOU", "FROZEN", "DUPLICATE", "REJECTED",
)
ADMISSION_INITIAL = "RECEIVED"
ADMISSION_TERMINAL = frozenset(("FROZEN", "DUPLICATE", "REJECTED"))


# ------------------------------------------------------------------ errors

class LedgerError(Exception):
    """Base class for every authority-core refusal."""


class SchemaTooNew(LedgerError):
    """The database schema is newer than the running code (D-70)."""


class ConflictError(LedgerError):
    """A compare-and-swap on state/version found different durable facts."""


class StaleFenceError(LedgerError):
    """A presented fence is not the current fence (INV-13)."""


class AuthorityLost(LedgerError):
    """The instance lease is no longer held at the expected epoch."""


class LeaseUnavailable(LedgerError):
    """Another unexpired holder owns the instance lease (INV-25a)."""


class PermitExhaustedError(LedgerError):
    """consumed_uses + reserved_uses would exceed permitted_uses (INV-48)."""


class IntegrityHalt(LedgerError):
    """A committed ledger reference does not resolve to its exact bytes
    (D-66), or durable identities fail recomputation. The runtime stops."""


class JournalBroken(LedgerError):
    """The TransitionRecord hash chain fails validation (JH-1, INV-10)."""


class AdmissionError(LedgerError):
    """A submission could not enter the admission machine at all."""


# -------------------------------------------------------------- id minting

def mint_id(prefix):
    """D-22 minted id: ``<prefix>_<32 lowercase hex>`` (128 random bits)."""
    return "%s_%s" % (prefix, secrets.token_hex(16))


# ----------------------------------------------------------------- instants

def instant(dt):
    """D-27: exactly millisecond-precision UTC, ``YYYY-MM-DDTHH:MM:SS.mmmZ``.

    The fixed width makes instants lexically ordered, so windows compare as
    strings (D-28)."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


class SystemClock:
    """D-69: the production clock. Time decides expiry only — never order."""

    def now(self):
        return datetime.now(timezone.utc)


class ManualClock:
    """Deterministic clock for tests and replay."""

    def __init__(self, start):
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        self._now = start

    def now(self):
        return self._now

    def advance(self, seconds):
        self._now = self._now + timedelta(seconds=seconds)
