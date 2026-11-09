"""An independent RFC 8785 (JCS) implementation for the D-58 dual-oracle.

Written directly against the RFC, sharing no code with autobot_contract:
different sort-key construction (explicit UTF-16 code-unit tuples instead of
an encode call), different escaping table construction, different traversal
(generator-based). It must agree byte-for-byte with the library on every
fixture and property input; a disagreement fails the conformance run.

Same D-24 subset: integers in [0, 2**53-1] only; null, floats and negatives
are errors.
"""

_INT_MAX = 2 ** 53 - 1


def _code_units(text):
    units = []
    for ch in text:
        cp = ord(ch)
        if cp <= 0xFFFF:
            units.append(cp)
        else:
            cp -= 0x10000
            units.append(0xD800 + (cp >> 10))
            units.append(0xDC00 + (cp & 0x3FF))
    return tuple(units)


def _escaped(text):
    pieces = ['"']
    for ch in text:
        cp = ord(ch)
        if ch == '"':
            pieces.append('\\"')
        elif ch == "\\":
            pieces.append("\\\\")
        elif cp >= 0x20:
            pieces.append(ch)
        elif ch == "\x08":
            pieces.append("\\b")
        elif ch == "\x09":
            pieces.append("\\t")
        elif ch == "\x0a":
            pieces.append("\\n")
        elif ch == "\x0c":
            pieces.append("\\f")
        elif ch == "\x0d":
            pieces.append("\\r")
        else:
            pieces.append("\\u" + format(cp, "04x"))
    pieces.append('"')
    return "".join(pieces)


def _emit(value):
    if value is True:
        yield "true"
        return
    if value is False:
        yield "false"
        return
    if value is None:
        raise ValueError("jcs2: null forbidden")
    if isinstance(value, float):
        raise ValueError("jcs2: float forbidden")
    if isinstance(value, int):
        if not (0 <= value <= _INT_MAX):
            raise ValueError("jcs2: integer out of range")
        yield repr(value)
        return
    if isinstance(value, str):
        yield _escaped(value)
        return
    if isinstance(value, (list, tuple)):
        yield "["
        first = True
        for item in value:
            if not first:
                yield ","
            first = False
            for piece in _emit(item):
                yield piece
        yield "]"
        return
    if isinstance(value, dict):
        yield "{"
        first = True
        for key in sorted(value.keys(), key=_code_units):
            if not isinstance(key, str):
                raise ValueError("jcs2: non-string key")
            if not first:
                yield ","
            first = False
            yield _escaped(key)
            yield ":"
            for piece in _emit(value[key]):
                yield piece
        yield "}"
        return
    raise ValueError("jcs2: unsupported type %r" % type(value).__name__)


def canonicalize2(value):
    return "".join(_emit(value)).encode("utf-8")
