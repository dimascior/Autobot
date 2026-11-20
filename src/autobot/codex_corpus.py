"""Census of retained Codex provider session records for obj-13.

Scans the native Codex sessions directory, reads every record, and
produces a reproducible vocabulary report. The report is a dict suitable
for JSON serialization.

READS ONLY. Never rewrites, moves, or deletes any provider record.
"""

import json
import os
from collections import Counter
from pathlib import Path

from .session_recognizer import (
    EXECUTION_RELEVANT_TYPES,
    REACH_CLASSIFICATION,
    recognize_session,
)


def _default_codex_sessions_root():
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    return Path(home) / ".codex" / "sessions"


def _decompress_zstd(data):
    try:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data)
    except ImportError:
        return None
    except Exception:
        return None


def find_session_files(root=None):
    """Walk the Codex sessions tree and yield (path, encoding) tuples."""
    root = Path(root) if root else _default_codex_sessions_root()
    if not root.is_dir():
        return
    for dirpath, _dirs, filenames in os.walk(str(root)):
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if name.endswith(".jsonl.zst"):
                yield path, "zstd"
            elif name.endswith(".jsonl"):
                yield path, "plain"


def read_session_bytes(path, encoding):
    """Read the native provider record bytes, decompressing if needed.
    Returns (raw_bytes, decoded_bytes) where raw_bytes is the
    producer-original file content and decoded_bytes is the
    decompressed JSONL for recognition."""
    raw = path.read_bytes()
    if encoding == "zstd":
        decoded = _decompress_zstd(raw)
        return raw, decoded
    return raw, raw


def extract_session_id_from_filename(path):
    """Extract the session UUID from a Codex session filename.
    Format: rollout-YYYY-MM-DDTHH-MM-SS-<session_id>.jsonl[.zst]"""
    name = path.stem
    if name.endswith(".jsonl"):
        name = name[:-len(".jsonl")]
    parts = name.split("-")
    if len(parts) >= 8:
        uuid_parts = parts[7:]
        return "-".join(uuid_parts)
    return ""


def census(root=None, max_records=None):
    """Produce a complete vocabulary report over the retained corpus.

    Returns a dict:
        total_files
        format_counts: {plain, zstd, unreadable}
        event_type_counts: Counter
        execution_item_type_counts: Counter
        reach_class_counts: Counter
        sessions_with_execution: int
        sessions_with_commands: int
        sessions_with_mcp: int
        sessions_with_collab_agent: int
        sessions_with_terminal: int
        session_id_forms: Counter (number of distinct session_id patterns)
        unknown_event_types: set
        unknown_item_types: set
        file_details: list of per-file summaries (optional, truncated)
    """
    report = {
        "total_files": 0,
        "format_counts": Counter(),
        "event_type_counts": Counter(),
        "execution_item_type_counts": Counter(),
        "reach_class_counts": Counter(),
        "sessions_with_execution": 0,
        "sessions_with_commands": 0,
        "sessions_with_mcp": 0,
        "sessions_with_collab_agent": 0,
        "sessions_with_terminal": 0,
        "unknown_event_types": set(),
        "unknown_item_types": set(),
        "decode_failures": 0,
        "file_details": [],
    }

    for path, encoding in find_session_files(root):
        report["total_files"] += 1
        if max_records and report["total_files"] > max_records:
            break
        report["format_counts"][encoding] += 1

        raw, decoded = read_session_bytes(path, encoding)
        if decoded is None:
            report["format_counts"]["unreadable"] += 1
            report["decode_failures"] += 1
            continue

        recognized = recognize_session(decoded)
        if recognized is None:
            report["decode_failures"] += 1
            continue

        for event_type, count in recognized["item_counts"].items():
            report["event_type_counts"][event_type] += count
            if (event_type not in REACH_CLASSIFICATION
                    and event_type not in ("response_item",)):
                report["unknown_event_types"].add(event_type)

        for item in recognized.get("execution_items", []):
            item_type = item["item_type"]
            report["execution_item_type_counts"][item_type] += 1
            if item_type not in EXECUTION_RELEVANT_TYPES:
                report["unknown_item_types"].add(item_type)

        for reach in recognized.get("reach_observed", set()):
            report["reach_class_counts"][reach] += 1

        if recognized["execution_items"]:
            report["sessions_with_execution"] += 1
        if recognized["command_ids"]:
            report["sessions_with_commands"] += 1
        if any(i["item_type"] == "McpToolCall"
               for i in recognized.get("execution_items", [])):
            report["sessions_with_mcp"] += 1
        if any(i["item_type"] == "CollabAgentToolCall"
               for i in recognized.get("execution_items", [])):
            report["sessions_with_collab_agent"] += 1
        if recognized["terminal"]:
            report["sessions_with_terminal"] += 1

        detail = {
            "path": str(path),
            "encoding": encoding,
            "session_id": recognized["session_id"],
            "model": recognized["model"],
            "line_count": recognized["line_count"],
            "commands": len(recognized["command_ids"]),
            "external_items": len(recognized.get("external_activity", [])),
            "terminal": recognized["terminal"],
            "byte_count": len(raw),
        }
        report["file_details"].append(detail)

    report["unknown_event_types"] = sorted(report["unknown_event_types"])
    report["unknown_item_types"] = sorted(report["unknown_item_types"])
    report["event_type_counts"] = dict(
        sorted(report["event_type_counts"].items()))
    report["execution_item_type_counts"] = dict(
        sorted(report["execution_item_type_counts"].items()))
    report["reach_class_counts"] = dict(
        sorted(report["reach_class_counts"].items()))
    report["format_counts"] = dict(sorted(report["format_counts"].items()))
    return report
