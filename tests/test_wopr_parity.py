# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Parity harness: wopr engine vs the legacy basic.py tokenizer.

For every parity input here, both tokenizers must emit identical
bytes. Inputs where the wopr engine is ROM-correct and the legacy
basic.py is wrong (alphabetical-first abbreviation resolution)
live in the ROM-correct section below; those assert the wopr output
directly against the byte the BBC ROM would emit.

When the parity suite is fully green the wopr engine is the
tokenizer, and basic.tokenize becomes a thin wrapper around
wopr.tokenizeLine.
"""

from beebtools.basic import _tokenizeContent
from beebtools.tokens import TOKENS
from beebtools.wopr import tokenizeLine
from beebtools.wopr_dialects import BBC_BASIC_II


# TOKENS maps both forms of pseudo-vars (PAGE at 0x90 and 0xD0) to the
# same name. The engine emits the function-form base byte and adds
# 0x40 itself when the keyword lands at start of statement, so the
# lookup here keeps the lower of the two.
_TOKEN_OF: dict = {}
for _tok, _name in TOKENS.items():
    if _name not in _TOKEN_OF or _tok < _TOKEN_OF[_name]:
        _TOKEN_OF[_name] = _tok


def assertParity(text: str) -> None:
    """Both tokenizers must produce identical bytes for `text`."""
    assert tokenizeLine(text, BBC_BASIC_II) == _tokenizeContent(text)


def assertRomFirstByte(text: str, expected: int) -> None:
    """The first byte of the wopr output must equal the ROM-correct byte."""
    out = tokenizeLine(text, BBC_BASIC_II)
    assert out[:1] == bytes([expected]), (
        f"wopr({text!r}) starts 0x{out[0]:02X}, expected 0x{expected:02X}"
    )


# Skeleton-stage parity: pure-literal inputs round-trip identically
# because both tokenizers emit them verbatim. These are the first
# parity tests that will pass at step 1.

def testParityEmpty():
    assertParity("")


def testParityBareLiteral():
    assertParity("A")


def testParityAssignmentLiteralRhs():
    assertParity("A=42")


def testParitySpacesAndPunctuation():
    assertParity("A = 42 + 1")


def testParityPrintKeyword():
    """PRINT followed by a literal: keyword tokenised, rest literal."""
    assertParity("PRINT 1")


def testParityStringLiteral():
    """PRINT \"hello\": keyword plus a string that must round-trip."""
    assertParity('PRINT "hello"')


def testParityRemTail():
    """REM swallows the rest of the line as opaque bytes."""
    assertParity("REM the rest of this line is opaque")


def testParityDotAbbreviation():
    """PR. -> PRINT: legacy and ROM agree on this one."""
    assertParity("PR.")


def testParityPseudoVarStatementForm():
    """PAGE= at start of statement picks the +0x40 statement form."""
    assertParity("PAGE=&1900")


def testParityHexLiteral():
    """Greedy hex emits all-literal bytes in both engines; passes at skeleton."""
    assertParity("A=&3DEF")


def testParityBareString():
    """A bare string round-trips: both engines emit the bytes verbatim."""
    assertParity('"hello"')


def testParityAssignmentToString():
    """Literal-prefix + string: A="hello" is all literal bytes on both sides."""
    assertParity('A="hello"')


def testParityStringWithPunctuation():
    """String content is preserved byte-for-byte including spaces and symbols."""
    assertParity('X$="one, two; three"')


def testParityEmptyString():
    """An empty string emits just the two quote bytes."""
    assertParity('A=""')


def testParityAdjacentStrings():
    """Two adjacent strings: the engine must exit and re-enter IN_STRING."""
    assertParity('"one""two"')


def testParityGotoLineRef():
    """GOTO 100: digits encode as a 0x8D inline line-number reference."""
    assertParity("GOTO 100")


def testParityGotoChainedLineRefs():
    """GOTO 10, 20, 30: comma preserves the expect-line-number latch."""
    assertParity("ON X GOTO 10, 20, 30")


def testParityForNext():
    """FOR/NEXT loop with a TO and STEP: two middle-keywords, back-to-back."""
    assertParity("FOR I=1 TO 10 STEP 2: NEXT I")


def testParityIfThenElse():
    """IF/THEN/ELSE: start-of-statement keywords re-arm line-number expectation."""
    assertParity("IF X=1 THEN 100 ELSE 200")


def testParityFnProcOpaqueName():
    """PROC and FN eat the following identifier as opaque bytes."""
    assertParity("PROCfoo(1): A=FNbar(2)")


def testParityConditionalTimer():
    """TIMER must not tokenise as TIME + R: conditional suppression."""
    assertParity("TIMER=0")


def testParityPseudoVarFunctionForm():
    """PAGE on the right of = takes its function form, no +0x40 offset."""
    assertParity("X=PAGE")


def testParityDataTail():
    """DATA swallows the rest of the line literally, commas included."""
    assertParity("DATA one, two, three")


def testParityLetAtStart():
    """LET at start transitions back to AT_START for what follows."""
    assertParity("LET A=1")


# ROM-correct abbreviations: cases where the BBC BASIC II ROM keyword
# table is hand-ordered so the common keyword wins the short prefix.
# Legacy basic.py walks the keyword table alphabetically and gets
# different answers (P. -> PAGE, E. -> ELSE, etc.); wopr matches the
# ROM. These tests assert the wopr output directly against the byte
# the ROM would emit, no parity check.

def testRomAbbrevPDotIsPrint():
    """P. resolves to PRINT (PRINT precedes PAGE/POINT in ROM table)."""
    assertRomFirstByte("P.", _TOKEN_OF["PRINT"])


def testRomAbbrevEDotIsEndproc():
    """E. resolves to ENDPROC (ENDPROC precedes END/ELSE/EOF in ROM table)."""
    assertRomFirstByte("E.", _TOKEN_OF["ENDPROC"])


def testRomAbbrevEnDotIsEndproc():
    """EN. also resolves to ENDPROC: the EN prefix still matches it first."""
    assertRomFirstByte("EN.", _TOKEN_OF["ENDPROC"])


def testRomAbbrevEndDotIsEnd():
    """END. resolves to END: the four-letter prefix only matches END."""
    assertRomFirstByte("END.", _TOKEN_OF["END"])


def testRomAbbrevRDotIsRepeat():
    """R. resolves to REPEAT (REPEAT precedes READ/RAD in ROM table)."""
    assertRomFirstByte("R.", _TOKEN_OF["REPEAT"])


def testRomAbbrevReDotIsRepeat():
    """RE. also resolves to REPEAT: the RE prefix still matches it first."""
    assertRomFirstByte("RE.", _TOKEN_OF["REPEAT"])


def testRomAbbrevReaDotIsRead():
    """REA. resolves to READ: the three-letter prefix only matches READ."""
    assertRomFirstByte("REA.", _TOKEN_OF["READ"])


def testRomAbbrevTDotIsTime():
    """T. resolves to TIME (TIME precedes TAN/TAB/THEN in ROM table)."""
    # TIME at start of statement gets the +0x40 statement form.
    assertRomFirstByte("T.", _TOKEN_OF["TIME"] + 0x40)


def testRomAbbrevTaDotIsTan():
    """TA. resolves to TAN: the TA prefix only matches TAN."""
    assertRomFirstByte("TA.", _TOKEN_OF["TAN"])


def testRomAbbrevADotIsAnd():
    """A. resolves to AND (AND precedes ABS/ACS/ASC/ASN in ROM table)."""
    assertRomFirstByte("A.", _TOKEN_OF["AND"])


def testRomAbbrevAbDotIsAbs():
    """AB. resolves to ABS: the AB prefix only matches ABS."""
    assertRomFirstByte("AB.", _TOKEN_OF["ABS"])


# Case sensitivity. The BBC BASIC ROM's keyword matcher is
# case-sensitive: only uppercase letters at the cursor can match a
# keyword row. The keyboard input stage uppercases as you type, which
# is why interactive listings always appear uppercase, but text loaded
# from disc or tape passes through tokenisation verbatim. Lowercase
# identifiers that happen to contain keyword letters (value, data,
# print, for, to) must survive as literal bytes.

def testCaseSensitiveLowercaseValueStaysLiteral():
    """Lowercase `value` does not match VAL; all bytes stay literal."""
    assertParity("value")


def testCaseSensitiveLowercaseDataStaysLiteral():
    """Lowercase `data` does not match DATA; no line-literal takeover."""
    assertParity("data")


def testCaseSensitiveLowercasePrintStaysLiteral():
    """Lowercase `print` does not match PRINT."""
    assertParity("print x")


def testCaseSensitiveLowercaseForStaysLiteral():
    """Lowercase `for` does not match FOR."""
    assertParity("for i=1 to 10")


def testCaseSensitiveLowercaseDotAbbrevDoesNotMatch():
    """Lowercase `p.` does not match any keyword by abbreviation."""
    assertParity("p.")


def testCaseSensitiveUppercaseValueMatchesVAL():
    """VALUE tokenises VAL + 'UE' (VAL's conditional flag rejects the
    identifier-char suppression only when followed by an ident char;
    here the letters after VAL are themselves part of the source)."""
    assertParity("VALUE")


# Embedded assembler blocks. The BBC BASIC ROM has no distinct
# assembler-mode in the tokenizer: `[` and `]` are ordinary literal
# bytes, and the text between them is tokenised by the same rules as
# the rest of the line. Keyword-letter mnemonics like LDA, STA, JMP
# are not keywords so pass through as literals; the logical operators
# AND/OR/EOR and control words FOR/NEXT *are* keywords and tokenise
# even inside `[...]`. Parity with the legacy tokenizer is the check.

def testAssemblerBlockLiteralsAndOperands():
    """Straight LDA/STA block with an AND operator tokenises AND only."""
    assertParity("[LDA value AND &7F: STA &70]")


def testAssemblerBlockLoopWithForNext():
    """FOR/NEXT inside an assembler block still tokenise as keywords."""
    assertParity("[FOR I%=0 TO 7: LDA data,X: NEXT]")


def testAssemblerBlockBracketsAreLiterals():
    """Bare `[` and `]` are ordinary ASCII in the output."""
    assertParity("[]")


def testAssemblerBlockWithLabel():
    """A .label inside an assembler block stays literal (no token)."""
    assertParity("[.loop LDA &70: BNE loop]")
