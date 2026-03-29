# SPDX-FileCopyrightText: 2026 beebtools contributors
# SPDX-License-Identifier: MIT

"""BBC BASIC pretty-printer.

Post-processes detokenized BASIC text lines, adding operator spacing and
handling copy-protection anti-listing traps.
"""

import re


def prettyPrint(lines):
    """Apply readable formatting to detokenized BASIC lines.

    This is a post-processing pass on plain text.  String literals and
    REM/DATA tails are passed through verbatim; everything else receives:

    - A space between the line number and the first token
    - Spaces around comparison and assignment operators (= < > <> <= >=)
    - Spaces around arithmetic operators (+ - * /)
    - Padding around colon statement separators ( : )
    - A trailing space after each comma
    - Star commands (*COMMAND) passed through verbatim - no spacing added
    - *| anti-listing traps converted to REM *| (control characters stripped)

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

        # Convert *| MOS comment syntax to REM, stripping any control characters
        # (e.g. VDU 21 bytes inserted as an anti-listing trap).
        stripped = code.lstrip()
        if stripped.startswith('*|'):
            rest = stripped[2:]
            rest = ''.join(c for c in rest if ord(c) >= 32)
            code = ' REM *|' + rest

        # Ensure exactly one space between line number and first token.
        if code and not code[0].isspace():
            code = ' ' + code

        result.append(num_part + _prettyCode(code))
    return result


def _prettyCode(code):
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


def _ensureSpace(buf):
    """Trim trailing spaces from buf then append exactly one space."""
    while buf and buf[-1] == ' ':
        buf.pop()
    buf.append(' ')
