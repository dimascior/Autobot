"""Generate (or re-verify) the golden vectors: canonical bytes, digests,
dispositions and predicate outcomes for V-A..V-S, P-1 and P-2 (D-58 oracle 2).

Deterministic by construction: fixtures are constants, so every run must
produce byte-identical output. If vectors/golden/golden_digests.json already
exists, this run recomputes everything and compares — a mismatch is a failure,
never an overwrite. Run via run_all.py, or directly: python gen_goldens.py
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import fixtures  # noqa: E402
from jcs2 import canonicalize2  # noqa: E402
from autobot_contract import (  # noqa: E402
    admit, canonicalize, compute_tiers, content_address, contract_preimage,
    derive_eligibility, domain_object, domained_digest, freeze_disposition,
    journal_entry_hash, load_schemas, object_hash, record_preimage,
    recognize_version, requirement_preimage, validate_payload,
)

# Expected tier movement per executable field-change vector (Phase A vectors.json).
TIER_EFFECTS = {
    "V-C": ("same", "different", "different"),
    "V-D": ("different", "different", "different"),
    "V-E": ("same", "same", "different"),
    "V-F": ("same", "different", "different"),
    "V-H": ("different", "different", "different"),
    "V-M": ("different", "different", "different"),
    "V-N": ("same", "different", "different"),
    "V-O": ("same", "same", "different"),
    "V-P": ("same", "different", "different"),
    "V-R": ("same", "different", "different"),
}


class GoldenError(AssertionError):
    pass


def _expect(condition, message):
    if not condition:
        raise GoldenError(message)


def _reordered(value):
    """Deep copy with object keys in reverse insertion order (V-B)."""
    if isinstance(value, dict):
        return {k: _reordered(value[k]) for k in reversed(list(value.keys()))}
    if isinstance(value, list):
        return [_reordered(v) for v in value]
    return value


def _dual_canonical(value, where):
    ours = canonicalize(value)
    theirs = canonicalize2(value)
    _expect(ours == theirs, "dual-oracle disagreement at %s" % where)
    return ours


def build():
    schemas = load_schemas(ROOT / "schemas")
    g, hashes = fixtures.build_graph()
    base = fixtures.va_record(g, hashes)
    context = fixtures.binding_context(g, hashes)

    golden = {"schema": "autobot-ch1b-golden/v1"}

    # Every graph fixture must validate against its schema, positively.
    for name, payload in g.items():
        result = validate_payload(payload["kind"], payload, schemas)
        _expect(result.ok, "fixture %s invalid: %s" % (name, result.codes()))
        _dual_canonical(payload, name)
    env = fixtures.env_fixture()
    _expect(validate_payload(env["kind"], env, schemas).ok, "obj-18 fixture invalid")
    golden["object_hashes"] = dict(sorted(hashes.items()))
    golden["actual_verification_environment_hash"] = object_hash(env["kind"], env)
    golden["artifact_roots"] = {
        "verifier_checker_v1": g["verifier_checker_v1"]["artifact_root"],
        "transform_identity_v1": g["transform_identity_v1"]["artifact_root"],
    }
    golden["content_addresses"] = {
        "va_input_lf": content_address(fixtures.VA_INPUT_LF.encode("utf-8")),
        "va_input_crlf": content_address(fixtures.VA_INPUT_CRLF.encode("utf-8")),
        "checker_script_v1": content_address(fixtures.SCRIPT_CHECKER_V1),
    }
    _expect(golden["content_addresses"]["va_input_lf"] != golden["content_addresses"]["va_input_crlf"],
            "V-H precondition: LF and CRLF bytes must hash apart")

    # ---- V-A: the base record.
    va = validate_payload("TaskContractRecord", base, schemas)
    _expect(va.ok, "V-A invalid: %s" % va.codes())
    _expect(derive_eligibility(base) == "AUTOMATIC", "V-A eligibility must derive AUTOMATIC (D-52)")
    disposition = freeze_disposition(base, context, schemas)
    _expect(disposition["disposition"] == "FROZEN", "V-A must freeze: %r" % disposition)
    rd, cd, rh = compute_tiers(base)
    golden["V-A"] = {
        "disposition": "executable",
        "requirement_digest": rd,
        "contract_digest": cd,
        "record_hash": rh,
        "canonical_requirement_preimage": _dual_canonical(requirement_preimage(base), "V-A req").decode("utf-8"),
        "canonical_contract_preimage": _dual_canonical(contract_preimage(base), "V-A con").decode("utf-8"),
        "canonical_record_preimage": _dual_canonical(record_preimage(base), "V-A rec").decode("utf-8"),
        "canonical_record_payload_sha256": content_address(_dual_canonical(base, "V-A full")),
        "eligibility": "AUTOMATIC",
    }

    # ---- V-B: key order never matters.
    shuffled = _reordered(base)
    _expect(_dual_canonical(shuffled, "V-B") == canonicalize(base), "V-B: canonical bytes differ")
    _expect(compute_tiers(shuffled) == (rd, cd, rh), "V-B: digests differ")
    golden["V-B"] = {"disposition": "executable", "expected": "same/same/same", "holds": True}

    # ---- field-change vectors.
    payloads = fixtures.vector_payloads(g, hashes, base)
    for vid, (payload, ctx_over) in sorted(payloads.items()):
        result = validate_payload("TaskContractRecord", payload, schemas)
        _expect(result.ok, "%s invalid: %s" % (vid, result.codes()))
        ctx = fixtures.binding_context(g, hashes, **(ctx_over or {}))
        dispo = freeze_disposition(payload, ctx, schemas)
        _expect(dispo["disposition"] == "FROZEN", "%s must freeze: %r" % (vid, dispo))
        vrd, vcd, vrh = compute_tiers(payload)
        expected = TIER_EFFECTS[vid]
        actual = (
            "same" if vrd == rd else "different",
            "same" if vcd == cd else "different",
            "same" if vrh == rh else "different",
        )
        _expect(actual == expected, "%s tier effect %r != expected %r" % (vid, actual, expected))
        golden[vid] = {
            "disposition": "executable", "expected": "/".join(expected),
            "requirement_digest": vrd, "contract_digest": vcd, "record_hash": vrh,
        }

    # ---- V-G: resolution context is out-of-band (same payload, two paths).
    golden["V-G"] = {
        "disposition": "executable", "expected": "same/same/same",
        "note": "resolution_path is no field; the identical payload resolved from two machine paths recomputes identical digests",
        "requirement_digest": rd, "contract_digest": cd, "record_hash": rh,
    }

    # ---- V-I: non-canonical set order is rejected, never re-sorted.
    swapped = json.loads(json.dumps(base))
    arr = swapped["acceptance_assertions"]["ids_and_environment_constraints"]
    swapped["acceptance_assertions"]["ids_and_environment_constraints"] = [arr[1], arr[0]]
    vi = validate_payload("TaskContractRecord", swapped, schemas)
    _expect(not vi.ok and "ERR_SET_ORDER" in vi.codes(), "V-I must reject with ERR_SET_ORDER: %s" % vi.codes())
    golden["V-I"] = {"disposition": "rejection", "code": "ERR_SET_ORDER"}

    # ---- V-J: unknown field.
    unknown = json.loads(json.dumps(base))
    unknown["not_a_field"] = 1
    vj = validate_payload("TaskContractRecord", unknown, schemas)
    _expect(not vj.ok and "ERR_UNKNOWN_FIELD" in vj.codes(), "V-J must reject with ERR_UNKNOWN_FIELD: %s" % vj.codes())
    golden["V-J"] = {"disposition": "rejection", "code": "ERR_UNKNOWN_FIELD"}

    # ---- V-K: unsupported and malformed versions are distinct outcomes.
    v2 = json.loads(json.dumps(base))
    v2["schema_version"] = "v2"
    _expect(recognize_version(v2) == "UNSUPPORTED_VERSION", "V-K recognition")
    vk = validate_payload("TaskContractRecord", v2, schemas)
    _expect(vk.codes() == ["ERR_UNSUPPORTED_VERSION"], "V-K must reject as unsupported: %s" % vk.codes())
    bad = json.loads(json.dumps(base))
    bad["schema_version"] = "1.0"
    _expect(recognize_version(bad) == "MALFORMED_VERSION", "V-K malformed recognition")
    golden["V-K"] = {"disposition": "rejection", "code": "ERR_UNSUPPORTED_VERSION", "malformed_code": "ERR_MALFORMED_VERSION"}

    # ---- V-L: domain separation (same canonical bytes, two object types).
    neutral = {"schema_version": "v1", "kind": "neutral"}
    d1 = domained_digest(domain_object("DelegationContract"), neutral)
    d2 = domained_digest(domain_object("RepositoryAuthorityDeclaration"), neutral)
    _expect(d1 != d2, "V-L: domains failed to separate")
    golden["V-L"] = {"disposition": "executable", "payload_canonical": canonicalize(neutral).decode("utf-8"),
                     "as_delegation_contract": d1, "as_repository_authority_declaration": d2}

    # ---- V-Q: the impossibility proof (D-44 / SEM-09).
    vq = json.loads(json.dumps(base))
    vq["delegation_id"] = "dg_000000000000000000000000000000ff"
    dq = freeze_disposition(vq, context, schemas)
    _expect(dq["disposition"] == "REJECTED", "V-Q form 1 must be rejected")
    _expect(any(i["code"] == "SEM-09" for i in dq["issues"]), "V-Q form 1 must cite SEM-09")
    vq2 = json.loads(json.dumps(base))
    vq2["resolved_bindings"]["delegation_contract"] = hashes["delegation_b"]
    vq2["resolved_bindings"]["mandatory_floor"]["delegation"] = hashes["delegation_b"]
    ctx_b = fixtures.binding_context(g, hashes, delegation="delegation_b")
    dq2 = freeze_disposition(vq2, ctx_b, schemas)
    _expect(dq2["disposition"] == "FROZEN", "V-Q control: same delegation_id under a new record of the same delegation freezes (V-P)")
    golden["V-Q"] = {
        "disposition": "rejection", "code": "SEM-09",
        "impossibility": "delegation_id is a consistency-constrained copy of the bound record's value (D-44): any payload where they differ is rejected, and the property test varies the id over random values with the binding fixed — none admits. The pair cannot exist.",
    }

    # ---- V-S: K-1 violation rejected at freeze.
    vs = json.loads(json.dumps(base))
    vs["source_domain"]["authority_location"] = "LOCAL"
    vs.pop("remote_verification_mode")
    vs["finalization_requirements"] = ["LOCAL_CANONICAL_COMMIT"]
    ds = freeze_disposition(vs, context, schemas)
    _expect(ds["disposition"] == "REJECTED", "V-S must be rejected")
    _expect(any(i["code"] == "SEM-01" for i in ds["issues"]), "V-S must cite SEM-01")
    golden["V-S"] = {"disposition": "rejection", "code": "SEM-01"}

    # ---- P-1: the scope predicate.
    outside = json.loads(json.dumps(base))
    outside["allowed_scope"]["paths"] = ["src/forbidden"]
    p1 = {
        "inside_scope": freeze_disposition(base, context, schemas)["disposition"],
        "outside_scope": freeze_disposition(outside, context, schemas)["disposition"],
    }
    authored = json.loads(json.dumps(base))
    authored["authored_by"] = "you"
    authored.pop("delegation_id")
    authored["resolved_bindings"].pop("delegation_contract")
    authored["resolved_bindings"].pop("mandatory_floor")
    ctx_none = fixtures.binding_context(g, hashes, delegation=None)
    p1["authored_by_you_no_delegation"] = freeze_disposition(authored, ctx_none, schemas)["disposition"]
    agent_no_delegation = json.loads(json.dumps(authored))
    agent_no_delegation["authored_by"] = "planner"
    p1["agent_no_delegation"] = freeze_disposition(agent_no_delegation, ctx_none, schemas)["disposition"]
    _expect(p1 == {
        "inside_scope": "FROZEN", "outside_scope": "QUEUED_FOR_YOU",
        "authored_by_you_no_delegation": "FROZEN", "agent_no_delegation": "QUEUED_FOR_YOU",
    }, "P-1 dispositions wrong: %r" % p1)
    golden["P-1"] = {"disposition": "executable", "outcomes": p1}

    # ---- P-2: admission outcomes against a stub live-Sequence lookup.
    live = {cd: rh}
    dup = admit(base, live, context, schemas)
    _expect(dup["outcome"] == "DUPLICATE" and dup["records_minted"] == 0 and dup["sequences_created"] == 0,
            "P-2 duplicate outcome wrong: %r" % dup)
    _expect(dup["returns_existing_record_hash"] == rh, "P-2 duplicate must return the existing record")
    forced, _ = payloads["V-E"]
    new = admit(forced, live, context, schemas)
    _expect(new["outcome"] == "NEW_RECORD" and new["records_minted"] == 1 and new["sequences_created"] == 1,
            "P-2 force_new_sequence outcome wrong: %r" % new)
    fresh = admit(base, {}, context, schemas)
    _expect(fresh["outcome"] == "NEW_RECORD" and fresh["record_hash"] == rh, "P-2 fresh admission wrong")
    golden["P-2"] = {
        "disposition": "executable",
        "duplicate": {"records_minted": 0, "sequences_created": 0, "mints": ["obj-32"], "returns": rh},
        "new_record": {"records_minted": 1, "sequences_created": 1,
                       "mints": ["obj-04", "obj-05", "obj-06", "obj-07", "obj-17"],
                       "sequence_rule": {"object": "obj-08", "basis": "INV-40", "form": "lifecycle_coupling"}},
    }

    # obj-32 fixture built from P-2's duplicate facts.
    dup_record = fixtures.duplicate_admission_fixture(rh, cd, canonicalize(base))
    _expect(validate_payload(dup_record["kind"], dup_record, schemas).ok, "obj-32 fixture invalid")
    golden["duplicate_admission_fixture_content"] = content_address(canonicalize(dup_record))

    # ---- JH-1: the journal hash chain (genesis omits `previous`, D-37).
    e1 = journal_entry_hash({"transition": "t-c02", "seq": 1})
    e2 = journal_entry_hash({"transition": "t-s06", "seq": 2}, previous=e1)
    e3 = journal_entry_hash({"transition": "t-s07", "seq": 3}, previous=e2)
    _expect(len({e1, e2, e3}) == 3, "JH-1: chain entries must differ")
    _expect(journal_entry_hash({"transition": "t-s06", "seq": 2}, previous=e3) != e2,
            "JH-1: a different predecessor must change the entry hash")
    golden["JH-1"] = {"disposition": "executable", "entries": [e1, e2, e3]}

    return golden, g, base, payloads


def write_or_verify(write_fixtures=True):
    golden, g, base, payloads = build()
    out_dir = ROOT / "vectors" / "golden"
    fix_dir = ROOT / "vectors" / "fixtures"
    out_dir.mkdir(parents=True, exist_ok=True)
    fix_dir.mkdir(parents=True, exist_ok=True)

    def dump(path, value):
        text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    if write_fixtures:
        dump(fix_dir / "graph.json", g)
        dump(fix_dir / "va_record.json", base)
        dump(fix_dir / "vector_payloads.json", {k: v[0] for k, v in payloads.items()})
        dump(fix_dir / "env_fixture.json", fixtures.env_fixture())

    golden_path = out_dir / "golden_digests.json"
    rendered = json.dumps(golden, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if golden_path.exists():
        existing = golden_path.read_text(encoding="utf-8")
        if existing != rendered:
            raise GoldenError("golden_digests.json does not match the recomputation — investigate before regenerating")
        return "VERIFIED", golden_path
    with open(str(golden_path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    return "GENERATED", golden_path


if __name__ == "__main__":
    status, path = write_or_verify()
    print("goldens %s: %s" % (status, path))
