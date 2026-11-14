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
from . import transforms
from . import verification_receipts
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
            "attempt_candidate": ("attempt_candidates", "admission_id"),
            "attempt_environment": ("attempt_environments", "admission_id"),
            "attempt_attestation": ("attempt_attestations", "admission_id"),
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
            if row["kind"] in ("VerifierRegistration",
                               "TransformRegistration"):
                # INV-58: an admitted registration's closure leaves are
                # committed references — the content store keeps them and
                # they are never orphans (for a packaged manifest this
                # covers the manifest AND every entry it names). Their
                # EXISTENCE is enforced at dispatch (INV-53: a missing
                # leaf HALTs the runner, never substituted) and reported
                # for live records by the retention report below, so a
                # registration whose artifacts were never uploaded does
                # not wedge recovery.
                closure = payload.get("closure", {})
                referenced.update(transforms.closure_leaf_addresses(
                    self.store, closure))
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
            for key in self._JOURNALED_RECEIPT_FACTS:
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

        # -- 8h. Chapter 4 verification proof objects (C4-M1): both
        #        directions over obj-16/obj-18 and the strengthened
        #        obj-19/obj-20 rows.
        #        attempt_candidates: every row joins an attempt that
        #        reached CANDIDATE_FROZEN, its bytes resolve, re-hash and
        #        ARE their own canonical form, the payload re-validates
        #        against the mirrored obj-16 schema, the row columns agree
        #        with the payload, every ledger-derivable binding still
        #        holds (attempt identity and fence, contract hash, record
        #        tiers, the durable AttemptContract's source_state, the
        #        admitted artifact triples, the class/tree coupling), and
        #        the admission is journaled (both directions, via
        #        reverse_tables above). An attempt in a post-freeze state
        #        with NO candidate row is forged state and HALTs — the
        #        candidate and the captured environment are immutable
        #        proof objects: restart never recaptures them.
        _POST_FREEZE_STATES = frozenset((
            "CANDIDATE_FROZEN", "EVIDENCE_COMPLETE", "VERIFIED",
            "REJECTED"))
        for row in cur.execute(
                "SELECT * FROM attempt_candidates").fetchall():
            att = cur.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            if att is None or att["state"] in ("DECLARED", "RESERVED",
                                               "DISPATCHED", "RUNNING"):
                raise IntegrityHalt(
                    "candidate %s joins attempt state %r: candidates are"
                    " admitted at the freeze only"
                    % (row["admission_id"], att["state"] if att else None))
            data = self.store.get(row["content_sha256"])  # re-hashes (D-66)
            referenced.add(row["content_sha256"])
            candidate_payload = json.loads(data.decode("utf-8"))
            if verification_receipts.receipt_bytes(candidate_payload) \
                    != data:
                raise IntegrityHalt(
                    "candidate %s bytes are not their own canonical form"
                    % row["admission_id"])
            if verification_receipts.validate_candidate(candidate_payload):
                raise IntegrityHalt(
                    "candidate %s no longer validates against the obj-16"
                    " schema" % row["admission_id"])
            tree = candidate_payload.get("candidate_tree")
            if (candidate_payload["finalization_class"]
                    != row["finalization_class"]
                    or (tree["git_tree_sha"] if tree is not None else None)
                    != row["candidate_tree"]):
                raise IntegrityHalt(
                    "candidate row %s disagrees with its admitted payload"
                    % row["admission_id"])
            seq_row = cur.execute(
                "SELECT * FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            identity = candidate_payload["attempt"]
            if (identity["sequence_id"] != att["sequence_id"]
                    or identity["attempt_id"] != att["attempt_id"]
                    or identity["fence"] != att["fence"]
                    or candidate_payload["attempt_contract_hash"]
                    != att["attempt_contract_hash"]
                    or candidate_payload["record_hash"]
                    != seq_row["record_hash"]
                    or candidate_payload["requirement_digest"]
                    != seq_row["requirement_digest"]
                    or candidate_payload["contract_digest"]
                    != seq_row["contract_digest"]):
                raise IntegrityHalt(
                    "candidate %s no longer binds its attempt's durable"
                    " identities (INV-17)" % row["admission_id"])
            contract_obj = cur.execute(
                "SELECT content_address FROM objects WHERE object_hash = ?",
                (att["attempt_contract_hash"],)).fetchone()
            contract_payload = json.loads(self.store.get(
                contract_obj["content_address"]).decode("utf-8"))
            if candidate_payload["source_state"] != \
                    contract_payload["source_state"]:
                raise IntegrityHalt(
                    "candidate %s source_state diverges from the durable"
                    " AttemptContract (INV-02)" % row["admission_id"])
            triples = sorted(
                ({"logical_path": a["logical_path"],
                  "content_sha256": a["content_sha256"], "role": a["role"]}
                 for a in cur.execute(
                     "SELECT logical_path, content_sha256, role"
                     " FROM attempt_artifacts WHERE attempt_id = ?"
                     " ORDER BY rowid",
                     (row["attempt_id"],)).fetchall()),
                key=lambda a: (a["logical_path"], a["content_sha256"]))
            if candidate_payload["artifacts"] != triples:
                raise IntegrityHalt(
                    "candidate %s artifact set diverges from the admitted"
                    " provenance events (INV-03)" % row["admission_id"])
            if candidate_payload["finalization_class"] == \
                    "SOURCE_MODIFYING":
                if tree is None:
                    raise IntegrityHalt(
                        "candidate %s is SOURCE_MODIFYING with no bound"
                        " candidate tree" % row["admission_id"])
            elif tree is not None:
                raise IntegrityHalt(
                    "candidate %s is READ_ONLY with an invented candidate"
                    " tree" % row["admission_id"])
            entry = last.get(("attempt_candidate", row["admission_id"]))
            if entry is None or entry.get("to_state") != "ADMITTED":
                raise IntegrityHalt(
                    "candidate %s has no journaled admission (D-68"
                    " pairing)" % row["admission_id"])
        for row in cur.execute(
                "SELECT attempt_id, state FROM attempts").fetchall():
            if row["state"] not in _POST_FREEZE_STATES:
                continue
            if cur.execute(
                    "SELECT 1 FROM attempt_candidates WHERE attempt_id = ?",
                    (row["attempt_id"],)).fetchone() is None:
                raise IntegrityHalt(
                    "attempt %s is %s with no admitted obj-16 candidate"
                    " (t-a05/t-s07 require it)"
                    % (row["attempt_id"], row["state"]))
        for row in cur.execute(
                "SELECT * FROM attempt_environments").fetchall():
            att = cur.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            if att is None:
                raise IntegrityHalt(
                    "environment %s joins no attempt" % row["admission_id"])
            data = self.store.get(row["content_sha256"])  # re-hashes
            referenced.add(row["content_sha256"])
            env_payload = json.loads(data.decode("utf-8"))
            refusal = validate_environment_capture(env_payload, att)
            if refusal is not None:
                raise IntegrityHalt(
                    "environment %s no longer validates or binds its"
                    " attempt: %s" % (row["admission_id"], refusal))
            entry = last.get(("attempt_environment", row["admission_id"]))
            if entry is None or entry.get("to_state") != "ADMITTED":
                raise IntegrityHalt(
                    "environment %s has no journaled admission (D-68"
                    " pairing)" % row["admission_id"])
        for row in cur.execute(
                "SELECT * FROM attempt_verifications"
                " WHERE by_principal = ?",
                (RESULT_PRODUCER,)).fetchall():
            att = cur.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            seq_row = cur.execute(
                "SELECT * FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            result_bytes = self.store.get(row["receipt_sha256"])
            result_payload = json.loads(result_bytes.decode("utf-8"))
            if verification_receipts.receipt_bytes(result_payload) != \
                    result_bytes:
                raise IntegrityHalt(
                    "verification %s receipt bytes are not canonical"
                    % row["verification_id"])
            if verification_receipts.validate_result_receipt(
                    result_payload):
                raise IntegrityHalt(
                    "verification %s no longer validates against the"
                    " obj-19/obj-20 schema" % row["verification_id"])
            candidate = cur.execute(
                "SELECT content_sha256 FROM attempt_candidates"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
            environment = cur.execute(
                "SELECT content_sha256 FROM attempt_environments"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
            identity = result_payload["attempt"]
            part = result_payload["assertion"]["part"]
            # D-47 part-kind-aware row-key mapping (C4-M2): an ASSERTION
            # part's row key is the assertion id; a ROLE_TRANSFORM part's
            # is "role-transform/<role>".
            if part.get("part_kind") == "ROLE_TRANSFORM":
                expected_row_key = "role-transform/%s" % part["part_id"]
            else:
                expected_row_key = part["part_id"]
            if (identity["attempt_id"] != att["attempt_id"]
                    or identity["sequence_id"] != att["sequence_id"]
                    or identity["fence"] != att["fence"]
                    or result_payload["attempt_contract_hash"]
                    != att["attempt_contract_hash"]
                    or result_payload["record_hash"]
                    != seq_row["record_hash"]
                    or result_payload["outcome"] != row["outcome"]
                    or expected_row_key != row["assertion_id"]
                    or part["record"] != seq_row["record_hash"]
                    or candidate is None
                    or result_payload["candidate_receipt_sha256"]
                    != candidate["content_sha256"]
                    or environment is None
                    or result_payload["environment_sha256"]
                    != environment["content_sha256"]
                    or row["environment_sha256"]
                    != environment["content_sha256"]):
                raise IntegrityHalt(
                    "verification %s no longer binds its durable candidate,"
                    " environment, assertion and attempt (INV-17)"
                    % row["verification_id"])
            output_bytes = self.store.get(
                result_payload["execution"]["output_sha256"])
            referenced.add(result_payload["execution"]["output_sha256"])
            if verification_receipts.outcome_consistency(
                    result_payload, output_bytes) is not None:
                raise IntegrityHalt(
                    "verification %s outcome no longer agrees with its"
                    " durable execution evidence (F-C4-1)"
                    % row["verification_id"])
        # -- 8e-2. attestation admissions (C4-M2, D-52/D-53): every
        #          admitted obj-37 resolves to its exact bytes, is its own
        #          canonical form, still validates with FULL bindings
        #          (attempt, contract, candidate, D-47 part) and has its
        #          journaled admission; every ATTESTED verification row
        #          pairs with its admitted attestation and its canonical
        #          obj-19 BY_ATTESTATION receipt.
        for row in cur.execute(
                "SELECT * FROM attempt_attestations").fetchall():
            att = cur.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            if att is None:
                raise IntegrityHalt(
                    "attestation %s joins no attempt" % row["admission_id"])
            seq_row = cur.execute(
                "SELECT * FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            candidate = cur.execute(
                "SELECT content_sha256 FROM attempt_candidates"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
            data = self.store.get(row["content_sha256"])  # re-hashes
            referenced.add(row["content_sha256"])
            attestation_payload = json.loads(data.decode("utf-8"))
            if canonicalize(attestation_payload) != data:
                raise IntegrityHalt(
                    "attestation %s bytes are not canonical (D-31)"
                    % row["admission_id"])
            refusal = validate_assertion_attestation(
                attestation_payload, att, row["assertion_id"],
                candidate["content_sha256"] if candidate else None,
                seq_row["record_hash"])
            if refusal is not None:
                raise IntegrityHalt(
                    "attestation %s no longer validates or binds its"
                    " attempt: %s" % (row["admission_id"], refusal))
            entry = last.get(("attempt_attestation", row["admission_id"]))
            if entry is None or entry.get("to_state") != "ADMITTED":
                raise IntegrityHalt(
                    "attestation %s has no journaled admission (D-68"
                    " pairing)" % row["admission_id"])
        for row in cur.execute(
                "SELECT * FROM attempt_verifications"
                " WHERE outcome = 'ATTESTED'").fetchall():
            att = cur.execute(
                "SELECT * FROM attempts WHERE attempt_id = ?",
                (row["attempt_id"],)).fetchone()
            seq_row = cur.execute(
                "SELECT * FROM sequences WHERE sequence_id = ?",
                (att["sequence_id"],)).fetchone()
            result_bytes = self.store.get(row["receipt_sha256"])
            result_payload = json.loads(result_bytes.decode("utf-8"))
            if verification_receipts.receipt_bytes(result_payload) != \
                    result_bytes:
                raise IntegrityHalt(
                    "attested verification %s receipt bytes are not"
                    " canonical" % row["verification_id"])
            if verification_receipts.validate_result_receipt(
                    result_payload):
                raise IntegrityHalt(
                    "attested verification %s no longer validates against"
                    " the obj-19 schema" % row["verification_id"])
            attestation_row = cur.execute(
                "SELECT content_sha256 FROM attempt_attestations"
                " WHERE attempt_id = ? AND assertion_id = ?",
                (row["attempt_id"], row["assertion_id"])).fetchone()
            candidate = cur.execute(
                "SELECT content_sha256 FROM attempt_candidates"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchone()
            identity = result_payload["attempt"]
            part = result_payload["assertion"]["part"]
            if (result_payload.get("result_source") != "BY_ATTESTATION"
                    or identity["attempt_id"] != att["attempt_id"]
                    or identity["sequence_id"] != att["sequence_id"]
                    or identity["fence"] != att["fence"]
                    or result_payload["attempt_contract_hash"]
                    != att["attempt_contract_hash"]
                    or result_payload["record_hash"]
                    != seq_row["record_hash"]
                    or part["part_id"] != row["assertion_id"]
                    or part["record"] != seq_row["record_hash"]
                    or candidate is None
                    or result_payload["candidate_receipt_sha256"]
                    != candidate["content_sha256"]
                    or row["environment_sha256"] is not None
                    or row["by_principal"] != ATTESTATION_PRODUCER
                    or attestation_row is None
                    or result_payload["attestation_sha256"]
                    != attestation_row["content_sha256"]):
                raise IntegrityHalt(
                    "attested verification %s no longer binds its durable"
                    " attestation, candidate, assertion and attempt"
                    " (D-52/D-53/INV-17)" % row["verification_id"])
        for row in cur.execute(
                "SELECT attempt_id, state FROM attempts"
                " WHERE state IN ('VERIFIED','REJECTED')").fetchall():
            outcomes = [r["outcome"] for r in cur.execute(
                "SELECT outcome FROM attempt_verifications"
                " WHERE attempt_id = ?", (row["attempt_id"],)).fetchall()]
            if row["state"] == "VERIFIED" and any(
                    o not in ("PASS", "ATTESTED") for o in outcomes):
                raise IntegrityHalt(
                    "attempt %s is VERIFIED with a non-passing admitted"
                    " result (t-a07 requires authoritative obj-19)"
                    % row["attempt_id"])
            if row["state"] == "REJECTED" and "FAIL" not in outcomes:
                raise IntegrityHalt(
                    "attempt %s is REJECTED with no admitted FAIL result"
                    " (t-a08 requires authoritative obj-20)"
                    % row["attempt_id"])

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

        # -- 8f. INV-58 retention report (C4-M2): every closure leaf a
        #        LIVE (non-terminal) sequence's bound registrations still
        #        bind must resolve. REPORTED, never a halt: the fail-closed
        #        act stays dispatch-time INV-53 (resolve_closure HALTs over
        #        a missing leaf, never substitutes), and recovery reporting
        #        keeps a runtime whose fixture registrations carry no
        #        executable bytes recoverable (D-C4-9) while making every
        #        gap visible the moment restart runs.
        missing_bound_leaves = []
        for seq_row in cur.execute(
                "SELECT sequence_id, record_hash, state FROM sequences"
        ).fetchall():
            if seq_row["state"] in SEQUENCE_TERMINAL:
                continue
            record_row = cur.execute(
                "SELECT content_address FROM records WHERE record_hash = ?",
                (seq_row["record_hash"],)).fetchone()
            record = json.loads(
                self.store.get(record_row["content_address"]).decode(
                    "utf-8"))
            bindings = record.get("resolved_bindings", {})
            for registration_hash in (
                    list(bindings.get("verifier_registrations", []))
                    + list(bindings.get("transform_registrations", []))):
                obj_row = cur.execute(
                    "SELECT content_address FROM objects"
                    " WHERE object_hash = ?", (registration_hash,)
                ).fetchone()
                if obj_row is None or not self.store.has(
                        obj_row["content_address"]):
                    missing_bound_leaves.append(
                        {"sequence_id": seq_row["sequence_id"],
                         "registration": registration_hash,
                         "leaf": registration_hash})
                    continue
                registration = json.loads(self.store.get(
                    obj_row["content_address"]).decode("utf-8"))
                for leaf in transforms.closure_leaf_addresses(
                        self.store, registration.get("closure", {})):
                    if not self.store.has(leaf):
                        missing_bound_leaves.append(
                            {"sequence_id": seq_row["sequence_id"],
                             "registration": registration_hash,
                             "leaf": leaf})

        # -- 6. harmless leftovers, reported and ignored
        orphans = self.store.orphans(referenced)

        return {"journal_entries": len(payloads),
                "objects_verified": objects_checked,
                "sequences": current, "orphans": orphans,
                "intents_in_doubt": intents_in_doubt,
                "retention": {
                    "missing_bound_leaves": missing_bound_leaves}}

    # ---------------------------------------------------------- retention
    #
    # INV-58's two halves, explicitly split (C4-M2): the content store
    # keeps every leaf a LIVE registration closure binds (retention_roots),
    # and reclamation is an explicit, re-derived act (reclaim) — never a
    # sweeper, never a dispatch-time substitute for INV-53 (a missing leaf
    # at dispatch still HALTs; retention exists so that case stays
    # impossible while any non-terminal sequence can still dispatch).

    # Ledger columns whose values are content addresses: anything they
    # name is committed evidence, never reclaimable through this seam.
    _LEDGER_ADDRESS_COLUMNS = (
        ("objects", "content_address"),
        ("records", "content_address"),
        ("admissions", "payload_content_address"),
        ("duplicate_admissions", "payload_content_address"),
        ("attempt_witnesses", "content_sha256"),
        ("attempt_receipts", "content_sha256"),
        ("attempt_artifacts", "content_sha256"),
        ("attempt_candidates", "content_sha256"),
        ("attempt_environments", "content_sha256"),
        ("attempt_attestations", "content_sha256"),
        ("attempt_verifications", "receipt_sha256"),
        ("attempt_verifications", "environment_sha256"),
        ("attempt_calls", "projection_address"),
        # F-TI-4: the obj-30/obj-35 references recovery resolves (8e).
        ("released_edges", "acceptance_sha256"),
        ("released_edges", "release_sha256"),
    )

    # Sequence-entry facts naming finalization receipts (obj-26, obj-30,
    # obj-35) that recovery resolves from the journal (8d). ONE list for
    # recovery and reclamation: F-TI-4 (TI-1) was reclaim protecting less
    # than recovery requires.
    _JOURNALED_RECEIPT_FACTS = (
        "commit_receipt_address", "observation_receipt_address",
        "acceptance_sha256", "release_sha256",
    )

    def retention_roots(self):
        """The live retention map (INV-58): content address -> sorted
        sequence ids that still bind it. A leaf is LIVE while ANY
        non-terminal sequence's frozen record binds its registration
        (resolved_bindings, verifier and transform alike); registration
        REPLACEMENT frees nothing until every sequence bound to the old
        registration is terminal. Recomputed from durable rows on every
        call — there is no cached list a restart or a new admission could
        leave stale."""
        cur = self.db.conn.cursor()
        roots = {}
        for seq_row in cur.execute(
                "SELECT sequence_id, record_hash, state FROM sequences"
        ).fetchall():
            if seq_row["state"] in SEQUENCE_TERMINAL:
                continue
            record_row = cur.execute(
                "SELECT content_address FROM records WHERE record_hash = ?",
                (seq_row["record_hash"],)).fetchone()
            if record_row is None or not self.store.has(
                    record_row["content_address"]):
                continue  # recovery HALTs on this; retention stays a map
            record = json.loads(self.store.get(
                record_row["content_address"]).decode("utf-8"))
            bindings = record.get("resolved_bindings", {})
            for registration_hash in (
                    list(bindings.get("verifier_registrations", []))
                    + list(bindings.get("transform_registrations", []))):
                obj_row = cur.execute(
                    "SELECT content_address FROM objects"
                    " WHERE object_hash = ?", (registration_hash,)
                ).fetchone()
                if obj_row is None or not self.store.has(
                        obj_row["content_address"]):
                    continue  # reported by recovery's retention report
                registration = json.loads(self.store.get(
                    obj_row["content_address"]).decode("utf-8"))
                for leaf in transforms.closure_leaf_addresses(
                        self.store, registration.get("closure", {})):
                    roots.setdefault(leaf, set()).add(
                        seq_row["sequence_id"])
        return {leaf: sorted(ids) for leaf, ids in roots.items()}

    def reclaim(self, addresses):
        """Explicit reclamation of NAMED content addresses (C4-M2,
        INV-58): recompute the live retention roots INSIDE this call (a
        list computed before a restart, a new admission or a registration
        replacement is stale and protects nothing), refuse every address a
        live closure still binds or any ledger column references, and
        unlink the rest. Returns {"removed": [...], "retained": {address:
        reason}}; a removal is journaled as an audit event. NOT a garbage
        collector: nothing is scanned for, only the named candidates are
        judged — and judged at the moment of deletion."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            roots = self.retention_roots()
            # A frozen record's declared specification inputs are committed
            # references FOREVER (recovery resolves every record's inputs,
            # terminal or not, and INV-44's chain starts at those exact
            # bytes) — never reclaimable, whatever the sequence states.
            frozen_inputs = set()
            for record_row in cur.execute(
                    "SELECT content_address FROM records").fetchall():
                record = json.loads(self.store.get(
                    record_row["content_address"]).decode("utf-8"))
                inputs = record.get("specification_inputs", {}).get(
                    "name_to_exact_byte_sha256", {})
                frozen_inputs.update(
                    v for v in inputs.values() if isinstance(v, str))
            # F-TI-4 (TI-1, exposed by the independent state oracle):
            # recovery resolves two more reference families that no
            # ledger COLUMN holds — reclaiming either turned a clean
            # ledger into a permanent recovery HALT. The journaled
            # finalization receipts (8d) and the execution evidence every
            # admitted obj-19/obj-20 binds (8h, F-C4-1) are committed
            # evidence, judged here exactly as recovery reads them.
            committed_evidence = {}
            for entry in journal.entries(cur):
                if entry.get("machine") != "sequence":
                    continue
                entry_facts = entry.get("facts", {})
                for key in self._JOURNALED_RECEIPT_FACTS:
                    named = entry_facts.get(key)
                    if isinstance(named, str):
                        committed_evidence.setdefault(
                            named, "journaled finalization receipt"
                                   " reference (%s %s)"
                                   % (entry.get("rule"), key))
            for result_row in cur.execute(
                    "SELECT receipt_sha256 FROM attempt_verifications"
                    " WHERE by_principal = ?",
                    (RESULT_PRODUCER,)).fetchall():
                try:
                    result_payload = json.loads(self.store.get(
                        result_row["receipt_sha256"]).decode("utf-8"))
                except (IntegrityHalt, UnicodeDecodeError, ValueError):
                    continue  # recovery HALTs on this; reclaim only judges
                execution = result_payload.get("execution") \
                    if isinstance(result_payload, dict) else None
                evidence = execution.get("output_sha256") \
                    if isinstance(execution, dict) else None
                if isinstance(evidence, str):
                    committed_evidence.setdefault(
                        evidence, "execution evidence bound by an admitted"
                                  " verification receipt (F-C4-1)")
            removed = []
            retained = {}
            for address in sorted(set(addresses)):
                if address in frozen_inputs:
                    retained[address] = (
                        "a frozen record's declared specification input"
                        " (resolvable forever; INV-44/D-66)")
                    continue
                if address in committed_evidence:
                    retained[address] = committed_evidence[address]
                    continue
                if address in roots:
                    retained[address] = (
                        "live registration closure leaf, bound by %s"
                        % ", ".join(roots[address]))
                    continue
                referenced_by = None
                for table, column in self._LEDGER_ADDRESS_COLUMNS:
                    if cur.execute(
                            "SELECT 1 FROM %s WHERE %s = ?"
                            % (table, column), (address,)
                    ).fetchone() is not None:
                        referenced_by = "%s.%s" % (table, column)
                        break
                if referenced_by is not None:
                    retained[address] = (
                        "referenced by ledger column %s" % referenced_by)
                    continue
                if self.store.remove(address):
                    removed.append(address)
                else:
                    retained[address] = "no bytes at this address"
            if removed:
                journal.append(cur, journal.transition_record(
                    machine="content_store", subject_id=mint_id("rc"),
                    rule="ch4:reclaim", to_state="RECLAIMED",
                    recorded_utc=now,
                    facts={"removed": removed,
                           "retained_count": len(retained)}))
            return {"removed": removed, "retained": retained}

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

    def _attempt_contract_payload(self, cur, att):
        """The durable obj-09 AttemptContract payload for one attempt (its
        content reference must resolve; D-66 on the read)."""
        row = cur.execute(
            "SELECT content_address FROM objects WHERE object_hash = ?",
            (att["attempt_contract_hash"],)).fetchone()
        if row is None:
            raise IntegrityHalt(
                "attempt %s names AttemptContract %s that is not in the"
                " ledger" % (att["attempt_id"],
                             att["attempt_contract_hash"]))
        return json.loads(
            self.store.get(row["content_address"]).decode("utf-8"))

    def _artifact_triples(self, cur, attempt_id):
        """The attempt's admitted artifact provenance events as sorted
        identity triples — the obj-16 ``artifacts`` binding's ledger
        side."""
        rows = cur.execute(
            "SELECT logical_path, content_sha256, role"
            " FROM attempt_artifacts WHERE attempt_id = ?"
            " ORDER BY rowid", (attempt_id,)).fetchall()
        return sorted(
            ({"logical_path": r["logical_path"],
              "content_sha256": r["content_sha256"], "role": r["role"]}
             for r in rows),
            key=lambda a: (a["logical_path"], a["content_sha256"]))

    def candidate_state(self, attempt_id):
        """The git_adapter's public read surface (cap-31/cap-32): one
        FINISHED attempt's identities, frozen record facts, durable
        AttemptContract source state and admitted artifact provenance —
        everything obj-16 binds that the ledger holds. The adapter adds
        only what Git owns (the candidate tree, cap-42)."""
        cur = self.db.conn.cursor()
        att = self._attempt(cur, attempt_id)
        if att["state"] != "FINISHED":
            raise ConflictError(
                "candidate capture is defined at FINISHED only, found %s"
                % att["state"])
        seq = self._sequence(cur, att["sequence_id"])
        payload = self._verified_record(cur, seq["record_hash"])
        contract = self._attempt_contract_payload(cur, att)
        return {"attempt_id": att["attempt_id"],
                "sequence_id": att["sequence_id"],
                "fence": att["fence"],
                "attempt_contract_hash": att["attempt_contract_hash"],
                "record_hash": seq["record_hash"],
                "requirement_digest": seq["requirement_digest"],
                "contract_digest": seq["contract_digest"],
                "finalization_class": payload["finalization_class"],
                "source_state": dict(contract["source_state"]),
                "artifacts": self._artifact_triples(cur, attempt_id)}

    def _candidate_row(self, cur, attempt_id):
        """The attempt's ONE admitted obj-16 row (freeze admission's
        durable half); its absence past CANDIDATE_FROZEN is forged state."""
        return cur.execute(
            "SELECT * FROM attempt_candidates WHERE attempt_id = ?",
            (attempt_id,)).fetchone()

    def freeze_candidate(self, attempt_id, expected_fence, candidate_address):
        """t-a05 FINISHED→CANDIDATE_FROZEN plus t-s07, now carrying the
        FULL obj-16 admission (C4-M1): both registered rows REQUIRE the
        CandidateReceipt, so the presented bytes must BE their own
        canonical form, validate against the mirrored obj-16 schema (kind,
        minter git_adapter, shape), and bind this exact attempt on every
        ledger-derivable member — attempt identity and CURRENT fence,
        attempt_contract_hash, record/requirement/contract digests, the
        frozen record's finalization_class, the durable AttemptContract's
        source_state, and the admitted artifact provenance triples. The
        class/tree coupling is enforced here (SOURCE_MODIFYING binds a
        candidate Git tree; READ_ONLY must not invent one). A validated
        receipt commits its ``attempt_candidates`` row — ONE per attempt,
        the immutability anchor every obj-19/obj-20 then binds — in the
        same transaction as t-a05/t-s07, journaled."""
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
            data = self.store.get(candidate_address)
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ConflictError(
                    "candidate bytes are not UTF-8 JSON (obj-16)")
            if not isinstance(payload, dict) or \
                    verification_receipts.receipt_bytes(payload) != data:
                raise ConflictError(
                    "candidate bytes are not their own canonical form: the"
                    " admitted address must be the payload's identity")
            issues = verification_receipts.validate_candidate(payload)
            if issues:
                raise ConflictError(
                    "candidate failed obj-16 schema validation: %s"
                    % "; ".join(repr(issue) for issue in issues))
            identity = payload["attempt"]
            if (identity["sequence_id"] != att["sequence_id"]
                    or identity["attempt_id"] != att["attempt_id"]
                    or identity["fence"] != seq["fence"]):
                raise ConflictError(
                    "obj-16 attempt identity %r does not bind this attempt"
                    " at its current fence (INV-13)" % (identity,))
            if payload["attempt_contract_hash"] != \
                    att["attempt_contract_hash"]:
                raise ConflictError(
                    "obj-16 attempt_contract_hash must match the durable"
                    " contract")
            if (payload["record_hash"] != seq["record_hash"]
                    or payload["requirement_digest"]
                    != seq["requirement_digest"]
                    or payload["contract_digest"]
                    != seq["contract_digest"]):
                raise ConflictError(
                    "obj-16 frozen record identities do not match the"
                    " Sequence's durable tiers")
            record_payload = self._verified_record(cur, seq["record_hash"])
            if payload["finalization_class"] != \
                    record_payload["finalization_class"]:
                raise ConflictError(
                    "obj-16 finalization_class %r is not the frozen"
                    " record's %r" % (payload["finalization_class"],
                                      record_payload["finalization_class"]))
            contract = self._attempt_contract_payload(cur, att)
            if payload["source_state"] != contract["source_state"]:
                raise ConflictError(
                    "obj-16 source_state does not equal the durable"
                    " AttemptContract's frozen source state (INV-02)")
            if payload["artifacts"] != self._artifact_triples(cur,
                                                              attempt_id):
                raise ConflictError(
                    "obj-16 artifacts do not equal the admitted artifact"
                    " provenance events (INV-03: the candidate's artifact"
                    " set is the ledger's, never a caller claim)")
            tree = payload.get("candidate_tree")
            if payload["finalization_class"] == "SOURCE_MODIFYING":
                if tree is None:
                    raise ConflictError(
                        "SOURCE_MODIFYING work binds the candidate Git"
                        " tree identity (obj-16)")
            elif tree is not None:
                raise ConflictError(
                    "READ_ONLY work gains no invented candidate tree"
                    " (obj-16)")
            existing = self._candidate_row(cur, attempt_id)
            if existing is not None:
                # The unique index is candidate immutability's durable
                # half; an identical re-freeze is unreachable (state gate),
                # so any row here is a conflicting second candidate.
                raise ConflictError(
                    "attempt %s already has an admitted candidate %s:"
                    " candidates are immutable, one per attempt"
                    % (attempt_id, existing["content_sha256"]))
            admission_id = mint_id("cnd")
            cur.execute(
                "INSERT INTO attempt_candidates (admission_id, attempt_id,"
                " content_sha256, finalization_class, candidate_tree,"
                " admitted_utc) VALUES (?, ?, ?, ?, ?, ?)",
                (admission_id, attempt_id, candidate_address,
                 payload["finalization_class"],
                 tree["git_tree_sha"] if tree is not None else None, now))
            admission_facts = {"attempt_id": attempt_id,
                               "sequence_id": att["sequence_id"],
                               "content_sha256": candidate_address,
                               "finalization_class":
                                   payload["finalization_class"],
                               "minter": payload["minter"],
                               "fence": seq["fence"]}
            if tree is not None:  # D-37
                admission_facts["candidate_tree"] = tree["git_tree_sha"]
            journal.append(cur, journal.transition_record(
                machine="attempt_candidate", subject_id=admission_id,
                rule="ch4:admit-candidate", to_state="ADMITTED",
                recorded_utc=now, facts=admission_facts))
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
                return registration, registration_hash
        raise IntegrityHalt(
            "assertion verifier %r has no bound registration in the frozen"
            " record (SEM-07 was enforced at freeze; the record is"
            " self-inconsistent)" % verifier_id)

    def _bound_transform_registration(self, cur, payload, transform_id):
        """Resolve a declared role transform to its bound
        TransformRegistration (SEM-06/K-2 at freeze). INV-53 exactly as
        for verifiers: a bound registration that no longer resolves — the
        ledger row or its exact bytes — HALTs and is never substituted."""
        for registration_hash in payload.get("resolved_bindings", {}).get(
                "transform_registrations", []):
            row = cur.execute(
                "SELECT content_address FROM objects WHERE object_hash = ?",
                (registration_hash,)).fetchone()
            if row is None:
                raise IntegrityHalt(
                    "bound transform registration %s is not in the ledger"
                    " (INV-53: never substituted)" % registration_hash)
            if not self.store.has(row["content_address"]):
                raise IntegrityHalt(
                    "bound transform registration %s has lost its bytes"
                    " (INV-53/D-66 HALT)" % registration_hash)
            registration = json.loads(
                self.store.get(row["content_address"]).decode("utf-8"))
            if (registration.get("kind") == "TransformRegistration"
                    and registration.get("transform_id") == transform_id):
                return registration, registration_hash
        raise IntegrityHalt(
            "role transform %r has no bound registration in the frozen"
            " record (SEM-06/K-2 was enforced at freeze; the record is"
            " self-inconsistent)" % transform_id)

    def _observed_prompt_identity(self, cur, attempt_id):
        """INV-49's OBSERVED side: the child's received-prompt identity,
        derived only from admitted obj-11 producer bytes (the provider's
        persisted task record carries the prompt the child was actually
        given; witness_semantics derives its content identity). A runner
        or child CLAIM is never evidence. Returns the one identity, or
        None when no admitted producer record carries one; DISTINCT
        identities across the admitted set are an ambiguity refusal, never
        chosen from."""
        digests = set()
        for row in cur.execute(
                "SELECT content_sha256 FROM attempt_witnesses"
                " WHERE attempt_id = ? AND witness_kind = 'obj-11'",
                (attempt_id,)).fetchall():
            facts = witness_semantics.extract_facts(
                "obj-11", self.store.get(row["content_sha256"]))
            value = facts.get("prompt_sha256")
            if isinstance(value, str) and value:
                digests.add(value)
        if len(digests) > 1:
            raise ConflictError(
                "admitted obj-11 evidence carries %d DISTINCT received-"
                "prompt identities for attempt %s: ambiguous producer"
                " evidence is refused (INV-49), never chosen from"
                % (len(digests), attempt_id))
        return digests.pop() if digests else None

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

    def _environment_row(self, cur, attempt_id):
        return cur.execute(
            "SELECT * FROM attempt_environments WHERE attempt_id = ?",
            (attempt_id,)).fetchone()

    def _bind_environment(self, cur, att, environment, now):
        """The obj-18 admission half (C4-M1): ONE captured environment per
        attempt, immutable. Supplied bytes are validated against the frozen
        Chapter 1 schema and this attempt's identity, then admitted (first
        capture) or matched byte-identically against the already-admitted
        capture — a different capture is refused, so restart or retry can
        never recapture its way to a more favorable environment. Returns
        the bound environment's content address (or None when the attempt
        has none yet and none was supplied)."""
        row = self._environment_row(cur, att["attempt_id"])
        if environment is None:
            return row["content_sha256"] if row is not None else None
        environment = bytes(environment)
        try:
            env_payload = json.loads(environment.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ConflictError(
                "environment bytes are not UTF-8 JSON (obj-18)")
        refusal = validate_environment_capture(env_payload, att)
        if refusal is not None:
            raise ConflictError(refusal)
        address = self.store.put(environment)
        if row is not None:
            if row["content_sha256"] != address:
                raise ConflictError(
                    "attempt %s already has its admitted obj-18 capture %s:"
                    " the environment is captured once and never replaced"
                    % (att["attempt_id"], row["content_sha256"]))
            return address
        admission_id = mint_id("env")
        cur.execute(
            "INSERT INTO attempt_environments (admission_id, attempt_id,"
            " content_sha256, admitted_utc) VALUES (?, ?, ?, ?)",
            (admission_id, att["attempt_id"], address, now))
        journal.append(cur, journal.transition_record(
            machine="attempt_environment", subject_id=admission_id,
            rule="ch4:admit-environment", to_state="ADMITTED",
            recorded_utc=now,
            facts={"attempt_id": att["attempt_id"],
                   "sequence_id": att["sequence_id"],
                   "content_sha256": address,
                   "minter": "environment_probe",
                   "fence": att["fence"]}))
        return address

    def admit_environment_capture(self, attempt_id, data):
        """The obj-18 admission seam (cap-33's durable half, C4-M1): admit
        the probe's capture for an EVIDENCE_COMPLETE attempt BEFORE any
        verifier launch, so the captured environment is an immutable
        durable proof object — restart resumes from it and never
        recaptures its way to a more favorable environment (the crash
        matrix's capture-before-launch point). Idempotent on identical
        bytes; a different capture for the same attempt is refused."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att, _seq = self._verification_context(cur, attempt_id)
            before = self._environment_row(cur, attempt_id)
            address = self._bind_environment(cur, att, bytes(data), now)
            return {"environment_address": address,
                    "duplicate": before is not None}

    def verification_state(self, attempt_id):
        """The verifier_runner's public read surface (cap-35 consuming
        cap-30/cap-32/cap-33): one EVIDENCE_COMPLETE attempt's identities,
        frozen record payload, declared assertions, the admitted obj-16
        candidate (address and payload) and the admitted obj-18
        environment address when one exists."""
        cur = self.db.conn.cursor()
        att = self._attempt(cur, attempt_id)
        seq = self._sequence(cur, att["sequence_id"])
        if att["state"] != "EVIDENCE_COMPLETE":
            raise ConflictError(
                "verification is defined at EVIDENCE_COMPLETE only,"
                " found %s" % att["state"])
        record_payload = self._verified_record(cur, seq["record_hash"])
        candidate = self._candidate_row(cur, attempt_id)
        if candidate is None:
            raise IntegrityHalt(
                "attempt %s is past CANDIDATE_FROZEN with no admitted"
                " obj-16 candidate (forged state)" % attempt_id)
        env_row = self._environment_row(cur, attempt_id)
        assertions = record_payload.get(
            "acceptance_assertions", {}).get(
            "ids_and_environment_constraints", [])
        registrations = {}
        for entry in assertions:
            if entry.get("mode") != "DETERMINISTIC":
                continue
            verifier_id = entry.get("verifier_id")
            if verifier_id in registrations:
                continue
            registration, registration_hash = \
                self._bound_verifier_registration(cur, record_payload,
                                                  verifier_id)
            registrations[verifier_id] = {
                "payload": registration,
                "object_sha256": registration_hash}
        state = {"attempt_id": att["attempt_id"],
                 "sequence_id": att["sequence_id"],
                 "fence": att["fence"],
                 "version": att["version"],
                 "attempt_contract_hash": att["attempt_contract_hash"],
                 "record_hash": seq["record_hash"],
                 "record_payload": record_payload,
                 "assertions": assertions,
                 "registrations": registrations,
                 "candidate_receipt_sha256": candidate["content_sha256"],
                 "candidate_payload": json.loads(self.store.get(
                     candidate["content_sha256"]).decode("utf-8"))}
        if env_row is not None:
            state["environment_sha256"] = env_row["content_sha256"]
        # C4-M2: the dispatched role, its declared transform binding (when
        # the frozen record names one) and the admitted attestations — the
        # runner's read surface for the role-transform check and the
        # BY_ATTESTATION incorporation. D-37: absent facts are omitted.
        call_row = cur.execute(
            "SELECT * FROM attempt_calls WHERE attempt_id = ?",
            (attempt_id,)).fetchone()
        if call_row is not None:
            state["role"] = call_row["role"]
            state["projection_address"] = call_row["projection_address"]
            transform_id = record_payload.get(
                "role_transforms", {}).get(call_row["role"])
            if transform_id is not None:
                registration, registration_hash = \
                    self._bound_transform_registration(
                        cur, record_payload, transform_id)
                state["role_transform"] = {
                    "role": call_row["role"],
                    "transform_id": transform_id,
                    "payload": registration,
                    "object_sha256": registration_hash,
                }
        attestations = {row["assertion_id"]: row["content_sha256"]
                        for row in cur.execute(
                            "SELECT assertion_id, content_sha256"
                            " FROM attempt_attestations"
                            " WHERE attempt_id = ?",
                            (attempt_id,)).fetchall()}
        if attestations:
            state["attestations"] = attestations
        return state

    def register_verifier_result(self, attempt_id, assertion_id, outcome,
                                 by, receipt, environment=None):
        """Admit one verifier-runner result for a DETERMINISTIC assertion —
        since C4-M1 the FULL canonical obj-19/obj-20 admission: outcome
        PASS/FAIL/INCONCLUSIVE with the runner's exact receipt bytes, which
        must BE their own canonical form, validate against the mirrored
        Chapter 4 schema for their kind, agree with the declared outcome,
        and bind — by equality against durable ledger facts — the exact
        Attempt (identity and CURRENT fence), the frozen record
        (attempt_contract_hash, record_hash, the D-47 assertion part
        reference), the admitted obj-16 candidate, the attempt's ONE
        admitted obj-18 environment, and the exact bound registration
        (object hash, registration_id, artifact_root). Eligibility is
        re-evaluated from the registered requirements (SEM-16/D-57), the
        bound registration resolves or HALTs (INV-53), and a declared
        environment constraint is matched against the bound capture (a
        mismatch is refused; t-s12's env_mismatch arm is the sequence-level
        HALT the caller then applies). INV-17's binding is therefore
        enforced at this seam: a receipt for another candidate, another
        environment, another registration, another assertion or another
        attempt dies here (CH4-verification-binding)."""
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
            registration, registration_hash = \
                self._bound_verifier_registration(
                    cur, payload, assertion.get("verifier_id"))
            refusal = verifier_eligibility(assertion, registration)
            if refusal is not None:
                raise ConflictError(refusal)
            candidate = self._candidate_row(cur, attempt_id)
            if candidate is None:
                raise IntegrityHalt(
                    "attempt %s is past CANDIDATE_FROZEN with no admitted"
                    " obj-16 candidate (forged state)" % attempt_id)
            env_address = self._bind_environment(cur, att, environment, now)
            constraint = assertion.get("environment_constraint")
            if env_address is None:
                raise ConflictError(
                    "no obj-18 capture is bound to attempt %s: every"
                    " verification receipt binds the attempt's admitted"
                    " environment (D-56/INV-17)" % attempt_id)
            if constraint is not None:
                env_payload = json.loads(
                    self.store.get(env_address).decode("utf-8"))
                refusal = environment_satisfies(constraint, env_payload)
                if refusal is not None:
                    raise ConflictError(
                        "environment mismatch (t-s12 env_mismatch): %s"
                        % refusal)
            try:
                receipt_payload = json.loads(receipt.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ConflictError(
                    "receipt bytes are not UTF-8 JSON (obj-19/obj-20)")
            if not isinstance(receipt_payload, dict) or \
                    verification_receipts.receipt_bytes(receipt_payload) \
                    != receipt:
                raise ConflictError(
                    "receipt bytes are not their own canonical form: the"
                    " admitted address must be the payload's identity")
            issues = verification_receipts.validate_result_receipt(
                receipt_payload)
            if issues:
                raise ConflictError(
                    "receipt failed obj-19/obj-20 schema validation: %s"
                    % "; ".join(repr(issue) for issue in issues))
            expected_kind = verification_receipts.RESULT_KINDS[outcome]
            if receipt_payload["kind"] != expected_kind:
                raise ConflictError(
                    "outcome %s must carry %s bytes, not %r"
                    % (outcome, expected_kind, receipt_payload["kind"]))
            if receipt_payload["outcome"] != outcome:
                raise ConflictError(
                    "receipt outcome %r does not agree with the declared"
                    " outcome %s" % (receipt_payload["outcome"], outcome))
            if receipt_payload["kind"] == "VerificationFailureReceipt":
                allowed = verification_receipts.CONCLUSION_BASES[outcome]
                if receipt_payload["conclusion_basis"] not in allowed:
                    raise ConflictError(
                        "conclusion_basis %r is not registered for outcome"
                        " %s (authority uncertainty is never an ordinary"
                        " FAIL)" % (receipt_payload["conclusion_basis"],
                                    outcome))
            identity = receipt_payload["attempt"]
            if (identity["sequence_id"] != att["sequence_id"]
                    or identity["attempt_id"] != att["attempt_id"]
                    or identity["fence"] != seq["fence"]):
                raise ConflictError(
                    "receipt attempt identity %r does not bind this attempt"
                    " at its current fence (INV-13/INV-18: a stale or"
                    " foreign receipt has no authority)" % (identity,))
            if receipt_payload["attempt_contract_hash"] != \
                    att["attempt_contract_hash"]:
                raise ConflictError(
                    "receipt attempt_contract_hash must match the durable"
                    " contract (INV-17)")
            if receipt_payload["record_hash"] != seq["record_hash"]:
                raise ConflictError(
                    "receipt record_hash must match the Sequence's frozen"
                    " record (INV-17)")
            if receipt_payload["candidate_receipt_sha256"] != \
                    candidate["content_sha256"]:
                raise ConflictError(
                    "receipt binds candidate %r, not the attempt's admitted"
                    " obj-16 %s (INV-17: the verified subject is the frozen"
                    " candidate)" % (
                        receipt_payload["candidate_receipt_sha256"],
                        candidate["content_sha256"]))
            if receipt_payload["environment_sha256"] != env_address:
                raise ConflictError(
                    "receipt binds environment %r, not the attempt's"
                    " admitted obj-18 %s (INV-17)"
                    % (receipt_payload["environment_sha256"], env_address))
            part = receipt_payload["assertion"]["part"]
            if (part["record"] != seq["record_hash"]
                    or part["part_id"] != assertion_id):
                raise ConflictError(
                    "receipt assertion part reference %r does not name"
                    " assertion %r of the frozen record (D-47)"
                    % (part, assertion_id))
            if receipt_payload["assertion"]["verifier_id"] != \
                    assertion.get("verifier_id"):
                raise ConflictError(
                    "receipt verifier_id %r is not the assertion's declared"
                    " verifier %r" % (
                        receipt_payload["assertion"]["verifier_id"],
                        assertion.get("verifier_id")))
            bound_registration = receipt_payload["registration"]
            if (bound_registration["object_sha256"] != registration_hash
                    or bound_registration["registration_id"]
                    != registration["registration_id"]
                    or bound_registration["artifact_root"]
                    != registration["artifact_root"]):
                raise ConflictError(
                    "receipt registration %r is not the frozen record's"
                    " bound registration %s (INV-52/INV-53: only the exact"
                    " registered verifier produces results)"
                    % (bound_registration, registration_hash))
            # F-C4-1: the claimed outcome must agree with the durable
            # execution evidence — the exact output bytes must resolve and
            # support it under the registered result protocol.
            output_address = receipt_payload["execution"]["output_sha256"]
            if not self.store.has(output_address):
                raise ConflictError(
                    "the receipt's execution output evidence %s does not"
                    " resolve in the content store (F-C4-1: an outcome"
                    " without evidence has no authority)" % output_address)
            refusal = verification_receipts.outcome_consistency(
                receipt_payload, self.store.get(output_address))
            if refusal is not None:
                raise ConflictError(
                    "receipt outcome disagrees with its execution evidence"
                    " (F-C4-1): %s" % refusal)
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

    def register_transform_check(self, attempt_id, role, outcome, by,
                                 receipt, environment=None):
        """Admit one role-transform check result (C4-M2, INV-44/INV-49):
        the after-the-fact proof that the child dispatched for ``role``
        RECEIVED exactly the declared registered transform's output over
        the frozen record's declared specification input.

        The ledger row key is ``role-transform/<role>`` (the name_id
        grammar forbids ``/``, so no assertion id can collide); outcome
        PASS/FAIL/INCONCLUSIVE with the runner's canonical obj-19/obj-20
        bytes on the ROLE_TRANSFORM arm, under the same INV-17 binding
        authority as assertion results.

        D-C3-2-grade admission: for any completed-transform claim (exit 0)
        this seam RE-EXECUTES the bound transform closure over the exact
        declared input bytes and refuses a receipt whose expected output
        identity is not the one it just re-derived. The OBSERVED identity
        comes only from admitted obj-11 producer bytes (exactly one
        distinct recorded prompt identity; zero admits only INCONCLUSIVE
        missing_producer_evidence; several distinct are an ambiguity
        refusal) — a child claiming "this is the prompt I received" is not
        enough, and neither is the runner (INV-49). When the attempt's
        projection carries transform_provenance, the dispatcher's claim
        must agree with the re-derivation (INV-44: dispatcher drift dies
        at this seam too)."""
        if outcome not in VERIFIER_OUTCOMES:
            raise ConflictError(
                "outcome %r is not in the verifier vocabulary %r"
                % (outcome, VERIFIER_OUTCOMES))
        if by != RESULT_PRODUCER:
            raise ConflictError(
                "%r is not the registered obj-19/obj-20 minter %r"
                % (by, RESULT_PRODUCER))
        receipt = bytes(receipt)
        row_key = "role-transform/%s" % role
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att, seq = self._verification_context(cur, attempt_id)
            payload = self._verified_record(cur, seq["record_hash"])
            transform_id = payload.get("role_transforms", {}).get(role)
            if transform_id is None:
                raise ConflictError(
                    "the frozen record declares no transform for role %r"
                    " (INV-49 checks declared role transforms only)"
                    % (role,))
            call = self._attempt_call(cur, attempt_id)
            if call["role"] != role:
                raise ConflictError(
                    "attempt %s dispatched role %r, not %r: the role-"
                    "transform check verifies the child that actually ran"
                    % (attempt_id, call["role"], role))
            registration, registration_hash = \
                self._bound_transform_registration(cur, payload,
                                                   transform_id)
            candidate = self._candidate_row(cur, attempt_id)
            if candidate is None:
                raise IntegrityHalt(
                    "attempt %s is past CANDIDATE_FROZEN with no admitted"
                    " obj-16 candidate (forged state)" % attempt_id)
            env_address = self._bind_environment(cur, att, environment, now)
            if env_address is None:
                raise ConflictError(
                    "no obj-18 capture is bound to attempt %s: every"
                    " BY_VERIFIER receipt binds the attempt's admitted"
                    " environment (D-56/INV-17)" % attempt_id)
            inputs = payload.get("specification_inputs", {}).get(
                "name_to_exact_byte_sha256", {})
            if len(inputs) != 1:
                raise ConflictError(
                    "the frozen record declares %d specification inputs;"
                    " the v1 role-transform binding is defined over exactly"
                    " ONE (the child's prompt is the declared transform's"
                    " output over the record's single specification input)"
                    % len(inputs))
            input_name = sorted(inputs)[0]
            input_address = inputs[input_name]
            if not self.store.has(input_address):
                raise IntegrityHalt(
                    "declared specification input %r (%s) has lost its"
                    " bytes (INV-53/D-66 HALT)"
                    % (input_name, input_address))
            try:
                receipt_payload = json.loads(receipt.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ConflictError(
                    "receipt bytes are not UTF-8 JSON (obj-19/obj-20)")
            if not isinstance(receipt_payload, dict) or \
                    verification_receipts.receipt_bytes(receipt_payload) \
                    != receipt:
                raise ConflictError(
                    "receipt bytes are not their own canonical form: the"
                    " admitted address must be the payload's identity")
            issues = verification_receipts.validate_result_receipt(
                receipt_payload)
            if issues:
                raise ConflictError(
                    "receipt failed obj-19/obj-20 schema validation: %s"
                    % "; ".join(repr(issue) for issue in issues))
            part = receipt_payload["assertion"]["part"]
            if part.get("part_kind") != "ROLE_TRANSFORM":
                raise ConflictError(
                    "this seam admits ROLE_TRANSFORM results only; the"
                    " receipt names part_kind %r (D-47: a result for one"
                    " part kind never satisfies another)"
                    % (part.get("part_kind"),))
            expected_kind = verification_receipts.RESULT_KINDS[outcome]
            if receipt_payload["kind"] != expected_kind:
                raise ConflictError(
                    "outcome %s must carry %s bytes, not %r"
                    % (outcome, expected_kind, receipt_payload["kind"]))
            if receipt_payload["outcome"] != outcome:
                raise ConflictError(
                    "receipt outcome %r does not agree with the declared"
                    " outcome %s" % (receipt_payload["outcome"], outcome))
            if receipt_payload["kind"] == "VerificationFailureReceipt":
                allowed = verification_receipts.TRANSFORM_CONCLUSION_BASES[
                    outcome]
                if receipt_payload["conclusion_basis"] not in allowed:
                    raise ConflictError(
                        "conclusion_basis %r is not registered for a"
                        " ROLE_TRANSFORM %s (allowed: %s)"
                        % (receipt_payload["conclusion_basis"], outcome,
                           ", ".join(allowed)))
            identity = receipt_payload["attempt"]
            if (identity["sequence_id"] != att["sequence_id"]
                    or identity["attempt_id"] != att["attempt_id"]
                    or identity["fence"] != seq["fence"]):
                raise ConflictError(
                    "receipt attempt identity %r does not bind this attempt"
                    " at its current fence (INV-13/INV-18)" % (identity,))
            if receipt_payload["attempt_contract_hash"] != \
                    att["attempt_contract_hash"]:
                raise ConflictError(
                    "receipt attempt_contract_hash must match the durable"
                    " contract (INV-17)")
            if receipt_payload["record_hash"] != seq["record_hash"]:
                raise ConflictError(
                    "receipt record_hash must match the Sequence's frozen"
                    " record (INV-17)")
            if receipt_payload["candidate_receipt_sha256"] != \
                    candidate["content_sha256"]:
                raise ConflictError(
                    "receipt binds candidate %r, not the attempt's admitted"
                    " obj-16 %s (INV-17)" % (
                        receipt_payload["candidate_receipt_sha256"],
                        candidate["content_sha256"]))
            if receipt_payload["environment_sha256"] != env_address:
                raise ConflictError(
                    "receipt binds environment %r, not the attempt's"
                    " admitted obj-18 %s (INV-17)"
                    % (receipt_payload["environment_sha256"], env_address))
            if part["record"] != seq["record_hash"] or \
                    part["part_id"] != role:
                raise ConflictError(
                    "receipt part reference %r does not name the frozen"
                    " record's role-transform binding for %r (D-47)"
                    % (part, role))
            if receipt_payload["assertion"]["verifier_id"] != transform_id:
                raise ConflictError(
                    "receipt verifier_id %r is not the declared transform"
                    " %r (INV-44)" % (
                        receipt_payload["assertion"]["verifier_id"],
                        transform_id))
            bound_registration = receipt_payload["registration"]
            if (bound_registration["object_sha256"] != registration_hash
                    or bound_registration["registration_id"]
                    != registration["registration_id"]
                    or bound_registration["artifact_root"]
                    != registration["artifact_root"]):
                raise ConflictError(
                    "receipt registration %r is not the frozen record's"
                    " bound transform registration %s (INV-52/INV-53: only"
                    " the exact registered transform is the A1 authority)"
                    % (bound_registration, registration_hash))
            check = receipt_payload["transform_check"]
            if check["input_name"] != input_name or \
                    check["input_sha256"] != input_address:
                raise ConflictError(
                    "receipt transform_check names input %r (%s); the"
                    " frozen record's declared specification input is %r"
                    " (%s) (INV-44: specification identity is exact bytes)"
                    % (check["input_name"], check["input_sha256"],
                       input_name, input_address))
            output_address = receipt_payload["execution"]["output_sha256"]
            if not self.store.has(output_address):
                raise ConflictError(
                    "the receipt's execution output evidence %s does not"
                    " resolve in the content store (F-C4-1)"
                    % output_address)
            refusal = verification_receipts.outcome_consistency(
                receipt_payload, self.store.get(output_address))
            if refusal is not None:
                raise ConflictError(
                    "receipt outcome disagrees with its evidence (F-C4-1):"
                    " %s" % refusal)
            # INV-49's observed side: admitted producer bytes ONLY — the
            # receipt may carry exactly what the admitted evidence derives,
            # nothing else.
            observed = self._observed_prompt_identity(cur, attempt_id)
            claimed_observed = check.get("observed_sha256")
            if observed is None and claimed_observed is not None:
                raise ConflictError(
                    "the receipt claims an observed prompt identity but no"
                    " admitted obj-11 producer record carries one (INV-49:"
                    " a claim is not producer evidence)")
            if observed is not None and claimed_observed != observed:
                raise ConflictError(
                    "the receipt's observed prompt identity %r is not the"
                    " admitted producer evidence %s (INV-49: observed is"
                    " derived from admitted bytes, never claimed)"
                    % (claimed_observed, observed))
            # D-C3-2-grade re-derivation for completed-transform claims:
            # the expected identity must be the one THIS admission just
            # recomputed by executing the exact bound closure over the
            # exact declared input bytes.
            if receipt_payload["execution"]["exit_code"] == 0:
                output_bytes, execution = transforms.execute_transform(
                    self.store, registration,
                    self.store.get(input_address))
                if output_bytes is None:
                    raise ConflictError(
                        "the bound transform does not complete over the"
                        " declared input (exit %r): the receipt's"
                        " completed-transform claim does not re-derive"
                        % (execution["exit_code"],))
                derived = execution["output_sha256"]
                if check.get("expected_sha256") != derived:
                    raise ConflictError(
                        "the receipt's expected output identity %r is not"
                        " the admission's re-derived transform output %s"
                        " (D-C3-2: full re-derivation, never trust)"
                        % (check.get("expected_sha256"), derived))
            # INV-44: the dispatcher's projected claim, when present, must
            # agree with the re-derivation — never the other way round.
            # The ledger only requires the projection address to RESOLVE
            # (obj-10's canonical form arrived with chapter 3; older flows
            # bind plain bytes), so a projection that is not a JSON object
            # simply carries no claim — tolerance here grants nothing,
            # because the check's authority is the re-derivation above.
            provenance = None
            try:
                projection = json.loads(self.store.get(
                    call["projection_address"]).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                projection = None
            if isinstance(projection, dict):
                provenance = projection.get("transform_provenance")
            if provenance is not None:
                if (provenance.get("transform_id") != transform_id
                        or provenance.get("registration")
                        != registration_hash
                        or provenance.get("input_name") != input_name
                        or provenance.get("input_sha256") != input_address):
                    raise ConflictError(
                        "the projection's transform_provenance does not"
                        " bind the frozen record's declared transform chain"
                        " (INV-44: dispatcher drift)")
                if (receipt_payload["execution"]["exit_code"] == 0
                        and provenance.get("output_sha256")
                        != check.get("expected_sha256")):
                    raise ConflictError(
                        "the projection claims transform output %r but the"
                        " admission re-derived %s (INV-44: the dispatcher's"
                        " claim never overrides the registered transform)"
                        % (provenance.get("output_sha256"),
                           check.get("expected_sha256")))
            receipt_address = self.store.put(receipt)
            existing = self._existing_verification(
                cur, attempt_id, row_key, outcome, receipt_address,
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
                (verification_id, attempt_id, row_key, transform_id,
                 outcome, by, receipt_address, env_address, now))
            journal.append(cur, journal.transition_record(
                machine="attempt_verification", subject_id=verification_id,
                rule="ch4:register-transform-check", to_state="RECORDED",
                recorded_utc=now,
                facts={"attempt_id": attempt_id,
                       "sequence_id": att["sequence_id"],
                       "role": role, "transform_id": transform_id,
                       "outcome": outcome, "by": by,
                       "receipt_sha256": receipt_address,
                       "environment_sha256": env_address,
                       "fence": att["fence"]}))
            return {"verification_id": verification_id,
                    "receipt_address": receipt_address, "duplicate": False}

    def register_attested_result(self, attempt_id, assertion_id, by,
                                 receipt):
        """Admit the runner's canonical obj-19 BY_ATTESTATION result for
        one HUMAN_ATTESTED assertion (C4-M2, D-52/D-53): the receipt bytes
        must be their own canonical form, validate against the obj-19
        schema's BY_ATTESTATION arm, and bind — by equality against
        durable ledger facts — the exact Attempt at its CURRENT fence, the
        frozen record, the admitted obj-16 candidate, the D-47 ASSERTION
        part and the attempt's ADMITTED obj-37 attestation bytes. No
        registration, no execution, no environment: an attestation is a
        human act, and nothing here may dress it up as an execution. The
        row's authority principal is YOU (the attester), not the
        incorporating runner."""
        if by != RESULT_PRODUCER:
            raise ConflictError(
                "%r is not the registered obj-19 minter %r"
                % (by, RESULT_PRODUCER))
        receipt = bytes(receipt)
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att, seq = self._verification_context(cur, attempt_id)
            payload = self._verified_record(cur, seq["record_hash"])
            assertion = self._assertion(payload, assertion_id)
            if assertion.get("mode") != "HUMAN_ATTESTED":
                raise ConflictError(
                    "assertion %r is %s: BY_ATTESTATION results exist only"
                    " for HUMAN_ATTESTED assertions (D-52)"
                    % (assertion_id, assertion.get("mode")))
            candidate = self._candidate_row(cur, attempt_id)
            if candidate is None:
                raise IntegrityHalt(
                    "attempt %s is past CANDIDATE_FROZEN with no admitted"
                    " obj-16 candidate (forged state)" % attempt_id)
            attestation_row = cur.execute(
                "SELECT * FROM attempt_attestations WHERE attempt_id = ?"
                " AND assertion_id = ?",
                (attempt_id, assertion_id)).fetchone()
            if attestation_row is None:
                raise ConflictError(
                    "no admitted obj-37 attestation exists for assertion %r"
                    " of attempt %s: a BY_ATTESTATION result INCORPORATES"
                    " an admitted attestation, never asserts one"
                    % (assertion_id, attempt_id))
            try:
                receipt_payload = json.loads(receipt.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ConflictError(
                    "receipt bytes are not UTF-8 JSON (obj-19)")
            if not isinstance(receipt_payload, dict) or \
                    verification_receipts.receipt_bytes(receipt_payload) \
                    != receipt:
                raise ConflictError(
                    "receipt bytes are not their own canonical form: the"
                    " admitted address must be the payload's identity")
            issues = verification_receipts.validate_result_receipt(
                receipt_payload)
            if issues:
                raise ConflictError(
                    "receipt failed obj-19 schema validation: %s"
                    % "; ".join(repr(issue) for issue in issues))
            if receipt_payload.get("result_source") != "BY_ATTESTATION":
                raise ConflictError(
                    "this seam admits the BY_ATTESTATION arm only; the"
                    " receipt declares result_source %r"
                    % (receipt_payload.get("result_source"),))
            identity = receipt_payload["attempt"]
            if (identity["sequence_id"] != att["sequence_id"]
                    or identity["attempt_id"] != att["attempt_id"]
                    or identity["fence"] != seq["fence"]):
                raise ConflictError(
                    "receipt attempt identity %r does not bind this attempt"
                    " at its current fence (INV-13/INV-18)" % (identity,))
            if receipt_payload["attempt_contract_hash"] != \
                    att["attempt_contract_hash"]:
                raise ConflictError(
                    "receipt attempt_contract_hash must match the durable"
                    " contract (INV-17)")
            if receipt_payload["record_hash"] != seq["record_hash"]:
                raise ConflictError(
                    "receipt record_hash must match the Sequence's frozen"
                    " record (INV-17)")
            if receipt_payload["candidate_receipt_sha256"] != \
                    candidate["content_sha256"]:
                raise ConflictError(
                    "receipt binds candidate %r, not the attempt's admitted"
                    " obj-16 %s (D-53: the attested verdict is about THIS"
                    " frozen candidate)" % (
                        receipt_payload["candidate_receipt_sha256"],
                        candidate["content_sha256"]))
            part = receipt_payload["assertion"]["part"]
            if (part["record"] != seq["record_hash"]
                    or part["part_id"] != assertion_id):
                raise ConflictError(
                    "receipt part reference %r does not name assertion %r"
                    " of the frozen record (D-47)" % (part, assertion_id))
            if receipt_payload["attestation_sha256"] != \
                    attestation_row["content_sha256"]:
                raise ConflictError(
                    "receipt binds attestation %r, not the admitted obj-37"
                    " %s (D-53: the result incorporates the exact admitted"
                    " attestation bytes)" % (
                        receipt_payload["attestation_sha256"],
                        attestation_row["content_sha256"]))
            receipt_address = self.store.put(receipt)
            existing = self._existing_verification(
                cur, attempt_id, assertion_id, "ATTESTED", receipt_address,
                None)
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
                 receipt_address, now))
            journal.append(cur, journal.transition_record(
                machine="attempt_verification", subject_id=verification_id,
                rule="ch4:register-attested-result", to_state="RECORDED",
                recorded_utc=now,
                facts={"attempt_id": attempt_id,
                       "sequence_id": att["sequence_id"],
                       "assertion_id": assertion_id,
                       "mode": "HUMAN_ATTESTED", "outcome": "ATTESTED",
                       "by": ATTESTATION_PRODUCER,
                       "incorporated_by": by,
                       "receipt_sha256": receipt_address,
                       "attestation_sha256":
                           attestation_row["content_sha256"],
                       "fence": att["fence"]}))
            return {"verification_id": verification_id,
                    "receipt_address": receipt_address, "duplicate": False}

    def attest_assertion(self, attempt_id, assertion_id, attestation):
        """D-52/D-53 (reshaped in C4-M2): ADMIT your
        ManualAttestation.ASSERTION for one HUMAN_ATTESTED assertion —
        durable identity (minted by you), provenance (the attested
        verdict, preserved by exact bytes and journaled) and FULL scope
        binding: this attempt, this contract hash, the admitted obj-16
        candidate the verdict is about, and the D-47 typed part reference.
        The admission stores the exact bytes and writes the
        attempt_attestations row; the VERIFICATION row is written only
        when the verifier runner incorporates the admitted attestation
        into a canonical obj-19 BY_ATTESTATION result
        (``register_attested_result``) — so attestation remains durably
        recorded BEFORE conclude_verification can commit t-s10
        (attestation_before_commit_if_source_human_required), and the
        attestation itself never doubles as a result receipt."""
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
            candidate = self._candidate_row(cur, attempt_id)
            if candidate is None:
                raise IntegrityHalt(
                    "attempt %s is past CANDIDATE_FROZEN with no admitted"
                    " obj-16 candidate (forged state)" % attempt_id)
            refusal = validate_assertion_attestation(
                attestation, att, assertion_id,
                candidate["content_sha256"], seq["record_hash"])
            if refusal is not None:
                raise AdmissionError(refusal)
            # The D-29 kind-grammar finding on t-i07 applies identically:
            # obj-37's dotted kind has no object hash under Chapter 1 as
            # frozen, so the attestation is preserved by exact bytes (D-31).
            address = self.store.put(canonicalize(attestation))
            existing = cur.execute(
                "SELECT * FROM attempt_attestations WHERE attempt_id = ?"
                " AND assertion_id = ?",
                (attempt_id, assertion_id)).fetchone()
            if existing is not None:
                if existing["content_sha256"] == address:
                    return {"admission_id": existing["admission_id"],
                            "attestation_address": address,
                            "duplicate": True}
                raise ConflictError(
                    "assertion %r already has a different admitted"
                    " attestation for this attempt (admissions never"
                    " overwrite)" % (assertion_id,))
            admission_id = mint_id("ma")
            cur.execute(
                "INSERT INTO attempt_attestations (admission_id,"
                " attempt_id, assertion_id, content_sha256, admitted_utc)"
                " VALUES (?, ?, ?, ?, ?)",
                (admission_id, attempt_id, assertion_id, address, now))
            journal.append(cur, journal.transition_record(
                machine="attempt_attestation", subject_id=admission_id,
                rule="ch4:admit-attestation", to_state="ADMITTED",
                recorded_utc=now,
                facts={"attempt_id": attempt_id,
                       "sequence_id": att["sequence_id"],
                       "assertion_id": assertion_id,
                       "content_sha256": address,
                       "candidate_receipt_sha256":
                           candidate["content_sha256"],
                       "attested_by": ATTESTATION_PRODUCER,
                       "attests": "PASS",
                       "fence": att["fence"]}))
            return {"admission_id": admission_id,
                    "attestation_address": address, "duplicate": False}

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
            # INV-49 (C4-M2): when the frozen record declares a transform
            # for the DISPATCHED role, an admitted role-transform check is
            # part of verification — NO conclusion exists without it (its
            # FAIL drives t-a08/t-s11 like any row; its INCONCLUSIVE blocks
            # to t-s12's unknown arm like any row).
            role_transforms = payload.get("role_transforms", {})
            if role_transforms:
                call = self._attempt_call(cur, attempt_id)
                if call["role"] in role_transforms:
                    transform_key = "role-transform/%s" % call["role"]
                    if transform_key not in rows:
                        raise ConflictError(
                            "the frozen record declares a role transform"
                            " for the dispatched role %r and no admitted"
                            " role-transform check exists (INV-49: child"
                            " prompts are checked after the fact;"
                            " conclusion is impossible without the check)"
                            % (call["role"],))
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
            transform_rows = sorted(a_id for a_id in rows
                                    if a_id.startswith("role-transform/"))
            self._cas_attempt(cur, att, "VERIFIED", now,
                              require_sequence_fence=True)
            facts = self._quadruple(att)
            facts.update({"assertion_ids": [a["assertion_id"]
                                            for a in declared],
                          "verification_receipts": pass_receipts})
            if attested:  # D-37
                facts["attested_assertion_ids"] = attested
            if transform_rows:  # C4-M2 (INV-49): the check is journaled
                facts["role_transform_checks"] = {
                    a_id: rows[a_id]["receipt_sha256"]
                    for a_id in transform_rows}
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
            if transform_rows:  # C4-M2 (INV-49)
                seq_facts["role_transform_checks"] = {
                    a_id: rows[a_id]["receipt_sha256"]
                    for a_id in transform_rows}
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
