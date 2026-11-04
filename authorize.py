"""The designated-authority interface for evidence-backed task closure.

The autonomous loop proposes resolutions (resolve.py); it never closes a task.
Closing a task -- or spawning a rework task -- is a *material state change* that
only an explicit authority may enact. This CLI is that authority's hands.

    # review the proposed verdicts with their evidence lineage
    uv run --no-project --python 3.12 python authorize.py --list

    # enact one (the authority identity is recorded on the resolution)
    uv run --no-project --python 3.12 python authorize.py \
        --resolution 7 --authority <authority> --approve
    uv run --no-project --python 3.12 python authorize.py \
        --resolution 7 --authority <authority> --reject
"""

import argparse
import json
import tomllib
from pathlib import Path

from ledger import Ledger


def _ledger_from_config(config_path: Path) -> Ledger:
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    db = cfg["paths"]["ledger_db"]
    db_path = Path(db)
    if not db_path.is_absolute():
        db_path = config_path.parent / db_path
    return Ledger(db_path)


def _print_proposed(ledger: Ledger):
    rows = ledger.proposed_resolutions()
    if not rows:
        print("No proposed resolutions awaiting authority.")
        return
    print(f"\n{len(rows)} proposed resolution(s) awaiting authority:\n")
    for r in rows:
        item = ledger.get_item(r["item_id"]) or {}
        refs = json.loads(r["source_refs"] or "[]")
        hashes = json.loads(r["evidence_hashes"] or "{}")
        print(f"  [resolution {r['id']}] {r['item_id']}  ->  {r['verdict'].upper()}")
        print(f"      task:      {item.get('title', '(unknown)')}")
        print(f"      evaluator: {r['evaluator']}")
        print(f"      rationale: {r['rationale']}")
        print(f"      criteria:  {(r['acceptance_criteria'] or '')[:160]}")
        print(f"      evidence:  {len(refs)} source record(s), {len(hashes)} hash(es)")
        for ref in refs:
            print(f"                 - {ref.get('type')}: "
                  f"{ref.get('path') or ref.get('call_id') or ref.get('dispatch_id')}")
        for key, digest in hashes.items():
            print(f"                 # {digest[:16]}…  {key}")
        effect = {
            "resolved": "APPROVE -> task marked completed (verified closure)",
            "rework": "APPROVE -> new authorized rework task; original superseded",
            "review": "APPROVE -> acknowledged; task stays awaiting_resolution",
        }[r["verdict"]]
        print(f"      on approve: {effect}\n")


def main():
    ap = argparse.ArgumentParser(description="Authority interface for task closure")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--list", action="store_true", help="list proposed resolutions")
    ap.add_argument("--resolution", type=int, help="resolution id to act on")
    ap.add_argument("--authority", help="authority identity (recorded on the record)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true", help="enact the proposed verdict")
    g.add_argument("--reject", action="store_true", help="reject the proposal")
    args = ap.parse_args()

    here = Path(__file__).parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = here / config_path
    ledger = _ledger_from_config(config_path)

    try:
        if args.list or not args.resolution:
            _print_proposed(ledger)
            return
        if not args.authority:
            ap.error("--authority is required to approve or reject")
        if args.approve:
            outcome = ledger.authorize_resolution(args.resolution, args.authority)
            print(f"AUTHORIZED: {json.dumps(outcome, indent=2)}")
        elif args.reject:
            outcome = ledger.reject_resolution(args.resolution, args.authority)
            print(f"REJECTED: {json.dumps(outcome, indent=2)}")
        else:
            ap.error("choose --approve or --reject (or --list)")
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
