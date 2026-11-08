#!/usr/bin/env python3
"""Negative tests for check_registries.py.

Each case copies the registries into a temporary folder and puts back a defect
that a Chapter 0 draft or review actually had, or a rule the registries must
reject. The checker must then exit 1 with the named check failing. Standard
library only. Writes only inside temporary folders, which are removed afterwards.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "registries"
CHECKER = HERE / "check_registries.py"


def load(d, name):
    return json.loads((d / name).read_text(encoding="utf-8"))


def save(d, name, data):
    (d / name).write_text(json.dumps(data, indent=1), encoding="utf-8")


def tr(doc, tid):
    return next(t for t in doc["transitions"] if t["id"] == tid)


def m_single_halted(d):
    """Drafts 3-4: one HALTED state, resumed into publishing after a lease expiry."""
    st = load(d, "states.json")
    seq = st["machines"]["sequence"]
    seq["states"] = [s for s in seq["states"] if s["id"] != "HALTED_AFTER_VERIFICATION"]
    save(d, "states.json", st)
    t = load(d, "transitions.json")
    tr(t, "t-s28")["to"] = "HALTED"
    tr(t, "t-s29")["from"] = "HALTED"
    tr(t, "t-s30")["from"] = "HALTED"
    for p in t["paths"]:
        p["side_pre_ponr"] = [s for s in p["side_pre_ponr"] if s != "HALTED_AFTER_VERIFICATION"]
    save(d, "transitions.json", t)


def m_old_value_absent(d):
    """Draft 4's CORR-49: a grace-period read at the old value counted as absence."""
    t = load(d, "transitions.json")
    t["outcome_rules"]["remote_cas"]["target_equals_expected_old"] = "ABSENT"
    save(d, "transitions.json", t)


def m_finalization_class_tier(d):
    """Before CORR-47: finalization_class sat outside the RequirementDigest."""
    ident = load(d, "identity.json")
    req, con = ident["tiers"][0], ident["tiers"][1]
    req["fields"].remove("finalization_class")
    con["fields"].append("finalization_class")
    save(d, "identity.json", ident)


def m_reservation_minter(d):
    """R5: the Git sequencer minted permit-use records the Sequencer's transitions create."""
    o = load(d, "objects.json")
    next(x for x in o["objects"] if x["id"] == "obj-24")["minter"] = "git_sequencer"
    save(d, "objects.json", o)


def m_planner_edge(d):
    """The LLM Planner given authority over the Registry (CORR-15 forbids it)."""
    e = load(d, "authority_edges.json")
    e["authority_edges"].append({"from": "planner", "to": "registry", "label": "freeze", "topology": "both"})
    save(d, "authority_edges.json", e)


def m_unverified_release(d):
    """PUBLISHED_UNVERIFIED releasing dependencies (CORR-38, CORR-53 forbid it)."""
    t = load(d, "transitions.json")
    t["transitions"].append({
        "id": "t-bad", "machine": "sequence", "from": "PUBLISHED_UNVERIFIED", "to": "DEPENDENCIES_RELEASED",
        "actor": "sequencer", "capabilities": ["cap-45"], "requires": [], "mints": ["obj-35"],
        "preconditions": [], "paths": ["SRPO"], "ponr": False,
    })
    save(d, "transitions.json", t)


def m_no_delegation_check(d):
    """A canonical push intent issued without checking the delegation (C27, CORR-33)."""
    t = load(d, "transitions.json")
    tr(t, "t-s32")["preconditions"].remove("delegation_active_at_intent")
    save(d, "transitions.json", t)


def m_second_ponr(d):
    """Two points of no return on one path: the staging push marked canonical."""
    t = load(d, "transitions.json")
    tr(t, "t-s22")["ponr"] = True
    save(d, "transitions.json", t)


def m_untested_invariant(d):
    """An invariant with no planned test."""
    i = load(d, "invariants.json")
    next(x for x in i["invariants"] if x["id"] == "INV-42")["tests"] = []
    save(d, "invariants.json", i)


def m_two_owners(d):
    """A capability with two owners."""
    c = load(d, "capabilities.json")
    next(x for x in c["capabilities"] if x["id"] == "cap-43")["owner"] = ["sequencer", "you"]
    save(d, "capabilities.json", c)


def m_receipt_in_hdb(d):
    """Receipts stored in HDB, against D-04."""
    o = load(d, "objects.json")
    o["storage_by_class"]["receipt"] = "hdb"
    save(d, "objects.json", o)


CASES = [
    ("one HALTED state for both kinds of halt (a resume could skip verification)", m_single_halted, ["graph.no_unverified_acceptance"]),
    ("the expected old value read as ABSENT (draft 4's CORR-49)", m_old_value_absent, ["effects.outcome_rules"]),
    ("finalization_class outside the RequirementDigest (before CORR-47)", m_finalization_class_tier, ["identity.tiers"]),
    ("permit reservations minted by the Git sequencer (R5)", m_reservation_minter, ["transitions.minted_by_participant"]),
    ("the Planner given authority over the Registry", m_planner_edge, ["authority.acyclic_and_forbidden"]),
    ("PUBLISHED_UNVERIFIED releasing dependencies", m_unverified_release, ["graph.published_unverified_releases_nothing"]),
    ("a canonical push intent without a delegation check", m_no_delegation_check, ["effects.require_intent"]),
    ("a second PONR on one path", m_second_ponr, ["ponr.per_path"]),
    ("an invariant with no planned test", m_untested_invariant, ["invariants.testability"]),
    ("a capability with two owners", m_two_owners, ["capabilities.single_owner"]),
    ("receipts stored in HDB (against D-04)", m_receipt_in_hdb, ["objects.storage"]),
]


def run(folder):
    out = subprocess.run([sys.executable, "-I", str(CHECKER), str(folder)], capture_output=True, text=True)
    failed = {line.split()[1] for line in out.stdout.splitlines() if line.startswith("FAIL")}
    return out.returncode, failed


def main():
    code, failed = run(SRC)
    print(f"baseline registries: exit {code}, failing checks: {sorted(failed) or 'none'}")
    if code != 0:
        print("the baseline must pass before the negative cases mean anything")
        return 1
    caught = 0
    for title, mutate, expected in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "registries"
            shutil.copytree(SRC, folder)
            mutate(folder)
            code, failed = run(folder)
        good = code == 1 and set(expected) <= failed
        caught += good
        print(f"{'CAUGHT' if good else 'MISSED'}  {title}: expected {expected}, failed {sorted(failed)}")
    print()
    print(f"{caught} of {len(CASES)} planted defects caught")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
