# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the BBC BASIC IV dialect.

BBC BASIC IV is a strict superset of BBC BASIC II at the token-byte
level. Two checks here:

1. **Superset:** every BASIC II input tokenises identically under
   BBC_BASIC_IV. If this regresses, the IV dialect is not a clean
   superset and something has gone wrong.
2. **EDIT (0xCE):** the one new keyword we have a publicly-confirmed
   byte assignment for. Tokenises to 0xCE; abbreviation ED. resolves
   to EDIT; E. still resolves to ENDPROC (preserved by ROM-preferred
   ordering, which EDIT does not join).

TIME$ is the other publicly-known BASIC IV addition. Its exact byte
is not in any clean-room source we have, so it is intentionally not
in the dialect yet. A beebjit probe will close that gap.
"""

from beebtools.tokens import TOKENS
from beebtools.wopr import tokenizeLine
from beebtools.wopr_dialects import BBC_BASIC_II, BBC_BASIC_IV


_TOKEN_OF: dict = {}
for _tok, _name in TOKENS.items():
    if _name not in _TOKEN_OF or _tok < _TOKEN_OF[_name]:
        _TOKEN_OF[_name] = _tok


_BASIC_II_PROGRAMS = [
    "",
    "A=42",
    "PRINT 1",
    'PRINT "hello"',
    "REM the rest of this line is opaque",
    "PAGE=&1900",
    "GOTO 100",
    "ON X GOTO 10, 20, 30",
    "FOR I=1 TO 10 STEP 2: NEXT I",
    "IF X=1 THEN 100 ELSE 200",
    "PROCfoo(1): A=FNbar(2)",
    "TIMER=0",
    "DATA one, two, three",
    "LET A=1",
    "P.",
    "PR.",
    "E.",
    "EN.",
    "END.",
    "R.",
    "T.",
]


def testIvIsSupersetOfIi():
    """Every BASIC II input tokenises identically under BBC_BASIC_IV."""
    for src in _BASIC_II_PROGRAMS:
        ii = tokenizeLine(src, BBC_BASIC_II)
        iv = tokenizeLine(src, BBC_BASIC_IV)
        assert ii == iv, f"superset broken for {src!r}: II={ii.hex()} IV={iv.hex()}"


def testEditTokenIs0xCE():
    """EDIT in BASIC IV emits the single byte 0xCE."""
    out = tokenizeLine("EDIT", BBC_BASIC_IV)
    assert out == bytes([0xCE])


def testEditAbbreviationEdDot():
    """ED. resolves to EDIT in BASIC IV (no other ED-prefix keyword)."""
    out = tokenizeLine("ED.", BBC_BASIC_IV)
    assert out == bytes([0xCE])


def testEditAbbreviationEDotStillEndproc():
    """E. still resolves to ENDPROC in BASIC IV; EDIT does not steal it."""
    out = tokenizeLine("E.", BBC_BASIC_IV)
    assert out[0] == _TOKEN_OF["ENDPROC"]


def testEditConditionalSuppressedByIdentChar():
    """EDITOR must stay a variable, not tokenise as EDIT + 'OR'."""
    out = tokenizeLine("EDITOR=1", BBC_BASIC_IV)
    assert 0xCE not in out


def testEditNotInBasicIi():
    """The BASIC II dialect does not recognise EDIT; it stays literal."""
    out = tokenizeLine("EDIT", BBC_BASIC_II)
    assert 0xCE not in out
    assert out == b"EDIT"


def testOnProcIsTokenisedSameAsOnGoto():
    """ON expr PROC list (BASIC IV idiom) tokenises with no special rule:
    ON, PROC, and the body fall out of the keyword flags in both dialects.
    """
    src = "ON X PROCa,PROCb,PROCc"
    ii = tokenizeLine(src, BBC_BASIC_II)
    iv = tokenizeLine(src, BBC_BASIC_IV)
    assert ii == iv
    assert _TOKEN_OF["ON"] in iv
    assert _TOKEN_OF["PROC"] in iv
