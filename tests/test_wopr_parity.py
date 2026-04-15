# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Parity harness: wopr engine vs the legacy basic.py tokenizer.

For every input here, both tokenizers must emit identical bytes.
Until the wopr engine reaches parity, divergent inputs are marked
`xfail` and are closed out as each step of the wopr plan lands.

When the parity suite is fully green the wopr engine is the
tokenizer, and basic.tokenize becomes a thin wrapper around
wopr.tokenizeLine.
"""

import pytest

from beebtools.basic import _tokenizeContent
from beebtools.wopr import tokenizeLine
from beebtools.wopr_dialects import BBC_BASIC_II


def assertParity(text: str) -> None:
    """Both tokenizers must produce identical bytes for `text`."""
    assert tokenizeLine(text, BBC_BASIC_II) == _tokenizeContent(text)


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


@pytest.mark.xfail(reason="wopr step 4: dot-abbreviation matching not yet ported")
def testParityDotAbbreviation():
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
