#!/usr/bin/env python3
"""Phase C closure checker for Autobot Chapter 0.

Reads the JSON registries in ./registries and recomputes, instead of trusting,
what Chapter 0 claims:
  - one owner per capability and one minter per object, of an allowed kind;
  - every transition applied by the seat that owns it, minting only what its
    participants may mint;
  - exactly one point of no return (PONR) per finalization path, with nothing
    crossing it backwards;
  - every ref or push effect under a bounded EffectIntent, canonical effects
    under an active delegation and a permit reservation;
  - graph properties: no unverified acceptance, no uncommitted source
    acceptance, PUBLISHED_UNVERIFIED releases nothing, stale or fenced attempts
    are dead ends, revocation covers every pre-PONR state, every state has an
    exit and is reachable;
  - identity tiers that don't leak, bindings resolved at freeze, an acyclic
    authority graph with every forbidden edge absent;
  - corrections, decisions, unknowns and invariants complete and testable.

When the Chapter 0 documents sit next to the registries folder, it also checks
that they cite only IDs the registries define, quote the registry counts
exactly, carry an up-to-date generated register, and contain no machine paths.

Standard library only. Reads the registries folder (./registries, or the folder
given as an argument) and the documents beside it. Writes nothing, except that
`--render` regenerates CHAPTER0_REGISTERS.md. Prints one line per check and
exits 1 if any check fails.
"""
import json
import re
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
RENDER = "--render" in sys.argv[1:]
REG = Path(ARGS[0]).resolve() if ARGS else HERE / "registries"
DOCS = REG.parent
DOC_FILES = ["README.md", "CHAPTER0_ARCHITECTURE.md", "CHAPTER0_CLOSURE.md"]
REGISTER_FILE = "CHAPTER0_REGISTERS.md"
HANDOFFS = DOCS.parent.parent / "implementation" / "handoffs"

CHECKS = []


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn
    return wrap


def load(name):
    with (REG / name).open(encoding="utf-8") as f:
        return json.load(f)


P = {p["id"]: p for p in load("principals.json")["principals"]}
CAPS = load("capabilities.json")["capabilities"]
CAP = {c["id"]: c for c in CAPS}
OBJ_DOC = load("objects.json")
OBJS = OBJ_DOC["objects"]
OBJ = {o["id"]: o for o in OBJS}
MACHINES = load("states.json")["machines"]
TR = load("transitions.json")
TRANS = TR["transitions"]
WILD = TR["wildcards"]
ALL_T = TRANS + WILD
EFF = {e["id"]: e for e in TR["effects"]}
PATHS = {p["id"]: p for p in TR["paths"]}
GROUPS = TR["path_groups"]
RULES = TR["outcome_rules"]
POLICY = TR["policy_defaults"]
IDENT = load("identity.json")
REGS = load("registrations.json")
EDGES = load("authority_edges.json")
INV_DOC = load("invariants.json")
INVS = INV_DOC["invariants"]
CORR_DOC = load("corrections.json")
CORRS = CORR_DOC["corrections"]
DEC_DOC = load("decisions.json")
DECS = DEC_DOC["decisions"]
UNK_DOC = load("unknowns.json")
UNKS = UNK_DOC["unknowns"]

STATE_MACHINES_APPLIED_BY_SEQUENCER = {
    "sequence", "attempt", "effect_intent", "permit_reservation", "forward_repair_obligation",
}


def states(machine):
    return {s["id"]: s for s in MACHINES[machine]["states"]}


TERMINAL = {m: {s for s, d in states(m).items() if d["terminal"]} for m in MACHINES}


def expand(tags):
    out = set()
    for tag in tags or []:
        out.update(GROUPS.get(tag, [tag]))
    return out


def transition(tid):
    return next((t for t in ALL_T if t["id"] == tid), None)


def path_split(pid):
    """Return (pre-PONR states, post-PONR states, PONR transition) for a path."""
    p = PATHS[pid]
    t = transition(p["ponr_transition"])
    ordered = p["ordered_states"]
    idx = ordered.index(t["to"])
    return list(ordered[:idx]) + list(p["side_pre_ponr"]), list(ordered[idx:]), t


def wildcard_edges():
    out = []
    for w in WILD:
        if w["machine"] == "sequence" and w.get("from_any_pre_ponr"):
            for pid in expand(w["paths"]):
                pre, _, _ = path_split(pid)
                for s in pre:
                    if s not in TERMINAL["sequence"] and s != w["to"]:
                        out.append((s, w["to"], w, pid))
        elif w.get("from_any_nonterminal"):
            for s in states(w["machine"]):
                if s not in TERMINAL[w["machine"]] and s != w["to"]:
                    out.append((s, w["to"], w, None))
    return out


WEDGES = wildcard_edges()


def seq_adjacency(include, exclude_ids=(), drop_states=()):
    adj = defaultdict(set)
    for t in TRANS:
        if t["machine"] != "sequence" or t["id"] in exclude_ids or not include(t):
            continue
        if t["from"] in drop_states or t["to"] in drop_states:
            continue
        adj[t["from"]].add(t["to"])
    return adj


def reachable(adj, start):
    seen, queue = {start}, deque([start])
    while queue:
        for nxt in adj.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def dups(ids):
    return [f"duplicate id {i}" for i, n in Counter(ids).items() if n > 1]


# ---------------------------------------------------------------- ownership


@check("principals.unique")
def _principals():
    ids = [p["id"] for p in load("principals.json")["principals"]]
    return dups(ids), f"{len(ids)} principals"


@check("capabilities.single_owner")
def _capabilities():
    probs = dups([c["id"] for c in CAPS])
    for c in CAPS:
        owner = c.get("owner")
        if not isinstance(owner, str) or not owner:
            probs.append(f"{c['id']}: owner must be exactly one principal")
        elif owner not in P:
            probs.append(f"{c['id']}: unknown owner {owner}")
    by_kind = Counter()
    for c in CAPS:
        kind = P.get(c["owner"], {}).get("kind", "?")
        by_kind["you" if kind == "human" else "Autobot seats" if kind == "autobot_seat" else "external"] += 1
    info = f"{len(CAPS)} capabilities: you {by_kind['you']}, Autobot seats {by_kind['Autobot seats']}, external {by_kind['external']}"
    return probs, info


@check("capabilities.references")
def _capability_refs():
    probs = []
    for c in CAPS:
        for other in c.get("others", []):
            if other["principal"] not in P:
                probs.append(f"{c['id']}: unknown principal {other['principal']}")
            if other["level"] not in ("C", "R", "O"):
                probs.append(f"{c['id']}: unknown level {other['level']}")
        for f in c.get("forbidden", []):
            if f not in P:
                probs.append(f"{c['id']}: unknown forbidden principal {f}")
            if f == c.get("owner"):
                probs.append(f"{c['id']}: the owner is also forbidden")
    return probs, ""


@check("objects.minters")
def _objects():
    probs = dups([o["id"] for o in OBJS])
    classes = set(OBJ_DOC["classes"])
    for o in OBJS:
        if not isinstance(o.get("minter"), str) or o["minter"] not in P:
            probs.append(f"{o['id']}: minter must be exactly one known principal")
        if not o.get("mutability"):
            probs.append(f"{o['id']}: mutability not declared")
        if o.get("class") not in classes:
            probs.append(f"{o['id']}: unknown class {o.get('class')}")
    return probs, f"{len(OBJS)} object types"


@check("objects.minter_kinds")
def _object_kinds():
    probs = []
    autobot_classes = {
        "receipt", "non_authoritative_receipt", "journal", "state", "contract", "contract_part",
        "intent", "permit_state", "obligation", "lease", "legacy",
    }
    for o in OBJS:
        pr = P.get(o["minter"])
        if not pr:
            continue
        if pr["llm"] or pr["kind"] == "agent":
            probs.append(f"{o['id']} {o['name']}: minted by an LLM or agent ({pr['id']})")
        cls = o["class"]
        if cls == "witness" and pr["kind"] != "external_program":
            probs.append(f"{o['id']}: a witness must be minted by its external producer")
        if cls == "authority_record" and pr["kind"] not in ("human", "autobot_seat"):
            probs.append(f"{o['id']}: an authority record must come from you or an Autobot seat")
        if cls in autobot_classes and pr["kind"] != "autobot_seat":
            probs.append(f"{o['id']}: {cls} must be minted by a deterministic Autobot seat, not {pr['id']}")
    return probs, ""


# ---------------------------------------------------------------- transitions


@check("states.machines")
def _states():
    probs = []
    for m, d in MACHINES.items():
        ids = [s["id"] for s in d["states"]]
        probs += [f"{m}: {x}" for x in dups(ids)]
        if d["initial"] not in ids:
            probs.append(f"{m}: initial state {d['initial']} not declared")
        for s in d["states"]:
            if s["terminal"] and s["human_queue"]:
                probs.append(f"{m}.{s['id']}: both terminal and waiting for you")
    return probs, f"{len(MACHINES)} state machines"


@check("transitions.references")
def _transition_refs():
    probs = dups([t["id"] for t in ALL_T])
    for t in ALL_T:
        m = t["machine"]
        if m not in MACHINES:
            probs.append(f"{t['id']}: unknown machine {m}")
            continue
        st = states(m)
        if "from" in t:
            if t["from"] not in st:
                probs.append(f"{t['id']}: unknown state {t['from']}")
            elif t["from"] in TERMINAL[m]:
                probs.append(f"{t['id']}: leaves terminal state {t['from']}")
        if t["to"] not in st:
            probs.append(f"{t['id']}: unknown state {t['to']}")
        for o in t.get("requires", []) + t.get("mints", []):
            if o not in OBJ:
                probs.append(f"{t['id']}: unknown object {o}")
        for c in t.get("capabilities", []):
            if c not in CAP:
                probs.append(f"{t['id']}: unknown capability {c}")
        if t.get("trigger_capability") and t["trigger_capability"] not in CAP:
            probs.append(f"{t['id']}: unknown trigger capability {t['trigger_capability']}")
        for k in ("actor", "trigger"):
            if t.get(k) and t[k] not in P:
                probs.append(f"{t['id']}: unknown {k} {t[k]}")
        for k in ("effect", "intent_for"):
            if t.get(k) and t[k] not in EFF:
                probs.append(f"{t['id']}: unknown effect {t[k]}")
        if m == "sequence":
            if not t.get("paths"):
                probs.append(f"{t['id']}: a sequence transition must name its paths")
            for pid in expand(t.get("paths")):
                if pid not in PATHS:
                    probs.append(f"{t['id']}: unknown path {pid}")
    info = f"{len(TRANS)} transitions, {len(WILD)} wildcards expanding to {len(WEDGES)} edges"
    return probs, info


@check("transitions.actor_owns_capability")
def _actor_caps():
    probs = []
    for t in ALL_T:
        for c in t.get("capabilities", []):
            if c in CAP and CAP[c]["owner"] != t["actor"]:
                probs.append(f"{t['id']}: actor {t['actor']} does not own {c} (owner {CAP[c]['owner']})")
        tc = t.get("trigger_capability")
        if tc in CAP and CAP[tc]["owner"] != t.get("trigger"):
            probs.append(f"{t['id']}: trigger {t.get('trigger')} does not own {tc} (owner {CAP[tc]['owner']})")
        if t["machine"] in STATE_MACHINES_APPLIED_BY_SEQUENCER and t["actor"] != "sequencer":
            probs.append(f"{t['id']}: only the Sequencer applies {t['machine']} transitions")
        if t["machine"] == "contract_admission" and t["actor"] != "registry":
            probs.append(f"{t['id']}: only the Registry admits contracts")
    return probs, ""


@check("transitions.minted_by_participant")
def _minted_by():
    probs = []
    for t in ALL_T:
        allowed = {t["actor"]}
        if t.get("trigger"):
            allowed.add(t["trigger"])
        if t.get("effect") in EFF:
            allowed.add(EFF[t["effect"]]["executor"])
        for o in t.get("mints", []):
            if o in OBJ and OBJ[o]["minter"] not in allowed:
                probs.append(f"{t['id']} mints {o} ({OBJ[o]['name']}) whose minter is {OBJ[o]['minter']}")
    return probs, ""


@check("transitions.path_membership")
def _path_membership():
    probs = []
    for t in TRANS:
        if t["machine"] != "sequence":
            continue
        for pid in expand(t["paths"]):
            p = PATHS.get(pid)
            if not p:
                continue
            on = set(p["ordered_states"]) | set(p["side_pre_ponr"])
            if t["from"] not in on:
                probs.append(f"{t['id']}: {t['from']} is not on path {pid}")
            if t["to"] not in on and t["to"] not in TERMINAL["sequence"]:
                probs.append(f"{t['id']}: {t['to']} is not on path {pid}")
    return probs, ""


# ---------------------------------------------------------------- PONR


@check("ponr.per_path")
def _ponr():
    probs = []
    for pid, p in PATHS.items():
        tagged = [t for t in TRANS if t["machine"] == "sequence" and pid in expand(t["paths"]) and t.get("ponr")]
        if len(tagged) != 1:
            probs.append(f"{pid}: {len(tagged)} PONR transitions ({', '.join(t['id'] for t in tagged)})")
        elif tagged[0]["id"] != p["ponr_transition"]:
            probs.append(f"{pid}: declared PONR {p['ponr_transition']} but tagged {tagged[0]['id']}")
        t = transition(p["ponr_transition"])
        if not t:
            probs.append(f"{pid}: unknown PONR transition {p['ponr_transition']}")
            continue
        o = p["ordered_states"]
        if t["from"] not in o or t["to"] not in o or o.index(t["to"]) != o.index(t["from"]) + 1:
            probs.append(f"{pid}: PONR {t['id']} is not one step of the path")
        if p.get("source_modifying"):
            e = EFF.get(t.get("effect"))
            if not e:
                probs.append(f"{pid}: a source-modifying PONR must be an effect")
            elif pid not in e["canonical_on"]:
                probs.append(f"{pid}: PONR effect {e['id']} is not canonical on this path")
    return probs, f"{len(PATHS)} finalization paths"


@check("ponr.no_backward")
def _no_backward():
    probs = []
    for pid in PATHS:
        pre, post, _ = path_split(pid)
        preset, postset = set(pre), set(post)
        both = preset & postset
        if both:
            probs.append(f"{pid}: states on both sides of the PONR: {sorted(both)}")
        for t in TRANS:
            if t["machine"] != "sequence" or pid not in expand(t["paths"]) or t["from"] not in postset:
                continue
            if t["to"] in preset:
                probs.append(f"{pid}: {t['id']} crosses back over the PONR ({t['from']} -> {t['to']})")
            elif t["to"] not in postset and t["to"] not in TERMINAL["sequence"]:
                probs.append(f"{pid}: {t['id']} leaves the path after the PONR ({t['from']} -> {t['to']})")
        for f, to, w, wp in WEDGES:
            if w["machine"] == "sequence" and wp == pid and f in postset:
                probs.append(f"{pid}: wildcard {w['id']} fires after the PONR from {f}")
    return probs, ""


# ---------------------------------------------------------------- effects


@check("effects.definitions")
def _effects():
    probs = []
    for e in EFF.values():
        if not e.get("requires_intent"):
            probs.append(f"{e['id']}: does not require an EffectIntent")
        ex = e["executor"]
        cap = CAP.get(e["capability"])
        if ex not in P:
            probs.append(f"{e['id']}: unknown executor {ex}")
        if not cap:
            probs.append(f"{e['id']}: unknown capability {e['capability']}")
        elif cap["owner"] != ex:
            probs.append(f"{e['id']}: executor {ex} does not own {e['capability']}")
        r = OBJ.get(e["receipt"])
        if not r:
            probs.append(f"{e['id']}: unknown receipt {e['receipt']}")
        elif r["minter"] != ex:
            probs.append(f"{e['id']}: receipt {r['id']} is minted by {r['minter']}, not the executor")
        if e["outcome_rule"] not in RULES:
            probs.append(f"{e['id']}: unknown outcome rule {e['outcome_rule']}")
        for pid in e["canonical_on"]:
            if pid not in PATHS:
                probs.append(f"{e['id']}: unknown path {pid}")
        if e["type"] in ("STAGING_PUSH", "STAGING_REF_DELETE") and e["canonical_on"]:
            probs.append(f"{e['id']}: a staging effect can never be canonical")
        if not e.get("standalone") and not any(t.get("effect") == e["id"] for t in TRANS):
            probs.append(f"{e['id']}: no transition performs it")
    return probs, f"{len(EFF)} effect types"


@check("effects.outcome_rules")
def _outcome_rules():
    probs = []
    intent_states = set(states("effect_intent"))
    for name, rule in RULES.items():
        for key, val in rule.items():
            if key != "note" and val not in intent_states:
                probs.append(f"{name}.{key}: {val} is not an EffectIntent state")
        if name in ("remote_cas", "remote_delete") and rule.get("target_equals_expected_old") != "UNKNOWN_OUTCOME":
            probs.append(f"{name}: the expected old value must be UNKNOWN_OUTCOME, never ABSENT (CORR-49)")
        if name == "remote_create" and rule.get("target_absent") != "UNKNOWN_OUTCOME":
            probs.append(f"{name}: an absent target must be UNKNOWN_OUTCOME (CORR-49)")
        if name.startswith("remote") and rule.get("unobservable") != "UNKNOWN_OUTCOME":
            probs.append(f"{name}: an unobservable target must be UNKNOWN_OUTCOME")
        if name == "local":
            for key, val in rule.items():
                if "expected_old" in key and val == "ABSENT" and "executor_fenced" not in key:
                    probs.append(f"local.{key}: ABSENT at the old value needs a fenced executor")
    for e in EFF.values():
        remote = e["type"] in ("STAGING_PUSH", "CANONICAL_PUSH", "STAGING_REF_DELETE")
        if remote and not e["outcome_rule"].startswith("remote"):
            probs.append(f"{e['id']}: a remote effect needs a remote outcome rule")
    return probs, ""


@check("effects.require_intent")
def _require_intent():
    probs = []
    seq = [t for t in TRANS if t["machine"] == "sequence"]
    for t in seq:
        if t.get("effect"):
            e = EFF[t["effect"]]
            feeders = [x for x in seq if x.get("intent_for") == t["effect"] and x["to"] == t["from"]
                       and expand(x["paths"]) & expand(t["paths"])]
            if not feeders:
                probs.append(f"{t['id']}: no intent-issuing transition for {t['effect']} leads into {t['from']}")
            if "effect_happened" not in t["preconditions"]:
                probs.append(f"{t['id']}: performs {t['effect']} without effect_happened")
            if e["receipt"] not in t.get("requires", []):
                probs.append(f"{t['id']}: does not require the effect's receipt {e['receipt']}")
        if t.get("intent_for"):
            e = EFF[t["intent_for"]]
            pre = set(t["preconditions"])
            if "obj-25" not in t.get("mints", []):
                probs.append(f"{t['id']}: issues an intent but mints no EffectIntent")
            for needed in ("effect_intent_committed", "intent_ttl_gt_hard_timeout", "fence_matches"):
                if needed not in pre:
                    probs.append(f"{t['id']}: intent without {needed}")
            externally_visible = e["type"] in ("STAGING_PUSH", "CANONICAL_PUSH", "STAGING_REF_DELETE")
            canonical_here = set(e["canonical_on"]) & expand(t["paths"])
            if (canonical_here or externally_visible) and "delegation_active_at_intent" not in pre:
                probs.append(f"{t['id']}: {e['id']} intent without delegation_active_at_intent")
            if canonical_here and not ({"permit_reserved", "reservation_held"} & pre):
                probs.append(f"{t['id']}: canonical intent without a permit reservation")
    return probs, ""


@check("fence.attempt_bound_receipts")
def _fence():
    probs = []
    bound = {o["id"] for o in OBJS if o.get("attempt_bound")}
    for t in ALL_T:
        if t["machine"] not in ("sequence", "attempt"):
            continue
        touched = (set(t.get("requires", [])) | set(t.get("mints", []))) & bound
        if touched and "fence_matches" not in t["preconditions"]:
            probs.append(f"{t['id']}: uses attempt-bound {sorted(touched)} without fence_matches")
    return probs, f"{len(bound)} attempt-bound object types"


# ---------------------------------------------------------------- graph properties


@check("graph.no_unverified_acceptance")
def _no_unverified():
    probs = []
    adj = seq_adjacency(lambda t: not t.get("reuse"), drop_states={"VERIFIED"})
    if "ACCEPTED" in reachable(adj, MACHINES["sequence"]["initial"]):
        probs.append("ACCEPTED is reachable without VERIFIED and without a reuse receipt")
    for t in TRANS:
        if t["machine"] == "sequence" and t["to"] == "VERIFIED" and t["from"] == "VERIFYING":
            if "obj-19" not in t["requires"]:
                probs.append(f"{t['id']}: VERIFIED without a VerificationReceipt")
        if t.get("reuse"):
            if "obj-31" not in t["mints"]:
                probs.append(f"{t['id']}: reuse without a FulfillmentReuseReceipt")
            for needed in ("same_requirement_digest", "dominating_finalization_proof", "exact_canonical_head_if_source"):
                if needed not in t["preconditions"]:
                    probs.append(f"{t['id']}: reuse without {needed}")
    return probs, ""


@check("graph.no_uncommitted_source_acceptance")
def _no_uncommitted():
    probs = []
    for pid, p in PATHS.items():
        if not p.get("source_modifying"):
            continue
        adj = seq_adjacency(lambda t, pid=pid: pid in expand(t["paths"]), exclude_ids={p["ponr_transition"]})
        if "ACCEPTED" in reachable(adj, MACHINES["sequence"]["initial"]):
            probs.append(f"{pid}: ACCEPTED is reachable without its PONR {p['ponr_transition']}")
    return probs, ""


@check("graph.published_unverified_releases_nothing")
def _published_unverified():
    probs = []
    if "PUBLISHED_UNVERIFIED" not in TERMINAL["sequence"]:
        probs.append("PUBLISHED_UNVERIFIED must be terminal")
    if any(t["machine"] == "sequence" and t["from"] == "PUBLISHED_UNVERIFIED" for t in TRANS) or \
            any(f == "PUBLISHED_UNVERIFIED" for f, _, _, _ in WEDGES):
        probs.append("PUBLISHED_UNVERIFIED has an outgoing transition")
    for t in TRANS:
        if t["machine"] == "sequence" and t["to"] == "PUBLISHED_UNVERIFIED" and "obj-33" not in t["mints"]:
            probs.append(f"{t['id']}: reaches PUBLISHED_UNVERIFIED without a ForwardRepairObligation")
        if t["machine"] == "forward_repair_obligation" and t["to"] == "WAIVED":
            bad = {"obj-30", "obj-31", "obj-35"} & set(t.get("mints", []))
            if bad:
                probs.append(f"{t['id']}: a waiver mints {sorted(bad)}")
    return probs, ""


@check("graph.stale_fenced_terminal")
def _stale_fenced():
    probs = []
    for s in ("STALE", "FENCED"):
        if s not in TERMINAL["attempt"]:
            probs.append(f"attempt {s} must be terminal")
        if any(t["machine"] == "attempt" and t.get("from") == s for t in TRANS):
            probs.append(f"attempt {s} has an outgoing transition")
    return probs, ""


@check("graph.revocation_covers_pre_ponr")
def _revocation():
    probs = []
    revs = [w for w in WILD if w["machine"] == "sequence" and w["to"] == "DELEGATION_REVOKED"]
    covered = set()
    for w in revs:
        covered |= expand(w["paths"])
        for needed in ("reservation_released", "no_pending_or_unknown_intent", "fence_bumped"):
            if needed not in w["preconditions"]:
                probs.append(f"{w['id']}: revocation without {needed}")
    for pid in PATHS:
        if pid not in covered:
            probs.append(f"{pid}: no revocation covers its pre-PONR states")
    return probs, ""


@check("liveness.exits_and_reachability")
def _liveness():
    probs = []
    for m, d in MACHINES.items():
        st = states(m)
        out = defaultdict(list)
        adj = defaultdict(set)
        for t in TRANS:
            if t["machine"] == m:
                out[t["from"]].append(t)
                adj[t["from"]].add(t["to"])
        for f, to, w, _ in WEDGES:
            if w["machine"] == m:
                out[f].append(w)
                adj[f].add(to)
        for s, sd in st.items():
            if sd["terminal"]:
                if out.get(s):
                    probs.append(f"{m}.{s}: terminal state with outgoing transitions")
                continue
            if not out.get(s):
                probs.append(f"{m}.{s}: no exit")
            if sd["human_queue"] and not any(x.get("trigger") == "you" or x.get("actor") == "you" for x in out.get(s, [])):
                probs.append(f"{m}.{s}: waits for you, but no exit is yours")
        seen = reachable(adj, d["initial"])
        for s in st:
            if s not in seen:
                probs.append(f"{m}.{s}: unreachable from {d['initial']}")
    return probs, ""


# ---------------------------------------------------------------- identity, bindings, authority


@check("identity.tiers")
def _identity():
    probs = []
    tiers = IDENT["tiers"]
    where = defaultdict(list)
    for t in tiers:
        for f in t["fields"]:
            where[f].append(t["id"])
    for f, ts in where.items():
        if len(ts) > 1:
            probs.append(f"field {f} appears in {ts}")
    for i, t in enumerate(tiers):
        expected = None if i == 0 else tiers[i - 1]["id"]
        if t.get("embeds") != expected:
            probs.append(f"{t['id']} must embed {expected}")
    for f, tier in IDENT["required_placements"].items():
        if where.get(f) != [tier]:
            probs.append(f"{f} must be in {tier}, found {where.get(f)}")
    for f in IDENT["excluded_from_every_tier"]:
        if f in where:
            probs.append(f"{f} must not be in any tier")
    return probs, " / ".join(f"{t['id']} {len(t['fields'])} fields" for t in tiers)


@check("registrations.bindings")
def _registrations():
    probs = []
    contract_fields = set(next(t for t in IDENT["tiers"] if t["id"] == "contract_digest")["fields"])
    for r in REGS["registrations"]:
        o = OBJ.get(r["object"])
        if not o:
            probs.append(f"{r['id']}: unknown object {r['object']}")
            continue
        if not o["mutability"].startswith("immutable"):
            probs.append(f"{r['id']}: bound object {o['id']} is not immutable")
        if not r.get("resolved_at_freeze"):
            probs.append(f"{r['id']}: not resolved at freeze")
        if r["bound_as"] not in contract_fields:
            probs.append(f"{r['id']}: {r['bound_as']} is not a ContractDigest field")
    missing = {"artifact_root", "entrypoint", "execution_requirements"} - set(REGS["verifier_registration_fields"])
    if missing:
        probs.append(f"verifier registrations lack {sorted(missing)} (CORR-48 as refined)")
    if "HALT" not in REGS["artifact_closure"]["missing"]:
        probs.append("a missing bound artifact must HALT")
    return probs, f"{len(REGS['registrations'])} bindings"


@check("authority.acyclic_and_forbidden")
def _authority():
    probs = []
    for e in EDGES["authority_edges"] + EDGES["forbidden_edges"] + EDGES.get("evidence_edges", []):
        for k in ("from", "to"):
            if e[k] not in P:
                probs.append(f"unknown principal {e[k]} in an edge")
    for topo in ("interim", "target"):
        edges = {(e["from"], e["to"]) for e in EDGES["authority_edges"] if e["topology"] in (topo, "both")}
        adj = defaultdict(list)
        for a, b in edges:
            adj[a].append(b)
        color = {}

        def dfs(u, trail):
            color[u] = 1
            for v in adj[u]:
                if color.get(v) == 1:
                    probs.append(f"{topo}: authority cycle {' -> '.join(trail + [u, v])}")
                elif color.get(v) is None:
                    dfs(v, trail + [u])
            color[u] = 2

        for node in list(adj):
            if color.get(node) is None:
                dfs(node, [])
        for f in EDGES["forbidden_edges"]:
            if f["topology"] in (topo, "both") and (f["from"], f["to"]) in edges:
                probs.append(f"{topo}: forbidden edge present {f['from']} -> {f['to']} ({f['reason']})")
    return probs, f"{len(EDGES['authority_edges'])} authority edges, {len(EDGES['forbidden_edges'])} forbidden"


@check("policy.bounds")
def _policy():
    probs = []
    if not POLICY["effect_hard_timeout_seconds"] < POLICY["effect_intent_ttl_seconds"]:
        probs.append("effect_hard_timeout must be below effect_intent_ttl")
    if not POLICY.get("reservation_lease_renewable"):
        probs.append("reservation leases must be renewable")
    if "not semantic constants" not in POLICY["status"]:
        probs.append("defaults must be marked as configuration, not semantic constants (D-18)")
    info = (f"intent {POLICY['effect_intent_ttl_seconds']} s, hard timeout {POLICY['effect_hard_timeout_seconds']} s, "
            f"lease {POLICY['reservation_lease_seconds']} s")
    return probs, info


# ---------------------------------------------------------------- bookkeeping


@check("bookkeeping.corrections")
def _corrections():
    probs = dups([c["id"] for c in CORRS])
    allowed = set(CORR_DOC["statuses"])
    known = {c["id"] for c in CORRS}
    for c in CORRS:
        if c["status"] not in allowed:
            probs.append(f"{c['id']}: unknown status {c['status']}")
        if c["status"] == "REPLACED" and not c.get("replaced_by"):
            probs.append(f"{c['id']}: replaced without replaced_by")
        for ref in [c.get("replaced_by")] + c.get("refined_by", []):
            if ref and ref not in known:
                probs.append(f"{c['id']}: unknown reference {ref}")
        if c.get("origin") not in ("user", "assistant"):
            probs.append(f"{c['id']}: unknown origin {c.get('origin')}")
    nums = sorted(int(c["id"].split("-")[1]) for c in CORRS)
    missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
    if missing:
        probs.append(f"missing correction IDs: {missing}")
    counts = Counter(c["status"] for c in CORRS)
    return probs, f"{len(CORRS)} corrections: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


@check("bookkeeping.decisions")
def _decisions():
    probs = dups([d["id"] for d in DECS])
    allowed = set(DEC_DOC["statuses"])
    for d in DECS:
        if d["status"] not in allowed:
            probs.append(f"{d['id']}: unknown status {d['status']}")
        if d["status"] == "OPEN" and not d.get("blocks_chapter"):
            probs.append(f"{d['id']}: open without the chapter it blocks")
        if d["status"] != "OPEN" and not d.get("disposition"):
            probs.append(f"{d['id']}: decided without a disposition")
    counts = Counter(d["status"] for d in DECS)
    return probs, f"{len(DECS)} decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


@check("bookkeeping.unknowns")
def _unknowns():
    probs = dups([u["id"] for u in UNKS])
    allowed = set(UNK_DOC["statuses"])
    for u in UNKS:
        if u["status"] not in allowed:
            probs.append(f"{u['id']}: unknown status {u['status']}")
        if u["status"] in ("OPEN", "NARROWED") and not (u.get("next_probe") and u.get("blocks")):
            probs.append(f"{u['id']}: unresolved without a next probe and what it blocks")
        if u["status"] == "RESOLVED" and not u.get("resolution"):
            probs.append(f"{u['id']}: resolved without a resolution")
    counts = Counter(u["status"] for u in UNKS)
    return probs, f"{len(UNKS)} unknowns: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


@check("invariants.testability")
def _invariants():
    probs = dups([i["id"] for i in INVS])
    names = {n for n, _ in CHECKS}
    known = {i["id"] for i in INVS}
    for i in INVS:
        st = i["status"]
        if st not in ("active", "revised", "replaced"):
            probs.append(f"{i['id']}: unknown status {st}")
        if st == "replaced":
            if i.get("replaced_by") not in known:
                probs.append(f"{i['id']}: replaced by an unknown invariant")
            continue
        if not i.get("tests"):
            probs.append(f"{i['id']}: no planned test")
        for t in i.get("tests", []):
            if t["kind"] == "registry" and t["id"] not in names:
                probs.append(f"{i['id']}: unknown registry check {t['id']}")
            elif t["kind"] == "runtime" and not isinstance(t.get("chapter"), int):
                probs.append(f"{i['id']}: runtime test {t['id']} has no chapter")
            elif t["kind"] not in ("registry", "runtime"):
                probs.append(f"{i['id']}: unknown test kind {t['kind']}")
    counts = Counter(i["status"] for i in INVS)
    registry_proven = sum(1 for i in INVS if any(t["kind"] == "registry" for t in i.get("tests", [])))
    info = (f"{len(INVS)} invariants: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            + f"; {registry_proven} with a registry proof now")
    return probs, info


@check("invariants.enforcement_points")
def _enforcement():
    probs = []
    count = 0
    for i in INVS:
        for ep in i.get("enforcement_points", []):
            count += 1
            t = transition(ep["transition"])
            if not t:
                probs.append(f"{i['id']}: unknown transition {ep['transition']}")
            elif ep["precondition"] not in t["preconditions"]:
                probs.append(f"{i['id']}: {ep['transition']} lacks precondition {ep['precondition']}")
    return probs, f"{count} enforcement points"


@check("objects.storage")
def _storage():
    """D-04: receipts live in the ledger and the content store; HDB is an index; Git is source proof."""
    probs = []
    policy = OBJ_DOC.get("storage_policy", {})
    by_class = OBJ_DOC.get("storage_by_class", {})
    if policy.get("authoritative") != ["ledger", "content_store"]:
        probs.append("the authoritative stores must be exactly the ledger and the content store (D-04)")
    if "hdb" not in policy.get("index_only", []) or "git" not in policy.get("source_proof_only", []):
        probs.append("HDB must be index-only and Git source-proof-only (D-04)")
    classes = set(OBJ_DOC["classes"])
    if set(by_class) != classes:
        probs.append(f"storage_by_class must cover exactly the object classes; differs on {sorted(set(by_class) ^ classes)}")
    for cls, where in by_class.items():
        low = where.lower()
        if "hdb" in low or "git" in low:
            probs.append(f"{cls}: stored in {where}; HDB and Git never hold Autobot's authoritative objects")
        if cls == "receipt" and not ("ledger" in low and "content_store" in low):
            probs.append("receipts must live in the ledger and the content store")
        if cls == "witness" and "content_store" not in low:
            probs.append("an admitted witness copy must live in the content store")
    for o in OBJS:
        if o["minter"] in ("hdb", "git"):
            probs.append(f"{o['id']}: minted by {o['minter']}, which never mints Autobot objects")
    return probs, f"{len(by_class)} classes placed"


# ---------------------------------------------------------------- documents


def docs_present():
    return (DOCS / "CHAPTER0_ARCHITECTURE.md").exists()


def registry_counts():
    by_kind = Counter()
    for c in CAPS:
        kind = P.get(c["owner"], {}).get("kind", "?")
        by_kind["human" if kind == "human" else "autobot" if kind == "autobot_seat" else "external"] += 1
    return {
        "principals": len(P),
        "capabilities": len(CAPS),
        "capabilities owned by you": by_kind["human"],
        "capabilities owned by Autobot seats": by_kind["autobot"],
        "capabilities owned by external owners": by_kind["external"],
        "object types": len(OBJS),
        "state machines": len(MACHINES),
        "transitions": len(TRANS),
        "wildcards": len(WILD),
        "expanded wildcard edges": len(WEDGES),
        "finalization paths": len(PATHS),
        "effect types": len(EFF),
        "invariants": len(INVS),
        "invariants with a registry proof": sum(1 for i in INVS if any(t["kind"] == "registry" for t in i.get("tests", []))),
        "enforcement points": sum(len(i.get("enforcement_points", [])) for i in INVS),
        "corrections": len(CORRS),
        "decisions": len(DECS),
        "unknowns": len(UNKS),
        "checks": len(CHECKS),
    }


def known_ids():
    ids = set(OBJ) | set(CAP) | {t["id"] for t in ALL_T} | set(EFF) | set(PATHS)
    ids |= {i["id"] for i in INVS} | {c["id"] for c in CORRS} | {d["id"] for d in DECS} | {u["id"] for u in UNKS}
    return ids


ID_PATTERN = re.compile(
    r"\b(obj-\d{2}|cap-\d{2}|t-[a-z]\d{2}[a-z]?|w-[a-z]\d{2}|eff-[a-z]+(?:-[a-z]+)*|INV-\d{2}[ab]?|CORR-\d{2}|D-\d{2}b?|U-\d{2})\b"
)
# Built from pieces so this file never matches its own patterns.
MACHINE_PATH_PATTERNS = [
    re.compile("[A-Za-z]" + ":" + r"[\\/]" + "[A-Za-z]"),
    re.compile(r"[\\/]" + "Us" + "ers" + r"[\\/]"),
    re.compile(r"[\\/]" + "ho" + "me" + r"[\\/][a-z]"),
    re.compile("App" + "Data"),
]


def cell(value):
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value) if value else "none"
    return str(value if value not in (None, "") else "n/a").replace("|", "\\|").replace("\n", " ")


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(out)


def render_registers():
    parts = [
        "# Chapter 0 registers",
        "",
        "Generated from `registries/*.json` by `python check_registries.py --render`. Do not edit by hand:"
        " `check_registries.py` fails when this file differs from the registries.",
        "",
        "## Principals",
        "",
        table(["ID", "Name", "Kind", "LLM"], [[p["id"], p["name"], p["kind"], "yes" if p["llm"] else "no"] for p in P.values()]),
        "",
        "## Capabilities",
        "",
        table(["ID", "Capability", "Owner", "Others", "Forbidden to", "Enforced (interim / target)"],
              [[c["id"], c["name"], c["owner"], [f"{o['principal']} {o['level']}" for o in c.get("others", [])],
                c.get("forbidden", []), f"{c['enforcement']['interim']} / {c['enforcement']['target']}"] for c in CAPS]),
        "",
        "## Objects",
        "",
        table(["ID", "Object", "Minter", "Class", "Mutability", "Attempt-bound"],
              [[o["id"], o["name"], o["minter"], o["class"], o["mutability"], "yes" if o.get("attempt_bound") else "no"] for o in OBJS]),
        "",
        "## Storage (D-04)",
        "",
        table(["Store", "Holds"], [["ledger", OBJ_DOC["storage_policy"]["ledger"]],
                                   ["content_store", OBJ_DOC["storage_policy"]["content_store"]],
                                   ["hdb", "index only: " + OBJ_DOC["storage_policy"]["note"]],
                                   ["git", "source proof and path-free references only"]]),
        "",
        table(["Object class", "Stored in"], [[k, v] for k, v in OBJ_DOC["storage_by_class"].items()]),
        "",
        "## State machines",
        "",
    ]
    for m, d in MACHINES.items():
        rows = [[s["id"], "yes" if s["terminal"] else "no", "yes" if s["human_queue"] else "no"] for s in d["states"]]
        parts += [f"### {m} (initial {d['initial']})", "", table(["State", "Terminal", "Waits for you"], rows), ""]
    parts += [
        "## Finalization paths",
        "",
        table(["Path", "Name", "States", "Side states before the PONR", "PONR"],
              [[p["id"], p["name"], " → ".join(p["ordered_states"]), p["side_pre_ponr"], p["ponr_transition"]] for p in PATHS.values()]),
        "",
        "## Transitions",
        "",
        table(["ID", "Machine", "From → to", "Actor (trigger)", "Capabilities", "Requires", "Mints", "Effect / intent", "Preconditions", "Paths", "PONR"],
              [[t["id"], t["machine"], f"{t['from']} → {t['to']}", t["actor"] + (f" ({t['trigger']})" if t.get("trigger") else ""),
                t.get("capabilities", []), t.get("requires", []), t.get("mints", []),
                t.get("effect") or (f"intent for {t['intent_for']}" if t.get("intent_for") else ""),
                t.get("preconditions", []), t.get("paths", []), "yes" if t.get("ponr") else ""] for t in TRANS]),
        "",
        "## Wildcards",
        "",
        table(["ID", "Machine", "From", "To", "Actor (trigger)", "Requires", "Preconditions", "Paths"],
              [[w["id"], w["machine"], "any state before the PONR" if w.get("from_any_pre_ponr") else "any non-terminal state", w["to"],
                w["actor"] + (f" ({w['trigger']})" if w.get("trigger") else ""), w.get("requires", []), w.get("preconditions", []),
                w.get("paths", [])] for w in WILD]),
        "",
        "## Effects",
        "",
        table(["ID", "Type", "Target", "Executor", "Capability", "Canonical on", "Receipt", "Outcome rule"],
              [[e["id"], e["type"], e["target"], e["executor"], e["capability"], e["canonical_on"], e["receipt"], e["outcome_rule"]] for e in EFF.values()]),
        "",
        "## Outcome rules",
        "",
        table(["Rule", "Observation", "Outcome"],
              [[name, key, val] for name, rule in RULES.items() for key, val in rule.items() if key != "note"]),
        "",
        "## Policy defaults",
        "",
        table(["Setting", "Value"], [[k, v] for k, v in POLICY.items()]),
        "",
        "## Identity tiers",
        "",
        table(["Tier", "Question", "Embeds", "Fields"], [[t["id"], t["question"], t.get("embeds"), t["fields"]] for t in IDENT["tiers"]]),
        "",
        "## Bindings resolved at freeze",
        "",
        table(["ID", "Object", "Bound as", "Carries"], [[r["id"], r["object"], r["bound_as"], r["carries"]] for r in REGS["registrations"]]),
        "",
        "## Authority edges",
        "",
        table(["From", "To", "Authority", "Topology"], [[e["from"], e["to"], e["label"], e["topology"]] for e in EDGES["authority_edges"]]),
        "",
        "## Forbidden edges",
        "",
        table(["From", "To", "Reason", "Topology"], [[e["from"], e["to"], e["reason"], e["topology"]] for e in EDGES["forbidden_edges"]]),
        "",
        "## Invariants",
        "",
        table(["ID", "Invariant", "Status", "Tests", "Enforcement points"],
              [[i["id"], i["statement"], i["status"] + (f" by {i['replaced_by']}" if i.get("replaced_by") else ""),
                [t["id"] + (f" (chapter {t['chapter']})" if t["kind"] == "runtime" else " (registry)") for t in i.get("tests", [])],
                [f"{e['transition']}: {e['precondition']}" for e in i.get("enforcement_points", [])]] for i in INVS]),
        "",
        "## Corrections",
        "",
        table(["ID", "Correction", "Origin", "Status", "Replaced or refined by", "Refinement"],
              [[c["id"], c["title"], c["origin"], c["status"], [c.get("replaced_by")] if c.get("replaced_by") else c.get("refined_by", []),
                c.get("refinement")] for c in CORRS]),
        "",
        "## Decisions",
        "",
        table(["ID", "Question", "Status", "Disposition"], [[d["id"], d["question"], d["status"], d["disposition"]] for d in DECS]),
        "",
        "## Unknowns",
        "",
        table(["ID", "Unknown", "Status", "Next probe", "Blocks"],
              [[u["id"], u["question"], u["status"], u.get("next_probe") or u.get("resolution"), u.get("blocks")] for u in UNKS]),
        "",
    ]
    return "\n".join(parts)


@check("docs.ids_exist")
def _doc_ids():
    if not docs_present():
        return None, "skipped: no Chapter 0 documents beside these registries"
    probs, seen = [], set()
    ids = known_ids()
    for name in DOC_FILES:
        path = DOCS / name
        if not path.exists():
            probs.append(f"{name} is missing")
            continue
        for ref in ID_PATTERN.findall(path.read_text(encoding="utf-8")):
            seen.add(ref)
            if ref not in ids:
                probs.append(f"{name} cites {ref}, which no registry defines")
    return sorted(set(probs)), f"{len(seen)} distinct IDs cited"


@check("docs.counts_match")
def _doc_counts():
    if not docs_present():
        return None, "skipped: no Chapter 0 documents beside these registries"
    text = (DOCS / "CHAPTER0_CLOSURE.md").read_text(encoding="utf-8")
    marker = "<!-- registry-counts -->"
    if marker not in text:
        return [f"CHAPTER0_CLOSURE.md has no {marker} table"], ""
    quoted = {}
    for line in text.split(marker, 1)[1].splitlines()[1:]:
        if not line.strip():
            if quoted:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[1].isdigit():
            quoted[cells[0]] = int(cells[1])
    actual = registry_counts()
    probs = [f"{k}: the document says {quoted.get(k)}, the registries give {v}" for k, v in actual.items() if quoted.get(k) != v]
    probs += [f"the document quotes an unknown count '{k}'" for k in quoted if k not in actual]
    return probs, f"{len(quoted)} counts compared"


@check("docs.registers_current")
def _doc_registers():
    if not docs_present():
        return None, "skipped: no Chapter 0 documents beside these registries"
    path = DOCS / REGISTER_FILE
    if not path.exists():
        return [f"{REGISTER_FILE} is missing; run check_registries.py --render"], ""
    current = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    if current != render_registers().strip():
        return [f"{REGISTER_FILE} is out of date; run check_registries.py --render"], ""
    return [], f"{REGISTER_FILE} matches the registries"


@check("docs.no_machine_paths")
def _doc_paths():
    if not docs_present():
        return None, "skipped: no Chapter 0 documents beside these registries"
    probs, scanned = [], 0
    files = [p for p in DOCS.rglob("*") if p.is_file() and p.suffix in (".md", ".json", ".py", ".sha256")]
    if HANDOFFS.exists():
        files += [p for p in HANDOFFS.iterdir() if p.is_file()]
    for path in files:
        scanned += 1
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if any(pat.search(line) for pat in MACHINE_PATH_PATTERNS):
                probs.append(f"{path.name}:{n}: looks like a machine path")
    return probs, f"{scanned} files scanned"


def main():
    if RENDER:
        (DOCS / REGISTER_FILE).write_text(render_registers() + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {REGISTER_FILE}")
        return 0
    failed = skipped = 0
    for name, fn in CHECKS:
        try:
            probs, info = fn()
        except Exception as exc:  # a malformed registry is a failed check, not a crash
            probs, info = [f"check crashed: {type(exc).__name__}: {exc}"], ""
        if probs is None:
            skipped += 1
            print(f"SKIP  {name}  ({info})")
            continue
        status = "PASS" if not probs else "FAIL"
        failed += bool(probs)
        print(f"{status}  {name}" + (f"  ({info})" if info else ""))
        for p in probs[:25]:
            print(f"        - {p}")
        if len(probs) > 25:
            print(f"        ... and {len(probs) - 25} more")
    print()
    ran = len(CHECKS) - skipped
    print(f"{ran - failed} of {ran} checks passed" + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
