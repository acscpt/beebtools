# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC II token table and related constants."""

# Token table extracted from BASIC2.rom.
# Maps token byte values (0x80-0xFF) to their keyword strings.
# 0x8D is not in this table - it is the inline line-number escape sequence.
TOKENS = {
    0x80: "AND",       0x81: "DIV",       0x82: "EOR",       0x83: "MOD",
    0x84: "OR",        0x85: "ERROR",     0x86: "LINE",      0x87: "OFF",
    0x88: "STEP",      0x89: "SPC",       0x8A: "TAB(",      0x8B: "ELSE",
    0x8C: "THEN",
    # 0x8D = inline encoded line number (3 data bytes follow)
    0x8E: "OPENIN",    0x8F: "PTR",       0x90: "PAGE",      0x91: "TIME",
    0x92: "LOMEM",     0x93: "HIMEM",     0x94: "ABS",       0x95: "ACS",
    0x96: "ADVAL",     0x97: "ASC",       0x98: "ASN",       0x99: "ATN",
    0x9A: "BGET",      0x9B: "COS",       0x9C: "COUNT",     0x9D: "DEG",
    0x9E: "ERL",       0x9F: "ERR",       0xA0: "EVAL",      0xA1: "EXP",
    0xA2: "EXT",       0xA3: "FALSE",     0xA4: "FN",        0xA5: "GET",
    0xA6: "INKEY",     0xA7: "INSTR(",    0xA8: "INT",       0xA9: "LEN",
    0xAA: "LN",        0xAB: "LOG",       0xAC: "NOT",       0xAD: "OPENUP",
    0xAE: "OPENOUT",   0xAF: "PI",        0xB0: "POINT(",    0xB1: "POS",
    0xB2: "RAD",       0xB3: "RND",       0xB4: "SGN",       0xB5: "SIN",
    0xB6: "SQR",       0xB7: "TAN",       0xB8: "TO",        0xB9: "TRUE",
    0xBA: "USR",       0xBB: "VAL",       0xBC: "VPOS",      0xBD: "CHR$",
    0xBE: "GET$",      0xBF: "INKEY$",    0xC0: "LEFT$(",    0xC1: "MID$(",
    0xC2: "RIGHT$(",   0xC3: "STR$",      0xC4: "STRING$(",  0xC5: "EOF",
    0xC6: "AUTO",      0xC7: "DELETE",    0xC8: "LOAD",      0xC9: "LIST",
    0xCA: "NEW",       0xCB: "OLD",       0xCC: "RENUMBER",  0xCD: "SAVE",
    # 0xCE is unused in BBC BASIC II (reserved gap before the pseudo-variable block)
    0xCF: "PTR",       0xD0: "PAGE",      0xD1: "TIME",      0xD2: "LOMEM",
    0xD3: "HIMEM",     0xD4: "SOUND",     0xD5: "BPUT",      0xD6: "CALL",
    0xD7: "CHAIN",     0xD8: "CLEAR",     0xD9: "CLOSE",     0xDA: "CLG",
    0xDB: "CLS",       0xDC: "DATA",      0xDD: "DEF",       0xDE: "DIM",
    0xDF: "DRAW",      0xE0: "END",       0xE1: "ENDPROC",   0xE2: "ENVELOPE",
    0xE3: "FOR",       0xE4: "GOSUB",     0xE5: "GOTO",      0xE6: "GCOL",
    0xE7: "IF",        0xE8: "INPUT",     0xE9: "LET",       0xEA: "LOCAL",
    0xEB: "MODE",      0xEC: "MOVE",      0xED: "NEXT",      0xEE: "ON",
    0xEF: "VDU",       0xF0: "PLOT",      0xF1: "PRINT",     0xF2: "PROC",
    0xF3: "READ",      0xF4: "REM",       0xF5: "REPEAT",    0xF6: "REPORT",
    0xF7: "RESTORE",   0xF8: "RETURN",    0xF9: "RUN",       0xFA: "STOP",
    0xFB: "COLOUR",    0xFC: "TRACE",     0xFD: "UNTIL",     0xFE: "WIDTH",
    0xFF: "OSCLI",
}

# Tokens after which the remainder of the line is literal ASCII text
# and should not be expanded for further tokens.
LINE_LITERAL_TOKENS = {0xDC, 0xF4}  # DATA, REM
