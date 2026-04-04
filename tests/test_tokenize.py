# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""
Tests for the BBC BASIC II tokenizer.

Verifies that tokenize() produces correct tokenized binary from LIST-style
text, and that tokenize(detokenize(data)) round-trips back to the original
binary for a range of BASIC constructs.
"""

import pytest

from beebtools import detokenize, tokenize
from beebtools.tokenize import encodeLineRef, _parseLine, _tokenizeContent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def makeLine(linenum, content_bytes):
    """Build a single tokenized BASIC line record (Russell format)."""
    hi = (linenum >> 8) & 0xFF
    lo = linenum & 0xFF
    linelen = 4 + len(content_bytes)
    return bytes([0x0D, hi, lo, linelen]) + bytes(content_bytes)


def makeProgram(*lines):
    """Build a complete tokenized BASIC program from (linenum, content_bytes) pairs."""
    data = bytearray()
    for linenum, content in lines:
        data += makeLine(linenum, content)
    data += bytes([0x0D, 0xFF])
    return bytes(data)


# ---------------------------------------------------------------------------
# encodeLineRef tests
# ---------------------------------------------------------------------------

def testEncodeLineRefSmall():
    """Encoded line number 10 decodes back correctly via detokenize path."""
    ref = encodeLineRef(10)
    assert ref[0] == 0x8D
    assert len(ref) == 4

    # Verify bits 6 are set on payload bytes (benryves spec).
    assert ref[2] & 0x40 == 0x40
    assert ref[3] & 0x40 == 0x40


def testEncodeLineRefRoundTrip():
    """Line numbers round-trip through encode and the detokenizer's decode."""
    from beebtools import decodeLineRef

    for linenum in (0, 1, 10, 63, 64, 100, 255, 256, 999, 9999, 32767):
        ref = encodeLineRef(linenum)
        decoded = decodeLineRef(ref[1], ref[2], ref[3])
        assert decoded == linenum, f"Failed for {linenum}"


# ---------------------------------------------------------------------------
# _parseLine tests
# ---------------------------------------------------------------------------

def testParseLineBasic():
    """Standard 5-character right-justified line number."""
    linenum, content = _parseLine("   10PRINT")
    assert linenum == 10
    assert content == "PRINT"


def testParseLineNoLeadingSpaces():
    """Line number with no leading whitespace."""
    linenum, content = _parseLine("100GOTO50")
    assert linenum == 100
    assert content == "GOTO50"


def testParseLineInvalid():
    """Non-numeric start raises ValueError."""
    with pytest.raises(ValueError):
        _parseLine("PRINT")


# ---------------------------------------------------------------------------
# Basic keyword tokenization
# ---------------------------------------------------------------------------

def testTokenizePrint():
    """PRINT is tokenized to 0xF1."""
    data = tokenize(["   10PRINT"])
    expected = makeProgram((10, [0xF1]))
    assert data == expected


def testTokenizeEnd():
    """END is tokenized to 0xE0."""
    data = tokenize(["   10END"])
    expected = makeProgram((10, [0xE0]))
    assert data == expected


def testTokenizeTwoKeywords():
    """Adjacent keywords are both tokenized."""
    data = tokenize(["   10CLSPRINT"])
    expected = makeProgram((10, [0xDB, 0xF1]))
    assert data == expected


def testTokenizeKeywordWithArgument():
    """MODE followed by a digit."""
    data = tokenize(["   10MODE7"])
    expected = makeProgram((10, [0xEB, ord('7')]))
    assert data == expected


def testTokenizeMultipleLines():
    """Multiple program lines are all encoded."""
    data = tokenize(["   10PRINT", "   20END"])
    expected = makeProgram((10, [0xF1]), (20, [0xE0]))
    assert data == expected


def testTokenizeBlankLinesSkipped():
    """Blank lines and whitespace-only lines are silently skipped."""
    data = tokenize(["", "   10PRINT", "   ", "   20END"])
    expected = makeProgram((10, [0xF1]), (20, [0xE0]))
    assert data == expected


def testTokenizeEmptyProgram():
    """No lines produces just the end-of-program marker."""
    data = tokenize([])
    assert data == bytes([0x0D, 0xFF])


# ---------------------------------------------------------------------------
# String literal handling
# ---------------------------------------------------------------------------

def testStringLiteralsNotTokenized():
    """Keywords inside quoted strings are not tokenized."""
    data = tokenize(['   10PRINT"END"'])
    expected = makeProgram((10, [0xF1, 0x22, ord('E'), ord('N'), ord('D'), 0x22]))
    assert data == expected


def testStringWithEqualsSign():
    """Characters inside strings pass through unchanged."""
    data = tokenize(['   10PRINT"a=b"'])
    expected = makeProgram((10, [
        0xF1, 0x22, ord('a'), ord('='), ord('b'), 0x22
    ]))
    assert data == expected


# ---------------------------------------------------------------------------
# REM and DATA literal tails
# ---------------------------------------------------------------------------

def testRemTailNotTokenized():
    """Text after REM is not tokenized."""
    data = tokenize(["   10REMPRINT"])
    expected = makeProgram((10, [
        0xF4, ord('P'), ord('R'), ord('I'), ord('N'), ord('T')
    ]))
    assert data == expected


def testDataTailNotTokenized():
    """Text after DATA is not tokenized."""
    data = tokenize(["   10DATA1,2"])
    expected = makeProgram((10, [0xDC, ord('1'), ord(','), ord('2')]))
    assert data == expected


# ---------------------------------------------------------------------------
# Conditional flag
# ---------------------------------------------------------------------------

def testConditionalEndVsEndproc():
    """END has C flag so ENDPROC is not split into END + PROC."""
    data = tokenize(["   10ENDPROC"])
    expected = makeProgram((10, [0xE1]))
    assert data == expected


def testConditionalFalseNotMatchedInVariable():
    """FALSE has C flag so FALSEx is not tokenized as FALSE + x."""
    content = _tokenizeContent("FALSEx=1")
    # Should be literal text, not FALSE token + "x=1".
    assert 0xA3 not in content


def testConditionalTimeNotMatchedInTimer():
    """TIME has C flag so TIMER is not tokenized as TIME + R."""
    content = _tokenizeContent("TIMER=0")
    # Should be literal text, not TIME token + "R=0".
    assert 0x91 not in content
    assert 0xD1 not in content


# ---------------------------------------------------------------------------
# Pseudo-variable handling
# ---------------------------------------------------------------------------

def testPseudoVarStatementForm():
    """TIME at start of statement uses the statement form token (0xD1)."""
    # TIME=0 at start of line -> statement form
    content = _tokenizeContent("TIME=0")
    assert content[0] == 0xD1


def testPseudoVarFunctionForm():
    """TIME inside an expression uses the function form token (0x91)."""
    # PRINT TIME -> PRINT is a M-flag keyword, then TIME is mid-statement
    content = _tokenizeContent("PRINTTIME")
    assert content[0] == 0xF1   # PRINT
    assert content[1] == 0x91   # TIME (function form)


def testPseudoVarAfterColon():
    """Colon resets to start-of-statement, so pseudo-var gets statement form."""
    content = _tokenizeContent("CLS:TIME=0")
    # CLS = 0xDB, colon = 0x3A, TIME = 0xD1 (stmt form), '=' '0'
    assert content[0] == 0xDB
    assert content[1] == ord(':')
    assert content[2] == 0xD1


def testPseudoVarPtrStatementForm():
    """PTR at start of statement uses 0xCF."""
    content = _tokenizeContent("PTR#3=100")
    assert content[0] == 0xCF


def testPseudoVarPtrFunctionForm():
    """PTR inside expression uses 0x8F."""
    content = _tokenizeContent("PRINTPTR#3")
    assert content[0] == 0xF1  # PRINT
    assert content[1] == 0x8F  # PTR (function form)


# ---------------------------------------------------------------------------
# Line-number encoding (L flag)
# ---------------------------------------------------------------------------

def testGotoLineNumberEncoded():
    """GOTO followed by a digit sequence encodes the line number."""
    data = tokenize(["   10GOTO100"])
    # GOTO = 0xE5, then 0x8D + 3-byte encoded 100
    content = data[4:-2]  # skip 0x0D hi lo len prefix and 0x0D 0xFF end marker
    assert content[0] == 0xE5
    assert content[1] == 0x8D


def testGosubLineNumberEncoded():
    """GOSUB line number is encoded with 0x8D."""
    data = tokenize(["   10GOSUB200"])
    content = data[4:-2]
    assert content[0] == 0xE4
    assert content[1] == 0x8D


def testThenLineNumberEncoded():
    """THEN followed by a line number encodes it."""
    data = tokenize(["   10IFA=1THEN100"])
    content = data[4:-2]
    # IF A = 1 THEN <0x8D ref>
    assert 0x8D in content


def testGotoWithSpaceBeforeNumber():
    """Space between GOTO and line number is preserved, number is encoded."""
    data = tokenize(["   10GOTO 100"])
    content = data[4:-2]
    assert content[0] == 0xE5   # GOTO
    assert content[1] == 0x20   # space
    assert content[2] == 0x8D   # encoded line number


def testGotoMultipleLineNumbers():
    """ON GOTO with comma-separated line numbers encodes all of them."""
    data = tokenize(["   10ON x GOTO100,200,300"])
    content = data[4:-2]

    # Count 0x8D occurrences - should be 3.
    ref_count = sum(1 for b in content if b == 0x8D)
    assert ref_count == 3


def testRestoreLineNumber():
    """RESTORE with a line number encodes it."""
    data = tokenize(["   10RESTORE100"])
    content = data[4:-2]
    assert content[0] == 0xF7  # RESTORE
    assert content[1] == 0x8D


def testGotoVariable():
    """GOTO followed by a variable (not a digit) does not use 0x8D encoding."""
    data = tokenize(["   10GOTOx"])
    content = data[4:-2]
    assert content[0] == 0xE5  # GOTO
    assert 0x8D not in content
    assert content[1] == ord('x')


# ---------------------------------------------------------------------------
# Star commands
# ---------------------------------------------------------------------------

def testStarCommandNotTokenized():
    """Star command at start of statement is not tokenized."""
    data = tokenize(["   10*RUN"])
    content = data[4:-2]

    # Should be literal: '*', 'R', 'U', 'N'
    assert content == bytes([ord('*'), ord('R'), ord('U'), ord('N')])


def testStarCommandMidStatement():
    """Star command after colon is also not tokenized."""
    data = tokenize(["   10CLS:*RUN"])
    content = data[4:-2]
    assert content[0] == 0xDB   # CLS
    assert content[1] == ord(':')
    assert content[2] == ord('*')
    assert content[3] == ord('R')


# ---------------------------------------------------------------------------
# Ampersand hex prefix
# ---------------------------------------------------------------------------

def testAmpersandSkipsHex():
    """&DEF should not tokenize DEF as a keyword."""
    content = _tokenizeContent("X=&DEF")
    # Should be: X = & D E F  (all literal)
    assert 0xDD not in content  # DEF token


# ---------------------------------------------------------------------------
# FN/PROC name passthrough
# ---------------------------------------------------------------------------

def testFnNameNotTokenized():
    """FN followed by a name does not tokenize keywords in the name."""
    content = _tokenizeContent("FNprint")
    assert content[0] == 0xA4  # FN token
    # "print" should be literal, not PRINT token.
    assert 0xF1 not in content


def testProcNameNotTokenized():
    """PROC followed by a name does not tokenize keywords in the name."""
    content = _tokenizeContent("PROCend")
    assert content[0] == 0xF2  # PROC token
    assert 0xE0 not in content  # END token should not appear


# ---------------------------------------------------------------------------
# Round-trip tests: tokenize(detokenize(data)) == data
# ---------------------------------------------------------------------------

def testRoundTripSimple():
    """Simple PRINT program round-trips."""
    original = makeProgram((10, [0xF1]))
    assert tokenize(detokenize(original)) == original


def testRoundTripMultipleLines():
    """Multi-line program round-trips."""
    original = makeProgram(
        (10, [0xDB]),                          # CLS
        (20, [0xF1, 0x22, ord('H'), 0x22]),    # PRINT"H"
        (30, [0xE0]),                          # END
    )
    assert tokenize(detokenize(original)) == original


def testRoundTripGotoWithRef():
    """GOTO with encoded line-number reference round-trips."""
    ref = encodeLineRef(100)
    original = makeProgram(
        (10, [0xE5] + list(ref)),  # GOTO 100
        (100, [0xE0]),             # END
    )
    assert tokenize(detokenize(original)) == original


def testRoundTripGosubWithRef():
    """GOSUB with encoded line-number reference round-trips."""
    ref = encodeLineRef(500)
    original = makeProgram(
        (10, [0xE4] + list(ref)),        # GOSUB 500
        (20, [0xE0]),                     # END
        (500, [0xF1]),                    # PRINT
        (510, [0xF8]),                    # RETURN
    )
    assert tokenize(detokenize(original)) == original


def testRoundTripRemLiteral():
    """REM with literal tail including token-value bytes round-trips."""
    original = makeProgram((10, [0xF4, 0xF1]))  # REM + chr(0xF1)
    assert tokenize(detokenize(original)) == original


def testRoundTripDataLiteral():
    """DATA with literal values round-trips."""
    original = makeProgram((10, [0xDC, ord('1'), ord(','), ord('2')]))
    assert tokenize(detokenize(original)) == original


def testRoundTripStringLiteral():
    """String literal containing token-value bytes round-trips."""
    original = makeProgram((10, [0xF1, 0x22, 0xF1, 0x22]))
    assert tokenize(detokenize(original)) == original


def testRoundTripPseudoVarStatement():
    """Pseudo-variable in statement form round-trips."""
    # TIME=0 at start of line
    original = makeProgram((10, [0xD1, ord('='), ord('0')]))
    assert tokenize(detokenize(original)) == original


def testRoundTripPseudoVarFunction():
    """Pseudo-variable in function form round-trips."""
    # PRINT TIME
    original = makeProgram((10, [0xF1, 0x91]))
    assert tokenize(detokenize(original)) == original


def testRoundTripForNext():
    """FOR/NEXT loop round-trips."""
    ref = encodeLineRef(10)
    original = makeProgram(
        (10, [0xE3, ord('I'), ord('='), ord('1'), 0xB8, ord('8')]),  # FORi=1TO8
        (20, [0xF1, ord('I')]),    # PRINTi
        (30, [0xED, ord('I')]),    # NEXTi
    )
    assert tokenize(detokenize(original)) == original


def testRoundTripIfThenElse():
    """IF/THEN/ELSE with line numbers round-trips."""
    ref100 = encodeLineRef(100)
    ref200 = encodeLineRef(200)
    original = makeProgram(
        (10, [0xE7, ord('X'), ord('='), ord('1'),
              0x8C] + list(ref100) + [0x8B] + list(ref200)),
    )
    assert tokenize(detokenize(original)) == original


def testRoundTripSpacesPreserved():
    """Spaces in the original content are preserved through round-trip."""
    original = makeProgram((10, [ord(' '), 0xF1, ord(' ')]))
    assert tokenize(detokenize(original)) == original


def testRoundTripLargeLineNumber():
    """Large line number (32000) round-trips."""
    original = makeProgram((32000, [0xF1]))
    assert tokenize(detokenize(original)) == original


# ---------------------------------------------------------------------------
# Text-stable round-trip: detokenize(tokenize(text)) == text
#
# Verifies the extract-edit-add workflow: detokenized text, once
# retokenized and detokenized again, produces the same text output.
# ---------------------------------------------------------------------------

def testTextStablePrint():
    """PRINT round-trips through tokenize then detokenize."""
    text = ["   10PRINT"]
    assert detokenize(tokenize(text)) == text


def testTextStableMultiLine():
    """Multi-line program is text-stable."""
    text = [
        "   10CLS",
        '   20PRINT"HELLO"',
        "   30END",
    ]
    assert detokenize(tokenize(text)) == text


def testTextStableGotoRef():
    """GOTO with a line number is text-stable."""
    text = ["   10GOTO100", "  100END"]
    assert detokenize(tokenize(text)) == text


def testTextStableGosubReturn():
    """GOSUB/RETURN program is text-stable."""
    text = [
        "   10GOSUB100",
        "   20END",
        "  100PRINT",
        "  110RETURN",
    ]
    assert detokenize(tokenize(text)) == text


def testTextStableRemTail():
    """REM with literal tail is text-stable."""
    text = ["   10REMhello world"]
    assert detokenize(tokenize(text)) == text


def testTextStableData():
    """DATA line is text-stable."""
    text = ["   10DATA1,2,3,HELLO"]
    assert detokenize(tokenize(text)) == text


def testTextStableStringLiteral():
    """String literal is text-stable."""
    text = ['   10PRINT"GOTO END PRINT"']
    assert detokenize(tokenize(text)) == text


def testTextStableIfThenElse():
    """IF/THEN/ELSE with line numbers is text-stable."""
    text = ["   10IFA=1THEN100ELSE200"]
    assert detokenize(tokenize(text)) == text


def testTextStableForNext():
    """FOR/NEXT loop is text-stable."""
    text = [
        "   10FORI=1TO10",
        "   20PRINTI",
        "   30NEXTI",
    ]
    assert detokenize(tokenize(text)) == text


def testTextStablePseudoVarStatement():
    """Pseudo-variable in statement form is text-stable."""
    text = ["   10TIME=0"]
    assert detokenize(tokenize(text)) == text


def testTextStablePseudoVarFunction():
    """Pseudo-variable in function form is text-stable."""
    text = ["   10PRINTTIME"]
    assert detokenize(tokenize(text)) == text


def testTextStableStarCommand():
    """Star command is text-stable."""
    text = ["   10*RUN"]
    assert detokenize(tokenize(text)) == text


def testTextStableFnProc():
    """FN and PROC names are text-stable."""
    text = [
        "   10DEFPROCinit",
        "   20A=FNcalc",
        "   30ENDPROC",
    ]
    assert detokenize(tokenize(text)) == text


def testTextStableHexLiteral():
    """Hex literals with & prefix are text-stable."""
    text = ["   10A=&DEF"]
    assert detokenize(tokenize(text)) == text


def testTextStableColonResets():
    """Colon-separated statements are text-stable."""
    text = ["   10CLS:PRINT:END"]
    assert detokenize(tokenize(text)) == text


def testTextStableFullProgram():
    """A realistic multi-line program is text-stable."""
    text = [
        "   10REM ** DEMO **",
        "   20MODE7",
        "   30FORI=1TO8",
        "   40PRINTI*I",
        "   50NEXTI",
        "   60IFI>8THEN100",
        "   70GOSUB200",
        '   80DATA10,20,"HELLO"',
        "   90END",
        "  100PRINT",
        "  110GOTO90",
        "  200PRINT",
        "  210RETURN",
    ]
    assert detokenize(tokenize(text)) == text


# ---------------------------------------------------------------------------
# FN/PROC symbol table: two-pass tokenizer resolves identifier boundaries
# ---------------------------------------------------------------------------

def testFnProcSymbolTableThenAfterFn():
    """FNld followed by THEN is correctly split when DEFFNld is defined."""
    text = [
        "   10IFNOTFNldTHEN=0",
        "   20DEFFNld",
        "   30=GET",
    ]
    data = tokenize(text)
    # Detokenize line 10 and check THEN appears as a keyword.
    lines = detokenize(data)
    assert "THEN" in lines[0]
    # The token stream for line 10 should contain THEN token (0x8C).
    assert 0x8C in data


def testFnProcSymbolTableElseAfterProc():
    """PROCl followed by ELSE is correctly split when DEFPROCl exists."""
    text = [
        "   10IFA=1THENPROClELSEPROCm",
        "   20DEFPROCl",
        "   30ENDPROC",
        "   40DEFPROCm",
        "   50ENDPROC",
    ]
    data = tokenize(text)
    lines = detokenize(data)
    # Line 10 should have ELSE as a token, not eaten by PROCl's name.
    assert "ELSE" in lines[0]
    assert "PROCm" in lines[0]


def testFnProcSymbolTableDivAfterFn():
    """FNhd followed by DIV is correctly split when DEFFNhd exists."""
    text = [
        "   10X=FNhdDIV40",
        "   20DEFFNhd=42",
    ]
    data = tokenize(text)
    # DIV token (0x81) should be present.
    assert 0x81 in data


def testFnProcSymbolTableLongestMatch():
    """When both FNh and FNhd exist, FNhdDIV matches the longer name."""
    text = [
        "   10X=FNhdDIV40",
        "   20DEFFNh=1",
        "   30DEFFNhd=2",
    ]
    data = tokenize(text)
    # Should match FNhd (longer), leaving DIV to be tokenized.
    assert 0x81 in data


def testFnProcSymbolTableFallbackGreedy():
    """Without a matching DEF, FN/PROC falls back to greedy consumption."""
    text = [
        "   10X=FNunknownTHEN",
    ]
    data = tokenize(text)
    # No DEF for FNunknown, so greedy eats "unknownTHEN" as the name.
    # THEN token (0x8C) should NOT be present.
    assert 0x8C not in data


def testFnProcSymbolTableRoundTrip():
    """Program with FN/PROC boundary ambiguity round-trips correctly."""
    # Build binary where FNld token is followed directly by THEN token.
    ref100 = encodeLineRef(100)
    original = makeProgram(
        (10, [0xE7, 0xAC, 0xA4, ord('l'), ord('d'),
              0x8C] + list(ref100)),   # IFNOTFNldTHEN100
        (20, [0xDD, 0xA4, ord('l'), ord('d')]),  # DEFFNld
        (30, [0x3D, 0xA5]),                       # =GET
        (100, [0xE0]),                             # END
    )
    assert tokenize(detokenize(original)) == original
