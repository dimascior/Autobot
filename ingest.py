"""Stage 1 -- sweep the curated backlog and the evidence sources into the ledger.

backlog.toml is the ONLY source of dispatchable `task` items. The autoswarm
calls, findings files and gate tree are swept as historical evidence
(kind=execution / finding / gate) so the coordinator knows what has already been
done -- never as work to redo. Malformed JSON is quarantined with its path and
reason rather than silently dropped.
"""

import json
import logging
import re
import tomllib
from pathlib import Path

from ledger import Ledger

log = logging.getLogger(__name__)

# Correlation ids that are themselves analysis/automation sessions, not real work.
_ANALYSIS_PATTERN = re.compile(r"^(2026\d{4}-|luna\d|catalog-|capi-|helios-push|autoswarm-)")


def _read_json(path: Path):
    """Return (data, error). error is a human string when the file is unusable."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return None, str(exc)


# ── curated backlog (the only dispatchable work) ─────────────────

def _ingest_backlog(ledger: Ledger, backlog_file: Path) -> int:
    if not backlog_file.is_file():
        log.warning("backlog file not found: %s", backlog_file)
        return 0
    try:
        data = tomllib.loads(backlog_file.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        log.error("backlog unreadable (%s): %s", backlog_file, exc)
        ledger.quarantine_file(str(backlog_file), str(exc))
        return 0
    tasks = data.get("task", [])
    count = 0
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        new = ledger.upsert_item(
            id=str(tid), kind="task", domain=t.get("domain", "backlog"),
            title=t.get("title", str(tid)), description=t.get("description", ""),
            evidence=t.get("evidence", ""), source="backlog",
            source_ref=str(backlog_file),
            task_class=t.get("task_class", "investigate"),
            route=t.get("route", "luna"), priority=int(t.get("priority", 0) or 0),
            status="open",
        )
        if new:
            count += 1
    # Second pass: dependencies, now that every backlog task exists.
    for t in tasks:
        tid = t.get("id")
        for dep in (t.get("depends_on") or []):
            ledger.add_dependency(str(dep), str(tid))
    return count


# ── autoswarm calls (executions -- evidence) ─────────────────────

def _ingest_calls(ledger: Ledger, home: Path) -> int:
    calls_dir = home / "calls"
    if not calls_dir.is_dir():
        return 0
    count = 0
    for call_dir in sorted(calls_dir.iterdir()):
        if not call_dir.is_dir():
            continue
        call_id = call_dir.name
        task, err = _read_json(call_dir / "task.json")
        if not task:
            if err and (call_dir / "task.json").exists():
                ledger.quarantine_file(str(call_dir / "task.json"), err)
            continue
        result, _ = _read_json(call_dir / "result.json")
        outcome = (result or {}).get("outcome", "") if result else ""
        provider = task.get("provider", "unknown")
        model = task.get("model", "unknown")
        status = "in_progress"
        if outcome == "completed":
            status = "completed"
        elif outcome in ("failed", "error"):
            status = "failed"
        new = ledger.upsert_item(
            id=f"call-{call_id}", kind="execution", domain="dispatch",
            title=f"{provider}/{model} run {call_id[:16]}",
            description=(task.get("prompt", "") or "")[:300],
            evidence=f"autoswarm call {call_id}; outcome={outcome or 'pending'}",
            source="autoswarm", source_ref=str(call_dir),
            task_class="investigate",
            route="luna" if provider == "codex" else "opus",
            status=status,
        )
        if new:
            count += 1
    return count


# ── findings files (findings -- evidence) ────────────────────────

def _ingest_findings(ledger: Ledger, findings_dir: Path) -> int:
    if not findings_dir.is_dir():
        return 0
    count = 0
    for md in sorted(findings_dir.glob("helios-findings-*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            ledger.quarantine_file(str(md), str(exc))
            continue
        fid = f"findings-{md.stem}"
        new = ledger.upsert_item(
            id=fid, kind="finding", domain="findings", title=md.stem,
            description=text[:300], evidence=f"recorded findings file {md.name}",
            source="findings-file", source_ref=str(md), status="completed",
        )
        if new:
            count += 1
            for num, name, body in _find_sections(text):
                ledger.upsert_item(
                    id=f"{fid}-p{num}", kind="finding",
                    domain=_domain_from_priority(num),
                    title=f"P{num} {name}".strip(),
                    description=body.strip()[:500],
                    evidence=f"section P{num} of {md.name}",
                    source="findings-section", source_ref=fid, status="completed",
                )
    return count


def _find_sections(text: str):
    out = []
    for m in re.finditer(r"^#{1,4}\s*P(\d)\b[ \-:]*([^\n]*)", text, re.MULTILINE):
        num, name = m.group(1), m.group(2).strip()
        start = m.end()
        nxt = re.search(r"^#{1,4}\s*P\d\b", text[start:], re.MULTILINE)
        body = text[start:start + nxt.start()] if nxt else text[start:]
        out.append((num, name, body))
    return out


def _domain_from_priority(num: str) -> str:
    return {
        "1": "command-diversity", "2": "health", "3": "gate-sample",
        "4": "evidence-chain", "5": "risk-tier", "6": "integrity",
        "7": "session-structure", "8": "digest",
    }.get(num, "investigation")


# ── gate tree (gates -- evidence) ────────────────────────────────

def _ingest_gates(ledger: Ledger, gate_root: Path) -> int:
    count = 0
    quarantined = 0
    for folder, folder_status in [("pending", "open"), ("inflight", "in_progress"),
                                  ("blocked", "blocked")]:
        gate_dir = gate_root / folder
        if not gate_dir.is_dir():
            continue
        for gate_file in sorted(gate_dir.glob("*.gate.json")):
            gate, err = _read_json(gate_file)
            if not gate:
                if err:
                    ledger.quarantine_file(str(gate_file), err)
                    quarantined += 1
                continue
            cid = str(gate.get("correlation_id", gate_file.stem))
            if _ANALYSIS_PATTERN.match(cid):
                continue
            try:
                tier = int(gate.get("risk_tier", 0) or 0)
            except (TypeError, ValueError):
                tier = 0
            gid = f"gate-{cid}"
            new = ledger.upsert_item(
                id=gid, kind="gate", domain="gate-activity",
                title=f"gate {cid} (tier {tier}) in {folder}",
                description=str(gate.get("need", ""))[:300],
                evidence=f"{folder} gate {gate_file.name}; tier {tier}",
                source="gate-file", source_ref=str(gate_file),
                task_class="investigate", route="luna", status=folder_status,
            )
            if new:
                count += 1
            prev = gate.get("previous_correlation_id")
            if prev and str(prev) != "null":
                # Preserve the edge even if the predecessor isn't in this sweep;
                # add_dependency mints an unresolved placeholder for it.
                ledger.add_dependency(f"gate-{prev}", gid)
    if quarantined:
        log.info("quarantined %d malformed gate files", quarantined)
    return count


# ── public entry point ───────────────────────────────────────────

def ingest(ledger: Ledger, *, autoswarm_home: Path, findings_dir: Path,
           gate_root: Path, backlog_file: Path) -> dict:
    backlog = _ingest_backlog(ledger, backlog_file)
    calls = _ingest_calls(ledger, autoswarm_home)
    findings = _ingest_findings(ledger, findings_dir)
    gates = _ingest_gates(ledger, gate_root)
    ledger.commit()
    summary = {
        "backlog_tasks": backlog,
        "calls": calls,
        "findings": findings,
        "gates": gates,
        "quarantined": len(ledger.quarantined()),
        "total_new": backlog + calls + findings + gates,
    }
    log.info("ingest: %s", summary)
    return summary
