# SPDX-FileCopyrightText: 2026 beebtools contributors
# SPDX-License-Identifier: MIT

"""BBC BASIC II detokenizer.

Converts raw tokenized BASIC program bytes into LIST-style plain text,
one string per program line.
"""

from .tokens import TOKENS, LINE_LITERAL_TOKENS


def decodeLineRef(b0, b1, b2):
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


def detokenize(data):
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

        # Content runs from the byte after the header to the end of the record.
        # The length byte counts from the hi byte to where the next 0x0D starts.
        content = data[pos + 3 : pos - 1 + linelen]
        pos = pos - 1 + linelen

        text = _decodeLineContent(content)
        lines.append(f"{linenum:>5d}{text}")

    return lines


def _decodeLineContent(content):
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

        # End-of-line marker embedded in content.
        if b == 0x0D:
            break

        # Plain ASCII character.
        parts.append(chr(b))
        i += 1

    return "".join(parts)
