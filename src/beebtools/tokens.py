# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC token tables and per-keyword behaviour specs.

Single source of truth for which bytes mean which keywords and for
the behavioural facts the tokenizer and detokenizer attach to each
keyword. The tokenizer engine in `wopr.py` consumes a Dialect built
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

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple


@dataclass(frozen=True)
class TokenSpec:
    """One BBC BASIC keyword: its byte, its name, and its behaviours.

    Flags capture the facts the tokenizer and interpreter apply to
    the keyword when they encounter it:

    - conditional       suppress match if followed by identifier char
    - middle            sits mid-statement; keeps MID_STATEMENT state
    - startOfStatement  resets to AT_START after the keyword
    - lineLiteral       rest of line is opaque (REM, DATA)
    - expectLineNumber  next digit run tokenises as the 0x8D line ref
    - fnProc            next identifier is an opaque user FN/PROC name
    - pseudoVarBase     statement form is byte + 0x40 at statement start
    - commonAbbrev      ROM hand-ordering: claims short prefixes first
    """

    byte: int
    name: str
    conditional: bool = False
    middle: bool = False
    startOfStatement: bool = False
    lineLiteral: bool = False
    expectLineNumber: bool = False
    fnProc: bool = False
    pseudoVarBase: bool = False
    commonAbbrev: bool = False


def _T(byte: int, name: str, *flags: str) -> TokenSpec:
    """Compact TokenSpec constructor: flag names as positional args."""
    return TokenSpec(byte, name, **{flag: True for flag in flags})


# BBC BASIC II keyword table. One row per keyword at the function-
# form byte. Extracted from BASIC2.rom; behaviour flags curated from
# published ROM behaviour and parity with the legacy tokenizer.
# 0x8D (inline line-number escape) has no entry here; it is emitted
# by the engine directly, not matched as a keyword.
BBC_BASIC_II_TOKENS: Tuple[TokenSpec, ...] = (
    _T(0x80, "AND", "commonAbbrev"),
    _T(0x81, "DIV"),
    _T(0x82, "EOR"),
    _T(0x83, "MOD"),
    _T(0x84, "OR"),
    _T(0x85, "ERROR", "startOfStatement"),
    _T(0x86, "LINE"),
    _T(0x87, "OFF"),
    _T(0x88, "STEP"),
    _T(0x89, "SPC"),
    _T(0x8A, "TAB("),
    _T(0x8B, "ELSE", "startOfStatement", "expectLineNumber"),
    _T(0x8C, "THEN", "startOfStatement", "expectLineNumber"),
    _T(0x8E, "OPENIN"),
    _T(0x8F, "PTR", "conditional", "middle", "pseudoVarBase"),
    _T(0x90, "PAGE", "conditional", "middle", "pseudoVarBase"),
    _T(0x91, "TIME", "conditional", "middle", "pseudoVarBase", "commonAbbrev"),
    _T(0x92, "LOMEM", "conditional", "middle", "pseudoVarBase"),
    _T(0x93, "HIMEM", "conditional", "middle", "pseudoVarBase"),
    _T(0x94, "ABS"),
    _T(0x95, "ACS"),
    _T(0x96, "ADVAL"),
    _T(0x97, "ASC"),
    _T(0x98, "ASN"),
    _T(0x99, "ATN"),
    _T(0x9A, "BGET", "conditional"),
    _T(0x9B, "COS"),
    _T(0x9C, "COUNT", "conditional"),
    _T(0x9D, "DEG"),
    _T(0x9E, "ERL", "conditional"),
    _T(0x9F, "ERR", "conditional"),
    _T(0xA0, "EVAL"),
    _T(0xA1, "EXP"),
    _T(0xA2, "EXT", "conditional"),
    _T(0xA3, "FALSE", "conditional"),
    _T(0xA4, "FN", "fnProc"),
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
    _T(0xAF, "PI", "conditional"),
    _T(0xB0, "POINT("),
    _T(0xB1, "POS", "conditional"),
    _T(0xB2, "RAD"),
    _T(0xB3, "RND", "conditional"),
    _T(0xB4, "SGN"),
    _T(0xB5, "SIN"),
    _T(0xB6, "SQR"),
    _T(0xB7, "TAN"),
    _T(0xB8, "TO"),
    _T(0xB9, "TRUE", "conditional"),
    _T(0xBA, "USR"),
    _T(0xBB, "VAL"),
    _T(0xBC, "VPOS", "conditional"),
    _T(0xBD, "CHR$"),
    _T(0xBE, "GET$"),
    _T(0xBF, "INKEY$"),
    _T(0xC0, "LEFT$("),
    _T(0xC1, "MID$("),
    _T(0xC2, "RIGHT$("),
    _T(0xC3, "STR$"),
    _T(0xC4, "STRING$("),
    _T(0xC5, "EOF", "conditional"),
    _T(0xC6, "AUTO", "expectLineNumber"),
    _T(0xC7, "DELETE", "expectLineNumber"),
    _T(0xC8, "LOAD", "middle"),
    _T(0xC9, "LIST", "expectLineNumber"),
    _T(0xCA, "NEW", "conditional"),
    _T(0xCB, "OLD", "conditional"),
    _T(0xCC, "RENUMBER", "expectLineNumber"),
    _T(0xCD, "SAVE", "middle"),
    # 0xCE is unused in BASIC II; BASIC IV fills the slot with EDIT.
    _T(0xD4, "SOUND", "middle"),
    _T(0xD5, "BPUT", "conditional", "middle"),
    _T(0xD6, "CALL", "middle"),
    _T(0xD7, "CHAIN", "middle"),
    _T(0xD8, "CLEAR", "conditional"),
    _T(0xD9, "CLOSE", "conditional", "middle"),
    _T(0xDA, "CLG", "conditional"),
    _T(0xDB, "CLS", "conditional"),
    _T(0xDC, "DATA", "lineLiteral"),
    _T(0xDD, "DEF"),
    _T(0xDE, "DIM", "middle"),
    _T(0xDF, "DRAW", "middle"),
    _T(0xE0, "END", "conditional"),
    _T(0xE1, "ENDPROC", "conditional", "commonAbbrev"),
    _T(0xE2, "ENVELOPE", "middle"),
    _T(0xE3, "FOR", "middle"),
    _T(0xE4, "GOSUB", "middle", "expectLineNumber"),
    _T(0xE5, "GOTO", "middle", "expectLineNumber"),
    _T(0xE6, "GCOL", "middle"),
    _T(0xE7, "IF", "middle"),
    _T(0xE8, "INPUT", "middle"),
    _T(0xE9, "LET", "startOfStatement"),
    _T(0xEA, "LOCAL", "middle"),
    _T(0xEB, "MODE", "middle"),
    _T(0xEC, "MOVE", "middle"),
    _T(0xED, "NEXT", "middle"),
    _T(0xEE, "ON", "middle"),
    _T(0xEF, "VDU", "middle"),
    _T(0xF0, "PLOT", "middle"),
    _T(0xF1, "PRINT", "middle", "commonAbbrev"),
    _T(0xF2, "PROC", "middle", "fnProc", "commonAbbrev"),
    _T(0xF3, "READ", "middle"),
    _T(0xF4, "REM", "lineLiteral"),
    _T(0xF5, "REPEAT", "middle", "commonAbbrev"),
    _T(0xF6, "REPORT", "conditional"),
    _T(0xF7, "RESTORE", "middle", "expectLineNumber"),
    _T(0xF8, "RETURN", "conditional"),
    _T(0xF9, "RUN", "conditional"),
    _T(0xFA, "STOP", "conditional"),
    _T(0xFB, "COLOUR", "middle"),
    _T(0xFC, "TRACE", "middle", "expectLineNumber"),
    _T(0xFD, "UNTIL", "middle"),
    _T(0xFE, "WIDTH", "middle"),
    _T(0xFF, "OSCLI", "middle"),
)


# Keywords BBC BASIC IV adds on top of II. EDIT is the only net-new
# byte-token; it fills the previously-unused 0xCE gap. Stand-alone
# command keyword; conditional matches the style of NEW/OLD/RUN.
# TIME$ is BASIC IV's RTC pseudo-variable and tokenises as the TIME
# byte + literal '$', so it needs no spec entry.
BBC_BASIC_IV_ADDITIONS: Tuple[TokenSpec, ...] = (
    _T(0xCE, "EDIT", "conditional"),
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

        if spec.pseudoVarBase:
            mapping[spec.byte + 0x40] = spec.name

    return mapping


# Byte -> keyword name. The flat projection of the BASIC II spec
# table used by the legacy detokenizer in basic.py and by tests that
# invert the mapping to look up a token byte by keyword name.
TOKENS: Dict[int, str] = _buildFlatTokensMap(BBC_BASIC_II_TOKENS)


# Token bytes whose match makes the rest of the line opaque literal
# text (REM, DATA). Exported as a frozenset for O(1) membership.
LINE_LITERAL_TOKENS: FrozenSet[int] = frozenset(
    spec.byte for spec in BBC_BASIC_II_TOKENS if spec.lineLiteral
)
