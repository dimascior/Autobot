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

from autobot import Database, ManualClock, Registry, Sequencer, Store  # noqa: E402
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
