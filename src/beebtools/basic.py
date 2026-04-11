# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC II facade module.

Unified API for all BBC BASIC program handling: tokenization,
detokenization, content classification, and text escaping. Higher
layers (disc.py, cli.py) import from this module rather than reaching
into the individual sub-modules.

This module contains the core tokenizer and detokenizer (merged from
the former detokenize.py and tokenize.py), plus content-inspection
utilities that answer questions about BASIC program data.

The pretty-printer (pretty.py) is a separate optional display transform;
its prettyPrint function is re-exported here for convenience so callers
have a single import point for all BASIC operations.
"""

import re
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from .tokens import TOKENS, LINE_LITERAL_TOKENS
from .entry import DiscEntry
from .pretty import compactLine, prettyPrint  # noqa: F401 - re-export


# =====================================================================
# Detokenizer
# =====================================================================

def decodeLineRef(b0: int, b1: int, b2: int) -> int:
    """Decode a BBC BASIC inline line-number reference.

    The encoding XORs the top two bits of each byte of the 16-bit line number
    into a single control byte, with the sentinel value 0x54.

    Args:
        b0: Control byte encoding the high bits.
        b1: Encoded low byte payload.
        b2: Encoded high byte payload.

    Returns:
        Decoded BBC BASIC line number as an integer.
    """
    x = b0 ^ 0x54
    lo = (b1 & 0x3F) | ((x & 0x30) << 2)
    hi = (b2 & 0x3F) | ((x & 0x0C) << 4)
    return hi * 256 + lo


def basicProgramSize(data: bytes) -> int:
    """Return the number of bytes occupied by the BASIC program in data.

    Walks the tokenized line structure and returns the offset just past
    the 0x0D 0xFF end-of-program marker. If the file is entirely BASIC
    this equals len(data) (or close to it, with a few padding bytes).
    If there is appended machine code the return value will be much
    smaller than len(data).

    Returns 0 if data does not start with a valid BASIC line marker.
    """
    pos = 0

    while pos < len(data):
        if data[pos] != 0x0D:
            break

        pos += 1
        if pos >= len(data):
            break

        hi = data[pos]
        if hi == 0xFF:
            # End-of-program marker.  Program occupies bytes 0..pos inclusive.
            return pos + 1

        if pos + 2 >= len(data):
            break

        linelen = data[pos + 2]

        # A valid record is at least 4 bytes (hi, lo, len, trailing 0x0D).
        if linelen < 4:
            break

        pos = pos - 1 + linelen

    # Fell off the end without hitting 0xFF - return current position.
    return pos


def detokenize(data: bytes) -> List[str]:
    """Convert a tokenized BBC BASIC program to LIST-style text lines.

    Each line in the returned list corresponds to one BASIC program line and
    is formatted as a right-justified 5-character line number followed by the
    decoded statement text.

    Args:
        data: Raw bytes of a tokenized BBC BASIC II program.

    Returns:
        List of strings, one per program line.
    """
    lines = []
    pos = 0

    # Walk the tokenized program line-by-line.  Each record starts with 0x0D,
    # followed by high/low line number bytes, a length byte, and the content.
    while pos < len(data):
        if data[pos] != 0x0D:
            break

        pos += 1
        if pos >= len(data):
            break

        hi = data[pos]
        if hi == 0xFF:
            # End-of-program marker.
            break

        # Guard against a truncated record at the end of the file.
        if pos + 2 >= len(data):
            break

        lo = data[pos + 1]
        linenum = hi * 256 + lo
        linelen = data[pos + 2]

        # A valid record is at least 4 bytes (hi, lo, len, trailing 0x0D).
        # A zero or tiny length means we have hit trailing machine code
        # or corrupt data appended after the BASIC program - stop parsing.
        if linelen < 4:
            break

        # Content runs from the byte after the header to the end of the record.
        # The length byte counts from the hi byte to where the next 0x0D starts.
        content = data[pos + 3 : pos - 1 + linelen]
        pos = pos - 1 + linelen

        text = _decodeLineContent(content)
        lines.append(f"{linenum:>5d}{text}")

    return lines


def _decodeLineContent(content: bytes) -> str:
    """Decode token bytes for one BASIC line into LIST text.

    Args:
        content: Tokenized bytes for one line body (no line header, no trailing 0x0D).

    Returns:
        Decoded line text string.
    """
    parts = []
    i = 0
    in_string = False
    literal_rest = False

    while i < len(content):
        b = content[i]

        # Line terminator - always ends the content regardless of context.
        # In Acorn/Wilson format the content slice includes a trailing 0x0D;
        # in Russell format it does not. Either way, 0x0D cannot appear as
        # actual program text on the BBC Micro.
        if b == 0x0D:
            break

        # Inside a quoted string - emit raw bytes verbatim, handle close quote.
        if in_string:
            if b == 0x22:
                in_string = False
                parts.append('"')
            else:
                parts.append(chr(b))
            i += 1
            continue

        # After DATA or REM the rest of the line is literal - no token expansion.
        if literal_rest:
            parts.append(chr(b))
            i += 1
            continue

        # Opening quote - switch to string mode.
        if b == 0x22:
            in_string = True
            parts.append('"')
            i += 1
            continue

        # Inline encoded line number (GOTO/GOSUB target).
        if b == 0x8D:
            if i + 3 < len(content):
                target = decodeLineRef(content[i + 1], content[i + 2],
                                       content[i + 3])
                parts.append(str(target))
                i += 4
            else:
                parts.append("?")
                i += 1
            continue

        # Token byte - look it up and emit the keyword.
        if b >= 0x80:
            keyword = TOKENS.get(b)
            if keyword is not None:
                parts.append(keyword)
                if b in LINE_LITERAL_TOKENS:
                    literal_rest = True
            else:
                parts.append(f"[&{b:02X}]")
            i += 1
            continue

        # Plain ASCII character.
        parts.append(chr(b))
        i += 1

    return "".join(parts)


# =====================================================================
# Tokenizer
# =====================================================================

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

_KEYWORD_TO_TOKEN: Dict[str, int] = {}
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
# FN/PROC symbol table (pass 1)
# -----------------------------------------------------------------------

# Matches DEFFNname or DEFPROCname at any position in a line. The name
# is the run of alphanumeric and underscore characters after FN/PROC.
_DEF_FN_PROC_RE = re.compile(r'DEF\s*(FN|PROC)([A-Za-z_]\w*)')


def _collectFnProcNames(lines: List[str]) -> Dict[str, FrozenSet[str]]:
    """Scan all lines for DEFFN/DEFPROC declarations and collect names.

    Returns a dict mapping the prefix ('FN' or 'PROC') to a frozenset
    of declared names for that prefix. Used in pass 2 to determine
    where a FN/PROC identifier ends so that the keyword after it can
    be tokenized correctly.
    """
    names: Dict[str, Set[str]] = {'FN': set(), 'PROC': set()}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Extract content after line number.
        m = _LINE_RE.match(stripped)
        if not m:
            continue
        content = m.group(2)

        # Find all DEF FN/PROC declarations in this line.
        for match in _DEF_FN_PROC_RE.finditer(content):
            prefix = match.group(1)   # 'FN' or 'PROC'
            name = match.group(2)     # identifier name
            names[prefix].add(name)

    return {k: frozenset(v) for k, v in names.items()}


def _matchFnProcName(
    text: str, pos: int, length: int,
    known_names: FrozenSet[str]
) -> int:
    """Determine how many characters of the FN/PROC identifier to consume.

    Tries to find the longest known name from the symbol table that
    matches at position pos. If no known name matches, falls back to
    greedy consumption of all alphanumeric/underscore characters (the
    standard ROM behaviour).

    Returns the number of identifier characters to consume.
    """
    # Collect the full greedy identifier span.
    end = pos
    while end < length and (text[end].isalnum() or text[end] == '_'):
        end += 1
    greedy_len = end - pos

    if not known_names or greedy_len == 0:
        return greedy_len

    # Try longest-match against known names. We check from longest to
    # shortest so the first hit is the best match.
    candidate = text[pos:end]
    best = 0
    for name in known_names:
        nlen = len(name)
        if nlen > greedy_len:
            continue
        if nlen > best and candidate[:nlen] == name:
            best = nlen

    # If a known name matched, use that length. Otherwise fall back
    # to greedy (the name may be defined in another file via CHAIN).
    return best if best > 0 else greedy_len


# -----------------------------------------------------------------------
# Content tokenizer
# -----------------------------------------------------------------------

def _startsWithKeyword(text: str, pos: int, length: int) -> bool:
    """Check whether the text at pos begins with a known keyword.

    Used by the conditional-flag logic to allow adjacent tokens (e.g.
    CLS followed immediately by PRINT) while still rejecting keywords
    embedded in variable names (e.g. FALSE inside FALSEflag).
    """
    for kw in _KEYWORDS_BY_LENGTH:
        kw_len = len(kw)
        if pos + kw_len > length:
            continue
        if text[pos:pos + kw_len] == kw:
            return True
    return False


def _tokenizeContent(text: str, fn_proc_names: Dict[str, FrozenSet[str]] = None) -> bytes:
    """Tokenize the content portion of one BASIC line.

    This processes the text left to right, matching keywords, encoding
    line-number references after L-flag keywords, and respecting string
    literals, REM/DATA tails, FN/PROC names, and star commands.

    Args:
        text: Line content (everything after the line number).
        fn_proc_names: Symbol table mapping 'FN'/'PROC' to known names.

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
    in_variable = False    # inside a variable/identifier name

    if fn_proc_names is None:
        fn_proc_names = {}

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
                in_variable = False
            i += 1
            continue

        # Open quote - enter string mode.
        if ch == '"':
            in_string = True
            in_variable = False
            result.append(0x22)
            i += 1
            linenum_mode = False
            continue

        # Colon resets to start-of-statement mode.
        if ch == ':':
            result.append(ord(':'))
            at_start = True
            linenum_mode = False
            in_variable = False
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
            in_variable = False
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

        # The BBC BASIC ROM only attempts keyword matching when not
        # inside a variable name. Letters and underscores enter variable
        # mode; anything else (digits, operators, tokens) exits it.
        # This prevents embedded keywords like ON in NOON, TO in BOTTOM
        # from being tokenized, while still allowing 1TO10 (digit before
        # keyword) and PRINTTAB( (token before keyword).
        if in_variable and (ch.isalpha() or ch == '_'):
            at_start = False
            result.append(ord(ch))
            i += 1
            continue

        # Try to match a keyword at the current position.
        matched = False

        for kw in _KEYWORDS_BY_LENGTH:
            kw_len = len(kw)

            if i + kw_len > length:
                continue

            # Case-sensitive comparison: keywords match only UPPERCASE.
            # The BBC BASIC ROM uppercases keyboard input before
            # tokenizing, so all keywords in tokenized programs are
            # uppercase. Lowercase text (variable names, assembler
            # labels) must not be matched.
            if text[i:i + kw_len] != kw:
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
                if next_pos < length and (text[next_pos].isalnum() or text[next_pos] == '_'):
                    if not _startsWithKeyword(text, next_pos, length):
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
                # FN/PROC flag: the identifier name immediately after
                # the token must not be tokenized. Use the symbol table
                # to determine exactly where the name ends so that any
                # keyword following it (e.g. THEN after FNld) is still
                # tokenized correctly. Falls back to greedy consumption
                # when the name is not in the symbol table.
                prefix = 'FN' if token == 0xA4 else 'PROC'
                known = fn_proc_names.get(prefix, frozenset())
                name_len = _matchFnProcName(text, i, length, known)
                for _ in range(name_len):
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
            in_variable = False
            break

        if matched:
            continue

        # No keyword matched - emit the character as a literal byte.
        # Letters and underscores enter variable-name mode; everything
        # else exits it.
        if ch.isalpha() or ch == '_':
            at_start = False
            in_variable = True
        else:
            in_variable = False

        result.append(ord(ch))
        i += 1

    return bytes(result)


def tokenize(
    lines: List[str],
    on_overflow: Optional[Callable[[str, str], str]] = None,
) -> bytes:
    """Convert LIST-style text lines to tokenized BBC BASIC II binary.

    Accepts the output of detokenize() and produces bytes suitable for
    writing to a DFS disc image. Blank lines and lines containing only
    whitespace are silently skipped.

    Args:
        lines:       List of strings in LIST format, each starting with a
                     line number (optionally preceded by whitespace).
        on_overflow: Optional callback invoked when a tokenized line exceeds
                     255 bytes. Receives (line_text, error_message) and
                     returns a replacement line to retry. If the replacement
                     still overflows, ValueError is raised.

    Returns:
        Tokenized BBC BASIC II program as bytes, including the
        end-of-program marker (0x0D 0xFF).

    Raises:
        ValueError: If a non-blank line cannot be parsed, or if a line
            still exceeds 255 bytes after the on_overflow callback.
    """
    result = bytearray()

    # Pass 1: collect DEF FN/PROC names so pass 2 can determine where
    # each FN/PROC identifier ends in ambiguous cases like FNldTHEN.
    fn_proc_names = _collectFnProcNames(lines)

    # Pass 2: tokenize each line using the symbol table.
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        linenum, content_text = _parseLine(line)
        content = _tokenizeContent(content_text, fn_proc_names)

        hi = (linenum >> 8) & 0xFF
        lo = linenum & 0xFF

        # Length byte counts from the leading 0x0D through the content:
        # leading_0x0D + hi + lo + len_byte + content = 4 + len(content).
        # This is the Russell format used by BBC BASIC II on the 6502.
        linelen = 4 + len(content)

        # The length byte is a single unsigned byte (max 255).  If the
        # tokenized content exceeds that limit the program would be corrupt.
        if linelen > 255:
            msg = (f"Line {linenum} tokenizes to {linelen} bytes "
                   f"(max 255)")

            # Give the caller a chance to compact and retry.
            if on_overflow is not None:
                replacement = on_overflow(line, msg)
                _, content_text = _parseLine(replacement)
                content = _tokenizeContent(content_text, fn_proc_names)
                linelen = 4 + len(content)

            if linelen > 255:
                raise ValueError(msg)

        result.append(0x0D)
        result.append(hi)
        result.append(lo)
        result.append(linelen)
        result.extend(content)

    # End-of-program marker.
    result.append(0x0D)
    result.append(0xFF)

    return bytes(result)


# =====================================================================
# Content classification
# =====================================================================

def looksLikeTokenizedBasic(data: bytes) -> bool:
    """True if data contains a structurally valid Wilson/Acorn BASIC program.

    Walks the tokenized line structure looking for the 0x0D 0xFF
    end-of-program marker that every valid BBC BASIC program contains.

    A first-byte-only check is not sufficient because plain-text files
    beginning with CR (0x0D) would false-positive.

    See https://www.bbcbasic.net/wiki/doku.php?id=format for the
    canonical format-detection algorithm.
    """
    if len(data) < 2 or data[0] != 0x0D:
        return False

    # Walk the line structure and check for the 0xFF end marker.
    prog_size = basicProgramSize(data)
    return prog_size >= 2 and data[prog_size - 1] == 0xFF


# Bytes acceptable in a plain-text file: printable ASCII plus common
# whitespace (tab, carriage return, line feed).
_PLAIN_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def looksLikePlainText(data: bytes) -> bool:
    """True if every byte is printable ASCII or common whitespace.

    Checks for printable ASCII (0x20-0x7E) plus tab (0x09), line feed
    (0x0A), and carriage return (0x0D). An empty file is not plain text.
    """
    if not data:
        return False
    return all(b in _PLAIN_TEXT_BYTES for b in data)


def classifyFileType(entry: DiscEntry, data: bytes) -> str:
    """Classify a disc file by inspecting its metadata and content.

    Returns one of:
        "BASIC"    - pure tokenized BASIC program
        "BASIC+MC" - BASIC program with appended machine code
        "BASIC?"   - has BASIC exec address but data is not tokenized
        "TEXT"     - plain ASCII text file
        "binary"   - everything else

    Args:
        entry: Catalogue entry with metadata (isBasic, isDirectory, etc.).
        data:  Raw file content bytes.

    Returns:
        Classification string.
    """
    # Check for tokenized BASIC (by exec address or content).
    if entry.isBasic:
        if looksLikeTokenizedBasic(data):
            prog_size = basicProgramSize(data)
            if prog_size < len(data) - 16:
                return "BASIC+MC"
            return "BASIC"
        return "BASIC?"

    # Content-based detection for files without a BASIC exec address.
    if looksLikeTokenizedBasic(data):
        prog_size = basicProgramSize(data)
        if prog_size < len(data) - 16:
            return "BASIC+MC"
        return "BASIC?"

    # Plain text detection.
    if looksLikePlainText(data):
        return "TEXT"

    return "binary"


# =====================================================================
# Text escaping for non-ASCII round-tripping
# =====================================================================

# Regex matching a \xHH escape sequence (two uppercase hex digits).
_ESCAPE_RE = re.compile(r"\\x([0-9A-F]{2})")


def hasEscapes(text: str) -> bool:
    """Return True if text contains any \\xHH escape sequence.

    Used by callers that want to decide whether to run unescapeNonAscii()
    on a block of text without scanning it twice.
    """
    return bool(_ESCAPE_RE.search(text))


def escapeNonAscii(line: str) -> str:
    """Replace non-printable-ASCII characters with \\xHH escapes.

    Characters outside the printable ASCII range 0x20-0x7E (e.g. BBC Micro
    teletext control codes embedded in PRINT strings) are replaced with a
    two-digit hex escape.  A literal backslash followed by 'x' is escaped
    as \\x5Cx to avoid ambiguity on the reverse trip.

    This is the forward half of a lossless round-trip.  Use unescapeNonAscii()
    to reverse.
    """
    out: List[str] = []

    for ch in line:
        code = ord(ch)
        if code == 0x5C:
            # Always escape backslash so the reverse is unambiguous.
            out.append("\\x5C")
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        else:
            out.append(f"\\x{code:02X}")

    return "".join(out)


def unescapeNonAscii(line: str) -> str:
    """Reverse escapeNonAscii - convert \\xHH sequences back to characters."""
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), line)
