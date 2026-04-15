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
from beebtools.basic import encodeLineRef, _parseLine, _tokenizeContent


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


def testLineTooLongRaises():
    """A line whose tokenized content exceeds 255 bytes raises ValueError."""
    # REM tokenizes to 1 byte (0xF4), rest is literal.  We need content
    # that makes 4 + len(content) > 255, i.e. content > 251 bytes.
    # 1 (REM token) + 251 (x's) = 252 content bytes -> linelen = 256.
    long_line = "   10REM" + "x" * 251
    with pytest.raises(ValueError, match="max 255"):
        tokenize([long_line])


def testOnOverflowCallbackCompacts():
    """The on_overflow callback is invoked and its result retokenized."""
    # Build a line that overflows: REM + 251 x's = 256 bytes total.
    long_line = "   10REM" + "x" * 251
    # Callback trims to 200 x's so it fits (4 + 1 + 200 = 205).
    short_line = "   10REM" + "x" * 200
    msgs = []
    result = tokenize(
        [long_line],
        on_overflow=lambda text, msg: (msgs.append(msg), short_line)[-1],
    )
    assert len(msgs) == 1
    assert "256 bytes" in msgs[0]
    # Verify the tokenized output matches the short version.
    assert result == tokenize([short_line])


def testOnOverflowStillTooLongRaises():
    """ValueError is raised when callback result still exceeds 255 bytes."""
    long_line = "   10REM" + "x" * 251
    with pytest.raises(ValueError, match="max 255"):
        tokenize([long_line], on_overflow=lambda text, msg: text)


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


def testPseudoVarFunctionFormAfterEquals():
    """PAGE on the RHS of '=' uses the function form (0x90), not stmt form."""
    # '=' as the first non-whitespace character indicates we are inside
    # an expression (e.g. the body of a function that returns PAGE),
    # so the following PAGE must be the function-form token.
    content = _tokenizeContent("=PAGE")
    assert content[0] == ord('=')
    assert content[1] == 0x90


def testPseudoVarFunctionFormAfterIndirection():
    """PAGE inside an indirection operator's argument uses function form."""
    # ?(PAGE+1024)=1 assigns through a byte-indirection operator. The
    # PAGE inside the parens is on the RHS of an expression, not the
    # head of a statement, so it must tokenize as function form 0x90.
    content = _tokenizeContent("?(PAGE+1024)=1")
    # Find PAGE in the output: should be 0x90, not 0xD0.
    assert 0x90 in content
    assert 0xD0 not in content


def testPseudoVarFunctionFormAfterShriekAssignment():
    """TIME on the RHS of a word-indirection assignment uses function form."""
    content = _tokenizeContent("!&80=TIME")
    assert 0x91 in content   # function form
    assert 0xD1 not in content  # not statement form


def testPseudoVarStatementFormAtLineStart():
    """PAGE=&1900 at the very start of a line still emits statement form."""
    content = _tokenizeContent("PAGE=&1900")
    assert content[0] == 0xD0   # statement form PAGE


def testPseudoVarFunctionFormAfterStringLiteral():
    """A string literal counts as non-whitespace and clears start-of-stmt."""
    # "X"=PAGE is not meaningful BASIC but exercises the rule: the
    # closing quote leaves us mid-expression, so PAGE after '=' must be
    # function form.
    content = _tokenizeContent('"X"=PAGE')
    assert 0x90 in content
    assert 0xD0 not in content


def testPseudoVarFunctionFormAfterHexLiteral():
    """A hex literal clears start-of-statement for the next pseudo-var."""
    # &0=PAGE: the ampersand-hex consumes its own bytes; the '=' and
    # PAGE that follow must not treat PAGE as head-of-statement.
    content = _tokenizeContent("&0=PAGE")
    assert 0x90 in content
    assert 0xD0 not in content


def testPseudoVarStatementFormAfterColonSurvives():
    """Colon still resets to start-of-statement even with the wider reset."""
    content = _tokenizeContent(":TIME=0")
    assert content[0] == ord(':')
    assert content[1] == 0xD1   # statement form TIME


def testPseudoVarStatementFormAfterLeadingWhitespace():
    """Leading spaces before a pseudo-var keep start-of-statement mode."""
    content = _tokenizeContent("   PAGE=&1900")
    # First three bytes are spaces, then PAGE in statement form.
    assert content[:3] == bytes([0x20, 0x20, 0x20])
    assert content[3] == 0xD0


# ---------------------------------------------------------------------------
# Identifier character range: BBC BASIC II accepts '_' through 'z' plus
# digits and uppercase letters in identifiers. Backtick (0x60) lives in
# that range but is missed by Python's str.isalnum().
# ---------------------------------------------------------------------------

def testBacktickSuppressesConditionalKeyword():
    """END followed by backtick counts as an identifier continuation."""
    # END is a conditional keyword; a following identifier character
    # suppresses tokenization. Backtick must count as ident in BBC
    # BASIC II, so "END`EOR" is one variable name, not END + `EOR.
    content = _tokenizeContent("END`EOR=1")
    assert 0xE0 not in content   # END must not tokenize
    assert 0xA7 not in content   # EOR must not tokenize either


def testBacktickStartsVariable():
    """A name starting with backtick consumes any keyword within it."""
    content = _tokenizeContent("`PRINT=3")
    # `PRINT is one variable name; PRINT must not tokenize.
    assert 0xF1 not in content


def testUnderscoreStartsVariableRegression():
    """Underscore-starting variable still works (regression guard)."""
    content = _tokenizeContent("_END=4")
    assert 0xE0 not in content   # END must not tokenize inside _END


def testProcNameWithBacktickAndDigits():
    """Greedy PROC name eater consumes backtick and digits in the name."""
    # PROC's F-flag makes the following identifier opaque. Without a
    # matching DEF, the name is consumed greedily across all identifier
    # characters - which must include backtick and digits - so the
    # trailing PRINT does not tokenize.
    content = _tokenizeContent("PROC`1PRINT")
    assert content[0] == 0xF2   # PROC token
    # No PRINT keyword in the identifier tail.
    assert 0xF1 not in content[1:]


def testDefProcNameWithBacktickCollected():
    """DEFPROC`name is recognised by the symbol-table collector."""
    # A DEFPROC with a backtick in the name must be recorded so the
    # boundary is known on the caller side.
    data = tokenize([
        "10 IF A=1 THEN PROC`fooELSEPROC`bar",
        "20 DEFPROC`foo",
        "30 ENDPROC",
        "40 DEFPROC`bar",
        "50 ENDPROC",
    ])
    # Round-trip via detokenize to verify ELSE tokenized correctly on
    # line 10, i.e. PROC`foo boundary was found.
    lines = detokenize(data)
    assert "ELSE" in lines[0]
    assert "PROC`bar" in lines[0]


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


def testHexScanStopsBeforeKeyword():
    """A keyword after at least one hex digit breaks the hex run."""
    # "&3DEF" should produce the hex literal "&3" followed by the DEF
    # keyword (0xDD). The first hex digit is always consumed, so "&DEF"
    # on its own still greedily stays a single hex literal - only the
    # ambiguous case where a keyword can start inside the hex run is
    # resolved in favour of the keyword.
    content = _tokenizeContent("A=&3DEF")
    assert content == bytes([
        ord('A'), ord('='), ord('&'), ord('3'), 0xDD,
    ])


def testHexScanGreedyWhenNoKeywordSuffix():
    """&FFFF stays a single hex literal when no keyword hides inside."""
    content = _tokenizeContent("A=&FFFF")
    # No keyword starts with FFF, FF, or F, so all four hex digits
    # are consumed greedily.
    assert content == bytes([
        ord('A'), ord('='), ord('&'), ord('F'), ord('F'), ord('F'), ord('F'),
    ])


def testHexScanStopsAtNonHexKeywordStart():
    """&276BMOD16 splits into hex &276B then MOD keyword then "16"."""
    # M is not a hex digit, so the hex run naturally stops at &276B.
    # The following MOD must tokenize as the MOD keyword (0x83).
    content = _tokenizeContent("A=&276BMOD16")
    assert content[:2] == bytes([ord('A'), ord('=')])
    # Locate the ampersand run.
    assert content[2] == ord('&')
    assert content[3:7] == b'276B'
    assert content[7] == 0x83   # MOD token
    assert content[8:] == b'16'


# ---------------------------------------------------------------------------
# Dot-abbreviation form: "P." -> PRINT, "PRO." -> PROC, etc. Resolution
# is by token value (earlier tokens claim shorter prefixes first).
# ---------------------------------------------------------------------------

def testAbbreviationPrExpandsToPrint():
    """PR. expands to PRINT - resolves by alphabetical-first match."""
    # P-prefix keywords in alphabetical order: PAGE, PI, PLOT, POS,
    # PRINT, PROC, PTR. PR-prefix first is PRINT. So PR. -> PRINT.
    content = _tokenizeContent("PR.")
    assert content == bytes([0xF1])


def testAbbreviationPrinStillPrint():
    """PRIN. also resolves to PRINT (longer prefix, same first match)."""
    content = _tokenizeContent("PRIN.")
    assert content == bytes([0xF1])


def testAbbreviationPrDisambiguatesProc():
    """PRO. expands to PROC (PR. taken by PRINT, PRO. frees up)."""
    content = _tokenizeContent("PRO.")
    assert content == bytes([0xF2])


def testAbbreviationFollowedByString():
    """PR. followed by a string tokenizes PRINT and preserves the string."""
    content = _tokenizeContent('PR."HI"')
    assert content == bytes([0xF1, 0x22, ord('H'), ord('I'), 0x22])


def testAbbreviationInsideRemTailIsLiteral():
    """After REM the rest of the line is literal, so PR. does not expand."""
    data = tokenize(["10 REM PR."])
    lines = detokenize(data)
    assert lines[0].rstrip().endswith("PR.")


def testAbbreviationInsideStringIsLiteral():
    """A dot inside a quoted string is literal, no expansion."""
    data = tokenize(['10 PRINT"PR."'])
    lines = detokenize(data)
    assert '"PR."' in lines[0]


def testAbbreviationConditionalSuppressedByIdentChar():
    """EN. followed by an identifier is not expanded (END is conditional)."""
    # EN-prefix first alphabetically is END (conditional keyword). A
    # trailing identifier character must suppress the expansion.
    content = _tokenizeContent("EN.X")
    assert 0xE0 not in content   # END token must not appear


def testAbbreviationConditionalExpandsBeforeNonIdent():
    """EN. at the end of a line is expanded to END."""
    content = _tokenizeContent("EN.")
    # No character after the dot, so the conditional check is a no-op.
    assert 0xE0 in content


def testAbbreviationPseudoVarFunctionForm():
    """TI. inside an expression expands to TIME in function form."""
    content = _tokenizeContent("X=TI.")
    # After '=' at_start is False, so pseudo-var TI. -> 0x91.
    assert 0x91 in content
    assert 0xD1 not in content


def testAbbreviationPseudoVarStatementForm():
    """TI.=0 at start of statement expands to TIME statement form."""
    content = _tokenizeContent("TI.=0")
    # TI. -> TIME; at start of statement -> 0xD1 (0x91 + 0x40).
    assert content[0] == 0xD1


def testAbbreviationAfterColonIsStatementForm():
    """Colon resets start-of-statement, so TI.=0 after ':' emits stmt form."""
    content = _tokenizeContent("CLS:TI.=0")
    assert content[0] == 0xDB
    assert content[1] == ord(':')
    assert content[2] == 0xD1


def testAbbreviationDetokenizesToFullKeyword():
    """Abbreviations are lossy input: detokenize emits the canonical keyword."""
    data = tokenize(["10 PR."])
    lines = detokenize(data)
    assert "PRINT" in lines[0]
    assert "PR." not in lines[0]


def testAbbreviationWithLineNumberEncoding():
    """GOT. (GOTO abbrev) followed by digits encodes a line-number ref."""
    # G-keywords alphabetical order: GCOL, GET, GET$, GOSUB, GOTO.
    # GOT-prefix first is GOTO (no other keyword starts with GOT).
    data = tokenize(["10 GOT. 100", "100 END"])
    assert 0xE5 in data   # GOTO
    assert 0x8D in data   # inline line-number ref


def testAbbreviationAutoNumberSource():
    """Auto-numbered source uses abbreviations too."""
    data = tokenize(['PR."HI"'])
    # _normalizeLines prepends a space; abbreviation PR. -> PRINT.
    expected = makeProgram(
        (1, [0x20, 0xF1, 0x22, ord('H'), ord('I'), 0x22]),
    )
    assert data == expected


def testAbbreviationNotTriggeredInsideVariable():
    """XPR. within a variable name is literal, no abbreviation expansion."""
    # XPR is a variable; the dot is just a dot, not a keyword abbreviation.
    content = _tokenizeContent("XPR.=1")
    # PRINT token must not appear.
    assert 0xF1 not in content


def testHexSingleDigitStillGreedy():
    """A lone hex digit like &D before a keyword keeps the first digit."""
    # "&DTHEN" - D is the first hex digit (consumed unconditionally),
    # THEN is not a hex run continuation character, so the hex stops
    # at &D and THEN tokenizes normally.
    content = _tokenizeContent("&DTHEN")
    assert content[0] == ord('&')
    assert content[1] == ord('D')
    assert content[2] == 0x8C   # THEN token


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


# ---------------------------------------------------------------------------
# Auto-numbered source: lines without an explicit line number
#
# The tokenizer assigns line numbers to numberless lines: start at 1,
# increment by 1, blank lines advance the counter without emitting
# output, and explicit line numbers may interleave freely provided
# they stay strictly greater than the previous line.
# ---------------------------------------------------------------------------

def testAutoNumberSingleLine():
    """A single source line with no line number gets line 1."""
    data = tokenize(["PRINT"])
    # Numberless content gets a leading space prepended so it mirrors
    # the stored form of an explicit "<N> <content>" line.
    expected = makeProgram((1, [0x20, 0xF1]))
    assert data == expected


def testAutoNumberStepsByOne():
    """Successive numberless lines get 1, 2, 3."""
    data = tokenize(["PRINT", "END", "PRINT"])
    expected = makeProgram(
        (1, [0x20, 0xF1]),
        (2, [0x20, 0xE0]),
        (3, [0x20, 0xF1]),
    )
    assert data == expected


def testAutoNumberBlankLinesAdvanceCounter():
    """Blank lines drop from output but still bump the counter."""
    data = tokenize(["PRINT", "", "   ", "END"])
    # "PRINT" -> 1, blank -> 2, whitespace-only -> 3, "END" -> 4.
    expected = makeProgram(
        (1, [0x20, 0xF1]),
        (4, [0x20, 0xE0]),
    )
    assert data == expected


def testAutoNumberPreservesLeadingIndentation():
    """Source indentation is stored verbatim in the tokenized line content."""
    # The stored form is "<linenum> <line as typed>", so a source line
    # indented with two spaces gets three spaces of content bytes.
    data = tokenize(["  PRINT"])
    expected = makeProgram((1, [0x20, 0x20, 0x20, 0xF1]))
    assert data == expected


def testAutoNumberIdenticalToExplicitForm():
    """Auto-numbered source produces the same bytes as the explicit form."""
    auto = tokenize(["PRINT \"HELLO\"", "END"])
    explicit = tokenize(["1 PRINT \"HELLO\"", "2 END"])
    assert auto == explicit


def testAutoNumberLeadingBlanksAdvanceCounter():
    """Leading blank lines bump the counter before the first emitted line."""
    # Two leading blanks bump last_line from -1 -> 1 -> 2, then PRINT
    # lands on 3.
    data = tokenize(["", "  ", "PRINT"])
    expected = makeProgram((3, [0x20, 0xF1]))
    assert data == expected


def testAutoNumberFnProcSymbolTable():
    """FN/PROC names declared in auto-numbered source are still collected.

    Without the symbol table, FNldTHEN would greedily consume "ldTHEN"
    as the name, leaving no THEN token. With it, FNld is matched and
    THEN tokenizes correctly.
    """
    data = tokenize([
        "IF NOT FNld THEN 100",
        "DEF FNld",
        "=GET",
    ])
    # THEN token (0x8C) must be present, proving FNld boundary was found.
    assert 0x8C in data


def testAutoNumberGotoRefersToInjectedLine():
    """GOTO targets within auto-numbered source resolve to injected numbers."""
    data = tokenize([
        "PRINT \"START\"",    # becomes line 1
        "GOTO 1",              # becomes line 2, jumps back to 1
    ])
    listed = detokenize(data)
    assert listed[0].lstrip().startswith("1")
    assert listed[1].lstrip().startswith("2")
    assert "GOTO1" in listed[1].replace(" ", "")


def testMixedModeAllowsNumberlessAfterNumbered():
    """A numbered line followed by numberless lines auto-numbers continuing."""
    # First line is explicit 10; subsequent numberless line is 11.
    data = tokenize(["10 PRINT", "END"])
    expected = makeProgram(
        (10, [0x20, 0xF1]),
        (11, [0x20, 0xE0]),
    )
    assert data == expected


def testExplicitNumbersMustStrictlyIncrease():
    """Explicit line numbers out of order raise ValueError."""
    with pytest.raises(ValueError, match="must increase"):
        tokenize(["20 PRINT", "10 END"])


def testExplicitNumberMustBeatAutoCounter():
    """An explicit number that has been overtaken by the auto-counter errors."""
    # After three numberless lines (1, 2, 3), an explicit "3 ..." tries
    # to reuse 3 which is not strictly greater than last_line.
    with pytest.raises(ValueError, match="must increase"):
        tokenize(["PRINT", "PRINT", "PRINT", "3 END"])
