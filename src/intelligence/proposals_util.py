"""Deterministic JSON identity for intelligence-plane records.

The protocol's canonical bytes (Chapter 1 ``canonicalize``) govern every
AUTHORITY object; intelligence-plane records (proposals, assignments)
need only a stable digest for convergence across restarts. One rule,
used everywhere in this package: sorted-key, UTF-8, no-whitespace JSON,
sha256 hex.
"""

import hashlib
import json


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def canonical_json_digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()
