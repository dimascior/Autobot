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
    Registry,
    Sequencer,
    Store,
)
from contract_library import content_address  # noqa: E402

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
    """Admit the bound graph objects and the specification input bytes."""
    graph = load_fixture("graph.json")
    for name in names:
        rt.registry.admit_object(graph[name], submitted_by="you")
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
                         finalization_requirements=None, objective=None):
    """The M5/M6 SL-path contract family: V-A re-targeted to the LOCAL
    authority declaration (SOURCE_MODIFYING + authority_location LOCAL,
    finalization requirement LOCAL_CANONICAL_COMMIT by default). The
    keyword overrides mint sibling contracts for the dependency-release
    scenarios: a distinct ``objective`` changes the requirement tier
    (``task_contract_id`` alone is record-tier only, so admission
    deduplicates it on the shared contract_digest), ``predecessors`` are
    frozen obj-07 edges, and the READ_ONLY variant reshapes the payload
    per D-54 (no target_ref; the delegation's registered floor for
    (autobot, READ_ONLY) is empty; SEM-05 derives requirements [])."""
    import json as _json
    from contract_library import object_hash
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
    if objective is not None:
        payload["objective"] = objective
    if predecessors is not None:
        payload["predecessors"] = predecessors
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
    from intelligence import RepositoryEnvironment
    graph = load_fixture("graph.json")
    record = load_fixture("va_record.json")
    defaults = {
        "acceptance_assertions": record["acceptance_assertions"],
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
        verifier_registrations=[graph["verifier_checker_v1"],
                                graph["verifier_vector_v1"]],
        transform_registrations=[graph["transform_identity_v1"]],
        defaults=defaults)


def collect_receipt(rt, attempt_id, window_seconds=None):
    """Derive-and-admit the frozen attempt's obj-15 through the registered
    Collector seat (cap-30); returns the admitted receipt's address."""
    kwargs = {}
    if window_seconds is not None:
        kwargs["evidence_window_seconds"] = window_seconds
    collector = Collector(rt.sequencer, rt.store, **kwargs)
    return collector.collect_and_admit(attempt_id)["receipt_address"]


def drive_to_verified(rt, admitted_result, role="builder"):
    """Drive one admitted sequence through the full M2–M4 lifecycle to
    sequence VERIFIED / attempt VERIFIED (shared by the finalization tests
    and the crash driver). The caller holds the instance lease."""
    import json as _json
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
    seq.dispatch_attempt(attempt_id, role, "fake", projection)
    start = seq.admit_witness(attempt_id, "obj-11", "autoswarm",
                              b"provider session record\n")
    seq.attempt_running(attempt_id, "native-sess-9",
                        start["content_address"])
    seq.admit_witness(attempt_id, "obj-12", "helios",
                      b"command governance log\n")
    end = seq.admit_witness(attempt_id, "obj-13", "agent_clis",
                            b"transcript\n")
    seq.finish_attempt(attempt_id, role, "success", end["content_address"])
    seq.freeze_candidate(attempt_id, fence,
                         rt.store.put(b"candidate tree stand-in\n"))
    # The registered Collector derives COMPLETE from the frozen record's
    # evidence_requirements (COMMAND_GOVERNANCE + EXECUTION_LIFECYCLE)
    # over the admitted rows, mints obj-15 and admits it (C3-M1).
    receipt = collect_receipt(rt, attempt_id)
    seq.complete_evidence(attempt_id, fence, receipt)
    environment = _json.dumps(
        {"schema_version": "v1", "kind": "ActualVerificationEnvironment",
         "environment_id": "ve_" + "0" * 32,
         "attempt": {"sequence_id": sequence_id, "attempt_id": attempt_id,
                     "version": 1, "fence": fence},
         "probed_utc": "2026-11-09T12:00:00.000Z",
         "interpreter": {"implementation": "cpython", "version": "3.12.4",
                         "executable_sha256": "sha256:" + "0" * 64},
         "platform": {"os": "windows", "os_version": "10",
                      "architecture": "x86_64"},
         "env_vars": {}, "tools": {}}, sort_keys=True).encode("utf-8")
    for assertion_id, env in (("a1", environment), ("a2", None)):
        seq.register_verifier_result(
            attempt_id, assertion_id, "PASS", "verifier_runner",
            _json.dumps({"schema_version": "v1",
                         "kind": "VerificationReceipt",
                         "assertion_id": assertion_id, "outcome": "PASS",
                         "attempt_id": attempt_id},
                        sort_keys=True).encode("utf-8"),
            environment=env)
    seq.conclude_verification(attempt_id, fence)
    return {"attempt_id": attempt_id, "fence": fence,
            "attempt_contract_hash": info["attempt_contract_hash"]}


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
