"""The coordinator cycle -- ingest, state, plan, safety-gate, dispatch, repeat."""

import argparse
import logging
import os
import time
import tomllib
from pathlib import Path

from coordinator import PROJECT_ROOT, discover
from coordinator.ledger import Ledger
from coordinator.ingest import ingest
from coordinator.state import compute_state
from coordinator.planner import plan
from coordinator.resolve import evaluate_awaiting
from coordinator.safety import SafetyViolation, gate_batch, run_safety_checks
from coordinator.dispatch import dispatch_batch, sync_active_dispatches

log = logging.getLogger("coordinator")


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay into base (overlay wins). Layers
    config.local.toml (machine-specific, gitignored) over config.toml."""
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path: Path) -> dict:
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    # Machine-specific overrides live beside config.toml but are gitignored.
    local = config_path.with_name("config.local.toml")
    if local.exists():
        with open(local, "rb") as f:
            _deep_merge(cfg, tomllib.load(f))
    # Fill any path/workspace still blank from authoritative sources
    # (settings.json, $AUTOSWARM_HOME, PATH). Explicit values above win.
    discover.apply_to_config(cfg)
    # Export the resolved home so the whole subprocess tree (dispatched children
    # and anything they spawn) agrees on where runs land -- without clobbering an
    # AUTOSWARM_HOME the launching shell already set, and only when discovery
    # actually found one (an unset home must never export an empty var).
    home = cfg["paths"].get("autoswarm_home")
    if home:
        os.environ.setdefault("AUTOSWARM_HOME", home)
    return cfg


def _resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else base / p


def one_cycle(ledger: Ledger, cfg: dict, trigger: str = "manual") -> dict:
    here = PROJECT_ROOT
    paths = cfg["paths"]
    caps = cfg["caps"]
    cooldowns = cfg["cooldowns"]
    models = cfg["models"]
    routes = cfg["routes"]
    safety_cfg = cfg["safety"]
    dry_run = safety_cfg.get("dry_run", True)

    stop_file = _resolve(here, safety_cfg["stop_file"])
    backlog_file = _resolve(here, paths["backlog_file"])

    # autoswarm_home / findings_dir / gate_root are non-blocking inputs: an unset
    # one resolves to a path that does not exist, and every ingest sweep guards
    # with .is_dir(), so the cycle runs (ingesting nothing from that source)
    # instead of crashing. `discover.py --check` is where unreadiness is flagged.
    def _dir(value):
        return Path(value) if value else here / "__unset__"
    autoswarm_home = _dir(paths.get("autoswarm_home"))
    findings_dir = _dir(paths.get("findings_dir"))
    gate_root = _dir(paths.get("gate_root"))

    cycle_id = ledger.start_cycle(trigger, dry_run=dry_run)
    result = {"cycle_id": cycle_id, "trigger": trigger, "dry_run": dry_run}

    try:
        # Read results BEFORE the cap check: otherwise a full cap blocks the
        # result read and the cycle deadlocks after the first full batch.
        sync_active_dispatches(ledger, autoswarm_home)

        slots = run_safety_checks(
            ledger,
            stop_file=stop_file,
            max_concurrent=caps["max_concurrent"],
            max_per_hour=caps["max_cycles_per_hour"],
            daily_ceiling=caps["daily_ceiling"],
        )
        log.info("cycle %d: %d slots available", cycle_id, slots)

        # stage 1: ingest (curated backlog + evidence)
        ingest_summary = ingest(
            ledger,
            autoswarm_home=autoswarm_home,
            findings_dir=findings_dir,
            gate_root=gate_root,
            backlog_file=backlog_file,
        )
        ledger.log_audit(cycle_id, "ingest", output_data=ingest_summary)

        # stage 2b: resolution evaluator -- PROPOSE evidence-backed verdicts for
        # anything parked at awaiting_resolution. This never closes a task; the
        # designated authority does that via authorize.py.
        resolution_summary = evaluate_awaiting(
            ledger,
            findings_dir=findings_dir,
            autoswarm_home=autoswarm_home,
        )
        ledger.log_audit(cycle_id, "resolve", output_data=resolution_summary)

        # stage 2: state (exclusion, staleness, blocking, ranking)
        state_summary = compute_state(
            ledger,
            stale_threshold_minutes=cooldowns.get("stale_threshold_minutes", 120),
        )
        ledger.log_audit(cycle_id, "state", output_data=state_summary)

        # stage 3: plan
        plan_result = plan(
            ledger,
            state_summary,
            available_slots=slots,
            model_config={name: rc["model"] for name, rc in routes.items()},
            planner_model=models.get("planner", "claude-opus-4-6"),
            use_llm=(not dry_run) or safety_cfg.get("planner_in_dry_run", False),
            require_approval=safety_cfg.get("require_approval", []),
        )
        ledger.log_audit(cycle_id, "plan",
                         input_data=plan_result.get("planner_input"),
                         output_data={"batch": plan_result.get("batch", []),
                                      "planner": plan_result.get("planner")})

        # Validate and enrich: the planner (especially the LLM) may return
        # items that are no longer dispatchable, non-task items, or more auto
        # items than the available slots.  Filter against the ledger's truth.
        raw_batch = plan_result.get("batch", [])
        batch = []
        auto_count = 0
        require_approval_set = set(safety_cfg.get("require_approval", []))
        for item in raw_batch:
            db_item = ledger.get_item(item["id"])
            if not db_item:
                log.warning("planner proposed unknown item %s — dropped", item["id"])
                continue
            if db_item["kind"] != "task":
                log.warning("planner proposed non-task %s (kind=%s) — dropped",
                            item["id"], db_item["kind"])
                continue
            if db_item["status"] not in ("open",):
                log.warning("planner proposed non-open task %s (status=%s) — dropped",
                            item["id"], db_item["status"])
                continue
            item["task_class"] = db_item.get("task_class", "investigate")
            item["route"] = db_item.get("route", "luna")
            is_approval = item["task_class"] in require_approval_set
            if not is_approval:
                if auto_count >= slots:
                    log.info("planner batch trimmed: auto slots exhausted (%d)", slots)
                    continue
                auto_count += 1
            batch.append(item)

        # safety gate: park approval-class work, pass the rest
        auto, parked = gate_batch(batch, safety_cfg.get("require_approval", []),
                                  ledger, cycle_id)
        ledger.log_audit(cycle_id, "safety",
                         output_data={"auto": len(auto), "parked": len(parked)})

        # stage 4: dispatch (dry-run logs, launches nothing)
        dispatch_results = dispatch_batch(
            ledger, auto, cycle_id=cycle_id,
            max_dispatches=caps["max_dispatches_per_cycle"],
            routes_cfg=routes, autoswarm_home=autoswarm_home, dry_run=dry_run,
        )
        ledger.log_audit(cycle_id, "dispatch", output_data=dispatch_results)

        dispatched_count = sum(
            1 for r in dispatch_results if r.get("status") in ("dispatched", "dry-run")
        )
        ledger.complete_cycle(cycle_id, ingested=ingest_summary["total_new"],
                              planned=len(batch), dispatched=dispatched_count)

        result.update({
            "status": "completed",
            "ingest_breakdown": ingest_summary,
            "dispatchable": state_summary["dispatchable_count"],
            "blocked_tasks": state_summary["blocked_tasks"],
            "reconcile": state_summary["reconcile_count"],
            "planned": len(batch),
            "dispatched": dispatched_count,
            "parked": len(parked),
            "planner": plan_result.get("planner"),
            "resolutions_proposed": resolution_summary.get("evaluated", 0),
            "resolution_breakdown": resolution_summary,
        })
        log.info("cycle %d complete: dispatchable=%d planned=%d dispatched=%d parked=%d",
                 cycle_id, state_summary["dispatchable_count"], len(batch),
                 dispatched_count, len(parked))

    except SafetyViolation as exc:
        ledger.log_audit(cycle_id, "safety-halt", output_data={"error": str(exc)})
        ledger.complete_cycle(cycle_id)
        result["status"] = "safety-halt"
        result["error"] = str(exc)
        log.warning("cycle %d halted: %s", cycle_id, exc)

    except Exception as exc:  # noqa: BLE001
        ledger.log_audit(cycle_id, "error", output_data={"error": str(exc)})
        ledger.complete_cycle(cycle_id)
        result["status"] = "error"
        result["error"] = str(exc)
        log.error("cycle %d error: %s", cycle_id, exc, exc_info=True)

    return result


def run_loop(cfg: dict, once: bool = False):
    db_path = _resolve(PROJECT_ROOT, cfg["paths"]["ledger_db"])
    ledger = Ledger(db_path)
    timer_floor = cfg["triggers"]["timer_floor_minutes"] * 60

    log.info("coordinator started (dry_run=%s, once=%s)", cfg["safety"]["dry_run"], once)

    try:
        while True:
            result = one_cycle(ledger, cfg, trigger="manual" if once else "timer")
            if once:
                _print_summary(result, ledger)
                break
            if result.get("status") == "safety-halt" and "STOP" in str(result.get("error", "")):
                log.info("STOP file detected -- draining and halting")
                break
            log.info("sleeping %d seconds until next cycle", timer_floor)
            time.sleep(timer_floor)
    finally:
        ledger.close()


def _print_summary(result: dict, ledger: Ledger):
    print("\n=== Coordinator Cycle Summary ===")
    print(f"  Cycle:        {result.get('cycle_id')}")
    print(f"  Status:       {result.get('status')}")
    print(f"  Dry run:      {result.get('dry_run')}")
    print(f"  Planner:      {result.get('planner', '-')}")
    b = result.get("ingest_breakdown")
    if b:
        print(f"  Ingest(new):  backlog={b['backlog_tasks']} calls={b['calls']} "
              f"findings={b['findings']} gates={b['gates']} quarantined={b['quarantined']}")
    print(f"  Dispatchable: {result.get('dispatchable', 0)} (backlog tasks only)")
    print(f"  Blocked:      {result.get('blocked_tasks', 0)}")
    print(f"  Reconcile:    {result.get('reconcile', 0)}")
    print(f"  Planned:      {result.get('planned', 0)}")
    label = " (dry-run)" if result.get("dry_run") else ""
    print(f"  Dispatched:   {result.get('dispatched', 0)}{label}")
    print(f"  Parked:       {result.get('parked', 0)}")
    rb = result.get("resolution_breakdown") or {}
    if rb.get("evaluated") or rb.get("skipped"):
        print(f"  Resolutions:  proposed={rb.get('evaluated', 0)} "
              f"(resolved={rb.get('resolved', 0)} rework={rb.get('rework', 0)} "
              f"review={rb.get('review', 0)}) skipped={rb.get('skipped', 0)}")
    if result.get("error"):
        print(f"  Error:        {result['error']}")

    approvals = ledger.pending_approvals()
    if approvals:
        print(f"\n  Pending approvals ({len(approvals)}):")
        for a in approvals:
            print(f"    - [{a['id']}] {a['item_id']}: {a['reason']}")

    proposed = ledger.proposed_resolutions()
    if proposed:
        print(f"\n  Proposed resolutions awaiting authority ({len(proposed)}):")
        for r in proposed:
            print(f"    - [resolution {r['id']}] {r['item_id']} -> {r['verdict']}: "
                  f"{r['rationale'][:80]}")
        print("    (review with:  python authorize.py --list)")
    recon = ledger.reconciliation_items()
    if recon:
        print(f"\n  Needs reconciliation ({len(recon)}):")
        for r in recon[:10]:
            print(f"    - {r['id']} [{r['status']}]")

    quarantined = ledger.quarantined()
    if quarantined:
        print(f"\n  Quarantined files ({len(quarantined)}): malformed JSON, skipped")
    print()


def main():
    parser = argparse.ArgumentParser(description="Swarm coordinator")
    parser.add_argument("--config", default="config.toml", help="config file path")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--dry-run", action="store_true", help="force dry-run mode")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(_resolve(PROJECT_ROOT, args.config))
    if args.dry_run:
        cfg["safety"]["dry_run"] = True

    run_loop(cfg, once=args.once)


if __name__ == "__main__":
    main()
