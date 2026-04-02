# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC II tokenizer.

Converts LIST-style plain text back into the tokenized binary format
understood by the BBC Micro BASIC ROM. This is the inverse of
detokenize.py.
"""

import re
from typing import List, Tuple

from .tokens import TOKENS, LINE_LITERAL_TOKENS


# -----------------------------------------------------------------------
# Reverse mapping: keyword string -> token byte
# -----------------------------------------------------------------------

# Pseudo-variables appear twice in the token table: a function form
# (0x8F-0x93) used inside expressions, and a statement form (0xCF-0xD3)
# used at the start of a statement (e.g. TIME=0). The base mapping uses
# the function form; the statement form is selected at tokenize time by
# adding 0x40 when at the start of a statement.
_PSEUDO_VAR_STATEMENT_TOKENS = {0xCF, 0xD0, 0xD1, 0xD2, 0xD3}
_PSEUDO_VAR_BASE = {0x8F, 0x90, 0x91, 0x92, 0x93}

_KEYWORD_TO_TOKEN = {}
for _tok, _kw in TOKENS.items():
    if _tok in _PSEUDO_VAR_STATEMENT_TOKENS:
        continue  # handled by the +0x40 pseudo-variable logic
    _KEYWORD_TO_TOKEN[_kw] = _tok

# Sort keywords longest-first so longer matches take priority (e.g.
# ENDPROC before END).
_KEYWORDS_BY_LENGTH = sorted(
    _KEYWORD_TO_TOKEN.keys(), key=len, reverse=True
)

# -----------------------------------------------------------------------
# Keyword flags (BBC BASIC II)
# -----------------------------------------------------------------------

# Conditional flag (C): do NOT tokenize this keyword if the character
# immediately after the keyword text is alphanumeric. Prevents e.g.
# "TIMER" from being tokenized as TIME + "R".
_CONDITIONAL = {
    0x8F, 0x90, 0x91, 0x92, 0x93,  # PTR PAGE TIME LOMEM HIMEM (func)
    0x9A,  # BGET
    0x9C,  # COUNT
    0x9E,  # ERL
    0x9F,  # ERR
    0xA2,  # EXT
    0xA3,  # FALSE
    0xAF,  # PI
    0xB1,  # POS
    0xB3,  # RND
    0xB9,  # TRUE
    0xBC,  # VPOS
    0xC5,  # EOF
    0xCA,  # NEW
    0xCB,  # OLD
    0xD5,  # BPUT
    0xD8,  # CLEAR
    0xD9,  # CLOSE
    0xDA,  # CLG
    0xDB,  # CLS
    0xE0,  # END
    0xE1,  # ENDPROC
    0xF6,  # REPORT
    0xF8,  # RETURN
    0xF9,  # RUN
    0xFA,  # STOP
}

# Line-number flag (L): after this keyword, digit sequences are encoded
# as compact 0x8D inline references.
_LINENUM = {
    0x8B,  # ELSE
    0x8C,  # THEN
    0xC6,  # AUTO
    0xC7,  # DELETE
    0xC9,  # LIST
    0xCC,  # RENUMBER
    0xE4,  # GOSUB
    0xE5,  # GOTO
    0xF7,  # RESTORE
    0xFC,  # TRACE
}

# Start-of-statement flag (S): after tokenizing this keyword the
# tokenizer re-enters start-of-statement mode.
_START_OF_STATEMENT = {
    0x85,  # ERROR
    0x8B,  # ELSE
    0x8C,  # THEN
    0xE9,  # LET
}

# Middle-of-statement flag (M): after tokenizing this keyword the
# tokenizer moves out of start-of-statement mode.
_MIDDLE = {
    0x8F, 0x90, 0x91, 0x92, 0x93,  # pseudo-var function forms
    0xC8,  # LOAD
    0xCD,  # SAVE
    0xD4,  # SOUND
    0xD5,  # BPUT
    0xD6,  # CALL
    0xD7,  # CHAIN
    0xD9,  # CLOSE
    0xDE,  # DIM
    0xDF,  # DRAW
    0xE2,  # ENVELOPE
    0xE3,  # FOR
    0xE4,  # GOSUB
    0xE5,  # GOTO
    0xE6,  # GCOL
    0xE7,  # IF
    0xE8,  # INPUT
    0xEA,  # LOCAL
    0xEB,  # MODE
    0xEC,  # MOVE
    0xED,  # NEXT
    0xEE,  # ON
    0xEF,  # VDU
    0xF0,  # PLOT
    0xF1,  # PRINT
    0xF2,  # PROC
    0xF3,  # READ
    0xF5,  # REPEAT
    0xF7,  # RESTORE
    0xFB,  # COLOUR
    0xFC,  # TRACE
    0xFD,  # UNTIL
    0xFE,  # WIDTH
    0xFF,  # OSCLI
}

# FN/PROC flag (F): the identifier name immediately after FN or PROC
# must not be tokenized.
_FN_PROC = {0xA4, 0xF2}  # FN, PROC


# -----------------------------------------------------------------------
# Line-number encoding
# -----------------------------------------------------------------------

def encodeLineRef(linenum: int) -> bytes:
    """Encode a line number as the 4-byte 0x8D inline reference.

    The format is:
        byte 0: 0x8D sentinel
        byte 1: control byte with inverted high bits XORed with 0x54
        byte 2: low byte of line number (bits 0-5) with bit 6 set
        byte 3: high byte of line number (bits 0-5) with bit 6 set

    Args:
        linenum: BBC BASIC line number (0-32767).

    Returns:
        Four bytes: the 0x8D marker followed by three encoded data bytes.
    """
    b1 = (((linenum & 0x00C0) >> 2) | ((linenum & 0xC000) >> 12)) ^ 0x54
    b2 = (linenum & 0x3F) | 0x40
    b3 = ((linenum >> 8) & 0x3F) | 0x40

    return bytes([0x8D, b1, b2, b3])


# -----------------------------------------------------------------------
# Line parsing
# -----------------------------------------------------------------------

_LINE_RE = re.compile(r"^\s*(\d+)(.*)")


def _parseLine(line: str) -> Tuple[int, str]:
    """Extract the line number and content from a LIST-style text line.

    Accepts formats like '   10PRINT "HELLO"' where the line number may
    be preceded by whitespace.

    Args:
        line: One line of LIST-style BASIC text.

    Returns:
        (line_number, content_text) tuple.

    Raises:
        ValueError: If the line does not start with a valid line number.
    """
    m = _LINE_RE.match(line)

    if not m:
        raise ValueError(f"Cannot parse line number from: {line!r}")

    return int(m.group(1)), m.group(2)


# -----------------------------------------------------------------------
# Content tokenizer
# -----------------------------------------------------------------------

def _startsWithKeyword(upper_text: str, pos: int, length: int) -> bool:
    """Check whether the text at pos begins with a known keyword.

    Used by the conditional-flag logic to allow adjacent tokens (e.g.
    CLS followed immediately by PRINT) while still rejecting keywords
    embedded in variable names (e.g. FALSE inside FALSEflag).
    """
    for kw in _KEYWORDS_BY_LENGTH:
        kw_len = len(kw)
        if pos + kw_len > length:
            continue
        if upper_text[pos:pos + kw_len] == kw:
            return True
    return False


def _tokenizeContent(text: str) -> bytes:
    """Tokenize the content portion of one BASIC line.

    This processes the text left to right, matching keywords, encoding
    line-number references after L-flag keywords, and respecting string
    literals, REM/DATA tails, FN/PROC names, and star commands.

    Args:
        text: Line content (everything after the line number).

    Returns:
        Tokenized content bytes.
    """
    result = bytearray()
    i = 0
    length = len(text)
    at_start = True        # start-of-statement mode
    linenum_mode = False   # encoding line numbers after L-flag keyword
    in_string = False
    literal_rest = False   # after REM or DATA - rest of line is literal

    upper = text.upper()

    while i < length:
        ch = text[i]

        # After REM or DATA token, or after * at start of statement,
        # the rest of the line is literal ASCII with no tokenization.
        if literal_rest:
            result.append(ord(ch))
            i += 1
            continue

        # Inside a quoted string - pass through verbatim.
        if in_string:
            result.append(ord(ch))
            if ch == '"':
                in_string = False
            i += 1
            continue

        # Open quote - enter string mode.
        if ch == '"':
            in_string = True
            result.append(0x22)
            i += 1
            linenum_mode = False
            continue

        # Colon resets to start-of-statement mode.
        if ch == ':':
            result.append(ord(':'))
            at_start = True
            linenum_mode = False
            i += 1
            continue

        # Star command at start of statement - rest of line is literal.
        if ch == '*' and at_start:
            result.append(ord('*'))
            literal_rest = True
            i += 1
            continue

        # Ampersand skips hex digits that follow (prevents tokenizing
        # the hex literal, e.g. &DEF should not tokenize DEF).
        if ch == '&':
            result.append(ord('&'))
            i += 1
            while i < length and upper[i] in '0123456789ABCDEF':
                result.append(ord(text[i]))
                i += 1
            continue

        # Line-number mode: encode digit sequences as 0x8D references.
        # Spaces and commas are emitted as-is and keep the mode active.
        # Any other character exits line-number mode.
        if linenum_mode:
            if ch == ' ':
                result.append(0x20)
                i += 1
                continue

            if ch == ',':
                result.append(ord(','))
                i += 1
                continue

            if ch.isdigit():
                # Collect all consecutive digits.
                num_start = i
                while i < length and text[i].isdigit():
                    i += 1
                linenum = int(text[num_start:i])
                result.extend(encodeLineRef(linenum))
                continue

            # Non-digit, non-comma, non-space exits line-number mode.
            linenum_mode = False
            # Fall through to normal processing for this character.

        # Try to match a keyword at the current position.
        matched = False

        for kw in _KEYWORDS_BY_LENGTH:
            kw_len = len(kw)

            if i + kw_len > length:
                continue

            # Case-insensitive comparison on the keyword portion.
            if upper[i:i + kw_len] != kw:
                continue

            token = _KEYWORD_TO_TOKEN[kw]

            # Conditional flag: do not tokenize if the next character is
            # alphanumeric AND the text following this keyword does not
            # itself start with another keyword. This prevents FALSE
            # matching in FALSEflag while still allowing adjacent tokens
            # like CLS+PRINT (which the detokenizer produces as
            # "CLSPRINT" with no separator).
            if token in _CONDITIONAL:
                next_pos = i + kw_len
                if next_pos < length and (upper[next_pos].isalnum() or upper[next_pos] == '_'):
                    if not _startsWithKeyword(upper, next_pos, length):
                        continue

            # Pseudo-variable: use statement form (+0x40) at start of
            # statement, function form otherwise.
            if token in _PSEUDO_VAR_BASE and at_start:
                result.append(token + 0x40)
            else:
                result.append(token)

            i += kw_len

            # Update tokenizer state based on keyword flags.
            if token in LINE_LITERAL_TOKENS:
                # REM and DATA - rest of line is literal.
                literal_rest = True
            elif token in _FN_PROC:
                # Skip the identifier name after FN or PROC.
                while i < length and (text[i].isalnum() or text[i] == '_'):
                    result.append(ord(text[i]))
                    i += 1
            else:
                if token in _LINENUM:
                    linenum_mode = True
                if token in _START_OF_STATEMENT:
                    at_start = True
                elif token in _MIDDLE:
                    at_start = False

            matched = True
            break

        if matched:
            continue

        # No keyword matched - emit the character as a literal byte.
        if ch.isalpha() or ch == '_':
            at_start = False

        result.append(ord(ch))
        i += 1

    return bytes(result)


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def tokenize(lines: List[str]) -> bytes:
    """Convert LIST-style text lines to tokenized BBC BASIC II binary.

    Accepts the output of detokenize() and produces bytes suitable for
    writing to a DFS disc image. Blank lines and lines containing only
    whitespace are silently skipped.

    Args:
        lines: List of strings in LIST format, each starting with a
            line number (optionally preceded by whitespace).

    Returns:
        Tokenized BBC BASIC II program as bytes, including the
        end-of-program marker (0x0D 0xFF).

    Raises:
        ValueError: If a non-blank line cannot be parsed.
    """
    result = bytearray()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        linenum, content_text = _parseLine(line)
        content = _tokenizeContent(content_text)

        hi = (linenum >> 8) & 0xFF
        lo = linenum & 0xFF

        # Length byte counts from the hi byte to the start of the next
        # record's 0x0D.  That is: hi + lo + length_byte + content = 3 + 1 + len(content).
        linelen = 3 + 1 + len(content)

        result.append(0x0D)
        result.append(hi)
        result.append(lo)
        result.append(linelen)
        result.extend(content)

    # End-of-program marker.
    result.append(0x0D)
    result.append(0xFF)

    return bytes(result)
