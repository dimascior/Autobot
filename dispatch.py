"""Stage 4 -- autoswarm run invocations (subscription CLIs) + idempotency."""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ledger import Ledger

log = logging.getLogger(__name__)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _find_autoswarm() -> Optional[str]:
    for name in ("autoswarm", "autoswarm.exe", "autoswarm.cmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _build_run_cmd(autoswarm: str, route_cfg: dict) -> list[str]:
    """autoswarm run invocation for a route -- subscription CLI via --agent.

    - --agent selects the provider's own CLI (codex / claude-code), which uses
      the user's subscription login. No API key anywhere.
    - The prompt is fed on stdin (--input -) to avoid quoting a large task body.
    - --background returns immediately with the call record; the loop polls the
      result later via sync_active_dispatches.
    - --no-parent keeps the run standalone (no callback to a parent session that
      may not be alive); with ask-for-approval=never it never needs one.
    - provider_args carry the per-provider accommodations verbatim, e.g.
      --sandbox danger-full-access for codex (or the luna-008 stall returns).
    """
    cmd = [
        autoswarm, "run",
        "--agent", route_cfg["agent"],
        "--model", route_cfg["model"],
        "--cwd", route_cfg["workspace"],
        "--background", "--no-parent",
        "--input", "-",
    ]
    if route_cfg.get("effort"):
        cmd += ["--effort", route_cfg["effort"]]
    for arg in route_cfg.get("provider_args", []):
        cmd.append("--provider-arg=" + arg)
    return cmd


def _parse_call_id(stdout: str) -> str:
    """AutoSwarm wraps responses as {ok, data, error}; call_id is at .data.call_id."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return ""
    if isinstance(envelope, dict):
        data = envelope.get("data")
        if isinstance(data, dict):
            return data.get("call_id", "") or ""
    return ""


def _warn_if_ungoverned(route_cfg: dict) -> None:
    """A claude-code child runs ungated ONLY if its --cwd holds a local
    .command-gate (the settings.json wrapper then defers). If an overridden
    workspace lacks one, the child silently routes to the global gate instead,
    whose timeout / fail-open policy then governs the outcome -- not the deferred
    passthrough the opus route assumes. Codex routes are exempt (codex does not
    load the settings.json hooks at all)."""
    if route_cfg.get("agent") != "claude-code":
        return
    ws = route_cfg.get("workspace", "")
    if ws and not (Path(ws) / ".command-gate").is_dir():
        log.warning(
            "route governance: claude-code workspace %r has NO local "
            ".command-gate -- its child shell routes to the global gate, NOT "
            "the deferred passthrough this route assumes", ws)


def dispatch_item(
    ledger: Ledger,
    *,
    item_id: str,
    cycle_id: int,
    route_cfg: dict,
    prompt: str,
    autoswarm_home: Path,
    dry_run: bool = True,
) -> dict:
    model = route_cfg["model"]
    _warn_if_ungoverned(route_cfg)
    if ledger.was_dispatched_recently(item_id, minutes=10):
        log.info("skip %s: dispatched within last 10 minutes", item_id)
        return {"status": "skipped", "reason": "cooldown"}
    # Restart-safety guard: if an earlier cycle (even a crashed one) already has a
    # pending/running dispatch for this item, never launch a second child.
    if ledger.has_open_dispatch(item_id):
        log.info("skip %s: an open dispatch already exists (restart guard)", item_id)
        return {"status": "skipped", "reason": "open dispatch exists"}

    if dry_run:
        # A preview: log what would go out, record nothing. Repeated dry-runs
        # stay idempotent (no dispatch rows, no cooldown) so the backlog preview
        # is consistent across runs.
        log.info("DRY RUN -- would dispatch: item=%s agent=%s model=%s prompt=%.140s",
                 item_id, route_cfg.get("agent"), model, prompt)
        return {"status": "dry-run", "item_id": item_id, "model": model}

    autoswarm = _find_autoswarm()
    if not autoswarm:
        log.error("autoswarm CLI not found on PATH")
        return {"status": "error", "reason": "autoswarm not found"}

    # Reserve BEFORE launching: the item leaves the dispatchable pool and a
    # pending dispatch row is committed first. A crash/restart between here and
    # the child's launch therefore cannot double-dispatch -- the reservation is
    # caught by staleness and surfaced for reconciliation, never auto-rerun.
    dispatch_id = ledger.reserve_dispatch(
        item_id=item_id, cycle_id=cycle_id, model=model, prompt=prompt,
    )

    cmd = _build_run_cmd(autoswarm, route_cfg)
    env = os.environ.copy()
    env["AUTOSWARM_HOME"] = str(autoswarm_home)  # dispatched run lands in the home we ingest from

    try:
        proc = subprocess.run(
            cmd, input=prompt, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        # The launch call itself timed out -- a child may or may not exist. Do
        # NOT revert to open (that risks a duplicate); leave the reservation for
        # reconciliation to adjudicate against the autoswarm home.
        log.error("autoswarm run launch timed out for %s (reservation %d left for "
                  "reconciliation)", item_id, dispatch_id)
        return {"status": "error", "reason": "launch timeout", "dispatch_id": dispatch_id}
    except Exception as exc:  # noqa: BLE001
        # No subprocess started -> safe to release the reservation back to open.
        ledger.fail_reservation(dispatch_id, item_id, f"launch error: {exc}")
        log.error("autoswarm run failed to launch for %s: %s", item_id, exc)
        return {"status": "error", "reason": str(exc), "dispatch_id": dispatch_id}

    if proc.returncode != 0:
        # The CLI refused the launch (no child) -> release the reservation.
        reason = (proc.stderr or proc.stdout or "")[:300]
        ledger.fail_reservation(dispatch_id, item_id, f"exit {proc.returncode}: {reason}")
        log.error("autoswarm run exited %d for %s: %s", proc.returncode, item_id, reason)
        return {"status": "error", "reason": f"exit {proc.returncode}",
                "dispatch_id": dispatch_id}

    call_id = _parse_call_id(proc.stdout)
    if not call_id:
        log.warning("dispatched %s but could not parse call_id from: %.200s",
                    item_id, proc.stdout)
    # Child is live: attach the call_id and mark the reservation running. The
    # item stays in_progress (set by the reservation) so staleness can catch a
    # hung run.
    ledger.mark_dispatch_launched(dispatch_id, call_id)
    log.info("dispatched %s -> call_id=%s", item_id, call_id or "(unknown)")
    return {"status": "dispatched", "dispatch_id": dispatch_id, "call_id": call_id}


def sync_active_dispatches(ledger: Ledger, autoswarm_home: Path):
    active = ledger.active_dispatches()
    for d in active:
        call_id = d.get("call_id", "")
        if not call_id or call_id == "dry-run":
            continue
        result_file = autoswarm_home / "calls" / call_id / "result.json"
        if not result_file.exists():
            continue
        result = _read_json(result_file)
        if not result:
            continue
        outcome = result.get("outcome", "unknown")
        content = (result.get("content", "") or "")[:500]
        status = "completed" if outcome == "completed" else "failed"
        ledger.update_dispatch(
            d["id"], status=status, outcome=outcome, result_summary=content,
        )
        item = ledger.get_item(d["item_id"])
        if item and status == "completed":
            # A finished execution is not proof the task's finding is resolved.
            # Park it for reconciliation; a human / retry policy completes it.
            if item["status"] not in ("completed", "cancelled", "superseded"):
                ledger.set_status(d["item_id"], "awaiting_resolution")
        elif item and status == "failed":
            ledger.set_status(d["item_id"], "failed")
        log.info("synced dispatch %d: %s -> %s", d["id"], call_id, status)
    ledger.commit()


def dispatch_batch(
    ledger: Ledger,
    batch: list[dict],
    *,
    cycle_id: int,
    max_dispatches: int,
    routes_cfg: dict,
    autoswarm_home: Path,
    dry_run: bool = True,
) -> list[dict]:
    results = []
    dispatched = 0
    default_route = "luna" if "luna" in routes_cfg else next(iter(routes_cfg), None)
    for item in batch:
        if dispatched >= max_dispatches:
            log.info("dispatch cap reached (%d)", max_dispatches)
            break
        route_name = item.get("route", default_route)
        route_cfg = routes_cfg.get(route_name) or routes_cfg.get(default_route)
        if not route_cfg:
            log.error("no route config for %s (route=%s)", item["id"], route_name)
            results.append({**item, "status": "error", "reason": "no route config"})
            continue
        r = dispatch_item(
            ledger,
            item_id=item["id"],
            cycle_id=cycle_id,
            route_cfg=route_cfg,
            prompt=item.get("prompt", ""),
            autoswarm_home=autoswarm_home,
            dry_run=dry_run,
        )
        results.append({**item, **r})
        if r["status"] in ("dispatched", "dry-run"):
            dispatched += 1
    ledger.commit()
    return results
