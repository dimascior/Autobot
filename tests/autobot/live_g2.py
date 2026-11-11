"""The bounded real-provider test (G2 closure, A2 mechanism) — operator-run.

NOT part of the hermetic suite (discovery matches test_*.py only): this
script launches ONE real AutoSwarm claude-code child and proves, against the
live provider home, the three demonstrations the operator named:

  T3  a missing session lookup is UNOBSERVED, never launch-failure evidence:
      before any launch, find_session(call_id) is None and
      reconcile_dispatched HOLDS the attempt in DISPATCHED (no transition);
  T1  one Sequencer-minted call_id cannot produce duplicate specialist
      sessions: two CLI launches with the same --call-id (A2, the fork's
      provider-native key) yield one call directory, one task record, one
      native session — the second launch adopts;
  T2  the session is recovered from durable provider state alone:
      find_session reads calls/<call_id>/handle.json, and
      reconcile_dispatched adopts it through admit_witness (obj-11, exact
      handle bytes) + t-a03.

The child is bounded: one prompt demanding a one-word reply and no tool
use. Model policy is the operator's standing instruction (2026-11-10):
claude-code children run claude-opus-4-6[1m]; codex children would run
gpt-6-luna at max effort. Everything observed lands in a JSON report.

Run from the repo root:
  PYTHONPATH=src .venv/Scripts/python.exe tests/autobot/live_g2.py <state-dir>
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import support
from autobot import project_prompt, project_state, reconcile_dispatched
from autobot.transport import Transport

AUTOSWARM_EXE = os.environ.get(
    "AUTOSWARM_EXE", r"C:\Users\dimas\.local\bin\autoswarm.exe")
AUTOSWARM_HOME = Path(os.environ.get(
    "AUTOSWARM_HOME", r"E:\Users\dimas\autoswarm-home"))
PARENT_BINDING = os.environ.get("AUTOSWARM_PARENT", (
    r"C:\Users\dimas\AppData\Local\autoswarm\parents"
    r"\70c474174b17f1fc482cd3bb7284ceb40619a866eb91844701814570bd16682f.json"))
# The operator's standing model policy (2026-11-10).
MODEL_POLICY = {"claude-code": {"model": "claude-opus-4-6[1m]"},
                "codex": {"model": "gpt-6-luna", "effort": "max"}}
PROVIDER = "claude-code"
NATIVE_ID_WAIT_S = 120
RESULT_WAIT_S = 360


class AutoSwarmLiveTransport(Transport):
    """The Transport contract over the installed AutoSwarm CLI and its
    durable home. launch() invokes the real CLI with the Sequencer-minted
    key (A2 --call-id); observation reads provider files only."""

    def __init__(self, home: Path, workspace: Path, report: dict):
        self.home = home
        self.workspace = workspace
        self.report = report

    def _cli(self, argv):
        env = dict(os.environ)
        env["AUTOSWARM_HOME"] = str(self.home)
        proc = subprocess.run([AUTOSWARM_EXE, *argv], capture_output=True,
                              text=True, encoding="utf-8", timeout=300,
                              env=env)
        try:
            envelope = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("unparseable CLI output (exit %s): %r %r"
                               % (proc.returncode, proc.stdout, proc.stderr))
        if not envelope.get("ok"):
            raise RuntimeError("CLI error: %s" % json.dumps(envelope.get("error")))
        return envelope["data"]

    def launch(self, projection_address, provider, role, call_id):
        prompt = ("Autobot bounded G2 transport test (role %s, projection %s)."
                  " Reply with exactly one word: done."
                  " Do not run any commands or tools." % (role, projection_address))
        selection = MODEL_POLICY[provider]
        argv = ["run", "--agent", provider, "--cwd", str(self.workspace),
                "--call-id", call_id, "--model", selection["model"],
                "--background", "--parent", PARENT_BINDING]
        if "effort" in selection:
            argv += ["--effort", selection["effort"]]
        data = self._cli(argv + [prompt])
        self.report.setdefault("launches", []).append(
            {"argv_model": selection, "call_id": data.get("call_id"),
             "state": data.get("state"),
             "native_session_id": data.get("native_session_id")})
        return data.get("native_session_id")

    def find_session(self, call_id):
        handle_path = self.home / "calls" / call_id / "handle.json"
        if not handle_path.exists():
            return None
        data = handle_path.read_bytes()  # the producer's exact bytes (D-38)
        try:
            native = json.loads(data.decode("utf-8")).get("native_session_id")
        except ValueError:
            return None
        if not native:
            return None
        return {"native_session_id": native, "evidence": data}

    def positive_launch_failure(self, call_id):
        # Not exercised live in this bounded scope: no registered bounded
        # rule is claimed here, so no failure can be "proven" — t-a10 stays
        # proven by the deterministic arms.
        return None

    def collect(self, call_id):
        result_path = self.home / "calls" / call_id / "result.json"
        data = result_path.read_bytes()
        outcome = json.loads(data.decode("utf-8")).get("outcome")
        return {"witness_files": [("obj-11", "autoswarm", data)],
                "outcome": "success" if outcome == "completed" else outcome}


def main():
    state_dir = Path(sys.argv[1]).resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    workspace = state_dir / "child-workspace"
    workspace.mkdir(exist_ok=True)
    report = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "autoswarm_home": str(AUTOSWARM_HOME), "provider": PROVIDER,
              "checks": []}
    # Reproducibility provenance (operator requirement): which installed
    # tool, which fork commit (when the runner exports it), which model.
    version = subprocess.run([AUTOSWARM_EXE, "--version"], capture_output=True,
                             text=True, encoding="utf-8", timeout=60)
    report["provenance"] = {
        "autoswarm_exe": AUTOSWARM_EXE,
        "tool_version": version.stdout.strip() or version.stderr.strip(),
        "fork_commit": os.environ.get("AUTOSWARM_FORK_COMMIT"),
        "model_policy": MODEL_POLICY[PROVIDER],
        "parent_binding": PARENT_BINDING,
    }
    checks = report["checks"]

    def check(name, ok, **details):
        checks.append({"check": name, "pass": bool(ok), **details})
        print(("PASS  " if ok else "FAIL  ") + name + "  " + json.dumps(details))
        return ok

    rt, admitted = support.admitted_runtime(state_dir / "autobot",
                                            clock=support.manual_clock())
    seq = rt.sequencer
    sequence_id = admitted["sequence_id"]
    seq.acquire_lease(ttl_seconds=360000)
    seq.mark_ready(sequence_id)
    info = seq.start_execution(sequence_id)
    attempt_id, fence = info["attempt_id"], info["fence"]
    seq.reserve_attempt(attempt_id, expected_fence=fence)

    record_row = rt.db.conn.execute(
        "SELECT content_address FROM records WHERE record_hash = ?",
        (admitted["record_hash"],)).fetchone()
    payload = json.loads(rt.store.get(record_row["content_address"]).decode("utf-8"))
    identity = {"sequence_id": sequence_id, "attempt_id": attempt_id,
                "fence": fence,
                "attempt_contract_hash": info["attempt_contract_hash"]}
    _, projection = project_prompt(
        rt.store, payload, "builder", identity,
        {"author": "planner",
         "guidance": "Bounded G2 real-provider test: reply done, no tools."})

    call_id = seq.dispatch_attempt(attempt_id, "builder", PROVIDER,
                                   projection)["call_id"]
    report["call_id"] = call_id
    transport = AutoSwarmLiveTransport(AUTOSWARM_HOME, workspace, report)

    def journal_count():
        return rt.db.conn.execute(
            "SELECT COUNT(*) AS n FROM transition_journal").fetchone()["n"]

    def attempt_state():
        return rt.db.conn.execute(
            "SELECT state FROM attempts WHERE attempt_id = ?",
            (attempt_id,)).fetchone()["state"]

    # T3 — before any launch: unobserved is held, never failed.
    before = journal_count()
    held = reconcile_dispatched(seq, transport)
    check("T3_unobserved_holds",
          transport.find_session(call_id) is None
          and held == [{"attempt_id": attempt_id, "call_id": call_id,
                        "action": "held"}]
          and attempt_state() == "DISPATCHED" and journal_count() == before,
          reconcile=held, state=attempt_state())

    # T1 — two launches, one Sequencer-minted key, one session.
    calls_before = {p.name for p in (AUTOSWARM_HOME / "calls").glob("call_*")}
    native_1 = transport.launch(projection, PROVIDER, "builder", call_id)
    native_2 = transport.launch(projection, PROVIDER, "builder", call_id)
    calls_after = {p.name for p in (AUTOSWARM_HOME / "calls").glob("call_*")}
    task = json.loads((AUTOSWARM_HOME / "calls" / call_id / "task.json")
                      .read_text(encoding="utf-8"))
    check("T1_idempotent_launch",
          calls_after - calls_before == {call_id}
          and task["call_id"] == call_id
          and (native_1 is None or native_2 is None or native_1 == native_2),
          new_call_dirs=sorted(calls_after - calls_before),
          native_first=native_1, native_second=native_2)

    # T2 — recover the session from durable provider state and adopt it.
    deadline = time.monotonic() + NATIVE_ID_WAIT_S
    found = None
    while time.monotonic() < deadline:
        found = transport.find_session(call_id)
        if found:
            break
        time.sleep(1.0)
    adopted = reconcile_dispatched(seq, transport)
    bound = rt.db.conn.execute(
        "SELECT native_session_id FROM attempt_calls WHERE attempt_id = ?",
        (attempt_id,)).fetchone()["native_session_id"]
    witness = rt.db.conn.execute(
        "SELECT witness_kind, producer, content_sha256 FROM attempt_witnesses"
        " WHERE attempt_id = ?", (attempt_id,)).fetchone()
    check("T2_adopted_from_durable_state",
          found is not None
          and adopted and adopted[0]["action"] == "adopted"
          and attempt_state() == "RUNNING"
          and bound == found["native_session_id"]
          and witness is not None and witness["witness_kind"] == "obj-11"
          and rt.store.get(witness["content_sha256"]) == found["evidence"],
          adopted=adopted, native_session_id=bound)

    # Bounded completion: wait for the child's result, admit it, finish.
    deadline = time.monotonic() + RESULT_WAIT_S
    collected = None
    while time.monotonic() < deadline:
        if (AUTOSWARM_HOME / "calls" / call_id / "result.json").exists():
            collected = transport.collect(call_id)
            break
        time.sleep(2.0)
    if collected is None:
        check("completion_observed", False, note="no result.json before deadline")
    else:
        kind, producer, data = collected["witness_files"][0]
        admitted_result = seq.admit_witness(attempt_id, kind, producer, data)
        final = seq.finish_attempt(attempt_id, "builder", collected["outcome"],
                                   admitted_result["content_address"])
        check("completion_observed", final == "FINISHED",
              outcome=collected["outcome"], final_state=final)

    recover_report = seq.recover()
    check("recover_clean", recover_report["intents_in_doubt"] == [],
          intents_in_doubt=recover_report["intents_in_doubt"])
    report["project_state"] = {
        "attempt": [a for a in project_state(rt.db)["attempts"]
                    if a["attempt_id"] == attempt_id]}
    report["all_passed"] = all(c["pass"] for c in checks)
    (state_dir / "g2-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"all_passed": report["all_passed"],
                      "report": str(state_dir / "g2-report.json"),
                      "call_id": call_id}))
    rt.db.close()
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
