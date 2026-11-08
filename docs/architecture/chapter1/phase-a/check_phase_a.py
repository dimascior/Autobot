#!/usr/bin/env python3
"""Checks the Autobot Chapter 1 Phase A draft against Chapter 0 and the HEA corpus.

Reads this folder's registries/ and its documents, the Chapter 0 folder (--ch0: the folder holding
registries/ and CHAPTER0_ARCHITECTURE.md), the Chapter 0 handoff folder (--handoffs), and the HEA
extract (--hea: the folder holding docs/contracts and docs/implementation/handoffs at f439f52).
Optional --evidence: the Helios evidence folder, to confirm cited gates ran. Standard library only.
Writes nothing, except that --render regenerates CHAPTER1_PHASE_A_REGISTERS.md. Prints one line per
check and exits 1 if any check fails.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REG = HERE / "registries"
DOC = HERE / "CHAPTER1_PHASE_A.md"
REGISTER = HERE / "CHAPTER1_PHASE_A_REGISTERS.md"

ap = argparse.ArgumentParser()
ap.add_argument("--ch0", required=True)
ap.add_argument("--handoffs", required=True)
ap.add_argument("--hea", required=True)
ap.add_argument("--evidence")
ap.add_argument("--render", action="store_true")
args = ap.parse_args()
CH0 = Path(args.ch0)
CH0REG = CH0 / "registries"
HAND = Path(args.handoffs)
HEA = Path(args.hea)


def load(p):
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# Phase A registries.
INV_A = load(REG / "inventory.json")
IDF = load(REG / "identity_fields.json")
VEC = load(REG / "vectors.json")
CLM = load(REG / "claims.json")
HEAT = load(REG / "hea_techniques.json")
UNK = load(REG / "unknowns.json")
DC = load(REG / "decision_candidates.json")
RV = load(REG / "review.json")

# Chapter 0.
O0 = load(CH0REG / "objects.json")
ID0 = load(CH0REG / "identity.json")
RG0 = load(CH0REG / "registrations.json")
IV0 = load(CH0REG / "invariants.json")
D0 = load(CH0REG / "decisions.json")
U0 = load(CH0REG / "unknowns.json")
T0 = load(CH0REG / "transitions.json")
S0 = load(CH0REG / "states.json")
C0 = load(CH0REG / "corrections.json")
CAP0 = load(CH0REG / "capabilities.json")
ARCH = (CH0 / "CHAPTER0_ARCHITECTURE.md").read_text(encoding="utf-8")
CLOSURE = (CH0 / "CHAPTER0_CLOSURE.md").read_text(encoding="utf-8")
ARCH_SECTIONS = {f"ARCHITECTURE §{m.group(1)}": m.group(0)
                 for m in re.finditer(r"^## (\d+)\. .*?(?=^## |\Z)", ARCH, re.S | re.M)}
CLOSURE_SECTIONS = {f"CLOSURE §{n}" for n in re.findall(r"^## (\d+)\. ", CLOSURE, re.M)}

OBJ0 = {o["id"]: o for o in O0["objects"]}
INV0 = {i["id"]: i for i in IV0["invariants"]}
INV_REPLACED = {i for i, d in INV0.items() if d["status"] == "replaced"}
CORR_IDS = {c["id"] for c in C0["corrections"]}
D_IDS = {d["id"] for d in D0["decisions"]}
U0_IDS = {u["id"] for u in U0["unknowns"]}
TRANS = {t["id"]: t for t in T0["transitions"] + T0["wildcards"]}
EFFECTS = {e["id"]: e for e in T0["effects"]}
CAP_IDS = {c["id"] for c in CAP0["capabilities"]}
REG_IDS = {r["id"] for r in RG0["registrations"]}
RULES = {f"R-{n}" for n in re.findall(r"\*\*R-(\d+)\*\*", ARCH)}
PATHS0 = {p["id"]: p for p in T0["paths"]}
GROUPS = T0["path_groups"]
CH0_FILES = {"objects.json", "identity.json", "registrations.json", "invariants.json", "decisions.json", "unknowns.json",
             "transitions.json", "states.json", "principals.json", "capabilities.json", "corrections.json", "authority_edges.json"}
TIER_ORDER = [t["id"] for t in ID0["tiers"]]
TIER_OF = {f: t["id"] for t in ID0["tiers"] for f in t["fields"]}
FINALIZATION_CLASSES = {"READ_ONLY", "SOURCE_MODIFYING"}
OUTCOMES = {"HAPPENED", "ABSENT", "UNKNOWN_OUTCOME"}
# The actors of Chapter 0's transitions: an object one of them mints is minted by a transition or by a stated lifecycle rule.
TRANSITION_ACTORS = {t["actor"] for t in T0["transitions"] + T0["wildcards"]} - {"you"}

TECH = {t["id"]: t for t in HEAT["techniques"]}
NEW_U = {u["id"]: u for u in UNK["new"]}
ALL_U = U0_IDS | set(NEW_U)
VECTORS = {v["id"]: v for v in VEC["vectors"]}
PREDS = {p["id"]: p for p in VEC["predicate_tests"]}
COUPLINGS = {c["id"]: c for c in VEC["couplings"]}
CANDS = {c["key"]: c for c in DC["candidates"]}
CLAIMS = {c["id"]: c for c in CLM["claims"]}
REVIEW = {r["id"]: r for r in RV["findings"]}
OBJS = INV_A["objects"]
OBJ_A = {o["id"]: o for o in OBJS}
FIELDS = IDF["fields"]
FIELD_PATHS = {f["path"] for f in FIELDS}
OPEN_OF = {f["path"]: set(f["open"]) for f in FIELDS}
ALWAYS = set(VEC.get("fixture_waits_for_all", []))

TOKEN = re.compile(r"\b(obj-\d{2}|INV-\d{2}[ab]?|CORR-\d{2}|D-\d{2}b?|U-\d{2}|[tw]-[a-z]\d{2}[a-z]?|cap-\d{2}|reg-[a-z]+|R-\d{1,2}|T-\d{2}|V-[A-Z]|P-\d|C-\d{2}|K-\d{1,2}|RV-\d{2}|dc\.[a-z][a-z-]*[a-z])\b")
ANCHOR = re.compile(r"^(CLOSURE §[1-7]|ARCHITECTURE §([1-9]|1[0-7])|ARCHITECTURE status|README)$")
GATE = re.compile(r"^gate (\d{8}-[a-z0-9-]+)$")
CH0_KIND = re.compile(r"^(obj-|INV-|CORR-|D-|[tw]-|cap-|reg-|R-)")


def anchor_exists(ref):
    """A section anchor names a heading that exists in the committed Chapter 0 document."""
    if ref.startswith("ARCHITECTURE §"):
        return ref in ARCH_SECTIONS
    if ref.startswith("CLOSURE §"):
        return ref in CLOSURE_SECTIONS
    if ref == "ARCHITECTURE status":
        return "**Status:**" in ARCH
    return ref == "README" and (CH0 / "README.md").is_file()


def exists(ref):
    """Whether a reference names something that exists. Unknown kinds of reference fail."""
    if ref in CH0_FILES:
        return True
    if ANCHOR.match(ref):
        return anchor_exists(ref)
    m = GATE.match(ref)
    if m:
        if not args.evidence:
            return True
        corr = m.group(1)
        if (Path(args.evidence) / f"{corr[:8]}-{corr}.gate.json").is_file():
            return True
        return any(Path(args.evidence).glob(f"*-{corr}.gate.json"))
    kinds = [
        (r"^obj-\d{2}$", OBJ0), (r"^INV-\d{2}[ab]?$", INV0), (r"^CORR-\d{2}$", CORR_IDS), (r"^D-\d{2}b?$", D_IDS),
        (r"^U-\d{2}$", ALL_U), (r"^[tw]-[a-z]\d{2}[a-z]?$", TRANS), (r"^cap-\d{2}$", CAP_IDS), (r"^reg-[a-z]+$", REG_IDS),
        (r"^R-\d{1,2}$", RULES), (r"^T-\d{2}$", TECH), (r"^V-[A-Z]$", VECTORS), (r"^P-\d$", PREDS), (r"^K-\d{1,2}$", COUPLINGS),
        (r"^C-\d{2}$", CLAIMS), (r"^RV-\d{2}$", REVIEW), (r"^dc\.[a-z-]+$", CANDS),
    ]
    for pattern, ids in kinds:
        if re.match(pattern, ref):
            return ref in ids
    return False


def text_refs(text):
    return TOKEN.findall(text or "")


def is_ch0_ref(ref):
    return ref in CH0_FILES or bool(ANCHOR.match(ref)) or bool(CH0_KIND.match(ref)) or (ref.startswith("U-") and ref in U0_IDS)


def expand(paths):
    out = set()
    for p in paths:
        out |= set(GROUPS.get(p, [p]))
    return out


# The binding grammar: common_bindings, plus conditional_bindings: rules whose when clauses AND their selectors. A rule's
# provenance names the Chapter 0 transitions it covers and never decides whether it holds.
SELECTORS = set(INV_A["binding_conditions"]["selectors"])
RESULTS = {k: v for k, v in INV_A["effect_results"].items() if k != "note"}
PONR_ONLY = set(INV_A["ponr_only_bindings"]["members"])
RECEIPT_CLASSES = {"receipt", "non_authoritative_receipt"}
OLD_BINDING_KEYS = ["bindings_by_path", "bindings_by_effect", "bindings_by_kind", "bindings_by_finalization_class",
                    "bindings_by_outcome", "open_bindings", "must_bind"]
RULE_KEYS = {"when", "provenance", "bindings", "open"}
EFFECTS_OF = {}
for _e, _d in EFFECTS.items():
    EFFECTS_OF.setdefault(_d["receipt"], set()).add(_e)
# What a transition that creates an intent must check for each binding the intent gets.
INTENT_SUPPORT = {"attempt_identity": {"fence_matches"}, "reservation_held": {"permit_reserved", "reservation_held"},
                  "delegation_active_at_intent": {"delegation_active_at_intent"},
                  "intent_window": {"intent_ttl_gt_hard_timeout"}}  # INV-67's enforcement point


def as_list(v):
    return v if isinstance(v, list) else [v]


def groups(o):
    return o.get("conditional_bindings", [])


def provenance(g):
    return as_list(g.get("provenance", []))


def situated(o):
    """Whether the checker derives the object's Chapter 0 contexts: receipts and intents."""
    return o["ch0_class"] in RECEIPT_CLASSES or o["ch0_class"] == "intent"


def sits_of(o):
    return situations(o) if o["ch0_class"] in RECEIPT_CLASSES else intent_situations(o)


def bindings(o):
    """(condition, when, binding) for every binding of an object."""
    out = [("common", None, b) for b in o.get("common_bindings", [])]
    for g in groups(o):
        out += [("when", json.dumps(g.get("when", {}), sort_keys=True), b) for b in g.get("bindings", [])]
    return out


def touches(t, oid):
    return oid in t.get("mints", []) or oid in t.get("requires", []) or EFFECTS.get(t.get("effect", ""), {}).get("receipt") == oid


def contexts(oid):
    """Transitions that mint the object, prove the effect it is the receipt of, or require it."""
    return [t for t in TRANS.values() if touches(t, oid)]


def matches(when, s):
    """Whether a when clause holds for the record in situation s. Only selectors decide: every one must hold (AND), and a
    list of values means any of them. A key that isn't a selector, such as transition, holds for no record."""
    for key, val in when.items():
        if key not in SELECTORS:
            return False
        allowed = expand(as_list(val)) if key == "path" else set(as_list(val))
        if s.get(key) not in allowed:
            return False
    return True


def applicable(o, s):
    """The targets an object binds in situation s: its common bindings plus those of every context that matches."""
    out = {b["target"] for b in o.get("common_bindings", [])}
    for g in groups(o):
        if matches(g.get("when", {}), s):
            out |= {b["target"] for b in g.get("bindings", [])}
    return out


def open_contexts(o, key, value):
    """The unknowns of the open contexts whose when clause names this selector value."""
    return {u for g in groups(o) if value in as_list(g.get("when", {}).get(key, [])) for u in g.get("open", [])}


def result_of(t, oid):
    """The effect result a transition that requires the receipt establishes, from its own preconditions, or None."""
    if oid not in t.get("requires", []):
        return None
    pre = set(t.get("preconditions", []))
    hits = sorted(name for name, r in RESULTS.items() if pre & set(r["established_by"]))
    return hits[0] if len(hits) == 1 else None


def upstream(t, p, stop):
    """Transitions of t's machine, on path p, that lead into t's from-state, searching back until one satisfies stop."""
    found, seen, todo = [], set(), [t.get("from")]
    while todo:
        state = todo.pop()
        if state is None or state in seen:
            continue
        seen.add(state)
        for u in TRANS.values():
            if u.get("machine") != t.get("machine") or u.get("to") != state or "from" not in u:
                continue
            if p is not None and p not in expand(u.get("paths", [])):
                continue
            if stop(u):
                found.append(u)
            else:
                todo.append(u["from"])
    return found


def dead_ends(t, p, stop):
    """The states where a way into t's from-state on path p begins without passing a transition that satisfies stop:
    states the backward search reaches that have no way in. upstream() finds the transitions that satisfy stop on some
    way in; an empty result here proves every way in passes one."""
    out, seen, todo = [], set(), [t.get("from")]
    while todo:
        state = todo.pop()
        if state is None or state in seen:
            continue
        seen.add(state)
        ins = [u for u in TRANS.values() if u.get("machine") == t.get("machine") and u.get("to") == state and "from" in u
               and (p is None or p in expand(u.get("paths", [])))]
        if not ins:
            out.append(state)
        todo += [u["from"] for u in ins if not stop(u)]
    return sorted(out)


def establishes(t, oid):
    """Whether t's own preconditions establish what happened to the effect the receipt records (t-s16, t-s17, t-s18)."""
    return result_of(t, oid) is not None


def proves(t, oid):
    """Whether t names, as its own effect, the effect whose receipt oid is."""
    return EFFECTS.get(t.get("effect", ""), {}).get("receipt") == oid


def intents_upstream(t, p, oid):
    """The effects of the intents created upstream of t on path p, for the effects whose receipt oid is."""
    effs = EFFECTS_OF.get(oid, set())
    return sorted({u["intent_for"] for u in upstream(t, p, lambda u: u.get("intent_for") in effs)})


def receipt_facts(u, p, oid):
    """(effect, effect_result) pairs a receipt records when u establishes it on path p: the effect u proves, or else the
    effect of the intent upstream. t-s18 names no effect; it consumes the receipt of the compare-and-swap t-s15a's intent was for."""
    r = result_of(u, oid)
    if proves(u, oid):
        return [(u["effect"], r)]
    return [(e, r) for e in intents_upstream(u, p, oid)] or [(None, r)]


def situations(o):
    """Every Chapter 0 context a receipt appears in: one per transition that mints, proves or consumes it, and per path.
    A situation keeps the transition apart from the record:
      transition, role (mints, proves or consumes), transition_effect and transition_ponr describe the transition itself;
      path, effect, effect_result, finalization_class and at_ponr are the record's facts, the only ones rules select on,
      and established_by names the transitions that established them.
    A transition proves an effect only where Chapter 0 names it as its own. A consumer that names none takes the record's
    facts from the transition upstream that established them: t-s20 consumes the receipt t-s16 established, so it sees
    eff-local-canonical-cas HAPPENED at the point of no return t-s16, and is credited with neither. Facts Chapter 0 leaves
    open, such as the finalization class off the execution paths, are enumerated."""
    oid = o["id"]
    effs = EFFECTS_OF.get(oid, set())
    minters = [t for t in TRANS.values() if oid in t.get("mints", [])]
    out = []
    for t in contexts(oid):
        paths = sorted(expand(t.get("paths", []))) or [None]
        # A consumer outside the Sequence machine can see the receipt from any path a transition mints it on.
        outside = paths == [None] and bool(minters) and t.get("machine") != "sequence"
        if outside:
            paths = sorted({q for m in minters for q in expand(m.get("paths", []))})
        role = "mints" if oid in t.get("mints", []) else "proves" if proves(t, oid) else "consumes"
        own = {"transition": t["id"], "role": role, "transition_effect": t.get("effect"), "transition_ponr": bool(t.get("ponr"))}
        for p in paths:
            # unestablished: (state, start) where a way into that state begins at start without passing the transition
            # the derivation relies on, which would leave the record's facts unproved on that way.
            if effs:
                if establishes(t, oid):
                    sources, gaps = [[t]], []
                else:
                    sources = [[u] for u in upstream(t, p, lambda u: establishes(u, oid))] or [[]]
                    gaps = [(t["from"], x, "establishes the receipt") for x in dead_ends(t, p, lambda u: establishes(u, oid))]
                for src in sources:
                    for e, r in [f for u in src for f in receipt_facts(u, p, oid)] or [(None, None)]:
                        need_intent = bool(e) and EFFECTS[e].get("requires_intent")
                        no_intent = [(u["from"], x, f"creates an intent for {e}") for u in src if need_intent
                                     for x in dead_ends(u, p, lambda v: v.get("intent_for") in effs)]
                        out.append({**own, "path": p, "effect": e, "effect_result": r, "established_by": [u["id"] for u in src],
                                    "at_ponr": r == "HAPPENED" and bool(src) and all(u.get("ponr") for u in src),
                                    "unestablished": gaps + no_intent})
            else:
                gaps = []
                if role == "mints":
                    ms = [t]
                elif outside:
                    ms = [m for m in minters if p in expand(m.get("paths", []))]
                else:
                    ms = upstream(t, p, lambda u: oid in u.get("mints", []))
                    if minters:
                        gaps = [(t["from"], x, "mints it") for x in dead_ends(t, p, lambda u: oid in u.get("mints", []))]
                out.append({**own, "path": p, "effect": None, "effect_result": None, "established_by": [m["id"] for m in ms],
                            "at_ponr": bool(ms) and all(m.get("ponr") for m in ms), "unestablished": gaps})
    final = []
    for s in out:
        if s["path"] in GROUPS["ALL_EXEC"]:
            final.append({**s, "finalization_class": "SOURCE_MODIFYING" if PATHS0[s["path"]]["source_modifying"] else "READ_ONLY"})
        else:
            final += [{**s, "finalization_class": c} for c in sorted(FINALIZATION_CLASSES)]
    return final


def intent_situations(o):
    """Every Chapter 0 context an intent is created in: one per minting transition, effect and path. A transition that mints
    an intent without naming its effect (t-i06, a retry) mints it for the identical effect of the intent it supersedes,
    so it is enumerated over every effect an initiation rule issues an intent for."""
    initiated = sorted({t["intent_for"] for t in TRANS.values() if t.get("intent_for")})
    out = []
    for t in [x for x in TRANS.values() if o["id"] in x.get("mints", [])]:
        for e in [t["intent_for"]] if t.get("intent_for") else initiated:
            for p in sorted(expand(t.get("paths", []))) or [None]:
                out.append({"transition": t["id"], "role": "mints", "transition_effect": t.get("effect"),
                            "transition_ponr": bool(t.get("ponr")), "path": p, "effect": e, "effect_result": None,
                            "established_by": [t["id"]], "at_ponr": False, "retry": not t.get("intent_for")})
    return out


def describe(o, s):
    """A situation: the transition and how it touches the record, then the record's facts its rules select on."""
    if o["ch0_class"] == "intent":
        return f"{s['transition']} creating {'a retry' if s.get('retry') else 'the'} intent for {s['effect']}" + (f" on {s['path']}" if s["path"] else "")
    used = {k for g in groups(o) for k in g.get("when", {})}
    text = f"{s['transition']} on {s['path'] or 'no path'}"
    if EFFECTS_OF.get(o["id"]):
        facts = f"{s.get('effect')} {s.get('effect_result')}"
        if s["role"] == "proves":
            text += f" proving {facts}"
        elif s["established_by"] == [s["transition"]]:
            text += f" consuming the receipt of {facts}"
        else:
            text += f" consuming the receipt {' and '.join(s['established_by']) or 'no transition'} established ({facts})"
    if "finalization_class" in used:
        text += f", {s['finalization_class']}"
    return text


CHECKS = []


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn
    return wrap


@check("baseline.chapter0")
def _baseline():
    probs = []
    handoff = HAND / "ch00-phase-c.json"
    digest = sha(handoff.read_bytes())
    if digest != INV_A["baseline"]["chapter0_handoff_sha256"]:
        probs.append(f"Chapter 0 handoff sha256 {digest[:8]} differs from the baseline")
    recorded = load(handoff)["artifact_hashes"]
    prefix = "docs/architecture/chapter0/"
    keys = sorted(k for k in recorded if k.startswith(prefix))
    for k in keys:
        p = CH0 / k[len(prefix):]
        if not p.is_file():
            probs.append(f"{k} is missing from the Chapter 0 folder")
        elif sha(p.read_bytes()) != recorded[k]:
            probs.append(f"{k} differs from the committed Chapter 0")
    # Every Chapter 0 file this checker reads or the draft cites is one the handoff records.
    read = sorted(CH0REG.glob("*.json")) + [CH0 / n for n in ("CHAPTER0_ARCHITECTURE.md", "CHAPTER0_CLOSURE.md", "README.md", "check_registries.py")]
    for p in read:
        if prefix + p.relative_to(CH0).as_posix() not in recorded:
            probs.append(f"{p.name} is read or cited but not recorded in the Chapter 0 handoff")
    outside = sorted(k for k in recorded if not k.startswith(prefix))
    return probs, (f"all {len(keys)} Chapter 0 files in the handoff equal the committed bytes "
                   f"({len(recorded)} recorded; {', '.join(outside) or 'none'} outside the Chapter 0 folder, not read)")


@check("inventory.coverage")
def _coverage():
    probs = []
    ids = [o["id"] for o in OBJS]
    if sorted(ids) != sorted(OBJ0) or len(ids) != len(set(ids)):
        probs.append(f"inventory ids differ from Chapter 0's {len(OBJ0)} (found {len(ids)})")
    for o in OBJS:
        c = OBJ0.get(o["id"])
        if not c:
            continue
        for mine, theirs in (("name", "name"), ("ch0_class", "class"), ("minter", "minter"), ("attempt_bound", "attempt_bound")):
            if o[mine] != c[theirs]:
                probs.append(f"{o['id']}: {mine} {o[mine]!r} differs from Chapter 0's {c[theirs]!r}")
        if o["storage"] != O0["storage_by_class"][c["class"]]:
            probs.append(f"{o['id']}: storage differs from Chapter 0's storage_by_class")
    return probs, f"{len(ids)} objects, each once, matching Chapter 0"


@check("inventory.forms")
def _forms():
    probs = []
    counts = {}
    for o in OBJS:
        rule = INV_A["class_rules"].get(o["ch0_class"])
        if o["schema_form"] not in INV_A["schema_forms"]:
            probs.append(f"{o['id']}: unknown schema form {o['schema_form']}")
        if o["authority_effect"] not in INV_A["authority_effects"]:
            probs.append(f"{o['id']}: unknown authority effect {o['authority_effect']}")
        if not rule or (rule["schema_form"], rule["authority_effect"]) != (o["schema_form"], o["authority_effect"]):
            probs.append(f"{o['id']}: form or effect doesn't follow the rule for class {o['ch0_class']}")
        missing = [u for u in (rule or {}).get("requires_unknowns", []) if u not in o["unknowns"]]
        if missing:
            probs.append(f"{o['id']}: class {o['ch0_class']} requires it to cite {missing} "
                         f"({', '.join(rule.get('requires_unknowns_basis', []))})")
        counts[o["schema_form"]] = counts.get(o["schema_form"], 0) + 1
    for cls, rule in INV_A["class_rules"].items():
        for ref in rule.get("requires_unknowns", []) + rule.get("requires_unknowns_basis", []):
            if not exists(ref):
                probs.append(f"class rule {cls} cites missing {ref}")
    return probs, ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


@check("inventory.kinds")
def _kinds():
    probs = []
    multi = sorted(o["id"] for o in OBJS if len(o["kinds"]) > 1)
    if multi != ["obj-03", "obj-37", "obj-38"]:
        probs.append(f"multi-kind objects are {multi}, expected obj-03, obj-37, obj-38")
    for o in OBJS:
        if len(o["kinds"]) > 1 and "U-20" not in o["unknowns"]:
            probs.append(f"{o['id']} has several kinds but doesn't cite U-20")
        kind_contexts = [g for g in groups(o) if "kind" in g.get("when", {})]
        for k in o["kinds"] if kind_contexts else []:
            n = sum(1 for g in kind_contexts if k in as_list(g["when"]["kind"]))
            if n != 1:
                probs.append(f"{o['id']}: kind {k} is in {n} kind contexts; each kind needs exactly one")
    return probs, f"{len(multi)} object types with two record kinds, each citing U-20"


@check("inventory.hash_forms")
def _hash_forms():
    probs = []
    allowed = {"protocol_object": {"object_hash"}, "embedded_contract_part": {"within_tiers"}, "raw_witness": {"content_sha256"},
               "legacy_read_only": {"undecided"}, "ledger_state_row": {"by_id_and_version"},
               "append_only_journal_record": {"by_id", "hash_chain", "object_hash", "undecided"}}
    for o in OBJS:
        if o["canonical_hash"] not in INV_A["canonical_hash_forms"] or o["canonical_hash"] not in allowed[o["schema_form"]]:
            probs.append(f"{o['id']}: hash form {o['canonical_hash']} doesn't fit {o['schema_form']}")
        if o["canonical_hash"] == "undecided" and not o["unknowns"]:
            probs.append(f"{o['id']}: an undecided hash form must cite the unknown that asks")
    return probs, "every hash form fits its schema form; undecided ones cite an unknown"


@check("inventory.ch1_scope")
def _scope():
    probs = []
    required = {o["id"] for o in OBJS if o["ch1_scope"] == "required"}
    for o in OBJS:
        if o["ch1_scope"] not in ("required", "form_only"):
            probs.append(f"{o['id']}: scope {o['ch1_scope']}")
        if not o["ch1_scope_reason"]:
            probs.append(f"{o['id']}: no reason for its Chapter 1 scope")
        for ref in text_refs(" ".join(o["ch1_scope_reason"])):
            if not exists(ref):
                probs.append(f"{o['id']}: scope reason cites unknown {ref}")
    must = {r["object"] for r in RG0["registrations"]} | {"obj-04", "obj-05", "obj-17", "obj-18", "obj-32"}
    missing = sorted(must - required)
    if missing:
        probs.append(f"objects Chapter 0 needs in Chapter 1 are not marked required: {missing}")
    return probs, f"{len(required)} required in Chapter 1: {', '.join(sorted(required))}"


@check("inventory.bindings")
def _bindings():
    probs = []
    facts = INV_A["binding_facts"]
    n = 0
    for o in OBJS:
        for key in OLD_BINDING_KEYS:
            if key in o:
                probs.append(f"{o['id']}: {key} is replaced by common_bindings and conditional_bindings with when clauses")
        for g in groups(o):
            if not g.get("when") or not isinstance(g.get("bindings"), list):
                probs.append(f"{o['id']}: a binding context needs a when clause and a bindings list, empty if it adds nothing")
            for u in g.get("open", []):
                if u not in ALL_U:
                    probs.append(f"{o['id']}: an open context cites {u}, which isn't an unknown")
        for cond, key, b in bindings(o):
            n += 1
            if b["target"] not in OBJ0 and b["target"] not in facts:
                probs.append(f"{o['id']}: binds unknown target {b['target']}")
            for ref in b["why"]:
                for r in text_refs(ref) or [ref]:
                    if not exists(r):
                        probs.append(f"{o['id']}: binding reason {r} doesn't exist")
            for u in b.get("open", []):
                if u not in ALL_U:
                    probs.append(f"{o['id']}: binding cites {u}, which isn't an unknown")
        for ref in o["invariants"] + o["unknowns"] + o.get("embeds", []):
            if not exists(ref):
                probs.append(f"{o['id']}: cites missing {ref}")
            if ref in INV_REPLACED:
                probs.append(f"{o['id']}: cites replaced invariant {ref}")
    for key, fact in facts.items():
        for r in fact["basis"] + fact.get("open", []):
            for x in text_refs(r) or [r]:
                if not exists(x):
                    probs.append(f"binding fact {key} cites missing {x}")
    for name, r in RESULTS.items():
        for x in r["basis"]:
            if not exists(x):
                probs.append(f"effect result {name} cites missing {x}")
        if not any(set(r["established_by"]) & set(t.get("preconditions", [])) for t in TRANS.values()):
            probs.append(f"effect result {name}: no Chapter 0 transition has a precondition that establishes it")
    for x in INV_A["ponr_only_bindings"]["basis"]:
        if not exists(x):
            probs.append(f"ponr_only_bindings cites missing {x}")
    for m in PONR_ONLY - set(facts):
        probs.append(f"ponr_only_bindings names {m}, which isn't a binding fact")
    return probs, f"{n} bindings, every target and reason resolved"


@check("inventory.conditions")
def _conditions():
    probs = []
    n = 0
    for o in OBJS:
        ctx_paths = set()
        for t in contexts(o["id"]):
            ctx_paths |= expand(t.get("paths", []))
        own_effects = EFFECTS_OF.get(o["id"], set())
        for g in groups(o):
            n += 1
            for key, val in g.get("when", {}).items():
                vals = as_list(val)
                if key not in SELECTORS:
                    probs.append(f"{o['id']}: unknown selector {key}")
                elif key == "path":
                    bad = [v for v in vals if v not in PATHS0 and v not in GROUPS]
                    if bad:
                        probs.append(f"{o['id']}: {bad} are not finalization paths")
                    elif not expand(vals) <= ctx_paths:
                        probs.append(f"{o['id']}: conditioned on {sorted(expand(vals) - ctx_paths)}, where Chapter 0 never produces or uses it")
                elif key == "effect":
                    allowed = set(EFFECTS) if o["ch0_class"] == "intent" else own_effects
                    if not set(vals) <= allowed:
                        probs.append(f"{o['id']}: conditioned on {sorted(set(vals) - allowed)}, which it isn't the receipt or intent of")
                elif key == "effect_result":
                    if not own_effects or not set(vals) <= set(RESULTS):
                        probs.append(f"{o['id']}: effect_result {vals} needs an effect receipt and a registered effect result")
                elif key == "kind":
                    if not set(vals) <= set(o["kinds"]):
                        probs.append(f"{o['id']}: {sorted(set(vals) - set(o['kinds']))} are not its kinds")
                elif key == "finalization_class":
                    if not set(vals) <= FINALIZATION_CLASSES:
                        probs.append(f"{o['id']}: unknown finalization class {vals}")
        if any(b["target"] == "observation_window" for b in o.get("common_bindings", [])):
            probs.append(f"{o['id']}: the observation window is READ_ONLY only; it can't be a common binding")
    # A minted receipt binds every receipt its minting transition requires, in each context that transition mints it in.
    for t in TRANS.values():
        for m in t.get("mints", []):
            o = OBJ_A.get(m)
            if not o or o["ch0_class"] != "receipt":
                continue
            for s in [s for s in situations(o) if s["transition"] == t["id"]]:
                for r in sorted(set(t.get("requires", [])) - applicable(o, s)):
                    probs.append(f"{m}: minted by {t['id']} in {describe(o, s)}, which requires {r}, but doesn't bind it there")
        minted = [x for x in t.get("mints", []) if x in OBJ_A and OBJ_A[x]["ch0_class"] == "receipt"]
        for a in minted:
            for b in minted:
                if a < b:
                    ta = {x[2]["target"] for x in bindings(OBJ_A[a])}
                    tb = {x[2]["target"] for x in bindings(OBJ_A[b])}
                    if b not in ta and a not in tb:
                        probs.append(f"{t['id']} mints {a} and {b}, but neither binds the other")
    # Every effect's receipt binds the intent it ran under.
    for e, d in EFFECTS.items():
        r = OBJ_A[d["receipt"]]
        if not d.get("requires_intent") or any(b["target"] == "intent" for b in r.get("common_bindings", [])):
            continue
        sits = [s for s in situations(r) if s.get("effect") == e]
        if not sits or any("intent" not in applicable(r, s) for s in sits):
            probs.append(f"{r['id']}: the receipt of {e} must bind its intent")
    return probs, f"{n} binding rules, each conditioned only on selectors Chapter 0 gives the object"


@check("inventory.ponr_receipts")
def _ponr():
    probs = []
    for pid, path in sorted(PATHS0.items()):
        t = TRANS[path["ponr_transition"]]
        touched = set(t.get("requires", [])) | set(t.get("mints", []))
        receipts = {o["id"] for o in OBJS if pid in o.get("ponr_receipt_for", [])}
        if not receipts:
            probs.append(f"path {pid}: no PONR receipt named")
        elif not receipts <= touched:
            probs.append(f"path {pid}: {sorted(receipts - touched)} not required or minted by {t['id']}")
        for oid in sorted(receipts & touched):
            if not any(s["at_ponr"] for s in situations(OBJ_A[oid]) if s["transition"] == t["id"] and s["path"] == pid):
                probs.append(f"{oid}: Chapter 0 gives no context in which the point of no return {t['id']} on {pid} produces or proves it")
    # Assertion 2: every receipt that records a point of no return that happened binds what that point requires, the
    # delegation state (INV-41) and the consumed reservation where the point of no return consumes one (t-r01). The
    # requirement comes from the transition that established the receipt, so a later consumer sees the same receipt.
    # Assertion 3: no other receipt context inherits a PONR-only binding.
    at = others = 0
    for o in [x for x in OBJS if x["ch0_class"] in RECEIPT_CLASSES]:
        for s in situations(o):
            have = applicable(o, s)
            if s["at_ponr"]:
                at += 1
                consumes = any("reservation_consumed" in TRANS[x].get("preconditions", []) for x in s["established_by"])
                missing = ({"delegation_state_at_ponr"} | ({"reservation_consumed"} if consumes else set())) - have
                if missing:
                    probs.append(f"{o['id']}: in {describe(o, s)}, the receipt records the point of no return "
                                 f"{' and '.join(s['established_by'])}, so it must bind {sorted(missing)}")
            else:
                others += 1
                leaked = PONR_ONLY & have
                if leaked:
                    probs.append(f"{o['id']}: in {describe(o, s)}, the receipt records no point of no return that happened, "
                                 f"but inherits {sorted(leaked)}")
    return probs, (f"{len(PATHS0)} paths each name the receipt their point of no return produces or proves; in {at} contexts a "
                   f"receipt records a point of no return and binds what it requires, and none of the other {others} inherits "
                   f"{', '.join(sorted(PONR_ONLY))}")


@check("inventory.contexts")
def _binding_contexts():
    """Rules add up across partitions and are exclusive within one. Every Chapter 0 context of a conditionally bound receipt
    or intent resolves to exactly one rule in each partition (the rules conditioned on the same selectors), and every rule
    that isn't open matches at least one context."""
    probs = []
    n_sit = n_obj = n_part = 0
    for o in [x for x in OBJS if situated(x) and groups(x)]:
        n_obj += 1
        sits = sits_of(o)
        n_sit += len(sits)
        parts = {}
        for i, g in enumerate(groups(o)):
            parts.setdefault(tuple(sorted(g["when"])), []).append(i)
        n_part += len(parts)
        hit_any = set()
        for s in sits:
            if EFFECTS_OF.get(o["id"]) and (s["effect"] is None or s["effect_result"] is None):
                probs.append(f"{o['id']}: Chapter 0 determines no effect or effect result for {describe(o, s)}")
            for keys, idxs in sorted(parts.items()):
                hits = [i for i in idxs if matches(groups(o)[i]["when"], s)]
                hit_any |= set(hits)
                if len(hits) != 1:
                    probs.append(f"{o['id']}: {describe(o, s)} matches {len(hits)} rules in the partition {list(keys)}; "
                                 "it must resolve to exactly one")
        for i, g in enumerate(groups(o)):
            if i not in hit_any and not g.get("open"):
                probs.append(f"{o['id']}: the rule {json.dumps(g['when'], sort_keys=True)} matches no Chapter 0 context")
    return probs, (f"{n_sit} Chapter 0 contexts of the {n_obj} conditionally bound receipts and intents each resolve to exactly "
                   f"one rule in every partition of their object ({n_part} partitions in all), and the matches add up across "
                   "partitions; every rule that isn't open matches a context")


@check("inventory.provenance")
def _provenance():
    """Provenance stays apart from the record. No when clause selects on a transition. A transition proves an effect only
    where Chapter 0 names it as its own and its preconditions establish the result. A consumer that names no effect takes
    what its receipt records from the transition upstream that established it. Every provenance list is exact: it names
    each transition whose context the rule matches, and only those."""
    probs = []
    downstream, proving, lists = set(), 0, 0
    for o in OBJS:
        for g in groups(o):
            if "transition" in g.get("when", {}):
                probs.append(f"{o['id']}: transition is provenance, not a when selector: a record's bindings can't depend on "
                             "which transition consumes it, so the rule lists it under provenance")
            for k in sorted(set(g) - RULE_KEYS):
                probs.append(f"{o['id']}: a binding rule has the unknown key {k}")
    for o in [x for x in OBJS if x["ch0_class"] in RECEIPT_CLASSES and EFFECTS_OF.get(x["id"])]:
        for s in situations(o):
            t = TRANS[s["transition"]]
            if s["role"] == "proves":
                proving += 1
                if not establishes(t, o["id"]):
                    probs.append(f"{o['id']}: {t['id']} names {t['effect']}, whose receipt it requires, but establishes no "
                                 f"result for it: it consumes the receipt {' and '.join(s['established_by']) or 'no transition'} "
                                 "established, and a consumer doesn't perform the effect that receipt records")
                elif EFFECTS[t["effect"]].get("requires_intent"):
                    ups = intents_upstream(t, s["path"], o["id"])
                    if ups != [t["effect"]]:
                        probs.append(f"{o['id']}: {t['id']} proves {t['effect']} on {s['path']}, but the intents upstream are for {ups}")
            elif not s["established_by"]:
                probs.append(f"{o['id']}: {t['id']} consumes it on {s['path']}, but no transition upstream establishes what it records")
            elif s["established_by"] != [t["id"]]:
                downstream.add(f"{t['id']} from {' and '.join(s['established_by'])}")
    # Every way in passes the transition the derivation relies on, not just some way.
    ways = 0
    for o in [x for x in OBJS if x["ch0_class"] in RECEIPT_CLASSES]:
        for s in situations(o):
            ways += 1
            for state, start, what in s["unestablished"]:
                probs.append(f"{o['id']}: {describe(o, s)}: a way into {state} on {s['path']} starts at {start} and passes "
                             f"no transition that {what}")
    for o in [x for x in OBJS if situated(x)]:
        sits = sits_of(o)
        for g in [g for g in groups(o) if "provenance" in g]:
            lists += 1
            listed = provenance(g)
            covered = {s["transition"] for s in sits if matches(g["when"], s)}
            for tid in listed:
                if tid not in TRANS or not (touches(TRANS[tid], o["id"]) or o["id"] in TRANS[tid].get("mints", [])):
                    probs.append(f"{o['id']}: {tid} is in a rule's provenance, but doesn't mint, prove or consume it")
                elif tid not in covered:
                    tsits = [s for s in sits if s["transition"] == tid]
                    bad = [k for k in g["when"] if k in SELECTORS and not any(matches({k: g["when"][k]}, s) for s in tsits)]
                    for k in bad:
                        probs.append(f"{o['id']}: {tid} is in the provenance of a rule conditioned on {k} {g['when'][k]}, "
                                     f"but Chapter 0 gives it {sorted({str(s.get(k)) for s in tsits})}")
                    if not bad:
                        probs.append(f"{o['id']}: {tid} is in the provenance of the rule {json.dumps(g['when'], sort_keys=True)}, "
                                     "but none of its contexts matches the whole rule")
            for tid in sorted(covered - set(listed)):
                s = next(s for s in sits if s["transition"] == tid and matches(g["when"], s))
                probs.append(f"{o['id']}: {describe(o, s)} falls under the rule {json.dumps(g['when'], sort_keys=True)}, "
                             f"whose provenance doesn't list {tid}")
    return probs, (f"no rule selects on a transition; {proving} contexts prove the effect their transition names, and "
                   f"{len(downstream)} consumers take what their receipt records from the transition that established it "
                   f"({', '.join(sorted(downstream))}), credited with no effect; in the {ways} receipt contexts, every way in "
                   f"passes the transition its derivation relies on; {lists} provenance lists are exact")


@check("inventory.intents")
def _intents():
    """An intent binds what its creating transition checks, and nothing that transition doesn't check: the fence's attempt,
    the held reservation and the delegation at intent (INTENT_SUPPORT). Every transition that mints an intent is checked.
    A retry (t-i06) falls under its effect's rule, since no protocol field marks a retry, so the bindings it inherits but
    doesn't check pass only while an open Chapter 0 correction candidate covers that transition."""
    probs, covered, waits = [], [], {}
    covering_t = {}
    for u in UNK["new"]:
        if u.get("classification") == "ch0_correction_candidate" and u.get("status") != "RESOLVED":
            for tid in u.get("covers_transitions", []):
                covering_t.setdefault(tid, []).append(u["id"])
    for o in [x for x in OBJS if x["ch0_class"] == "intent"]:
        for e in sorted(EFFECTS):
            if any(t.get("intent_for") == e for t in TRANS.values()):
                covered.append(e)
            elif not open_contexts(o, "effect", e):
                probs.append(f"{o['id']}: no Chapter 0 transition creates the intent for {e}, so its rule must be open")
        for s in intent_situations(o):
            t = TRANS[s["transition"]]
            pre = set(t["preconditions"])
            have = applicable(o, s)
            need = {b for b, req in INTENT_SUPPORT.items() if pre & req}
            if not need <= have:
                probs.append(f"{o['id']}: the intent {t['id']} creates for {s['effect']} on {s['path']} must bind {sorted(need - have)}")
            unchecked = sorted(b for b, req in INTENT_SUPPORT.items() if b in have and not pre & req)
            if not unchecked:
                continue
            if covering_t.get(t["id"]):
                waits.setdefault((t["id"], ", ".join(covering_t[t["id"]])), set()).update(unchecked)
            else:
                probs.append(f"{o['id']}: the {'retry ' if s['retry'] else ''}intents {t['id']} mints for {s['effect']} fall under that "
                             f"effect's rule and bind {unchecked}, which {t['id']} doesn't check")
    detail = (f"{len(covered)} effects' intents bind exactly what their initiation rules check; "
              f"the {len(EFFECTS) - len(covered)} others' rules are open (U-44)")
    for (tid, us), un in sorted(waits.items()):
        detail += f"; {tid}'s retry intents fall under their effect's rule and inherit {', '.join(sorted(un))} unchecked, which {us} covers"
    return probs, detail


def reachable(machine, start):
    """States of a Chapter 0 machine reachable from start."""
    seen, todo = {start}, [start]
    while todo:
        s = todo.pop()
        for t in TRANS.values():
            if t.get("machine") == machine and t.get("from") == s and t.get("to") not in seen:
                seen.add(t["to"])
                todo.append(t["to"])
    return seen


@check("effects.intent_closure")
def _intent_closure():
    """Every effect that requires an intent has, in Chapter 0, an initiation rule that mints an EffectIntent bound to it,
    outcome paths to HAPPENED, ABSENT and UNKNOWN_OUTCOME, and its receipt reachable from HAPPENED. Chapter 0 shares one
    EffectIntent machine across all effects, so the outcome paths are checked once, against every outcome each effect's
    outcome rule can produce. Every transition that mints an EffectIntent is an initiation rule for a named effect. A gap
    passes only while an open Chapter 0 correction candidate covers it, and a candidate may cover only real gaps."""
    probs = []
    intent_obj = next(o["id"] for o in OBJS if o["ch0_class"] == "intent")
    machine = "effect_intent"
    initial = S0["machines"][machine]["initial"]
    reach = reachable(machine, initial)
    gaps, closed = {}, []
    for e, d in sorted(EFFECTS.items()):
        if not d.get("requires_intent"):
            continue
        missing = []
        if not any(t.get("intent_for") == e and intent_obj in t.get("mints", []) for t in TRANS.values()):
            missing.append(f"no initiation rule mints {intent_obj} for it")
        rule = T0["outcome_rules"][d["outcome_rule"]]
        unreachable = sorted((OUTCOMES | {v for k, v in rule.items() if k != "note"}) - reach)
        if unreachable:
            missing.append(f"its intent can't reach {unreachable} from {initial}")
        if not any(t.get("effect") == e and "effect_happened" in t.get("preconditions", []) and d["receipt"] in t.get("requires", [])
                   for t in TRANS.values()):
            missing.append(f"no transition reaches its receipt {d['receipt']} from HAPPENED")
        if missing:
            gaps[e] = missing
        else:
            closed.append(e)
    covering = {}
    for u in UNK["new"]:
        if u.get("classification") == "ch0_correction_candidate" and u.get("status") != "RESOLVED":
            for e in u.get("covers_effects", []):
                covering.setdefault(e, []).append(u)
    for e, missing in sorted(gaps.items()):
        if e not in covering:
            probs.append(f"{e}: {'; '.join(missing)}, and no open Chapter 0 correction candidate covers it")
            continue
        receipt = EFFECTS[e]["receipt"]
        for u in covering[e]:
            if u.get("proposed_correction", {}).get("applied"):
                probs.append(f"{u['id']}: says its correction is applied, but Chapter 0 still has the gap for {e}")
            if u["id"] not in OBJ_A[receipt]["unknowns"]:
                probs.append(f"{receipt}: the receipt of {e} must cite {u['id']}, which covers that effect's gap")
            if u["id"] not in open_contexts(OBJ_A[intent_obj], "effect", e):
                probs.append(f"{intent_obj}: its open context for {e} must cite {u['id']}")
    for e, us in sorted(covering.items()):
        names = ", ".join(u["id"] for u in us)
        if e not in EFFECTS:
            probs.append(f"{names} covers {e}, which is not a Chapter 0 effect")
        elif e not in gaps:
            probs.append(f"{names} covers {e}, which already closes in Chapter 0")
    # A transition that mints an intent without naming the effect it is for escapes every intent check.
    covering_t = {}
    for u in UNK["new"]:
        if u.get("classification") == "ch0_correction_candidate" and u.get("status") != "RESOLVED":
            for tid in u.get("covers_transitions", []):
                covering_t.setdefault(tid, []).append(u)
    outside = sorted(t["id"] for t in TRANS.values() if intent_obj in t.get("mints", []) and not t.get("intent_for"))
    for tid in outside:
        if tid not in covering_t:
            probs.append(f"{tid}: mints {intent_obj} without naming the effect it is for (intent_for), so no intent check reaches it, "
                         "and no open Chapter 0 correction candidate covers it")
            continue
        for u in covering_t[tid]:
            if u.get("proposed_correction", {}).get("applied"):
                probs.append(f"{u['id']}: says its correction is applied, but {tid} still mints {intent_obj} outside an initiation rule")
            if u["id"] not in OBJ_A[intent_obj]["unknowns"]:
                probs.append(f"{intent_obj}: must cite {u['id']}, which covers {tid}")
    for tid, us in sorted(covering_t.items()):
        if tid not in outside:
            probs.append(f"{', '.join(u['id'] for u in us)} covers {tid}, which is not a transition that mints {intent_obj} outside an initiation rule")
    waiting = sorted({u["id"] for e in gaps for u in covering.get(e, [])})
    waiting_t = sorted({u["id"] for t in outside for u in covering_t.get(t, [])})
    return probs, (f"{len(closed)} of {len(closed) + len(gaps)} effects that require an intent close in Chapter 0 (initiation, "
                   f"outcomes from {initial}, receipt from HAPPENED); "
                   + (f"{' and '.join(sorted(gaps))} wait on the Chapter 0 correction candidate {', '.join(waiting)}" if gaps else "no gaps")
                   + (f"; {', '.join(outside)} mints {intent_obj} outside an initiation rule and waits on {', '.join(waiting_t)}" if outside else ""))


def lifecycle_text(ref):
    """The Chapter 0 text a creation rule may rest on: an invariant's statement or an ARCHITECTURE section."""
    if ref in INV0:
        return INV0[ref]["statement"]
    return ARCH_SECTIONS.get(ref, "")


def mentions(text, o):
    """Whether Chapter 0 text names the object, by ID or by its name with or without word breaks."""
    name = o["name"].split(" (")[0].split(" / ")[0]
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()
    return bool(text) and (o["id"] in text or name in text or spaced in text.lower())


@check("inventory.creation")
def _creation():
    """An object a transition actor mints is minted by a Chapter 0 transition or created by a stated lifecycle rule, never both."""
    probs = []
    forms = {k for k in INV_A["creation_forms"] if k != "note"}
    ruled = []
    for o in OBJS:
        minting = [t["id"] for t in TRANS.values() if o["id"] in t.get("mints", [])]
        cb = o.get("created_by")
        if o["minter"] not in TRANSITION_ACTORS:
            if cb:
                probs.append(f"{o['id']}: created_by is only for objects a transition actor mints; the {o['minter']} mints this one")
            continue
        if minting and cb:
            probs.append(f"{o['id']}: minted by {', '.join(minting)}, so it can't also have a created_by rule")
        elif not minting and not cb:
            probs.append(f"{o['id']}: the {o['minter']} mints it, no Chapter 0 transition does, and no created_by rule says how Chapter 0 creates it")
        if not cb:
            continue
        ruled.append(o["id"])
        if cb.get("form") not in forms:
            probs.append(f"{o['id']}: created_by form {cb.get('form')}")
        if not cb.get("rule") or not cb.get("basis"):
            probs.append(f"{o['id']}: created_by needs a rule and a basis")
        for ref in cb.get("basis", []) + text_refs(cb.get("rule", "")):
            if not exists(ref) or not is_ch0_ref(ref):
                probs.append(f"{o['id']}: created_by cites {ref}, which is not a Chapter 0 reference")
        if not any(mentions(lifecycle_text(r), o) for r in cb.get("basis", [])):
            probs.append(f"{o['id']}: created_by must cite an invariant or an ARCHITECTURE section that names the object")
        for key in ("minted_by", "transition"):
            if key in cb:
                probs.append(f"{o['id']}: created_by names {cb[key]} as its minter, but no Chapter 0 transition mints it")
        if cb.get("form") == "lifecycle_coupling" and (cb.get("coupled_to") not in OBJ0 or not any(r.startswith("INV-") for r in cb.get("basis", []))):
            probs.append(f"{o['id']}: a lifecycle coupling names the object it is coupled to and the invariant that couples them")
    return probs, (f"every object the {' or the '.join(sorted(TRANSITION_ACTORS))} mints has a minting transition or a "
                   f"Chapter 0 lifecycle rule ({', '.join(ruled)}), never both")


@check("inventory.reuse_conditions")
def _reuse():
    members = INV_A["binding_facts"]["reuse_conditions"]["members"]
    probs = []
    for tid in ("t-s43", "t-s44"):
        pre = TRANS[tid]["preconditions"]
        if sorted(members) != sorted(pre) or len(members) != len(pre):
            probs.append(f"reuse_conditions differ from {tid}'s preconditions {pre}")
    return probs, f"reuse_conditions lists the {len(members)} preconditions of t-s43 and t-s44"


@check("identity.placement")
def _placement():
    probs = []
    paths = [f["path"] for f in FIELDS]
    if len(paths) != len(set(paths)):
        probs.append("a field is listed twice")
    for tier in ID0["tiers"]:
        mine = [f["path"] for f in FIELDS if f["tier"] == tier["id"]]
        if mine != tier["fields"]:
            probs.append(f"{tier['id']}: fields differ from Chapter 0 (found {len(mine)}, expected {len(tier['fields'])})")
    flagged = {f["path"]: f["tier"] for f in FIELDS if f.get("required_placement")}
    if flagged != ID0["required_placements"]:
        probs.append(f"required placements differ from Chapter 0: {sorted(set(flagged.items()) ^ set(ID0['required_placements'].items()))}")
    for f in FIELDS:
        if f["path"] in ID0["excluded_from_every_tier"]:
            probs.append(f"{f['path']} is excluded from every tier")
    sizes = "/".join(str(len(t["fields"])) for t in ID0["tiers"])
    return probs, f"{len(paths)} fields placed {sizes} exactly as identity.json, {len(flagged)} required placements"


@check("identity.refs")
def _identity_refs():
    probs = []
    for f in FIELDS:
        if f["shape"] not in IDF["shapes"]:
            probs.append(f"{f['path']}: unknown shape {f['shape']}")
        for ref in f["ch0_refs"]:
            if not exists(ref) or not is_ch0_ref(ref):
                probs.append(f"{f['path']}: {ref} is not a Chapter 0 reference")
        for ref in f["open"]:
            if ref not in ALL_U:
                probs.append(f"{f['path']}: open item {ref} is not an unknown")
        if f["shape"] == "set" and "U-25" not in f["open"]:
            probs.append(f"{f['path']}: a set must cite U-25 until its order is decided")
    return probs, f"{len(FIELDS)} fields; every set-shaped field waits on U-25"


@check("bindings.five")
def _five():
    probs = []
    by_path = {f["path"]: f for f in FIELDS}
    req = {o["id"] for o in OBJS if o["ch1_scope"] == "required"}
    for r in RG0["registrations"]:
        f = by_path.get(r["bound_as"])
        if not f or f["tier"] != "contract_digest":
            probs.append(f"{r['id']}: {r['bound_as']} is not a ContractDigest field")
        if r["object"] not in req:
            probs.append(f"{r['id']}: {r['object']} is not required in Chapter 1")
    return probs, f"{len(RG0['registrations'])} freeze-time bindings, each a ContractDigest field of a required object"


def all_refs():
    """Every reference the draft makes, for the invariant accounting."""
    refs = []
    for o in OBJS:
        refs += o["invariants"] + o["unknowns"] + text_refs(" ".join(o["ch1_scope_reason"]))
        for _, _, b in bindings(o):
            refs += b["why"] + b.get("open", [])
        if o.get("created_by"):
            refs += o["created_by"]["basis"] + text_refs(o["created_by"]["rule"])
        for g in groups(o):
            refs += g.get("open", [])
    refs += [x for r in RESULTS.values() for x in r["basis"]] + INV_A["ponr_only_bindings"]["basis"]
    for rule in INV_A["class_rules"].values():
        refs += rule.get("requires_unknowns_basis", [])
    refs += [r for f in FIELDS for r in f["ch0_refs"] + f["open"]]
    refs += [r for c in CLM["claims"] for r in c["refs"]]
    refs += [r for c in DC["candidates"] for r in c["ch0_basis"]]
    for u in UNK["new"] + UNK["inherited"]:
        refs += text_refs(json.dumps(u))
    for p in VEC["predicate_tests"]:
        refs += [p["invariant"]] + text_refs(p["rule"] + " " + p.get("note", ""))
    for c in VEC["couplings"]:
        refs += c["basis"]
    refs += text_refs(json.dumps(INV_A["binding_facts"]))
    return refs


@check("invariants.accounting")
def _invariants():
    probs = []
    used = {r for r in all_refs() if r.startswith("INV-")}
    missing = sorted(r for r in used if r not in INV0)
    if missing:
        probs.append(f"cites invariants Chapter 0 lacks: {missing}")
    replaced = sorted(used & INV_REPLACED)
    if replaced:
        probs.append(f"cites replaced invariants: {replaced}")
    ch1 = {i for i, d in INV0.items() if any(t.get("chapter") == 1 for t in d["tests"])}
    if not ch1 <= used:
        probs.append(f"Chapter 1 invariants not addressed: {sorted(ch1 - used)}")
    active = [i for i, d in INV0.items() if d["status"] != "replaced"]
    later = [i for i in active if i not in used]
    checked = [i for i in later if any(t.get("kind") == "registry" for t in INV0[i]["tests"])]
    return probs, (f"{len(INV0)} invariants: {len(ch1)} with Chapter 1 tests, {len(used & set(INV0))} cited by the draft, "
                   f"{len(later)} classified as having no Chapter 1 runtime obligation and retained for later runtime chapters "
                   f"({', '.join(later)}; {len(checked)} of them already have Chapter 0 registry checks), "
                   f"{len(INV_REPLACED)} replaced and uncited")


@check("tests.ch1")
def _ch1_tests():
    probs = []
    tests = sorted({t["id"] for d in INV0.values() for t in d["tests"] if t.get("chapter") == 1})
    covered = {}
    for v in list(VECTORS.values()) + list(PREDS.values()):
        for t in v.get("tests", []) + ([v["test"]] if "test" in v else []):
            covered.setdefault(t, []).append(v["id"])
    for t in tests:
        if t not in covered:
            probs.append(f"{t} has no vector or predicate test")
    unknown = sorted(set(covered) - set(tests))
    if unknown:
        probs.append(f"vectors cite tests Chapter 0 doesn't define: {unknown}")
    return probs, "; ".join(f"{t} by {len(covered.get(t, []))}" for t in tests)


def changed_tiers(fields):
    embeds = {t["id"]: t["embeds"] for t in ID0["tiers"]}
    out = {TIER_OF[f] for f in fields}
    grew = True
    while grew:
        grew = False
        for t, e in embeds.items():
            if e in out and t not in out:
                out.add(t)
                grew = True
    return out


@check("vectors.status")
def _vector_status():
    probs = []
    counts = {}
    for v in VEC["vectors"]:
        s = v["status"]
        counts[s] = counts.get(s, 0) + 1
        if s not in VEC["statuses"]:
            probs.append(f"{v['id']}: status {s}")
            continue
        for u in v.get("blocked_by", []) + v.get("fixture_blocked_by", []) + v.get("also_open", []):
            if u not in ALL_U:
                probs.append(f"{v['id']}: {u} is not an unknown")
        # A fixture's own list covers the unknowns open on the fields it changes (the base: every field it sets),
        # beyond what every fixture waits on.
        derived = (s == "tier_effect_specified" and v["kind"] == "field_change") or v["kind"] == "base"
        if derived:
            own = set(v.get("fixture_blocked_by", []) if s == "tier_effect_specified" else v.get("blocked_by", []))
            if v["kind"] == "base":
                fields = ID0["tiers"][0]["fields"]
            else:
                fields = [v["field"]] + v.get("coupled_fields", [])
            need = set().union(*(OPEN_OF.get(f, set()) for f in fields))
            missing = sorted(need - ALWAYS - own)
            if missing:
                probs.append(f"{v['id']}: also waits on {missing}, open on the fields it sets or changes")
            if own & ALWAYS:
                probs.append(f"{v['id']}: lists {sorted(own & ALWAYS)}, which every fixture already waits on (fixture_waits_for_all)")
        if s == "tier_effect_specified":
            if not isinstance(v.get("expected"), dict) or "fixture_blocked_by" not in v:
                probs.append(f"{v['id']}: a tier effect needs expected tiers and the unknowns its fixture waits on")
            if v["kind"] not in ("field_change", "out_of_band_change", "jcs_property"):
                probs.append(f"{v['id']}: kind {v['kind']} can't have a specified tier effect")
        elif s == "rejection_specified":
            c = COUPLINGS.get(v.get("violates"))
            if v.get("expected") != "rejected" or not c or c["status"] != "ch0" or v.get("field") not in c["fields"]:
                probs.append(f"{v['id']}: a rejection must violate a Chapter 0 coupling on its own field")
        elif s == "blocked":
            if not v.get("blocked_by") or "expected" in v:
                probs.append(f"{v['id']}: a blocked vector names its unknowns and states no outcome")
        elif s == "executable_vector":
            if not v.get("canonical_bytes") or not v.get("digests"):
                probs.append(f"{v['id']}: an executable vector carries its canonical bytes and digests")
    for p in VEC["predicate_tests"]:
        if p["status"] not in VEC["predicate_statuses"]:
            probs.append(f"{p['id']}: status {p['status']}")
        if p["status"] == "blocked" and not p.get("blocked_by"):
            probs.append(f"{p['id']}: blocked without naming an unknown")
        for u in p.get("blocked_by", []) + p.get("also_open", []):
            if u not in ALL_U:
                probs.append(f"{p['id']}: {u} is not an unknown")
    for u in sorted(ALWAYS):
        if u not in ALL_U:
            probs.append(f"fixture_waits_for_all: {u} is not an unknown")
    return probs, (f"{len(VEC['vectors'])} vectors: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                   + f"; every fixture waits on {', '.join(sorted(ALWAYS))}, and each list covers the unknowns open on the fields it changes")


@check("vectors.expected")
def _vectors():
    probs = []
    derived = 0
    for v in VEC["vectors"]:
        if v["status"] != "tier_effect_specified":
            continue
        if v["kind"] == "field_change":
            changed = [v["field"]] + v.get("coupled_fields", [])
            bad = [f for f in changed if f not in TIER_OF]
            if bad:
                probs.append(f"{v['id']}: {bad} not identity fields")
                continue
            ch = changed_tiers(changed)
            want = {t: ("different" if t in ch else "same") for t in TIER_ORDER}
            if v["expected"] != want:
                probs.append(f"{v['id']}: expected {v['expected']} but the tiers give {want}")
            derived += 1
        elif v["kind"] == "out_of_band_change":
            if v["field"] not in ID0["excluded_from_every_tier"] or v["field"] in FIELD_PATHS or set(v["expected"].values()) != {"same"}:
                probs.append(f"{v['id']}: an out-of-band change must be excluded from every tier and leave each tier the same")
            derived += 1
        elif v["kind"] == "jcs_property":
            if set(v["expected"].values()) != {"same"} or "D-09" not in v.get("basis", []):
                probs.append(f"{v['id']}: key order must not change any tier (D-09)")
    return probs, f"{derived} tier effects derived from identity.json and confirmed"


@check("vectors.couplings")
def _couplings():
    probs = []
    for c in VEC["couplings"]:
        if c["status"] not in VEC["coupling_statuses"]:
            probs.append(f"{c['id']}: status {c['status']}")
        for f in c["fields"]:
            if f not in FIELD_PATHS:
                probs.append(f"{c['id']}: {f} is not an identity field")
        for b in c["basis"]:
            if not exists(b) or not is_ch0_ref(b):
                probs.append(f"{c['id']}: basis {b} is not a Chapter 0 reference")
        for u in c.get("pending_on", []):
            if u not in ALL_U:
                probs.append(f"{c['id']}: pending on {u}, which isn't an unknown")
        if c["status"] == "pending" and not c.get("pending_on"):
            probs.append(f"{c['id']}: a pending coupling names its unknown")
        if c["status"] != "pending" and c.get("pending_on"):
            probs.append(f"{c['id']}: pending_on is only for a pending coupling; an open detail of a stated rule goes in open")
        for u in c.get("open", []):
            if u not in ALL_U:
                probs.append(f"{c['id']}: open on {u}, which isn't an unknown")
    for v in VEC["vectors"]:
        if v["kind"] != "field_change" or v["status"] != "tier_effect_specified":
            continue
        changed = {v["field"], *v.get("coupled_fields", [])}
        for c in VEC["couplings"]:
            touched = set(c["fields"]) & changed
            if touched and not set(c["fields"]) <= changed and not v.get("coupling_satisfied", {}).get(c["id"]):
                probs.append(f"{v['id']}: changes {sorted(touched)} without its coupled field (coupling {c['id']}); change both or state why the pair stays valid")
        for k in v.get("coupling_satisfied", {}):
            if k not in COUPLINGS:
                probs.append(f"{v['id']}: coupling_satisfied names unknown coupling {k}")
    return probs, f"{len(VEC['couplings'])} couplings; every tier-effect vector changes coupled fields together or says why one stays valid"


@check("predicates.duplicate")
def _duplicate():
    probs = []
    p2 = next(p for p in VEC["predicate_tests"] if p["test"] == "CH1-duplicate-admission")
    dup, new = p2["outcome"]["duplicate"], p2["outcome"]["new_record"]
    if dup["records_minted"] != 0 or dup["sequences_created"] != 0:
        probs.append("a duplicate admission creates no record and no Sequence (INV-54)")
    if sorted(dup["minted"]) != sorted(TRANS["t-c03"]["mints"]):
        probs.append(f"a duplicate mints exactly what t-c03 mints ({TRANS['t-c03']['mints']})")
    if "existing" not in dup["returns"]:
        probs.append("a duplicate returns the existing record")
    if sorted(new["minted"]) != sorted(TRANS["t-c02"]["mints"]):
        probs.append(f"a new record mints exactly what t-c02 mints ({TRANS['t-c02']['mints']})")
    if "obj-08" in new["minted"] + dup["minted"]:
        probs.append("no Chapter 0 transition mints the Sequence (obj-08), so no admission outcome lists it as minted")
    seq, cb = new.get("sequence_rule", {}), OBJ_A["obj-08"].get("created_by", {})
    if (seq.get("object"), seq.get("basis"), seq.get("form")) != ("obj-08", "INV-40", cb.get("form")) or "INV-40" not in cb.get("basis", []):
        probs.append("a new record's Sequence comes from obj-08's created_by coupling on INV-40, not from t-c02's mints")
    if "creates no record and no Sequence" not in INV0["INV-54"]["statement"]:
        probs.append("INV-54 no longer says a duplicate creates no record")
    for v in VEC["vectors"]:
        if v.get("admission_outcome") == "duplicate":
            probs.append(f"{v['id']}: a duplicate has no RecordHash of its own, so no vector may model one")
        if "CH1-duplicate-admission" in v.get("tests", []) and v["kind"] == "field_change" and v.get("admission_outcome") != "new_record":
            probs.append(f"{v['id']}: a duplicate-admission vector that hashes a record must be a new-record admission")
    return probs, ("a duplicate mints only a DuplicateAdmissionRecord and returns the existing record (t-c03, INV-54); a new record "
                   "mints what t-c02 mints, and its Sequence comes from INV-40's coupling; no vector models a duplicate's RecordHash")


@check("unknowns.inherited")
def _inherited():
    probs = []
    ch0 = {u["id"]: u for u in U0["unknowns"]}
    for u in UNK["inherited"]:
        c = ch0.get(u["id"])
        if not c:
            probs.append(f"{u['id']} is not a Chapter 0 unknown")
        elif u["question"] != c["question"] or u["status"] != c["status"]:
            probs.append(f"{u['id']}: question or status differs from Chapter 0")
    if sorted(u["id"] for u in UNK["inherited"]) != ["U-10", "U-18"]:
        probs.append("the inherited set must be exactly U-10 and U-18")
    carried = sorted(set(ch0) - {"U-10", "U-18"})
    if sorted(UNK["carried_unchanged"]) != carried:
        probs.append("carried_unchanged must list every other Chapter 0 unknown")
    return probs, "U-10 and U-18 keep Chapter 0's exact question and status; the other 16 carry unchanged"


@check("unknowns.new")
def _new_unknowns():
    probs = []
    ids = [u["id"] for u in UNK["new"]]
    start = max(int(i[2:]) for i in U0_IDS) + 1
    if ids != [f"U-{n:02d}" for n in range(start, start + len(ids))]:
        probs.append(f"new IDs must run consecutively from U-{start:02d}")
    if set(ids) & U0_IDS:
        probs.append("a new ID reuses a Chapter 0 ID")
    if len({u["question"] for u in UNK["new"]}) != len(ids):
        probs.append("two new unknowns ask the same question")
    splits = narrowed = refined = candidates = 0
    for u in UNK["new"]:
        uid = u["id"]
        for key in ("question", "ch0_fixes", "open", "blocks", "next_probe", "status", "classification"):
            if not u.get(key):
                probs.append(f"{uid}: missing {key}")
        if u.get("status") not in UNK["statuses"]:
            probs.append(f"{uid}: status {u.get('status')}")
        if u.get("classification") not in UNK["classifications"]:
            probs.append(f"{uid}: classification {u.get('classification')}")
        for r in text_refs(json.dumps(u)):
            if not exists(r):
                probs.append(f"{uid}: cites {r}, which doesn't exist")
        fixes = u.get("ch0_fixes", "")
        if not any(is_ch0_ref(r) for r in text_refs(fixes)) and not any(f in fixes for f in CH0_FILES):
            probs.append(f"{uid}: ch0_fixes must cite Chapter 0")
        if "first_defined_as" in u:
            probs.append(f"{uid}: the question itself keeps the earliest definition; first_defined_as is retired (RV-21)")
        # Narrowing: the question stays as first defined, the status is NARROWED, and the remaining scope is stated.
        children = u.get("split_to", [])
        if children or "narrowed_by" in u or u.get("status") == "NARROWED":
            narrowed += 1
            if u.get("status") != "NARROWED":
                probs.append(f"{uid}: facets were split from it, so its status must be NARROWED")
            if not children or u.get("narrowed_by") not in REVIEW:
                probs.append(f"{uid}: a NARROWED unknown names the unknowns split from it and the review entry that narrowed it")
            if not u.get("remaining") or u.get("remaining") == u.get("question"):
                probs.append(f"{uid}: a NARROWED unknown states its remaining scope apart from its original question")
        if "refinement" in u or "refined_by" in u:
            refined += 1
            if not u.get("refinement") or u.get("refined_by") not in REVIEW:
                probs.append(f"{uid}: a refinement is stated beside the question, with the review entry that made it")
        for child in children:
            c = NEW_U.get(child)
            if not c or c.get("split_from") != uid or child <= uid:
                probs.append(f"{uid}: split_to {child} must be a later unknown whose split_from points back")
        if "split_from" in u:
            splits += 1
            parent = NEW_U.get(u["split_from"]) or {}
            if uid not in parent.get("split_to", []) or u.get("split_by") not in REVIEW or u.get("split_by") != parent.get("narrowed_by"):
                probs.append(f"{uid}: split_from must name a parent that lists it, and split_by the review entry that narrowed the parent")
        # A Chapter 0 correction candidate is closed only by a correction you append to Chapter 0.
        if u.get("classification") == "ch0_correction_candidate":
            candidates += 1
            effects, trans, pc = u.get("covers_effects", []), u.get("covers_transitions", []), u.get("proposed_correction", {})
            if not (effects or trans) or any(e not in EFFECTS for e in effects) or any(t not in TRANS for t in trans):
                probs.append(f"{uid}: a Chapter 0 correction candidate names the Chapter 0 effects or transitions it covers")
            if not u.get("discover") or not u.get("ch0_checker"):
                probs.append(f"{uid}: a Chapter 0 correction candidate lists what remains to discover and why Chapter 0's checker passes")
            if pc.get("decided_by") != "you" or pc.get("applied") is not False or not (pc.get("refine") or pc.get("add")):
                probs.append(f"{uid}: its proposed correction is unapplied, yours to decide, and says what it refines or adds")
            for r in pc.get("refine", []):
                if not exists(r["target"]) or not is_ch0_ref(r["target"]):
                    probs.append(f"{uid}: refines {r['target']}, which is not a Chapter 0 item")
        elif any(k in u for k in ("covers_effects", "covers_transitions", "proposed_correction")):
            probs.append(f"{uid}: only a Chapter 0 correction candidate covers effects or transitions, or proposes a correction")
    return probs, (f"{len(ids)} new unknowns, {ids[0]} to {ids[-1]}, each stating what Chapter 0 already fixes; {narrowed} NARROWED, "
                   f"{splits} split off with provenance, {refined} refined in place, {candidates} Chapter 0 correction candidates")


@check("decisions.candidates")
def _candidates():
    probs = []
    resolved = {}
    for c in DC["candidates"]:
        if not c["key"].startswith("dc.") or re.match(r"^D-\d", c["key"]):
            probs.append(f"{c['key']}: a candidate has a slug, never a decision ID")
        if c["decided_by"] != "you":
            probs.append(f"{c['key']}: decided_by must be you")
        if len(c["resolves"]) > 1:
            probs.append(f"{c['key']}: resolves {c['resolves']}; one decision must not force another")
        for u in c["resolves"]:
            if u not in ALL_U:
                probs.append(f"{c['key']}: resolves missing {u}")
            resolved.setdefault(u, []).append(c["key"])
        for b in c["ch0_basis"]:
            if b.startswith("T-") or not exists(b) or not is_ch0_ref(b):
                probs.append(f"{c['key']}: basis {b} is not a Chapter 0 reference")
        for t in c["informed_by_hea"]:
            if t not in TECH:
                probs.append(f"{c['key']}: informed_by_hea {t} is not a technique")
        for n in c.get("not_informed_by", []):
            if n["technique"] not in TECH or not n.get("why") or n["technique"] in c["informed_by_hea"]:
                probs.append(f"{c['key']}: not_informed_by must name a technique, say why, and not also use it")
        for d in c.get("decide_with", []):
            if d not in CANDS or d == c["key"]:
                probs.append(f"{c['key']}: decide_with {d} is not another candidate")
        corrects = [u for u in c["resolves"] if NEW_U.get(u, {}).get("classification") == "ch0_correction_candidate"]
        if c.get("decides_as") not in (None, "ch0_correction") or bool(corrects) != (c.get("decides_as") == "ch0_correction"):
            probs.append(f"{c['key']}: decides_as is ch0_correction exactly when it resolves a Chapter 0 correction candidate")
    open_ids = set(NEW_U) | {"U-10", "U-18"}
    for u in sorted(open_ids):
        if len(resolved.get(u, [])) != 1:
            probs.append(f"{u} is resolved by {resolved.get(u, [])}; it needs exactly one candidate")
    corrections = sum(1 for c in DC["candidates"] if c.get("decides_as") == "ch0_correction")
    return probs, (f"{len(DC['candidates'])} candidates, none with an ID; each resolves at most one unknown and every "
                   f"Phase A unknown has exactly one; {corrections} decide{'s' if corrections == 1 else ''} whether to append "
                   "a Chapter 0 correction; no basis cites HEA")


@check("hea.citations")
def _citations():
    probs = []
    for rel, want in HEAT["corpus"]["files"].items():
        p = HEA / rel
        if not p.is_file():
            probs.append(f"{rel} is missing from the extract")
        elif sha(p.read_bytes()) != want:
            probs.append(f"{rel}: sha256 differs from the recorded value")
    n = 0
    for t in HEAT["techniques"]:
        if t["kind"] not in ("technique", "lesson_from_defect") or not t["evidence"]:
            probs.append(f"{t['id']}: needs a kind and evidence")
        for e in t["evidence"]:
            n += 1
            if e["file"] not in HEAT["corpus"]["files"]:
                probs.append(f"{t['id']}: {e['file']} is not in the corpus")
                continue
            lines = (HEA / e["file"]).read_text(encoding="utf-8").splitlines()
            a, b = e["lines"]
            if not (1 <= a <= b <= len(lines)):
                probs.append(f"{t['id']}: lines {a}-{b} outside {e['file']} ({len(lines)} lines)")
            elif e["needle"] not in "\n".join(lines[a - 1:b]):
                probs.append(f"{t['id']}: '{e['needle']}' not found in {e['file']} lines {a}-{b}")
    return probs, f"{len(HEAT['techniques'])} techniques, {n} citations, every quoted phrase found in a file whose hash matches"


@check("claims.classification")
def _claims():
    probs = []
    ids = [c["id"] for c in CLM["claims"]]
    if ids != [f"C-{n:02d}" for n in range(1, len(ids) + 1)]:
        probs.append("claim IDs must run C-01 onwards")
    counts = {}
    expect = {"yes": {"ACCEPTED"}, "partial": {"PARTIALLY_ACCEPTED"}, "no": {"REJECTED"}, "n/a": {"METHOD_ONLY", "PENDING_DECISION"}}
    for c in CLM["claims"]:
        cat = c["category"]
        counts[cat] = counts.get(cat, 0) + 1
        if cat not in CLM["categories"]:
            probs.append(f"{c['id']}: category {cat}")
        if c["verified"] not in CLM["verified_values"]:
            probs.append(f"{c['id']}: verified {c['verified']}")
        allowed = expect.get(c["verified"], set())
        if c["verified"] == "partial" and cat == "Phase-A proposal":
            # A partly verified proposal that still waits on an open candidate can't be cited until you decide it.
            waits = any(r in CANDS and CANDS[r]["status"] == "OPEN" for r in c["refs"])
            allowed = {"PENDING_DECISION"} if waits else {"PARTIALLY_ACCEPTED"}
        if c.get("disposition") not in CLM["dispositions"] or c.get("disposition") not in allowed:
            probs.append(f"{c['id']}: disposition {c.get('disposition')} doesn't fit verified {c['verified']} for a {cat}")
        if c["verified"] in ("partial", "no"):
            if not c["note"] or not c.get("corrected_by"):
                probs.append(f"{c['id']}: a partial or failed check needs a note and corrected_by")
        for r in c.get("corrected_by", []) + c["refs"]:
            if not exists(r):
                probs.append(f"{c['id']}: ref {r} doesn't exist")
        refs = c["refs"]
        if cat == "HEA-derived technique" and not any(r.startswith("T-") for r in refs):
            probs.append(f"{c['id']}: an HEA-derived technique must cite a T- technique")
        if cat == "CH0-required" and not any(is_ch0_ref(r) or GATE.match(r) for r in refs):
            probs.append(f"{c['id']}: a Chapter 0 requirement must cite Chapter 0")
        if cat == "unresolved" and not any(r in ALL_U for r in refs):
            probs.append(f"{c['id']}: an unresolved claim must cite an unknown")
        if cat == "Phase-A proposal" and not any(r.startswith(("dc.", "V-", "P-", "T-", "U-")) for r in refs):
            probs.append(f"{c['id']}: a proposal must cite a candidate, vector or unknown")
    disp = {}
    for c in CLM["claims"]:
        disp[c.get("disposition")] = disp.get(c.get("disposition"), 0) + 1
    return probs, (f"{len(ids)} claims: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
                   + "; " + ", ".join(f"{k} {v}" for k, v in sorted(disp.items())))


@check("claims.normative")
def _normative():
    """REJECTED, METHOD_ONLY and PENDING_DECISION claims may stay as history, but nothing may rest on them."""
    probs = []
    barred = {c["id"] for c in CLM["claims"] if c["disposition"] in ("REJECTED", "METHOD_ONLY", "PENDING_DECISION")}
    sources = []
    for o in OBJS:
        sources += [(o["id"], r) for _, _, b in bindings(o) for r in b["why"]]
        sources += [(o["id"], r) for r in text_refs(" ".join(o["ch1_scope_reason"]))]
        sources += [(o["id"], r) for r in o.get("created_by", {}).get("basis", [])]
    sources += [(f["path"], r) for f in FIELDS for r in f["ch0_refs"] + text_refs(f.get("note", ""))]
    for v in VEC["vectors"]:
        sources += [(v["id"], r) for r in v.get("basis", []) + text_refs(v["description"] + " " + " ".join(v.get("coupling_satisfied", {}).values()))]
    sources += [(c["id"], r) for c in VEC["couplings"] for r in c["basis"] + text_refs(c["rule"])]
    sources += [(p["id"], r) for p in VEC["predicate_tests"] for r in [p["invariant"]] + text_refs(p["rule"] + " " + p.get("note", ""))]
    sources += [(c["key"], r) for c in DC["candidates"] for r in c["ch0_basis"] + text_refs(c["question"])]
    sources += [(k, r) for k, f in INV_A["binding_facts"].items() for r in f["basis"]]
    sources += [(u["id"], r) for u in UNK["new"] for r in text_refs(u.get("ch0_fixes", ""))]
    hits = [(where, r) for where, r in sources if r in barred]
    for where, r in hits:
        probs.append(f"{where} rests on {r}, whose disposition is {CLAIMS[r]['disposition']}")
    return probs, f"{len(barred)} claims kept as history or proposals; no schema rule, vector, coupling or decision rests on them"


@check("review.log")
def _review():
    probs = []
    ids = [r["id"] for r in RV["findings"]]
    if ids != [f"RV-{n:02d}" for n in range(1, len(ids) + 1)]:
        probs.append("review IDs must run RV-01 onwards")
    for r in RV["findings"]:
        if r["verified"] not in RV["verified_values"] or not r["finding"] or not r["changes"]:
            probs.append(f"{r['id']}: needs a finding, a verdict and its changes")
        for e in r["evidence"]:
            if not exists(e):
                probs.append(f"{r['id']}: evidence {e} doesn't exist")
        for x in text_refs(" ".join(r["changes"])):
            if not exists(x):
                probs.append(f"{r['id']}: changes cite {x}, which doesn't exist")
    return probs, f"{len(ids)} review findings, each verified, with its changes resolved"


def counts():
    forms = {}
    for o in OBJS:
        forms[o["schema_form"]] = forms.get(o["schema_form"], 0) + 1
    cats, disp, status = {}, {}, {}
    for c in CLM["claims"]:
        cats[c["category"]] = cats.get(c["category"], 0) + 1
        disp[c["disposition"]] = disp.get(c["disposition"], 0) + 1
    for v in VEC["vectors"]:
        status[v["status"]] = status.get(v["status"], 0) + 1
    return {
        "object types": len(OBJS),
        "protocol objects": forms.get("protocol_object", 0),
        "embedded contract parts": forms.get("embedded_contract_part", 0),
        "ledger state rows": forms.get("ledger_state_row", 0),
        "append-only journal records": forms.get("append_only_journal_record", 0),
        "raw witnesses": forms.get("raw_witness", 0),
        "legacy read-only": forms.get("legacy_read_only", 0),
        "objects required in Chapter 1": sum(1 for o in OBJS if o["ch1_scope"] == "required"),
        "objects with conditional bindings": sum(1 for o in OBJS if groups(o)),
        "binding rules": sum(len(groups(o)) for o in OBJS),
        "binding rules with provenance": sum(1 for o in OBJS for g in groups(o) if "provenance" in g),
        "effect results": len(RESULTS),
        "objects with lifecycle creation rules": sum(1 for o in OBJS if o.get("created_by")),
        "identity fields": len(FIELDS),
        "freeze-time bindings": len(RG0["registrations"]),
        "field couplings": len(VEC["couplings"]),
        "golden vectors": len(VEC["vectors"]),
        "vectors with tier effect specified": status.get("tier_effect_specified", 0),
        "vectors with rejection specified": status.get("rejection_specified", 0),
        "executable vectors": status.get("executable_vector", 0),
        "blocked vectors": status.get("blocked", 0),
        "predicate tests": len(VEC["predicate_tests"]),
        "brief claims": len(CLM["claims"]),
        "claims CH0-required": cats.get("CH0-required", 0),
        "claims HEA-derived technique": cats.get("HEA-derived technique", 0),
        "claims Phase-A proposal": cats.get("Phase-A proposal", 0),
        "claims unresolved": cats.get("unresolved", 0),
        "claims accepted": disp.get("ACCEPTED", 0),
        "claims partially accepted": disp.get("PARTIALLY_ACCEPTED", 0),
        "claims rejected": disp.get("REJECTED", 0),
        "claims method only": disp.get("METHOD_ONLY", 0),
        "claims pending decision": disp.get("PENDING_DECISION", 0),
        "HEA techniques": sum(1 for t in HEAT["techniques"] if t["kind"] == "technique"),
        "HEA lessons from defects": sum(1 for t in HEAT["techniques"] if t["kind"] == "lesson_from_defect"),
        "inherited unknowns": len(UNK["inherited"]),
        "new unknowns": len(UNK["new"]),
        "NARROWED unknowns": sum(1 for u in UNK["new"] if u["status"] == "NARROWED"),
        "Chapter 0 correction candidates": sum(1 for u in UNK["new"] if u["classification"] == "ch0_correction_candidate"),
        "decision candidates": len(DC["candidates"]),
        "review findings": len(RV["findings"]),
        "review findings from your reviews": sum(1 for r in RV["findings"] if r["source"].startswith("your ")),
        "review findings from self-checks": sum(1 for r in RV["findings"] if "self-check" in r["source"]),
        "review findings from the independent review": sum(1 for r in RV["findings"] if r["source"].startswith("independent review")),
        "decide_with pairs": len({frozenset((c["key"], d)) for c in DC["candidates"] for d in c.get("decide_with", [])}),
        "checks": len(CHECKS),
    }


MACHINE_PATH_PATTERNS = [
    re.compile("[A-Za-z]" + ":" + r"[\\/]" + "[A-Za-z]"),
    re.compile(r"[\\/]" + "Us" + "ers" + r"[\\/]"),
    re.compile(r"[\\/]" + "ho" + "me" + r"[\\/][a-z]"),
    re.compile("App" + "Data"),
]


@check("docs.consistency")
def _docs():
    if not DOC.is_file():
        return ["CHAPTER1_PHASE_A.md is missing"], ""
    probs = []
    text = DOC.read_text(encoding="utf-8")
    for ref in sorted(set(text_refs(text))):
        if not exists(ref):
            probs.append(f"the document cites {ref}, which doesn't exist")
    # Every ID cited anywhere in the registries resolves, notes and free text included.
    for path in sorted(REG.glob("*.json")):
        for ref in sorted(set(text_refs(json.dumps(load(path), ensure_ascii=False)))):
            if not exists(ref):
                probs.append(f"{path.name} cites {ref}, which doesn't exist")
    # The document's list of what V-A needs is the candidates for V-A's unknowns and the shared fixture unknowns.
    va = re.search(r"<!-- va-candidates -->(.*?)<!-- /va-candidates -->", text, re.S)
    want = sorted({c["key"] for c in DC["candidates"] for u in c["resolves"] if u in ALWAYS | set(VECTORS["V-A"]["blocked_by"])})
    if not va:
        probs.append("the list of candidates V-A needs is missing")
    elif sorted(re.findall(r"dc\.[a-z][a-z-]*[a-z]", va.group(1))) != want:
        probs.append(f"the document's list for V-A differs from the registries: {want}")
    block = re.search(r"<!-- phase-a-counts -->(.*?)<!-- /phase-a-counts -->", text, re.S)
    if not block:
        probs.append("the counts table is missing")
    else:
        quoted = dict(re.findall(r"^\| ([^|]+?) \| (\d+) \|$", block.group(1), re.M))
        for k, v in counts().items():
            if str(v) != quoted.get(k):
                probs.append(f"count '{k}': document says {quoted.get(k)}, registries give {v}")
    for path in [DOC, REGISTER, Path(__file__)] + sorted(REG.glob("*.json")):
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(line) for p in MACHINE_PATH_PATTERNS):
                probs.append(f"{path.name}:{n}: looks like a machine path")
    return probs, "every ID cited in the document and the registries exists, the counts and V-A's list match, no machine paths"


def esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def binding_text(o):
    parts = []
    if o.get("common_bindings"):
        parts.append("always: " + ", ".join(b["target"] for b in o["common_bindings"]))
    for g in groups(o):
        when = " and ".join(f"{k} {'|'.join(as_list(v))}" for k, v in g["when"].items())
        what = ", ".join(b["target"] for b in g.get("bindings", [])) or "nothing more"
        if g.get("open"):
            what = f"open ({', '.join(g['open'])})"
        prov = f" (provenance {', '.join(provenance(g))})" if "provenance" in g else ""
        parts.append(f"when {when}{prov}: {what}")
    return "; ".join(parts)


def render():
    parts = ["# Chapter 1 Phase A registers", "", "Generated by `check_phase_a.py --render` from `registries/*.json`. Don't edit by hand.", ""]
    parts += ["## Objects", "", table(
        ["ID", "Name", "Class", "Schema form", "Authority", "Kinds", "Hash", "Chapter 1", "First runtime chapter", "Bindings", "Unknowns"],
        [[o["id"], o["name"], o["ch0_class"], o["schema_form"], o["authority_effect"], ", ".join(o["kinds"]), o["canonical_hash"],
          o["ch1_scope"], o["first_runtime_chapter"], binding_text(o), ", ".join(o["unknowns"])] for o in OBJS]), ""]
    parts += ["## Lifecycle creation rules", "", INV_A["creation_forms"]["note"], "", table(["Object", "Form", "Coupled to", "Rule", "Basis"],
        [[o["id"], o["created_by"]["form"], o["created_by"].get("coupled_to", ""), o["created_by"]["rule"], ", ".join(o["created_by"]["basis"])]
         for o in OBJS if o.get("created_by")]), ""]
    bc = INV_A["binding_conditions"]
    labels = {"binding_set": "Binding set", "conditional_bindings": "Rules", "partitions": "Partitions", "provenance": "Provenance"}
    parts += ["## Binding grammar", ""] + [x for k, label in labels.items() for x in (f"**{label}.** {bc[k]}", "")]
    parts += [table(["Selector", "Meaning"], [[k, v] for k, v in bc["selectors"].items()]), ""]
    parts += ["## Effect results", "", INV_A["effect_results"]["note"], "", table(["Result", "Meaning", "Established by", "Basis"],
        [[k, r["meaning"], ", ".join(r["established_by"]), ", ".join(r["basis"])] for k, r in RESULTS.items()]), ""]
    parts += [f"PONR-only bindings: {', '.join(INV_A['ponr_only_bindings']['members'])}. {INV_A['ponr_only_bindings']['rule']}", ""]
    parts += ["## Binding facts", "", table(["Fact", "Meaning", "Basis", "Members"],
        [[k, f["meaning"], ", ".join(f["basis"] + f.get("open", [])), ", ".join(f.get("members", []))] for k, f in INV_A["binding_facts"].items()]), ""]
    parts += ["## Identity fields", "", table(
        ["Field", "Tier", "Required placement", "Semantic type", "Shape", "Chapter 0", "Open"],
        [[f["path"], f["tier"], "yes" if f.get("required_placement") else "", f["semantic_type"], f["shape"], ", ".join(f["ch0_refs"]), ", ".join(f["open"])] for f in FIELDS]), ""]
    parts += ["## Field couplings", "", table(["ID", "Fields", "Rule", "Basis", "Status", "Pending on", "Open"],
        [[c["id"], ", ".join(c["fields"]), c["rule"], ", ".join(c["basis"]), c["status"], ", ".join(c.get("pending_on", [])), ", ".join(c.get("open", []))] for c in VEC["couplings"]]), ""]
    parts += ["## Golden vectors", "", VEC["note"], "", f"Every fixture waits on: {', '.join(VEC['fixture_waits_for_all'])}.", "", table(
        ["ID", "From", "Kind", "Fields", "Requirement", "Contract", "Record", "Status", "Waits on", "Tests"],
        [[v["id"], v["brief"], v["kind"], ", ".join([v.get("field", "")] + v.get("coupled_fields", [])).strip(", "),
          (v.get("expected") or {}).get("requirement_digest", v.get("expected", "")) if isinstance(v.get("expected"), dict) else v.get("expected", ""),
          (v.get("expected") or {}).get("contract_digest", "") if isinstance(v.get("expected"), dict) else "",
          (v.get("expected") or {}).get("record_hash", "") if isinstance(v.get("expected"), dict) else "",
          v["status"], ", ".join(v.get("blocked_by", []) or v.get("fixture_blocked_by", [])), ", ".join(v.get("tests", []))] for v in VEC["vectors"]]), ""]
    parts += ["## Predicate tests", "", table(["ID", "Test", "Invariant", "Rule", "Status", "Blocked by"],
        [[p["id"], p["test"], p["invariant"], p["rule"], p["status"], ", ".join(p.get("blocked_by", []))] for p in VEC["predicate_tests"]]), ""]
    parts += ["## Brief claims", "", table(["ID", "Item", "Category", "Verified", "Disposition", "Statement", "References", "Corrected by", "Note"],
        [[c["id"], c["item"], c["category"], c["verified"], c["disposition"], c["statement"], ", ".join(c["refs"]),
          ", ".join(c.get("corrected_by", [])), c["note"]] for c in CLM["claims"]]), ""]
    parts += ["## HEA proved techniques", "", HEAT["rule"], "", table(["ID", "Kind", "Technique", "Evidence"],
        [[t["id"], t["kind"], t["technique"], "; ".join(f"{e['file']}:{e['lines'][0]}-{e['lines'][1]}" for e in t["evidence"])] for t in HEAT["techniques"]]), ""]
    parts += ["## HEA-specific semantics not carried", "", table(["Item", "Why"], [[x["item"], x["why"]] for x in HEAT["hea_specific_not_carried"]]), ""]
    parts += ["## Autobot decision candidates", "", DC["rule"], "", table(["Key", "Question", "Resolves", "Decides as", "Chapter 0 basis", "Informed by HEA", "Not informed by", "Decide with"],
        [[c["key"], c["question"], ", ".join(c["resolves"]), c.get("decides_as", ""), ", ".join(c["ch0_basis"]), ", ".join(c["informed_by_hea"]),
          ", ".join(n["technique"] for n in c.get("not_informed_by", [])), ", ".join(c.get("decide_with", []))] for c in DC["candidates"]]), ""]
    parts += ["## Inherited unknowns", "", table(["ID", "Question", "Status", "Already fixed by Chapter 0", "Remaining"],
        [[u["id"], u["question"], u["status"], u["already_fixed_by_ch0"], u["remaining"]] for u in UNK["inherited"]]), ""]
    parts += [f"Carried unchanged from Chapter 0: {', '.join(UNK['carried_unchanged'])}.", ""]
    parts += ["## New unknowns", "", UNK["principle"], "", table(
        ["ID", "Status", "Classification", "Question (as first defined)", "Remaining scope", "Refinement", "Chapter 0 fixes", "Open", "Blocks", "Next probe", "Lineage"],
        [[u["id"], u["status"], u["classification"], u["question"], u.get("remaining", ""), u.get("refinement", ""), u["ch0_fixes"], u["open"], u["blocks"], u["next_probe"],
          "; ".join(x for x in [
              f"split from {u['split_from']} ({u['split_by']})" if "split_from" in u else "",
              f"narrowed by {u['narrowed_by']}, split to {', '.join(u.get('split_to', []))}" if "narrowed_by" in u else "",
              f"refined by {u['refined_by']}" if "refined_by" in u else ""] if x)] for u in UNK["new"]]), ""]
    for u in [x for x in UNK["new"] if x["classification"] == "ch0_correction_candidate"]:
        pc = u["proposed_correction"]
        parts += [f"## Chapter 0 correction candidate {u['id']}", "", UNK["classifications"][u["classification"]], "",
                  f"Covers: {', '.join(u.get('covers_effects', []) + u.get('covers_transitions', []))}. Decided by: {pc['decided_by']}. Applied: {'yes' if pc['applied'] else 'no'}.", "",
                  u["ch0_checker"], "", "To discover:", ""]
        parts += [f"- {d}" for d in u["discover"]] + ["", "Proposed correction:", ""]
        parts += [f"- Keep: {k}" for k in pc.get("keep", [])]
        parts += [f"- Refine {r['target']} toward: {r['toward']}" for r in pc.get("refine", [])]
        parts += [f"- Add: {a}" for a in pc.get("add", [])]
        parts += [f"- Acknowledge: {a}" for a in pc.get("acknowledge", [])] + [""]
    parts += ["## Review log", "", RV["note"], "", table(["ID", "Source", "Verified", "Finding", "Changes"],
        [[r["id"], r["source"], r["verified"], r["finding"], " ".join(r["changes"])] for r in RV["findings"]]), ""]
    return "\n".join(parts)


@check("docs.registers_current")
def _registers():
    if args.render:
        REGISTER.write_text(render() + "\n", encoding="utf-8", newline="\n")
        return [], "wrote CHAPTER1_PHASE_A_REGISTERS.md"
    if not REGISTER.is_file():
        return ["CHAPTER1_PHASE_A_REGISTERS.md is missing; run with --render"], ""
    current = REGISTER.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    return ([] if current == render().strip() else ["CHAPTER1_PHASE_A_REGISTERS.md is stale; run --render"]), "the register matches the registries"


def main():
    ran = failed = skipped = 0
    for name, fn in CHECKS:
        try:
            probs, detail = fn()
        except Exception as exc:  # a crash is a failure, never a pass
            probs, detail = [f"{type(exc).__name__}: {exc}"], ""
        if probs is None:
            skipped += 1
            print(f"SKIP {name}: {detail}")
            continue
        ran += 1
        if probs:
            failed += 1
            print(f"FAIL {name}: {detail}")
            for p in probs:
                print(f"     {p}")
        else:
            print(f"PASS {name}: {detail}")
    print()
    print(f"{ran - failed} of {ran} checks passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
