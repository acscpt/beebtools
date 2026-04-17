# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC token tables and per-keyword behaviour specs.

Single source of truth for which bytes mean which keywords and for
the behavioural facts the tokenizer and detokenizer attach to each
keyword. The tokenizer engine in `sophie.py` consumes a Dialect built
from these specs; the legacy detokenizer in `basic.py` reads the
flat `TOKENS` byte-to-name dict.

Pseudo-variable keywords (PTR, PAGE, TIME, LOMEM, HIMEM) appear in
the spec table once, at their function-form byte (0x8F-0x93). The
statement form (0xCF-0xD3) is byte + 0x40 and is derived at tokenise
time. The flat `TOKENS` dict includes both forms so byte-walking
consumers can name every token they see.

BBC BASIC IV is a strict superset of II at the byte level. The only
net-new byte-token is EDIT at 0xCE (unused in II). TIME$ tokenises
as the TIME byte followed by a literal '$' and needs no spec entry.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Tuple


class Flag(str, Enum):
    """Per-keyword behaviour flag.

    The engine reads these out of each Keyword's `flags` set and
    branches accordingly. Values match the historical string names so
    the behavioural vocabulary lives in one enum rather than in bool
    fields repeated across every keyword row.

    - CONDITIONAL          suppress match if followed by identifier char
    - MIDDLE               sits mid-statement; keeps MID_STATEMENT state
    - START_OF_STATEMENT   resets to AT_START after the keyword
    - LINE_LITERAL         rest of line is opaque (REM, DATA)
    - EXPECT_LINE_NUMBER   next digit run tokenises as the 0x8D line ref
    - FN_PROC              next identifier is an opaque user FN/PROC name
    - PSEUDO_VAR_BASE      statement form is byte + 0x40 at statement start
    - COMMON_ABBREV        ROM hand-ordering: claims short prefixes first
    """

    CONDITIONAL = "conditional"
    MIDDLE = "middle"
    START_OF_STATEMENT = "startOfStatement"
    LINE_LITERAL = "lineLiteral"
    EXPECT_LINE_NUMBER = "expectLineNumber"
    FN_PROC = "fnProc"
    PSEUDO_VAR_BASE = "pseudoVarBase"
    COMMON_ABBREV = "commonAbbrev"


@dataclass(frozen=True)
class TokenSpec:
    """One BBC BASIC keyword: its byte, its name, and its behaviours.

    The `flags` frozenset carries the behavioural facts the tokenizer
    and interpreter apply when they encounter this keyword (see the
    Flag enum for the vocabulary).
    """

    byte: int
    name: str
    flags: FrozenSet[Flag] = field(default_factory=frozenset)


def _T(byte: int, name: str, *flags: Flag) -> TokenSpec:
    """Compact TokenSpec constructor: flag enum members as positional args."""
    return TokenSpec(byte, name, frozenset(flags))


# BBC BASIC II keyword table. One row per keyword at the function-
# form byte. Extracted from BASIC2.rom; behaviour flags curated from
# published ROM behaviour and parity with the legacy tokenizer.
# 0x8D (inline line-number escape) has no entry here; it is emitted
# by the engine directly, not matched as a keyword.
BBC_BASIC_II_TOKENS: Tuple[TokenSpec, ...] = (
    _T(0x80, "AND", Flag.COMMON_ABBREV),
    _T(0x81, "DIV"),
    _T(0x82, "EOR"),
    _T(0x83, "MOD"),
    _T(0x84, "OR"),
    _T(0x85, "ERROR", Flag.START_OF_STATEMENT),
    _T(0x86, "LINE"),
    _T(0x87, "OFF"),
    _T(0x88, "STEP"),
    _T(0x89, "SPC"),
    _T(0x8A, "TAB("),
    _T(0x8B, "ELSE", Flag.START_OF_STATEMENT, Flag.EXPECT_LINE_NUMBER),
    _T(0x8C, "THEN", Flag.START_OF_STATEMENT, Flag.EXPECT_LINE_NUMBER),
    _T(0x8E, "OPENIN"),
    _T(0x8F, "PTR", Flag.CONDITIONAL, Flag.MIDDLE, Flag.PSEUDO_VAR_BASE),
    _T(0x90, "PAGE", Flag.CONDITIONAL, Flag.MIDDLE, Flag.PSEUDO_VAR_BASE),
    _T(0x91, "TIME", Flag.CONDITIONAL, Flag.MIDDLE, Flag.PSEUDO_VAR_BASE, Flag.COMMON_ABBREV),
    _T(0x92, "LOMEM", Flag.CONDITIONAL, Flag.MIDDLE, Flag.PSEUDO_VAR_BASE),
    _T(0x93, "HIMEM", Flag.CONDITIONAL, Flag.MIDDLE, Flag.PSEUDO_VAR_BASE),
    _T(0x94, "ABS"),
    _T(0x95, "ACS"),
    _T(0x96, "ADVAL"),
    _T(0x97, "ASC"),
    _T(0x98, "ASN"),
    _T(0x99, "ATN"),
    _T(0x9A, "BGET", Flag.CONDITIONAL),
    _T(0x9B, "COS"),
    _T(0x9C, "COUNT", Flag.CONDITIONAL),
    _T(0x9D, "DEG"),
    _T(0x9E, "ERL", Flag.CONDITIONAL),
    _T(0x9F, "ERR", Flag.CONDITIONAL),
    _T(0xA0, "EVAL"),
    _T(0xA1, "EXP"),
    _T(0xA2, "EXT", Flag.CONDITIONAL),
    _T(0xA3, "FALSE", Flag.CONDITIONAL),
    _T(0xA4, "FN", Flag.FN_PROC),
    _T(0xA5, "GET"),
    _T(0xA6, "INKEY"),
    _T(0xA7, "INSTR("),
    _T(0xA8, "INT"),
    _T(0xA9, "LEN"),
    _T(0xAA, "LN"),
    _T(0xAB, "LOG"),
    _T(0xAC, "NOT"),
    _T(0xAD, "OPENUP"),
    _T(0xAE, "OPENOUT"),
    _T(0xAF, "PI", Flag.CONDITIONAL),
    _T(0xB0, "POINT("),
    _T(0xB1, "POS", Flag.CONDITIONAL),
    _T(0xB2, "RAD"),
    _T(0xB3, "RND", Flag.CONDITIONAL),
    _T(0xB4, "SGN"),
    _T(0xB5, "SIN"),
    _T(0xB6, "SQR"),
    _T(0xB7, "TAN"),
    _T(0xB8, "TO"),
    _T(0xB9, "TRUE", Flag.CONDITIONAL),
    _T(0xBA, "USR"),
    _T(0xBB, "VAL"),
    _T(0xBC, "VPOS", Flag.CONDITIONAL),
    _T(0xBD, "CHR$"),
    _T(0xBE, "GET$"),
    _T(0xBF, "INKEY$"),
    _T(0xC0, "LEFT$("),
    _T(0xC1, "MID$("),
    _T(0xC2, "RIGHT$("),
    _T(0xC3, "STR$"),
    _T(0xC4, "STRING$("),
    _T(0xC5, "EOF", Flag.CONDITIONAL),
    _T(0xC6, "AUTO", Flag.EXPECT_LINE_NUMBER),
    _T(0xC7, "DELETE", Flag.EXPECT_LINE_NUMBER),
    _T(0xC8, "LOAD", Flag.MIDDLE),
    _T(0xC9, "LIST", Flag.EXPECT_LINE_NUMBER),
    _T(0xCA, "NEW", Flag.CONDITIONAL),
    _T(0xCB, "OLD", Flag.CONDITIONAL),
    _T(0xCC, "RENUMBER", Flag.EXPECT_LINE_NUMBER),
    _T(0xCD, "SAVE", Flag.MIDDLE),
    # 0xCE is unused in BASIC II; BASIC IV fills the slot with EDIT.
    _T(0xD4, "SOUND", Flag.MIDDLE),
    _T(0xD5, "BPUT", Flag.CONDITIONAL, Flag.MIDDLE),
    _T(0xD6, "CALL", Flag.MIDDLE),
    _T(0xD7, "CHAIN", Flag.MIDDLE),
    _T(0xD8, "CLEAR", Flag.CONDITIONAL),
    _T(0xD9, "CLOSE", Flag.CONDITIONAL, Flag.MIDDLE),
    _T(0xDA, "CLG", Flag.CONDITIONAL),
    _T(0xDB, "CLS", Flag.CONDITIONAL),
    _T(0xDC, "DATA", Flag.LINE_LITERAL),
    _T(0xDD, "DEF"),
    _T(0xDE, "DIM", Flag.MIDDLE),
    _T(0xDF, "DRAW", Flag.MIDDLE),
    _T(0xE0, "END", Flag.CONDITIONAL),
    _T(0xE1, "ENDPROC", Flag.CONDITIONAL, Flag.COMMON_ABBREV),
    _T(0xE2, "ENVELOPE", Flag.MIDDLE),
    _T(0xE3, "FOR", Flag.MIDDLE),
    _T(0xE4, "GOSUB", Flag.MIDDLE, Flag.EXPECT_LINE_NUMBER),
    _T(0xE5, "GOTO", Flag.MIDDLE, Flag.EXPECT_LINE_NUMBER),
    _T(0xE6, "GCOL", Flag.MIDDLE),
    _T(0xE7, "IF", Flag.MIDDLE),
    _T(0xE8, "INPUT", Flag.MIDDLE),
    _T(0xE9, "LET", Flag.START_OF_STATEMENT),
    _T(0xEA, "LOCAL", Flag.MIDDLE),
    _T(0xEB, "MODE", Flag.MIDDLE),
    _T(0xEC, "MOVE", Flag.MIDDLE),
    _T(0xED, "NEXT", Flag.MIDDLE),
    _T(0xEE, "ON", Flag.MIDDLE),
    _T(0xEF, "VDU", Flag.MIDDLE),
    _T(0xF0, "PLOT", Flag.MIDDLE),
    _T(0xF1, "PRINT", Flag.MIDDLE, Flag.COMMON_ABBREV),
    _T(0xF2, "PROC", Flag.MIDDLE, Flag.FN_PROC, Flag.COMMON_ABBREV),
    _T(0xF3, "READ", Flag.MIDDLE),
    _T(0xF4, "REM", Flag.LINE_LITERAL),
    _T(0xF5, "REPEAT", Flag.MIDDLE, Flag.COMMON_ABBREV),
    _T(0xF6, "REPORT", Flag.CONDITIONAL),
    _T(0xF7, "RESTORE", Flag.MIDDLE, Flag.EXPECT_LINE_NUMBER),
    _T(0xF8, "RETURN", Flag.CONDITIONAL),
    _T(0xF9, "RUN", Flag.CONDITIONAL),
    _T(0xFA, "STOP", Flag.CONDITIONAL),
    _T(0xFB, "COLOUR", Flag.MIDDLE),
    _T(0xFC, "TRACE", Flag.MIDDLE, Flag.EXPECT_LINE_NUMBER),
    _T(0xFD, "UNTIL", Flag.MIDDLE),
    _T(0xFE, "WIDTH", Flag.MIDDLE),
    _T(0xFF, "OSCLI", Flag.MIDDLE),
)


# Keywords BBC BASIC IV adds on top of II. EDIT is the only net-new
# byte-token; it fills the previously-unused 0xCE gap. Stand-alone
# command keyword; conditional matches the style of NEW/OLD/RUN.
# TIME$ is BASIC IV's RTC pseudo-variable and tokenises as the TIME
# byte + literal '$', so it needs no spec entry.
BBC_BASIC_IV_ADDITIONS: Tuple[TokenSpec, ...] = (
    _T(0xCE, "EDIT", Flag.CONDITIONAL),
)


# Full BBC BASIC IV keyword table. Strict superset of BASIC II.
BBC_BASIC_IV_TOKENS: Tuple[TokenSpec, ...] = (
    BBC_BASIC_II_TOKENS + BBC_BASIC_IV_ADDITIONS
)


def _buildFlatTokensMap(specs: Tuple[TokenSpec, ...]) -> Dict[int, str]:
    """Byte-to-name mapping with both pseudo-var forms expanded.

    Byte-walking consumers (notably the legacy detokenizer) need a
    name for every token byte they might see in a program stream.
    Pseudo-variable statement forms (byte + 0x40) are expanded here
    so the detokenizer can decode 0xCF as PTR, 0xD0 as PAGE, etc.
    """
    mapping: Dict[int, str] = {}

    for spec in specs:
        mapping[spec.byte] = spec.name

        if Flag.PSEUDO_VAR_BASE in spec.flags:
            mapping[spec.byte + 0x40] = spec.name

    return mapping


# Byte -> keyword name. The flat projection of the BASIC II spec
# table used by the legacy detokenizer in basic.py and by tests that
# invert the mapping to look up a token byte by keyword name.
TOKENS: Dict[int, str] = _buildFlatTokensMap(BBC_BASIC_II_TOKENS)


# Token bytes whose match makes the rest of the line opaque literal
# text (REM, DATA). Exported as a frozenset for O(1) membership.
LINE_LITERAL_TOKENS: FrozenSet[int] = frozenset(
    spec.byte for spec in BBC_BASIC_II_TOKENS if Flag.LINE_LITERAL in spec.flags
)
