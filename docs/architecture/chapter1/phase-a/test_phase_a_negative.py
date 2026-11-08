#!/usr/bin/env python3
"""Negative tests for check_phase_a.py.

Each case copies this draft (the checker, its registries, the document and the generated register) into a
temporary folder, plants one defect the checker must catch, and runs the checker against the same Chapter 0
and HEA inputs. The checker must exit 1 with the named check failing; where a case names a message, the failure
must also contain it, so a defect counts as caught only for the right reason. Chapter 0 cases also copy the Chapter 0
folder and plant the defect in the copy; an unchanged copy is run first as a control. Standard library only; writes only inside temporary folders, which are removed afterwards, and never
touches the committed Chapter 0. Pass the same --ch0, --handoffs and --hea arguments as the checker.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASSTHROUGH = sys.argv[1:]


def load(d, name):
    return json.loads((d / "registries" / name).read_text(encoding="utf-8"))


def save(d, name, data):
    (d / "registries" / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def obj(doc, oid):
    return next(o for o in doc["objects"] if o["id"] == oid)


def vec(doc, vid):
    return next(v for v in doc["vectors"] if v["id"] == vid)


def unknown(doc, uid):
    return next(u for u in doc["new"] if u["id"] == uid)


def chapter0(fn):
    """Marks a mutation that plants its defect in a copy of Chapter 0."""
    fn.ch0 = True
    return fn


def m_minter(d):
    """An inventory row that disagrees with Chapter 0's minter."""
    x = load(d, "inventory.json")
    obj(x, "obj-19")["minter"] = "planner"
    save(d, "inventory.json", x)


def m_tier(d):
    """reuse_policy moved into the RequirementDigest."""
    x = load(d, "identity_fields.json")
    next(f for f in x["fields"] if f["path"] == "reuse_policy")["tier"] = "requirement_digest"
    save(d, "identity_fields.json", x)


def m_u10_rewritten(d):
    """U-10's question silently redefined."""
    x = load(d, "unknowns.json")
    x["inherited"][0]["question"] = "Which Python version verifiers use"
    save(d, "unknowns.json", x)


def m_id_reuse(d):
    """A new question minted under a Chapter 0 unknown ID (HEA's CORR-F defect)."""
    x = load(d, "unknowns.json")
    x["new"][0]["id"] = "U-05"
    save(d, "unknowns.json", x)


def m_hea_as_basis(d):
    """A decision candidate that cites HEA as its basis."""
    x = load(d, "decision_candidates.json")
    x["candidates"][0]["ch0_basis"].append("T-05")
    save(d, "decision_candidates.json", x)


def m_needle(d):
    """A citation whose quoted phrase isn't in the cited lines."""
    x = load(d, "hea_techniques.json")
    x["techniques"][4]["evidence"][0]["needle"] = "unknown fields are accepted"
    save(d, "hea_techniques.json", x)


def m_vector(d):
    """A vector that claims a retry-policy change alters the RequirementDigest."""
    x = load(d, "vectors.json")
    vec(x, "V-C")["expected"]["requirement_digest"] = "different"
    save(d, "vectors.json", x)


def m_ponr(d):
    """The remote PONR receipt without the delegation state (INV-41)."""
    x = load(d, "inventory.json")
    o = obj(x, "obj-28")
    o["common_bindings"] = [b for b in o["common_bindings"] if b["target"] != "delegation_state_at_ponr"]
    save(d, "inventory.json", x)


def m_claim(d):
    """An HEA-derived claim with no HEA technique behind it."""
    x = load(d, "claims.json")
    next(c for c in x["claims"] if c["id"] == "C-16")["refs"] = ["obj-04"]
    save(d, "claims.json", x)


def m_scope(d):
    """The DuplicateAdmissionRecord dropped from Chapter 1 although CH1-duplicate-admission needs it."""
    x = load(d, "inventory.json")
    obj(x, "obj-32")["ch1_scope"] = "form_only"
    save(d, "inventory.json", x)


def m_replaced_invariant(d):
    """A binding justified by the replaced invariant INV-33."""
    x = load(d, "inventory.json")
    obj(x, "obj-01")["invariants"].append("INV-33")
    save(d, "inventory.json", x)


def m_test_link(d):
    """No vector left for CH1-exact-byte-input-hash."""
    x = load(d, "vectors.json")
    for v in x["vectors"]:
        v["tests"] = [t for t in v.get("tests", []) if t != "CH1-exact-byte-input-hash"]
    save(d, "vectors.json", x)


def context(o, **when):
    """The binding context of an object whose when clause has these selector values."""
    return next(g for g in o["conditional_bindings"] if all(g["when"].get(k) == v for k, v in when.items()))


def m_unconditional_ponr(d):
    """Your RV-25 test: the CommitReceipt's delegation state made unconditional, although t-s17 is no point of no
    return and t-s18's effect didn't happen."""
    x = load(d, "inventory.json")
    o = obj(x, "obj-26")
    g = context(o, effect="eff-local-canonical-cas", effect_result="HAPPENED")
    moved = [b for b in g["bindings"] if b["target"] == "delegation_state_at_ponr"]
    g["bindings"] = [b for b in g["bindings"] if b["target"] != "delegation_state_at_ponr"]
    o["common_bindings"] += moved
    save(d, "inventory.json", x)


def m_stale_as_happened(d):
    """Your RV-25 test: t-s18 listed under the HAPPENED rule, as if the stale compare-and-swap had happened."""
    x = load(d, "inventory.json")
    o = obj(x, "obj-26")
    context(o, effect="eff-local-canonical-cas", effect_result="HAPPENED")["provenance"].append("t-s18")
    o["conditional_bindings"] = [g for g in o["conditional_bindings"] if g["when"].get("effect_result") != "EXPECTED_OLD_STALE"]
    save(d, "inventory.json", x)


def m_stale_matches_happened(d):
    """Your RV-25 test, by the when clause: the HAPPENED rule widened to the stale result, so t-s18 matches it."""
    x = load(d, "inventory.json")
    o = obj(x, "obj-26")
    g = context(o, effect="eff-local-canonical-cas", effect_result="HAPPENED")
    g["when"]["effect_result"] = ["HAPPENED", "EXPECTED_OLD_STALE"]
    g["provenance"].append("t-s18")
    o["conditional_bindings"] = [h for h in o["conditional_bindings"] if h["when"].get("effect_result") != "EXPECTED_OLD_STALE"]
    save(d, "inventory.json", x)


def m_transition_selector(d):
    """The SL commit rule selecting on its transitions again, so its bindings would depend on which transition consumes it."""
    x = load(d, "inventory.json")
    g = context(obj(x, "obj-26"), effect="eff-local-canonical-cas", effect_result="HAPPENED")
    g["when"]["transition"] = g.pop("provenance")
    save(d, "inventory.json", x)


def m_provenance_missing(d):
    """t-s20 dropped from the provenance of the rule its receipt falls under."""
    x = load(d, "inventory.json")
    g = context(obj(x, "obj-26"), effect="eff-local-canonical-cas", effect_result="HAPPENED")
    g["provenance"] = [t for t in g["provenance"] if t != "t-s20"]
    save(d, "inventory.json", x)


def m_context_gap(d):
    """The RemoteVerificationReceipt loses its SRPO context, so t-s39 and t-s40 consume it in no context."""
    x = load(d, "inventory.json")
    o = obj(x, "obj-21")
    o["conditional_bindings"] = [g for g in o["conditional_bindings"] if g["when"].get("path") != "SRPO"]
    save(d, "inventory.json", x)


def m_ponr_reservation(d):
    """The SL point of no return's receipt without the reservation t-s16 consumes."""
    x = load(d, "inventory.json")
    g = context(obj(x, "obj-26"), effect="eff-local-canonical-cas", effect_result="HAPPENED")
    g["bindings"] = [b for b in g["bindings"] if b["target"] != "reservation_consumed"]
    save(d, "inventory.json", x)


def m_reuse_conditions(d):
    """reuse_conditions without bindings_equal_or_policy_alternative."""
    x = load(d, "inventory.json")
    f = x["binding_facts"]["reuse_conditions"]
    f["members"] = [m for m in f["members"] if m != "bindings_equal_or_policy_alternative"]
    save(d, "inventory.json", x)


def m_intent_check(d):
    """A canonical-push intent that doesn't bind the reservation its creating transitions check."""
    x = load(d, "inventory.json")
    g = context(obj(x, "obj-25"), effect="eff-canonical-push")
    g["bindings"] = [b for b in g["bindings"] if b["target"] != "reservation_held"]
    save(d, "inventory.json", x)


def m_coupling(d):
    """V-M changes authority_location without the declaration it must match (INV-45)."""
    x = load(d, "vectors.json")
    vec(x, "V-M").pop("coupled_fields")
    save(d, "vectors.json", x)


def m_bundled_decision(d):
    """One candidate deciding timestamps and authority windows together."""
    x = load(d, "decision_candidates.json")
    next(c for c in x["candidates"] if c["key"] == "dc.timestamps")["resolves"].append("U-39")
    save(d, "decision_candidates.json", x)


def m_duplicate_record(d):
    """A duplicate admission that mints a second record."""
    x = load(d, "vectors.json")
    next(p for p in x["predicate_tests"] if p["id"] == "P-2")["outcome"]["duplicate"]["records_minted"] = 1
    save(d, "vectors.json", x)


def m_rejected_basis(d):
    """A coupling resting on the rejected claim C-55."""
    x = load(d, "vectors.json")
    x["couplings"][0]["basis"].append("C-55")
    save(d, "vectors.json", x)


def m_split_orphan(d):
    """A split-off unknown whose parent doesn't list it."""
    x = load(d, "unknowns.json")
    unknown(x, "U-39")["split_from"] = "U-28"
    save(d, "unknowns.json", x)


def m_observation_common(d):
    """The READ_ONLY observation window made a binding of every AcceptanceReceipt."""
    x = load(d, "inventory.json")
    o = obj(x, "obj-30")
    g = context(o, finalization_class="READ_ONLY")
    o["common_bindings"] += g["bindings"]
    g["bindings"] = []
    save(d, "inventory.json", x)


def m_vq_specified(d):
    """V-Q given a tier effect while delegation_id and the bound delegation record may not vary independently."""
    x = load(d, "vectors.json")
    v = vec(x, "V-Q")
    v.update({"status": "tier_effect_specified", "blocked_by": [], "fixture_blocked_by": ["U-37"],
              "expected": {"requirement_digest": "same", "contract_digest": "same", "record_hash": "different"}})
    save(d, "vectors.json", x)


def m_u44_drops_effect(d):
    """U-44 no longer covers eff-workspace-sync, which still has no intent in Chapter 0."""
    x = load(d, "unknowns.json")
    u = unknown(x, "U-44")
    u["covers_effects"] = [e for e in u["covers_effects"] if e != "eff-workspace-sync"]
    save(d, "unknowns.json", x)


def m_u44_stale_cover(d):
    """U-44 claims to cover eff-staging-push, whose intent t-s21 already creates."""
    x = load(d, "unknowns.json")
    unknown(x, "U-44")["covers_effects"].append("eff-staging-push")
    save(d, "unknowns.json", x)


def m_no_created_by(d):
    """The Sequence left with no creation rule, although no transition mints it."""
    x = load(d, "inventory.json")
    obj(x, "obj-08").pop("created_by")
    save(d, "inventory.json", x)


def m_sequence_minted(d):
    """P-2 lists the Sequence among the new admission's mints, attributing it to t-c02."""
    x = load(d, "vectors.json")
    next(p for p in x["predicate_tests"] if p["id"] == "P-2")["outcome"]["new_record"]["minted"].append("obj-08")
    save(d, "vectors.json", x)


def m_narrowed_open(d):
    """U-31 left OPEN after U-45 was split from it."""
    x = load(d, "unknowns.json")
    unknown(x, "U-31")["status"] = "OPEN"
    save(d, "unknowns.json", x)


def c0_load(c, name):
    return json.loads((c / "registries" / name).read_text(encoding="utf-8"))


def c0_save(c, name, data):
    (c / "registries" / name).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def c0_transition(doc, tid):
    return next(t for t in doc["transitions"] if t["id"] == tid)


@chapter0
def c0_no_initiation(c):
    """t-s21 no longer issues the staging-push intent: an effect that requires an intent, with no rule creating one."""
    x = c0_load(c, "transitions.json")
    c0_transition(x, "t-s21").pop("intent_for")
    c0_save(c, "transitions.json", x)


@chapter0
def c0_no_receipt_path(c):
    """t-s22 no longer performs the staging push, so the push's receipt is unreachable from HAPPENED."""
    x = c0_load(c, "transitions.json")
    c0_transition(x, "t-s22").pop("effect")
    c0_save(c, "transitions.json", x)


@chapter0
def c0_consumer_effect(c):
    """t-s20 credited with the effect whose proof it only consumes: it names eff-local-canonical-cas but establishes no result."""
    x = c0_load(c, "transitions.json")
    c0_transition(x, "t-s20")["effect"] = "eff-local-canonical-cas"
    c0_save(c, "transitions.json", x)


@chapter0
def c0_unestablished_way(c):
    """t-s23 also run on SL: a way into COMMITTED from STAGING_PUBLISHING that passes no proof of the commit t-s20 consumes."""
    x = c0_load(c, "transitions.json")
    c0_transition(x, "t-s23")["paths"] = ["SRP", "SL"]
    c0_save(c, "transitions.json", x)


@chapter0
def c0_initiation_unchecked(c):
    """t-s21 without fence_matches: its staging-push intent binds the fence's attempt without checking the fence."""
    x = c0_load(c, "transitions.json")
    t = c0_transition(x, "t-s21")
    t["preconditions"] = [p for p in t["preconditions"] if p != "fence_matches"]
    c0_save(c, "transitions.json", x)


@chapter0
def c0_no_unknown_outcome(c):
    """t-i03 removed: a pending intent can no longer reach UNKNOWN_OUTCOME."""
    x = c0_load(c, "transitions.json")
    x["transitions"] = [t for t in x["transitions"] if t["id"] != "t-i03"]
    c0_save(c, "transitions.json", x)


def m_retry_uncovered(d):
    """U-47 no longer covers t-i06, which still mints an intent outside any initiation rule."""
    x = load(d, "unknowns.json")
    unknown(x, "U-47")["covers_transitions"] = []
    save(d, "unknowns.json", x)


def m_legacy_hash(d):
    """The LegacyRecord's identity set back to a content hash, undoing RV-11."""
    x = load(d, "inventory.json")
    obj(x, "obj-40")["canonical_hash"] = "content_sha256"
    save(d, "inventory.json", x)


def m_floor_unstated(d):
    """V-P without saying the new floor is among the contract's assertions (INV-34)."""
    x = load(d, "vectors.json")
    vec(x, "V-P")["coupling_satisfied"].pop("K-8")
    save(d, "vectors.json", x)


def m_vector_wait(d):
    """V-R no longer waiting on U-24, which is open on reuse_policy."""
    x = load(d, "vectors.json")
    vec(x, "V-R")["fixture_blocked_by"] = ["U-39"]
    save(d, "vectors.json", x)


def m_dangling_note(d):
    """A registry note citing a candidate slug that doesn't exist."""
    x = load(d, "identity_fields.json")
    x["note"] += " See dc.identifiers."
    save(d, "identity_fields.json", x)


def m_pending_citable(d):
    """C-51 made citable although its proposal still waits on dc.version-compatibility."""
    x = load(d, "claims.json")
    next(c for c in x["claims"] if c["id"] == "C-51")["disposition"] = "PARTIALLY_ACCEPTED"
    save(d, "claims.json", x)


@chapter0
def c0_readme_changed(c):
    """Chapter 0's README edited after the freeze: the baseline must cover it."""
    p = c / "README.md"
    p.write_bytes(p.read_bytes() + b"\nedited\n")


CASES = [
    ("an inventory minter that disagrees with Chapter 0", m_minter, ["inventory.coverage"]),
    ("reuse_policy placed in the RequirementDigest", m_tier, ["identity.placement"]),
    ("U-10's question redefined", m_u10_rewritten, ["unknowns.inherited"]),
    ("a new question under a Chapter 0 unknown ID", m_id_reuse, ["unknowns.new"]),
    ("HEA cited as a decision's basis", m_hea_as_basis, ["decisions.candidates"]),
    ("a quoted phrase missing from the cited lines", m_needle, ["hea.citations"]),
    ("a vector whose expectation contradicts the tiers", m_vector, ["vectors.expected"]),
    ("a PONR receipt without the delegation state", m_ponr, ["inventory.ponr_receipts"]),
    ("an HEA-derived claim with no technique", m_claim, ["claims.classification"]),
    ("a needed object dropped from Chapter 1", m_scope, ["inventory.ch1_scope"]),
    ("a replaced invariant cited", m_replaced_invariant, ["inventory.bindings", "invariants.accounting"]),
    ("an inherited Chapter 1 test with no vector", m_test_link, ["tests.ch1"]),
    ("your RV-25 test: obj-26's delegation state made unconditional", m_unconditional_ponr, ["inventory.ponr_receipts"],
     "obj-26: in t-s17 on SRD proving eff-local-sequence-ref HAPPENED, the receipt records no point of no return that happened, "
     "but inherits ['delegation_state_at_ponr']"),
    ("a reuse condition missing", m_reuse_conditions, ["inventory.reuse_conditions"]),
    ("an intent not binding what its creating transition checks", m_intent_check, ["inventory.intents"]),
    ("a vector changing one field of a coupled pair", m_coupling, ["vectors.couplings"]),
    ("one decision resolving two unknowns", m_bundled_decision, ["decisions.candidates"]),
    ("a duplicate admission minting a record", m_duplicate_record, ["predicates.duplicate"]),
    ("a rejected claim used as a basis", m_rejected_basis, ["claims.normative"]),
    ("a split unknown with no matching parent", m_split_orphan, ["unknowns.new"]),
    ("a READ_ONLY binding made universal", m_observation_common, ["inventory.conditions"]),
    ("V-Q given a tier effect before U-37 is decided", m_vq_specified, ["vectors.couplings"]),
    # Your third review.
    ("Chapter 0: an effect that requires an intent, with no intent-creation rule", c0_no_initiation, ["effects.intent_closure"],
     "eff-staging-push: no initiation rule mints obj-25 for it"),
    ("Chapter 0: an effect's receipt unreachable from HAPPENED", c0_no_receipt_path, ["effects.intent_closure"],
     "eff-staging-push: no transition reaches its receipt obj-27 from HAPPENED"),
    ("Chapter 0: no path from PENDING to UNKNOWN_OUTCOME", c0_no_unknown_outcome, ["effects.intent_closure"],
     "can't reach ['UNKNOWN_OUTCOME'] from PENDING"),
    ("a standalone effect's gap no longer covered by U-44", m_u44_drops_effect, ["effects.intent_closure"],
     "eff-workspace-sync: no initiation rule mints obj-25 for it"),
    ("U-44 covering an effect that already closes", m_u44_stale_cover, ["effects.intent_closure"],
     "covers eff-staging-push, which already closes in Chapter 0"),
    ("the Sequence with no creation rule", m_no_created_by, ["inventory.creation"],
     "obj-08: the sequencer mints it, no Chapter 0 transition does"),
    ("the Sequence listed among t-c02's mints", m_sequence_minted, ["predicates.duplicate"],
     "no Chapter 0 transition mints the Sequence"),
    ("a split parent left OPEN instead of NARROWED", m_narrowed_open, ["unknowns.new"],
     "U-31: facets were split from it, so its status must be NARROWED"),
    # The independent review.
    ("a retry intent no longer covered by U-47", m_retry_uncovered, ["effects.intent_closure", "inventory.intents"],
     "t-i06: mints obj-25 without naming the effect it is for",
     "obj-25: the retry intents t-i06 mints for eff-canonical-push fall under that effect's rule and bind "
     "['attempt_identity', 'delegation_active_at_intent', 'intent_window'], which t-i06 doesn't check"),
    ("the LegacyRecord's identity set back to a content hash", m_legacy_hash, ["inventory.hash_forms"],
     "obj-40: hash form content_sha256 doesn't fit legacy_read_only"),
    ("a delegation change that doesn't state its floor containment", m_floor_unstated, ["vectors.couplings"],
     "V-P: changes ['resolved_bindings.mandatory_floor'] without its coupled field (coupling K-8)"),
    ("a vector not waiting on an unknown open on its field", m_vector_wait, ["vectors.status"],
     "V-R: also waits on ['U-24']"),
    ("a registry note citing a slug that doesn't exist", m_dangling_note, ["docs.consistency"],
     "identity_fields.json cites dc.identifiers, which doesn't exist"),
    ("a pending proposal made citable", m_pending_citable, ["claims.classification"],
     "C-51: disposition PARTIALLY_ACCEPTED doesn't fit verified partial"),
    ("Chapter 0: the README changed after the freeze", c0_readme_changed, ["baseline.chapter0"],
     "docs/architecture/chapter0/README.md differs from the committed Chapter 0"),
    # Your decision on RV-25.
    ("your RV-25 test: t-s18 listed under the HAPPENED rule", m_stale_as_happened, ["inventory.provenance", "inventory.contexts"],
     "obj-26: t-s18 is in the provenance of a rule conditioned on effect_result HAPPENED, but Chapter 0 gives it ['EXPECTED_OLD_STALE']"),
    ("a consuming transition left with no binding rule", m_context_gap, ["inventory.contexts"],
     "obj-21: t-s39 on SRPO matches 0 rules in the partition ['path']; it must resolve to exactly one"),
    ("a point-of-no-return receipt without the reservation it consumes", m_ponr_reservation, ["inventory.ponr_receipts"],
     "obj-26: in t-s16 on SL proving eff-local-canonical-cas HAPPENED, the receipt records the point of no return t-s16, "
     "so it must bind ['reservation_consumed']",
     "obj-26: in t-s20 on SL consuming the receipt t-s16 established (eff-local-canonical-cas HAPPENED), the receipt records "
     "the point of no return t-s16, so it must bind ['reservation_consumed']"),
    # Your review of RV-48.
    ("your RV-25 test by the when clause: the HAPPENED rule widened so t-s18 matches it", m_stale_matches_happened, ["inventory.ponr_receipts"],
     "obj-26: in t-s18 on SL consuming the receipt of eff-local-canonical-cas EXPECTED_OLD_STALE, the receipt records no point of "
     "no return that happened, but inherits ['delegation_state_at_ponr', 'reservation_consumed']"),
    ("a rule selecting on the transitions that consume its receipt", m_transition_selector, ["inventory.provenance"],
     "obj-26: transition is provenance, not a when selector"),
    ("a consumer missing from the provenance of its receipt's rule", m_provenance_missing, ["inventory.provenance"],
     "obj-26: t-s20 on SL consuming the receipt t-s16 established (eff-local-canonical-cas HAPPENED) falls under the rule"),
    ("Chapter 0: t-s20 credited with the effect it only consumes the proof of", c0_consumer_effect, ["inventory.provenance"],
     "obj-26: t-s20 names eff-local-canonical-cas, whose receipt it requires, but establishes no result for it"),
    # The review before freeze.
    ("Chapter 0: a way into a consumer's state that passes no proof", c0_unestablished_way, ["inventory.provenance"],
     "a way into COMMITTED on SL starts at STAGING_PUBLISHING and passes no transition that establishes the receipt"),
    ("Chapter 0: an initiation rule's intent binding what the rule doesn't check", c0_initiation_unchecked, ["inventory.intents"],
     "obj-25: the intents t-s21 mints for eff-staging-push fall under that effect's rule and bind ['attempt_identity'], "
     "which t-s21 doesn't check"),
]


def ch0_source():
    if "--ch0" in PASSTHROUGH:
        return Path(PASSTHROUGH[PASSTHROUGH.index("--ch0") + 1])
    raise SystemExit("pass --ch0 <chapter0 folder>")


def with_ch0(path):
    out = list(PASSTHROUGH)
    out[out.index("--ch0") + 1] = str(path)
    return out


def copy_draft(tmp):
    d = Path(tmp) / "phase-a"
    shutil.copytree(HERE / "registries", d / "registries")
    for name in ("check_phase_a.py", "CHAPTER1_PHASE_A.md", "CHAPTER1_PHASE_A_REGISTERS.md"):
        if (HERE / name).is_file():
            shutil.copy2(HERE / name, d / name)
    return d


def copy_ch0(tmp):
    src, c = ch0_source(), Path(tmp) / "chapter0"
    shutil.copytree(src, c, ignore=shutil.ignore_patterns("__pycache__"))
    return c


def run(d, args=None):
    out = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(d / "check_phase_a.py"), *(PASSTHROUGH if args is None else args)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    failed = {line.split()[1].rstrip(":") for line in out.stdout.splitlines() if line.startswith("FAIL")}
    return out.returncode, failed, out.stdout


def main():
    with tempfile.TemporaryDirectory() as tmp:
        code, failed, _ = run(copy_draft(tmp))
    print(f"baseline draft: exit {code}, failing checks: {sorted(failed) or 'none'}")
    with tempfile.TemporaryDirectory() as tmp:
        code0, failed0, _ = run(copy_draft(tmp), with_ch0(copy_ch0(tmp)))
    print(f"control, unchanged copy of Chapter 0: exit {code0}, failing checks: {sorted(failed0) or 'none'}")
    if code != 0 or code0 != 0:
        print("the baseline and the control must pass before the negative cases mean anything")
        return 1
    caught = 0
    for title, mutate, expected, *needle in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            d = copy_draft(tmp)
            if getattr(mutate, "ch0", False):
                c = copy_ch0(tmp)
                mutate(c)
                code, failed, out = run(d, with_ch0(c))
            else:
                mutate(d)
                code, failed, out = run(d)
        good = code == 1 and set(expected) <= failed and all(n in out for n in needle)
        caught += good
        why = f"; message {'found' if all(n in out for n in needle) else 'NOT found'}: {needle[0]!r}" if needle else ""
        print(f"{'CAUGHT' if good else 'MISSED'}  {title}: expected {expected}, failed {sorted(failed)}{why}")
    print()
    print(f"{caught} of {len(CASES)} planted defects caught")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
