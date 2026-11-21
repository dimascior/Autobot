"""Entry point: python -m intelligence.dashboard [options]

Usage:
    python -m intelligence.dashboard                 # live polling (default 3s)
    python -m intelligence.dashboard --once          # single snapshot
    python -m intelligence.dashboard --interval 5    # poll every 5 seconds
    python -m intelligence.dashboard --json          # snapshot as JSON
    python -m intelligence.dashboard --step-log driver_steps.jsonl
"""

import argparse
import json
import sys
from pathlib import Path


def _find_root():
    """Walk up from CWD or this file to find config.toml."""
    for start in (Path.cwd(), Path(__file__).resolve().parents[3]):
        p = start
        for _ in range(6):
            if (p / "config.toml").is_file():
                return p
            p = p.parent
    return Path.cwd()


def main():
    parser = argparse.ArgumentParser(
        prog="intelligence.dashboard",
        description="Live read-only Autobot dashboard")
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: auto-detect)")
    parser.add_argument("--intelligence-db", default=None,
                        help="path to intelligence.db")
    parser.add_argument("--authority-db", default=None,
                        help="path to autobot.db")
    parser.add_argument("--step-log", default=None,
                        help="path to driver step JSONL log")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="poll interval in seconds (default: 3)")
    parser.add_argument("--once", action="store_true",
                        help="print one snapshot and exit")
    parser.add_argument("--json", action="store_true",
                        help="output snapshot as JSON")
    args = parser.parse_args()

    root = args.root or _find_root()
    intel_path = args.intelligence_db or str(root / "intelligence.db")
    auth_path = args.authority_db or str(root / "autobot.db")

    if not Path(intel_path).is_file():
        print("intelligence.db not found at %s" % intel_path,
              file=sys.stderr)
        return 1
    if not Path(auth_path).is_file():
        print("autobot.db not found at %s" % auth_path,
              file=sys.stderr)
        return 1

    if args.json:
        from .readers import IntelligenceReader, AuthorityReader, StepLogReader
        from .model import build_snapshot
        import dataclasses

        intel = IntelligenceReader(intel_path)
        auth = AuthorityReader(auth_path)
        step = StepLogReader(args.step_log) if args.step_log else None
        try:
            snapshot = build_snapshot(intel, auth, step_reader=step)
            print(json.dumps(dataclasses.asdict(snapshot),
                             indent=2, default=str))
        finally:
            intel.close()
            auth.close()
        return 0

    from .cli_stream import stream
    stream(intel_path, auth_path,
           step_log_path=args.step_log,
           interval=args.interval,
           once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
