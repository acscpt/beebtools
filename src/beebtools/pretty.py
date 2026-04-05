# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC pretty-printer.

Post-processes detokenized BASIC text lines, adding operator spacing and
handling copy-protection anti-listing traps.
"""

import re
from typing import List


def prettyPrint(lines: List[str]) -> List[str]:
    """Apply readable formatting to detokenized BASIC lines.

    This is a post-processing pass on plain text.  String literals and
    REM/DATA tails are passed through verbatim; everything else receives:

    - A space between the line number and the first token
    - Spaces around comparison and assignment operators (= < > <> <= >=)
    - Spaces around arithmetic operators (+ - * /)
    - Padding around colon statement separators ( : )
    - A trailing space after each comma
    - Star commands (*COMMAND) passed through verbatim - no spacing added
    - *| anti-listing traps preserved as *| (control characters kept intact
      for the text-encoding layer to handle via the chosen text mode)

    Args:
        lines: List of detokenized BASIC line strings.

    Returns:
        List of prettified line strings.
    """
    result = []
    for line in lines:
        # Detokenized lines start with a right-justified line number.
        # re.DOTALL so that .* captures control characters including \n.
        m = re.match(r'^(\s*\d+)(.*)', line, re.DOTALL)
        if not m:
            result.append(line)
            continue

        num_part = m.group(1)
        code = m.group(2)

        # Anti-listing trap: keep *| as a MOS comment (not REM) so the
        # tokenizer reproduces the original star-command bytes.  Control
        # characters (e.g. VDU 21) are preserved; the text-encoding layer
        # handles display via the chosen text mode (ascii/utf8/escape).
        # Skip _prettyCode since the tail is literal, not BASIC syntax.
        stripped = code.lstrip()
        if stripped.startswith('*|'):
            result.append(num_part + ' ' + stripped)
            continue

        # Ensure exactly one space between line number and first token.
        if code and not code[0].isspace():
            code = ' ' + code

        result.append(num_part + _prettyCode(code))
    return result


def _prettyCode(code: str) -> str:
    """Format the code portion of one BASIC line.

    Walks character by character so quoted strings and REM/DATA tails are
    passed through verbatim.  Outside those regions, spaces are normalised
    around operators and punctuation.

    Args:
        code: The code portion of a detokenized BASIC line (no line number).

    Returns:
        Formatted code string.
    """
    buf = []
    i = 0
    n = len(code)
    in_string = False
    literal_rest = False

    while i < n:
        ch = code[i]

        # Inside a quoted string - pass through verbatim until closing quote.
        if in_string:
            buf.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue

        # After REM or DATA - pass the rest of the line through unchanged.
        if literal_rest:
            buf.append(ch)
            i += 1
            continue

        # Opening quote - switch to string mode.
        if ch == '"':
            in_string = True
            buf.append(ch)
            i += 1
            continue

        # Detect REM or DATA keywords.  In BBC BASIC these are always tokenized,
        # so after detokenization these text sequences can never be part of a
        # longer identifier - we always treat them as the literal-tail markers.
        triggered = False
        for kw in ('REM', 'DATA'):
            kl = len(kw)
            if code[i:i + kl] == kw:
                buf.append(kw)
                i += kl
                literal_rest = True
                triggered = True
                break
        if triggered:
            continue

        # Two-character comparison operators - must be checked before single-char.
        two = code[i:i + 2]
        if two in ('<>', '<=', '>='):
            _ensureSpace(buf)
            buf.append(two)
            buf.append(' ')
            i += 2
            while i < n and code[i] == ' ':
                i += 1
            continue

        # Single-character comparison and assignment operators.
        if ch in ('=', '<', '>'):
            _ensureSpace(buf)
            buf.append(ch)
            buf.append(' ')
            i += 1
            while i < n and code[i] == ' ':
                i += 1
            continue

        # Star command - * at the start of a statement passes the remainder of
        # the line verbatim to the MOS command interpreter.  Do not add spaces.
        if ch == '*':
            prev = ''.join(buf).rstrip()
            if not prev or prev[-1] == ':':
                buf.append('*')
                i += 1
                literal_rest = True
                continue

        # Arithmetic operators.  Treat +/- as unary when following ( , : or operator.
        if ch in ('+', '-', '*', '/'):
            prev = ''.join(buf).rstrip()
            is_unary = ch in ('+', '-') and (not prev or prev[-1] in '(,:+-*/=')
            if is_unary:
                buf.append(ch)
            else:
                _ensureSpace(buf)
                buf.append(ch)
                buf.append(' ')
                while i + 1 < n and code[i + 1] == ' ':
                    i += 1
            i += 1
            continue

        # Colon statement separator.
        if ch == ':':
            while buf and buf[-1] == ' ':
                buf.pop()
            buf.append(' : ')
            i += 1
            while i < n and code[i] == ' ':
                i += 1
            continue

        # Comma - normalise to exactly one following space, no leading space.
        if ch == ',':
            while buf and buf[-1] == ' ':
                buf.pop()
            buf.append(', ')
            i += 1
            while i < n and code[i] == ' ':
                i += 1
            continue

        buf.append(ch)
        i += 1

    # Collapse any double spaces introduced by adjacent padding operations.
    return re.sub(r'  +', ' ', ''.join(buf))


def _ensureSpace(buf: List[str]) -> None:
    """Trim trailing spaces from buf then append exactly one space."""
    while buf and buf[-1] == ' ':
        buf.pop()
    buf.append(' ')


# =====================================================================
# Compact line - inverse of prettyPrint spacing
# =====================================================================

def compactLine(line: str) -> str:
    """Strip cosmetic whitespace added by prettyPrint.

    Removes spaces that were introduced by operator/punctuation padding
    while preserving content inside string literals and after REM/DATA
    keywords.  A space is kept only when both its neighbours are word
    characters (letters, digits, or underscore), which prevents merging
    identifiers or keywords.

    Args:
        line: A single detokenized (possibly pretty-printed) BASIC line.

    Returns:
        The same line with cosmetic whitespace removed.
    """
    # Split the line number prefix from the code body.
    m = re.match(r'^(\s*\d+)\s*(.*)', line, re.DOTALL)
    if not m:
        return line

    num_part = m.group(1)
    code = m.group(2)

    return num_part + _compactCode(code)


def _compactCode(code: str) -> str:
    """Strip cosmetic spaces from the code portion of one BASIC line.

    Walks character by character, preserving quoted strings and
    REM/DATA tails verbatim.  In code regions, runs of spaces are
    dropped unless both the preceding and following characters are
    word characters (a-z, 0-9, underscore), which keeps spaces that
    prevent identifier or keyword merging.
    """
    buf: List[str] = []
    i = 0
    n = len(code)
    in_string = False
    literal_rest = False

    while i < n:
        ch = code[i]

        # Inside a quoted string - pass through verbatim.
        if in_string:
            buf.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue

        # After REM or DATA - pass the rest unchanged.
        if literal_rest:
            buf.append(ch)
            i += 1
            continue

        # Opening quote.
        if ch == '"':
            in_string = True
            buf.append(ch)
            i += 1
            continue

        # Detect REM or DATA keywords - everything after is literal.
        triggered = False
        for kw in ('REM', 'DATA'):
            kl = len(kw)
            if code[i:i + kl] == kw:
                buf.append(kw)
                i += kl
                literal_rest = True
                triggered = True
                break
        if triggered:
            continue

        # Space run - keep a single space only when both neighbours
        # are word characters, to prevent keyword/identifier merging.
        if ch == ' ':
            prev = buf[-1] if buf else ''
            j = i
            while j < n and code[j] == ' ':
                j += 1
            nxt = code[j] if j < n else ''
            if _isWordChar(prev) and _isWordChar(nxt):
                buf.append(' ')
            i = j
            continue

        buf.append(ch)
        i += 1

    return ''.join(buf)


def _isWordChar(ch: str) -> bool:
    """Return True if ch is a letter, digit, or underscore."""
    return ch.isalnum() or ch == '_'
