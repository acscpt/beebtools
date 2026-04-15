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


# Keyword-bearing inputs: the legacy tokenizer emits tokens, wopr
# emits literals. These xfails pin the engine's growth: each one
# becomes a passing test as the relevant transition logic lands.

@pytest.mark.xfail(reason="wopr step 3: keyword matching not yet ported")
def testParityPrintKeyword():
    assertParity("PRINT 1")


@pytest.mark.xfail(reason="wopr step 2: IN_STRING transition not yet ported")
def testParityStringLiteral():
    assertParity('PRINT "hello"')


@pytest.mark.xfail(reason="wopr step 2: LINE_LITERAL transition not yet ported")
def testParityRemTail():
    assertParity("REM the rest of this line is opaque")


@pytest.mark.xfail(reason="wopr step 4: dot-abbreviation matching not yet ported")
def testParityDotAbbreviation():
    assertParity("PR.")


@pytest.mark.xfail(reason="wopr step 3: pseudo-var form selection not yet ported")
def testParityPseudoVarStatementForm():
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


@pytest.mark.xfail(reason="wopr step 3: line-number ref encoding not yet ported")
def testParityGotoLineRef():
    assertParity("GOTO 100")
