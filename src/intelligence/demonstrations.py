"""Capability demonstrations: what a specialist verifiably did.

A demonstration is the record that one specialist — a retained member or
an outside specialist on a project — carried one project assignment to a
conclusion the AUTHORITY PLANE reached. It is never the Engineer's
opinion and never the specialist's own claim: every criterion below is
read from the ledger through the read-only connection (attempt and
sequence state, admitted verification rows, the admitted ExecutionReceipt
classification, the admitted candidate) and the record REFERENCES those
objects by their existing identities and content addresses. It duplicates
none of them.

A demonstration belongs to the specialist and by itself changes nothing:
it creates no membership and no seat. It is the EVIDENCE an explicit
retention decision must bind.

The original Taskforce membership principle — a specialist may earn a
retained seat after demonstrating the capacity to exert coherent action
within the relevant environment, on real systems and complex engineering
activities — is translated here into evidence-driven criteria:

    task_completed        the sequence was accepted and released on THIS
                          attempt
    candidate_accepted    the attempt's admitted candidate is what was
                          accepted
    verification_passed   every admitted verification row for the attempt
                          is PASS (or your ATTESTED), and the attempt is
                          VERIFIED
    scope_respected       SOURCE_MODIFYING: the candidate tree was captured
                          after the frozen mutation-scope check; READ_ONLY:
                          a candidate exists and the assignment granted no
                          write outside command-governance files
    governance_satisfied  the admitted ExecutionReceipt is COMPLETE (the
                          Collector's classification, including command
                          governance when the contract requires it)
    repair_coherent       the attempt that verified is this one — a repair
                          that verified is coherent repair behavior; a
                          rejected attempt is not
    outcome_useful        the accepted work was created for an objective

All seven hold -> QUALIFYING. Anything else -> NOT_QUALIFYING, recorded
with the criteria that failed. Only a retention decision that binds
QUALIFYING demonstrations can create Taskforce membership, and repeated
qualifying work in a retained seat is what establishes it: the Taskforce
does not certify its own specialists, and neither does anyone else.

``settle`` is also where assignments are reconciled FROM ledger facts
(never the other way around): status follows the attempt's durable state,
and an assignment whose attempt identity was never bound (a crash between
attempt creation and the bind) recovers it from the ledger by ordinal.
"""

import json

from .proposals_util import canonical_json_digest
from .workforce import OPEN_STATES, OUTSIDE

# The accepted-and-released sequence states a completed task shows.
RELEASED_STATES = ("DEPENDENCIES_RELEASED", "COMPLETED")
# Terminal sequence states in which a verified attempt was never accepted.
DEAD_SEQUENCE_STATES = ("SUPERSEDED", "ABANDONED", "DELEGATION_REVOKED",
                        "PUBLISHED_UNVERIFIED")

CRITERIA = ("task_completed", "candidate_accepted", "verification_passed",
            "scope_respected", "governance_satisfied", "repair_coherent",
            "outcome_useful")

_STATUS_FOR_ATTEMPT = {
    "DECLARED": "SELECTED",
    "RESERVED": "SELECTED",
    "DISPATCHED": "DISPATCHED",
    "RUNNING": "EXECUTING",
    "FINISHED": "COLLECTED",
    "CANDIDATE_FROZEN": "COLLECTED",
    "EVIDENCE_COMPLETE": "COLLECTED",
}


def demonstration_identity(assignment_id):
    return "dem:" + canonical_json_digest({"assignment": assignment_id})


def _loads(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


class DemonstrationRecorder:
    def __init__(self, plan, roster, ro_conn, store):
        self.plan = plan
        self.roster = roster
        self.ro = ro_conn
        self.store = store

    # ------------------------------------------------------ ledger reads

    def _one(self, sql, args):
        row = self.ro.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql, args):
        return [dict(r) for r in self.ro.execute(sql, args).fetchall()]

    def _attempt_at_ordinal(self, sequence_id, ordinal):
        rows = self._all(
            "SELECT attempt_id FROM attempts WHERE sequence_id = ?"
            " ORDER BY rowid", (sequence_id,))
        if len(rows) < ordinal:
            return None
        return rows[ordinal - 1]["attempt_id"]

    def _record(self, record_hash):
        row = self._one(
            "SELECT content_address, task_contract_id FROM records"
            " WHERE record_hash = ?", (record_hash,))
        payload = json.loads(
            self.store.get(row["content_address"]).decode("utf-8"))
        return payload

    def _accepted_attempt(self, sequence_id):
        row = self._one(
            "SELECT attempt_id FROM attempts WHERE sequence_id = ? AND"
            " state = 'VERIFIED' ORDER BY rowid DESC LIMIT 1",
            (sequence_id,))
        return row["attempt_id"] if row else None

    def _newer_attempt_exists(self, sequence_id, attempt_id):
        rows = self._all(
            "SELECT attempt_id FROM attempts WHERE sequence_id = ?"
            " ORDER BY rowid", (sequence_id,))
        ids = [r["attempt_id"] for r in rows]
        return attempt_id in ids and ids.index(attempt_id) < len(ids) - 1

    def _originating_proposal(self, sequence_id):
        row = self.plan.conn.execute(
            "SELECT * FROM proposals WHERE sequence_id = ? AND status ="
            " 'ADMITTED' ORDER BY created_utc LIMIT 1",
            (sequence_id,)).fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------- evidence

    def evidence(self, assignment, attempt, sequence):
        """Everything one demonstration rests on, read from the ledger."""
        attempt_id = attempt["attempt_id"]
        record = self._record(sequence["record_hash"])
        verifications = self._all(
            "SELECT assertion_id, outcome, receipt_sha256 FROM"
            " attempt_verifications WHERE attempt_id = ?"
            " ORDER BY assertion_id", (attempt_id,))
        receipt = self._one(
            "SELECT content_sha256, completeness FROM attempt_receipts"
            " WHERE attempt_id = ? ORDER BY rowid DESC LIMIT 1",
            (attempt_id,))
        candidate = self._one(
            "SELECT content_sha256, candidate_tree, finalization_class"
            " FROM attempt_candidates WHERE attempt_id = ?", (attempt_id,))
        released = self._one(
            "SELECT acceptance_sha256, release_sha256 FROM released_edges"
            " WHERE predecessor_sequence_id = ? ORDER BY release_id"
            " LIMIT 1", (sequence["sequence_id"],))
        origin = self._originating_proposal(sequence["sequence_id"])
        body = _loads(origin["body"], {}) if origin else {}
        requirement = body.get("requirement", {})
        approvals = [dict(r) for r in self.plan.conn.execute(
            "SELECT decision, tool_name FROM assignment_approvals"
            " WHERE assignment_id = ?",
            (assignment["assignment_id"],)).fetchall()]
        findings = [r["finding_id"] for r in self.plan.conn.execute(
            "SELECT finding_id FROM findings WHERE attempt_id = ?"
            " ORDER BY admitted_utc, finding_id", (attempt_id,)).fetchall()]

        fin = record["finalization_class"]
        accepted = (sequence["state"] in RELEASED_STATES
                    and self._accepted_attempt(sequence["sequence_id"])
                    == attempt_id)
        verification_passed = (
            attempt["state"] == "VERIFIED" and bool(verifications)
            and all(v["outcome"] in ("PASS", "ATTESTED")
                    for v in verifications))
        granted_writes = [a for a in approvals if a["decision"] == "allow"
                          and a["tool_name"] in ("Write", "Edit",
                                                 "MultiEdit",
                                                 "NotebookEdit")]
        if fin == "SOURCE_MODIFYING":
            scope_respected = bool(candidate
                                   and candidate["candidate_tree"])
        else:
            # READ_ONLY: writes are granted only for command-governance
            # (gate) files, which the supervision policy records as such.
            scope_respected = candidate is not None
        criteria = {
            "task_completed": accepted,
            "candidate_accepted": bool(accepted and candidate),
            "verification_passed": verification_passed,
            "scope_respected": scope_respected,
            "governance_satisfied": bool(
                receipt and receipt["completeness"] == "COMPLETE"),
            "repair_coherent": verification_passed,
            "outcome_useful": bool(accepted and origin
                                   and origin.get("objective_id")),
        }
        observations = {
            "attempt_ordinal": assignment["attempt_ordinal"],
            "repairs_used": sequence.get("repairs_used", 0),
            "requests_allowed": len(
                [a for a in approvals if a["decision"] == "allow"]),
            "requests_denied": len(
                [a for a in approvals if a["decision"] == "deny"]),
            "writes_granted": len(granted_writes),
        }
        domains = set()
        subsystem = requirement.get("subsystem")
        if isinstance(subsystem, str) and subsystem:
            domains.add(subsystem)
        if fin == "SOURCE_MODIFYING":
            domains.update(record.get("allowed_scope", {}).get("paths", []))
        evidence_refs = {}
        if receipt:
            evidence_refs["execution_receipt"] = receipt["content_sha256"]
        if candidate:
            evidence_refs["candidate_receipt"] = candidate["content_sha256"]
            if candidate["candidate_tree"]:
                evidence_refs["candidate_tree"] = candidate["candidate_tree"]
        if released:
            evidence_refs["acceptance"] = released["acceptance_sha256"]
            evidence_refs["release"] = released["release_sha256"]
        return {
            "record": record,
            "environment": "%s/%s" % (
                record["source_domain"]["repository_id"], fin),
            "system_domains": sorted(domains),
            "verification_refs": verifications,
            "evidence_refs": evidence_refs,
            "criteria": criteria,
            "observations": observations,
            "findings": findings,
        }

    # ------------------------------------------------------------ settle

    def _record_demonstration(self, cur, assignment, attempt, sequence,
                              facts, outcome_label, now):
        criteria = dict(facts["criteria"])
        qualifying = all(criteria[name] for name in CRITERIA)
        demonstration_id = demonstration_identity(
            assignment["assignment_id"])
        stored = dict(criteria)
        stored["observations"] = facts["observations"]
        stored["settled_as"] = outcome_label
        standing = assignment["standing_at_assignment"]
        cur.execute(
            "INSERT OR IGNORE INTO capability_demonstrations"
            " (demonstration_id, specialist_id, seat_id, assignment_id,"
            " project_id, standing, capability, role, environment,"
            " system_domains, sequence_id, attempt_id, call_id,"
            " task_contract_id, record_hash, contract_digest,"
            " verification_refs, evidence_refs, criteria, outcome,"
            " observed_findings, recorded_utc) VALUES (?, ?, ?, ?, ?, ?,"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (demonstration_id, assignment["specialist_id"],
             assignment["seat_id"], assignment["assignment_id"],
             assignment["project_id"], standing,
             assignment["capability"], assignment["role"],
             facts["environment"], json.dumps(facts["system_domains"]),
             sequence["sequence_id"], attempt["attempt_id"],
             assignment["call_id"], facts["record"]["task_contract_id"],
             sequence["record_hash"], sequence["contract_digest"],
             json.dumps(facts["verification_refs"], sort_keys=True),
             json.dumps(facts["evidence_refs"], sort_keys=True),
             json.dumps(stored, sort_keys=True),
             "QUALIFYING" if qualifying else "NOT_QUALIFYING",
             json.dumps(facts["findings"]), now))
        created = cur.rowcount == 1
        if created:
            # The demonstration is a fact about the SPECIALIST. Recording
            # it changes no membership and creates no seat: an outside
            # specialist with qualifying work is exactly as outside as
            # before, until a retention decision says otherwise.
            self.plan.emit_workforce_event(
                cur, "demonstration:" + demonstration_id,
                "demonstration_recorded", demonstration_id,
                {"specialist_id": assignment["specialist_id"],
                 "seat_id": assignment["seat_id"],
                 "project_id": assignment["project_id"],
                 "capability": assignment["capability"],
                 "role": assignment["role"],
                 "sequence_id": sequence["sequence_id"],
                 "outcome": "QUALIFYING" if qualifying
                 else "NOT_QUALIFYING",
                 "standing": standing,
                 "retention_candidate": bool(
                     qualifying and standing == OUTSIDE)}, now)
        if qualifying and assignment["seat_id"]:
            # Repeated verified work in a seat already RETAINED is what
            # establishes it (the Taskforce's own evidence-bound rule).
            seat = cur.execute(
                "SELECT * FROM taskforce_seats WHERE seat_id = ?",
                (assignment["seat_id"],)).fetchone()
            if seat is not None:
                self.roster._maybe_establish(cur, dict(seat), now)
        return demonstration_id, qualifying

    def _settle_one(self, assignment, now):
        attempt_id = assignment["attempt_id"]
        if attempt_id is None:
            attempt_id = self._attempt_at_ordinal(
                assignment["sequence_id"], assignment["attempt_ordinal"])
            if attempt_id is None:
                return None  # staffed; the attempt does not exist yet
        call = self._one(
            "SELECT call_id, native_session_id FROM attempt_calls"
            " WHERE attempt_id = ?", (attempt_id,))
        if assignment["attempt_id"] is None or (
                assignment["call_id"] is None and call is not None):
            # Bookkeeping reconciled FROM the ledger: an identity the
            # authority plane minted but this store never recorded.
            self.roster.bind_execution(
                assignment["assignment_id"], attempt_id,
                call["call_id"] if call else None, now)
            assignment = self.roster.assignment(assignment["assignment_id"])
        attempt = self._one(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,))
        sequence = self._one(
            "SELECT * FROM sequences WHERE sequence_id = ?",
            (assignment["sequence_id"],))
        if attempt is None or sequence is None:
            return None
        state = attempt["state"]
        if state in _STATUS_FOR_ATTEMPT:
            target = _STATUS_FOR_ATTEMPT[state]
            if target != assignment["status"]:
                with self.plan.transaction() as cur:
                    self.roster.set_assignment_status(
                        cur, assignment["assignment_id"], target, now)
            return None
        label = None
        demonstrate = True
        if state == "VERIFIED":
            if (sequence["state"] in RELEASED_STATES
                    and self._accepted_attempt(sequence["sequence_id"])
                    == attempt_id):
                label = "VERIFIED_ACCEPTED"
            elif (sequence["state"] in DEAD_SEQUENCE_STATES
                    or self._newer_attempt_exists(
                        sequence["sequence_id"], attempt_id)):
                label = "VERIFIED_NOT_ACCEPTED"
            else:
                # Verified, awaiting finalization/acceptance (possibly
                # your permit): the specialist is free, the outcome is
                # not yet known.
                if assignment["status"] != "COLLECTED":
                    with self.plan.transaction() as cur:
                        self.roster.set_assignment_status(
                            cur, assignment["assignment_id"], "COLLECTED",
                            now)
                return None
        elif state == "REJECTED":
            label = "VERIFICATION_FAILED"
        elif state in ("FAILED", "STALE"):
            if call is not None and call["native_session_id"]:
                label = "ATTEMPT_" + state
            else:
                # The session never came into existence: a launch
                # failure is not a performance to demonstrate.
                label, demonstrate = "LAUNCH_FAILED", False
        else:  # FENCED, RECONCILED: authority arcs, not performance
            label, demonstrate = "ATTEMPT_" + state, False
        entry = {"assignment_id": assignment["assignment_id"],
                 "sequence_id": assignment["sequence_id"],
                 "attempt_id": attempt_id, "outcome": label}
        facts = self.evidence(assignment, attempt, sequence) \
            if demonstrate else None
        with self.plan.transaction() as cur:
            if demonstrate:
                demonstration_id, qualifying = self._record_demonstration(
                    cur, assignment, attempt, sequence, facts, label, now)
                entry["demonstration_id"] = demonstration_id
                entry["qualifying"] = qualifying
            self.roster.set_assignment_status(
                cur, assignment["assignment_id"], "SETTLED", now,
                outcome=label)
        return entry

    def settle(self, now):
        """Reconcile every open assignment against the ledger; record a
        demonstration for each one whose attempt has concluded. Returns
        the settlements (an empty list is a quiet sweep)."""
        settled = []
        for assignment in self.roster.assignments(statuses=OPEN_STATES):
            entry = self._settle_one(assignment, now)
            if entry is not None:
                settled.append(entry)
        return settled
