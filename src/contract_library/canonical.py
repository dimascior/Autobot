"""RFC 8785 (JCS) canonicalization, restricted to the D-24 protocol subset.

The subset: JSON objects, arrays, strings, booleans and integers in
[0, 2**53 - 1]. Fractional numbers, negative numbers, null and any other
value are refused outright (D-24, D-37), which makes the RFC 8785 number
serialization reduce to exact decimal digits. Object keys are sorted by
UTF-16 code units (RFC 8785 section 3.2.3); strings are escaped per
section 3.2.2.2 and the result is strict UTF-8.

canonicalize() hashes exactly what it is given; admission-time rules
(NFC, set order, grammars) live in validate.py. Everything here is
stdlib-only (D-59) and deterministic.
"""

INT_MAX = 2 ** 53 - 1

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class CanonicalizationError(ValueError):
    """A value outside the D-24 protocol subset, or invalid Unicode."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _escape_string(value):
    out = []
    for ch in value:
        short = _SHORT_ESCAPES.get(ch)
        if short is not None:
            out.append(short)
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _sort_key(key):
    # RFC 8785 sorts property names by UTF-16 code units.
    try:
        return key.encode("utf-16-be")
    except UnicodeEncodeError:
        raise CanonicalizationError(
            "ERR_INVALID_UNICODE", "object key contains an unpaired surrogate"
        )


def _serialize(value, out):
    if value is None:
        raise CanonicalizationError("ERR_NULL_FORBIDDEN", "null is forbidden (D-37)")
    if isinstance(value, bool):
        out.append("true" if value else "false")
        return
    if isinstance(value, int):
        if value < 0 or value > INT_MAX:
            raise CanonicalizationError(
                "ERR_INT_RANGE",
                "integer %d outside [0, 2**53-1] (D-24)" % value,
            )
        out.append(str(value))
        return
    if isinstance(value, float):
        raise CanonicalizationError(
            "ERR_FLOAT_FORBIDDEN", "fractional numbers are forbidden (D-24)"
        )
    if isinstance(value, str):
        out.append(_escape_string(value))
        return
    if isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _serialize(item, out)
        out.append("]")
        return
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError(
                    "ERR_KEY_TYPE", "object keys must be strings"
                )
        out.append("{")
        for i, key in enumerate(sorted(value, key=_sort_key)):
            if i:
                out.append(",")
            out.append(_escape_string(key))
            out.append(":")
            _serialize(value[key], out)
        out.append("}")
        return
    raise CanonicalizationError(
        "ERR_VALUE_TYPE", "unsupported value type %r" % type(value).__name__
    )


def canonicalize(value):
    """Return the canonical UTF-8 bytes of ``value`` (RFC 8785, D-24 subset)."""
    out = []
    _serialize(value, out)
    text = "".join(out)
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        raise CanonicalizationError(
            "ERR_INVALID_UNICODE", "string contains an unpaired surrogate"
        )
