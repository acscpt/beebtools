# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""
Tests for the BBC BASIC detokenizer.

Tokenized BASIC line format (one record):
  0x0D  HI  LO  LINELEN  [content bytes]

Where LINELEN counts from the HI byte to (but not including) the 0x0D that
starts the next record.  So LINELEN = 4 + len(content_bytes).

The end-of-program marker is: 0x0D 0xFF
"""

import pytest

from beebtools import basicProgramSize, detokenize, decodeLineRef


# ---------------------------------------------------------------------------
# Helpers to build tokenized BASIC byte sequences
# ---------------------------------------------------------------------------

def makeLine(linenum, content_bytes):
    """Build a single tokenized BASIC line record."""
    hi = (linenum >> 8) & 0xFF
    lo = linenum & 0xFF
    # LINELEN counts HI + LO + LINELEN byte + content bytes.
    linelen = 3 + 1 + len(content_bytes)
    return bytes([0x0D, hi, lo, linelen]) + bytes(content_bytes)


def makeProgram(*lines):
    """Build a complete tokenized BASIC program from (linenum, content_bytes) pairs."""
    data = bytearray()
    for linenum, content in lines:
        data += makeLine(linenum, content)
    data += bytes([0x0D, 0xFF])
    return bytes(data)


def encodeLineRef(linenum):
    """Encode a line number as the 3-byte 0x8D inline reference format."""
    lo_byte = linenum & 0xFF
    hi_byte = (linenum >> 8) & 0xFF
    b1 = lo_byte & 0x3F
    lo_hi2 = (lo_byte >> 6) & 0x03
    b2 = hi_byte & 0x3F
    hi_hi2 = (hi_byte >> 6) & 0x03
    x = (lo_hi2 << 4) | (hi_hi2 << 2)
    b0 = x ^ 0x54
    return b0, b1, b2


# ---------------------------------------------------------------------------
# Token expansion tests
# ---------------------------------------------------------------------------

def testSinglePrintToken():
    """0xF1 should expand to PRINT."""
    data = makeProgram((10, [0xF1]))
    assert detokenize(data) == ["   10PRINT"]


def testSingleEndToken():
    """0xE0 should expand to END."""
    data = makeProgram((10, [0xE0]))
    assert detokenize(data) == ["   10END"]


def testTwoAdjacentTokens():
    """Adjacent tokens expand to adjacent keywords with no separator added."""
    data = makeProgram((10, [0xDB, 0xF1]))  # CLS PRINT
    assert detokenize(data) == ["   10CLSPRINT"]


def testTokenWithAscii():
    """Token followed by ASCII argument."""
    # MODE 7
    data = makeProgram((10, [0xEB, ord('7')]))  # MODE + '7'
    assert detokenize(data) == ["   10MODE7"]


def testUnknownTokenHexEscape():
    """Unknown token bytes should be emitted as [&XX] hex escapes."""
    # 0xCE is unused in BBC BASIC II - a genuine gap in the token table.
    data = makeProgram((10, [0xCE]))
    lines = detokenize(data)
    assert lines[0].endswith("[&CE]")


# ---------------------------------------------------------------------------
# REM and DATA literal-tail tests
# ---------------------------------------------------------------------------

def testRemTailNotExpanded():
    """Bytes after a REM token are emitted as raw text, not expanded as tokens."""
    # REM followed by 0xF1 (the PRINT token byte) should appear as chr(0xF1).
    data = makeProgram((10, [0xF4, 0xF1]))
    lines = detokenize(data)
    assert lines[0] == "   10REM" + chr(0xF1)


def testDataTailNotExpanded():
    """Bytes after a DATA token are emitted as raw text."""
    data = makeProgram((10, [0xDC, ord('1'), ord(','), ord('2')]))
    lines = detokenize(data)
    assert lines[0] == "   10DATA1,2"


def testRemTailWithLeadingAlpha():
    """REM followed immediately by an alpha character (no space) is still literal."""
    # In BBC BASIC, REM is always a keyword token; the following text is always literal.
    data = makeProgram((10, [0xF4, ord('N'), ord('o'), ord('t'), ord('e'), ord(':')]))
    lines = detokenize(data)
    assert lines[0] == "   10REMNote:"


# ---------------------------------------------------------------------------
# String literal tests
# ---------------------------------------------------------------------------

def testStringLiteralNotExpanded():
    """Token bytes inside a quoted string are not expanded to keywords."""
    # PRINT "X" where 0xF1 appears inside the string - should stay chr(0xF1).
    data = makeProgram((10, [0xF1, 0x22, 0xF1, 0x22]))
    lines = detokenize(data)
    assert lines[0] == '   10PRINT"' + chr(0xF1) + '"'


def testStringLiteralEqualsNotAffected():
    """'=' inside a string literal passes through unchanged."""
    data = makeProgram((10, [0xF1, 0x22, ord('a'), ord('='), ord('b'), 0x22]))
    lines = detokenize(data)
    assert lines[0] == '   10PRINT"a=b"'


# ---------------------------------------------------------------------------
# Inline line-number reference tests
# ---------------------------------------------------------------------------

def testInlineLineRefSmall():
    """0x8D encoding for a small line number (< 64) decodes correctly."""
    b0, b1, b2 = encodeLineRef(10)
    assert decodeLineRef(b0, b1, b2) == 10


def testInlineLineRef100():
    """0x8D encoding for line 100 decodes correctly."""
    b0, b1, b2 = encodeLineRef(100)
    assert decodeLineRef(b0, b1, b2) == 100


def testInlineLineRefInGoto():
    """GOTO with an encoded line number emits the decoded number."""
    b0, b1, b2 = encodeLineRef(100)
    data = makeProgram((10, [0xE5, 0x8D, b0, b1, b2]))  # GOTO 100
    lines = detokenize(data)
    assert lines[0] == "   10GOTO100"


def testInlineLineRefLarge():
    """Line numbers up to 32767 round-trip through the encoding."""
    for linenum in (1, 10, 100, 999, 9999, 32767):
        b0, b1, b2 = encodeLineRef(linenum)
        assert decodeLineRef(b0, b1, b2) == linenum


# ---------------------------------------------------------------------------
# Multi-line and edge-case tests
# ---------------------------------------------------------------------------

def testMultipleLinesAllReturned():
    """All lines in a program are returned."""
    data = makeProgram((10, [0xF1]), (20, [0xE0]))
    lines = detokenize(data)
    assert len(lines) == 2
    assert lines[0] == "   10PRINT"
    assert lines[1] == "   20END"


def testLineNumbersRightJustified():
    """Line numbers are right-justified in a 5-character field."""
    data = makeProgram((1, [0xF1]), (9999, [0xE0]))
    lines = detokenize(data)
    assert lines[0].startswith("    1")
    assert lines[1].startswith(" 9999")


def testEmptyProgram():
    """A program with only the end marker produces no lines."""
    data = bytes([0x0D, 0xFF])
    assert detokenize(data) == []


def testProgramWithSpacesPreserved():
    """ASCII space characters in the content are preserved."""
    data = makeProgram((10, [ord(' '), 0xF1, ord(' ')]))
    lines = detokenize(data)
    assert lines[0] == "   10 PRINT "


# ---------------------------------------------------------------------------
# basicProgramSize tests
# ---------------------------------------------------------------------------

def testProgramSizeEmptyProgram():
    """An empty program (just the end marker) is 2 bytes: 0x0D 0xFF."""
    data = bytes([0x0D, 0xFF])
    assert basicProgramSize(data) == 2


def testProgramSizeSingleLine():
    """Program size includes the line record and end marker."""
    data = makeProgram((10, [0xF1]))  # PRINT
    # 1 line: 0x0D + hi + lo + len + content (1) = 5 bytes
    # end marker: 0x0D + 0xFF = 2 bytes
    # total: 7
    assert basicProgramSize(data) == 7


def testProgramSizeWithTrailingBinary():
    """Program size excludes appended machine code."""
    prog = makeProgram((10, [0xF1]))
    machine_code = bytes(range(256)) * 10  # 2560 bytes of binary
    data = prog + machine_code
    # basicProgramSize should return only the program portion.
    assert basicProgramSize(data) == len(prog)


def testProgramSizeNonBasicData():
    """Data that doesn't start with 0x0D returns 0."""
    data = bytes([0x00, 0x01, 0x02])
    assert basicProgramSize(data) == 0


def testProgramSizeMultipleLines():
    """Multi-line program size is measured correctly."""
    data = makeProgram(
        (10, [0xF1]),            # PRINT
        (20, [0xE5]),            # END
        (30, [0xF4, ord('X')]),  # REM X
    )
    assert basicProgramSize(data) == len(data)
