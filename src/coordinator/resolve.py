"""Stage 2b -- the resolution evaluator.

A finished execution is NOT a closed task. When a dispatch completes, its item
is parked at `awaiting_resolution` (dispatch.py). This stage compares the task's
original acceptance criteria against the execution artifacts and the
authoritative evidence, and records a durable *proposed* verdict with evidence
lineage:

    resolved -> the acceptance criteria are met and authoritative evidence
                corroborates it (verified closure).
    rework   -> the execution failed, or the evidence contradicts closure; a new
                authorized task is the right next step.
    review   -> insufficient evidence to decide; a human should look.

This stage only *proposes*. It never changes item status and never closes a
task. `ledger.authorize_resolution` -- invoked by the designated authority
(see authorize.py) -- is the single path that enacts a material state change.
That is the "no automatic promotion of successful executions into completed
tasks" guarantee, in code.
"""

import hashlib
import json
import logging
from pathlib import Path

from coordinator.ledger import Ledger

log = logging.getLogger(__name__)

EVALUATOR_ID = "rule:v1"


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_execution_evidence(ledger: Ledger, item: dict, dispatch: dict,
                                autoswarm_home: Path):
    """Gather what the execution actually produced, with hashes."""
    source_refs = []
    evidence_hashes = {}
    if not dispatch:
        return source_refs, evidence_hashes
    call_id = dispatch.get("call_id") or ""
    source_refs.append({"type": "dispatch", "dispatch_id": dispatch["id"],
                        "call_id": call_id, "outcome": dispatch.get("outcome"),
                        "status": dispatch.get("status")})
    summary = dispatch.get("result_summary") or ""
    if summary:
        evidence_hashes[f"dispatch:{dispatch['id']}:summary"] = _sha256_text(summary)
    # the raw result file, if the live call wrote one
    if call_id and call_id != "dry-run":
        result_file = autoswarm_home / "calls" / call_id / "result.json"
        if result_file.is_file():
            source_refs.append({"type": "result_file", "path": str(result_file)})
            try:
                evidence_hashes[str(result_file)] = _sha256_file(result_file)
            except OSError as exc:
                log.warning("could not hash %s: %s", result_file, exc)
    return source_refs, evidence_hashes


def _collect_authoritative_evidence(ledger: Ledger, item: dict, dispatch: dict,
                                    findings_dir: Path):
    """Find independent authoritative evidence that corroborates closure.

    For this backlog that means findings files recorded AFTER the dispatch went
    out (the worker's own record of what it concluded). We hash whatever we find
    so the verdict is reproducible and auditable.
    """
    refs = []
    hashes = {}
    if not findings_dir.is_dir():
        return refs, hashes
    dispatched_utc = (dispatch or {}).get("dispatched_utc", "")
    dispatch_ts = None
    if dispatched_utc:
        try:
            from datetime import datetime
            dispatch_ts = datetime.fromisoformat(
                dispatched_utc.replace("Z", "+00:00")).timestamp()
        except (ValueError, OSError):
            pass
    for md in sorted(findings_dir.glob("helios-findings-*.md")):
        try:
            mtime = md.stat().st_mtime
        except OSError:
            continue
        if dispatch_ts is not None and mtime < dispatch_ts:
            continue
        refs.append({"type": "findings_file", "path": str(md), "mtime": mtime})
        try:
            hashes[str(md)] = _sha256_file(md)
        except OSError:
            pass
    return refs, hashes


def _verdict(item: dict, dispatch: dict, authoritative_refs: list) -> tuple[str, str]:
    """Rule-based verdict. Conservative: only `resolved` when the execution
    completed AND independent authoritative evidence exists."""
    if not dispatch:
        return "review", "awaiting_resolution but no dispatch on record"
    outcome = (dispatch.get("outcome") or "").lower()
    status = (dispatch.get("status") or "").lower()
    if outcome in ("failed", "error") or status == "failed":
        return "rework", f"execution did not complete (outcome={outcome or status})"
    if outcome != "completed":
        return "review", f"execution outcome is '{outcome or status}', not completed"
    if not authoritative_refs:
        return "review", ("execution completed but no independent authoritative "
                          "evidence (findings file) was located to corroborate closure")
    return "resolved", (f"execution completed and {len(authoritative_refs)} "
                        "authoritative evidence record(s) corroborate closure")


def evaluate_awaiting(ledger: Ledger, *, findings_dir: Path, autoswarm_home: Path,
                      evaluator: str = EVALUATOR_ID) -> dict:
    """Propose a resolution for every item parked at awaiting_resolution.

    Idempotent per (item, latest dispatch): an item that already has a proposed
    resolution for its latest dispatch is skipped, so repeated cycles do not
    pile up duplicate proposals.
    """
    out = {"evaluated": 0, "resolved": 0, "rework": 0, "review": 0, "skipped": 0}
    for item in ledger.items_by_status("awaiting_resolution"):
        dispatch = ledger.latest_dispatch(item["id"])
        dispatch_id = dispatch["id"] if dispatch else None
        if ledger.has_open_resolution(item["id"], dispatch_id):
            out["skipped"] += 1
            continue
        exec_refs, exec_hashes = _collect_execution_evidence(
            ledger, item, dispatch, autoswarm_home)
        auth_refs, auth_hashes = _collect_authoritative_evidence(
            ledger, item, dispatch, findings_dir)
        verdict, rationale = _verdict(item, dispatch, auth_refs)
        ledger.record_resolution(
            item_id=item["id"], verdict=verdict, evaluator=evaluator,
            dispatch_id=dispatch_id, rationale=rationale,
            acceptance_criteria=item.get("description", ""),
            source_refs=exec_refs + auth_refs,
            evidence_hashes={**exec_hashes, **auth_hashes},
        )
        out["evaluated"] += 1
        out[verdict] += 1
        log.info("resolution proposed: %s -> %s (%s)", item["id"], verdict, rationale)
    return out
