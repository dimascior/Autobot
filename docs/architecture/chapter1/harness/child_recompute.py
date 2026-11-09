"""Cross-process oracle child (D-58 oracle 5): a fresh interpreter recomputes
the golden digests from the written fixtures and prints them as JSON.
Usage: python -I child_recompute.py <src_dir> <fixtures_dir>
"""

import json
import sys
from pathlib import Path


def main():
    src_dir, fixtures_dir = sys.argv[1], sys.argv[2]
    sys.path.insert(0, src_dir)
    from contract_library import compute_tiers, object_hash

    fixtures_path = Path(fixtures_dir)
    graph = json.loads((fixtures_path / "graph.json").read_text(encoding="utf-8"))
    base = json.loads((fixtures_path / "va_record.json").read_text(encoding="utf-8"))
    payloads = json.loads((fixtures_path / "vector_payloads.json").read_text(encoding="utf-8"))

    out = {"object_hashes": {name: object_hash(p["kind"], p) for name, p in graph.items()}}
    rd, cd, rh = compute_tiers(base)
    out["V-A"] = {"requirement_digest": rd, "contract_digest": cd, "record_hash": rh}
    for vid, payload in payloads.items():
        vrd, vcd, vrh = compute_tiers(payload)
        out[vid] = {"requirement_digest": vrd, "contract_digest": vcd, "record_hash": vrh}
    json.dump(out, sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()
