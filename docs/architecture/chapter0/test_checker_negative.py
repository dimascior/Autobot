#!/usr/bin/env python3
"""Negative tests for check_registries.py.

Each case copies the registries into a temporary folder and puts back a defect
that a Chapter 0 draft or review actually had, or a rule the registries must
reject. The checker must then exit 1 with the named check failing; where a case
names a message, the output must also contain it, so the defect counts as caught
only for the right reason. Standard library only. Writes only inside temporary
folders, which are removed afterwards.
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


# Amendment 1 (CORR-55, CORR-56): the intent lifecycle of standalone effects and retries.

def m_no_standalone_creation(d):
    """The staging delete's initiation rule removed: an effect that requires an intent nothing creates (U-44's gap)."""
    t = load(d, "transitions.json")
    t["transitions"] = [x for x in t["transitions"] if x["id"] != "t-g01"]
    save(d, "transitions.json", t)


def m_unnamed_intent(d):
    """The retry mints an EffectIntent without naming the effects it retries (U-47's gap)."""
    t = load(d, "transitions.json")
    tr(t, "t-i06").pop("retry_for")
    save(d, "transitions.json", t)


def m_retry_no_delegation(d):
    """The retry no longer checks the delegation."""
    t = load(d, "transitions.json")
    tr(t, "t-i06")["preconditions"].remove("delegation_active_at_intent")
    save(d, "transitions.json", t)


def m_retry_no_window(d):
    """The retry no longer bounds its intent's window."""
    t = load(d, "transitions.json")
    tr(t, "t-i06")["preconditions"].remove("intent_ttl_gt_hard_timeout")
    save(d, "transitions.json", t)


def m_retry_other_target(d):
    """The retry aimed at a different target than the intent it supersedes."""
    t = load(d, "transitions.json")
    pre = tr(t, "t-i06")["preconditions"]
    pre[pre.index("retry_same_target")] = "retry_any_target"
    save(d, "transitions.json", t)


def m_unsafe_sync_intent(d):
    """A workspace sync whose safety predicate failed still creates an intent."""
    t = load(d, "transitions.json")
    tr(t, "t-w03")["mints"] = ["obj-25"]
    save(d, "transitions.json", t)


def m_standalone_without_intent(d):
    """The staging delete performed straight from RETAINED, with no intent created first."""
    t = load(d, "transitions.json")
    tr(t, "t-g02")["from"] = "RETAINED"
    save(d, "transitions.json", t)


def m_delete_no_cleanup_authority(d):
    """A staging delete intent without the cleanup authority its class checks."""
    t = load(d, "transitions.json")
    tr(t, "t-g01")["preconditions"].remove("cleanup_authority_from_frozen_policy")
    save(d, "transitions.json", t)


def m_skipped_as_proof(d):
    """The workspace sync's HAPPENED path removed: the SKIPPED receipt must not count as proof the sync ran."""
    t = load(d, "transitions.json")
    t["transitions"] = [x for x in t["transitions"] if x["id"] != "t-w02"]
    save(d, "transitions.json", t)


def m_absent_ref_not_deleted(d):
    """A staging ref observed absent treated as a delete that didn't happen, against the frozen remote_delete rule."""
    t = load(d, "transitions.json")
    tr(t, "t-g03")["observation"] = "target_absent"
    save(d, "transitions.json", t)


def m_unknown_leaves_deleting(d):
    """The staging ref leaves DELETING while its intent is still UNKNOWN_OUTCOME."""
    t = load(d, "transitions.json")
    t["transitions"].append({
        "id": "t-g04", "machine": "staging_ref", "from": "DELETING", "to": "RETAINED", "actor": "sequencer",
        "capabilities": ["cap-51"], "requires": [], "mints": [], "on_outcome": "UNKNOWN_OUTCOME",
        "observation": "target_equals_expected_old", "preconditions": ["intent_unknown_outcome"],
    })
    save(d, "transitions.json", t)


def m_standalone_retryable(d):
    """The retry rule made to retry a staging delete, which the registry names as not retryable."""
    t = load(d, "transitions.json")
    tr(t, "t-i06")["retry_for"].append("eff-staging-delete")
    save(d, "transitions.json", t)


def m_standalone_fence(d):
    """The staging delete class made to inherit the attempt-scoped fence check."""
    t = load(d, "transitions.json")
    t["intent_classes"]["classes"]["staging_delete"]["checks"].append(["fence_matches"])
    save(d, "transitions.json", t)


CASES = [
    ("one HALTED state for both kinds of halt (a resume could skip verification)", m_single_halted, ["graph.no_unverified_acceptance"]),
    ("the expected old value read as ABSENT (draft 4's CORR-49)", m_old_value_absent, ["effects.outcome_rules"]),
    ("finalization_class outside the RequirementDigest (before CORR-47)", m_finalization_class_tier, ["identity.tiers"]),
    ("permit reservations minted by the Git sequencer (R5)", m_reservation_minter, ["transitions.minted_by_participant"]),
    ("the Planner given authority over the Registry", m_planner_edge, ["authority.acyclic_and_forbidden"]),
    ("PUBLISHED_UNVERIFIED releasing dependencies", m_unverified_release, ["graph.published_unverified_releases_nothing"]),
    ("a canonical push intent without a delegation check", m_no_delegation_check, ["effects.intent_creation"],
     "t-s32: the intent for eff-canonical-push lacks the attempt_scoped checks [['delegation_active_at_intent']]"),
    ("a second PONR on one path", m_second_ponr, ["ponr.per_path"]),
    ("an invariant with no planned test", m_untested_invariant, ["invariants.testability"]),
    ("a capability with two owners", m_two_owners, ["capabilities.single_owner"]),
    ("receipts stored in HDB (against D-04)", m_receipt_in_hdb, ["objects.storage"]),
    # Amendment 1.
    ("a standalone effect with no intent-creation rule", m_no_standalone_creation, ["effects.intent_closure"],
     "eff-staging-delete: no transition creates its intent"),
    ("an EffectIntent minted without naming its effect", m_unnamed_intent, ["effects.intent_creation"],
     "t-i06: mints obj-25 without naming the effect it is for (intent_for) or the effects it retries (retry_for)"),
    ("a retry without its delegation check", m_retry_no_delegation, ["effects.intent_creation"],
     "t-i06: the intent for eff-local-canonical-cas lacks the attempt_scoped checks [['delegation_active_at_intent']]"),
    ("a retry without its window check", m_retry_no_window, ["effects.intent_creation"],
     "t-i06: the intent for eff-local-canonical-cas lacks the attempt_scoped checks [['intent_ttl_gt_hard_timeout']]"),
    ("a retry aimed at a different target", m_retry_other_target, ["effects.intent_creation"],
     "t-i06: a retry must prove the identical effect and bind the intent it supersedes; it lacks [['retry_same_target']]"),
    ("a failed workspace safety check that still creates an intent", m_unsafe_sync_intent, ["effects.intent_creation"],
     "t-w03: the workspace_sync skip (worktree_safety_predicate_fails) holds, yet it mints an intent"),
    ("a standalone effect performed without an intent", m_standalone_without_intent, ["effects.require_intent"],
     "t-g02: performs eff-staging-delete, but no transition that creates its intent leads into RETAINED"),
    ("a staging delete intent without cleanup authority", m_delete_no_cleanup_authority, ["effects.intent_creation"],
     "t-g01: the intent for eff-staging-delete lacks the staging_delete checks [['cleanup_authority_from_frozen_policy']]"),
    # Your review of amendment 1.
    ("the SKIPPED receipt standing in for the sync's HAPPENED path", m_skipped_as_proof, ["effects.intent_closure"],
     "eff-workspace-sync: no transition reaches its receipt obj-29 from HAPPENED"),
    ("an absent staging ref read as a delete that didn't happen", m_absent_ref_not_deleted, ["effects.intent_closure"],
     "t-g03: leaves DELETING on ABSENT, but under remote_delete the observation target_absent gives HAPPENED"),
    ("a staging ref leaving DELETING while its intent is UNKNOWN_OUTCOME", m_unknown_leaves_deleting, ["effects.intent_closure"],
     "t-g04: leaves DELETING on UNKNOWN_OUTCOME; while its intent is unresolved the staging_ref lifecycle must hold in DELETING"),
    ("a standalone effect made retryable by t-i06", m_standalone_retryable, ["effects.intent_creation"],
     "t-i06: retries eff-staging-delete, which is not retryable by t-i06"),
    ("a standalone class inheriting the attempt-scoped fence check", m_standalone_fence, ["effects.intent_creation"],
     "intent class staging_delete: a standalone effect's class must not require the attempt-scoped checks ['fence_matches']"),
]


def run(folder):
    out = subprocess.run([sys.executable, "-I", str(CHECKER), str(folder)], capture_output=True, text=True)
    failed = {line.split()[1] for line in out.stdout.splitlines() if line.startswith("FAIL")}
    return out.returncode, failed, out.stdout


def main():
    code, failed, _ = run(SRC)
    print(f"baseline registries: exit {code}, failing checks: {sorted(failed) or 'none'}")
    if code != 0:
        print("the baseline must pass before the negative cases mean anything")
        return 1
    caught = 0
    for title, mutate, expected, *needles in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "registries"
            shutil.copytree(SRC, folder)
            mutate(folder)
            code, failed, out = run(folder)
        found = all(n in out for n in needles)
        good = code == 1 and set(expected) <= failed and found
        caught += good
        why = f"; message {'found' if found else 'NOT found'}: {needles[0]!r}" if needles else ""
        print(f"{'CAUGHT' if good else 'MISSED'}  {title}: expected {expected}, failed {sorted(failed)}{why}")
    print()
    print(f"{caught} of {len(CASES)} planted defects caught")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
