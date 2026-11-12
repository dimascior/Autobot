"""The Taskforce: coordinated engineering work over authority APIs.

The Taskforce owns DOING the work (cap-22) and WATCHING it (cap-23):
allocating specialist seats for READY work, projecting the frozen
contract into a specialist task (obj-10), launching provider sessions
through the Transport contract, collecting producer-original witnesses
and artifacts, admitting findings, and walking each attempt through the
Sequencer's registered transitions. It holds no authority of its own
(D-63, cap-51 forbids it transitions): every state change below goes
through a public Sequencer API, which enforces lease, fence, budgets
and the frozen rows regardless of what this code asks for.

Allocation discipline (the Chief Engineer gate): a sequence's FIRST
attempt is allocated from its originating admitted proposal; every
LATER attempt (a repair after t-s11, a retry after t-s08) requires an
explicit seat authorization minted by a REPAIR_TASK/ASSIGN_SPECIALIST
proposal — failures never re-execute themselves, and findings never
become work without the planner's say (the F-CE review axes "findings
automatically becoming work" and "circular task generation").

Authority state is the only truth the pump trusts: seats are plan-store
bookkeeping reconciled FROM ledger facts (by call_id), never the other
way around. Reads go through the read-only connection; effect
parameters proposed by a specialist are exactly that — proposals — the
registered executor and the ref-CAS semantics decide what happened.
"""

import json

from autobot.collector import Collector
from autobot.git_executor import execute_local_cas
from autobot.projection import project_prompt
from autobot.transport import reconcile_dispatched

from .findings import admit_finding
from .proposals_util import canonical_json_digest


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class Taskforce:
    def __init__(self, plan, sequencer, store, transport, runner,
                 agent_factory, clock, ro_conn, repo_path=None,
                 repository_context=None, collector=None):
        self.plan = plan
        self.sequencer = sequencer
        self.store = store
        self.transport = transport
        self.runner = runner
        self.factory = agent_factory
        self.clock = clock
        self.ro = ro_conn
        self.repo_path = repo_path
        self.repository_context = repository_context
        # The registered collector seat (cap-30): the Taskforce ASKS it to
        # collect; it never classifies, mints or submits a receipt itself
        # (task_force is on cap-30's forbidden list).
        self.collector = collector or Collector(sequencer, store)

    # ------------------------------------------------------------- reads

    def _sequences_in(self, states):
        marks = ",".join("?" for _ in states)
        return [dict(r) for r in self.ro.execute(
            "SELECT * FROM sequences WHERE state IN (%s)"
            " ORDER BY sequence_id" % marks, tuple(states)).fetchall()]

    def _attempts_in(self, states):
        marks = ",".join("?" for _ in states)
        return [dict(r) for r in self.ro.execute(
            "SELECT a.*, c.role, c.provider, c.call_id,"
            " c.projection_address FROM attempts a LEFT JOIN attempt_calls"
            " c ON c.attempt_id = a.attempt_id WHERE a.state IN (%s)"
            " ORDER BY a.attempt_id" % marks, tuple(states)).fetchall()]

    def _record_payload(self, record_hash):
        row = self.ro.execute(
            "SELECT content_address FROM records WHERE record_hash = ?",
            (record_hash,)).fetchone()
        return json.loads(self.store.get(
            row["content_address"]).decode("utf-8"))

    def _attempt_count(self, sequence_id):
        return self.ro.execute(
            "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
            (sequence_id,)).fetchone()["n"]

    def _permit_available(self, sequence_id):
        row = self.ro.execute(
            "SELECT permitted_uses, consumed_uses, reserved_uses"
            " FROM permit_state WHERE sequence_id = ?",
            (sequence_id,)).fetchone()
        return row is not None and \
            row["consumed_uses"] + row["reserved_uses"] < row["permitted_uses"]

    def _originating_proposal(self, sequence_id):
        row = self.plan.conn.execute(
            "SELECT * FROM proposals WHERE sequence_id = ? AND status ="
            " 'ADMITTED' ORDER BY created_utc LIMIT 1",
            (sequence_id,)).fetchone()
        return dict(row) if row else None

    def _provenance_findings(self, proposal_body):
        ids = proposal_body.get("provenance", {}).get("findings", [])
        out = []
        for finding_id in ids:
            row = self.plan.conn.execute(
                "SELECT * FROM findings WHERE finding_id = ?",
                (finding_id,)).fetchone()
            if row is not None:
                out.append({
                    "finding_id": row["finding_id"],
                    "finding_class": row["finding_class"],
                    "affected_component": row["affected_component"],
                    "significance": row["significance"],
                    "suggested_follow_up": row["suggested_follow_up"] or "",
                    "evidence_refs": _loads(row["evidence_refs"], []),
                })
        return out

    # -------------------------------------------------------- allocation

    def _allocate(self, seq_row, role, capability, guidance, findings,
                  authorization, now):
        # The Taskforce asks for WHAT it needs (capability, role) and
        # never names a provider or model: WHICH seat serves the request
        # is the Agent Factory's cap-14 decision (forbidden to the
        # task_force), consumed below by the dispatch (t-a02).
        assignment = self.factory.select(capability, role=role or None)
        record = self._record_payload(seq_row["record_hash"])
        info = self.sequencer.start_execution(seq_row["sequence_id"])
        self.sequencer.reserve_attempt(info["attempt_id"],
                                       expected_fence=info["fence"])
        identity = {"sequence_id": seq_row["sequence_id"],
                    "attempt_id": info["attempt_id"],
                    "fence": info["fence"],
                    "attempt_contract_hash": info["attempt_contract_hash"]}
        context = {"author": "planner", "guidance": guidance,
                   "expected_outputs": assignment["expected_output"]}
        if findings:
            context["predecessor_findings"] = findings
        if self.repository_context:
            context["repository_context"] = self.repository_context
        _data, projection = project_prompt(
            self.store, record, assignment["role"], identity, context)
        dispatched = self.sequencer.dispatch_attempt(
            info["attempt_id"], assignment["role"], assignment["provider"],
            projection)
        call_id = dispatched["call_id"]
        self.transport.launch(projection, assignment["provider"],
                              assignment["role"], call_id)
        seat_id = (authorization or {}).get("seat_id") or \
            "st_" + canonical_json_digest({"call": call_id})[:32]
        with self.plan.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO seats (seat_id, sequence_id, role,"
                " capability, provider, status, created_utc, updated_utc)"
                " VALUES (?, ?, ?, ?, ?, 'AUTHORIZED', ?, ?)",
                (seat_id, seq_row["sequence_id"], assignment["role"],
                 capability, assignment["provider"], now, now))
            cur.execute(
                "UPDATE seats SET attempt_id = ?, call_id = ?, role = ?,"
                " provider = ?, model = ?, selected_by = ?,"
                " projection_address = ?, status = 'DISPATCHED',"
                " updated_utc = ? WHERE seat_id = ?",
                (info["attempt_id"], call_id, assignment["role"],
                 assignment["provider"], assignment["model"],
                 assignment["selected_by"], projection, now, seat_id))
        return {"sequence_id": seq_row["sequence_id"],
                "attempt_id": info["attempt_id"], "call_id": call_id,
                "seat_id": seat_id, "role": assignment["role"],
                "provider": assignment["provider"]}

    def allocate_ready(self, now):
        """Breadth-first: every READY sequence allocatable under the
        Chief Engineer gate gets its seat BEFORE any later stage runs, so
        independent contracts run concurrently at the provider."""
        allocated = []
        for seq_row in self._sequences_in(["READY"]):
            if seq_row["current_attempt_id"] is not None:
                continue
            first = self._attempt_count(seq_row["sequence_id"]) == 0
            authorization = self.plan.open_authorization(
                seq_row["sequence_id"])
            if not first and authorization is None:
                continue  # re-execution waits for the planner's word
            if authorization is not None:
                auth_proposal = self.plan.proposal(
                    authorization["authorized_by_proposal"]) or {}
                body = _loads(auth_proposal.get("body"), {})
                guidance = body.get("guidance", "") or body.get(
                    "requirement", {}).get("guidance", "")
                capability = authorization["capability"]
                role = authorization["role"] or None
                findings = self._provenance_findings(body)
            else:
                origin = self._originating_proposal(seq_row["sequence_id"])
                if origin is None:
                    continue  # no admitted proposal: not this plane's work
                body = _loads(origin.get("body"), {})
                requirement = body.get("requirement", {})
                guidance = requirement.get("guidance", "")
                capability = requirement.get("capability")
                roles = requirement.get("roles", {})
                required = sorted(
                    [r for r in roles if roles[r].get("required")]) or \
                    sorted(roles)
                role = required[0] if required else None
                findings = self._provenance_findings(body)
            if not capability or not guidance:
                continue
            allocated.append(self._allocate(
                seq_row, role, capability, guidance, findings,
                authorization, now))
        return allocated

    # -------------------------------------------------------- collection

    def _witness_address_for_finish(self, admitted):
        for kind in ("obj-13", "obj-11", "obj-12", "obj-14"):
            if kind in admitted:
                return admitted[kind]
        return None

    def collect_running(self, now):
        """For each RUNNING attempt: collect producer originals, admit
        witnesses, admit findings, finish per the dispatched role, freeze
        the candidate and complete evidence (t-a04..t-a06)."""
        collected = []
        for att in self._attempts_in(["RUNNING"]):
            result = self.transport.collect(att["call_id"])
            if result is None:
                continue  # still working
            admitted = {}
            for kind, producer, data in result.get("witness_files", []):
                entry = self.sequencer.admit_witness(
                    att["attempt_id"], kind, producer, data)
                admitted[kind] = entry["content_address"]
            for finding in result.get("findings", []):
                merged = dict(finding)
                merged.setdefault("attempt_id", att["attempt_id"])
                merged.setdefault("sequence_id", att["sequence_id"])
                merged.setdefault("call_id", att["call_id"])
                merged.setdefault("role", att["role"])
                admit_finding(self.plan, merged, now)
            finish_witness = self._witness_address_for_finish(admitted)
            outcome = result.get("outcome", "success")
            state = self.sequencer.finish_attempt(
                att["attempt_id"], att["role"], outcome, finish_witness)
            entry = {"attempt_id": att["attempt_id"],
                     "sequence_id": att["sequence_id"],
                     "outcome": outcome, "state": state}
            if state == "FINISHED":
                artifact = result.get("artifact_bytes",
                                      b"no artifact collected\n")
                candidate = self.store.put(artifact)
                seq = self.ro.execute(
                    "SELECT fence FROM sequences WHERE sequence_id = ?",
                    (att["sequence_id"],)).fetchone()
                self.sequencer.freeze_candidate(
                    att["attempt_id"], seq["fence"], candidate)
                with self.plan.transaction() as cur:
                    cur.execute(
                        "UPDATE seats SET artifact_address = ?, status ="
                        " 'COLLECTED', updated_utc = ? WHERE call_id = ?",
                        (candidate, now, att["call_id"]))
                entry["candidate_address"] = candidate
            collected.append(entry)
        collected += self.settle_frozen_evidence(now)
        return collected

    def settle_frozen_evidence(self, now):
        """The C3-M1 evidence pass, for EVERY attempt in CANDIDATE_FROZEN —
        frozen this sweep or inherited across a restart: ask the registered
        Collector (cap-30) to derive-and-admit the obj-15 classification,
        then act on the ADMITTED facts alone. COMPLETE completes the
        evidence (t-a06/t-s09); INTEGRITY_FAILED, or INCOMPLETE whose
        derived bound has elapsed, fails the candidate (t-a13/t-s45);
        INCOMPLETE inside its window stays frozen for a later sweep —
        unchanged facts re-admit idempotently and report nothing, so a
        waiting attempt is quiet, not busy. The Taskforce never sets,
        forwards or interprets a classification of its own (F-CE-4
        closed)."""
        settled = []
        for att in self._attempts_in(["CANDIDATE_FROZEN"]):
            seq = self.ro.execute(
                "SELECT state, fence FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            if seq["state"] != "CANDIDATE_FROZEN":
                continue  # the sequence arc has already moved (e.g. HALTED)
            outcome = self.collector.collect_and_admit(att["attempt_id"])
            entry = {"attempt_id": att["attempt_id"],
                     "sequence_id": att["sequence_id"],
                     "evidence": outcome["completeness"]}
            if outcome["completeness"] == "COMPLETE":
                self.sequencer.complete_evidence(
                    att["attempt_id"], seq["fence"],
                    outcome["receipt_address"])
                settled.append(entry)
            elif (outcome["completeness"] == "INTEGRITY_FAILED"
                    or outcome["evidence_complete_by_utc"] <= now):
                self.sequencer.fail_candidate(
                    att["attempt_id"], seq["fence"],
                    outcome["receipt_address"],
                    reason="collector classification %s"
                           % outcome["completeness"])
                settled.append(entry)
            elif not outcome["duplicate"]:
                settled.append(entry)  # a NEW classification is news
        return settled

    # ------------------------------------------------------ verification

    def verify_evidence_complete(self):
        verified = []
        for att in self._attempts_in(["EVIDENCE_COMPLETE"]):
            seq = self.ro.execute(
                "SELECT * FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            record = self._record_payload(seq["record_hash"])
            assertions = record.get("acceptance_assertions", {}).get(
                "ids_and_environment_constraints", [])
            if any(a.get("mode") == "HUMAN_ATTESTED" for a in assertions):
                continue  # the attestation path is yours (cap-44)
            identity = {"sequence_id": att["sequence_id"],
                        "attempt_id": att["attempt_id"],
                        "version": att["version"], "fence": att["fence"]}
            inconclusive = False
            for assertion in assertions:
                existing = self.ro.execute(
                    "SELECT outcome FROM attempt_verifications WHERE"
                    " attempt_id = ? AND assertion_id = ?",
                    (att["attempt_id"], assertion["assertion_id"])
                ).fetchone()
                if existing is not None:
                    continue
                environment = None
                if "environment_constraint" in assertion:
                    environment = self.runner.capture_environment(identity)
                outcome, receipt = self.runner.run_assertion(
                    assertion, None,
                    {"attempt_id": att["attempt_id"],
                     "sequence_id": att["sequence_id"],
                     "identity": identity})
                self.sequencer.register_verifier_result(
                    att["attempt_id"], assertion["assertion_id"], outcome,
                    "verifier_runner", receipt, environment=environment)
                if outcome == "INCONCLUSIVE":
                    inconclusive = True
            if inconclusive:
                self.sequencer.halt_verification(
                    att["sequence_id"], "unknown")
                verified.append({"attempt_id": att["attempt_id"],
                                 "result": "HALTED"})
                continue
            result = self.sequencer.conclude_verification(
                att["attempt_id"], att["fence"])
            verified.append({"attempt_id": att["attempt_id"],
                             "result": result})
        return verified

    # ------------------------------------------- finalization/acceptance

    def advance_authority_arcs(self):
        """The deterministic registered arcs past VERIFIED: finalization
        (SL, inside your permit grant), acceptance, release, completion,
        and readiness re-evaluation. Each is a registered transition the
        Sequencer alone applies; a missing permit or attestation leaves
        the sequence visibly waiting for YOUR registered authority."""
        report = {"finalized": [], "accepted": [], "released": [],
                  "completed": [], "ready": [], "blocked": []}
        for row in self._sequences_in(["DECLARED"]):
            state = self.sequencer.mark_ready(row["sequence_id"])
            report["ready" if state == "READY" else "blocked"].append(
                row["sequence_id"])
        for row in self._sequences_in(["BLOCKED"]):
            result = self.sequencer.reevaluate_blocked(row["sequence_id"])
            if result["state"] == "READY":
                report["ready"].append(row["sequence_id"])
        for row in self._sequences_in(["VERIFIED"]):
            record = self._record_payload(row["record_hash"])
            # Latest by INSERTION order (rowid), never by minted id:
            # after a t-s18 stale-base arc two VERIFIED attempts can
            # exist, and only the newest carries the current fence
            # (F-CE-5; the F-M5-1 lesson again).
            att = self.ro.execute(
                "SELECT attempt_id, fence FROM attempts WHERE sequence_id"
                " = ? AND state = 'VERIFIED' ORDER BY rowid DESC"
                " LIMIT 1", (row["sequence_id"],)).fetchone()
            if record["finalization_class"] == "READ_ONLY":
                self.sequencer.accept_verified(row["sequence_id"],
                                               att["fence"])
                report["accepted"].append(row["sequence_id"])
                continue
            if not self._permit_available(row["sequence_id"]):
                continue  # cap-36: publication permits are yours
            if self.repo_path is None:
                continue
            seat = self.plan.conn.execute(
                "SELECT artifact_address FROM seats WHERE attempt_id = ?",
                (att["attempt_id"],)).fetchone()
            if seat is None or not seat["artifact_address"]:
                continue
            effect = _loads(self.store.get(
                seat["artifact_address"]).decode("utf-8"), {})
            needed = ("target_ref", "expected_old", "new_value")
            if not all(isinstance(effect.get(k), str) for k in needed):
                continue  # the specialist proposed no executable effect
            minted = self.sequencer.begin_commit(
                row["sequence_id"], att["attempt_id"],
                effect["target_ref"], effect["expected_old"],
                effect["new_value"])
            outcome = execute_local_cas(self.sequencer,
                                        minted["intent_id"], self.repo_path)
            if outcome["outcome"] == "HAPPENED":
                self.sequencer.commit_committed(
                    row["sequence_id"], att["fence"], outcome["receipt"])
                report["finalized"].append(row["sequence_id"])
            # ABSENT arms (t-s18/t-s19) stay with the M5 surface; the
            # intent outcome is durable and the planner sees it.
        for row in self._sequences_in(["COMMITTED"]):
            self.sequencer.accept_commit(row["sequence_id"], row["fence"])
            report["accepted"].append(row["sequence_id"])
        for row in self._sequences_in(["ACCEPTED"]):
            self.sequencer.release_dependencies(row["sequence_id"])
            report["released"].append(row["sequence_id"])
        for row in self._sequences_in(["DEPENDENCIES_RELEASED"]):
            self.sequencer.complete_sequence(row["sequence_id"])
            report["completed"].append(row["sequence_id"])
        return report

    # --------------------------------------------------------- the sweep

    def advance(self, now):
        """One breadth-first Taskforce sweep over authority state."""
        adopted = reconcile_dispatched(self.sequencer, self.transport)
        allocated = self.allocate_ready(now)
        # Newly allocated seats are DISPATCHED: adopt their sessions now
        # (same registered path a restart would take).
        adopted += reconcile_dispatched(self.sequencer, self.transport)
        collected = self.collect_running(now)
        verified = self.verify_evidence_complete()
        arcs = self.advance_authority_arcs()
        return {"allocated": allocated,
                "adopted": [a for a in adopted if a["action"] == "adopted"],
                "collected": collected, "verified": verified, "arcs": arcs}
