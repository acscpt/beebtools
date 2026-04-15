# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Dialect instances consumed by the wopr tokenizer engine.

Each dialect is a `Dialect` instance carrying an ordered tuple of
`Keyword` rows. Engine code never reads from this module; it consumes
a `Dialect` passed by the caller.

BBC_BASIC_II is derived from the token byte table in `tokens.py` and
the hand-curated flag sets below. BBC_BASIC_IV is BBC_BASIC_II plus
the small set of additional keywords the BBC Master's CMOS BASIC
introduced. The flag sets are token-byte-keyed and shared between
dialects: a flag for a token byte not present in a dialect's keyword
list is simply ignored.

The keyword tuple is sorted with ROM-preferred abbreviation winners
first, then longest-first. Full-keyword matches see ENDPROC before
END; abbreviations see PRINT before PAGE; both fall out of one walk.
"""

from typing import Iterable, List, Tuple

from .tokens import TOKENS
from .wopr import Dialect, Keyword


_PSEUDO_VAR_STATEMENT_FORMS = frozenset({0xCF, 0xD0, 0xD1, 0xD2, 0xD3})

_PSEUDO_VAR_BASE = frozenset({0x8F, 0x90, 0x91, 0x92, 0x93})

_CONDITIONAL = frozenset({
    0x8F, 0x90, 0x91, 0x92, 0x93,   # PTR PAGE TIME LOMEM HIMEM (function forms)
    0x9A,   # BGET
    0x9C,   # COUNT
    0x9E,   # ERL
    0x9F,   # ERR
    0xA2,   # EXT
    0xA3,   # FALSE
    0xAF,   # PI
    0xB1,   # POS
    0xB3,   # RND
    0xB9,   # TRUE
    0xBC,   # VPOS
    0xC5,   # EOF
    0xCA,   # NEW
    0xCB,   # OLD
    0xCE,   # EDIT      (BASIC IV; ignored by BASIC II since 0xCE is unused there)
    0xD5,   # BPUT
    0xD8,   # CLEAR
    0xD9,   # CLOSE
    0xDA,   # CLG
    0xDB,   # CLS
    0xE0,   # END
    0xE1,   # ENDPROC
    0xF6,   # REPORT
    0xF8,   # RETURN
    0xF9,   # RUN
    0xFA,   # STOP
})

_MIDDLE = frozenset({
    0x8F, 0x90, 0x91, 0x92, 0x93,   # pseudo-var function forms
    0xC8,   # LOAD
    0xCD,   # SAVE
    0xD4,   # SOUND
    0xD5,   # BPUT
    0xD6,   # CALL
    0xD7,   # CHAIN
    0xD9,   # CLOSE
    0xDE,   # DIM
    0xDF,   # DRAW
    0xE2,   # ENVELOPE
    0xE3,   # FOR
    0xE4,   # GOSUB
    0xE5,   # GOTO
    0xE6,   # GCOL
    0xE7,   # IF
    0xE8,   # INPUT
    0xEA,   # LOCAL
    0xEB,   # MODE
    0xEC,   # MOVE
    0xED,   # NEXT
    0xEE,   # ON
    0xEF,   # VDU
    0xF0,   # PLOT
    0xF1,   # PRINT
    0xF2,   # PROC
    0xF3,   # READ
    0xF5,   # REPEAT
    0xF7,   # RESTORE
    0xFB,   # COLOUR
    0xFC,   # TRACE
    0xFD,   # UNTIL
    0xFE,   # WIDTH
    0xFF,   # OSCLI
})

_START_OF_STATEMENT = frozenset({
    0x85,   # ERROR
    0x8B,   # ELSE
    0x8C,   # THEN
    0xE9,   # LET
})

_LINE_LITERAL = frozenset({
    0xDC,   # DATA
    0xF4,   # REM
})

_EXPECT_LINE_NUMBER = frozenset({
    0x8B,   # ELSE
    0x8C,   # THEN
    0xC6,   # AUTO
    0xC7,   # DELETE
    0xC9,   # LIST
    0xCC,   # RENUMBER
    0xE4,   # GOSUB
    0xE5,   # GOTO
    0xF7,   # RESTORE
    0xFC,   # TRACE
})

_FN_PROC = frozenset({
    0xA4,   # FN
    0xF2,   # PROC
})

# ROM-preferred abbreviation winners. The BBC BASIC II ROM keyword
# table is hand-ordered so the common keyword in each shared-prefix
# cluster is reached first by the linear matcher: PRINT before PAGE,
# ENDPROC before END, REPEAT before READ, TIME before TAN, AND before
# ABS, PROC before POS/PTR/PI. Marking these keywords promotes them
# to the head of the dialect's keyword tuple so dot-abbreviations
# resolve as the ROM does.
_COMMON_ABBREV = frozenset({
    0x80,   # AND        (A. AN.)
    0xE1,   # ENDPROC    (E. EN.)
    0xF1,   # PRINT      (P. PR.)
    0xF2,   # PROC       (PRO.)
    0xF5,   # REPEAT     (R. RE.)
    0x91,   # TIME       (T. TI. TIM.)
})


# Keywords BBC BASIC IV adds on top of BBC BASIC II. The 6502 CMOS
# BASIC shipped on the BBC Master is otherwise a strict superset of
# BASIC II: same token byte assignments, same flag behaviour. EDIT
# fills the previously-unused 0xCE slot.
#
# BASIC IV's other documented additions need no token additions:
#
# - TIME$ is the battery-backed RTC pseudo-variable. Per mdfs.net's
#   token table it tokenises as the TIME byte (0x91/0xD1) followed by
#   a literal '$' (0x24); the interpreter distinguishes TIME from
#   TIME$ at run time. Falls out of the existing engine for free.
# - LIST IF, ON PROC, EXT# as statement, and VDU '|' all reuse
#   existing tokens with new parser/interpreter semantics.
# - OPENUP and OPENOUT are already in BASIC II at 0xAD and 0xAE.
#
# So at the token-byte level, EDIT is the only delta.
_BBC_BASIC_IV_ADDITIONS: Tuple[Tuple[int, str], ...] = (
    (0xCE, "EDIT"),
)


def _buildKeywordRows(items: Iterable[Tuple[int, str]]) -> Tuple[Keyword, ...]:
    """Build the ordered keyword tuple for a dialect from (token, name) pairs.

    Pseudo-variable statement-form rows are skipped because the engine
    derives them at emission time by adding 0x40 to the function-form
    token. The result is sorted with ROM-preferred abbreviation winners
    first, then longest-first within each cluster.
    """
    rows: List[Keyword] = []

    for token, name in items:
        if token in _PSEUDO_VAR_STATEMENT_FORMS:
            continue

        rows.append(Keyword(
            name=name,
            token=token,
            conditional=token in _CONDITIONAL,
            middle=token in _MIDDLE,
            startOfStatement=token in _START_OF_STATEMENT,
            lineLiteral=token in _LINE_LITERAL,
            expectLineNumber=token in _EXPECT_LINE_NUMBER,
            fnProc=token in _FN_PROC,
            pseudoVarBase=token in _PSEUDO_VAR_BASE,
            commonAbbrev=token in _COMMON_ABBREV,
        ))

    rows.sort(key=lambda kw: (not kw.commonAbbrev, -len(kw.name), kw.token))

    return tuple(rows)


def _buildBbcBasicII() -> Dialect:
    """Assemble the BBC BASIC II dialect from the canonical token map."""
    return Dialect(name="BBC BASIC II", keywords=_buildKeywordRows(TOKENS.items()))


def _buildBbcBasicIV() -> Dialect:
    """Assemble the BBC BASIC IV dialect: BASIC II plus the Master's additions."""
    items = list(TOKENS.items()) + list(_BBC_BASIC_IV_ADDITIONS)
    return Dialect(name="BBC BASIC IV", keywords=_buildKeywordRows(items))


BBC_BASIC_II = _buildBbcBasicII()
BBC_BASIC_IV = _buildBbcBasicIV()
