# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""
Tests for the BBC BASIC pretty-printer.

The pretty-printer is a post-processing pass on detokenized BASIC text.
It receives lines formatted as "NNNNN code..." (right-justified line number)
and returns the same lines with operator spacing normalised.
"""

import pytest

from beebtools import prettyPrint, tokenize, detokenize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pretty(code, linenum=10):
    """Apply prettyPrint to a single line and return the code portion."""
    raw = f"{linenum:>5d} {code}"
    result = prettyPrint([raw])
    # Strip the line number prefix and return just the code portion.
    return result[0][5:]


# ---------------------------------------------------------------------------
# Line number spacing
# ---------------------------------------------------------------------------

def testSpaceInsertedAfterLineNumber():
    """A space should be inserted between line number and code when absent."""
    result = prettyPrint(["   10PRINT"])
    assert result[0] == "   10 PRINT"


def testExistingSpaceAfterLineNumberPreserved():
    """An existing space after the line number is kept (not doubled)."""
    result = prettyPrint(["   10 PRINT"])
    assert result[0] == "   10 PRINT"


# ---------------------------------------------------------------------------
# Assignment and comparison operator spacing
# ---------------------------------------------------------------------------

def testEqualsSpacing():
    """= should be surrounded by spaces."""
    assert pretty("x=1") == " x = 1"


def testLessThanSpacing():
    """< should be surrounded by spaces."""
    assert pretty("a<b") == " a < b"


def testGreaterThanSpacing():
    """> should be surrounded by spaces."""
    assert pretty("a>b") == " a > b"


def testNotEqualTwoChar():
    """<> should be treated as a single two-character operator."""
    assert pretty("a<>b") == " a <> b"


def testLessEqualTwoChar():
    """<= should be treated as a single two-character operator."""
    assert pretty("a<=b") == " a <= b"


def testGreaterEqualTwoChar():
    """>= should be treated as a single two-character operator."""
    assert pretty("a>=b") == " a >= b"


# ---------------------------------------------------------------------------
# Arithmetic operator spacing
# ---------------------------------------------------------------------------

def testAdditionSpacing():
    """+ should be surrounded by spaces."""
    assert pretty("a+b") == " a + b"


def testSubtractionSpacing():
    """Subtraction - should be surrounded by spaces."""
    assert pretty("a-b") == " a - b"


def testMultiplicationSpacing():
    """* should be surrounded by spaces."""
    assert pretty("a*b") == " a * b"


def testDivisionSpacing():
    """/ should be surrounded by spaces."""
    assert pretty("a/b") == " a / b"


def testChainOfArithmetic():
    """Multiple operators all spaced correctly."""
    assert pretty("a+b*c-d") == " a + b * c - d"


# ---------------------------------------------------------------------------
# Unary operator context
# ---------------------------------------------------------------------------

def testUnaryMinusAfterOpenParen():
    """Unary minus after ( is not space-padded."""
    assert pretty("x=(-y)") == " x = (-y)"


def testUnaryMinusAfterEquals():
    """Unary minus after = assignment is not space-padded."""
    assert pretty("x=-y") == " x = -y"


def testUnaryMinusAfterComma():
    """Unary minus after , is not space-padded."""
    assert pretty("SOUND1,-15,s,5") == " SOUND1, -15, s, 5"


def testBinaryMinusBetweenIdentifiers():
    """Minus between two identifiers is binary and should be spaced."""
    assert pretty("a-b") == " a - b"


# ---------------------------------------------------------------------------
# Colon statement separator
# ---------------------------------------------------------------------------

def testColonSeparatorSpacing():
    """Colon should be padded to ' : '."""
    assert pretty("CLS:PRINT") == " CLS : PRINT"


def testColonStripTrailingSpaceBefore():
    """Any trailing space before : should be consumed."""
    assert pretty("CLS :PRINT") == " CLS : PRINT"


def testColonMultipleStatements():
    """Multiple colon separators all get padded."""
    assert pretty("a=1:b=2:c=3") == " a = 1 : b = 2 : c = 3"


# ---------------------------------------------------------------------------
# Comma spacing
# ---------------------------------------------------------------------------

def testCommaGetsTrailingSpace():
    """Comma should have exactly one trailing space, no leading space."""
    assert pretty("a,b") == " a, b"


def testCommaStripsExistingSpaces():
    """Existing spaces around comma are normalised to one trailing space."""
    assert pretty("a , b") == " a, b"


def testMultipleCommas():
    """Multiple commas are all normalised."""
    assert pretty("PRINT a,b,c") == " PRINT a, b, c"


# ---------------------------------------------------------------------------
# String literal protection
# ---------------------------------------------------------------------------

def testEqualsInsideStringUnchanged():
    """= inside a string literal must not be spaced."""
    assert pretty('a$="x=y"') == ' a$ = "x=y"'


def testColonInsideStringUnchanged():
    """Colon inside a string should not be treated as a statement separator."""
    assert pretty('PRINT"a:b"') == ' PRINT"a:b"'


def testArithmeticInsideStringUnchanged():
    """Arithmetic operators inside a string literal are not spaced."""
    assert pretty('PRINT"a+b"') == ' PRINT"a+b"'


def testCommaInsideStringUnchanged():
    """Comma inside a string literal is not normalised."""
    assert pretty('PRINT"a,b"') == ' PRINT"a,b"'


# ---------------------------------------------------------------------------
# REM and DATA literal tail protection
# ---------------------------------------------------------------------------

def testRemTailUnchanged():
    """Content after REM must pass through completely unmodified."""
    assert pretty("REMa=b+c:d,e") == " REMa=b+c:d,e"


def testRemTailUnchangedWithSpace():
    """REM followed by a space - the rest is still untouched."""
    assert pretty("REM a=b+c") == " REM a=b+c"


def testDataTailUnchanged():
    """Content after DATA must pass through unmodified."""
    assert pretty("DATA1,2,3") == " DATA1,2,3"


def testRemTailWithLeadingAlphaUnchanged():
    """REM immediately followed by alpha - common in uncommented BASIC."""
    assert pretty("REMNote:x=1") == " REMNote:x=1"


# ---------------------------------------------------------------------------
# Star command pass-through
# ---------------------------------------------------------------------------

def testStarCommandNoSpacesInserted():
    """A * at statement start marks the rest as literal - no operator spacing."""
    assert pretty("*SCUMPI") == " *SCUMPI"


def testStarCommandAfterColon():
    """Star command after a colon separator is also passed through verbatim."""
    assert pretty("CLS:*SCUMPI") == " CLS : *SCUMPI"


def testStarMidExpressionIsMultiply():
    """An * in the middle of an expression is treated as multiply, not a command."""
    assert pretty("a*b") == " a * b"


# ---------------------------------------------------------------------------
# Anti-listing trap (*| conversion)
# ---------------------------------------------------------------------------

def testAntiListingTrapPreserved():
    """*| at the start of statement is kept as *| (not converted to REM)."""
    result = prettyPrint(["   10*|"])
    assert result[0] == "   10 *|"


def testAntiListingTrapTextPreserved():
    """Any printable text after *| should be preserved."""
    result = prettyPrint(["   10*|Hello"])
    assert result[0] == "   10 *|Hello"


def testAntiListingTrapControlCharsPreserved():
    """Control characters in the *| tail (e.g. VDU 21) are kept intact."""
    # VDU 21 (chr 21) was commonly inserted to blank the screen on LIST.
    # The text-encoding layer handles display; pretty-printer preserves them.
    result = prettyPrint(["   10*|\x15\x15visible"])
    assert result[0] == "   10 *|\x15\x15visible"


def testAntiListingTrapAllControlCharsPreserved():
    """All bytes in the trap tail are preserved for the encoding layer."""
    tail = "".join(chr(c) for c in range(1, 32))
    result = prettyPrint([f"   10*|{tail}text"])
    assert result[0] == f"   10 *|{tail}text"


def testAntiListingTrapRoundTrip():
    """Anti-listing trap survives detokenize -> prettyPrint -> tokenize."""
    # Build a tokenized program with a *| trap containing VDU 21 bytes.
    # Star commands are literal ASCII in the tokenized form: the * byte
    # followed by the rest of the line as raw characters.
    # Include a leading space before *| since the pretty-printer normalises
    # spacing between the line number and the first token.
    trap_line = b' *|\x15\x15visible'
    line_content = trap_line
    line_len = 4 + len(line_content)  # 0x0D + hi + lo + len + content
    original = bytes([
        0x0D, 0x00, 0x0A, line_len,
    ]) + line_content + bytes([0x0D, 0xFF])

    # Round-trip through pretty-print.
    lines = detokenize(original)
    pretty_lines = prettyPrint(lines)
    result = tokenize(pretty_lines)

    assert result == original


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def testLineWithNoCodeContent():
    """A line with only a line number and no code is returned unchanged."""
    result = prettyPrint(["   10"])
    assert result[0] == "   10"


def testDoubleSpacesCollapsed():
    """Multiple adjacent spaces in the code output are collapsed to one."""
    result = prettyPrint(["   10 a  =  b"])
    # Skip the fixed 5-char right-justified line number prefix, which may
    # contain leading spaces that are not part of the code output.
    assert "  " not in result[0][5:]
