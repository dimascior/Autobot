"""The first real effect executor (M5): the local canonical ref CAS.

Principal ``git_sequencer`` (cap-38: local commit and local ref CAS; the
Sequencer holds C on it). The executor's whole authority contract:

1. it BEGINS an external effect only from authority returned by
   ``Sequencer.committed_intent()`` — a durably committed, still-PENDING
   EffectIntent inside its hard-timeout window (D-68/D-71);
2. it executes exactly the authorized compare-and-swap — ``git update-ref
   <target> <new_value> <expected_old>``, Git's own atomic CAS primitive —
   nothing else, ever;
3. it observes repository state independently afterwards and feeds the
   observation into the registered outcome machine
   (``Sequencer.resolve_intent``) under the frozen ``local`` rule;
4. it mints the obj-26 CommitReceipt bytes for a HAPPENED outcome
   (git_sequencer is obj-26's registered minter).

The executor grants no protocol authority: outcomes are durable intent
state, and only the Sequencer's registered transitions (t-s16/t-s18/t-s19,
t-r01/t-r02) move the Sequence. On restart, ``observe_and_resolve`` is the
re-entry: observation alone decides, never inference — process death plus
the single-writer discipline is what makes the executor positively fenced
(the frozen ``local`` rule's note: a local ref update has no asynchronous
server, so a fenced executor plus the old value proves absence).
"""

import json
import os
import subprocess

from .models import ConflictError

EFFECT_ID = "eff-local-canonical-cas"


def _git(repo_path, *args):
    return subprocess.run(["git", "-C", str(repo_path), *args],
                          capture_output=True, text=True)


def observe_ref(repo_path, ref):
    """The independent observation: the ref's current value, or None when
    the ref does not exist."""
    proc = _git(repo_path, "rev-parse", "--verify", "--quiet", ref)
    return proc.stdout.strip() if proc.returncode == 0 else None


def ref_lock_present(repo_path, ref):
    """A live ``<ref>.lock`` means an update may be in flight: the fenced
    half of the ABSENT determination is then not establishable."""
    return os.path.exists(os.path.join(str(repo_path), ".git",
                                       *(ref + ".lock").split("/")))


def classify_observation(observed, expected_old, new_value,
                         executor_fenced, ref_locked):
    """Map an observed ref value onto the frozen ``local`` outcome rule's
    observation vocabulary."""
    if observed == new_value:
        return "target_equals_new"
    if observed == expected_old:
        if executor_fenced and not ref_locked:
            return "target_equals_expected_old_and_executor_fenced_and_no_ref_lock"
        return "target_equals_expected_old_otherwise"
    return "target_other"


def mint_commit_receipt(facts, observed_value):
    """obj-26 CommitReceipt bytes (minter git_sequencer): the effect
    identity and the independently observed value, deterministic bytes."""
    payload = {"schema_version": "v1", "kind": "CommitReceipt",
               "intent_id": facts["intent_id"],
               "effect_id": facts["effect_id"],
               "target": facts["target"],
               "expected_old": facts["expected_old"],
               "new_value": facts["new_value"],
               "observed_value": observed_value}
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _verify_authority(facts):
    """The executor-side verification of the committed intent: effect kind,
    CAS identity (target, expected old, new value) and the bindings the
    intent row carries (sequence, attempt, fence) must all be present —
    the executor runs nothing it cannot name exactly."""
    if facts.get("effect_id") != EFFECT_ID:
        raise ConflictError(
            "intent %s authorizes effect %r, not the local canonical CAS"
            % (facts.get("intent_id"), facts.get("effect_id")))
    for field in ("target", "expected_old", "new_value",
                  "sequence_id", "attempt_id", "fence", "reservation_id"):
        if not facts.get(field):
            raise ConflictError(
                "committed intent lacks %s: the executor refuses an"
                " underspecified authorization" % field)


def observe_and_resolve(sequencer, intent_id, repo_path, *,
                        executor_fenced):
    """Observation-only re-entry (restart, re-check, uncertainty
    resolution): read the ref, classify under the frozen rule, feed the
    outcome machine. ``executor_fenced`` is the caller's positive statement
    that no executor invocation for this intent can still be in flight
    (e.g. the owning process died, under the single-writer discipline)."""
    cur = sequencer.db.conn.cursor()
    row = cur.execute(
        "SELECT * FROM effect_intents WHERE intent_id = ?",
        (intent_id,)).fetchone()
    if row is None:
        raise ConflictError("unknown intent %r" % intent_id)
    observed = observe_ref(repo_path, row["target"])
    observation = classify_observation(
        observed, row["expected_old"], row["new_value"], executor_fenced,
        ref_lock_present(repo_path, row["target"]))
    outcome = sequencer.resolve_intent(intent_id, observation,
                                       cause="local-cas-observation")
    report = {"intent_id": intent_id, "target": row["target"],
              "observed_value": observed, "observation": observation,
              "outcome": outcome}
    if outcome == "HAPPENED":
        report["receipt"] = mint_commit_receipt(
            {"intent_id": intent_id, "effect_id": row["effect_id"],
             "target": row["target"], "expected_old": row["expected_old"],
             "new_value": row["new_value"]}, observed)
    return report


def execute_local_cas(sequencer, intent_id, repo_path):
    """The full executor pass: authority read, the one authorized CAS,
    independent observation, outcome resolution, receipt minting."""
    facts = sequencer.committed_intent(intent_id)  # authority (D-71)
    _verify_authority(facts)
    update = _git(repo_path, "update-ref", facts["target"],
                  facts["new_value"], facts["expected_old"])
    # The subprocess has returned: this executor holds no in-flight
    # operation, so the observation below is made fenced.
    observed = observe_ref(repo_path, facts["target"])
    observation = classify_observation(
        observed, facts["expected_old"], facts["new_value"], True,
        ref_lock_present(repo_path, facts["target"]))
    outcome = sequencer.resolve_intent(intent_id, observation,
                                       cause="local-cas-executor")
    report = {"intent_id": intent_id, "target": facts["target"],
              "observed_value": observed, "observation": observation,
              "outcome": outcome, "update_ref_exit": update.returncode}
    if outcome == "HAPPENED":
        report["receipt"] = mint_commit_receipt(facts, observed)
    return report
