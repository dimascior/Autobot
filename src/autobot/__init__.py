"""autobot — the authority core (Chapter 2, D-62..D-71).

One authority runtime owns all mutations of the authoritative ledger
(``autobot.db``) and its content-addressed store. Registry and Sequencer are
its internal authority services; everything else — AutoSwarm, Taskforce,
Helios, HDB, the old coordinator — consumes APIs and never writes
authoritative tables directly (D-63).

Protocol semantics come from the packaged ``contract_library``, a
byte-identical mirror of the frozen Chapter 1 library (D-65).
"""

from .db import SCHEMA_VERSION, Database
from .journal import append as journal_append
from .journal import transition_record, validate as journal_validate
from .models import (
    AdmissionError,
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    JournalBroken,
    LeaseUnavailable,
    LedgerError,
    ManualClock,
    PermitExhaustedError,
    SchemaTooNew,
    StaleFenceError,
    SystemClock,
    instant,
    mint_id,
)
from .registry import Registry
from .sequencer import Sequencer
from .store import Store

__all__ = [name for name in dir() if not name.startswith("_")]
