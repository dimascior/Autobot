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
from .effects import EFFECTS, OUTCOME_RULES, RETRYABLE
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
            if payload.get("predecessors"):
                # Dependency release is a later milestone: any predecessor
                # edge is unsatisfied here (t-s01).
                version = self._cas_sequence(cur, row, now, state="BLOCKED")
                rule, to_state = "t-s01", "BLOCKED"
            else:
                version = self._cas_sequence(cur, row, now, state="READY")
                rule, to_state = "t-s03", "READY"
            journal.append(cur, journal.transition_record(
                machine="sequence", subject_id=sequence_id, rule=rule,
                from_state="DECLARED", to_state=to_state, recorded_utc=now,
                facts={"record_hash": row["record_hash"], "version": version,
                       "fence": row["fence"]},
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
    # t-a02..t-a06 plus the t-a10/t-a11/t-a13 failure arms: the authoritative
    # attempt lifecycle for one bound execution. The dispatcher/collector
    # TRIGGER these; the Sequencer applies them (chapter 0's actor model).
    # Receipt and witness payload SCHEMAS are later chapters' (obj-10/11/15/16
    # are form_only here): the transitions take content-addressed references
    # that must resolve in the CAS, and the deeper admission-join semantics
    # land with the M3 evidence seam (declared in INTEGRATION_PLAN.md).
    # t-a12 (STALE on member-lease expiry) is deferred with them.

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

    def dispatch_attempt(self, attempt_id, role, provider, call_id,
                         projection_address):
        """t-a02 RESERVED→DISPATCHED (trigger: dispatcher). call_id_bound and
        the obj-10 projection reference are the registered requirements; the
        role must be one the frozen record's completion_policies name."""
        now = instant(self.clock.now())
        with self.db.transaction() as cur:
            self._assert_lease(cur)
            att = self._attempt(cur, attempt_id)
            if att["state"] != "RESERVED":
                raise ConflictError(
                    "dispatch expects RESERVED, found %s" % att["state"])
            if not (isinstance(call_id, str) and call_id):
                raise ConflictError("call_id_bound requires a non-empty call id")
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

    def attempt_running(self, attempt_id, native_session_id, witness_address):
        """t-a03 DISPATCHED→RUNNING (trigger: collector): the execution
        handle must carry the provider's native session id, witnessed by an
        obj-11 reference."""
        now = instant(self.clock.now())
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
                "UPDATE attempt_calls SET native_session_id = ?, updated_utc = ?"
                " WHERE attempt_id = ?",
                (native_session_id, now, attempt_id))
            self._cas_attempt(cur, att, "RUNNING", now)
            facts = self._quadruple(att)
            facts.update({"native_session_id": native_session_id,
                          "witness_address": witness_address})
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a03",
                from_state="DISPATCHED", to_state="RUNNING",
                recorded_utc=now, facts=facts))

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
        """t-a05 FINISHED→CANDIDATE_FROZEN: fence_matches is registered here,
        so the presented fence, the row fence and the owning Sequence's fence
        are all conditions of the one UPDATE; obj-16 by reference."""
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

    def complete_evidence(self, attempt_id, expected_fence,
                          execution_receipt_address):
        """t-a06 CANDIDATE_FROZEN→EVIDENCE_COMPLETE: fence_matches plus
        required_joins_present — v1 reads the obj-15 reference and requires a
        non-empty admitted_witnesses list whose every address resolves; the
        full admission-join semantics are the M3 evidence seam's."""
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
            witnesses = receipt.get("admitted_witnesses")
            if not (isinstance(witnesses, list) and witnesses):
                raise ConflictError(
                    "required_joins_present: the execution receipt names no"
                    " admitted witnesses")
            missing = [w for w in witnesses
                       if not (isinstance(w, str) and self.store.has(w))]
            if missing:
                raise ConflictError(
                    "required_joins_present: admitted witnesses do not"
                    " resolve: %s" % ", ".join(missing[:3]))
            self._cas_attempt(cur, att, "EVIDENCE_COMPLETE", now,
                              require_sequence_fence=True)
            facts = self._quadruple(att)
            facts["execution_receipt_address"] = execution_receipt_address
            journal.append(cur, journal.transition_record(
                machine="attempt", subject_id=attempt_id, rule="t-a06",
                from_state="CANDIDATE_FROZEN", to_state="EVIDENCE_COMPLETE",
                recorded_utc=now, facts=facts))

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

    def fail_candidate(self, attempt_id, expected_fence,
                       execution_receipt_address, reason):
        """t-a13 CANDIDATE_FROZEN→FAILED. Registered preconditions enforced
        exactly (operator-identified corrections, 2026-11-10):

        * ``fence_matches``: the presented fence, the row fence and the
          owning Sequence's fence must agree, with the equality a condition
          of the mutating SQL — the same discipline as t-a05/t-a06.
        * ``execution_receipt_integrity_failed_or_incomplete_beyond_bound``:
          an INTEGRITY_FAILED receipt is final and fails the candidate at
          once; an INCOMPLETE receipt fails it only once its permitted
          completion bound has ELAPSED — the bound is the receipt-declared
          ``evidence_complete_by_utc`` (HOW that bound derives from D-21
          admission facts is Chapter 3's receipt semantics, so an
          INCOMPLETE receipt that declares no bound is refused as
          unevaluable: that arm stays PARTIAL until Chapter 3 supplies it).
          Premature incomplete evidence never fails the candidate."""
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
            receipt = self._resolve_receipt(
                cur, execution_receipt_address, att)
            # --- Authority boundary (fail-closed, PARTIAL until Ch3) ---
            # The receipt's completeness classification and any declared
            # deadline are self-asserted fields.  An INTEGRITY_FAILED
            # classification requires Chapter 3's receipt-admission
            # mechanism to establish it authoritatively; an INCOMPLETE
            # deadline must derive from an authoritative policy/window
            # or validated admission record (D-28).  Until those inputs
            # exist, fail_candidate cannot treat the receipt as
            # authoritative evidence for failing the candidate.
            raise ConflictError(
                "t-a13 receipt-classification authority is PARTIAL:"
                " Chapter 3's receipt-admission mechanism must"
                " establish the completeness and deadline fields as"
                " authoritative before this transition can proceed")
            # The classification logic below is preserved for Chapter 3.
            completeness = receipt.get("completeness")
            if completeness == "INTEGRITY_FAILED":
                bound_fact = None
            elif completeness == "INCOMPLETE":
                bound = receipt.get("evidence_complete_by_utc")
                if not isinstance(bound, str):
                    raise ConflictError(
                        "t-a13: an INCOMPLETE receipt must declare its"
                        " completion bound (evidence_complete_by_utc, a"
                        " Chapter 3 receipt fact) before it can fail the"
                        " candidate")
                if bound > now:
                    raise ConflictError(
                        "t-a13: incomplete evidence is premature — the"
                        " completion bound %s has not elapsed" % bound)
                bound_fact = bound
            else:
                raise ConflictError(
                    "t-a13 requires a receipt whose completeness is"
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
