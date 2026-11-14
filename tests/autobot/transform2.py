"""transform2 - independent references for the calibrated A1 ingestion
transform ``autoswarm-input-v1`` (TI-1 WP3).

The registered closure (support.LIVE_AUTOSWARM_TRANSFORM_SCRIPT) decodes
the whole input at once and applies a two-step ``str.replace`` chain. The
rule it implements was calibrated against the installed provider (chapter 4
VERIFICATION.md section 13, U-12):

    strict UTF-8 (input that is not UTF-8 is REFUSED, never decoded by
    guess); the BOM kept as U+FEFF; universal newlines (CRLF and lone CR
    fold to LF); nothing appended; nothing stripped.

This module states that same rule twice more, through two mechanisms that
share nothing with the registered script and little with each other:

``ingest``            the interpreter's own incremental text layer
                      (``io.TextIOWrapper`` in universal-newline mode,
                      read in small slices so the pending-CR and
                      split-multibyte paths are really exercised);
``ingest_automaton``  a byte automaton: strict UTF-8 validation with an
                      incremental decoder fed one byte at a time, then
                      newline folding on the BYTES themselves (0x0D and
                      0x0A never occur inside a multibyte UTF-8 sequence,
                      so folding needs no decoded text at all).

Both return the output bytes, or None when the input is refused. Neither
imports anything from ``autobot`` or ``contract_library``.
"""

import codecs
import io


def ingest(raw, slice_chars=5):
    """The calibrated rule through the incremental text layer."""
    wrapper = io.TextIOWrapper(io.BytesIO(bytes(raw)), encoding="utf-8",
                               errors="strict", newline=None)
    pieces = []
    try:
        while True:
            piece = wrapper.read(slice_chars)
            if not piece:
                break
            pieces.append(piece)
    except UnicodeDecodeError:
        return None
    return "".join(pieces).encode("utf-8")


def ingest_automaton(raw):
    """The calibrated rule as a byte automaton."""
    data = bytes(raw)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        for index in range(len(data)):
            decoder.decode(data[index:index + 1])
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return None
    out = bytearray()
    index = 0
    while index < len(data):
        byte = data[index]
        if byte == 0x0D:
            out.append(0x0A)
            if index + 1 < len(data) and data[index + 1] == 0x0A:
                index += 1
        else:
            out.append(byte)
        index += 1
    return bytes(out)
