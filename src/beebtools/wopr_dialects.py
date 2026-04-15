# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Dialect instances consumed by the wopr tokenizer engine.

Each dialect is a `Dialect` instance carrying an ordered tuple of
`Keyword` rows. Engine code never reads from this module; it consumes
a `Dialect` passed by the caller.

BBC_BASIC_II is derived from the token byte table in `tokens.py` and
hand-curated flag sets capturing BBC BASIC II keyword behaviour. The
keyword tuple is ordered longest-first so that full-keyword matches
find ENDPROC before END and STRING$( before ERROR. Abbreviation
precedence (P. -> PRINT, E. -> ENDPROC) requires ROM table ordering
and is introduced in a later step.
"""

from typing import List

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


def _buildBbcBasicII() -> Dialect:
    """Assemble the BBC BASIC II dialect table from the token map and flag sets."""
    rows: List[Keyword] = []

    for token, name in TOKENS.items():
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
        ))

    rows.sort(key=lambda kw: (-len(kw.name), kw.token))

    return Dialect(name="BBC BASIC II", keywords=tuple(rows))


BBC_BASIC_II = _buildBbcBasicII()
