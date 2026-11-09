"""check_chapter1.py — the Chapter 1 acceptance gate's registry checks.

Validates the ch1 workspace against itself (internal consistency) and against
the authoritative repository (Chapter 0 + amendment registries, the frozen
Phase A registries, and the handoff chain, D-60). Pure reads; no paths are
stored in any registry — the repository root is auto-detected when this tree
sits inside it, or passed with --repo.

Usage: python check_chapter1.py [--repo <swarm-coordinator root>] [--ch1 <ch1 root>]
Exit 0 iff every check passes. run_all.py runs this after the test suites.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CH1 = HERE.parent
sys.path.insert(0, str(DEFAULT_CH1 / "src"))

CHECKS = []


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn
    return wrap


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Ctx:
    def __init__(self, ch1, repo):
        self.ch1 = Path(ch1)
        self.repo = Path(repo)
        self.decisions = _load(self.ch1 / "registries" / "decisions.json")
        self.enums = _load(self.ch1 / "registries" / "enums.json")
        self.identifiers = _load(self.ch1 / "registries" / "identifiers.json")
        self.semantic = _load(self.ch1 / "registries" / "semantic_rules.json")
        self.resolution = _load(self.ch1 / "registries" / "vectors_resolution.json")
        self.traceability = _load(self.ch1 / "registries" / "traceability.json")
        self.representation = _load(self.ch1 / "registries" / "representation.json")
        ch0 = self.repo / "docs" / "architecture" / "chapter0" / "registries"
        self.ch0_invariants = _load(ch0 / "invariants.json")
        self.ch0_transitions = _load(ch0 / "transitions.json")
        self.ch0_states = _load(ch0 / "states.json")
        self.ch0_objects = _load(ch0 / "objects.json")
        self.ch0_decisions = _load(ch0 / "decisions.json")
        self.ch0_corrections = _load(ch0 / "corrections.json")
        self.ch0_capabilities = _load(ch0 / "capabilities.json")
        self.ch0_principals = _load(ch0 / "principals.json")
        self.ch0_unknowns = _load(ch0 / "unknowns.json")
        self.ch0_registrations = _load(ch0 / "registrations.json")
        pa = self.repo / "docs" / "architecture" / "chapter1" / "phase-a" / "registries"
        self.pa_candidates = _load(pa / "decision_candidates.json")
        self.pa_vectors = _load(pa / "vectors.json")
        self.pa_unknowns = _load(pa / "unknowns.json")
        self.pa_inventory = _load(pa / "inventory.json")
        self.pa_hea_ids = set(re.findall(r'"(T-\d+)"', (pa / "hea_techniques.json").read_text(encoding="utf-8")))
        self.golden = None
        golden_path = self.ch1 / "vectors" / "golden" / "golden_digests.json"
        if golden_path.exists():
            self.golden = _load(golden_path)


# ----------------------------------------------------------------- decisions

@check("decisions.count_and_ids")
def _d_ids(ctx):
    problems = []
    rows = ctx.decisions["decisions"]
    expected = ["D-%d" % n for n in range(19, 62)]
    actual = [d["id"] for d in rows]
    if actual != expected:
        problems.append("decision ids are not exactly D-19..D-61 in order")
    if len(rows) != 43:
        problems.append("expected 43 decisions, found %d" % len(rows))
    return problems


@check("decisions.candidate_order")
def _d_order(ctx):
    problems = []
    candidates = [c["key"] for c in ctx.pa_candidates["candidates"]]
    mapped = [d["candidate"] for d in ctx.decisions["decisions"]]
    if mapped != candidates:
        problems.append("decisions are not in the frozen candidate registry's order")
    return problems


@check("decisions.resolves_match")
def _d_resolves(ctx):
    problems = []
    by_key = {c["key"]: c for c in ctx.pa_candidates["candidates"]}
    for d in ctx.decisions["decisions"]:
        cand = by_key.get(d["candidate"])
        if cand is None:
            problems.append("%s names unknown candidate %s" % (d["id"], d["candidate"]))
            continue
        if sorted(d.get("resolves", [])) != sorted(cand.get("resolves", [])):
            problems.append("%s resolves %r but the candidate says %r"
                            % (d["id"], d.get("resolves"), cand.get("resolves")))
    return problems


@check("decisions.statuses")
def _d_status(ctx):
    problems = []
    for d in ctx.decisions["decisions"]:
        if d["candidate"] in ("dc.standalone-intents", "dc.retry-intents"):
            if d["status"] != "ACCEPTED_CH0_CORRECTION":
                problems.append("%s must be ACCEPTED_CH0_CORRECTION" % d["id"])
        elif d["status"] != "DECIDED":
            problems.append("%s must be DECIDED (ratified 2026-11-08)" % d["id"])
        if d.get("decided_by") != "you":
            problems.append("%s decided_by must be you" % d["id"])
    return problems


@check("decisions.basis_ids_exist")
def _d_basis(ctx):
    problems = []
    known = set()
    known |= {i["id"] for i in ctx.ch0_invariants["invariants"]}
    known |= {d["id"] for d in ctx.ch0_decisions["decisions"]}
    known |= {c["id"] for c in ctx.ch0_corrections["corrections"]}
    known |= {c["id"] for c in ctx.ch0_capabilities.get("capabilities", [])}
    known |= {o["id"] for o in ctx.ch0_objects["objects"]}
    known |= {t["id"] for t in ctx.ch0_transitions["transitions"]}
    known |= {w["id"] for w in ctx.ch0_transitions.get("wildcards", [])}
    known |= {r["id"] for r in ctx.ch0_registrations["registrations"]}
    known |= {u["id"] for u in ctx.pa_unknowns.get("inherited", [])}
    known |= {u["id"] for u in ctx.pa_unknowns.get("new", [])}
    known |= set(ctx.pa_unknowns.get("carried_unchanged", []))
    known |= {k["id"] for k in ctx.pa_vectors.get("couplings", [])}
    known |= ctx.pa_hea_ids
    known |= {d["id"] for d in ctx.decisions["decisions"]}
    id_re = re.compile(r"^(INV-\w+|D-\d+\w*|CORR-\d+|U-\d+|cap-\d+|obj-\d+|t-\w+|w-\w+|reg-[a-z-]+|K-\d+|T-\d+)$")
    for d in ctx.decisions["decisions"]:
        for token in d.get("basis", []) + d.get("hea", []):
            head = token.split(" ")[0]
            if id_re.match(head) and head not in known:
                problems.append("%s cites unknown id %r" % (d["id"], head))
    return problems


# --------------------------------------------------------------------- enums

@check("enums.ch0_consistency")
def _e_ch0(ctx):
    problems = []
    enums = {e["name"]: e for e in ctx.enums["enums"]}
    effects = [e["id"] for e in ctx.ch0_transitions["effects"]]
    if sorted(enums["effect_id"]["values"]) != sorted(effects):
        problems.append("effect_id enum != transitions.json effects")
    classes = sorted(ctx.ch0_transitions["intent_classes"]["classes"].keys())
    if sorted(enums["intent_class"]["values"]) != classes:
        problems.append("intent_class enum != transitions.json intent classes")
    states = [s["id"] for s in ctx.ch0_states["machines"]["effect_intent"]["states"]]
    if enums["effect_intent_state"]["values"] != states:
        problems.append("effect_intent_state enum != states.json")
    return problems


@check("enums.schema_consistency")
def _e_schemas(ctx):
    problems = []
    registered = {}
    for e in ctx.enums["enums"]:
        if e.get("closed"):
            registered[frozenset(e["values"])] = e["name"]
    def walk(node):
        if isinstance(node, dict):
            if "enum" in node and isinstance(node["enum"], list):
                values = frozenset(node["enum"])
                if values not in registered:
                    problems.append("schema enum %r is not exactly a registered enum" % sorted(values))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    for path in sorted((ctx.ch1 / "schemas").glob("*.schema.json")):
        walk(_load(path))
    return problems


@check("identity.tiers_match")
def _tiers_match(ctx):
    """RV-54: the library's tier field tuples and the frozen V-A preimages
    must agree with Chapter 0's identity.json, not merely with each other."""
    problems = []
    import autobot_contract.digests as digests
    identity = _load(ctx.repo / "docs" / "architecture" / "chapter0" / "registries" / "identity.json")
    tiers = {t["id"]: t["fields"] for t in identity["tiers"]}
    expected = {
        tier: sorted({path.split(".")[0] for path in fields})
        for tier, fields in tiers.items()
    }
    actual = {
        "requirement_digest": sorted(digests.REQUIREMENT_FIELDS),
        "contract_digest": sorted(digests.CONTRACT_FIELDS),
        "record_hash": sorted(digests.RECORD_FIELDS),
    }
    for tier, heads in expected.items():
        if actual.get(tier) != heads:
            problems.append("library %s fields %r != identity.json heads %r"
                            % (tier, actual.get(tier), heads))
    if ctx.golden is not None and "V-A" in ctx.golden:
        va = ctx.golden["V-A"]
        try:
            req_keys = set(json.loads(va["canonical_requirement_preimage"]).keys())
            con_keys = set(json.loads(va["canonical_contract_preimage"]).keys())
            rec_keys = set(json.loads(va["canonical_record_preimage"]).keys())
        except Exception as exc:
            return problems + ["V-A preimages unparsable: %r" % exc]
        if req_keys != set(expected["requirement_digest"]):
            problems.append("V-A requirement preimage keys %r != identity heads" % sorted(req_keys))
        if con_keys != set(expected["contract_digest"]) | {"requirement_digest"}:
            problems.append("V-A contract preimage keys %r != identity heads + requirement_digest" % sorted(con_keys))
        expected_rec = (set(expected["record_hash"]) - {"supersession_provenance"}) | {"contract_digest"}
        if rec_keys != expected_rec:
            problems.append("V-A record preimage keys %r != present identity heads + contract_digest" % sorted(rec_keys))
    return problems


@check("identifiers.grammar_sync")
def _i_sync(ctx):
    problems = []
    from autobot_contract import grammars
    prefixes = sorted(p for p in grammars.MINTED_PREFIXES)
    registered = sorted(i["prefix"] for i in ctx.identifiers["identifiers"] if "prefix" in i)
    if prefixes != registered:
        problems.append("minted prefixes %r != registered %r" % (prefixes, registered))
    if ctx.identifiers["name_grammar"] != grammars.NAME_ID_RE.pattern:
        problems.append("name grammar differs between registry and library")
    return problems


# ------------------------------------------------------------------- schemas

@check("schemas.profile_and_coverage")
def _s_cov(ctx):
    problems = []
    from autobot_contract import load_schemas
    from autobot_contract.validate import KIND_TO_SCHEMA
    try:
        schemas = load_schemas(ctx.ch1 / "schemas")
    except Exception as exc:
        return ["schema profile violation: %s" % exc]
    for kind, filename in KIND_TO_SCHEMA.items():
        if filename not in schemas:
            problems.append("missing schema file %s for %s" % (filename, kind))
    required = [o for o in ctx.representation["objects"] if o["ch1_scope"] == "required"]
    if len(required) != 10:
        problems.append("expected 10 required objects, found %d" % len(required))
    for obj in required:
        schema_file = obj.get("schema_file", "")
        base = schema_file.split(" ")[0]
        if base and not (ctx.ch1 / "schemas" / base).exists():
            problems.append("%s names missing schema %s" % (obj["id"], base))
    return problems


@check("semantic_rules.tests_exist")
def _sem_tests(ctx):
    problems = []
    rules = ctx.semantic["rules"]
    ids = [r["id"] for r in rules]
    if ids != ["SEM-%02d" % n for n in range(1, 19)]:
        problems.append("semantic rule ids are not SEM-01..SEM-18")
    harness_text = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(HERE.glob("test_*.py")))
    for rule in rules:
        test_name = rule["parity_test"].split(".")[-1]
        if test_name.startswith("test_") and ("def %s" % test_name) not in harness_text:
            problems.append("%s parity test %s not found in the harness" % (rule["id"], test_name))
    return problems


# ------------------------------------------------------------------- vectors

@check("vectors.ids_match_phase_a")
def _v_ids(ctx):
    problems = []
    pa_ids = [v["id"] for v in ctx.pa_vectors["vectors"]]
    res_ids = [v["id"] for v in ctx.resolution["vectors"]]
    if res_ids != pa_ids:
        problems.append("vector resolution ids differ from frozen Phase A ids")
    pa_pred = [p["id"] for p in ctx.pa_vectors["predicate_tests"]]
    res_pred = [p["id"] for p in ctx.resolution["predicates"]]
    if res_pred != pa_pred:
        problems.append("predicate ids differ from frozen Phase A ids")
    return problems


@check("vectors.goldens_consistent")
def _v_golden(ctx):
    if ctx.golden is None:
        return ["vectors/golden/golden_digests.json missing (run gen_goldens)"]
    problems = []
    for v in ctx.resolution["vectors"]:
        vid = v["id"]
        entry = ctx.golden.get(vid)
        if entry is None:
            problems.append("golden missing %s" % vid)
            continue
        if entry["disposition"] != v["disposition"]:
            problems.append("%s disposition mismatch registry=%s golden=%s"
                            % (vid, v["disposition"], entry["disposition"]))
        if v["disposition"] == "rejection" and entry.get("code") != v.get("code"):
            problems.append("%s rejection code mismatch" % vid)
        if v["disposition"] == "executable" and vid not in ("V-B", "V-G", "V-L"):
            for tier in ("requirement_digest", "contract_digest", "record_hash"):
                value = entry.get(tier, "")
                if not re.match(r"^sha256:[0-9a-f]{64}$", value):
                    problems.append("%s %s not a canonical digest" % (vid, tier))
    for pid in ("P-1", "P-2", "JH-1"):
        if pid not in ctx.golden:
            problems.append("golden missing %s" % pid)
    return problems


# -------------------------------------------------------------- traceability

@check("traceability.covers_76")
def _t_cov(ctx):
    problems = []
    inv_ids = [i["id"] for i in ctx.ch0_invariants["invariants"]]
    rows = {r["id"]: r for r in ctx.traceability["rows"]}
    if list(rows.keys()) != inv_ids:
        problems.append("traceability rows do not match the 76 invariants in order")
    chapter_tests = {}
    for inv in ctx.ch0_invariants["invariants"]:
        chapters = [t["chapter"] for t in inv.get("tests", []) if t.get("kind") == "runtime"]
        chapter_tests[inv["id"]] = min(chapters) if chapters else None
    for inv_id, row in rows.items():
        owner = row["owner"]
        expected = chapter_tests.get(inv_id)
        if inv_id == "INV-33":
            if owner != "replaced":
                problems.append("INV-33 owner must be 'replaced'")
            continue
        if expected is None:
            if owner != "registry":
                problems.append("%s has no runtime test; owner must be 'registry', got %r" % (inv_id, owner))
        elif owner != "ch%d" % expected:
            problems.append("%s owner %r but its first runtime test is chapter %d" % (inv_id, owner, expected))
    ch1_rows = [i for i, r in rows.items() if r["owner"] == "ch1"]
    if sorted(ch1_rows) != ["INV-05", "INV-21", "INV-28", "INV-40"]:
        problems.append("ch1-owned invariants must be exactly INV-05, INV-21, INV-28, INV-40")
    return problems


# ------------------------------------------------------------ representation

@check("representation.covers_42")
def _r_cov(ctx):
    problems = []
    ch0_ids = [o["id"] for o in ctx.ch0_objects["objects"]]
    rows = [o["id"] for o in ctx.representation["objects"]]
    if rows != ch0_ids:
        problems.append("representation rows do not match Chapter 0's 42 objects in order")
    pa = {o["id"]: o for o in ctx.pa_inventory["objects"]}
    for obj in ctx.representation["objects"]:
        frozen = pa.get(obj["id"])
        if frozen is None:
            problems.append("%s missing from frozen Phase A inventory" % obj["id"])
            continue
        if obj["ch1_scope"] != frozen["ch1_scope"]:
            problems.append("%s ch1_scope %r differs from frozen %r"
                            % (obj["id"], obj["ch1_scope"], frozen["ch1_scope"]))
        if obj["storage"] != frozen["storage"]:
            problems.append("%s storage differs from frozen inventory" % obj["id"])
        if obj["schema_form"] != frozen["schema_form"]:
            problems.append("%s schema_form differs from frozen inventory" % obj["id"])
    conditional = sorted(o["id"] for o in ctx.representation["objects"] if o.get("conditional_bindings"))
    if conditional != ["obj-16", "obj-21", "obj-25", "obj-26", "obj-30", "obj-37"]:
        problems.append("conditional-binding objects must be exactly obj-16/21/25/26/30/37, got %r" % conditional)
    return problems


# ------------------------------------------------------------------ handoffs

@check("handoff.chain")
def _h_chain(ctx):
    problems = []
    handoffs = ctx.repo / "docs" / "implementation" / "handoffs"

    def sha(name):
        return hashlib.sha256((handoffs / name).read_bytes()).hexdigest()

    latest = _load(handoffs / "latest.json")
    if sha(latest["latest"]) != latest["sha256"]:
        problems.append("latest.json sha mismatch for %s" % latest["latest"])
    amendment = _load(handoffs / "ch00-amendment-01.json")
    if sha(amendment["predecessor"]["handoff"]) != amendment["predecessor"]["sha256"]:
        problems.append("amendment predecessor hash mismatch")
    phase_a = _load(handoffs / "ch01-phase-a.json")
    if sha(phase_a["predecessor"]["handoff"]) != phase_a["predecessor"]["sha256"]:
        problems.append("phase-a predecessor hash mismatch")
    draft = ctx.ch1 / "handoff" / "ch01-phase-b.draft.json"
    if draft.exists():
        payload = _load(draft)
        if payload["predecessor"]["handoff"] != "ch00-amendment-01.json":
            problems.append("draft handoff predecessor must be ch00-amendment-01.json")
        if payload["predecessor"]["sha256"] != sha("ch00-amendment-01.json"):
            problems.append("draft handoff predecessor sha mismatch")
    real = handoffs / "ch01-phase-b.json"
    if real.exists():
        payload = _load(real)
        if payload["predecessor"]["handoff"] != "ch00-amendment-01.json" \
                or payload["predecessor"]["sha256"] != sha("ch00-amendment-01.json"):
            problems.append("phase-b handoff predecessor mismatch")
        if latest["latest"] != "ch01-phase-b.json" or latest["sha256"] != sha("ch01-phase-b.json"):
            problems.append("latest.json does not name the frozen phase-b handoff with its hash")
        sidecar = handoffs / "ch01-phase-b.json.sha256"
        if not sidecar.exists() or not sidecar.read_text(encoding="utf-8").startswith(sha("ch01-phase-b.json")):
            problems.append("phase-b sidecar missing or mismatched")
    return problems


@check("docs.present_and_counted")
def _docs(ctx):
    problems = []
    spec = ctx.ch1 / "CHAPTER1_SPECIFICATION.md"
    package = ctx.ch1 / "DECISION_PACKAGE.md"
    for path in (spec, package):
        if not path.exists():
            problems.append("missing %s" % path.name)
    if spec.exists():
        text = spec.read_text(encoding="utf-8")
        for needle in ("43 decisions", "76", "42", "D-19", "D-61"):
            if needle not in text:
                problems.append("specification does not state %r" % needle)
    if package.exists():
        text = package.read_text(encoding="utf-8")
        for n in range(19, 62):
            if "D-%d" % n not in text:
                problems.append("decision package missing D-%d" % n)
                break
    return problems


def run(ch1=DEFAULT_CH1, repo=None, quiet=False):
    if repo is None:
        for parent in [DEFAULT_CH1] + list(DEFAULT_CH1.parents):
            if (parent / "docs" / "architecture" / "chapter0" / "registries" / "invariants.json").exists():
                repo = parent
                break
    if repo is None:
        print("FAIL: repository root not found; pass --repo")
        return 2
    ctx = Ctx(ch1, repo)
    failures = 0
    for name, fn in CHECKS:
        try:
            problems = fn(ctx)
        except Exception as exc:  # a crashed check is a failed check
            problems = ["check crashed: %r" % exc]
        if problems:
            failures += 1
            print("FAIL %s" % name)
            for p in problems:
                print("     - %s" % p)
        elif not quiet:
            print("PASS %s" % name)
    print("check_chapter1: %d/%d checks pass" % (len(CHECKS) - failures, len(CHECKS)))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=None)
    parser.add_argument("--ch1", default=str(DEFAULT_CH1))
    args = parser.parse_args()
    sys.exit(run(Path(args.ch1), Path(args.repo) if args.repo else None))
