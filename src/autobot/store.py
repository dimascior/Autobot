"""The content-addressed store (D-66): exact bytes keyed by their D-31
content address, beside — never inside — the ledger.

Write discipline: temp file → flush+fsync → atomic rename → directory fsync.
Windows cannot open directories for fsync, so directory-metadata durability
is not guaranteed there; the RECOVERY CONTRACT compensates and is explicit:

  * an orphaned temp file or unreferenced object is HARMLESS — recovery
    reports it and never consults it;
  * a committed ledger reference to missing or altered bytes is HALT
    (IntegrityHalt), exactly as D-66 states — enforced on every read, at
    put-collision time, and across the whole ledger by Sequencer.recover().

Objects are immutable: a second put of identical bytes is a no-op, and a
put that finds different bytes already at its address HALTs rather than
trusting or replacing them.
"""

import os
import secrets
from pathlib import Path

from contract_library import DIGEST_RE, content_address

from .models import IntegrityHalt


class Store:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "objects").mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)

    def _path_for(self, address):
        if not isinstance(address, str) or not DIGEST_RE.match(address):
            raise IntegrityHalt("not a D-30 content address: %r" % (address,))
        hexpart = address.split(":", 1)[1]
        return self.root / "objects" / hexpart[:2] / hexpart

    @staticmethod
    def _fsync_dir(path):
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except OSError:
            return  # Windows: directories cannot be opened for fsync
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def put(self, data):
        """Store exact bytes; returns their content address (D-31)."""
        address = content_address(data)
        final = self._path_for(address)
        if final.exists():
            # Idempotent only for identical bytes: a different file already
            # sitting at this address is tampering, never trusted (D-66).
            if content_address(final.read_bytes()) != address:
                raise IntegrityHalt(
                    "pre-existing bytes at %s do not re-hash to it (D-66 HALT)"
                    % address
                )
            return address
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.root / "tmp" / ("put-" + secrets.token_hex(8))
        with open(str(tmp), "wb") as fh:
            fh.write(bytes(data))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(final))
        self._fsync_dir(final.parent)
        return address

    def has(self, address):
        return self._path_for(address).exists()

    def get(self, address):
        """Exact bytes for a committed reference; re-hashes on every read.

        Missing or altered content under a committed reference is the D-66
        HALT case."""
        path = self._path_for(address)
        try:
            with open(str(path), "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            raise IntegrityHalt(
                "committed reference to missing content: %s (D-66 HALT)" % address
            )
        if content_address(data) != address:
            raise IntegrityHalt(
                "stored bytes do not re-hash to %s (D-66 HALT)" % address
            )
        return data

    def verify(self, addresses):
        """Returns the subset of ``addresses`` that do not resolve to exact
        bytes (used by recovery, D-69)."""
        bad = []
        for address in addresses:
            try:
                self.get(address)
            except IntegrityHalt:
                bad.append(address)
        return bad

    def orphans(self, referenced):
        """The harmless leftovers (D-66's recovery contract): temp files from
        torn puts, and stored objects no committed reference names. Reported,
        never consulted, never deleted here."""
        referenced = set(referenced)
        tmp_files = sorted(p.name for p in (self.root / "tmp").glob("*") if p.is_file())
        unreferenced = sorted(
            "sha256:" + p.name
            for p in (self.root / "objects").rglob("*")
            if p.is_file() and ("sha256:" + p.name) not in referenced
        )
        return {"tmp_files": tmp_files, "unreferenced_objects": unreferenced}
