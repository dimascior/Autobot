"""The Sequencer authority service (actor ``sequencer``).

Owns the InstanceLease (INV-25a), the sequence/attempt transitions with
version+fence compare-and-swap (D-68, INV-12, INV-13), the standing-permit
arithmetic and reservation leases (INV-42, INV-48, INV-60), and restart
recovery (D-69, INV-10). Versions and fences — never wall-clock time —
determine authority and order (D-69); the injected clock decides expiry only.

Every mutating method is one transaction that first proves the instance
lease is still held at this process's epoch, then compare-and-swaps the
exact expected durable state, mints and indexes any required objects, and
appends the hash-chained TransitionRecord (D-68).
"""

import json
import sqlite3
from datetime import timedelta

from contract_library import canonicalize, compute_tiers, object_hash

from . import journal
from . import receipts
from . import witness_semantics
from .effects import EFFECTS, OUTCOME_RULES, RETRYABLE
from .verification import (
    ATTESTATION_PRODUCER,
    RESULT_PRODUCER,
    VERIFIER_OUTCOMES,
    environment_satisfies,
    validate_assertion_attestation,
    validate_environment_capture,
    verifier_eligibility,
)
from .witnesses import WITNESS_KINDS
from .models import (
    ATTEMPT_TERMINAL,
    INT_MAX,
    SEQUENCE_TERMINAL,
    AdmissionError,
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    LeaseUnavailable,
    PermitExhaustedError,
    StaleFenceError,
    instant,
    mint_id,
)

# Attempt states that exist only after a bound execution (t-a02 onward):
# each must carry its attempt_calls row, and vice versa.
EXECUTION_STATES = frozenset((
    "DISPATCHED", "RUNNING", "FINISHED", "CANDIDATE_FROZEN",
    "EVIDENCE_COMPLETE",
))


class Sequencer:
    def __init__(self, db, store, clock, holder):
        self.db = db
        self.store = store
        self.clock = clock
        self.holder = holder
        self.epoch = None

    # ------------------------------------------------------ instance lease

    def acquire_lease(self, ttl_seconds=60):
        """INV-25a: one InstanceLease holder at a time. The same holder may
        retake its lease after a crash; a different holder may take over only
        once the lease has expired. Every (re)acquisition bumps the epoch,
        fencing out any writer that still believes it holds an older one."""
        now_dt = self.clock.now()
        now = instant(now_dt)
        expires = instant(now_dt + timedelta(seconds=ttl_seconds))
        with self.db.transaction() as cur:
            row = cur.execute(
                "SELECT holder, epoch, expires_utc FROM instance_lease"
                " WHERE lease_key = 'sequencer'"
            ).fetchone()
            if row is None:
                epoch = 1
                cur.execute(
                    "INSERT INTO instance_lease (lease_key, holder, epoch, acquired_utc, expires_utc)"
                    " VALUES ('sequencer', ?, ?, ?, ?)",
                    (self.holder, epoch, now, expires),
                )
            elif row["holder"] == self.holder or row["expires_utc"] <= now:
                epoch = row["epoch"] + 1
                if epoch > INT_MAX:
                    raise IntegrityHalt("lease epoch would leave the D-24 domain")
                cur.execute(
                    "UPDATE instance_lease SET holder = ?, epoch = ?, acquired_utc = ?,"
                    " expires_utc = ? WHERE lease_key = 'sequencer'",
                    (self.holder, epoch, now, expires),
                )
            else:
                raise LeaseUnavailable(
                    "instance lease held by %r until %s (INV-25a)"
                    % (row["holder"], row["expires_utc"])
                )
            journal.append(cur, journal.transition_record(
                machine="instance_lease", subject_id="sequencer",
                rule="ch2:instance-lease", to_state="HELD", recorded_utc=now,
                facts={"holder": self.holder, "epoch": epoch,
                       "expires_utc": expires},
            ))
        self.epoch = epoch
        return epoch

    def renew_lease(self, ttl_seconds=60):
        now_dt = self.clock.now()
        now = instant(now_dt)
        expires = instant(now_dt + timedelta(seconds=ttl_seconds))
        with self.db.transaction() as cur:
            cur.execute(
                "UPDATE instance_lease SET expires_utc = ? WHERE lease_key = 'sequencer'"
                " AND holder = ? AND epoch = ? AND expires_utc > ?",
                (expires, self.holder, self.epoch, now),
            )
            if cur.rowcount != 1:
                raise AuthorityLost("lease not held at epoch %r" % self.epoch)
            journal.append(cur, journal.transition_record(
                machine="instance_lease", subject_id="sequencer",
                rule="ch2:lease-renew", to_state="HELD", recorded_utc=now,
                facts={"holder": self.holder, "epoch": self.epoch,
                       "expires_utc": expires},
            ))
        return expires

    def _assert_lease(self, cur):
        if self.epoch is None:
            raise AuthorityLost("no instance lease acquired")
        row = cur.execute(
            "SELECT holder, epoch, expires_utc FROM instance_lease"
            " WHERE lease_key = 'sequencer'"
        ).fetchone()
        now = instant(self.clock.now())
        if (row is None or row["holder"] != self.holder
                or row["epoch"] != self.epoch or row["expires_utc"] <= now):
            raise AuthorityLost(
                "instance lease lost (holder %r epoch %r)" % (self.holder, self.epoch)
            )

    # ------------------------------------------------------------ records

    def _verified_record(self, cur, record_hash):
        """INV-01/INV-11: the frozen record must exist and its exact bytes
        must still recompute all three identity tiers."""
        row = cur.execute(
            "SELECT * FROM records WHERE record_hash = ?", (record_hash,)
        ).fetchone()
        if row is None:
            raise IntegrityHalt(
                "no frozen record %s (INV-01: no execution without a frozen contract)"
                % record_hash
            )
        payload = json.loads(self.store.get(row["content_address"]).decode("utf-8"))
        rd, cd, rh = compute_tiers(payload)
        if (rd, cd, rh) != (row["requirement_digest"], row["contract_digest"],
                            row["record_hash"]):
            raise IntegrityHalt(
                "record %s does not recompute from its stored bytes (INV-11)"
                % record_hash
            )
        return payload

    # ---------------------------------------------------------- sequences

    def _sequence(self, cur, sequence_id):
        row = cur.execute(
            "SELECT * FROM sequences WHERE sequence_id = ?", (sequence_id,)
        ).fetchone()
        if row is None:
            raise ConflictError("unknown sequence %r" % sequence_id)
        return row

    def _cas_sequence(self, cur, row, now, **changes):
        """by_id_and_version compare-and-swap. The expected state, version
        AND fence are conditions of the UPDATE statement itself, never a
        Python check followed by a separate write — so even a writer that
        bypassed every in-process check could not apply a stale mutation
        (D-68, INV-13). Every mutation bumps version."""
        fields = dict(changes)
        fields["version"] = row["version"] + 1
        fields["updated_utc"] = now
        assignments = ", ".join("%s = ?" % name for name in sorted(fields))
        values = [fields[name] for name in sorted(fields)]
        cur.execute(
            "UPDATE sequences SET %s WHERE sequence_id = ? AND state = ?"
            " AND version = ? AND fence = ?" % assignments,
            values + [row["sequence_id"], row["state"], row["version"],
                      row["fence"]],
        )
        if cur.rowcount != 1:
            raise ConflictError(
                "sequence %s moved under us (expected %s v%d fence %d)"
                % (row["sequence_id"], row["state"], row["version"], row["fence"])
            )
        return fields["version"]

    def mark_ready(self, sequence_id):
        """t-s03 (or t-s01 to BLOCKED while predecessors are unsatisfied)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            row = self._sequence(cur, sequence_id)
            if row["state"] != "DECLARED":
                raise ConflictError(
                    "mark_ready expects DECLARED, found %s" % row["state"]
                )
            payload = self._verified_record(cur, row["record_hash"])
            self._assert_finalizer_supported(payload)
            facts = {"record_hash": row["record_hash"],
                     "fence": row["fence"]}
            if payload.get("predecessors"):
                # M6: edges are evaluated against DURABLE released edges
                # (all_edges_satisfied_by_declared_tier), never inferred
                # graph state.
                unsatisfied, satisfied = self._edges_satisfied(cur, payload)
                if unsatisfied:
                    version = self._cas_sequence(cur, row, now,
                                                 state="BLOCKED")
                    rule, to_state = "t-s01", "BLOCKED"
                    facts["unsatisfied_edge_ids"] = unsatisfied
                else:
                    version = self._cas_sequence(cur, row, now,
                                                 state="READY")
                    rule, to_state = "t-s03", "READY"
                    facts["satisfied_edges"] = satisfied
                    facts["freshness_reevaluated_utc"] = now
            else:
                version = self._cas_sequence(cur, row, now, state="READY")
                rule, to_state = "t-s03", "READY"
                facts["freshness_reevaluated_utc"] = now
            facts["version"] = version
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule=rule,
                from_state="DECLARED", to_state=to_state, recorded_utc=now,
                facts=facts,
            ))
            return to_state

    def start_execution(self, sequence_id):
        """t-s06 READY→EXECUTING: verify the frozen record (INV-01), the
        instance lease, the delegation and the retry budget; mint the
        AttemptContract (obj-09) capturing the frozen source state (INV-02);
        issue the next fence (INV-13); one transaction (D-68)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            row = self._sequence(cur, sequence_id)
            if row["state"] != "READY":
                raise ConflictError(
                    "start_execution expects READY, found %s" % row["state"]
                )
            payload = self._verified_record(cur, row["record_hash"])

            # effective_budget_available: retries are attempts beyond the first.
            max_retries = payload.get("retry_policy", {}).get("max_retries", 0)
            prior = cur.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
                (sequence_id,),
            ).fetchone()["n"]
            if prior >= 1 + max_retries:
                raise ConflictError(
                    "retry budget exhausted: %d attempts, max_retries %d"
                    % (prior, max_retries)
                )

            # delegation_active (t-d02 drives expiry).
            bindings = payload.get("resolved_bindings", {})
            delegation_hash = bindings.get("delegation_contract")
            if delegation_hash is not None:
                obj = cur.execute(
                    "SELECT content_address FROM objects WHERE object_hash = ?",
                    (delegation_hash,),
                ).fetchone()
                if obj is None:
                    raise IntegrityHalt(
                        "bound delegation %s not in the ledger" % delegation_hash
                    )
                delegation = json.loads(self.store.get(obj["content_address"]).decode("utf-8"))
                if delegation.get("expires_utc", "") <= now:
                    raise ConflictError(
                        "bound delegation expired (t-d02): not delegation_active"
                    )

            fence = row["fence"] + 1
            if fence > INT_MAX:
                raise IntegrityHalt("fence would leave the D-24 domain (INV-48)")

            attempt_id = mint_id("at")
            source_state = {
                "record_hash": row["record_hash"],
                "repository_id": payload["source_domain"]["repository_id"],
                "authority_location": payload["source_domain"]["authority_location"],
            }
            declaration_hash = bindings.get("repository_authority_declaration")
            if declaration_hash is not None:  # D-37: absence by omission
                source_state["repository_authority_declaration"] = declaration_hash
            if "target_ref" in payload["source_domain"]:
                source_state["target_ref"] = payload["source_domain"]["target_ref"]
            attempt_contract = {
                "schema_version": "v1",
                "kind": "AttemptContract",
                "attempt": {
                    "sequence_id": sequence_id,
                    "attempt_id": attempt_id,
                    "version": 1,
                    "fence": fence,
                },
                "record_hash": row["record_hash"],
                "requirement_digest": row["requirement_digest"],
                "contract_digest": row["contract_digest"],
                "source_state": source_state,
                "created_utc": now,
            }
            contract_hash = object_hash("AttemptContract", attempt_contract)
            address = self.store.put(canonicalize(attempt_contract))
            cur.execute(
                "INSERT OR IGNORE INTO objects (object_hash, kind, content_address, minted_utc)"
                " VALUES (?, 'AttemptContract', ?, ?)",
                (contract_hash, address, now),
            )
            try:
                cur.execute(
                    "INSERT INTO attempts (attempt_id, sequence_id, state, version,"
                    " fence, attempt_contract_hash, created_utc, updated_utc)"
                    " VALUES (?, ?, 'DECLARED', 1, ?, ?, ?, ?)",
                    (attempt_id, sequence_id, fence, contract_hash, now, now),
                )
            except sqlite3.IntegrityError as exc:  # the INV-12 partial unique index
                raise ConflictError(
                    "a current attempt already exists for %s (INV-12): %s"
                    % (sequence_id, exc)
                )
            version = self._cas_sequence(
                cur, row, now, state="EXECUTING", fence=fence,
                current_attempt_id=attempt_id,
            )
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s06",
                from_state="READY", to_state="EXECUTING", recorded_utc=now,
                facts={"sequence_id": sequence_id, "attempt_id": attempt_id,
                       "version": version, "fence": fence,
                       "attempt_contract_hash": contract_hash,
                       "mints": ["obj-09"]},
            ))
            return {"attempt_id": attempt_id, "fence": fence,
                    "attempt_contract_hash": contract_hash}

    def retry_sequence(self, sequence_id):
        """t-s08 EXECUTING→READY once the current attempt failed or went
        stale and retry budget remains."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            row = self._sequence(cur, sequence_id)
            if row["state"] != "EXECUTING":
                raise ConflictError(
                    "retry_sequence expects EXECUTING, found %s" % row["state"]
                )
            attempt = cur.execute(
                "SELECT state FROM attempts WHERE attempt_id = ?",
                (row["current_attempt_id"],),
            ).fetchone()
            if attempt is None or attempt["state"] not in ("FAILED", "STALE"):
                raise ConflictError(
                    "retry requires the current attempt FAILED or STALE"
                )
            payload = self._verified_record(cur, row["record_hash"])
            max_retries = payload.get("retry_policy", {}).get("max_retries", 0)
            prior = cur.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE sequence_id = ?",
                (sequence_id,),
            ).fetchone()["n"]
            if prior >= 1 + max_retries:
                raise ConflictError("retry budget exhausted")
            version = self._cas_sequence(
                cur, row, now, state="READY", current_attempt_id=None,
            )
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s08",
                from_state="EXECUTING", to_state="READY", recorded_utc=now,
                facts={"version": version, "fence": row["fence"]},
            ))
            return version

    # ------------------------------------------------------------ attempts

    def _attempt(self, cur, attempt_id):
        row = cur.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise ConflictError("unknown attempt %r" % attempt_id)
        return row

    def reserve_attempt(self, attempt_id, expected_fence):
        """t-a01 DECLARED→RESERVED. The presented fence must be the current
        fence of both the attempt and its Sequence; a stale fence yields no
        authority (INV-13)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            seq = self._sequence(cur, att["sequence_id"])
            if att["state"] in ATTEMPT_TERMINAL:
                raise ConflictError("attempt %s is terminal (%s)" % (attempt_id, att["state"]))
            if expected_fence != seq["fence"] or att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"])
                )
            if att["state"] != "DECLARED":
                raise ConflictError(
                    "reserve_attempt expects DECLARED, found %s" % att["state"]
                )
            # The presented fence, the row's own fence AND the owning
            # Sequence's current fence are all conditions of this one UPDATE:
            # the stale-fence refusal lives in the SQL, not in the Python
            # checks above (which only choose the error type).
            cur.execute(
                "UPDATE attempts SET state = 'RESERVED', version = version + 1,"
                " updated_utc = ? WHERE attempt_id = ? AND state = 'DECLARED'"
                " AND version = ? AND fence = ?"
                " AND fence = (SELECT fence FROM sequences"
                "              WHERE sequence_id = attempts.sequence_id)",
                (now, attempt_id, att["version"], expected_fence),
            )
            if cur.rowcount != 1:
                raise ConflictError("attempt %s moved under us" % attempt_id)
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a01",
                from_state="DECLARED", to_state="RESERVED", recorded_utc=now,
                facts={"sequence_id": att["sequence_id"],
                       "attempt_id": attempt_id,
                       "version": att["version"] + 1, "fence": att["fence"]},
            ))
            return att["fence"]

    def fail_attempt(self, attempt_id, reason):
        """t-a09 RESERVED→FAILED (launch_failed)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "RESERVED":
                raise ConflictError(
                    "fail_attempt expects RESERVED, found %s" % att["state"]
                )
            cur.execute(
                "UPDATE attempts SET state = 'FAILED', version = version + 1,"
                " updated_utc = ? WHERE attempt_id = ? AND state = 'RESERVED'"
                " AND version = ? AND fence = ?",
                (now, attempt_id, att["version"], att["fence"]),
            )
            if cur.rowcount != 1:
                raise ConflictError("attempt %s moved under us" % attempt_id)
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a09",
                from_state="RESERVED", to_state="FAILED", recorded_utc=now,
                facts={"sequence_id": att["sequence_id"],
                       "attempt_id": attempt_id,
                       "version": att["version"] + 1, "fence": att["fence"],
                       "reason": reason},
            ))

    # ------------------------------------------------------------- permits

    def grant_permit(self, sequence_id, permitted_uses):
        """obj-23: the per-Sequence standing permit counters (INV-24/INV-48).
        v1 grants directly; the obj-22 PublicationPermit object lands with
        the effect milestones."""
        if not (0 <= permitted_uses <= INT_MAX):
            raise ConflictError("permitted_uses outside the D-24 domain")
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            self._sequence(cur, sequence_id)
            cur.execute(
                "INSERT INTO permit_state (sequence_id, permitted_uses,"
                " consumed_uses, reserved_uses, version) VALUES (?, ?, 0, 0, 1)",
                (sequence_id, permitted_uses),
            )
            journal.append(cur, journal.transition_record(
                machine="standing_permit_state", subject_id=sequence_id,
                rule="ch2:grant-permit", to_state="GRANTED", recorded_utc=now,
                facts={"permitted_uses": permitted_uses},
            ))

    def reserve_use(self, sequence_id, attempt_id, lease_seconds=7200):
        """Mint one PermitUseReservation row (obj-24): counters move only
        under the INV-48 inequality, and the row carries its own renewable
        lease window (INV-60)."""
        now_dt = self.clock.now()
        now = instant(now_dt)
        lease_expires = instant(now_dt + timedelta(seconds=lease_seconds))
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            cur.execute(
                "UPDATE permit_state SET reserved_uses = reserved_uses + 1,"
                " version = version + 1 WHERE sequence_id = ?"
                " AND consumed_uses + reserved_uses < permitted_uses",
                (sequence_id,),
            )
            if cur.rowcount != 1:
                raise PermitExhaustedError(
                    "no permit use available for %s (INV-48)" % sequence_id
                )
            reservation_id = mint_id("rv")
            cur.execute(
                "INSERT INTO reservations (reservation_id, sequence_id, attempt_id,"
                " state, lease_expires_utc, created_utc, updated_utc)"
                " VALUES (?, ?, ?, 'RESERVED', ?, ?, ?)",
                (reservation_id, sequence_id, attempt_id, lease_expires, now, now),
            )
            journal.append(cur, journal.transition_record(
                machine="permit_reservation", subject_id=reservation_id,
                rule="ch2:reserve-use", to_state="RESERVED", recorded_utc=now,
                facts={"sequence_id": sequence_id, "attempt_id": attempt_id,
                       "lease_expires_utc": lease_expires},
            ))
            return reservation_id

    def consume_reservation(self, reservation_id, cause):
        """t-r01 RESERVED→CONSUMED: exactly one row moves exactly once
        (row_status_reserved_rowcount_1, INV-42)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            cur.execute(
                "UPDATE reservations SET state = 'CONSUMED', updated_utc = ?"
                " WHERE reservation_id = ? AND state = 'RESERVED'",
                (now, reservation_id),
            )
            if cur.rowcount != 1:
                raise ConflictError(
                    "reservation %s is not RESERVED (INV-42: consume moves a"
                    " row from RESERVED exactly once)" % reservation_id
                )
            row = cur.execute(
                "SELECT sequence_id FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            cur.execute(
                "UPDATE permit_state SET consumed_uses = consumed_uses + 1,"
                " reserved_uses = reserved_uses - 1, version = version + 1"
                " WHERE sequence_id = ?",
                (row["sequence_id"],),
            )
            journal.append(cur, journal.transition_record(
                machine="permit_reservation", subject_id=reservation_id,
                rule="t-r01", from_state="RESERVED", to_state="CONSUMED",
                recorded_utc=now,
                facts={"sequence_id": row["sequence_id"], "cause": cause},
            ))

    def release_reservation(self, reservation_id, cause):
        """t-r02 RESERVED→RELEASED, idempotent (rowcount 0 or 1, INV-42)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            cur.execute(
                "UPDATE reservations SET state = 'RELEASED', updated_utc = ?"
                " WHERE reservation_id = ? AND state = 'RESERVED'",
                (now, reservation_id),
            )
            if cur.rowcount == 0:
                return False
            row = cur.execute(
                "SELECT sequence_id FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            cur.execute(
                "UPDATE permit_state SET reserved_uses = reserved_uses - 1,"
                " version = version + 1 WHERE sequence_id = ?",
                (row["sequence_id"],),
            )
            journal.append(cur, journal.transition_record(
                machine="permit_reservation", subject_id=reservation_id,
                rule="t-r02", from_state="RESERVED", to_state="RELEASED",
                recorded_utc=now,
                facts={"sequence_id": row["sequence_id"], "cause": cause},
            ))
            return True

    def renew_reservation(self, reservation_id, lease_seconds=7200):
        """INV-60: every reservation's lease is its own and renewable."""
        now_dt = self.clock.now()
        now = instant(now_dt)
        lease_expires = instant(now_dt + timedelta(seconds=lease_seconds))
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            cur.execute(
                "UPDATE reservations SET lease_expires_utc = ?, updated_utc = ?"
                " WHERE reservation_id = ? AND state = 'RESERVED'",
                (lease_expires, now, reservation_id),
            )
            if cur.rowcount != 1:
                raise ConflictError(
                    "reservation %s is not RESERVED" % reservation_id
                )
            journal.append(cur, journal.transition_record(
                machine="permit_reservation", subject_id=reservation_id,
                rule="ch2:renew-reservation", to_state="RESERVED",
                recorded_utc=now,
                facts={"lease_expires_utc": lease_expires},
            ))
            return lease_expires

    def release_expired_reservations(self):
        """Lease expiry releases authority (INV-63's reservation half):
        every RESERVED row whose window has passed is released (t-r02)."""
        now = instant(self.clock.now())
        released = []
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            rows = cur.execute(
                "SELECT reservation_id, sequence_id FROM reservations"
                " WHERE state = 'RESERVED' AND lease_expires_utc <= ?",
                (now,),
            ).fetchall()
            for row in rows:
                cur.execute(
                    "UPDATE reservations SET state = 'RELEASED', updated_utc = ?"
                    " WHERE reservation_id = ? AND state = 'RESERVED'",
                    (now, row["reservation_id"]),
                )
                if cur.rowcount != 1:
                    continue
                cur.execute(
                    "UPDATE permit_state SET reserved_uses = reserved_uses - 1,"
                    " version = version + 1 WHERE sequence_id = ?",
                    (row["sequence_id"],),
                )
                journal.append(cur, journal.transition_record(
                    machine="permit_reservation",
                    subject_id=row["reservation_id"], rule="t-r02",
                    from_state="RESERVED", to_state="RELEASED",
                    recorded_utc=now,
                    facts={"sequence_id": row["sequence_id"],
                           "cause": "lease_expired"},
                ))
                released.append(row["reservation_id"])
        return released

    # ------------------------------------------------------------ recovery

    def recover(self):
        """D-69: restart reads durable state, validates the journal and every
        committed content reference, and resumes from that state — raising
        JournalBroken or IntegrityHalt instead of guessing (INV-10).

        The explicit recovery contract this enforces:
          1. the chain validates end to end (JH-1);
          2. D-68 pairing, both directions: every state row agrees with its
             machine's LAST journal entry (initial state when none exists),
             and every journaled subject has its row — a durable mutation
             without its TransitionRecord, or a TransitionRecord whose
             mutation rolled back, HALTs. (Bounded to last-state agreement;
             full deterministic replay is a later milestone.)
          3. every committed content reference — objects, records, admission
             payload preservations, duplicate-admission preservations, and
             each frozen record's specification-input addresses — resolves
             to bytes that re-hash exactly (D-66's HALT side), and identity
             tiers recompute (INV-11);
          4. permit counters equal their reservation rows (INV-42/INV-48);
          5. the instance-lease row matches its last journal entry, so a
             takeover's epoch advance is itself journaled authority;
          6. orphaned temp files and unreferenced objects are reported and
             ignored (D-66's harmless side — the Windows directory-fsync
             gap resolves to this, never to trust)."""
        cur = self.db.conn.cursor()
        payloads = journal.entries(cur)

        # -- 2. journal <-> row reconciliation (D-68 pairing)
        last = {}
        for payload in payloads:
            last[(payload["machine"], payload["subject_id"])] = payload

        def expect(machine, subject, actual, default=None):
            entry = last.get((machine, subject))
            journaled = entry["to_state"] if entry else default
            if journaled != actual:
                raise IntegrityHalt(
                    "%s %s: journal says %r, row says %r (D-68 pairing)"
                    % (machine, subject, journaled, actual))

        for row in cur.execute("SELECT sequence_id, state FROM sequences").fetchall():
            expect("sequence", row["sequence_id"], row["state"], default="DECLARED")
        for row in cur.execute("SELECT attempt_id, state FROM attempts").fetchall():
            expect("attempt", row["attempt_id"], row["state"], default="DECLARED")
        for row in cur.execute(
                "SELECT reservation_id, state FROM reservations").fetchall():
            expect("permit_reservation", row["reservation_id"], row["state"])
        for row in cur.execute(
                "SELECT admission_id, state FROM admissions").fetchall():
            expect("contract_admission", "adm-%d" % row["admission_id"], row["state"])
        for row in cur.execute(
                "SELECT intent_id, state FROM effect_intents").fetchall():
            # a t-i06 successor is minted inside its predecessor's entry, so
            # an intent without its own entry must still be PENDING
            expect("effect_intent", row["intent_id"], row["state"], default="PENDING")

        reverse_tables = {
            "sequence": ("sequences", "sequence_id"),
            "attempt": ("attempts", "attempt_id"),
            "permit_reservation": ("reservations", "reservation_id"),
            "standing_permit_state": ("permit_state", "sequence_id"),
            "registry_objects": ("objects", "object_hash"),
            "effect_intent": ("effect_intents", "intent_id"),
            "attempt_witness": ("attempt_witnesses", "admission_id"),
            "attempt_verification": ("attempt_verifications",
                                     "verification_id"),
            "attempt_receipt": ("attempt_receipts", "admission_id"),
            "attempt_artifact": ("attempt_artifacts", "admission_id"),
        }
        for (machine, subject) in last:
            mapped = reverse_tables.get(machine)
            if mapped is None:
                continue
            table, key = mapped
            if cur.execute(
                    "SELECT 1 FROM %s WHERE %s = ?" % (table, key), (subject,)
            ).fetchone() is None:
                raise IntegrityHalt(
                    "journal names %s %s but its row is gone (D-68 pairing)"
                    % (machine, subject))

        # -- 5. instance lease <-> its journaled authority
        lease_row = cur.execute(
            "SELECT holder, epoch FROM instance_lease WHERE lease_key = 'sequencer'"
        ).fetchone()
        lease_entry = last.get(("instance_lease", "sequencer"))
        if (lease_row is None) != (lease_entry is None):
            raise IntegrityHalt("instance lease row and journal disagree")
        if lease_row is not None:
            facts = lease_entry.get("facts", {})
            if (facts.get("holder") != lease_row["holder"]
                    or facts.get("epoch") != lease_row["epoch"]):
                raise IntegrityHalt(
                    "instance lease row (%s epoch %s) does not match its last"
                    " journal entry" % (lease_row["holder"], lease_row["epoch"]))

        # -- 4. permit counters are exactly their reservation rows
        for row in cur.execute(
                "SELECT sequence_id, consumed_uses, reserved_uses FROM permit_state"
        ).fetchall():
            counts = {r["state"]: r["n"] for r in cur.execute(
                "SELECT state, COUNT(*) AS n FROM reservations"
                " WHERE sequence_id = ? GROUP BY state",
                (row["sequence_id"],),
            ).fetchall()}
            if (row["reserved_uses"] != counts.get("RESERVED", 0)
                    or row["consumed_uses"] != counts.get("CONSUMED", 0)):
                raise IntegrityHalt(
                    "permit counters for %s disagree with reservation rows"
                    " (INV-42/INV-48)" % row["sequence_id"])

        # -- 3. every committed content reference resolves and recomputes
        referenced = set()
        objects_checked = 0
        for row in cur.execute(
                "SELECT object_hash, kind, content_address FROM objects").fetchall():
            data = self.store.get(row["content_address"])  # re-hashes (D-66)
            payload = json.loads(data.decode("utf-8"))
            if row["kind"] == "TaskContractRecord":
                _, _, rh = compute_tiers(payload)
                if rh != row["object_hash"]:
                    raise IntegrityHalt(
                        "record %s does not recompute (INV-11)" % row["object_hash"])
            elif object_hash(row["kind"], payload) != row["object_hash"]:
                raise IntegrityHalt(
                    "object %s does not recompute" % row["object_hash"])
            referenced.add(row["content_address"])
            objects_checked += 1

        for row in cur.execute("SELECT * FROM records").fetchall():
            payload = json.loads(
                self.store.get(row["content_address"]).decode("utf-8"))
            rd, cd, rh = compute_tiers(payload)
            if (rd, cd, rh) != (row["requirement_digest"], row["contract_digest"],
                                row["record_hash"]):
                raise IntegrityHalt(
                    "record %s digests do not recompute (INV-11)"
                    % row["record_hash"])
            referenced.add(row["content_address"])
            inputs = payload.get("specification_inputs", {}).get(
                "name_to_exact_byte_sha256", {})
            for name in sorted(inputs):
                self.store.get(inputs[name])  # frozen inputs stay resolvable
                referenced.add(inputs[name])

        for table, column in (("admissions", "payload_content_address"),
                              ("duplicate_admissions", "payload_content_address")):
            for row in cur.execute(
                    "SELECT %s AS a FROM %s" % (column, table)).fetchall():
                self.store.get(row["a"])
                referenced.add(row["a"])

        # -- 8. attempt executions: every state past RESERVED carries its
        #       bound call, every bound call joins a dispatched attempt, and
        #       the committed projection reference resolves (t-a02/obj-10).
        for row in cur.execute(
                "SELECT attempt_id, state FROM attempts").fetchall():
            has_call = cur.execute(
                "SELECT 1 FROM attempt_calls WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone() is not None
            if row["state"] in EXECUTION_STATES and not has_call:
                raise IntegrityHalt(
                    "attempt %s is %s with no bound execution (t-a02 pairing)"
                    % (row["attempt_id"], row["state"]))
        for row in cur.execute(
                "SELECT attempt_id, projection_address FROM attempt_calls"
        ).fetchall():
            att = cur.execute(
                "SELECT state FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            if att is None or att["state"] in ("DECLARED", "RESERVED"):
                raise IntegrityHalt(
                    "bound execution for undispatched attempt %s"
                    % row["attempt_id"])
            self.store.get(row["projection_address"])
            referenced.add(row["projection_address"])

        # -- 8b. witness admissions (M3 seam): every row joins a bound
        #        execution, resolves to its exact bytes, and has its
        #        journaled admission (D-68 pairing, both directions via
        #        reverse_tables above).
        for row in cur.execute(
                "SELECT admission_id, attempt_id, content_sha256"
                " FROM attempt_witnesses").fetchall():
            if cur.execute(
                    "SELECT 1 FROM attempt_calls WHERE attempt_id = ?",
                    (row["attempt_id"],)).fetchone() is None:
                raise IntegrityHalt(
                    "witness admission %s joins no bound execution"
                    " (D-21 correlation)" % row["admission_id"])
            self.store.get(row["content_sha256"])  # re-hashes (D-66)
            referenced.add(row["content_sha256"])
            entry = last.get(("attempt_witness", row["admission_id"]))
            if entry is None or entry.get("to_state") != "ADMITTED":
                raise IntegrityHalt(
                    "witness admission %s has no journaled admission"
                    " (D-68 pairing)" % row["admission_id"])

        # -- 8c. verification results (M4): every row joins an attempt at
        #        or past EVIDENCE_COMPLETE, its receipt (and environment)
        #        bytes re-hash (D-66), and its admission is journaled —
        #        both directions, via reverse_tables above. A VERIFIED or
        #        REJECTED attempt without a single admitted result, or a
        #        repair counter that disagrees with the journaled t-s11
        #        history, is forged state and HALTs.
        _VERIFICATION_ATTEMPT_STATES = frozenset((
            "EVIDENCE_COMPLETE", "VERIFIED", "REJECTED", "FENCED",
            "RECONCILED"))
        for row in cur.execute(
                "SELECT verification_id, attempt_id, receipt_sha256,"
                " environment_sha256 FROM attempt_verifications").fetchall():
            att = cur.execute(
                "SELECT state FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            if att is None or att["state"] not in _VERIFICATION_ATTEMPT_STATES:
                raise IntegrityHalt(
                    "verification %s joins attempt state %r: results are"
                    " admitted at EVIDENCE_COMPLETE only"
                    % (row["verification_id"],
                       att["state"] if att else None))
            self.store.get(row["receipt_sha256"])  # re-hashes (D-66)
            referenced.add(row["receipt_sha256"])
            if row["environment_sha256"] is not None:
                self.store.get(row["environment_sha256"])
                referenced.add(row["environment_sha256"])
            entry = last.get(("attempt_verification", row["verification_id"]))
            if entry is None or entry.get("to_state") != "RECORDED":
                raise IntegrityHalt(
                    "verification %s has no journaled admission (D-68"
                    " pairing)" % row["verification_id"])
        for row in cur.execute(
                "SELECT attempt_id, state FROM attempts"
                " WHERE state IN ('VERIFIED','REJECTED')").fetchall():
            n = cur.execute(
                "SELECT COUNT(*) AS n FROM attempt_verifications"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()["n"]
            if n == 0:
                raise IntegrityHalt(
                    "attempt %s is %s with no admitted verification result"
                    " (t-a07/t-a08 require obj-19/obj-20)"
                    % (row["attempt_id"], row["state"]))
        repair_history = {}
        for payload in payloads:
            if (payload.get("machine") == "sequence"
                    and payload.get("rule") == "t-s11"):
                subject = payload["subject_id"]
                repair_history[subject] = repair_history.get(subject, 0) + 1
        for row in cur.execute(
                "SELECT sequence_id, repairs_used FROM sequences").fetchall():
            if row["repairs_used"] != repair_history.get(row["sequence_id"], 0):
                raise IntegrityHalt(
                    "sequence %s repairs_used %d disagrees with its journaled"
                    " t-s11 history %d"
                    % (row["sequence_id"], row["repairs_used"],
                       repair_history.get(row["sequence_id"], 0)))

        # -- 8d. finalization coherence (M5): a COMMITTING sequence names a
        #        current attempt with at least one finalization intent
        #        (t-s15a mints obj-25 durably before any effect), and every
        #        journaled finalization receipt reference (t-s16 obj-26,
        #        t-s18 observation receipt) resolves to its exact bytes.
        for row in cur.execute(
                "SELECT sequence_id, current_attempt_id FROM sequences"
                " WHERE state = 'COMMITTING'").fetchall():
            if row["current_attempt_id"] is None or cur.execute(
                    "SELECT 1 FROM effect_intents WHERE sequence_id = ?"
                    " AND attempt_id = ?"
                    " AND effect_id = 'eff-local-canonical-cas'",
                    (row["sequence_id"], row["current_attempt_id"])
            ).fetchone() is None:
                raise IntegrityHalt(
                    "sequence %s is COMMITTING with no finalization intent"
                    " (t-s15a mints obj-25 before any effect)"
                    % row["sequence_id"])
        for entry_payload in payloads:
            if entry_payload.get("machine") != "sequence":
                continue
            entry_facts = entry_payload.get("facts", {})
            for key in ("commit_receipt_address",
                        "observation_receipt_address",
                        "acceptance_sha256", "release_sha256"):
                address = entry_facts.get(key)
                if address is not None:
                    self.store.get(address)  # re-hashes (D-66)
                    referenced.add(address)

        # -- 8e. acceptance and dependency release (M6): an ACCEPTED-or-
        #        beyond sequence must carry its journaled obj-30 reference;
        #        every released_edges row pairs with a t-s41 entry (both
        #        directions), joins a durably released predecessor and
        #        resolves its references; and a READY sequence with frozen
        #        predecessor edges must still satisfy them from the durable
        #        rows — forged readiness HALTs.
        acceptance_rules = ("t-s14", "t-s20")
        release_ids_journaled = {}
        acceptance_seen = set()
        for entry_payload in payloads:
            if entry_payload.get("machine") != "sequence":
                continue
            subject = entry_payload.get("subject_id")
            if entry_payload.get("rule") in acceptance_rules:
                acceptance_seen.add(subject)
            if entry_payload.get("rule") == "t-s41":
                for release_id in entry_payload.get("facts", {}).get(
                        "release_ids", []):
                    release_ids_journaled[release_id] = subject
        for row in cur.execute(
                "SELECT sequence_id, state FROM sequences WHERE state IN"
                " ('ACCEPTED','DEPENDENCIES_RELEASED','COMPLETED')"
        ).fetchall():
            if row["sequence_id"] not in acceptance_seen:
                raise IntegrityHalt(
                    "sequence %s is %s without its journaled obj-30"
                    " acceptance reference"
                    % (row["sequence_id"], row["state"]))
        for row in cur.execute(
                "SELECT * FROM released_edges").fetchall():
            if release_ids_journaled.get(row["release_id"]) != \
                    row["predecessor_sequence_id"]:
                raise IntegrityHalt(
                    "released edge %s has no journaled t-s41 release"
                    " (D-68 pairing)" % row["release_id"])
            pred = cur.execute(
                "SELECT state FROM sequences WHERE sequence_id = ?",
                (row["predecessor_sequence_id"],)).fetchone()
            if pred is None or pred["state"] not in (
                    "DEPENDENCIES_RELEASED", "COMPLETED"):
                raise IntegrityHalt(
                    "released edge %s names predecessor %s in state %s:"
                    " release requires a durably released predecessor"
                    % (row["release_id"], row["predecessor_sequence_id"],
                       pred["state"] if pred else None))
            self.store.get(row["acceptance_sha256"])  # re-hashes (D-66)
            referenced.add(row["acceptance_sha256"])
            self.store.get(row["release_sha256"])
            referenced.add(row["release_sha256"])
        for release_id, subject in release_ids_journaled.items():
            if cur.execute(
                    "SELECT 1 FROM released_edges WHERE release_id = ?",
                    (release_id,)).fetchone() is None:
                raise IntegrityHalt(
                    "journal names released edge %s for %s but its row is"
                    " gone (D-68 pairing)" % (release_id, subject))
        for row in cur.execute(
                "SELECT s.sequence_id, r.content_address FROM sequences s"
                " JOIN records r ON r.record_hash = s.record_hash"
                " WHERE s.state = 'READY'").fetchall():
            record_payload = json.loads(
                self.store.get(row["content_address"]).decode("utf-8"))
            if record_payload.get("predecessors"):
                unsatisfied, _ = self._edges_satisfied(cur, record_payload)
                if unsatisfied:
                    raise IntegrityHalt(
                        "sequence %s is READY with unsatisfied frozen"
                        " dependency edges %s"
                        % (row["sequence_id"], ", ".join(unsatisfied)))

        # -- 8f. execution receipts (C3-M1 seam): every attempt_receipts
        #        row joins an attempt at or past CANDIDATE_FROZEN, its
        #        bytes resolve and re-hash (D-66), the payload re-validates
        #        against the mirrored obj-15 schema, the row columns agree
        #        with the payload, its admission is journaled (both
        #        directions, via reverse_tables above), and the payload
        #        RE-DERIVES byte-identically from durable facts — the
        #        t-a05 journal instant as anchor, the frozen record, the
        #        attempt's witness rows and the declared window (D-C3-2:
        #        a forged or divergent row HALTs). A genuinely
        #        INTEGRITY_FAILED ledger halts earlier, at 8b's witness
        #        re-hash — D-C3-3 records that interplay as deliberate.
        _RECEIPT_ATTEMPT_STATES = frozenset((
            "CANDIDATE_FROZEN", "EVIDENCE_COMPLETE", "VERIFIED",
            "REJECTED", "FAILED", "FENCED", "RECONCILED"))
        freeze_instants = {}
        for entry_payload in payloads:
            if (entry_payload.get("machine") == "attempt"
                    and entry_payload.get("rule") == "t-a05"):
                freeze_instants[entry_payload["subject_id"]] = \
                    entry_payload["recorded_utc"]
        for row in cur.execute("SELECT * FROM attempt_receipts").fetchall():
            att = cur.execute(
                "SELECT state, attempt_contract_hash, sequence_id"
                " FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            if att is None or att["state"] not in _RECEIPT_ATTEMPT_STATES:
                raise IntegrityHalt(
                    "receipt admission %s joins attempt state %r: receipts"
                    " are admitted at CANDIDATE_FROZEN only"
                    % (row["admission_id"], att["state"] if att else None))
            data = self.store.get(row["content_sha256"])  # re-hashes (D-66)
            referenced.add(row["content_sha256"])
            receipt_payload = json.loads(data.decode("utf-8"))
            if receipts.validate_receipt(receipt_payload):
                raise IntegrityHalt(
                    "receipt %s no longer validates against the obj-15"
                    " schema" % row["admission_id"])
            if (receipt_payload["attempt_id"] != row["attempt_id"]
                    or receipt_payload["completeness"] != row["completeness"]
                    or receipt_payload["evidence_complete_by_utc"]
                    != row["evidence_complete_by_utc"]
                    or receipt_payload["minter"] != row["minter"]
                    or receipt_payload["call_id"] != row["call_id"]):
                raise IntegrityHalt(
                    "receipt row %s disagrees with its admitted payload"
                    % row["admission_id"])
            entry = last.get(("attempt_receipt", row["admission_id"]))
            if entry is None or entry.get("to_state") != "ADMITTED":
                raise IntegrityHalt(
                    "receipt admission %s has no journaled admission"
                    " (D-68 pairing)" % row["admission_id"])
            anchor = freeze_instants.get(row["attempt_id"])
            if anchor is None:
                raise IntegrityHalt(
                    "receipt %s exists for an attempt with no journaled"
                    " t-a05 freeze" % row["admission_id"])
            witness_rows = [dict(r) for r in cur.execute(
                "SELECT witness_kind, producer, content_sha256"
                " FROM attempt_witnesses WHERE attempt_id = ?"
                " ORDER BY rowid", (row["attempt_id"],)).fetchall()]
            artifact_rows = [dict(r) for r in cur.execute(
                "SELECT logical_path, content_sha256, role"
                " FROM attempt_artifacts WHERE attempt_id = ?"
                " ORDER BY rowid", (row["attempt_id"],)).fetchall()]
            seq_row = cur.execute(
                "SELECT record_hash FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            record_payload = json.loads(self.store.get(cur.execute(
                "SELECT content_address FROM records WHERE record_hash = ?",
                (seq_row["record_hash"],)).fetchone()["content_address"]
            ).decode("utf-8"))
            call = cur.execute(
                "SELECT role, native_session_id FROM attempt_calls"
                " WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            receipt_state = {
                "record_payload": record_payload, "role": call["role"],
                "attempt_id": row["attempt_id"],
                "attempt_contract_hash": att["attempt_contract_hash"],
                "call_id": row["call_id"],
                "native_session_id": call["native_session_id"],
                "witnesses": witness_rows, "artifacts": artifact_rows,
                "anchor_utc": anchor}
            expected = receipts.derive_evidence(
                self.store, receipt_state,
                receipt_payload["derivation"]["window_seconds"])
            if receipts.receipt_bytes(expected) != data:
                raise IntegrityHalt(
                    "receipt %s does not re-derive from durable facts"
                    " (D-C3-2: forged or divergent admission)"
                    % row["admission_id"])
        for row in cur.execute(
                "SELECT attempt_id, state FROM attempts WHERE state IN"
                " ('EVIDENCE_COMPLETE','VERIFIED','REJECTED')").fetchall():
            n = cur.execute(
                "SELECT COUNT(*) AS n FROM attempt_receipts"
                " WHERE attempt_id = ? AND completeness = 'COMPLETE'",
                (row["attempt_id"],)).fetchone()["n"]
            if n == 0:
                raise IntegrityHalt(
                    "attempt %s is %s with no admitted COMPLETE receipt"
                    " (execution_receipt_complete's classification half)"
                    % (row["attempt_id"], row["state"]))
        for entry_payload in payloads:
            if (entry_payload.get("machine") == "attempt"
                    and entry_payload.get("rule") == "t-a13"):
                t13_facts = entry_payload.get("facts", {})
                n = cur.execute(
                    "SELECT COUNT(*) AS n FROM attempt_receipts"
                    " WHERE attempt_id = ? AND content_sha256 = ?"
                    " AND completeness = ?",
                    (entry_payload["subject_id"],
                     t13_facts.get("execution_receipt_address"),
                     t13_facts.get("completeness"))).fetchone()["n"]
                if n == 0:
                    raise IntegrityHalt(
                        "journaled t-a13 for %s names no matching admitted"
                        " receipt" % entry_payload["subject_id"])

        # -- 8g. artifact provenance and witness-correlation coherence
        #        (C3-M2): every attempt_artifacts row joins a bound
        #        execution, its ledger correlation matches the row, its
        #        bytes resolve and re-hash (D-66), and its admission is
        #        journaled (both directions, via reverse_tables above);
        #        and for every bound execution, the admitted witnesses'
        #        RECOGNIZED facts must not contradict the ledger identity
        #        (cross-execution injection surfaced at restart HALTs —
        #        D-C3-5/6). Absent facts stay absent: recovery never
        #        invents certainty.
        for row in cur.execute(
                "SELECT * FROM attempt_artifacts").fetchall():
            call = cur.execute(
                "SELECT call_id, role FROM attempt_calls"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
            if call is None:
                raise IntegrityHalt(
                    "artifact %s joins no bound execution (INV-03)"
                    % row["admission_id"])
            if (call["call_id"] != row["call_id"]
                    or call["role"] != row["role"]):
                raise IntegrityHalt(
                    "artifact %s correlation disagrees with the ledger"
                    " (INV-03 causal provenance)" % row["admission_id"])
            self.store.get(row["content_sha256"])  # re-hashes (D-66)
            referenced.add(row["content_sha256"])
            entry = last.get(("attempt_artifact", row["admission_id"]))
            if entry is None or entry.get("to_state") != "ADMITTED":
                raise IntegrityHalt(
                    "artifact %s has no journaled admission (D-68"
                    " pairing)" % row["admission_id"])
        for call_row in cur.execute(
                "SELECT c.attempt_id, c.call_id, c.native_session_id"
                " FROM attempt_calls c JOIN attempts a"
                " ON a.attempt_id = c.attempt_id"
                " WHERE a.state NOT IN ('DECLARED','RESERVED',"
                "'DISPATCHED')").fetchall():
            for wrow in cur.execute(
                    "SELECT witness_kind, content_sha256"
                    " FROM attempt_witnesses WHERE attempt_id = ?",
                    (call_row["attempt_id"],)).fetchall():
                try:
                    wdata = self.store.get(wrow["content_sha256"])
                except IntegrityHalt:
                    raise
                wfacts = witness_semantics.extract_facts(
                    wrow["witness_kind"], wdata)
                conflicts = witness_semantics.identity_conflicts(
                    wrow["witness_kind"], wfacts, call_row["call_id"],
                    call_row["native_session_id"])
                if conflicts:
                    raise IntegrityHalt(
                        "witness %s contradicts the bound execution of"
                        " attempt %s (D-C3-5): %s"
                        % (wrow["content_sha256"], call_row["attempt_id"],
                           "; ".join(conflicts)))

        # -- resumable view + fence coherence
        current = []
        for row in cur.execute(
            "SELECT s.sequence_id, s.state, s.version, s.fence, s.current_attempt_id,"
            " a.state AS attempt_state, a.fence AS attempt_fence"
            " FROM sequences s LEFT JOIN attempts a ON a.attempt_id = s.current_attempt_id"
        ).fetchall():
            if row["current_attempt_id"] is not None:
                if row["attempt_state"] is None:
                    raise IntegrityHalt(
                        "sequence %s names a missing attempt" % row["sequence_id"])
                if row["attempt_fence"] != row["fence"]:
                    raise IntegrityHalt(
                        "sequence %s fence %r != current attempt fence %r (INV-13)"
                        % (row["sequence_id"], row["fence"], row["attempt_fence"]))
            current.append({
                "sequence_id": row["sequence_id"], "state": row["state"],
                "version": row["version"], "fence": row["fence"],
                "current_attempt_id": row["current_attempt_id"],
                "attempt_state": row["attempt_state"],
            })

        # -- 7. effect intents: windows, retry-chain integrity, in-doubt set
        intents_in_doubt = []
        for row in cur.execute("SELECT * FROM effect_intents").fetchall():
            if (row["expires_utc"] is not None and row["hard_timeout_utc"] is not None
                    and not row["hard_timeout_utc"] < row["expires_utc"]):
                raise IntegrityHalt(
                    "intent %s window violates intent_ttl_gt_hard_timeout"
                    " (SEM-12, INV-67)" % row["intent_id"])
            if row["superseded_intent_id"] is not None:
                prior = cur.execute(
                    "SELECT state, effect_id, target, expected_old FROM effect_intents"
                    " WHERE intent_id = ?", (row["superseded_intent_id"],),
                ).fetchone()
                if prior is None or prior["state"] != "SUPERSEDED_BY_RETRY":
                    raise IntegrityHalt(
                        "intent %s binds a superseded intent that is missing or"
                        " not SUPERSEDED_BY_RETRY (INV-73)" % row["intent_id"])
                if (prior["effect_id"] != row["effect_id"]
                        or prior["target"] != row["target"]
                        or prior["expected_old"] != row["expected_old"]):
                    raise IntegrityHalt(
                        "retry intent %s changes effect, target or expected old"
                        " value (t-i06)" % row["intent_id"])
            if row["state"] in ("PENDING", "UNKNOWN_OUTCOME"):
                intents_in_doubt.append({
                    "intent_id": row["intent_id"], "state": row["state"],
                    "effect_id": row["effect_id"],
                })
        for row in cur.execute(
                "SELECT intent_id FROM effect_intents WHERE state = 'SUPERSEDED_BY_RETRY'"
        ).fetchall():
            n = cur.execute(
                "SELECT COUNT(*) AS n FROM effect_intents WHERE superseded_intent_id = ?",
                (row["intent_id"],)).fetchone()["n"]
            if n != 1:
                raise IntegrityHalt(
                    "superseded intent %s has %d successors; t-i06 mints exactly"
                    " one" % (row["intent_id"], n))

        # -- 6. harmless leftovers, reported and ignored
        orphans = self.store.orphans(referenced)

        return {"journal_entries": len(payloads),
                "objects_verified": objects_checked,
                "sequences": current, "orphans": orphans,
                "intents_in_doubt": intents_in_doubt}

    # ------------------------------------------------ attempt execution
    #
    # t-a02..t-a06 plus the t-a10/t-a11/t-a12/t-a13 failure/staleness arms:
    # the authoritative attempt lifecycle for one bound execution. The
    # dispatcher/collector TRIGGER these; the Sequencer applies them
    # (chapter 0's actor model). Receipt and witness payload SCHEMAS are
    # later chapters' (obj-10/11/15/16 are form_only here): the transitions
    # take content-addressed references that must resolve in the CAS; the
    # admission-join is the M3 evidence seam, and verification (t-a07/t-a08
    # with the sequence arc t-s07/t-s09..t-s12/t-s45) is the M4 surface
    # below.

    def _attempt_call(self, cur, attempt_id):
        row = cur.execute(
            "SELECT * FROM attempt_calls WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise ConflictError("attempt %s has no bound execution" % attempt_id)
        return row

    def _resolve_receipt(self, cur, execution_receipt_address, att):
        """Two-step receipt resolution enforcing the boundary between
        content-addressed evidence and authoritative receipt conclusions.

        Step 1 — content-address resolution (D-66): the receipt bytes
        exist in the CAS and re-hash to the declared address.
        Step 2 — authoritative receipt validation (INV-68/INV-69): the
        receipt is structurally an ExecutionReceipt, bound to THIS
        attempt by attempt_id, and its attempt_contract_hash (when
        present) matches the durable contract.  Chapter 3 owns the
        deeper admission join semantics; this boundary enforces the
        identity and binding invariants the Sequencer can verify."""
        receipt = json.loads(
            self.store.get(execution_receipt_address).decode("utf-8"))
        if receipt.get("kind") != "ExecutionReceipt":
            raise ConflictError(
                "receipt is not kind ExecutionReceipt (INV-68: receipt"
                " identity)")
        if receipt.get("attempt_id") != att["attempt_id"]:
            raise ConflictError(
                "receipt attempt_id %r does not bind to attempt %s"
                " (INV-69: receipt must bind its Attempt)"
                % (receipt.get("attempt_id"), att["attempt_id"]))
        if receipt.get("attempt_contract_hash") != att["attempt_contract_hash"]:
            raise ConflictError(
                "receipt attempt_contract_hash must match the durable"
                " contract (INV-68: receipt integrity)")
        return receipt

    def _cas_attempt(self, cur, att, to_state, now, require_sequence_fence=False):
        """Attempt compare-and-swap: state, version and the row's own fence
        are conditions of the UPDATE; with ``require_sequence_fence`` the
        registered fence_matches precondition joins the statement as the
        correlated sequence-fence equality."""
        sql = ("UPDATE attempts SET state = ?, version = version + 1,"
               " updated_utc = ? WHERE attempt_id = ? AND state = ?"
               " AND version = ? AND fence = ?")
        if require_sequence_fence:
            sql += (" AND fence = (SELECT fence FROM sequences"
                    "              WHERE sequence_id = attempts.sequence_id)")
        cur.execute(sql, (to_state, now, att["attempt_id"], att["state"],
                          att["version"], att["fence"]))
        if cur.rowcount != 1:
            raise ConflictError(
                "attempt %s moved under us (expected %s v%d fence %d)"
                % (att["attempt_id"], att["state"], att["version"], att["fence"]))

    def _quadruple(self, att):
        return {"sequence_id": att["sequence_id"],
                "attempt_id": att["attempt_id"],
                "version": att["version"] + 1, "fence": att["fence"]}

    def dispatch_attempt(self, attempt_id, role, provider,
                         projection_address):
        """t-a02 RESERVED→DISPATCHED (trigger: dispatcher). The SEQUENCER
        mints the call identity inside this transaction (M3 ownership
        reconciliation): the ``call_id`` and its ``attempt_calls`` row are
        durably committed BEFORE any external launch may use them, no caller
        can supply or reuse one, and ``call_id_bound`` holds by construction.
        The obj-10 projection reference must resolve, and the role must be
        one the frozen record's completion_policies name. Returns
        ``{"call_id": ...}`` — the provider-side idempotency key."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "RESERVED":
                raise ConflictError(
                    "dispatch expects RESERVED, found %s" % att["state"])
            call_id = mint_id("call")
            if not self.store.has(projection_address):
                raise ConflictError(
                    "projection %s does not resolve in the content store"
                    " (t-a02 requires obj-10)" % projection_address)
            seq = self._sequence(cur, att["sequence_id"])
            payload = self._verified_record(cur, seq["record_hash"])
            if role not in payload.get("completion_policies", {}):
                raise ConflictError(
                    "role %r is not named by the frozen record's"
                    " completion_policies" % role)
            # INV-37's dispatch-side re-hash (D-C3-9): every declared
            # specification input must resolve to its exact declared
            # bytes BEFORE any external launch — the collection-side
            # probe and the receipt's input_rehash binding are the
            # other half.
            declared_inputs = payload.get("specification_inputs", {}).get(
                "name_to_exact_byte_sha256", {})
            for input_name in sorted(declared_inputs):
                if self.store.verify([declared_inputs[input_name]]):
                    raise ConflictError(
                        "specification input %r does not resolve to its"
                        " declared bytes at dispatch (INV-37: inputs are"
                        " re-hashed at dispatch and at collection)"
                        % input_name)
            cur.execute(
                "INSERT INTO attempt_calls (attempt_id, role, provider, call_id,"
                " projection_address, created_utc, updated_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (attempt_id, role, provider, call_id, projection_address,
                 now, now))
            self._cas_attempt(cur, att, "DISPATCHED", now)
            facts = self._quadruple(att)
            facts.update({"role": role, "provider": provider,
                          "call_id": call_id,
                          "projection_address": projection_address})
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a02",
                from_state="RESERVED", to_state="DISPATCHED",
                recorded_utc=now, facts=facts))
            return {"call_id": call_id}

    def attempt_running(self, attempt_id, native_session_id, witness_address,
                        member_lease_seconds=7200):
        """t-a03 DISPATCHED→RUNNING (trigger: collector): the execution
        handle must carry the provider's native session id, witnessed by an
        obj-11 reference. Binding the session also opens the required
        member's liveness window (t-a12's lease): a configuration default
        (D-18), not a semantic constant."""
        now_dt = self.clock.now()
        now = instant(now_dt)
        member_lease = instant(now_dt + timedelta(seconds=member_lease_seconds))
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "DISPATCHED":
                raise ConflictError(
                    "attempt_running expects DISPATCHED, found %s" % att["state"])
            if not (isinstance(native_session_id, str) and native_session_id):
                raise ConflictError(
                    "handle_with_native_session_id requires the provider's"
                    " session id")
            if not self.store.has(witness_address):
                raise ConflictError(
                    "witness %s does not resolve (t-a03 requires obj-11)"
                    % witness_address)
            self._attempt_call(cur, attempt_id)
            cur.execute(
                "UPDATE attempt_calls SET native_session_id = ?, updated_utc = ?,"
                " member_lease_expires_utc = ? WHERE attempt_id = ?",
                (native_session_id, now, member_lease, attempt_id))
            self._cas_attempt(cur, att, "RUNNING", now)
            facts = self._quadruple(att)
            facts.update({"native_session_id": native_session_id,
                          "witness_address": witness_address,
                          "member_lease_expires_utc": member_lease})
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a03",
                from_state="DISPATCHED", to_state="RUNNING",
                recorded_utc=now, facts=facts))

    def admit_witness(self, attempt_id, witness_kind, producer, data):
        """The M3 witness-admission seam (D-21/D-38): the exact producer
        bytes go to the CAS, and one ``attempt_witnesses`` row carries
        Chapter 1 D-21's structured admission entry — ``witness_kind`` (a
        registered WitnessRecord object id), ``producer`` (that kind's
        registered minter), ``content_sha256`` (the D-31 address of the
        exact bytes), ``admitted_utc`` (D-27), and the execution correlation
        (call_id and, once known, the native session id) read from the
        ledger's ``attempt_calls`` row, never caller-supplied — journaled in
        the same transaction. Admission requires a bound, not-yet-frozen
        execution (DISPATCHED/RUNNING/FINISHED): everything a frozen
        candidate's receipt references must already be admitted (t-a05
        precedes t-a06's join). Recognized payload facts gate identity
        (C3-M2, D-C3-5): a witness whose facts NAME another execution is
        refused here, cross-attempt injection dying at the seam, while
        absent or unrecognized facts stay tolerated and the admitted
        bytes remain exactly the producer's either way.

        Idempotent on identity: re-admitting the same (attempt, kind,
        producer, content) returns the existing entry and journals nothing —
        the journal never claims an admission that did not occur."""
        kind = WITNESS_KINDS.get(witness_kind)
        if kind is None:
            raise ConflictError(
                "witness_kind %r is not a registered WitnessRecord object"
                % (witness_kind,))
        if producer != kind["producer"]:
            raise ConflictError(
                "producer %r is not the registered minter %r of %s (D-21)"
                % (producer, kind["producer"], witness_kind))
        data = bytes(data)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] not in ("DISPATCHED", "RUNNING", "FINISHED"):
                raise ConflictError(
                    "witness admission requires a bound, unfrozen execution"
                    " (DISPATCHED/RUNNING/FINISHED), found %s" % att["state"])
            call = self._attempt_call(cur, attempt_id)
            facts = witness_semantics.extract_facts(witness_kind, data)
            conflicts = witness_semantics.identity_conflicts(
                witness_kind, facts, call["call_id"],
                call["native_session_id"])
            if conflicts:
                raise ConflictError(
                    "witness admission refused (cross-execution identity,"
                    " D-C3-5): %s" % "; ".join(conflicts))
            address = self.store.put(data)
            existing = cur.execute(
                "SELECT admission_id FROM attempt_witnesses WHERE attempt_id = ?"
                " AND witness_kind = ? AND producer = ? AND content_sha256 = ?",
                (attempt_id, witness_kind, producer, address)).fetchone()
            if existing is not None:
                return {"admission_id": existing["admission_id"],
                        "content_address": address, "duplicate": True}
            admission_id = mint_id("wit")
            cur.execute(
                "INSERT INTO attempt_witnesses (admission_id, attempt_id,"
                " witness_kind, producer, content_sha256, call_id,"
                " native_session_id, admitted_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (admission_id, attempt_id, witness_kind, producer, address,
                 call["call_id"], call["native_session_id"], now))
            facts = {"attempt_id": attempt_id,
                     "sequence_id": att["sequence_id"],
                     "witness_kind": witness_kind, "producer": producer,
                     "content_sha256": address, "call_id": call["call_id"]}
            if call["native_session_id"] is not None:  # D-37
                facts["native_session_id"] = call["native_session_id"]
            journal.append(cur, journal.transition_record(
                machine="attempt_witness", subject_id=admission_id,
                rule="ch2:admit-witness", to_state="ADMITTED",
                recorded_utc=now, facts=facts))
            return {"admission_id": admission_id,
                    "content_address": address, "duplicate": False}

    def admit_artifact(self, attempt_id, logical_path, data):
        """The C3-M2 artifact-provenance seam (INV-03/INV-04/INV-22): one
        PROVENANCE EVENT binding an admitted engineering artifact to
        exactly ONE attempt by causal admission — the exact bytes go to
        the CAS, and the row carries the LEDGER-derived execution
        correlation (sequence, call, dispatched role), the
        producer-declared logical path and the D-27 instant, journaled in
        the same transaction. Ownership never derives from the
        filesystem: no path is read here, no mtime exists in this plane,
        and byte-identical artifacts from two attempts are two events.
        Idempotent on (attempt, logical_path, content): re-admission
        returns the existing event and journals nothing."""
        if not (isinstance(logical_path, str) and logical_path
                and logical_path == logical_path.strip()
                and "\n" not in logical_path):
            raise ConflictError(
                "logical_path must be non-empty single-line text with no"
                " surrounding whitespace")
        data = bytes(data)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] not in ("DISPATCHED", "RUNNING", "FINISHED"):
                raise ConflictError(
                    "artifact admission requires a bound, unfrozen"
                    " execution (DISPATCHED/RUNNING/FINISHED), found %s"
                    % att["state"])
            call = self._attempt_call(cur, attempt_id)
            address = self.store.put(data)
            existing = cur.execute(
                "SELECT admission_id FROM attempt_artifacts"
                " WHERE attempt_id = ? AND logical_path = ?"
                " AND content_sha256 = ?",
                (attempt_id, logical_path, address)).fetchone()
            if existing is not None:
                return {"admission_id": existing["admission_id"],
                        "content_address": address, "duplicate": True}
            admission_id = mint_id("art")
            cur.execute(
                "INSERT INTO attempt_artifacts (admission_id, attempt_id,"
                " sequence_id, call_id, role, logical_path,"
                " content_sha256, admitted_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (admission_id, attempt_id, att["sequence_id"],
                 call["call_id"], call["role"], logical_path, address,
                 now))
            journal.append(cur, journal.transition_record(
                machine="attempt_artifact", subject_id=admission_id,
                rule="ch3:admit-artifact", to_state="ADMITTED",
                recorded_utc=now,
                facts={"attempt_id": attempt_id,
                       "sequence_id": att["sequence_id"],
                       "call_id": call["call_id"], "role": call["role"],
                       "logical_path": logical_path,
                       "content_sha256": address}))
            return {"admission_id": admission_id,
                    "content_address": address, "duplicate": False}

    def evidence_state(self, attempt_id):
        """The collector's public read surface (D-63, cap-30): one frozen
        attempt's identity, dispatched role, frozen record payload,
        admitted witness entries (insertion order), admitted artifact
        provenance events, the bound native session and the D-C3-1
        anchor.

        Collection is defined at CANDIDATE_FROZEN only: the witness set is
        immutable from the freeze onward (admit_witness refuses frozen
        attempts), and the attempt row's updated_utc IS the t-a05 instant
        while the state holds — the only transition into CANDIDATE_FROZEN,
        with no later mutation of the row until the state changes.

        The rows carry admitted_utc for the collector's index-state
        DIAGNOSTIC only (INV-30, D-C3-7); the receipt derivation projects
        each entry to its identity triple, so the instant never enters
        the obj-15 bytes."""
        cur = self.db.conn.cursor()
        att = self._attempt(cur, attempt_id)
        if att["state"] != "CANDIDATE_FROZEN":
            raise ConflictError(
                "evidence collection is defined at CANDIDATE_FROZEN only,"
                " found %s" % att["state"])
        call = self._attempt_call(cur, attempt_id)
        seq = self._sequence(cur, att["sequence_id"])
        payload = self._verified_record(cur, seq["record_hash"])
        rows = cur.execute(
            "SELECT witness_kind, producer, content_sha256, admitted_utc"
            " FROM attempt_witnesses WHERE attempt_id = ? ORDER BY rowid",
            (attempt_id,)).fetchall()
        artifact_rows = cur.execute(
            "SELECT logical_path, content_sha256, role, admitted_utc"
            " FROM attempt_artifacts WHERE attempt_id = ? ORDER BY rowid",
            (attempt_id,)).fetchall()
        return {"attempt_id": att["attempt_id"],
                "sequence_id": att["sequence_id"],
                "fence": att["fence"],
                "attempt_contract_hash": att["attempt_contract_hash"],
                "role": call["role"],
                "call_id": call["call_id"],
                "native_session_id": call["native_session_id"],
                "record_payload": payload,
                "witnesses": [dict(row) for row in rows],
                "artifacts": [dict(row) for row in artifact_rows],
                "anchor_utc": att["updated_utc"]}

    def admit_execution_receipt(self, attempt_id, data):
        """The C3-M1 receipt-admission seam (cap-30; D-C3-2).

        Admits the collector-minted obj-15 bytes for a CANDIDATE_FROZEN
        attempt: the bytes must BE their own canonical form, validate
        against the mirrored obj-15 schema (which pins kind, minter and
        shape), bind this exact attempt (INV-68/INV-69) and its ledger
        call correlation — and the ENTIRE payload must re-derive
        byte-identically from durable ledger facts (the frozen record's
        evidence_requirements, the dispatched role, the attempt_witnesses
        rows, the non-raising D-66 probe, the D-C3-1 anchor) given the
        receipt's one collector parameter, the declared window. Authority
        is derived, never declared: forging any field changes the bytes
        and the admission refuses.

        Idempotent on content identity — re-admitting the same bytes
        returns the existing row and journals nothing; changed facts mint
        a NEW row (obj-15's append-only mutability), latest by rowid."""
        data = bytes(data)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "CANDIDATE_FROZEN":
                raise ConflictError(
                    "receipt admission requires CANDIDATE_FROZEN, found %s"
                    % att["state"])
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ConflictError(
                    "receipt bytes are not UTF-8 JSON (obj-15)")
            if not isinstance(payload, dict) \
                    or receipts.receipt_bytes(payload) != data:
                raise ConflictError(
                    "receipt bytes are not their own canonical form: the"
                    " admitted address must be the payload's identity")
            issues = receipts.validate_receipt(payload)
            if issues:
                raise ConflictError(
                    "receipt failed obj-15 schema validation: %s"
                    % "; ".join(repr(issue) for issue in issues))
            if payload["attempt_id"] != att["attempt_id"]:
                raise ConflictError(
                    "receipt attempt_id %r does not bind to attempt %s"
                    " (INV-69)" % (payload["attempt_id"], att["attempt_id"]))
            if payload["attempt_contract_hash"] != \
                    att["attempt_contract_hash"]:
                raise ConflictError(
                    "receipt attempt_contract_hash must match the durable"
                    " contract (INV-68)")
            call = self._attempt_call(cur, attempt_id)
            if payload["call_id"] != call["call_id"]:
                raise ConflictError(
                    "receipt call_id %r is not the ledger's bound call %s"
                    " (D-21 correlation)"
                    % (payload["call_id"], call["call_id"]))
            window = payload["derivation"]["window_seconds"]
            seq = self._sequence(cur, att["sequence_id"])
            record = self._verified_record(cur, seq["record_hash"])
            rows = cur.execute(
                "SELECT witness_kind, producer, content_sha256"
                " FROM attempt_witnesses WHERE attempt_id = ?"
                " ORDER BY rowid", (attempt_id,)).fetchall()
            artifact_rows = cur.execute(
                "SELECT logical_path, content_sha256, role"
                " FROM attempt_artifacts WHERE attempt_id = ?"
                " ORDER BY rowid", (attempt_id,)).fetchall()
            state = {"record_payload": record, "role": call["role"],
                     "attempt_id": att["attempt_id"],
                     "attempt_contract_hash":
                         att["attempt_contract_hash"],
                     "call_id": call["call_id"],
                     "native_session_id": call["native_session_id"],
                     "witnesses": [dict(row) for row in rows],
                     "artifacts": [dict(row) for row in artifact_rows],
                     "anchor_utc": att["updated_utc"]}
            expected = receipts.derive_evidence(self.store, state, window)
            if receipts.receipt_bytes(expected) != data:
                raise ConflictError(
                    "receipt diverges from the ledger derivation (D-C3-2:"
                    " authority is derived, never declared) — the ledger"
                    " derives %s over %d admitted entries, anchor %s"
                    % (expected["completeness"], len(state["witnesses"]),
                       att["updated_utc"]))
            address = self.store.put(data)
            existing = cur.execute(
                "SELECT admission_id, completeness, evidence_complete_by_utc"
                " FROM attempt_receipts WHERE attempt_id = ?"
                " AND content_sha256 = ?",
                (attempt_id, address)).fetchone()
            if existing is not None:
                return {"admission_id": existing["admission_id"],
                        "content_address": address,
                        "completeness": existing["completeness"],
                        "evidence_complete_by_utc":
                            existing["evidence_complete_by_utc"],
                        "duplicate": True}
            admission_id = mint_id("rcp")
            cur.execute(
                "INSERT INTO attempt_receipts (admission_id, attempt_id,"
                " content_sha256, completeness, evidence_complete_by_utc,"
                " minter, call_id, admitted_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (admission_id, attempt_id, address,
                 expected["completeness"],
                 expected["evidence_complete_by_utc"],
                 receipts.RECEIPT_MINTER, call["call_id"], now))
            journal.append(cur, journal.transition_record(
                machine="attempt_receipt", subject_id=admission_id,
                rule="ch3:admit-execution-receipt", to_state="ADMITTED",
                recorded_utc=now,
                facts={"attempt_id": attempt_id,
                       "sequence_id": att["sequence_id"],
                       "content_sha256": address,
                       "completeness": expected["completeness"],
                       "evidence_complete_by_utc":
                           expected["evidence_complete_by_utc"],
                       "minter": receipts.RECEIPT_MINTER,
                       "call_id": call["call_id"]}))
            return {"admission_id": admission_id,
                    "content_address": address,
                    "completeness": expected["completeness"],
                    "evidence_complete_by_utc":
                        expected["evidence_complete_by_utc"],
                    "duplicate": False}

    def finish_attempt(self, attempt_id, role, outcome, witness_address):
        """RUNNING→FINISHED (t-a04, completion_policy_satisfied_per_role) or
        RUNNING→FAILED (t-a11) when a required role's outcome is not among
        its registered success outcomes."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "RUNNING":
                raise ConflictError(
                    "finish_attempt expects RUNNING, found %s" % att["state"])
            call = self._attempt_call(cur, attempt_id)
            if role != call["role"]:
                raise ConflictError(
                    "role %r is not the dispatched role %r" % (role, call["role"]))
            if not self.store.has(witness_address):
                raise ConflictError(
                    "witness %s does not resolve (t-a04 requires obj-11)"
                    % witness_address)
            seq = self._sequence(cur, att["sequence_id"])
            payload = self._verified_record(cur, seq["record_hash"])
            policy = payload["completion_policies"][role]
            success = outcome in policy.get("success_outcomes", [])
            if success or not policy.get("required", False):
                rule_id, to_state = "t-a04", "FINISHED"
            else:
                rule_id, to_state = "t-a11", "FAILED"
            self._cas_attempt(cur, att, to_state, now)
            facts = self._quadruple(att)
            facts.update({"role": role, "outcome": outcome,
                          "witness_address": witness_address})
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule=rule_id,
                from_state="RUNNING", to_state=to_state,
                recorded_utc=now, facts=facts))
            return to_state

    def freeze_candidate(self, attempt_id, expected_fence, candidate_address):
        """t-a05 FINISHED→CANDIDATE_FROZEN plus t-s07 (M4): the Sequence
        follows its attempt into CANDIDATE_FROZEN in the same transaction —
        both registered rows share the trigger (the attempt finished per its
        completion policy, obj-16 by reference) and fence_matches, so the
        presented fence, each row's fence and the owning Sequence's fence
        are conditions of the mutating SQL."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            seq = self._sequence(cur, att["sequence_id"])
            if expected_fence != seq["fence"] or att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"]))
            if att["state"] != "FINISHED":
                raise ConflictError(
                    "freeze_candidate expects FINISHED, found %s" % att["state"])
            if seq["state"] != "EXECUTING":
                raise ConflictError(
                    "t-s07 expects the sequence EXECUTING, found %s"
                    % seq["state"])
            if not self.store.has(candidate_address):
                raise ConflictError(
                    "candidate %s does not resolve (t-a05 requires obj-16)"
                    % candidate_address)
            self._cas_attempt(cur, att, "CANDIDATE_FROZEN", now,
                              require_sequence_fence=True)
            facts = self._quadruple(att)
            facts["candidate_address"] = candidate_address
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a05",
                from_state="FINISHED", to_state="CANDIDATE_FROZEN",
                recorded_utc=now, facts=facts))
            version = self._cas_sequence(cur, seq, now, state="CANDIDATE_FROZEN")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=att["sequence_id"], rule="t-s07",
                from_state="EXECUTING", to_state="CANDIDATE_FROZEN",
                recorded_utc=now,
                facts={"attempt_id": attempt_id, "version": version,
                       "fence": seq["fence"],
                       "candidate_address": candidate_address}))

    def complete_evidence(self, attempt_id, expected_fence,
                          execution_receipt_address):
        """t-a06 CANDIDATE_FROZEN→EVIDENCE_COMPLETE: fence_matches plus
        required_joins_present as a REAL JOIN (M3): the receipt's
        ``admitted_witnesses`` must be a non-empty list of D-21 structured
        admission entries, and every entry must match an
        ``attempt_witnesses`` row admitted FOR THIS ATTEMPT (by
        witness_kind + producer + content_sha256). Receipt entries are a
        subset of the admitted rows — a receipt cannot introduce evidence
        the admission seam never saw. Since C3-M1 the completeness
        CLASSIFICATION half is enforced too: the presented receipt must be
        ADMITTED (cap-30) with the derived classification COMPLETE."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            seq = self._sequence(cur, att["sequence_id"])
            if expected_fence != seq["fence"] or att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"]))
            if att["state"] != "CANDIDATE_FROZEN":
                raise ConflictError(
                    "complete_evidence expects CANDIDATE_FROZEN, found %s"
                    % att["state"])
            receipt = self._resolve_receipt(
                cur, execution_receipt_address, att)
            # The classification half of execution_receipt_complete
            # (t-a06/t-s09; C3-M1), checked FIRST: the presented receipt
            # must be ADMITTED and its DERIVED classification COMPLETE —
            # an INTEGRITY_FAILED receipt refuses here by its registered
            # classification, before any byte probing could only
            # backstop-HALT. The join below stays — defense in depth over
            # the same admitted rows.
            admitted = self._admitted_classification(
                cur, att, execution_receipt_address)
            if admitted["completeness"] != "COMPLETE":
                raise ConflictError(
                    "execution_receipt_complete requires the admitted"
                    " COMPLETE classification; the admitted receipt is %s"
                    % admitted["completeness"])
            witnesses = receipt.get("admitted_witnesses")
            if not (isinstance(witnesses, list) and witnesses):
                raise ConflictError(
                    "required_joins_present: the execution receipt names no"
                    " admitted witnesses")
            for entry in witnesses:
                if not (isinstance(entry, dict)
                        and isinstance(entry.get("witness_kind"), str)
                        and isinstance(entry.get("producer"), str)
                        and isinstance(entry.get("content_sha256"), str)):
                    raise ConflictError(
                        "required_joins_present: receipt witness entries"
                        " must be D-21 structured admission entries"
                        " (witness_kind, producer, content_sha256)")
                row = cur.execute(
                    "SELECT admission_id FROM attempt_witnesses"
                    " WHERE attempt_id = ? AND witness_kind = ?"
                    " AND producer = ? AND content_sha256 = ?",
                    (att["attempt_id"], entry["witness_kind"],
                     entry["producer"], entry["content_sha256"])).fetchone()
                if row is None:
                    raise ConflictError(
                        "required_joins_present: the receipt names evidence"
                        " never admitted for this attempt (%s by %s)"
                        % (entry["witness_kind"], entry["producer"]))
                if not self.store.has(entry["content_sha256"]):
                    raise IntegrityHalt(
                        "admitted witness %s has lost its bytes (D-66 HALT)"
                        % entry["content_sha256"])
            self._cas_attempt(cur, att, "EVIDENCE_COMPLETE", now,
                              require_sequence_fence=True)
            facts = self._quadruple(att)
            facts["execution_receipt_address"] = execution_receipt_address
            facts["admitted_witness_count"] = len(witnesses)
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a06",
                from_state="CANDIDATE_FROZEN", to_state="EVIDENCE_COMPLETE",
                recorded_utc=now, facts=facts))
            # t-s09 (M4, completed by C3-M1): the Sequence enters VERIFYING
            # with the same evidence, and execution_receipt_complete is now
            # WHOLE — the join half (the t-a06 real join above, Chapter 2)
            # plus the classification half (the admitted COMPLETE
            # classification checked above). The receipt's DECLARED fields
            # are still never read here: only the admitted row speaks.
            if seq["state"] != "CANDIDATE_FROZEN":
                raise ConflictError(
                    "t-s09 expects the sequence CANDIDATE_FROZEN, found %s"
                    % seq["state"])
            version = self._cas_sequence(cur, seq, now, state="VERIFYING")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=att["sequence_id"], rule="t-s09",
                from_state="CANDIDATE_FROZEN", to_state="VERIFYING",
                recorded_utc=now,
                facts={"attempt_id": attempt_id, "version": version,
                       "fence": seq["fence"],
                       "execution_receipt_address": execution_receipt_address}))

    def fail_dispatched_attempt(self, attempt_id, reason):
        """t-a10 DISPATCHED→FAILED (launch_failed_after_call_id)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "DISPATCHED":
                raise ConflictError(
                    "fail_dispatched_attempt expects DISPATCHED, found %s"
                    % att["state"])
            self._cas_attempt(cur, att, "FAILED", now)
            facts = self._quadruple(att)
            facts["reason"] = reason
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a10",
                from_state="DISPATCHED", to_state="FAILED",
                recorded_utc=now, facts=facts))

    def _admitted_classification(self, cur, att, execution_receipt_address):
        """The receipt authority seam — LIVE since C3-M1 (it failed closed
        from the 2026-11-10 operator correction until the admission
        mechanism landed).

        Receipt-DECLARED fields still carry no authority: the facts
        returned here come from the ``attempt_receipts`` row the admission
        seam created after re-deriving the payload byte-identically from
        the ledger (D-C3-2), keyed by the presented receipt's exact
        content address. Presenting bytes that were never admitted grants
        nothing (cap-30)."""
        row = cur.execute(
            "SELECT completeness, evidence_complete_by_utc"
            " FROM attempt_receipts WHERE attempt_id = ?"
            " AND content_sha256 = ?",
            (att["attempt_id"], execution_receipt_address)).fetchone()
        if row is None:
            raise ConflictError(
                "the presented receipt is not admitted for this attempt:"
                " only the collector's admitted classification carries"
                " authority (cap-30, D-C3-2)")
        return {"completeness": row["completeness"],
                "evidence_complete_by_utc":
                    row["evidence_complete_by_utc"]}

    def fail_candidate(self, attempt_id, expected_fence,
                       execution_receipt_address, reason):
        """t-a13 CANDIDATE_FROZEN→FAILED, with t-s45 beside it — LIVE
        (C3-M1 closed the fail-closed seam).

        Enforced, in order: the caller's lease, the registered
        ``fence_matches`` (presented fence plus attempt↔sequence agreement,
        with the correlated equality in the mutating SQL, as on
        t-a05/t-a06), the CANDIDATE_FROZEN state, the receipt's identity
        binding (INV-68/INV-69: kind, attempt_id, attempt_contract_hash),
        and the ``_admitted_classification`` authority seam: the presented
        receipt must be ADMITTED, and the arms execute on the ADMITTED
        facts only — the registered
        ``execution_receipt_integrity_failed_or_incomplete_beyond_bound``:
        INTEGRITY_FAILED fails at once; INCOMPLETE fails only once its
        derived D-C3-1 bound has elapsed, never prematurely."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            seq = self._sequence(cur, att["sequence_id"])
            if expected_fence != seq["fence"] or att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"]))
            if att["state"] != "CANDIDATE_FROZEN":
                raise ConflictError(
                    "fail_candidate expects CANDIDATE_FROZEN, found %s"
                    % att["state"])
            self._resolve_receipt(cur, execution_receipt_address, att)
            admitted = self._admitted_classification(
                cur, att, execution_receipt_address)
            completeness = admitted.get("completeness")
            if completeness == "INTEGRITY_FAILED":
                bound_fact = None
            elif completeness == "INCOMPLETE":
                bound = admitted.get("evidence_complete_by_utc")
                if not isinstance(bound, str):
                    raise ConflictError(
                        "t-a13: the admitted INCOMPLETE classification"
                        " carries no derived completion bound")
                if bound > now:
                    raise ConflictError(
                        "t-a13: incomplete evidence is premature — the"
                        " completion bound %s has not elapsed" % bound)
                bound_fact = bound
            else:
                raise ConflictError(
                    "t-a13 requires an admitted classification of"
                    " INTEGRITY_FAILED or INCOMPLETE")
            self._cas_attempt(cur, att, "FAILED", now,
                              require_sequence_fence=True)
            facts = self._quadruple(att)
            facts.update({"execution_receipt_address": execution_receipt_address,
                          "completeness": completeness,
                          "reason": reason})
            if bound_fact is not None:
                facts["evidence_complete_by_utc"] = bound_fact
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a13",
                from_state="CANDIDATE_FROZEN", to_state="FAILED",
                recorded_utc=now, facts=facts))
            # t-s45 (M4): the Sequence HALTs on the same admitted
            # classification — registered beside t-a13 and, like it,
            # reachable only through the Chapter 3 seam above, so both
            # remain fail-closed PARTIAL until receipt admission lands.
            if seq["state"] == "CANDIDATE_FROZEN":
                version = self._cas_sequence(cur, seq, now, state="HALTED")
                journal.append(cur, journal.transition_record(
                    machine="sequence", subject_id=att["sequence_id"],
                    rule="t-s45", from_state="CANDIDATE_FROZEN",
                    to_state="HALTED", recorded_utc=now,
                    facts={"attempt_id": attempt_id, "version": version,
                           "fence": seq["fence"],
                           "completeness": completeness, "reason": reason}))

    # -------------------------------------------------------- verification
    #
    # The M4 surface: authoritative verification over admitted evidence.
    # Results enter ONLY through the typed admission seams below — the
    # verifier_runner's obj-19/obj-20 receipts (register_verifier_result)
    # and your obj-37 ManualAttestation.ASSERTION (attest_assertion, D-52) —
    # and the registered transitions (t-a07/t-a08 with t-s10/t-s11/t-s12)
    # consume those durable rows. The runner itself is Chapter 4's; this
    # runtime executes nothing.

    def _assertion(self, payload, assertion_id):
        entries = payload.get("acceptance_assertions", {}).get(
            "ids_and_environment_constraints", [])
        for entry in entries:
            if entry.get("assertion_id") == assertion_id:
                return entry
        raise ConflictError(
            "assertion %r is not declared by the frozen record" % assertion_id)

    def _bound_verifier_registration(self, cur, payload, verifier_id):
        """Resolve an assertion's verifier_id to its bound registration
        (SEM-07 at freeze). INV-53: a bound registration that no longer
        resolves — the ledger row or its exact bytes — HALTs and is never
        substituted. The closure's leaf artifacts are the Chapter 4
        runner's dispatch obligation."""
        for registration_hash in payload.get("resolved_bindings", {}).get(
                "verifier_registrations", []):
            row = cur.execute(
                "SELECT content_address FROM objects WHERE object_hash = ?",
                (registration_hash,)).fetchone()
            if row is None:
                raise IntegrityHalt(
                    "bound verifier registration %s is not in the ledger"
                    " (INV-53: never substituted)" % registration_hash)
            if not self.store.has(row["content_address"]):
                raise IntegrityHalt(
                    "bound verifier registration %s has lost its bytes"
                    " (INV-53/D-66 HALT)" % registration_hash)
            registration = json.loads(
                self.store.get(row["content_address"]).decode("utf-8"))
            if (registration.get("kind") == "VerifierRegistration"
                    and registration.get("verifier_id") == verifier_id):
                return registration
        raise IntegrityHalt(
            "assertion verifier %r has no bound registration in the frozen"
            " record (SEM-07 was enforced at freeze; the record is"
            " self-inconsistent)" % verifier_id)

    def _verification_context(self, cur, attempt_id, expected_fence=None):
        """The shared M4 gates: the attempt holds EVIDENCE_COMPLETE, its
        Sequence is VERIFYING, and the fences agree (presented fence too,
        when the registered row carries fence_matches)."""
        att = self._attempt(cur, attempt_id)
        seq = self._sequence(cur, att["sequence_id"])
        if expected_fence is not None and expected_fence != seq["fence"]:
            raise StaleFenceError(
                "fence %r is not the current fence %r (INV-13)"
                % (expected_fence, seq["fence"]))
        if att["fence"] != seq["fence"]:
            raise StaleFenceError(
                "attempt fence %r is not the sequence fence %r (fence_matches)"
                % (att["fence"], seq["fence"]))
        if att["state"] != "EVIDENCE_COMPLETE":
            raise ConflictError(
                "verification expects the attempt EVIDENCE_COMPLETE, found %s"
                % att["state"])
        if seq["state"] != "VERIFYING":
            raise ConflictError(
                "verification expects the sequence VERIFYING, found %s"
                % seq["state"])
        return att, seq

    def _existing_verification(self, cur, attempt_id, assertion_id,
                               outcome, receipt_address, env_address):
        """Duplicate result admissions: an identical re-admission is
        idempotent and journals nothing; a CONFLICTING one is refused —
        an assertion's authoritative result is written exactly once."""
        row = cur.execute(
            "SELECT * FROM attempt_verifications WHERE attempt_id = ?"
            " AND assertion_id = ?", (attempt_id, assertion_id)).fetchone()
        if row is None:
            return None
        if (row["outcome"] == outcome
                and row["receipt_sha256"] == receipt_address
                and row["environment_sha256"] == env_address):
            return {"verification_id": row["verification_id"],
                    "receipt_address": receipt_address, "duplicate": True}
        raise ConflictError(
            "assertion %r already has a different admitted result for this"
            " attempt (duplicate result admissions never overwrite)"
            % assertion_id)

    def register_verifier_result(self, attempt_id, assertion_id, outcome,
                                 by, receipt, environment=None):
        """Admit one verifier-runner result for a DETERMINISTIC assertion
        (the typed interface's write side): outcome PASS/FAIL/INCONCLUSIVE
        with the runner's exact receipt bytes (obj-19 on PASS, obj-20 on
        FAIL), eligibility re-evaluated from the registered requirements
        (SEM-16/D-57), the bound registration resolved or HALT (INV-53),
        and — when the assertion declares an environment constraint — the
        obj-18 capture bound to this exact attempt and matched against the
        declaration (a mismatch is refused; t-s12's env_mismatch arm is the
        sequence-level HALT the caller then applies)."""
        if outcome not in VERIFIER_OUTCOMES:
            raise ConflictError(
                "outcome %r is not in the verifier vocabulary %r"
                % (outcome, VERIFIER_OUTCOMES))
        if by != RESULT_PRODUCER:
            raise ConflictError(
                "%r is not the registered obj-19/obj-20 minter %r"
                % (by, RESULT_PRODUCER))
        receipt = bytes(receipt)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att, seq = self._verification_context(cur, attempt_id)
            payload = self._verified_record(cur, seq["record_hash"])
            assertion = self._assertion(payload, assertion_id)
            if assertion.get("mode") != "DETERMINISTIC":
                raise ConflictError(
                    "assertion %r is %s: it passes only by your attestation"
                    " (D-52), never by a verifier result"
                    % (assertion_id, assertion.get("mode")))
            registration = self._bound_verifier_registration(
                cur, payload, assertion.get("verifier_id"))
            refusal = verifier_eligibility(assertion, registration)
            if refusal is not None:
                raise ConflictError(refusal)
            env_address = None
            constraint = assertion.get("environment_constraint")
            if constraint is not None and environment is None:
                raise ConflictError(
                    "assertion %r declares an environment constraint:"
                    " the obj-18 capture is required" % assertion_id)
            if environment is not None:
                # Supplied evidence is never silently dropped (review
                # finding): it is scope-validated and stored whether or not
                # a constraint binds it.
                environment = bytes(environment)
                env_payload = json.loads(environment.decode("utf-8"))
                refusal = validate_environment_capture(env_payload, att)
                if refusal is not None:
                    raise ConflictError(refusal)
                if constraint is not None:
                    refusal = environment_satisfies(constraint, env_payload)
                    if refusal is not None:
                        raise ConflictError(
                            "environment mismatch (t-s12 env_mismatch): %s"
                            % refusal)
                env_address = self.store.put(environment)
            receipt_address = self.store.put(receipt)
            existing = self._existing_verification(
                cur, attempt_id, assertion_id, outcome, receipt_address,
                env_address)
            if existing is not None:
                return existing
            verification_id = mint_id("vr")
            cur.execute(
                "INSERT INTO attempt_verifications (verification_id,"
                " attempt_id, assertion_id, mode, verifier_id, outcome,"
                " by_principal, receipt_sha256, environment_sha256,"
                " recorded_utc) VALUES (?, ?, ?, 'DETERMINISTIC', ?, ?, ?,"
                " ?, ?, ?)",
                (verification_id, attempt_id, assertion_id,
                 assertion.get("verifier_id"), outcome, by, receipt_address,
                 env_address, now))
            facts = {"attempt_id": attempt_id,
                     "sequence_id": att["sequence_id"],
                     "assertion_id": assertion_id, "mode": "DETERMINISTIC",
                     "verifier_id": assertion.get("verifier_id"),
                     "outcome": outcome, "by": by,
                     "receipt_sha256": receipt_address,
                     "fence": att["fence"]}
            if env_address is not None:  # D-37
                facts["environment_sha256"] = env_address
            journal.append(cur, journal.transition_record(
                machine="attempt_verification", subject_id=verification_id,
                rule="ch2:register-verification", to_state="RECORDED",
                recorded_utc=now, facts=facts))
            return {"verification_id": verification_id,
                    "receipt_address": receipt_address, "duplicate": False}

    def attest_assertion(self, attempt_id, assertion_id, attestation):
        """D-52: your ManualAttestation.ASSERTION satisfies one
        HUMAN_ATTESTED assertion — durable identity (minted by you),
        provenance (the attested verdict, preserved by exact bytes and
        journaled) and scope binding (this attempt, this assertion, this
        contract hash). Recorded BEFORE conclude_verification can commit
        t-s10 (attestation_before_commit_if_source_human_required)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att, seq = self._verification_context(cur, attempt_id)
            payload = self._verified_record(cur, seq["record_hash"])
            assertion = self._assertion(payload, assertion_id)
            if assertion.get("mode") != "HUMAN_ATTESTED":
                raise ConflictError(
                    "assertion %r is %s: DETERMINISTIC assertions are"
                    " verified by the runner, never attested (D-52)"
                    % (assertion_id, assertion.get("mode")))
            refusal = validate_assertion_attestation(
                attestation, att, assertion_id)
            if refusal is not None:
                raise AdmissionError(refusal)
            # The D-29 kind-grammar finding on t-i07 applies identically:
            # obj-37's dotted kind has no object hash under Chapter 1 as
            # frozen, so the attestation is preserved by exact bytes (D-31).
            address = self.store.put(canonicalize(attestation))
            existing = self._existing_verification(
                cur, attempt_id, assertion_id, "ATTESTED", address, None)
            if existing is not None:
                return existing
            verification_id = mint_id("vr")
            cur.execute(
                "INSERT INTO attempt_verifications (verification_id,"
                " attempt_id, assertion_id, mode, verifier_id, outcome,"
                " by_principal, receipt_sha256, environment_sha256,"
                " recorded_utc) VALUES (?, ?, ?, 'HUMAN_ATTESTED', ?,"
                " 'ATTESTED', ?, ?, NULL, ?)",
                (verification_id, attempt_id, assertion_id,
                 assertion.get("verifier_id"), ATTESTATION_PRODUCER,
                 address, now))
            journal.append(cur, journal.transition_record(
                machine="attempt_verification", subject_id=verification_id,
                rule="ch2:attest-assertion", to_state="RECORDED",
                recorded_utc=now,
                facts={"attempt_id": attempt_id,
                       "sequence_id": att["sequence_id"],
                       "assertion_id": assertion_id,
                       "mode": "HUMAN_ATTESTED", "outcome": "ATTESTED",
                       "by": ATTESTATION_PRODUCER,
                       "receipt_sha256": address, "attests": "PASS",
                       "fence": att["fence"]}))
            return {"verification_id": verification_id,
                    "receipt_address": address, "duplicate": False}

    def conclude_verification(self, attempt_id, expected_fence):
        """The registered verification outcomes, from admitted rows only.

        t-a07 + t-s10 (all assertions PASS or ATTESTED, with the D-52
        attestation already durable — required_assertions_pass_or_attested,
        bound_registered_artifacts_only by the admission seams above,
        attestation_before_commit_if_source_human_required by
        construction); or t-a08 (any assertion FAIL, obj-20) with t-s11
        (repair_budget_left: repairs_used < the frozen
        repair_policy.max_repairs) or t-s12 (budget_exhausted). An
        INCONCLUSIVE row concludes nothing: it is t-s12's "unknown" arm,
        applied by halt_verification."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att, seq = self._verification_context(cur, attempt_id,
                                                  expected_fence)
            payload = self._verified_record(cur, seq["record_hash"])
            declared = payload.get("acceptance_assertions", {}).get(
                "ids_and_environment_constraints", [])
            rows = {row["assertion_id"]: row for row in cur.execute(
                "SELECT * FROM attempt_verifications WHERE attempt_id = ?",
                (attempt_id,)).fetchall()}
            missing = [a["assertion_id"] for a in declared
                       if a["assertion_id"] not in rows]
            if missing:
                raise ConflictError(
                    "unresolved assertions: %s (every declared assertion"
                    " needs an admitted result before conclusion)"
                    % ", ".join(sorted(missing)))
            inconclusive = sorted(
                a_id for a_id, row in rows.items()
                if row["outcome"] == "INCONCLUSIVE")
            if inconclusive:
                raise ConflictError(
                    "assertions %s are INCONCLUSIVE: conclusion is"
                    " impossible — t-s12's unknown arm (halt_verification)"
                    " is the registered exit" % ", ".join(inconclusive))
            failed = sorted(a_id for a_id, row in rows.items()
                            if row["outcome"] == "FAIL")
            attested = sorted(a_id for a_id, row in rows.items()
                              if row["outcome"] == "ATTESTED")
            record_row = cur.execute(
                "SELECT eligibility FROM records WHERE record_hash = ?",
                (seq["record_hash"],)).fetchone()
            if failed:
                failure_receipts = [rows[a_id]["receipt_sha256"]
                                    for a_id in failed]
                self._cas_attempt(cur, att, "REJECTED", now,
                                  require_sequence_fence=True)
                facts = self._quadruple(att)
                facts.update({"failed_assertion_ids": failed,
                              "failure_receipts": failure_receipts})
                journal.append(cur, journal.transition_record(
                    machine="attempt", subject_id=attempt_id, rule="t-a08",
                    from_state="EVIDENCE_COMPLETE", to_state="REJECTED",
                    recorded_utc=now, facts=facts))
                max_repairs = payload.get("repair_policy", {}).get(
                    "max_repairs", 0)
                if seq["repairs_used"] < max_repairs:
                    version = self._cas_sequence(
                        cur, seq, now, state="READY",
                        current_attempt_id=None,
                        repairs_used=seq["repairs_used"] + 1)
                    journal.append(cur, journal.transition_record(
                        machine="sequence", subject_id=att["sequence_id"],
                        rule="t-s11", from_state="VERIFYING",
                        to_state="READY", recorded_utc=now,
                        facts={"attempt_id": attempt_id, "version": version,
                               "fence": seq["fence"],
                               "failed_assertion_ids": failed,
                               "failure_receipts": failure_receipts,
                               "repairs_used": seq["repairs_used"] + 1,
                               "max_repairs": max_repairs}))
                    sequence_state = "READY"
                else:
                    version = self._cas_sequence(cur, seq, now,
                                                 state="HALTED")
                    journal.append(cur, journal.transition_record(
                        machine="sequence", subject_id=att["sequence_id"],
                        rule="t-s12", from_state="VERIFYING",
                        to_state="HALTED", recorded_utc=now,
                        facts={"attempt_id": attempt_id, "version": version,
                               "fence": seq["fence"],
                               "reason": "budget_exhausted",
                               "failed_assertion_ids": failed,
                               "repairs_used": seq["repairs_used"],
                               "max_repairs": max_repairs}))
                    sequence_state = "HALTED"
                return {"attempt": "REJECTED", "sequence": sequence_state}
            pass_receipts = [rows[a["assertion_id"]]["receipt_sha256"]
                             for a in declared]
            self._cas_attempt(cur, att, "VERIFIED", now,
                              require_sequence_fence=True)
            facts = self._quadruple(att)
            facts.update({"assertion_ids": [a["assertion_id"]
                                            for a in declared],
                          "verification_receipts": pass_receipts})
            if attested:  # D-37
                facts["attested_assertion_ids"] = attested
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a07",
                from_state="EVIDENCE_COMPLETE", to_state="VERIFIED",
                recorded_utc=now, facts=facts))
            version = self._cas_sequence(cur, seq, now, state="VERIFIED")
            seq_facts = {"attempt_id": attempt_id, "version": version,
                         "fence": seq["fence"],
                         "eligibility": record_row["eligibility"],
                         "verification_receipts": pass_receipts}
            if attested:  # attestation_before_commit_if_source_human_required
                seq_facts["attested_assertion_ids"] = attested
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=att["sequence_id"],
                rule="t-s10", from_state="VERIFYING", to_state="VERIFIED",
                recorded_utc=now, facts=seq_facts))
            return {"attempt": "VERIFIED", "sequence": "VERIFIED"}

    def halt_verification(self, sequence_id, reason):
        """t-s12 VERIFYING→HALTED on the registered reasons — the DURABLE
        halt. The seams that detect these conditions mutate nothing
        themselves: an environment mismatch or an INCONCLUSIVE conclusion
        is a refusal (ConflictError; journal and Sequence unchanged), and a
        missing bound artifact is an integrity stop (IntegrityHalt, INV-53;
        processing stops with no transition committed). THIS transition is
        how such an observation becomes durable Sequence state, journaled
        with its reason; budget_exhausted also commits automatically inside
        conclude_verification. The attempt HOLDS in EVIDENCE_COMPLETE —
        HALTED is a human_queue state whose resume (t-s13, your budget
        grant or attestation) is outside this milestone."""
        reasons = ("unknown", "env_mismatch", "budget_exhausted",
                   "bound_artifact_unavailable")
        if reason not in reasons:
            raise ConflictError(
                "reason %r is not a registered t-s12 arm %r"
                % (reason, reasons))
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "VERIFYING":
                raise ConflictError(
                    "halt_verification expects VERIFYING, found %s"
                    % seq["state"])
            version = self._cas_sequence(cur, seq, now, state="HALTED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s12",
                from_state="VERIFYING", to_state="HALTED", recorded_utc=now,
                facts={"version": version, "fence": seq["fence"],
                       "reason": reason}))
            return version

    def mark_member_stale(self, attempt_id):
        """t-a12 RUNNING→STALE (cap-16): the registered
        lease_expired_required_member_without_result — the bound call's
        member lease has expired, the dispatched role is a REQUIRED member
        of the frozen completion policy, and no result exists (the attempt
        is still RUNNING: t-a04/t-a11 never fired). The sequence's retry
        path is the pre-existing t-s08 (retry_sequence)."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "RUNNING":
                raise ConflictError(
                    "mark_member_stale expects RUNNING, found %s"
                    % att["state"])
            call = self._attempt_call(cur, attempt_id)
            expiry = call["member_lease_expires_utc"]
            if expiry is None:
                raise ConflictError(
                    "no member lease recorded for attempt %s" % attempt_id)
            if expiry > now:
                raise ConflictError(
                    "member lease runs until %s: staleness is proven by"
                    " expiry, never asserted early" % expiry)
            seq = self._sequence(cur, att["sequence_id"])
            payload = self._verified_record(cur, seq["record_hash"])
            policy = payload["completion_policies"].get(call["role"], {})
            if not policy.get("required", False):
                raise ConflictError(
                    "role %r is not a required member: t-a12 is the"
                    " required-member staleness arm" % call["role"])
            self._cas_attempt(cur, att, "STALE", now)
            facts = self._quadruple(att)
            facts.update({"role": call["role"], "call_id": call["call_id"],
                          "member_lease_expires_utc": expiry})
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a12",
                from_state="RUNNING", to_state="STALE", recorded_utc=now,
                facts=facts))
            return "STALE"

    # -------------------------------------------------------- finalization
    #
    # The M5 surface: the SL finalization path VERIFIED → COMMITTING →
    # COMMITTED driven by REAL EffectIntent outcomes. t-s15a mints the
    # obj-24 reservation and the obj-25 intent in its own transaction (the
    # registry's mints), the external effect runs only off
    # committed_intent() (D-71, the git_executor module), the outcome is
    # observation-driven through the registered intent machine, and the
    # sequence advances only from durable outcomes: t-s16 (HAPPENED,
    # consuming the reservation at the registered PONR point, t-r01),
    # t-s19 (ABSENT with the ref unmoved: back to VERIFIED to re-mint) or
    # t-s18 (ABSENT because the canonical ref moved: the candidate's base
    # is stale, back to READY). Remote arcs (t-s15b, t-s17, t-s21..) are
    # later milestones.

    def _delegation_active(self, cur, payload, now, label):
        delegation_hash = payload.get("resolved_bindings", {}).get(
            "delegation_contract")
        if delegation_hash is None:
            return
        obj = cur.execute(
            "SELECT content_address FROM objects WHERE object_hash = ?",
            (delegation_hash,)).fetchone()
        if obj is None:
            raise IntegrityHalt(
                "bound delegation %s not in the ledger" % delegation_hash)
        delegation = json.loads(
            self.store.get(obj["content_address"]).decode("utf-8"))
        if delegation.get("expires_utc", "") <= now:
            raise ConflictError(
                "bound delegation expired: not %s (t-d02)" % label)

    def _current_attempt(self, cur, seq):
        if seq["current_attempt_id"] is None:
            raise ConflictError(
                "sequence %s has no current attempt" % seq["sequence_id"])
        return self._attempt(cur, seq["current_attempt_id"])

    def _finalization_intent(self, cur, seq, state):
        """The committing arc's intent: the LATEST eff-local-canonical-cas
        intent bound to the sequence's current attempt, which must be in
        ``state`` (durable outcome, never a caller claim)."""
        # Insertion order (rowid) is the durable total order: created_utc
        # ties when two intents mint in the same instant (F-M5-1).
        row = cur.execute(
            "SELECT * FROM effect_intents WHERE sequence_id = ?"
            " AND attempt_id = ? AND effect_id = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (seq["sequence_id"], seq["current_attempt_id"],
             "eff-local-canonical-cas")).fetchone()
        if row is None:
            raise ConflictError(
                "no finalization intent exists for sequence %s"
                % seq["sequence_id"])
        if row["state"] != state:
            raise ConflictError(
                "finalization intent %s is %s, not %s: outcomes are durable"
                " intent state, never asserted"
                % (row["intent_id"], row["state"], state))
        return row

    def _intent_observation(self, cur, intent_id):
        """The observation that resolved an intent, read from its journaled
        transition (durable fact, not a caller claim)."""
        observation = None
        for payload in journal.entries(cur):
            if (payload.get("machine") == "effect_intent"
                    and payload.get("subject_id") == intent_id):
                observation = payload.get("facts", {}).get("observation",
                                                           observation)
        return observation

    def begin_commit(self, sequence_id, attempt_id, target, expected_old,
                     new_value, lease_seconds=7200, ttl_seconds=None,
                     hard_timeout_seconds=None):
        """t-s15a VERIFIED→COMMITTING (SL path): mints the obj-24
        PermitUseReservation and the obj-25 EffectIntent for
        eff-local-canonical-cas in this one transaction — the registered
        mints — under the full attempt-scoped checks: source_modifying
        (and authority_location LOCAL: the remote arcs are deferred),
        permit_reserved (INV-48 counters), delegation_active_at_intent,
        cas_state_version_attempt_fence and fence_matches (conditions of
        the mutating SQL), effect_intent_committed (this transaction) and
        intent_ttl_gt_hard_timeout (SEM-12). The external effect may begin
        only from committed_intent() afterwards."""
        if not (isinstance(target, str) and target
                and isinstance(expected_old, str) and expected_old
                and isinstance(new_value, str) and new_value):
            raise ConflictError(
                "the local CAS needs non-empty target, expected_old and"
                " new_value")
        now_dt = self.clock.now()
        now = instant(now_dt)
        lease_expires = instant(now_dt + timedelta(seconds=lease_seconds))
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "VERIFIED":
                raise ConflictError(
                    "begin_commit expects VERIFIED, found %s" % seq["state"])
            att = self._current_attempt(cur, seq)
            if att["attempt_id"] != attempt_id:
                raise ConflictError(
                    "attempt %s is not the sequence's verified attempt %s"
                    % (attempt_id, att["attempt_id"]))
            if att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "attempt fence %r is not the sequence fence %r"
                    " (fence_matches)" % (att["fence"], seq["fence"]))
            payload = self._verified_record(cur, seq["record_hash"])
            if payload.get("finalization_class") != "SOURCE_MODIFYING":
                raise ConflictError(
                    "t-s15a requires source_modifying; the record is %r"
                    % payload.get("finalization_class"))
            if payload["source_domain"].get("authority_location") != "LOCAL":
                raise ConflictError(
                    "M5 implements the SL path only (authority_location"
                    " LOCAL); remote finalization arcs are deferred")
            self._delegation_active(cur, payload, now,
                                    "delegation_active_at_intent")
            # permit_reserved: mint the obj-24 reservation under INV-48.
            cur.execute(
                "UPDATE permit_state SET reserved_uses = reserved_uses + 1,"
                " version = version + 1 WHERE sequence_id = ?"
                " AND consumed_uses + reserved_uses < permitted_uses",
                (sequence_id,))
            if cur.rowcount != 1:
                raise PermitExhaustedError(
                    "no permit use available for %s (INV-48)" % sequence_id)
            reservation_id = mint_id("rv")
            cur.execute(
                "INSERT INTO reservations (reservation_id, sequence_id,"
                " attempt_id, state, lease_expires_utc, created_utc,"
                " updated_utc) VALUES (?, ?, ?, 'RESERVED', ?, ?, ?)",
                (reservation_id, sequence_id, attempt_id, lease_expires,
                 now, now))
            journal.append(cur, journal.transition_record(
                machine="permit_reservation", subject_id=reservation_id,
                rule="ch2:reserve-use", to_state="RESERVED",
                recorded_utc=now,
                facts={"sequence_id": sequence_id, "attempt_id": attempt_id,
                       "lease_expires_utc": lease_expires,
                       "minted_by": "t-s15a"}))
            expires, hard_to = self._intent_windows(
                payload, now_dt, ttl_seconds, hard_timeout_seconds)
            intent_id = mint_id("in")
            cur.execute(
                "INSERT INTO effect_intents (intent_id, effect_id,"
                " intent_class, state, sequence_id, attempt_id, fence,"
                " reservation_id, created_utc, expires_utc,"
                " hard_timeout_utc, version, target, expected_old,"
                " new_value) VALUES (?, 'eff-local-canonical-cas',"
                " 'attempt_scoped', 'PENDING', ?, ?, ?, ?, ?, ?, ?, 1,"
                " ?, ?, ?)",
                (intent_id, sequence_id, attempt_id, att["fence"],
                 reservation_id, now, expires, hard_to, target,
                 expected_old, new_value))
            version = self._cas_sequence(cur, seq, now, state="COMMITTING")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s15a",
                from_state="VERIFIED", to_state="COMMITTING",
                recorded_utc=now,
                facts={"attempt_id": attempt_id, "intent_id": intent_id,
                       "reservation_id": reservation_id,
                       "effect_id": "eff-local-canonical-cas",
                       "target": target, "expected_old": expected_old,
                       "new_value": new_value, "expires_utc": expires,
                       "hard_timeout_utc": hard_to, "version": version,
                       "fence": seq["fence"],
                       "mints": ["obj-24", "obj-25"]}))
            return {"intent_id": intent_id, "reservation_id": reservation_id}

    def commit_committed(self, sequence_id, expected_fence, commit_receipt):
        """t-s16 COMMITTING→COMMITTED — the SL PONR row: effect_happened
        (the finalization intent is durably HAPPENED), fence_matches, and
        reservation_consumed — t-r01 (ponr_effect_happened, exactly one
        RESERVED row moves once) commits in this same transaction, which is
        the reservation consumption's registered durable point. Requires
        obj-26: the git_sequencer's CommitReceipt bytes, identity-bound to
        the intent, preserved by exact bytes; the payload SCHEMA stays with
        obj-26's owning later chapter."""
        commit_receipt = bytes(commit_receipt)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if expected_fence != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"]))
            if seq["state"] != "COMMITTING":
                raise ConflictError(
                    "commit_committed expects COMMITTING, found %s"
                    % seq["state"])
            att = self._current_attempt(cur, seq)
            if att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "attempt fence %r is not the sequence fence %r"
                    " (fence_matches)" % (att["fence"], seq["fence"]))
            intent = self._finalization_intent(cur, seq, "HAPPENED")
            receipt = json.loads(commit_receipt.decode("utf-8"))
            if (receipt.get("kind") != "CommitReceipt"
                    or receipt.get("intent_id") != intent["intent_id"]
                    or receipt.get("target") != intent["target"]
                    or receipt.get("new_value") != intent["new_value"]):
                raise ConflictError(
                    "obj-26 receipt must bind the finalization intent's"
                    " identity (kind, intent_id, target, new_value)")
            receipt_address = self.store.put(commit_receipt)
            # t-r01: the PONR effect happened; exactly one RESERVED row
            # moves to CONSUMED, durably, in this transaction.
            cur.execute(
                "UPDATE reservations SET state = 'CONSUMED', updated_utc = ?"
                " WHERE reservation_id = ? AND state = 'RESERVED'",
                (now, intent["reservation_id"]))
            if cur.rowcount != 1:
                raise ConflictError(
                    "reservation %s is not RESERVED (t-r01:"
                    " row_status_reserved_rowcount_1)"
                    % intent["reservation_id"])
            cur.execute(
                "UPDATE permit_state SET consumed_uses = consumed_uses + 1,"
                " reserved_uses = reserved_uses - 1, version = version + 1"
                " WHERE sequence_id = ?", (sequence_id,))
            journal.append(cur, journal.transition_record(
                machine="permit_reservation",
                subject_id=intent["reservation_id"], rule="t-r01",
                from_state="RESERVED", to_state="CONSUMED",
                recorded_utc=now,
                facts={"sequence_id": sequence_id,
                       "cause": "ponr_effect_happened (t-s16)"}))
            version = self._cas_sequence(cur, seq, now, state="COMMITTED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s16",
                from_state="COMMITTING", to_state="COMMITTED",
                recorded_utc=now,
                facts={"attempt_id": att["attempt_id"],
                       "intent_id": intent["intent_id"],
                       "reservation_id": intent["reservation_id"],
                       "commit_receipt_address": receipt_address,
                       "effect": "eff-local-canonical-cas",
                       "version": version, "fence": seq["fence"],
                       "ponr": True}))
            return receipt_address

    def _release_finalization_reservation(self, cur, intent, now):
        """t-r02, idempotent: the registered reservation_released
        precondition of t-s18/t-s19."""
        cur.execute(
            "UPDATE reservations SET state = 'RELEASED', updated_utc = ?"
            " WHERE reservation_id = ? AND state = 'RESERVED'",
            (now, intent["reservation_id"]))
        if cur.rowcount == 1:
            cur.execute(
                "UPDATE permit_state SET reserved_uses = reserved_uses - 1,"
                " version = version + 1 WHERE sequence_id = ?",
                (intent["sequence_id"],))
            journal.append(cur, journal.transition_record(
                machine="permit_reservation",
                subject_id=intent["reservation_id"], rule="t-r02",
                from_state="RESERVED", to_state="RELEASED",
                recorded_utc=now,
                facts={"sequence_id": intent["sequence_id"],
                       "cause": "pre_ponr_exit"}))

    def commit_absent(self, sequence_id):
        """t-s19 COMMITTING→VERIFIED: effect_absent_per_outcome_rule with
        the ref UNMOVED — the intent is durably ABSENT and its journaled
        observation is the fenced expected-old form, so the same VERIFIED
        candidate may re-mint and retry; the reservation is released
        (t-r02) in this transaction."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "COMMITTING":
                raise ConflictError(
                    "commit_absent expects COMMITTING, found %s"
                    % seq["state"])
            intent = self._finalization_intent(cur, seq, "ABSENT")
            observation = self._intent_observation(cur, intent["intent_id"])
            if observation != ("target_equals_expected_old_and_executor_"
                               "fenced_and_no_ref_lock"):
                raise ConflictError(
                    "t-s19 requires absence with the target at its expected"
                    " old value; the journaled observation is %r (a moved"
                    " ref is t-s18)" % observation)
            self._release_finalization_reservation(cur, intent, now)
            version = self._cas_sequence(cur, seq, now, state="VERIFIED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s19",
                from_state="COMMITTING", to_state="VERIFIED",
                recorded_utc=now,
                facts={"attempt_id": seq["current_attempt_id"],
                       "intent_id": intent["intent_id"],
                       "observation": observation, "version": version,
                       "fence": seq["fence"]}))
            return version

    def commit_stale(self, sequence_id, observation_receipt):
        """t-s18 COMMITTING→READY: canonical_ref_moved_expected_old_stale —
        the intent is durably ABSENT because the observation was
        target_other (the canonical ref moved), so the frozen candidate's
        base is stale and a NEW attempt is required. Requires obj-26: the
        git_sequencer's observation receipt of the moved ref, preserved by
        exact bytes; the reservation is released (t-r02) and fence_matches
        holds in the mutating SQL."""
        observation_receipt = bytes(observation_receipt)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "COMMITTING":
                raise ConflictError(
                    "commit_stale expects COMMITTING, found %s"
                    % seq["state"])
            att = self._current_attempt(cur, seq)
            if att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "attempt fence %r is not the sequence fence %r"
                    " (fence_matches)" % (att["fence"], seq["fence"]))
            intent = self._finalization_intent(cur, seq, "ABSENT")
            observation = self._intent_observation(cur, intent["intent_id"])
            if observation != "target_other":
                raise ConflictError(
                    "t-s18 requires the canonical ref observed at another"
                    " value; the journaled observation is %r" % observation)
            receipt = json.loads(observation_receipt.decode("utf-8"))
            if (receipt.get("kind") != "CommitReceipt"
                    or receipt.get("intent_id") != intent["intent_id"]
                    or receipt.get("target") != intent["target"]):
                raise ConflictError(
                    "obj-26 observation receipt must bind the finalization"
                    " intent's identity")
            receipt_address = self.store.put(observation_receipt)
            self._release_finalization_reservation(cur, intent, now)
            version = self._cas_sequence(cur, seq, now, state="READY",
                                         current_attempt_id=None)
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s18",
                from_state="COMMITTING", to_state="READY",
                recorded_utc=now,
                facts={"attempt_id": att["attempt_id"],
                       "intent_id": intent["intent_id"],
                       "observation": observation,
                       "observation_receipt_address": receipt_address,
                       "version": version, "fence": seq["fence"]}))
            return version

    # ------------------------------------------- acceptance and release
    #
    # The M6 surface: acceptance as a deterministic authority decision
    # over durable admitted facts (t-s20 for the SL path, t-s14 for
    # READ_ONLY — both minting obj-30, derived only from ledger state),
    # dependency release through its own durable authority path (t-s41:
    # the released_edges rows plus the obj-35 receipt, never a silent
    # unlock), completion (t-s42), and readiness consumption of released
    # edges (t-s02/t-s03, all_edges_satisfied_by_declared_tier). The
    # remote and reuse acceptance arms (t-s37/t-s39/t-s43/t-s44) are
    # deferred with their evidence families. obj-30/obj-35 payload
    # SCHEMAS belong to their owning later chapter; Chapter 2 owns the
    # transitions and the durable, content-addressed references.

    def _sequence_rule_facts(self, cur, sequence_id, rules):
        """The LAST journaled facts for this sequence under one of the
        given rules — durable finalization facts, never caller claims."""
        found = None
        for payload in journal.entries(cur):
            if (payload.get("machine") == "sequence"
                    and payload.get("subject_id") == sequence_id
                    and payload.get("rule") in rules):
                found = payload.get("facts", {})
        return found

    def _assert_finalizer_supported(self, payload):
        """finalizer_supported_in_v1 (INV-64, enforced at both t-s02 and
        t-s03): only the frozen finalization_class vocabulary v1 can
        drive; the mediated-publication arm (t-s04) is REMOTE-path and
        deferred with it. No quarantine exists in this runtime, so
        not_quarantined_or_is_repair holds vacuously."""
        if payload.get("finalization_class") not in (
                "READ_ONLY", "SOURCE_MODIFYING"):
            raise ConflictError(
                "finalizer_supported_in_v1 fails for %r"
                % payload.get("finalization_class"))

    def _edges_satisfied(self, cur, payload):
        """all_edges_satisfied_by_declared_tier over DURABLE released
        edges: each frozen obj-07 edge (edge_kind RECORD/CONTRACT/
        REQUIREMENT, target digest) is satisfied by a released_edges row
        of the same kind and value whose predecessor durably released
        (DEPENDENCIES_RELEASED or COMPLETED). ACCEPTANCE_FRESH edges fail
        closed: their freshness semantics belong to the reuse/dominance
        rules and are deferred."""
        unsatisfied, satisfied = [], []
        for edge in payload.get("predecessors", []):
            if edge.get("observation_requirement") != "ACCEPTANCE":
                unsatisfied.append(edge.get("edge_id"))
                continue
            row = cur.execute(
                "SELECT r.release_id, r.predecessor_sequence_id"
                " FROM released_edges r JOIN sequences s"
                " ON s.sequence_id = r.predecessor_sequence_id"
                " WHERE r.edge_kind = ? AND r.edge_value = ?"
                " AND s.state IN ('DEPENDENCIES_RELEASED','COMPLETED')"
                " ORDER BY r.release_id LIMIT 1",
                (edge.get("edge_kind"), edge.get("target"))).fetchone()
            if row is None:
                unsatisfied.append(edge.get("edge_id"))
            else:
                satisfied.append({
                    "edge_id": edge.get("edge_id"),
                    "edge_kind": edge.get("edge_kind"),
                    "target": edge.get("target"),
                    "release_id": row["release_id"],
                    "predecessor_sequence_id":
                        row["predecessor_sequence_id"]})
        return unsatisfied, satisfied

    def _acceptance_verification_facts(self, cur, att):
        """The durable verification facts acceptance references: every
        admitted result for the VERIFIED attempt (obj-19/obj-37 receipt
        addresses), which must exist and be PASS or ATTESTED."""
        rows = cur.execute(
            "SELECT assertion_id, outcome, receipt_sha256"
            " FROM attempt_verifications WHERE attempt_id = ?"
            " ORDER BY assertion_id", (att["attempt_id"],)).fetchall()
        if not rows:
            raise IntegrityHalt(
                "attempt %s is VERIFIED with no admitted verification"
                " results" % att["attempt_id"])
        for row in rows:
            if row["outcome"] not in ("PASS", "ATTESTED"):
                raise IntegrityHalt(
                    "attempt %s carries a non-passing verification result"
                    " yet is VERIFIED" % att["attempt_id"])
        return [{"assertion_id": r["assertion_id"], "outcome": r["outcome"],
                 "receipt_sha256": r["receipt_sha256"]} for r in rows]

    def _mint_acceptance(self, cur, seq, att, path, now, extra):
        """obj-30 AcceptanceReceipt (minter: sequencer): deterministic
        bytes binding the exact Sequence, frozen record identity tiers,
        Attempt, finalization path and the authoritative facts."""
        verification = self._acceptance_verification_facts(cur, att)
        record_row = cur.execute(
            "SELECT eligibility FROM records WHERE record_hash = ?",
            (seq["record_hash"],)).fetchone()
        if record_row["eligibility"] == "HUMAN_REQUIRED":
            attested = [v for v in verification if v["outcome"] == "ATTESTED"]
            if not attested:
                raise ConflictError(
                    "HUMAN_REQUIRED acceptance needs your durable obj-37"
                    " attestation among the verification facts")
        receipt = {"schema_version": "v1", "kind": "AcceptanceReceipt",
                   "sequence_id": seq["sequence_id"],
                   "record_hash": seq["record_hash"],
                   "requirement_digest": seq["requirement_digest"],
                   "contract_digest": seq["contract_digest"],
                   "attempt_id": att["attempt_id"],
                   "attempt_contract_hash": att["attempt_contract_hash"],
                   "finalization_path": path, "fence": seq["fence"],
                   "eligibility": record_row["eligibility"],
                   "verification_receipts": verification}
        receipt.update(extra)
        data = canonicalize(receipt)
        return self.store.put(data), verification

    def accept_commit(self, sequence_id, expected_fence):
        """t-s20 COMMITTED→ACCEPTED (SL): a deterministic authority
        decision over durable admitted facts — the committed finalization
        (the journaled t-s16 facts and their resolvable obj-26 bytes, the
        HAPPENED intent), the VERIFIED attempt's admitted verification
        results, the derived eligibility (a HUMAN_REQUIRED record must
        carry your attestation among them), and every registered
        finalization requirement satisfied by this path. Mints obj-30 and
        journals its address; a second invocation is refused at the state
        gate with the existing receipt identity intact in the journal."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if expected_fence != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"]))
            if seq["state"] != "COMMITTED":
                raise ConflictError(
                    "accept_commit expects COMMITTED, found %s (a"
                    " superseded, abandoned or already accepted sequence"
                    " is refused here)" % seq["state"])
            att = self._current_attempt(cur, seq)
            if att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "attempt fence %r is not the sequence fence %r"
                    " (fence_matches)" % (att["fence"], seq["fence"]))
            payload = self._verified_record(cur, seq["record_hash"])
            satisfied = {"LOCAL_CANONICAL_COMMIT"}
            missing = [req for req in payload.get(
                "finalization_requirements", []) if req not in satisfied]
            if missing:
                raise ConflictError(
                    "finalization_requirements_satisfied fails: %s are not"
                    " satisfiable on the SL path yet (their arcs are"
                    " deferred)" % ", ".join(sorted(missing)))
            committed = self._sequence_rule_facts(cur, sequence_id,
                                                  ("t-s16",))
            if committed is None or "commit_receipt_address" not in committed:
                raise IntegrityHalt(
                    "sequence %s is COMMITTED without journaled t-s16"
                    " facts (obj-26)" % sequence_id)
            self.store.get(committed["commit_receipt_address"])  # D-66
            self._finalization_intent(cur, seq, "HAPPENED")
            address, verification = self._mint_acceptance(
                cur, seq, att, "SL", now,
                {"finalization_requirements":
                     payload.get("finalization_requirements", []),
                 "commit_receipt_sha256":
                     committed["commit_receipt_address"]})
            version = self._cas_sequence(cur, seq, now, state="ACCEPTED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s20",
                from_state="COMMITTED", to_state="ACCEPTED",
                recorded_utc=now,
                facts={"attempt_id": att["attempt_id"],
                       "acceptance_sha256": address,
                       "commit_receipt_sha256":
                           committed["commit_receipt_address"],
                       "verification_count": len(verification),
                       "version": version, "fence": seq["fence"],
                       "mints": ["obj-30"]}))
            return address

    def accept_verified(self, sequence_id, expected_fence):
        """t-s14 VERIFIED→ACCEPTED (the READ_ONLY path, a registered PONR
        row): read_only (the frozen record's finalization_class),
        automatic_or_attested (derived eligibility; HUMAN_REQUIRED needs
        your durable attestation among the admitted results),
        fence_matches; requires obj-19 (the admitted verification
        receipts), mints obj-30."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if expected_fence != seq["fence"]:
                raise StaleFenceError(
                    "fence %r is not the current fence %r (INV-13)"
                    % (expected_fence, seq["fence"]))
            if seq["state"] != "VERIFIED":
                raise ConflictError(
                    "accept_verified expects VERIFIED, found %s"
                    % seq["state"])
            att = self._current_attempt(cur, seq)
            if att["fence"] != seq["fence"]:
                raise StaleFenceError(
                    "attempt fence %r is not the sequence fence %r"
                    " (fence_matches)" % (att["fence"], seq["fence"]))
            payload = self._verified_record(cur, seq["record_hash"])
            if payload.get("finalization_class") != "READ_ONLY":
                raise ConflictError(
                    "t-s14 is the READ_ONLY acceptance; a SOURCE_MODIFYING"
                    " sequence finalizes first (t-s15a..t-s16, then t-s20)")
            address, verification = self._mint_acceptance(
                cur, seq, att, "RO", now, {})
            version = self._cas_sequence(cur, seq, now, state="ACCEPTED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s14",
                from_state="VERIFIED", to_state="ACCEPTED",
                recorded_utc=now,
                facts={"attempt_id": att["attempt_id"],
                       "acceptance_sha256": address,
                       "verification_count": len(verification),
                       "version": version, "fence": seq["fence"],
                       "ponr": True, "mints": ["obj-30"]}))
            return address

    def release_dependencies(self, sequence_id):
        """t-s41 ACCEPTED→DEPENDENCIES_RELEASED: acceptance alone unlocks
        nothing — this transition writes the DURABLE released edges (one
        row per identity tier the acceptance can satisfy: RECORD /
        CONTRACT / REQUIREMENT), mints the obj-35 DependencyReleaseReceipt
        binding the predecessor identity, the acceptance reference and the
        successors known at release time, and journals it all in one
        transaction (successor_edges_durably_satisfied). A second
        invocation is refused at the state gate; the unique tier index
        makes conflicting rows impossible."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "ACCEPTED":
                raise ConflictError(
                    "release_dependencies expects ACCEPTED, found %s"
                    " (duplicate releases are refused; readiness is never"
                    " duplicated)" % seq["state"])
            accepted = self._sequence_rule_facts(cur, sequence_id,
                                                 ("t-s14", "t-s20"))
            if accepted is None or "acceptance_sha256" not in accepted:
                raise IntegrityHalt(
                    "sequence %s is ACCEPTED without a journaled obj-30"
                    " reference" % sequence_id)
            acceptance = accepted["acceptance_sha256"]
            self.store.get(acceptance)  # D-66
            tiers = (("RECORD", seq["record_hash"]),
                     ("CONTRACT", seq["contract_digest"]),
                     ("REQUIREMENT", seq["requirement_digest"]))
            # Successors known now, recorded in the receipt and journal;
            # later-admitted successors consume the same durable rows.
            successors = []
            for row in cur.execute(
                    "SELECT s.sequence_id, r.content_address"
                    " FROM sequences s JOIN records r"
                    " ON r.record_hash = s.record_hash"
                    " WHERE s.sequence_id != ?",
                    (sequence_id,)).fetchall():
                edges = json.loads(self.store.get(
                    row["content_address"]).decode("utf-8")
                ).get("predecessors", [])
                if any(edge.get("edge_kind") == kind
                       and edge.get("target") == value
                       for (kind, value) in tiers for edge in edges):
                    successors.append(row["sequence_id"])
            successors = sorted(successors)
            release_ids = [mint_id("rel") for _ in tiers]
            receipt = canonicalize(
                {"schema_version": "v1", "kind": "DependencyReleaseReceipt",
                 "predecessor_sequence_id": sequence_id,
                 "record_hash": seq["record_hash"],
                 "contract_digest": seq["contract_digest"],
                 "requirement_digest": seq["requirement_digest"],
                 "acceptance_sha256": acceptance,
                 "release_ids": release_ids,
                 "known_successor_sequence_ids": successors})
            release_address = self.store.put(receipt)
            for release_id, (kind, value) in zip(release_ids, tiers):
                cur.execute(
                    "INSERT INTO released_edges (release_id,"
                    " predecessor_sequence_id, edge_kind, edge_value,"
                    " acceptance_sha256, release_sha256, released_utc)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (release_id, sequence_id, kind, value, acceptance,
                     release_address, now))
            version = self._cas_sequence(cur, seq, now,
                                         state="DEPENDENCIES_RELEASED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s41",
                from_state="ACCEPTED", to_state="DEPENDENCIES_RELEASED",
                recorded_utc=now,
                facts={"release_ids": release_ids,
                       "release_sha256": release_address,
                       "acceptance_sha256": acceptance,
                       "known_successor_sequence_ids": successors,
                       "version": version, "fence": seq["fence"],
                       "mints": ["obj-35"]}))
            return {"release_ids": release_ids,
                    "release_address": release_address,
                    "known_successors": successors}

    def complete_sequence(self, sequence_id):
        """t-s42 DEPENDENCIES_RELEASED→COMPLETED: requires obj-35 (the
        journaled release reference must resolve); no further
        preconditions in the frozen row."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "DEPENDENCIES_RELEASED":
                raise ConflictError(
                    "complete_sequence expects DEPENDENCIES_RELEASED,"
                    " found %s" % seq["state"])
            released = self._sequence_rule_facts(cur, sequence_id,
                                                 ("t-s41",))
            if released is None or "release_sha256" not in released:
                raise IntegrityHalt(
                    "sequence %s is DEPENDENCIES_RELEASED without a"
                    " journaled obj-35 reference" % sequence_id)
            self.store.get(released["release_sha256"])  # D-66
            version = self._cas_sequence(cur, seq, now, state="COMPLETED")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s42",
                from_state="DEPENDENCIES_RELEASED", to_state="COMPLETED",
                recorded_utc=now,
                facts={"release_sha256": released["release_sha256"],
                       "version": version, "fence": seq["fence"]}))
            return version

    def reevaluate_blocked(self, sequence_id):
        """t-s02 BLOCKED→READY: all_edges_satisfied_by_declared_tier over
        the durable released edges, freshness_reevaluated_now (the
        evaluation instant is journaled), finalizer_supported_in_v1 (the
        frozen finalization_class vocabulary) and
        not_quarantined_or_is_repair (no quarantine exists in this
        runtime). Unsatisfied edges mean NO transition and no journal
        entry — the sequence simply stays BLOCKED and the unsatisfied
        edge ids are reported."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq = self._sequence(cur, sequence_id)
            if seq["state"] != "BLOCKED":
                raise ConflictError(
                    "reevaluate_blocked expects BLOCKED, found %s"
                    % seq["state"])
            payload = self._verified_record(cur, seq["record_hash"])
            self._assert_finalizer_supported(payload)
            unsatisfied, satisfied = self._edges_satisfied(cur, payload)
            if unsatisfied:
                return {"state": "BLOCKED", "unsatisfied": unsatisfied}
            version = self._cas_sequence(cur, seq, now, state="READY")
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule="t-s02",
                from_state="BLOCKED", to_state="READY", recorded_utc=now,
                facts={"satisfied_edges": satisfied,
                       "freshness_reevaluated_utc": now,
                       "version": version, "fence": seq["fence"]}))
            return {"state": "READY", "satisfied_edges": satisfied}

    # ------------------------------------------------------ effect intents
    #
    # The crash-safe external-effect boundary (D-68): an external effect may
    # occur only after its EffectIntent row is durably committed, and the
    # executor must hold committed_intent() facts before beginning. v1 mints
    # the attempt_scoped class only; the standalone classes (staging_delete,
    # workspace_sync) mint through their own machines (t-g01/t-w01) in a
    # later milestone. No external executor exists yet (D-71): outcomes
    # enter through resolve_intent's registered observation vocabulary.

    def _intent(self, cur, intent_id):
        row = cur.execute(
            "SELECT * FROM effect_intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        if row is None:
            raise ConflictError("unknown intent %r" % intent_id)
        return row

    def _cas_intent(self, cur, row, to_state, now):
        cur.execute(
            "UPDATE effect_intents SET state = ?, version = version + 1"
            " WHERE intent_id = ? AND state = ? AND version = ?",
            (to_state, row["intent_id"], row["state"], row["version"]),
        )
        if cur.rowcount != 1:
            raise ConflictError(
                "intent %s moved under us (expected %s v%d)"
                % (row["intent_id"], row["state"], row["version"])
            )

    def _attempt_scoped_context(self, cur, sequence_id, attempt_id, now):
        """The attempt_scoped intent-class checks (INV-47 as amended):
        delegation_active_at_intent, reservation_held, fence_matches.
        Returns (sequence row, attempt row, reservation_id, record payload)."""
        seq = self._sequence(cur, sequence_id)
        if seq["state"] in SEQUENCE_TERMINAL:
            # Review correction (closure pass): a terminal Sequence grants no
            # intent authority, whatever its last attempt's fence says.
            raise ConflictError(
                "sequence %s is terminal (%s): no intent authority"
                % (sequence_id, seq["state"]))
        att = self._attempt(cur, attempt_id)
        if att["sequence_id"] != sequence_id:
            raise ConflictError(
                "attempt %s does not belong to sequence %s" % (attempt_id, sequence_id))
        if att["state"] in ("FAILED", "STALE", "REJECTED", "FENCED", "RECONCILED"):
            raise ConflictError(
                "attempt %s is %s: no intent authority" % (attempt_id, att["state"]))
        if att["fence"] != seq["fence"]:
            raise StaleFenceError(
                "attempt fence %r is not the sequence fence %r (fence_matches)"
                % (att["fence"], seq["fence"]))
        payload = self._verified_record(cur, seq["record_hash"])
        delegation_hash = payload.get("resolved_bindings", {}).get("delegation_contract")
        if delegation_hash is not None:
            obj = cur.execute(
                "SELECT content_address FROM objects WHERE object_hash = ?",
                (delegation_hash,)).fetchone()
            if obj is None:
                raise IntegrityHalt(
                    "bound delegation %s not in the ledger" % delegation_hash)
            delegation = json.loads(self.store.get(obj["content_address"]).decode("utf-8"))
            if delegation.get("expires_utc", "") <= now:
                raise ConflictError(
                    "bound delegation expired: not delegation_active_at_intent (t-d02)")
        reservation = cur.execute(
            "SELECT reservation_id FROM reservations WHERE attempt_id = ?"
            " AND state = 'RESERVED' AND lease_expires_utc > ?"
            " ORDER BY created_utc DESC LIMIT 1",
            (attempt_id, now)).fetchone()
        if reservation is None:
            raise ConflictError(
                "no live reservation for attempt %s (reservation_held, INV-60)"
                % attempt_id)
        return seq, att, reservation["reservation_id"], payload

    def _intent_windows(self, payload, now_dt, ttl_seconds, hard_timeout_seconds):
        policy = payload.get("effect_policy", {})
        ttl = ttl_seconds if ttl_seconds is not None else policy.get(
            "effect_intent_ttl_seconds", 300)
        hard = hard_timeout_seconds if hard_timeout_seconds is not None else policy.get(
            "effect_hard_timeout_seconds", 240)
        if not (isinstance(ttl, int) and isinstance(hard, int)
                and 0 < hard < ttl <= INT_MAX):
            raise ConflictError(
                "intent_ttl_gt_hard_timeout violated: ttl %r, hard %r"
                " (SEM-12, INV-67)" % (ttl, hard))
        return (instant(now_dt + timedelta(seconds=ttl)),
                instant(now_dt + timedelta(seconds=hard)))

    def mint_intent(self, sequence_id, attempt_id, effect_id, target,
                    expected_old=None, new_value=None,
                    ttl_seconds=None, hard_timeout_seconds=None):
        """Mint one attempt-scoped EffectIntent (obj-25) under the full
        intent-class checks, in one transaction ending in its
        TransitionRecord (D-68)."""
        effect = EFFECTS.get(effect_id)
        if effect is None:
            raise ConflictError("unknown effect %r" % effect_id)
        if effect["intent_class"] != "attempt_scoped":
            raise ConflictError(
                "effect %s is %s: standalone intent classes mint through"
                " their own machines (t-g01/t-w01), a later milestone"
                % (effect_id, effect["intent_class"]))
        now_dt = self.clock.now()
        now = instant(now_dt)
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            seq, att, reservation_id, payload = self._attempt_scoped_context(
                cur, sequence_id, attempt_id, now)
            expires, hard_to = self._intent_windows(
                payload, now_dt, ttl_seconds, hard_timeout_seconds)
            intent_id = mint_id("in")
            cur.execute(
                "INSERT INTO effect_intents (intent_id, effect_id, intent_class,"
                " state, sequence_id, attempt_id, fence, reservation_id,"
                " created_utc, expires_utc, hard_timeout_utc, version, target,"
                " expected_old, new_value)"
                " VALUES (?, ?, 'attempt_scoped', 'PENDING', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (intent_id, effect_id, sequence_id, attempt_id, att["fence"],
                 reservation_id, now, expires, hard_to, target, expected_old,
                 new_value),
            )
            facts = {"effect_id": effect_id, "intent_class": "attempt_scoped",
                     "sequence_id": sequence_id, "attempt_id": attempt_id,
                     "fence": att["fence"], "reservation_id": reservation_id,
                     "target": target, "expires_utc": expires,
                     "hard_timeout_utc": hard_to, "mints": ["obj-25"]}
            if expected_old is not None:
                facts["expected_old"] = expected_old
            if new_value is not None:
                facts["new_value"] = new_value
            journal.append(cur, journal.transition_record(
                machine="effect_intent", subject_id=intent_id,
                rule="ch2:mint-intent", to_state="PENDING", recorded_utc=now,
                facts=facts,
            ))
            return intent_id

    def committed_intent(self, intent_id):
        """The executor's read side of the boundary: only a durably
        committed, still-PENDING intent inside its hard-timeout window
        authorizes beginning the external effect (effect_intent_committed)."""
        cur = self.db.conn.cursor()
        row = self._intent(cur, intent_id)
        now = instant(self.clock.now())
        if row["state"] != "PENDING":
            raise ConflictError(
                "intent %s is %s, not PENDING: no effect may begin"
                % (intent_id, row["state"]))
        if row["hard_timeout_utc"] is not None and row["hard_timeout_utc"] <= now:
            raise ConflictError(
                "intent %s is past its hard timeout: the effect must not begin"
                % intent_id)
        return dict(row)

    def resolve_intent(self, intent_id, observation, cause=None):
        """Apply the effect's registered outcome rule to one observation:
        t-i01/t-i02 from PENDING, t-i03 from PENDING only once the window
        expired (before that the effect may still land, so PENDING holds),
        t-i04/t-i05 from UNKNOWN_OUTCOME. Returns the state after."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            row = self._intent(cur, intent_id)
            rule_name = EFFECTS[row["effect_id"]]["outcome_rule"]
            rule = OUTCOME_RULES[rule_name]
            if observation not in rule:
                raise ConflictError(
                    "observation %r is not in outcome rule %r"
                    % (observation, rule_name))
            result = rule[observation]
            if row["state"] == "PENDING":
                if result == "HAPPENED":
                    transition = "t-i01"
                elif result == "ABSENT":
                    transition = "t-i02"
                elif row["expires_utc"] > now:
                    return "PENDING"
                else:
                    transition = "t-i03"
            elif row["state"] == "UNKNOWN_OUTCOME":
                if result == "HAPPENED":
                    transition = "t-i04"
                elif result == "ABSENT":
                    transition = "t-i05"
                else:
                    return "UNKNOWN_OUTCOME"
            else:
                raise ConflictError(
                    "intent %s is terminal (%s)" % (intent_id, row["state"]))
            self._cas_intent(cur, row, result, now)
            facts = {"effect_id": row["effect_id"], "observation": observation}
            if cause is not None:
                facts["cause"] = cause
            journal.append(cur, journal.transition_record(
                machine="effect_intent", subject_id=intent_id, rule=transition,
                from_state=row["state"], to_state=result, recorded_utc=now,
                facts=facts,
            ))
            return result

    def expire_intents(self):
        """t-i03's sweep arm: every PENDING intent whose window passed with
        no observed outcome moves to UNKNOWN_OUTCOME."""
        now = instant(self.clock.now())
        moved = []
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            for row in cur.execute(
                "SELECT * FROM effect_intents WHERE state = 'PENDING'"
                " AND expires_utc <= ?", (now,),
            ).fetchall():
                self._cas_intent(cur, row, "UNKNOWN_OUTCOME", now)
                journal.append(cur, journal.transition_record(
                    machine="effect_intent", subject_id=row["intent_id"],
                    rule="t-i03", from_state="PENDING",
                    to_state="UNKNOWN_OUTCOME", recorded_utc=now,
                    facts={"cause": "ttl_expired", "observation": "unobservable"},
                ))
                moved.append(row["intent_id"])
        return moved

    def retry_intent(self, intent_id):
        """t-i06: supersede an UNKNOWN_OUTCOME intent with a fresh intent for
        the same effect, target and expected old value (CORR-56). Only
        registered-retryable effects (INV-73); the attempt-scoped checks run
        again in full, and the successor binds the superseded intent."""
        now_dt = self.clock.now()
        now = instant(now_dt)
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            row = self._intent(cur, intent_id)
            if row["state"] != "UNKNOWN_OUTCOME":
                raise ConflictError(
                    "retry requires UNKNOWN_OUTCOME, found %s" % row["state"])
            if row["effect_id"] not in RETRYABLE:
                raise ConflictError(
                    "effect %s is architecturally not retryable (INV-73)"
                    % row["effect_id"])
            seq, att, reservation_id, payload = self._attempt_scoped_context(
                cur, row["sequence_id"], row["attempt_id"], now)
            expires, hard_to = self._intent_windows(payload, now_dt, None, None)
            successor = mint_id("in")
            cur.execute(
                "INSERT INTO effect_intents (intent_id, effect_id, intent_class,"
                " state, sequence_id, attempt_id, fence, reservation_id,"
                " created_utc, expires_utc, hard_timeout_utc, version, target,"
                " expected_old, new_value, superseded_intent_id)"
                " VALUES (?, ?, 'attempt_scoped', 'PENDING', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (successor, row["effect_id"], row["sequence_id"],
                 row["attempt_id"], att["fence"], reservation_id, now, expires,
                 hard_to, row["target"], row["expected_old"], row["new_value"],
                 intent_id),
            )
            self._cas_intent(cur, row, "SUPERSEDED_BY_RETRY", now)
            journal.append(cur, journal.transition_record(
                machine="effect_intent", subject_id=intent_id, rule="t-i06",
                from_state="UNKNOWN_OUTCOME", to_state="SUPERSEDED_BY_RETRY",
                recorded_utc=now,
                facts={"successor_intent_id": successor,
                       "effect_id": row["effect_id"], "target": row["target"],
                       "reservation_id": reservation_id, "fence": att["fence"],
                       "binds_superseded_intent": intent_id,
                       "mints": ["obj-25"]},
            ))
            return successor

    def resolve_intent_by_attestation(self, intent_id, attestation):
        """t-i07 (trigger: you): once the delegated authority is inactive,
        your ManualAttestation.EFFECT_OUTCOME (obj-37, minimal v1 form)
        resolves an UNKNOWN_OUTCOME intent to HUMAN_RESOLVED."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            row = self._intent(cur, intent_id)
            if row["state"] != "UNKNOWN_OUTCOME":
                raise ConflictError(
                    "t-i07 requires UNKNOWN_OUTCOME, found %s" % row["state"])
            seq = self._sequence(cur, row["sequence_id"])
            payload = self._verified_record(cur, seq["record_hash"])
            delegation_hash = payload.get("resolved_bindings", {}).get("delegation_contract")
            if delegation_hash is not None:
                obj = cur.execute(
                    "SELECT content_address FROM objects WHERE object_hash = ?",
                    (delegation_hash,)).fetchone()
                if obj is None:
                    raise IntegrityHalt(
                        "bound delegation %s not in the ledger" % delegation_hash)
                delegation = json.loads(
                    self.store.get(obj["content_address"]).decode("utf-8"))
                if delegation.get("expires_utc", "") > now:
                    raise ConflictError(
                        "delegated authority still active: t-i07 requires"
                        " authority_inactive")
            if (not isinstance(attestation, dict)
                    or attestation.get("kind") != "ManualAttestation.EFFECT_OUTCOME"
                    or attestation.get("schema_version") != "v1"
                    or attestation.get("intent_id") != intent_id
                    or attestation.get("attested_outcome") not in ("HAPPENED", "ABSENT")
                    or attestation.get("attested_by") != "you"):
                raise AdmissionError(
                    "not a v1 ManualAttestation.EFFECT_OUTCOME for intent %s"
                    % intent_id)
            # Finding (surfaced to the operator): the frozen D-29 kind
            # grammar ([A-Za-z][A-Za-z0-9.]*) cannot express this registry
            # kind's underscore, so no object hash exists for obj-37 under
            # Chapter 1 as frozen. Until obj-37's owning chapter fixes its
            # per-kind domain label, the attestation is preserved by exact
            # bytes (D-31) and referenced from the journal entry.
            address = self.store.put(canonicalize(attestation))
            self._cas_intent(cur, row, "HUMAN_RESOLVED", now)
            journal.append(cur, journal.transition_record(
                machine="effect_intent", subject_id=intent_id, rule="t-i07",
                from_state="UNKNOWN_OUTCOME", to_state="HUMAN_RESOLVED",
                recorded_utc=now,
                facts={"attestation_content_address": address,
                       "attested_outcome": attestation["attested_outcome"]},
            ))
            return address
