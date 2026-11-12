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
"""

from .receipts import (
    DEFAULT_EVIDENCE_WINDOW_SECONDS,
    derive_evidence,
    receipt_bytes,
)

COLLECTOR_PRINCIPAL = "collector"


class Collector:
    """Derive-and-admit for one frozen attempt's evidence.

    ``evidence_window_seconds`` is the D-C3-1 window: a D-18 configuration
    default, never a semantic constant (the effective value is the
    stricter of delegation or repository policy and operational config —
    carried here as configuration in v1)."""

    principal = COLLECTOR_PRINCIPAL

    def __init__(self, sequencer, store,
                 evidence_window_seconds=DEFAULT_EVIDENCE_WINDOW_SECONDS):
        self.sequencer = sequencer
        self.store = store
        self.window_seconds = int(evidence_window_seconds)

    def collect_and_admit(self, attempt_id):
        """Collect the frozen attempt's admitted evidence, derive the
        obj-15 classification (per-role completeness over the frozen
        record's evidence_requirements; the D-66 integrity probe; the
        D-C3-1 bound), mint the canonical receipt and admit it.

        Deterministic end to end: unchanged facts re-mint byte-identical
        bytes and the admission is idempotent on content identity, so
        re-collection sweeps and restarts converge; changed facts mint a
        NEW version (obj-15's registered append-only mutability). Since
        C3-M2 the derivation carries the causal evidence graph: joins
        from recognized witness facts, contradictions (including
        deny_then_ran), per-role member completeness and artifact
        provenance (D-C3-5/6)."""
        state = self.sequencer.evidence_state(attempt_id)
        payload = derive_evidence(self.store, state, self.window_seconds)
        admitted = dict(self.sequencer.admit_execution_receipt(
            attempt_id, receipt_bytes(payload)))
        admitted["receipt_address"] = admitted["content_address"]
        return admitted
