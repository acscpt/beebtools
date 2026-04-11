# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Parse and format .inf sidecar files.

The .inf format is the standard BBC Micro community interchange format
for preserving DFS and ADFS file metadata alongside extracted data
files. Each data file has a companion text file whose name is the data
file's PC name with `.inf` (or `.INF`) appended, holding the original
Acorn name and attributes.

This module implements the stardot `.inf` format spec as of early 2026
(https://github.com/stardot/inf_format). Key features:

- Quoted string fields with RFC 3986 percent-encoding so names can
  carry any byte value, including space, DQUOTE, `%`, or control bytes.
- Syntax 1 (name load exec length access [extra_info]), syntax 2
  (name load exec [dfs_access] [extra_info], TubeHost/BeebLink form),
  and syntax 3 (name access [extra_info], ADFS Explorer directory
  form) are all accepted.
- 8-digit hex addresses are preferred, 6-digit are accepted and
  sign-extended when they begin with `FF`.
- Arbitrary `KEY=value` extra_info fields are preserved on read and
  round-tripped on write (including the common `CRC`, `CRC32`, `OPT`,
  `OPT4`, `TITLE`, and `DATETIME` keys).

The module is a pure text transform with no file I/O and no dependency
on the disc image reader. The orchestration layer (`disc.py`) handles
reading and writing the actual `.inf` files on disc.
"""

import warnings as _warnings
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# -----------------------------------------------------------------------
# Extra-info keys
# -----------------------------------------------------------------------

# beebtools proposes ``START_SECTOR`` as an .inf extra-info key that
# records the on-disc start sector for a file, so that a rebuild can
# place the file at its original location instead of running
# free-space allocation. This is what makes byte-exact rebuilds of
# copy-protected discs (Level 9 games) possible when their catalogue
# entries legitimately declare overlapping sector ranges. Until the
# convention sees wider adoption the library only ever WRITES the
# HTTP-style experimental form ``X_START_SECTOR``; it reads both and
# prefers the bare form when both appear. Drop the X- prefix on the
# write path (``INF_X_START_SECTOR = INF_START_SECTOR``) once the
# experimental status lifts.
INF_START_SECTOR = "START_SECTOR"
INF_X_START_SECTOR = "X_" + INF_START_SECTOR


# -----------------------------------------------------------------------
# Data class
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class InfData:
    """Parsed .inf sidecar file metadata.

    Fields mirror the DFS and ADFS catalogue entry attributes so that
    callers can easily map between InfData and their native entry
    types without coupling this module to the disc image readers.

    Names are stored as ``str`` where each code point is a byte value
    (latin-1 semantics). This means a name like ``BLANK\\x06`` carries
    a literal U+0006 in the string and ``nameBytes`` returns the
    original 6-byte sequence unchanged. This is the only way to
    represent arbitrary Acorn bytes as a Python str without losing
    information, and it matches how ``DFSEntry.name`` is already
    stored in the rest of the codebase.
    """

    directory: str
    name: str
    load_addr: int
    exec_addr: int
    length: Optional[int] = None
    locked: bool = False
    extra_info: Dict[str, str] = field(default_factory=dict)

    @property
    def fullName(self) -> str:
        """Full Acorn path with directory prefix, e.g. '$.BOOT' or 'T.MYPROG'.

        For DFS this is ``{dir}.{name}`` with a single-character
        directory. For ADFS it is the same concatenation, with the
        directory holding the dotted parent path (``$`` or
        ``$.GAMES.ACTION``) and the name being the leaf.
        """

        return f"{self.directory}.{self.name}"

    @property
    def nameBytes(self) -> bytes:
        """Return the original Acorn name as raw bytes (latin-1 round-trip)."""

        return self.name.encode("latin-1")

    @property
    def directoryBytes(self) -> bytes:
        """Return the original Acorn directory as raw bytes."""

        return self.directory.encode("latin-1")

    @property
    def access(self) -> int:
        """Return the 8-bit access byte form of the attributes.

        Bit 3 (0x08) is set when ``locked`` is true. Other access bits
        are not tracked on the DFS side of the codebase yet; if callers
        need ADFS R/W/E bits they should supply them via ``extra_info``
        or extend this dataclass in a later slice.
        """

        return 0x08 if self.locked else 0x00

    @property
    def crc(self) -> Optional[int]:
        """Return the 16-bit CRC from the extra_info dict if present."""

        value = self.extra_info.get("CRC")
        if value is None:
            return None

        try:
            return int(value, 16)
        except ValueError:
            return None

    @property
    def startSector(self) -> Optional[int]:
        """Return the on-disc start sector recorded in extra_info, if any.

        Reads the experimental ``X_START_SECTOR`` field, or the bare
        ``START_SECTOR`` field if the convention has graduated. The
        plain form is preferred when both are present but that is an
        anomaly - in practice only one should ever appear - so both
        keys showing up at once also emits a warning. An unparseable
        or negative value emits a ``UserWarning`` via ``warnings.warn``
        and returns None, so callers still fall back to normal
        free-space allocation but the data problem is visible to any
        observer that has not silenced warnings.
        """

        has_plain = INF_START_SECTOR in self.extra_info
        has_x = INF_X_START_SECTOR in self.extra_info

        if has_plain and has_x:
            _warnings.warn(
                f".inf sidecar for {self.fullName!r} contains both "
                f"{INF_START_SECTOR}="
                f"{self.extra_info[INF_START_SECTOR]!r} and "
                f"{INF_X_START_SECTOR}="
                f"{self.extra_info[INF_X_START_SECTOR]!r}; "
                f"only one should be present, using the plain form",
                stacklevel=2,
            )

        if has_plain:
            key = INF_START_SECTOR
        elif has_x:
            key = INF_X_START_SECTOR
        else:
            return None

        value = self.extra_info[key]

        try:
            parsed = int(value, 0)
        except ValueError:
            _warnings.warn(
                f".inf {key}={value!r} for {self.fullName!r} is not a "
                f"valid integer, ignoring and falling back to "
                f"auto-allocation",
                stacklevel=2,
            )
            return None

        if parsed < 0:
            _warnings.warn(
                f".inf {key}={value!r} for {self.fullName!r} is "
                f"negative, ignoring and falling back to "
                f"auto-allocation",
                stacklevel=2,
            )
            return None

        return parsed


# -----------------------------------------------------------------------
# Tokenizer
# -----------------------------------------------------------------------

_WHITESPACE = (" ", "\t")
_EOL = ("\r", "\n")


def _decodePercentEscapes(raw: str) -> str:
    """Decode RFC 3986 ``%XX`` escapes in a string to byte-value chars.

    Each ``%XX`` sequence with two hex digits becomes the single code
    point whose value is the decoded byte. Any ``%`` that is not
    followed by two hex digits is left as a literal character, which
    matches the tokenizer's historical tolerant behaviour.
    """

    out: List[str] = []
    i = 0
    n = len(raw)

    while i < n:
        ch = raw[i]

        if ch == '%' and i + 2 < n:
            try:
                byte = int(raw[i + 1:i + 3], 16)
                out.append(chr(byte))
                i += 3
                continue
            except ValueError:
                pass

        out.append(ch)
        i += 1

    return "".join(out)


def _tokenize(line: str) -> List[Tuple[str, str]]:
    """Split a .inf line into tokens per the stardot spec syntax.

    Each result is a ``(decoded, raw)`` pair. For unquoted tokens,
    decoded and raw are identical. For quoted tokens, decoded is the
    final string after percent-decoding while raw is the original
    contents of the quoted region with ``%XX`` escapes intact.

    Preserving the raw form lets callers such as ``_splitDirAndName``
    identify the directory/leaf boundary in the name field: an
    unencoded ``.`` in the raw form is always a path separator, while
    a ``%2E`` escape marks a literal dot byte that belongs inside
    the filename itself.

    Parsing stops at the first end-of-line character (CR, LF, or CR LF).

    Raises:
        ValueError: If a quoted string has no closing DQUOTE before
            the end of the line.
    """

    tokens: List[Tuple[str, str]] = []
    i = 0
    n = len(line)

    while i < n:
        ch = line[i]

        if ch in _EOL:
            break

        if ch in _WHITESPACE:
            i += 1
            continue

        if ch == '"':
            i += 1
            raw_start = i
            closed = False

            while i < n:
                c = line[i]

                if c in _EOL:
                    break

                if c == '"':
                    closed = True
                    break

                i += 1

            if not closed:
                raise ValueError(
                    "Unterminated quoted string in .inf line"
                )

            raw = line[raw_start:i]
            i += 1
            tokens.append((_decodePercentEscapes(raw), raw))
            continue

        start = i

        while i < n and line[i] not in _WHITESPACE and line[i] not in _EOL:
            i += 1

        unquoted = line[start:i]
        tokens.append((unquoted, unquoted))

    return tokens


# -----------------------------------------------------------------------
# Field classification helpers
# -----------------------------------------------------------------------

_HEX_CHARS = set("0123456789abcdefABCDEF")
_ACCESS_CHARS = set("ELWRDelwrd")
_KEY_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)


def _isHexToken(token: str) -> bool:
    """True if every character in token is a hex digit."""

    if not token:
        return False

    return all(c in _HEX_CHARS for c in token)


def _isDfsAccessToken(token: str) -> bool:
    """True if token is a DFS-style access shorthand ('L', 'Locked', 'LOCKED')."""

    return token in ("L", "LOCKED", "Locked")


def _isAdfsAccessToken(token: str) -> bool:
    """True if token looks like an ADFS symbolic access string.

    The ADFS access field is a non-empty run of E/L/W/R/D characters
    in either case. It overlaps with hex in the letters E and D, so
    the caller must decide tie-breaks by position.
    """

    if not token:
        return False

    return all(c in _ACCESS_CHARS for c in token)


def _isExtraInfoToken(token: str) -> bool:
    """True if token is a KEY= or KEY=value extra-info pair."""

    if "=" not in token:
        return False

    key, _, _ = token.partition("=")

    if not key:
        return False

    return all(c in _KEY_CHARS for c in key)


def _parseAdfsAccess(token: str) -> int:
    """Translate an ADFS symbolic access token to an access byte."""

    result = 0

    for c in token:
        if c == "R":
            result |= 0x01
        elif c == "W":
            result |= 0x02
        elif c == "E":
            result |= 0x04
        elif c == "L":
            result |= 0x08
        elif c == "r":
            result |= 0x10
        elif c == "w":
            result |= 0x20
        elif c == "e":
            result |= 0x40
        elif c == "l":
            result |= 0x80

    return result


# -----------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------

def parseInf(line: str) -> InfData:
    """Parse a .inf sidecar line into file metadata.

    Accepts all three syntax forms from the stardot spec:

    - Syntax 1: ``name load exec length access [extra_info...]``
      with hex access byte. Any additional hex fields past position
      four are collected but not interpreted.
    - Syntax 2: ``name load exec [L] [extra_info...]``, the historical
      TubeHost/BeebLink form with an optional DFS-style `L` flag.
    - Syntax 3: ``name access [extra_info...]``, the ADFS Explorer
      directory form where the access field replaces the hex address
      fields entirely.

    6-digit load and exec addresses beginning with ``FF`` are
    sign-extended to 32 bits, matching BBC Micro convention where the
    top 16 bits are implicitly ``FFFF`` for addresses in ROM.

    The filename field may be quoted (``"..."``) with ``%XX``
    percent-encoding for any byte outside the unquoted-safe range, so
    names containing spaces, quotes, control bytes, or the literal
    ``%`` and ``"`` characters round-trip losslessly.

    Args:
        line: A single .inf line. Leading and trailing whitespace is
              tolerated; parsing stops at the first end-of-line byte.

    Returns:
        InfData holding the parsed fields. ``extra_info`` is a dict
        of every ``KEY=value`` pair found after the hex region.

    Raises:
        ValueError: If the line is empty, has no name field, contains
            an unterminated quoted string, or is otherwise unparseable.
    """

    tokens = _tokenize(line)

    if not tokens:
        raise ValueError("Empty .inf line")

    idx = 0

    # Deprecated TAPE prefix: skip and treat the next token as the name.
    # Note this only applies when the token is the literal bareword
    # TAPE, not a quoted name that happens to be TAPE.
    if tokens[idx][0] == "TAPE":
        idx += 1

        if idx >= len(tokens):
            raise ValueError(
                "TAPE prefix in .inf line with no following name"
            )

    if idx >= len(tokens):
        raise ValueError("No name field in .inf line")

    # -- Name field. --
    name_decoded, name_raw = tokens[idx]
    idx += 1

    # Split at the last unescaped dot in the raw form, so dots that
    # were percent-encoded inside a quoted string stay inside the
    # filename rather than being treated as path separators.
    directory, name = _splitDirAndName(name_decoded, name_raw)

    # -- Walk remaining tokens and classify them greedily. --
    hex_fields: List[int] = []
    locked = False
    extra_info: Dict[str, str] = {}
    syntax3_access_seen = False

    while idx < len(tokens):
        token = tokens[idx][0]

        # Stop entirely at the deprecated NEXT tape marker.
        if token == "NEXT":
            break

        # Extra info: KEY=value or KEY= (empty value). These end the
        # hex region by the spec's rule that neither hex nor access
        # can contain an '='.
        if _isExtraInfoToken(token):
            key, _, value = token.partition("=")
            extra_info[key] = value
            idx += 1

            # After an extra info field, everything else must be
            # extra info or NEXT. Continue the loop with no hex
            # classification.
            continue

        # Deprecated DFS CRC= with a single space after the =.
        # A single "CRC=" token is handled above; the deprecated form
        # shows up as two tokens: "CRC=" and a hex field. We only
        # honour this when the "CRC=" key has no value attached.
        if "CRC=" in extra_info and extra_info.get("CRC") == "":
            extra_info["CRC"] = token
            idx += 1
            continue

        # DFS access shorthand: 'L', 'Locked', 'LOCKED'. Sets the
        # locked flag without consuming a hex slot.
        if _isDfsAccessToken(token):
            locked = True
            idx += 1
            continue

        # Hex field: plain hex digits. This is the common path.
        if _isHexToken(token):
            hex_fields.append(int(token, 16))
            idx += 1
            continue

        # Syntax 3 (ADFS Explorer directory): the first non-name
        # token is a symbolic access string instead of a hex field.
        # This only applies when no hex fields have been collected
        # yet and the token is a valid ADFS access string.
        if not hex_fields and _isAdfsAccessToken(token):
            access_byte = _parseAdfsAccess(token)
            locked = bool(access_byte & 0x08)
            syntax3_access_seen = True
            idx += 1
            continue

        # Unknown token type. Stop parsing defensively rather than
        # silently misinterpreting it.
        break

    # -- Extract addresses, length, and access byte from hex fields. --
    load_addr = hex_fields[0] if len(hex_fields) > 0 else 0
    exec_addr = hex_fields[1] if len(hex_fields) > 1 else 0
    length: Optional[int] = hex_fields[2] if len(hex_fields) > 2 else None

    if len(hex_fields) > 3:
        access_byte = hex_fields[3]

        if access_byte & 0x08:
            locked = True

    # Syntax 3 discards addresses entirely. Mark length as None so the
    # formatter re-emits it as syntax 3 if it wants to preserve shape.
    if syntax3_access_seen and not hex_fields:
        length = None

    # -- Sign-extend 6-digit load/exec values beginning with FF. --
    load_addr = _signExtend6Digit(load_addr)
    exec_addr = _signExtend6Digit(exec_addr)

    return InfData(
        directory=directory,
        name=name,
        load_addr=load_addr,
        exec_addr=exec_addr,
        length=length,
        locked=locked,
        extra_info=extra_info,
    )


def _splitDirAndName(decoded: str, raw: str) -> Tuple[str, str]:
    """Split a raw name token into (directory, leaf_name).

    The ``raw`` form is the tokenized string with ``%XX`` escapes
    still intact (quoted tokens) or the unquoted literal (unquoted
    tokens). Dots in the raw form that are not part of a ``%2E``
    escape are true path separators; dots that were percent-encoded
    stay inside the filename.

    DFS paths have the form ``D.NAME`` where D is a single byte and
    NAME may itself contain dots (which a ROM-faithful writer
    percent-encodes so the separator is unambiguous). ADFS paths
    take the form ``$.GAMES.ACTION.ELITE`` where the leaf is the
    final dot-separated segment.

    Disambiguation rules:

    - If the raw form has no path-separator dots, the whole token is
      the leaf name under the default ``$`` root.
    - If the raw form has exactly one separator at position 1, we
      treat it as a DFS single-character directory prefix.
    - If the raw form has multiple separator dots, we rsplit at the
      final separator so the directory prefix carries the full ADFS
      parent path.

    Both halves are then percent-decoded before returning.
    """

    dot_positions = _unescapedDotPositions(raw)

    if not dot_positions:
        return "$", decoded

    if len(dot_positions) == 1 and dot_positions[0] == 1:
        # DFS single-char directory prefix: "X.rest".
        return raw[0], _decodePercentEscapes(raw[2:])

    if len(dot_positions) == 1:
        # Single separator but not at position 1: treat as a shallow
        # ADFS path such as "GAMES.ELITE".
        split = dot_positions[0]
        return (
            _decodePercentEscapes(raw[:split]),
            _decodePercentEscapes(raw[split + 1:]),
        )

    # Multi-dot ADFS path: split at the last separator.
    split = dot_positions[-1]
    return (
        _decodePercentEscapes(raw[:split]),
        _decodePercentEscapes(raw[split + 1:]),
    )


def _unescapedDotPositions(raw: str) -> List[int]:
    """Return every index in raw holding a literal '.' byte.

    A ``.`` appearing directly in raw is a separator dot. A ``.``
    reached via a ``%2E`` (or ``%2e``) escape is a literal dot byte
    that belongs inside a filename and is skipped.
    """

    positions: List[int] = []
    i = 0
    n = len(raw)

    while i < n:
        ch = raw[i]

        if ch == '%' and i + 2 < n:
            # Skip over the escape; it decodes to a single byte and
            # cannot be a separator regardless of what byte it yields.
            try:
                int(raw[i + 1:i + 3], 16)
                i += 3
                continue
            except ValueError:
                pass

        if ch == '.':
            positions.append(i)

        i += 1

    return positions


def _signExtend6Digit(value: int) -> int:
    """Sign-extend 24-bit FFxxxx values to 32 bits.

    BBC Micro convention: addresses in the top 64K of the 6502 memory
    map (0xFFxxxx) are often serialised as 6-digit hex strings by old
    tools. The stardot spec says consumers MAY detect this and
    sign-extend to 32 bits by setting the top two bytes to 0xFF when
    the input's top byte is 0xFF and the value fits in 24 bits. This
    function implements that rule.
    """

    if value < 0x1000000 and (value >> 16) == 0xFF:
        return 0xFF000000 | value

    return value


# -----------------------------------------------------------------------
# Formatter
# -----------------------------------------------------------------------

# Characters that can appear unquoted in a string field per spec:
# the unquoted form excludes whitespace (used as the field separator),
# DQUOTE (reserved for the quoted form), and '%' (reserved for the
# percent-escape in quoted form). Both the start and continuation
# sets are %x21 / %x23-24 / %x26-7E, i.e. printable ASCII minus
# space, DQUOTE, and percent.
_UNQUOTED = (set(range(0x21, 0x7F))
             - {ord('"'), ord('%')})
_UNQUOTED_START = _UNQUOTED
_UNQUOTED_CONT = _UNQUOTED


def _needsQuoting(raw: str) -> bool:
    """True if the given string cannot be safely emitted unquoted."""

    if not raw:
        return True

    first = ord(raw[0])

    if first not in _UNQUOTED_START:
        return True

    for ch in raw[1:]:
        if ord(ch) not in _UNQUOTED_CONT:
            return True

    # Percent requires quoting because unquoted strings don't support
    # percent-encoding, and an unencoded '%' in the output would be
    # ambiguous with an escape sequence if a consumer later chose to
    # interpret it.
    if "%" in raw:
        return True

    return False


def _percentEncode(raw: str, always_encode: str = "") -> str:
    """Encode a string for inclusion inside a quoted .inf string field.

    Per the stardot spec, quoted strings support RFC 3986 percent-
    encoding. Producers MUST encode `"` (%22), `%` (%25), and any byte
    outside the 7-bit printable range. Other characters may be encoded
    verbatim or as %XX.

    Args:
        raw:            The string to encode, with code points
                        treated as latin-1 byte values.
        always_encode:  A string of characters that must always be
                        percent-encoded even when otherwise safe.

    Returns:
        The encoded string ready to be wrapped in DQUOTE.
    """

    parts: List[str] = []
    must_encode = set(always_encode) | {'"', '%'}

    for ch in raw:
        code = ord(ch)

        if ch in must_encode or code < 0x20 or code > 0x7E:
            parts.append(f"%{code:02X}")
        else:
            parts.append(ch)

    return "".join(parts)


def _formatNameField(directory: str, name: str) -> str:
    """Format the directory.name field, quoting and encoding as needed.

    The leaf name is always encoded with ``.`` as a forced escape so
    that DFS filenames containing literal dots (e.g. ``B1.1``) can be
    disambiguated from ADFS nested path separators on the way back in.
    If either half needs quoting at all, or the name contains a dot,
    the whole field is wrapped in DQUOTE.
    """

    name_quoted = _percentEncode(name, always_encode=".")
    dir_quoted = _percentEncode(directory)

    # The field must be quoted when any half would not be safely
    # representable as a bareword. A bareword name portion with dots
    # would re-introduce the ambiguity we just escaped away.
    needs_quoting = (
        _needsQuoting(directory)
        or _needsQuoting(name)
        or "." in name
    )

    if needs_quoting:
        return f'"{dir_quoted}.{name_quoted}"'

    return f"{directory}.{name}"


def formatInf(
    directory: str,
    name: str,
    load_addr: int,
    exec_addr: int,
    length: int,
    locked: bool = False,
    extra_info: Optional[Dict[str, str]] = None,
) -> str:
    """Format file metadata as a .inf sidecar line.

    Emits the stardot spec's preferred syntax 1 form:

        NAME  LLLLLLLL EEEEEEEE SSSSSSSS AA [KEY=value ...]

    where ``LLLLLLLL`` and ``EEEEEEEE`` are 8-digit hex load and exec
    addresses, ``SSSSSSSS`` is the 8-digit length, and ``AA`` is the
    2-digit hex access byte (bit 3 set when ``locked`` is true).

    If the name contains any byte that is not safe to emit unquoted -
    including space, DQUOTE, ``%``, or any byte outside the range
    0x21-0x7E - the name is wrapped in DQUOTE and the spec-forbidden
    bytes are percent-encoded.

    Extra info fields are emitted verbatim after the access byte.
    Values with special characters should be passed pre-quoted by the
    caller, since this function doesn't know which extra info fields
    need quoting and which are hex-safe.

    Args:
        directory:  Directory prefix: a single DFS char, or a dotted
                    ADFS path such as ``$.GAMES``.
        name:       Leaf filename (str, latin-1 byte semantics).
        load_addr:  32-bit load address.
        exec_addr:  32-bit execution address.
        length:     File length in bytes.
        locked:     True to set access bit 3 (not deletable by you).
        extra_info: Optional KEY=value dict emitted after access byte.

    Returns:
        Formatted .inf line (no trailing newline).
    """

    name_field = _formatNameField(directory, name)
    access_byte = 0x08 if locked else 0x00

    parts = [
        name_field,
        f"{load_addr & 0xFFFFFFFF:08X}",
        f"{exec_addr & 0xFFFFFFFF:08X}",
        f"{length & 0xFFFFFFFF:08X}",
        f"{access_byte:02X}",
    ]

    if extra_info:
        for key, value in extra_info.items():
            parts.append(f"{key}={value}")

    return " ".join(parts)
