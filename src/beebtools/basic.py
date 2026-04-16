# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC II facade module.

Unified API for all BBC BASIC program handling: tokenization,
detokenization, content sniffing, and text escaping. Higher layers
(disc.py, cli.py) import from this module rather than reaching into
the individual sub-modules.

Tokenization and detokenization delegate to the dialect-driven
state-machine engine in sophie.py; this module provides the public
entry points that resolve line numbering, binary packing, and
overflow handling around that engine. Content-inspection primitives
(looksLikeTokenizedBasic, looksLikePlainText, basicProgramSize)
answer low-level questions about BASIC program data. File-level
classification that combines these primitives with catalogue metadata
lives in disc.py alongside its consumers.

The pretty-printer (pretty.py) is a separate optional display transform;
its prettyPrint function is re-exported here for convenience so callers
have a single import point for all BASIC operations.
"""

import re
from typing import Callable, List, Optional, Tuple

from .pretty import compactLine, prettyPrint  # noqa: F401 - re-export
from .sophie import (  # noqa: F401 - re-export decodeLineRef, detokenize*
    decodeLineRef,
    detokenize as _sophieDetokenize,
    detokenizeLine as _sophieDetokenizeLine,
    tokenizeLine as _sophieTokenizeLine,
)
from .basic_dialects import BBC_BASIC_II


# =====================================================================
# Detokenizer
# =====================================================================


def basicProgramSize(data: bytes) -> int:
    """Return the number of bytes occupied by the BASIC program in data.

    Walks the tokenized line structure and returns the offset just past
    the 0x0D 0xFF end-of-program marker. If the file is entirely BASIC
    this equals len(data) (or close to it, with a few padding bytes).
    If there is appended machine code the return value will be much
    smaller than len(data).

    Returns 0 if data does not start with a valid BASIC line marker.
    """
    pos = 0

    while pos < len(data):
        if data[pos] != 0x0D:
            break

        pos += 1
        if pos >= len(data):
            break

        hi = data[pos]
        if hi == 0xFF:
            # End-of-program marker.  Program occupies bytes 0..pos inclusive.
            return pos + 1

        if pos + 2 >= len(data):
            break

        linelen = data[pos + 2]

        # A valid record is at least 4 bytes (hi, lo, len, trailing 0x0D).
        if linelen < 4:
            break

        pos = pos - 1 + linelen

    # Fell off the end without hitting 0xFF - return current position.
    return pos


def detokenize(data: bytes, dialect=BBC_BASIC_II) -> List[str]:
    """Convert a tokenized BBC BASIC program to LIST-style text lines.

    Thin wrapper around the dialect-driven detokenizer in `sophie`.
    Defaults to BBC BASIC II; pass a different Dialect instance to
    decode BBC BASIC IV (EDIT at 0xCE) or future dialects.

    Each line in the returned list is formatted as a right-justified
    5-character line number followed by the decoded statement text.
    """
    return _sophieDetokenize(data, dialect)


def _decodeLineContent(content: bytes) -> str:
    """Decode token bytes for one BASIC line into LIST text.

    Retained as an internal hook for callers that already hold a line
    body. Delegates to the dialect-driven engine at BBC BASIC II.

    Args:
        content: Raw token bytes for one BASIC line body.

    Returns:
        Decoded source text for this line body.
    """
    return _sophieDetokenizeLine(content, BBC_BASIC_II)


# =====================================================================
# Tokenizer
# =====================================================================


# -----------------------------------------------------------------------
# Line-number encoding
# -----------------------------------------------------------------------

def encodeLineRef(linenum: int) -> bytes:
    """Encode a line number as the 4-byte 0x8D inline reference.

    The format is:
        byte 0: 0x8D sentinel
        byte 1: control byte with inverted high bits XORed with 0x54
        byte 2: low byte of line number (bits 0-5) with bit 6 set
        byte 3: high byte of line number (bits 0-5) with bit 6 set

    Args:
        linenum: BBC BASIC line number (0-32767).

    Returns:
        Four bytes: the 0x8D marker followed by three encoded data bytes.
    """
    b1 = (((linenum & 0x00C0) >> 2) | ((linenum & 0xC000) >> 12)) ^ 0x54
    b2 = (linenum & 0x3F) | 0x40
    b3 = ((linenum >> 8) & 0x3F) | 0x40

    return bytes([0x8D, b1, b2, b3])


# -----------------------------------------------------------------------
# Line parsing
# -----------------------------------------------------------------------

_LINE_RE = re.compile(r"^\s*(\d+)(.*)")


def _parseLine(line: str) -> Tuple[int, str]:
    """Extract the line number and content from a LIST-style text line.

    Accepts formats like '   10PRINT "HELLO"' where the line number may
    be preceded by whitespace.

    Args:
        line: One line of LIST-style BASIC text.

    Returns:
        (line_number, content_text) tuple.

    Raises:
        ValueError: If the line does not start with a valid line number.
    """
    m = _LINE_RE.match(line)

    if not m:
        raise ValueError(f"Cannot parse line number from: {line!r}")

    return int(m.group(1)), m.group(2)


_AUTO_LINENUM_START = 1
_AUTO_LINENUM_STEP = 1


def _normalizeLines(lines: List[str]) -> List[Tuple[int, str]]:
    """Resolve each non-blank line to a (linenum, content_text) pair.

    Args:
        lines: Source lines, each optionally prefixed with a line number.

    Returns:
        List of (line_number, content_text) pairs for non-blank lines.

    Line-numbering rules:

    - A line beginning with a digit uses that explicit number, which
      must be strictly greater than the previous line's number.
    - A line not beginning with a digit is auto-numbered as
      last_line + 1, or 1 if no prior line has been numbered yet.
    - Blank or whitespace-only lines are dropped from the output but
      still advance the counter, so a numberless line that follows N
      blanks gets last_line + 1 + N.

    The two strategies (explicit and implicit) interleave freely in the
    same source file. Source authors are responsible for picking
    explicit numbers that stay ahead of where the auto-counter will
    land; jump and RESTORE targets are not rewritten.
    """
    pairs: List[Tuple[int, str]] = []
    last_line = -1

    for line in lines:
        stripped_left = line.lstrip()

        if not stripped_left:
            # Blank line: bump the counter so a later implicit line
            # gets a higher number, but emit nothing.
            last_line = (
                _AUTO_LINENUM_START
                if last_line < 0
                else last_line + _AUTO_LINENUM_STEP
            )
            continue

        if stripped_left[0].isdigit():
            linenum, content_text = _parseLine(line)
            if linenum <= last_line:
                raise ValueError(
                    f"Line numbers must increase: line {linenum} is "
                    f"not greater than previous line {last_line}"
                )
            last_line = linenum
        else:
            linenum = (
                _AUTO_LINENUM_START
                if last_line < 0
                else last_line + _AUTO_LINENUM_STEP
            )
            last_line = linenum
            # Prepend a single space so the stored bytes mirror the
            # explicit "<linenum> <content>" form. Trailing CR/LF is
            # dropped; leading indentation is preserved verbatim.
            content_text = " " + line.rstrip("\r\n")

        pairs.append((linenum, content_text))

    return pairs


# -----------------------------------------------------------------------
# Program assembly
# -----------------------------------------------------------------------

def tokenize(
    lines: List[str],
    on_overflow: Optional[Callable[[str, str], str]] = None,
) -> bytes:
    """Convert LIST-style text lines to tokenized BBC BASIC II binary.

    Accepts the output of detokenize() and produces bytes suitable for
    writing to a DFS disc image. Blank lines and lines containing only
    whitespace are silently skipped.

    Two source formats are accepted, selected by the first non-blank line:

    - Explicit line numbers: every line starts with a digit, optionally
      preceded by whitespace. Classic LIST-style output from detokenize().
    - No line numbers: no non-blank line begins with a digit. Line
      numbers are auto-injected starting at 1 in steps of 1, with blank
      lines advancing the counter.

    Args:
        lines:       List of strings in LIST format, each starting with a
                     line number (optionally preceded by whitespace), or
                     source with no line numbers at all.
        on_overflow: Optional callback invoked when a tokenized line exceeds
                     255 bytes. Receives (line_text, error_message) and
                     returns a replacement line to retry. If the replacement
                     still overflows, ValueError is raised.

    Returns:
        Tokenized BBC BASIC II program as bytes, including the
        end-of-program marker (0x0D 0xFF).

    Raises:
        ValueError: If a non-blank line cannot be parsed, or if a line
            still exceeds 255 bytes after the on_overflow callback.
    """
    result = bytearray()

    # Resolve each non-blank line to a (linenum, content) pair,
    # auto-numbering when the source has no explicit line numbers.
    pairs = _normalizeLines(lines)

    # Tokenize each line via the dialect-driven engine.
    for linenum, content_text in pairs:
        content = _sophieTokenizeLine(content_text, BBC_BASIC_II)

        hi = (linenum >> 8) & 0xFF
        lo = linenum & 0xFF

        # Length byte counts from the leading 0x0D through the content:
        # leading_0x0D + hi + lo + len_byte + content = 4 + len(content).
        # This is the Russell format used by BBC BASIC II on the 6502.
        linelen = 4 + len(content)

        # The length byte is a single unsigned byte (max 255).  If the
        # tokenized content exceeds that limit the program would be corrupt.
        if linelen > 255:
            msg = (f"Line {linenum} tokenizes to {linelen} bytes "
                   f"(max 255)")

            # Give the caller a chance to compact and retry. The callback
            # gets the explicit-form line so it can edit it in LIST style.
            if on_overflow is not None:
                replacement = on_overflow(f"{linenum}{content_text}", msg)
                _, content_text = _parseLine(replacement)
                content = _sophieTokenizeLine(content_text, BBC_BASIC_II)
                linelen = 4 + len(content)

            if linelen > 255:
                raise ValueError(msg)

        result.append(0x0D)
        result.append(hi)
        result.append(lo)
        result.append(linelen)
        result.extend(content)

    # End-of-program marker.
    result.append(0x0D)
    result.append(0xFF)

    return bytes(result)


# =====================================================================
# Content classification
# =====================================================================

def looksLikeTokenizedBasic(data: bytes) -> bool:
    """True if data contains a structurally valid Wilson/Acorn BASIC program.

    Walks the tokenized line structure looking for the 0x0D 0xFF
    end-of-program marker that every valid BBC BASIC program contains.

    A first-byte-only check is not sufficient because plain-text files
    beginning with CR (0x0D) would false-positive.

    See https://www.bbcbasic.net/wiki/doku.php?id=format for the
    canonical format-detection algorithm.
    """
    if len(data) < 2 or data[0] != 0x0D:
        return False

    # Walk the line structure and check for the 0xFF end marker.
    prog_size = basicProgramSize(data)
    return prog_size >= 2 and data[prog_size - 1] == 0xFF


# Bytes acceptable in a plain-text file: printable ASCII plus common
# whitespace (tab, carriage return, line feed).
_PLAIN_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def looksLikePlainText(data: bytes) -> bool:
    """True if every byte is printable ASCII or common whitespace.

    Checks for printable ASCII (0x20-0x7E) plus tab (0x09), line feed
    (0x0A), and carriage return (0x0D). An empty file is not plain text.
    """
    if not data:
        return False
    return all(b in _PLAIN_TEXT_BYTES for b in data)


# =====================================================================
# Text escaping for non-ASCII round-tripping
# =====================================================================

# Regex matching a \xHH escape sequence (two uppercase hex digits).
_ESCAPE_RE = re.compile(r"\\x([0-9A-F]{2})")


def hasEscapes(text: str) -> bool:
    """Return True if text contains any \\xHH escape sequence.

    Used by callers that want to decide whether to run unescapeNonAscii()
    on a block of text without scanning it twice.
    """
    return bool(_ESCAPE_RE.search(text))


def escapeNonAscii(line: str) -> str:
    """Replace non-printable-ASCII characters with \\xHH escapes.

    Characters outside the printable ASCII range 0x20-0x7E (e.g. BBC Micro
    teletext control codes embedded in PRINT strings) are replaced with a
    two-digit hex escape.  A literal backslash followed by 'x' is escaped
    as \\x5Cx to avoid ambiguity on the reverse trip.

    This is the forward half of a lossless round-trip.  Use unescapeNonAscii()
    to reverse.
    """
    out: List[str] = []

    for ch in line:
        code = ord(ch)
        if code == 0x5C:
            # Always escape backslash so the reverse is unambiguous.
            out.append("\\x5C")
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        else:
            out.append(f"\\x{code:02X}")

    return "".join(out)


def unescapeNonAscii(line: str) -> str:
    """Reverse escapeNonAscii - convert \\xHH sequences back to characters."""
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), line)
