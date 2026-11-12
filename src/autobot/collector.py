"""The Autobot Evidence Collector — the registered ``collector`` seat.

principals.json registers ``collector`` ("Autobot Evidence Collector",
kind autobot_seat, llm: false); cap-30 makes it the owner of
"ExecutionReceipt (admission, completeness per role, index state)", with
the sequencer and verifier_runner as consumers and the task_force
FORBIDDEN; it is the registered trigger of t-a03 and the obj-15 minter
(CORR-03). Like the git executor, this component GRANTS NO PROTOCOL
AUTHORITY: it reads frozen and admitted facts through the Sequencer's
public read surface (``evidence_state``, D-63), derives the one receipt
as a pure function of those facts (receipts.build_receipt), and submits
the canonical bytes to the admission seam, which re-derives the payload
and refuses any divergence (D-C3-2). Every state change remains a
registered Sequencer transition, driven elsewhere.

C3-M3 adds cap-30's third facet, "index state" (D-C3-7): the registered
``hdb -> collector`` authority edge is labeled "index, INDEX_LAG
diagnostic only" (cap-28 grants the collector R), so the catalog enters
here as an INJECTED probe whose recognized status facts are REPORTED
beside the authority plane and never inside it — the obj-15 payload is a
pure function of durable ledger facts, so a live probe result cannot
enter it without breaking the D-C3-2 byte-equality (INV-30:
INDEX_LAG never blocks; CH3-index-lag-diagnostic executes the proof).
"""

from datetime import datetime, timezone

from .receipts import (
    DEFAULT_EVIDENCE_WINDOW_SECONDS,
    derive_evidence,
    parse_instant,
    receipt_bytes,
)

COLLECTOR_PRINCIPAL = "collector"

# The catalog status document this facet recognizes (U-05 resolved
# 2026-11-12: the helios-evidence-catalog CLI's status output, the
# QueryableCatalogStore.status() contract).
INDEX_STATUS_SCHEMA = "helios.evidence.catalog.status.v1"

# Tolerated watermark spellings: the catalog serializes Postgres
# timestamps, D-27 instants are the first form. Recognition, never
# rewriting — an unparseable watermark stays verbatim with no lag math.
_WATERMARK_FORMS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S%z",
)


def _parse_watermark(text):
    """A catalog watermark to an aware instant, or None — tolerant, the
    witness-facts way (absence or strangeness reports, never raises)."""
    if not isinstance(text, str) or not text:
        return None
    for form in _WATERMARK_FORMS:
        try:
            parsed = datetime.strptime(text, form)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


class Collector:
    """Derive-and-admit for one frozen attempt's evidence.

    ``evidence_window_seconds`` is the D-C3-1 window: a D-18 configuration
    default, never a semantic constant (the effective value is the
    stricter of delegation or repository policy and operational config —
    carried here as configuration in v1). ``catalog_probe`` is the
    optional D-C3-7 index probe: a callable returning the catalog's
    status document (INDEX_STATUS_SCHEMA); it feeds ``index_state`` and
    NOTHING else."""

    principal = COLLECTOR_PRINCIPAL

    def __init__(self, sequencer, store,
                 evidence_window_seconds=DEFAULT_EVIDENCE_WINDOW_SECONDS,
                 catalog_probe=None):
        self.sequencer = sequencer
        self.store = store
        self.window_seconds = int(evidence_window_seconds)
        self.catalog_probe = catalog_probe

    def collect_and_admit(self, attempt_id):
        """Collect the frozen attempt's admitted evidence, derive the
        obj-15 classification (per-role completeness over the frozen
        record's evidence_requirements; the D-66 integrity probe; the
        INV-37 input re-hash; the D-C3-1 bound), mint the canonical
        receipt and admit it.

        Deterministic end to end: unchanged facts re-mint byte-identical
        bytes and the admission is idempotent on content identity, so
        re-collection sweeps and restarts converge; changed facts mint a
        NEW version (obj-15's registered append-only mutability). Since
        C3-M2 the derivation carries the causal evidence graph: joins
        from recognized witness facts, contradictions (including
        deny_then_ran and execution_without_decision), per-role member
        completeness and artifact provenance (D-C3-5/6)."""
        state = self.sequencer.evidence_state(attempt_id)
        payload = derive_evidence(self.store, state, self.window_seconds)
        admitted = dict(self.sequencer.admit_execution_receipt(
            attempt_id, receipt_bytes(payload)))
        admitted["receipt_address"] = admitted["content_address"]
        return admitted

    def index_state(self, attempt_id=None):
        """cap-30's "index state" facet — DIAGNOSTIC ONLY (INV-30,
        D-C3-7). Reads the injected catalog probe and recognizes the
        status document's watermark facts tolerantly: an absent probe, a
        failing probe or an unrecognized document all REPORT — nothing
        here raises, blocks, classifies or journals. With ``attempt_id``
        (an attempt in CANDIDATE_FROZEN), the committed watermark is
        compared against the attempt's newest admitted evidence instant
        to report INDEX_LAG. The output appears in NO receipt, NO
        transition precondition and NO classification."""
        if self.catalog_probe is None:
            return {"available": False,
                    "reason": "no catalog probe configured"}
        try:
            document = self.catalog_probe()
        except Exception as exc:
            return {"available": False,
                    "reason": "catalog probe failed: %s"
                              % type(exc).__name__}
        if not isinstance(document, dict):
            return {"available": False,
                    "reason": "catalog status is not a recognized"
                              " document"}
        state = {"available": True}
        schema = document.get("schema")
        if isinstance(schema, str) and schema:
            state["schema"] = schema
        watermarks = document.get("watermarks")
        if isinstance(watermarks, dict):
            for fact in ("latest_committed_utc", "latest_observed_utc"):
                value = watermarks.get(fact)
                if isinstance(value, str) and value:
                    state[fact] = value
        integrity = document.get("integrity")
        if isinstance(integrity, dict) \
                and isinstance(integrity.get("closed"), bool):
            state["integrity_closed"] = integrity["closed"]
        if attempt_id is not None:
            evidence = self.sequencer.evidence_state(attempt_id)
            admitted = [row["admitted_utc"]
                        for row in (list(evidence["witnesses"])
                                    + list(evidence["artifacts"]))
                        if row.get("admitted_utc")]
            if admitted:
                state["newest_admitted_utc"] = max(admitted)
                committed = _parse_watermark(
                    state.get("latest_committed_utc"))
                if committed is not None:
                    newest = parse_instant(state["newest_admitted_utc"])
                    lag = (newest - committed).total_seconds()
                    state["lagging_behind_attempt"] = lag > 0
                    state["lag_behind_attempt_seconds"] = \
                        int(max(0, lag))
        return state
