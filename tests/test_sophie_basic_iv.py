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
from beebtools.sophie import detokenizeLine, tokenizeLine
from beebtools.basic_dialects import BBC_BASIC_II, BBC_BASIC_IV


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


# TIME$ is BASIC IV's RTC pseudo-variable. Per mdfs.net's BBC BASIC
# token table, the 6502 BASIC IV ROM tokenises TIME$ as the TIME byte
# (0x91 function-form, 0xD1 statement-form) followed by a literal '$'
# byte (0x24). There is no separate token for TIME$. The interpreter
# distinguishes TIME from TIME$ at run time by the trailing '$'.
#
# Consequence for our engine: TIME$ falls out of existing logic with
# no dialect changes. The TIME keyword matches first (its conditional
# flag does not suppress here because '$' is not an identifier char),
# emits its token, and the '$' falls through to the literal-emission
# arm. The byte sequence is identical in BBC_BASIC_II and BBC_BASIC_IV
# at the tokenizer level; only the interpreter would treat the two
# differently.

def testTimeDollarRhsIsFunctionFormPlusLiteralDollar():
    """X=TIME$ tokenises as X = 0x91 0x24 in both dialects."""
    expected = bytes([ord('X'), ord('='), 0x91, 0x24])
    assert tokenizeLine("X=TIME$", BBC_BASIC_IV) == expected
    assert tokenizeLine("X=TIME$", BBC_BASIC_II) == expected


def testTimeDollarLhsIsStatementFormPlusLiteralDollar():
    """TIME$="01 JAN 2026" tokenises with the 0xD1 statement form."""
    out = tokenizeLine('TIME$="01 JAN 2026"', BBC_BASIC_IV)
    assert out[0] == 0xD1
    assert out[1] == 0x24


def testTimeDollarConditionalNotSuppressedByDollar():
    """TIME's conditional flag suppresses on identifier chars only.
    '$' is not an identifier char, so TIME still tokenises before $.
    """
    out = tokenizeLine("TIME$", BBC_BASIC_IV)
    assert 0xD1 in out or 0x91 in out


def testTimeRStillSuppressedInIV():
    """TIMER (identifier char after TIME) stays a variable in IV too."""
    out = tokenizeLine("TIMER=0", BBC_BASIC_IV)
    assert 0x91 not in out
    assert 0xD1 not in out


# Detokenizer dialect behaviour. The same byte stream decodes
# differently under II and IV for the one byte where they disagree
# (0xCE): IV knows it as EDIT, II sees it as an unknown token.

def testDetokenizeEditInBasicIv():
    """0xCE decodes to EDIT under BBC_BASIC_IV."""
    assert detokenizeLine(bytes([0xCE]), BBC_BASIC_IV) == "EDIT"


def testDetokenizeEditUnknownInBasicII():
    """0xCE has no keyword in II; renders as [&CE] placeholder."""
    assert detokenizeLine(bytes([0xCE]), BBC_BASIC_II) == "[&CE]"


def testDetokenizeRoundTripEditInBasicIv():
    """EDIT round-trips through tokenise and detokenise under IV."""
    tokens = tokenizeLine("EDIT", BBC_BASIC_IV)
    assert detokenizeLine(tokens, BBC_BASIC_IV) == "EDIT"


def testDetokenizeTimeDollarStatementForm():
    """Statement-form TIME$ (0xD1 0x24) decodes to 'TIME$'."""
    assert detokenizeLine(bytes([0xD1, 0x24]), BBC_BASIC_IV) == "TIME$"


def testDetokenizeBasicIiProgramIdenticalAcrossDialects():
    """A token stream with no IV-only bytes decodes identically."""
    # PRINT "HI" tokenises the same in II and IV
    src = 'PRINT "HI"'
    tokens = tokenizeLine(src, BBC_BASIC_II)
    assert detokenizeLine(tokens, BBC_BASIC_II) == detokenizeLine(tokens, BBC_BASIC_IV)
