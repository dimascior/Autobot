"""Stage 2 -- lifecycle transitions and ranking of the dispatchable backlog.

Dispatch candidates are ONLY `task` items that are open and whose predecessors
are all completed. Completed / cancelled / superseded evidence is excluded by
construction; stale and failed items are surfaced for reconciliation and never
returned to the dispatch pool automatically.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from coordinator.ledger import Ledger

log = logging.getLogger(__name__)


def _parse_utc(stamp: str) -> Optional[datetime]:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_ago(stamp: str) -> Optional[float]:
    dt = _parse_utc(stamp)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def apply_staleness(ledger: Ledger, stale_threshold_minutes: int) -> None:
    """An in_progress dispatch with no result past the threshold becomes STALE.

    STALE is a reconciliation condition, not permission to re-execute: a stale
    item leaves the dispatch pool and is surfaced for a human / retry policy.
    """
    for item in ledger.items_by_status("in_progress"):
        d = ledger.latest_dispatch(item["id"])
        if not d or d["status"] in ("completed", "failed"):
            continue
        age = _minutes_ago(d["dispatched_utc"])
        if age is not None and age > stale_threshold_minutes:
            ledger.set_status(item["id"], "stale")
            log.info("stale: %s (dispatch idle %.0f min, needs reconciliation)",
                     item["id"], age)
    ledger.commit()


def apply_blocking(ledger: Ledger) -> None:
    """Keep task open/blocked in sync with predecessor completion."""
    for item in ledger.items_by_status("blocked"):
        if item["kind"] == "task" and ledger.is_unblocked(item["id"]):
            ledger.set_status(item["id"], "open")
            log.info("unblocked: %s (all predecessors completed)", item["id"])
    for item in ledger.items_by_status("open"):
        if item["kind"] == "task" and not ledger.is_unblocked(item["id"]):
            ledger.set_status(item["id"], "blocked")
            log.info("blocked: %s (predecessor not completed)", item["id"])
    ledger.commit()


def rank(ledger: Ledger) -> list[dict]:
    ranked = []
    for item in ledger.dispatchable_tasks():
        score = item["priority"]
        age = _minutes_ago(item["created_utc"])
        if age and age > 240:
            score += 1
        if item["dispatch_count"] == 0:
            score += 2
        ranked.append({**item, "_score": score})
    ranked.sort(key=lambda x: (-x["_score"], x["created_utc"]))
    return ranked


def compute_state(ledger: Ledger, stale_threshold_minutes: int = 120) -> dict:
    apply_staleness(ledger, stale_threshold_minutes)
    apply_blocking(ledger)
    ranked = rank(ledger)
    reconcile = ledger.reconciliation_items()
    approvals = ledger.pending_approvals()

    summary = {
        "dispatchable_count": len(ranked),
        "blocked_tasks": len([i for i in ledger.items_by_status("blocked")
                              if i["kind"] == "task"]),
        "reconcile_count": len(reconcile),
        "pending_approvals": len(approvals),
        "kind_status": ledger.count_by_kind_status(),
        "top_items": [
            {
                "id": r["id"],
                "domain": r["domain"],
                "title": r["title"],
                "description": r["description"],
                "evidence": r["evidence"],
                "source_ref": r["source_ref"],
                "status": r["status"],
                "task_class": r["task_class"],
                "route": r["route"],
                "score": r["_score"],
                "dispatch_count": r["dispatch_count"],
            }
            for r in ranked[:20]
        ],
        "reconcile": [
            {"id": r["id"], "status": r["status"], "title": r["title"]}
            for r in reconcile[:20]
        ],
    }
    log.info("state: %d dispatchable, %d blocked tasks, %d to reconcile, %d awaiting approval",
             summary["dispatchable_count"], summary["blocked_tasks"],
             summary["reconcile_count"], summary["pending_approvals"])
    return summary
