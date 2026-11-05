"""Safety envelope — task-class gate, caps, kill switch, dry-run."""

import logging
from pathlib import Path

from coordinator.ledger import Ledger

log = logging.getLogger(__name__)


class SafetyViolation(Exception):
    pass


def check_kill_switch(stop_file: Path):
    if stop_file.exists():
        raise SafetyViolation(f"STOP file present: {stop_file}")


def check_concurrent_cap(ledger: Ledger, max_concurrent: int):
    active = ledger.active_dispatches()
    if len(active) >= max_concurrent:
        raise SafetyViolation(
            f"concurrent cap: {len(active)} active >= {max_concurrent} max"
        )
    return max_concurrent - len(active)


def check_cycle_cap(ledger: Ledger, max_per_hour: int):
    recent = ledger.cycles_since(hours=1)
    if len(recent) >= max_per_hour:
        raise SafetyViolation(
            f"cycle cap: {len(recent)} cycles in last hour >= {max_per_hour} max"
        )


def check_daily_cap(ledger: Ledger, daily_ceiling: int):
    today = ledger.cycles_since(hours=24)
    total_dispatched = sum(c.get("items_dispatched", 0) for c in today)
    if total_dispatched >= daily_ceiling:
        raise SafetyViolation(
            f"daily cap: {total_dispatched} dispatches today >= {daily_ceiling} ceiling"
        )


def gate_batch(
    batch: list[dict],
    require_approval: list[str],
    ledger: Ledger,
    cycle_id: int,
) -> tuple[list[dict], list[dict]]:
    auto = []
    parked = []
    for item in batch:
        tc = item.get("task_class", "investigate")
        if tc in require_approval:
            ledger.queue_approval(
                item_id=item["id"],
                cycle_id=cycle_id,
                prompt=item.get("prompt", ""),
                model=item.get("model", ""),
                reason=f"task_class={tc} requires manual approval",
            )
            parked.append(item)
            log.info("parked for approval: %s (task_class=%s)", item["id"], tc)
        else:
            auto.append(item)
    return auto, parked


def run_safety_checks(
    ledger: Ledger,
    *,
    stop_file: Path,
    max_concurrent: int,
    max_per_hour: int,
    daily_ceiling: int,
) -> int:
    check_kill_switch(stop_file)
    slots = check_concurrent_cap(ledger, max_concurrent)
    check_cycle_cap(ledger, max_per_hour)
    check_daily_cap(ledger, daily_ceiling)
    return slots
