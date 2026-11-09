"""Identifier, digest, instant, path and ref grammars (D-22, D-26, D-27, D-30, D-40).

Single source for every lexical rule the schemas also express as patterns;
harness checks keep the two in agreement with registries/identifiers.json.
Stdlib only.
"""

import re
import unicodedata

# D-30: the one digest spelling.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# D-27: exactly millisecond-precision UTC instants.
INSTANT_RE = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T"
    r"([01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z$"
)

# D-22: minted ids and name ids.
MINTED_PREFIXES = ("tc", "sq", "at", "rv", "in", "rg", "dg", "da", "ve", "lg")
MINTED_ID_RE = re.compile(r"^(%s)_[0-9a-f]{32}$" % "|".join(MINTED_PREFIXES))
NAME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# D-40: a PrincipalRef is a registered principals.json id.
PRINCIPAL_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_SEGMENT_FORBIDDEN = set("\\\x00")


def is_nfc(value):
    """True iff ``value`` is NFC-normalized valid Unicode without NUL."""
    if "\x00" in value:
        return False
    try:
        return unicodedata.normalize("NFC", value) == value
    except (TypeError, ValueError):
        return False


def minted_id_ok(value, prefix=None):
    if not MINTED_ID_RE.match(value):
        return False
    return prefix is None or value.startswith(prefix + "_")


def _segment_ok(segment):
    if segment in ("", ".", ".."):
        return False
    if any(ch in _SEGMENT_FORBIDDEN for ch in segment):
        return False
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in segment):
        return False
    if segment != segment.strip():
        return False
    return is_nfc(segment)


def repo_path_ok(value, allow_root=False):
    """D-26 RepoPath. ``allow_root`` admits the literal '.' (scope sets only)."""
    if value == ".":
        return bool(allow_root)
    if not value:
        return False
    return all(_segment_ok(seg) for seg in value.split("/"))


def path_contains(scope_entry, path):
    """D-26 containment: scope '.' contains everything; else prefix rule."""
    if scope_entry == ".":
        return True
    return path == scope_entry or path.startswith(scope_entry + "/")


def scope_within(inner_paths, outer_paths):
    """Every inner entry lies inside some outer entry (K-9 / INV-28)."""
    return all(
        any(path_contains(outer, inner) for outer in outer_paths)
        for inner in inner_paths
    )


_REF_FORBIDDEN_CHARS = set(" ~^:?*[\\\x7f") | {chr(c) for c in range(0x20)}


def ref_name_ok(value):
    """D-26 RefName: a full git ref under the canonical constraints."""
    if not value.startswith("refs/") or value.endswith("/") or "//" in value:
        return False
    if ".." in value or "@{" in value or value.endswith("."):
        return False
    segments = value.split("/")
    if len(segments) < 2:
        return False
    for seg in segments:
        if not seg or seg.startswith(".") or seg.endswith(".lock"):
            return False
        if any(ch in _REF_FORBIDDEN_CHARS for ch in seg):
            return False
        if not is_nfc(seg):
            return False
    return True


def utf16_key(value):
    """The one collation rule: UTF-16 code units (D-25, matching JCS)."""
    return value.encode("utf-16-be")
