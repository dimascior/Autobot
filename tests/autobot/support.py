"""Shared plumbing for the Chapter 2 runtime tests.

Bootstraps ``src`` onto sys.path, loads the frozen Chapter 1 fixtures (the
test corpus every payload comes from), and builds runtimes in temporary
directories. The fixtures are consumed from the frozen chapter tree —
Chapter 2 never copies or re-derives them.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
_SRC = str(REPO / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from autobot import (  # noqa: E402
    Collector,
    Database,
    ManualClock,
    ProductionVerifierRunner,
    Registry,
    Sequencer,
    Store,
    mint_candidate_receipt,
)
from autobot.environment_probe import interpreter_identity  # noqa: E402
from contract_library import (  # noqa: E402
    artifact_root,
    content_address,
    object_hash,
)

CH1 = REPO / "docs" / "architecture" / "chapter1"
FIXTURES = CH1 / "vectors" / "fixtures"
GOLDEN = CH1 / "vectors" / "golden" / "golden_digests.json"
CH0_REGISTRIES = REPO / "docs" / "architecture" / "chapter0" / "registries"

# The exact bytes behind V-A's one specification input (harness/fixtures.py);
# seed_bound_objects proves the constant against the frozen record's address.
VA_INPUT = "Chapter 1 golden vector input.\nSecond line.\n"

T_START = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)

# The bound objects V-A (and the executable vectors the tests use) resolve.
SEED_OBJECTS = (
    "declaration_remote",
    "delegation_a",
    "verifier_checker_v1",
    "verifier_vector_v1",
    "transform_identity_v1",
)

# ---------------------------------------------------------------------------
# The LIVE verification plane (C4-M1): registrations whose closures are REAL
# seedable script bytes under the interpreter actually running this suite,
# so the production verifier runner executes the exact registered closure.
# The frozen Chapter 1 fixture registrations stay untouched (their closure
# digests are identity vectors, not executable artifacts) — records that
# must EXECUTE verification bind these instead.

# A real verifier honoring the registered result protocol: it re-hashes
# every subject artifact against its content address (the candidate's exact
# bytes — drift means FAIL), then concludes PASS.
LIVE_PASS_SCRIPT = b'''import hashlib
import json
import sys


def main():
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        context = json.load(fh)
    for artifact in context["subject"]["artifacts"]:
        with open(artifact["file"], "rb") as fh:
            digest = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
        if digest != artifact["content_sha256"]:
            print(json.dumps({"autobot_verifier_result": {
                "conclusion": "FAIL",
                "detail": "subject artifact drifted: %s"
                          % artifact["logical_path"]}}))
            return 0
    print(json.dumps({"autobot_verifier_result": {
        "conclusion": "PASS",
        "detail": "subject bytes verified against their content"
                  " addresses"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

LIVE_FAIL_SCRIPT = b'''import json
import sys

print(json.dumps({"autobot_verifier_result": {
    "conclusion": "FAIL", "detail": "registered assertion unmet"}}))
sys.exit(0)
'''

# Exits without a typed conclusion: the production runner translates this
# to INCONCLUSIVE (verifier_execution_failed) — never FAIL, never PASS.
LIVE_CRASH_SCRIPT = b'''import sys
sys.exit(3)
'''

# Exit 0 with prose instead of the result protocol: INCONCLUSIVE
# (malformed_verifier_result) — a model-style "tests passed" is not a
# receipt (CH4-llm-output-never-pass).
LIVE_PROSE_SCRIPT = b'''print("All tests passed! Everything looks great.")
'''

LIVE_LOCK = b"# autobot live verifier dependency lock: stdlib only\\n"

# (verifier_id, behavior) -> (registration_id suffix, script bytes).
LIVE_SPECS = {
    ("registry-checker", "PASS"): ("c1", LIVE_PASS_SCRIPT),
    ("vector-checker", "PASS"): ("c2", LIVE_PASS_SCRIPT),
    ("vector-checker", "FAIL"): ("c3", LIVE_FAIL_SCRIPT),
    ("vector-checker", "CRASH"): ("c4", LIVE_CRASH_SCRIPT),
    ("vector-checker", "PROSE"): ("c5", LIVE_PROSE_SCRIPT),
}


def live_registration(verifier_id, behavior="PASS"):
    """The live VerifierRegistration payload for one (verifier, behavior):
    a reproducibly pinned python closure over the live interpreter
    identity, its artifact_root recomputed per D-32."""
    suffix, script = LIVE_SPECS[(verifier_id, behavior)]
    closure = {"kind": "python",
               "script_sha256": content_address(script),
               "interpreter_id": interpreter_identity(),
               "dependency_lock_sha256": content_address(LIVE_LOCK)}
    return {"schema_version": "v1", "kind": "VerifierRegistration",
            "registration_id": "rg_" + suffix.rjust(32, "0"),
            "verifier_id": verifier_id, "version": "1.0.0",
            "closure": closure, "artifact_root": artifact_root(closure),
            "entrypoint": "verify.py", "execution_requirements": [],
            "registered_by": "you",
            "registered_utc": "2026-11-12T00:00:00.000Z"}


def live_registration_hash(verifier_id, behavior="PASS"):
    return object_hash("VerifierRegistration",
                       live_registration(verifier_id, behavior))


def live_environment_constraint():
    """An obj-17 constraint the live interpreter satisfies (the a1
    constraint of every live-bound record)."""
    import platform
    return {"interpreter": {
        "implementations": [platform.python_implementation().lower()],
        "versions": [platform.python_version()]}}


def load_json(path):
    with open(str(path), "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_fixture(name):
    return load_json(FIXTURES / name)


def load_golden():
    return load_json(GOLDEN)


def manual_clock():
    return ManualClock(T_START)


def make_runtime(data_dir, clock, holder="test-instance"):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(data_dir / "autobot.db")
    store = Store(data_dir / "store")
    registry = Registry(db, store, clock)
    sequencer = Sequencer(db, store, clock, holder=holder)
    return SimpleNamespace(
        data_dir=data_dir, db=db, store=store,
        registry=registry, sequencer=sequencer, clock=clock,
    )


def seed_bound_objects(rt, names=SEED_OBJECTS):
    """Admit the bound graph objects, the LIVE verifier registrations with
    their executable closure bytes, and the specification input bytes."""
    graph = load_fixture("graph.json")
    for name in names:
        rt.registry.admit_object(graph[name], submitted_by="you")
    for verifier_id, behavior in sorted(LIVE_SPECS):
        _suffix, script = LIVE_SPECS[(verifier_id, behavior)]
        rt.store.put(script)
        rt.registry.admit_object(live_registration(verifier_id, behavior),
                                 submitted_by="you")
    rt.store.put(LIVE_LOCK)
    record = load_fixture("va_record.json")
    listed = record["specification_inputs"]["name_to_exact_byte_sha256"][
        "chapter1-brief.md"]
    data = VA_INPUT.encode("utf-8")
    if content_address(data) != listed:
        raise RuntimeError(
            "support.VA_INPUT does not hash to the frozen record's input"
            " address — fixture drift, fix the constant"
        )
    rt.store.put(data)


def ensure_candidate_repo(rt):
    """A lazily created real Git repository per runtime, for candidate
    tree capture (cap-42): branch main at commit A, commit B reachable.
    Returns (repo_path, sha_old, sha_new)."""
    if not hasattr(rt, "candidate_repo"):
        repo = rt.data_dir / "candidate-repo"
        sha_old, sha_new = init_git_repo(repo)
        rt.candidate_repo = (repo, sha_old, sha_new)
    return rt.candidate_repo


def mint_and_freeze(rt, attempt_id, fence, treeish=None):
    """Mint the REAL obj-16 through the git_adapter (SOURCE_MODIFYING work
    captures the candidate tree from the runtime's candidate repository;
    READ_ONLY work takes no tree) and freeze it (t-a05/t-s07). Returns the
    admitted candidate address."""
    state = rt.sequencer.candidate_state(attempt_id)
    if state["finalization_class"] == "SOURCE_MODIFYING":
        repo, _sha_old, sha_new = ensure_candidate_repo(rt)
        minted = mint_candidate_receipt(rt.sequencer, rt.store, attempt_id,
                                        repo_path=repo,
                                        treeish=treeish or sha_new)
    else:
        minted = mint_candidate_receipt(rt.sequencer, rt.store, attempt_id)
    rt.sequencer.freeze_candidate(attempt_id, fence,
                                  minted["candidate_address"])
    return minted["candidate_address"]


def admitted_runtime(data_dir, clock=None, holder="test-instance"):
    """A runtime with the seed admitted and V-A frozen: returns (rt, result)."""
    clock = clock or manual_clock()
    rt = make_runtime(data_dir, clock, holder=holder)
    seed_bound_objects(rt)
    result = rt.registry.admit_payload(
        load_fixture("va_record.json"), submitted_by="planner")
    return rt, result


def local_record_payload(task_contract_id=None, predecessors=None,
                         finalization_class=None,
                         finalization_requirements=None, objective=None,
                         completion_policies=None,
                         evidence_requirements=None,
                         vector_behavior="PASS"):
    """The M5/M6 SL-path contract family: V-A re-targeted to the LOCAL
    authority declaration (SOURCE_MODIFYING + authority_location LOCAL,
    finalization requirement LOCAL_CANONICAL_COMMIT by default), bound —
    since C4-M1 — to the LIVE verifier registrations so the production
    runner executes real registered closures (a1 keeps its environment
    constraint, now satisfied by the live interpreter; a2's verifier
    behavior is selectable: PASS, FAIL, CRASH or PROSE). The keyword
    overrides mint sibling contracts for the dependency-release
    scenarios: a distinct ``objective`` changes the requirement tier
    (``task_contract_id`` alone is record-tier only, so admission
    deduplicates it on the shared contract_digest), ``predecessors`` are
    frozen obj-07 edges, and the READ_ONLY variant reshapes the payload
    per D-54 (no target_ref; the delegation's registered floor for
    (autobot, READ_ONLY) is empty; SEM-05 derives requirements [])."""
    import json as _json
    payload = _json.loads(_json.dumps(load_fixture("va_record.json")))
    graph = load_fixture("graph.json")
    payload["task_contract_id"] = task_contract_id or ("tc_" + "face" * 8)
    payload["source_domain"] = {"repository_id": "autobot",
                                "authority_location": "LOCAL",
                                "target_ref": "refs/heads/main"}
    payload["resolved_bindings"]["repository_authority_declaration"] = (
        object_hash("RepositoryAuthorityDeclaration",
                    graph["declaration_local"]))
    payload.pop("remote_verification_mode", None)
    # The live verification plane: the assertions keep their frozen ids and
    # verifier names; the BINDINGS name the executable live registrations.
    payload["acceptance_assertions"]["ids_and_environment_constraints"][0][
        "environment_constraint"] = live_environment_constraint()
    payload["resolved_bindings"]["verifier_registrations"] = sorted((
        live_registration_hash("registry-checker", "PASS"),
        live_registration_hash("vector-checker", vector_behavior),
    ))
    if objective is not None:
        payload["objective"] = objective
    if predecessors is not None:
        payload["predecessors"] = predecessors
    if completion_policies is not None:
        payload["completion_policies"] = completion_policies
    if evidence_requirements is not None:
        payload["evidence_requirements"] = evidence_requirements
    if finalization_class is not None:
        payload["finalization_class"] = finalization_class
    if payload["finalization_class"] == "READ_ONLY":
        payload["source_domain"].pop("target_ref")
        payload["resolved_bindings"]["mandatory_floor"][
            "mandatory_assertion_ids"] = []
    payload["finalization_requirements"] = (
        finalization_requirements if finalization_requirements is not None
        else ([] if payload["finalization_class"] == "READ_ONLY"
              else ["LOCAL_CANONICAL_COMMIT"]))
    return payload


def intelligence_environment():
    """The compiler's bound authority context over the frozen Chapter 1
    graph: the LOCAL declaration, delegation A, the two registered
    verifiers and the identity transform, with the V-A engineering
    profile as defaults. Everything here is an object the tests also
    admit through the Registry, so compiled candidates freeze cleanly."""
    import json as _json
    from intelligence import RepositoryEnvironment
    graph = load_fixture("graph.json")
    record = load_fixture("va_record.json")
    # C4-M1: compiled contracts bind the LIVE registrations, so the
    # production runner executes their closures; a1's constraint names the
    # live interpreter.
    assertions = _json.loads(_json.dumps(record["acceptance_assertions"]))
    assertions["ids_and_environment_constraints"][0][
        "environment_constraint"] = live_environment_constraint()
    defaults = {
        "acceptance_assertions": assertions,
        "allowed_scope": record["allowed_scope"],
        "capability_requirements": record["capability_requirements"],
        "evidence_requirements": record["evidence_requirements"],
        "retry_policy": {"max_retries": 2},
        "repair_policy": {"max_repairs": 1},
        "reuse_policy": {"mode": "NEVER"},
        "effect_policy": {"effect_intent_ttl_seconds": 300,
                          "effect_hard_timeout_seconds": 240},
        "role_transform_id": "identity-projection",
    }
    return RepositoryEnvironment(
        declaration=graph["declaration_local"],
        delegation=graph["delegation_a"],
        verifier_registrations=[
            live_registration("registry-checker", "PASS"),
            live_registration("vector-checker", "PASS")],
        transform_registrations=[graph["transform_identity_v1"]],
        defaults=defaults)


def session_record_bytes(call_id, native_session_id="native-sess-9",
                         controller_id=None, **extra):
    """Producer-shaped obj-11 bytes (the provider task/handle record
    family): recognized facts join the witness to the bound call."""
    import json as _json
    payload = {"call_id": call_id,
               "native_session_id": native_session_id,
               "controller_id": controller_id or ("main_" + "0" * 32)}
    payload.update(extra)
    return _json.dumps(payload, sort_keys=True).encode("utf-8")


def collect_receipt(rt, attempt_id, window_seconds=None):
    """Derive-and-admit the frozen attempt's obj-15 through the registered
    Collector seat (cap-30); returns the admitted receipt's address."""
    kwargs = {}
    if window_seconds is not None:
        kwargs["evidence_window_seconds"] = window_seconds
    collector = Collector(rt.sequencer, rt.store, **kwargs)
    return collector.collect_and_admit(attempt_id)["receipt_address"]


def drive_to_evidence_complete(rt, admitted_result, role="builder",
                               artifact=b"engineering artifact bytes\n"):
    """Drive one admitted sequence to attempt EVIDENCE_COMPLETE / sequence
    VERIFYING through the full production evidence plane: dispatch,
    witnesses, finish, the REAL git_adapter-minted obj-16 freeze, the
    registered Collector's obj-15, complete_evidence."""
    seq = rt.sequencer
    sequence_id = admitted_result["sequence_id"]
    state = rt.db.conn.execute(
        "SELECT state FROM sequences WHERE sequence_id = ?",
        (sequence_id,)).fetchone()["state"]
    if state == "DECLARED":
        seq.mark_ready(sequence_id)
    info = seq.start_execution(sequence_id)
    attempt_id, fence = info["attempt_id"], info["fence"]
    seq.reserve_attempt(attempt_id, expected_fence=fence)
    projection = rt.store.put(b"projection bytes for finalization\n")
    dispatched = seq.dispatch_attempt(attempt_id, role, "fake", projection)
    # The provider's own session record (C3-M2): recognized facts join it
    # to the Sequencer-minted call — the causal chain's first link.
    start = seq.admit_witness(attempt_id, "obj-11", "autoswarm",
                              session_record_bytes(dispatched["call_id"]))
    seq.attempt_running(attempt_id, "native-sess-9",
                        start["content_address"])
    seq.admit_witness(attempt_id, "obj-12", "helios",
                      b"command governance log\n")
    end = seq.admit_witness(attempt_id, "obj-13", "agent_clis",
                            b"transcript\n")
    if artifact is not None:
        seq.admit_artifact(attempt_id, "artifact/engineering-notes",
                           artifact)
    seq.finish_attempt(attempt_id, role, "success", end["content_address"])
    # C4-M1: the REAL obj-16 — the git_adapter captures the candidate (the
    # tree for SOURCE_MODIFYING work) and the freeze admission validates
    # every ledger-derivable binding.
    mint_and_freeze(rt, attempt_id, fence)
    # The registered Collector derives COMPLETE from the frozen record's
    # evidence_requirements (COMMAND_GOVERNANCE + EXECUTION_LIFECYCLE)
    # over the admitted rows, mints obj-15 and admits it (C3-M1).
    receipt = collect_receipt(rt, attempt_id)
    seq.complete_evidence(attempt_id, fence, receipt)
    return {"attempt_id": attempt_id, "fence": fence,
            "attempt_contract_hash": info["attempt_contract_hash"]}


def drive_to_verified(rt, admitted_result, role="builder", runner=None):
    """Drive one admitted sequence through the full M2–C4 lifecycle to
    sequence VERIFIED / attempt VERIFIED (shared by the finalization tests
    and the crash driver): the production evidence plane, then the
    PRODUCTION verifier runner executing the exact registered live
    closures, then conclusion. The caller holds the instance lease.
    ``runner`` overrides the production runner for records whose frozen
    bindings are the chapter-1 identity fixtures (no executable closure
    bytes exist for those): the scripted fake still produces canonically
    BOUND receipts through the same admission seam."""
    info = drive_to_evidence_complete(rt, admitted_result, role=role)
    runner = runner or ProductionVerifierRunner(rt.sequencer, rt.store)
    outcome = runner.verify_attempt(info["attempt_id"])
    if outcome["inconclusive"]:
        raise RuntimeError(
            "drive_to_verified hit an INCONCLUSIVE production result; the"
            " scenario wanted PASS closures")
    rt.sequencer.conclude_verification(info["attempt_id"], info["fence"])
    return info


def init_git_repo(path):
    """A disposable real Git repository for the M5 effect tests: branch
    ``refs/heads/main`` at commit A (the expected old value) with commit B
    (the desired new value) existing as an object. Returns
    ``(sha_old, sha_new)``."""
    import subprocess

    def git(*args):
        proc = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError("git %s failed: %s %s"
                               % (args, proc.stdout, proc.stderr))
        return proc.stdout.strip()

    Path(path).mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "autobot@test.invalid")
    git("config", "user.name", "autobot-test")
    (Path(path) / "one.txt").write_text("one\n", encoding="utf-8")
    git("add", "one.txt")
    git("commit", "-q", "-m", "commit A")
    sha_old = git("rev-parse", "HEAD")
    (Path(path) / "two.txt").write_text("two\n", encoding="utf-8")
    git("add", "two.txt")
    git("commit", "-q", "-m", "commit B")
    sha_new = git("rev-parse", "HEAD")
    # main back to A; B stays reachable as an object for the CAS target.
    git("update-ref", "refs/heads/main", sha_old)
    git("checkout", "-q", "--detach", sha_new)
    return sha_old, sha_new
