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
from .models import (
    ATTEMPT_TERMINAL,
    INT_MAX,
    AuthorityLost,
    ConflictError,
    IntegrityHalt,
    LeaseUnavailable,
    PermitExhaustedError,
    StaleFenceError,
    instant,
    mint_id,
)


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

        reverse_tables = {
            "sequence": ("sequences", "sequence_id"),
            "attempt": ("attempts", "attempt_id"),
            "permit_reservation": ("reservations", "reservation_id"),
            "standing_permit_state": ("permit_state", "sequence_id"),
            "registry_objects": ("objects", "object_hash"),
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

        # -- 6. harmless leftovers, reported and ignored
        orphans = self.store.orphans(referenced)

        return {"journal_entries": len(payloads),
                "objects_verified": objects_checked,
                "sequences": current, "orphans": orphans}
