"""Stage 3 -- build planner input, call the LLM (via claude CLI) or rule fallback.

The planner only ever sees dispatchable backlog tasks (state.py has already
excluded historical evidence, blocked, stale and completed work). It drafts a
task prompt per item from the title + description + source evidence.
"""

import json
import logging
import shutil
import subprocess
from typing import Optional

from coordinator.ledger import Ledger

log = logging.getLogger(__name__)

PLANNER_SYSTEM = """You are the swarm coordinator planner. You receive a ranked
list of dispatchable backlog tasks -- each is already filtered to be genuinely
open, unblocked, real work (never historical evidence). Decide which to dispatch
this cycle.

Each item has: id, domain, title, description, evidence, status, task_class,
route, score, dispatch_count.

Rules:
- Propose at most `available_slots` auto-dispatchable (non-approval) tasks.
- You MAY also propose task_class=modify (or other approval-gated) tasks: they
  are parked for human approval, not dispatched, so they do not consume a slot.
  Surface them when they are high priority.
- Prefer higher score and never-dispatched tasks.
- Draft a concrete prompt from the title, description and evidence, suited to the
  route (luna=gpt-6-luna for investigation/execution, opus=claude-opus for
  verification/monitoring). Carry the source evidence so the worker can justify
  its conclusions.
- Return valid JSON: {"batch": [{"id": "...", "model": "...", "prompt": "..."}]}
- If nothing should be dispatched, return {"batch": []}.
"""


def _build_planner_input(state_summary: dict, available_slots: int,
                         model_config: dict) -> dict:
    return {
        "available_slots": available_slots,
        "model_routing": model_config,
        "dispatchable_tasks": state_summary.get("top_items", []),
        "reconcile": state_summary.get("reconcile", []),
        "counts": {
            "dispatchable": state_summary.get("dispatchable_count"),
            "blocked_tasks": state_summary.get("blocked_tasks"),
            "reconcile": state_summary.get("reconcile_count"),
            "pending_approvals": state_summary.get("pending_approvals"),
        },
    }


def _call_llm(planner_input: dict, model: str) -> Optional[dict]:
    """Call the planner model through the subscription CLI -- never a billed API.

    Claude models run through the `claude` CLI headless (the same Claude Code
    login used everywhere else). A gpt/codex planner is not wired here: it would
    have to route through `autoswarm run --agent codex` (also subscription), so
    rather than reach for an API key we fall back to the rule-based planner.
    """
    if not model.startswith("claude"):
        log.warning("planner model %s is not claude-*; codex/gpt planners must route "
                    "through autoswarm's codex CLI. Using rule-based planner.", model)
        return None
    claude = shutil.which("claude")
    if not claude:
        log.warning("`claude` CLI not found on PATH -- using rule-based planner")
        return None

    prompt = PLANNER_SYSTEM + "\n\nSTATE (decide the batch from this):\n" + \
        json.dumps(planner_input, indent=2, default=str)
    try:
        proc = subprocess.run(
            [claude, "-p", "--output-format", "json", "--model", model,
             "--dangerously-skip-permissions"],
            input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("claude CLI planner call failed: %s -- using rule-based planner", exc)
        return None
    if proc.returncode != 0:
        log.warning("claude CLI exited %d: %s", proc.returncode, (proc.stderr or "")[:300])
        return None

    text = proc.stdout
    try:
        envelope = json.loads(text)
        if isinstance(envelope, dict) and "result" in envelope:
            text = envelope["result"]
    except json.JSONDecodeError:
        pass  # headless printed raw text rather than a json envelope

    start, end = text.find("{"), text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            log.warning("planner output not parseable as a JSON batch")
    return None


def _draft_prompt(item: dict) -> str:
    domain = item.get("domain", "")
    title = item.get("title", "")
    desc = (item.get("description") or "").strip()
    evidence = (item.get("evidence") or "").strip()
    task_class = item.get("task_class", "investigate")
    verb = {"verify": "Verify", "modify": "Apply/fix", "monitor": "Monitor"}.get(
        task_class, "Investigate")
    parts = [f"{verb}: {title}.", f"Domain: {domain}."]
    if desc:
        parts.append(f"Details: {desc}")
    if evidence:
        parts.append(f"Source evidence: {evidence}")
    return " ".join(parts)


def _rule_based_plan(state_summary: dict, available_slots: int,
                     model_config: dict, require_approval) -> dict:
    require_approval = set(require_approval or ())
    batch = []
    auto = 0
    for item in state_summary.get("top_items", []):
        is_approval = item.get("task_class", "investigate") in require_approval
        if not is_approval and auto >= available_slots:
            continue  # out of auto slots; keep scanning for approval-class items
        route = item.get("route", "luna")
        model = model_config.get(route) or model_config.get("luna", "gpt-6-luna")
        batch.append({"id": item["id"], "model": model, "prompt": _draft_prompt(item)})
        if not is_approval:
            auto += 1
    return {"batch": batch}


def plan(
    ledger: Ledger,
    state_summary: dict,
    *,
    available_slots: int,
    model_config: dict,
    planner_model: str = "claude-opus-4-6",
    use_llm: bool = True,
    require_approval=(),
) -> dict:
    planner_input = _build_planner_input(state_summary, available_slots, model_config)

    result = None
    if use_llm:
        result = _call_llm(planner_input, planner_model)

    if result is None:
        result = _rule_based_plan(state_summary, available_slots, model_config,
                                  require_approval)
        result["planner"] = "rule-based"
    else:
        result["planner"] = "llm"

    result["planner_input"] = planner_input
    log.info("plan: %s produced %d items", result.get("planner"),
             len(result.get("batch", [])))
    return result
