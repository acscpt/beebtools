# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""
Tests for the basic.py module - BASIC content sniffers and non-ASCII
escape round-tripping.

Detokenization and tokenization are covered by test_detokenize.py and
test_tokenize.py respectively. File-level classification
(`classifyFileType`) lives in disc.py and is covered by test_disc.py.
"""

import pytest

from beebtools import (
    escapeNonAscii,
    looksLikeTokenizedBasic,
    looksLikePlainText,
    unescapeNonAscii,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def makeBasicProgram(*line_contents: bytes) -> bytes:
    """Build a minimal tokenized BASIC program from line content bytes.

    Each argument is the raw content of one line. Lines are numbered
    10, 20, 30, etc. The 0x0D-hi-lo-len header and end-of-program
    marker are added automatically.
    """
    result = bytearray()
    for i, content in enumerate(line_contents, start=1):
        linenum = i * 10
        hi = (linenum >> 8) & 0xFF
        lo = linenum & 0xFF
        linelen = 4 + len(content)
        result.extend([0x0D, hi, lo, linelen])
        result.extend(content)

    # End-of-program marker.
    result.extend([0x0D, 0xFF])
    return bytes(result)


# ---------------------------------------------------------------------------
# looksLikeTokenizedBasic
# ---------------------------------------------------------------------------

class TestLooksLikeTokenizedBasic:
    """Tests for structural detection of tokenized BASIC programs."""

    def testValidSingleLine(self) -> None:
        """A well-formed single-line program is detected."""
        data = makeBasicProgram(b"\xF1\"Hi\"")  # PRINT"Hi"
        assert looksLikeTokenizedBasic(data) is True

    def testValidMultipleLines(self) -> None:
        """A multi-line program is detected."""
        data = makeBasicProgram(b"\xF1\"A\"", b"\xE0")  # PRINT"A" / END
        assert looksLikeTokenizedBasic(data) is True

    def testEmptyProgram(self) -> None:
        """An empty program (just the end marker) is valid."""
        data = bytes([0x0D, 0xFF])
        assert looksLikeTokenizedBasic(data) is True

    def testEmptyBytesRejected(self) -> None:
        """Empty input is not a BASIC program."""
        assert looksLikeTokenizedBasic(b"") is False

    def testSingleByteRejected(self) -> None:
        """A single byte is not enough even if it's 0x0D."""
        assert looksLikeTokenizedBasic(b"\x0D") is False

    def testPlainTextRejected(self) -> None:
        """Plain ASCII text is not a BASIC program."""
        assert looksLikeTokenizedBasic(b"10 PRINT \"Hello\"\n") is False

    def testBinaryGarbageRejected(self) -> None:
        """Random binary data is not a BASIC program."""
        assert looksLikeTokenizedBasic(bytes(range(256))) is False

    def testCrLeadingButNotBasic(self) -> None:
        """Data starting with 0x0D but not valid BASIC is rejected."""
        # Starts with CR but second byte is not a valid line header.
        assert looksLikeTokenizedBasic(b"\x0D\x00\x00\x01") is False


# ---------------------------------------------------------------------------
# looksLikePlainText
# ---------------------------------------------------------------------------

class TestLooksLikePlainText:
    """Tests for plain-text byte detection."""

    def testPrintableAscii(self) -> None:
        """Printable ASCII characters are plain text."""
        assert looksLikePlainText(b"Hello, World!") is True

    def testWithWhitespace(self) -> None:
        """Tab, CR, LF are accepted whitespace."""
        assert looksLikePlainText(b"line1\r\nline2\ttab") is True

    def testEmptyRejected(self) -> None:
        """An empty file is not plain text."""
        assert looksLikePlainText(b"") is False

    def testControlCharsRejected(self) -> None:
        """Control characters (outside tab/CR/LF) reject the file."""
        assert looksLikePlainText(b"Hello\x01World") is False

    def testHighBitRejected(self) -> None:
        """Bytes with high bit set reject the file."""
        assert looksLikePlainText(b"Hello\x80World") is False

    def testPurePrintableRange(self) -> None:
        """Every printable ASCII byte passes."""
        data = bytes(range(0x20, 0x7F))
        assert looksLikePlainText(data) is True


# ---------------------------------------------------------------------------
# escapeNonAscii / unescapeNonAscii round-tripping
# ---------------------------------------------------------------------------

class TestEscapeNonAscii:
    """Tests for non-ASCII escape encoding."""

    def testPrintableAsciiUnchanged(self) -> None:
        """Printable ASCII passes through without escaping."""
        assert escapeNonAscii("Hello, World!") == "Hello, World!"

    def testControlCodeEscaped(self) -> None:
        """Control characters are escaped to \\xHH."""
        assert escapeNonAscii("\x85") == "\\x85"

    def testBackslashEscaped(self) -> None:
        """Backslash is always escaped to avoid ambiguity."""
        assert escapeNonAscii("a\\b") == "a\\x5Cb"

    def testMultipleEscapes(self) -> None:
        """Multiple non-ASCII characters are each escaped."""
        result = escapeNonAscii("\x01\x02\x03")
        assert result == "\\x01\\x02\\x03"

    def testMixedContent(self) -> None:
        """A line with mixed ASCII and non-ASCII is handled correctly."""
        line = 'PRINT "\x85Hello"'
        escaped = escapeNonAscii(line)
        assert "\\x85" in escaped
        assert "Hello" in escaped


class TestUnescapeNonAscii:
    """Tests for non-ASCII unescape decoding."""

    def testSimpleUnescape(self) -> None:
        """A single \\xHH sequence is unescaped."""
        assert unescapeNonAscii("\\x85") == "\x85"

    def testBackslashRoundTrip(self) -> None:
        """An escaped backslash round-trips correctly."""
        assert unescapeNonAscii("\\x5C") == "\\"

    def testNoEscapesUnchanged(self) -> None:
        """Text without escape sequences passes through unchanged."""
        assert unescapeNonAscii("Hello") == "Hello"


class TestEscapeRoundTrip:
    """Tests that escapeNonAscii and unescapeNonAscii are inverses."""

    def testPureAscii(self) -> None:
        """Round-trip of pure printable ASCII (no backslashes)."""
        line = "10 PRINT 42"
        assert unescapeNonAscii(escapeNonAscii(line)) == line

    def testWithTeletextCodes(self) -> None:
        """Round-trip of a line with teletext control codes."""
        line = '\x85\x86HELLO\x87'
        assert unescapeNonAscii(escapeNonAscii(line)) == line

    def testWithBackslash(self) -> None:
        """Round-trip preserves literal backslash characters."""
        line = "A\\B\\C"
        assert unescapeNonAscii(escapeNonAscii(line)) == line

    def testWithMixedContent(self) -> None:
        """Round-trip with printable ASCII, control codes, and backslash."""
        line = 'PRINT "\x85Hello\\World\x0D"'
        assert unescapeNonAscii(escapeNonAscii(line)) == line
