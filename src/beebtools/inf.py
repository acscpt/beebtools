# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Parse and format .inf sidecar files.

The .inf format is the standard BBC Micro community interchange format
for preserving DFS file metadata alongside extracted data files. Each
data file has a companion .inf text file containing the DFS directory,
filename, load address, execution address, file length, and an optional
lock flag.

Format:
    DIR.NAME  LLLLLL EEEEEE SSSSSS [L] [CRC=XXXX]

    DIR      -- single-character DFS directory (e.g. '$', 'T')
    NAME     -- DFS filename, up to 7 characters
    LLLLLL   -- load address in hexadecimal (6 or 8 digits)
    EEEEEE   -- execution address in hexadecimal
    SSSSSS   -- file length in hexadecimal
    L        -- optional lock flag
    CRC=XXXX -- optional 16-bit CRC (parsed but not generated)

This module is a pure transform layer with no file I/O and no dependency
on the disc image reader. The orchestration layer (disc.py) handles
reading and writing the actual .inf files on disc.
"""

from dataclasses import dataclass
from typing import Optional


# -----------------------------------------------------------------------
# Data class
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class InfData:
    """Parsed .inf sidecar file metadata.

    Fields mirror the DFS catalogue entry attributes so that callers can
    easily map between InfData and DFSEntry without coupling this module
    to the disc image reader.
    """

    directory: str
    name: str
    load_addr: int
    exec_addr: int
    length: int
    locked: bool = False
    crc: Optional[int] = None

    @property
    def fullName(self) -> str:
        """Full DFS filename with directory prefix, e.g. 'T.MYPROG'."""
        return f"{self.directory}.{self.name}"


# -----------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------

def parseInf(line: str) -> InfData:
    """Parse a .inf sidecar line into file metadata.

    Accepts the standard community format used by tools such as *SRAM,
    XFER, and BeebEm. Whitespace between fields is flexible (any amount
    of spaces or tabs).

    The filename field may be in DIR.NAME format (e.g. '$.BOOT') or a
    bare name with no directory prefix, which defaults to the '$'
    directory.

    Args:
        line: Single .inf line (leading/trailing whitespace is stripped).

    Returns:
        InfData with all parsed fields.

    Raises:
        ValueError: If the line has fewer than 4 whitespace-separated
            fields or contains non-hex values in address/length positions.
    """
    tokens = line.split()

    if len(tokens) < 4:
        raise ValueError(
            f"Invalid .inf line: expected at least 4 fields, got "
            f"{len(tokens)}: {line!r}"
        )

    # -- Filename: either "D.NAME" or bare "NAME" (defaults to $). --
    raw_name = tokens[0]

    if len(raw_name) >= 3 and raw_name[1] == ".":
        directory = raw_name[0]
        name = raw_name[2:]
    else:
        directory = "$"
        name = raw_name

    # -- Hex addresses and length. --
    load_addr = int(tokens[1], 16)
    exec_addr = int(tokens[2], 16)
    length = int(tokens[3], 16)

    # -- Scan remaining tokens for optional lock flag and CRC. --
    locked = False
    crc = None

    for token in tokens[4:]:
        upper = token.upper()

        if upper == "L":
            locked = True
        elif upper.startswith("CRC="):
            crc = int(upper[4:], 16)

    return InfData(
        directory=directory,
        name=name,
        load_addr=load_addr,
        exec_addr=exec_addr,
        length=length,
        locked=locked,
        crc=crc,
    )


# -----------------------------------------------------------------------
# Formatter
# -----------------------------------------------------------------------

def formatInf(
    directory: str,
    name: str,
    load_addr: int,
    exec_addr: int,
    length: int,
    locked: bool = False,
) -> str:
    """Format file metadata as a .inf sidecar line.

    Produces the standard community format with 6-digit uppercase hex
    values. DFS addresses are 18-bit (max 0x3FFFF), so 6 hex digits
    are sufficient for all valid DFS entries.

    Args:
        directory: Single-character DFS directory (e.g. '$', 'T').
        name:      DFS filename, up to 7 characters.
        load_addr: Load address.
        exec_addr: Execution address.
        length:    File length in bytes.
        locked:    True to append the lock flag.

    Returns:
        Formatted .inf line, e.g. '$.BOOT  FF1900 FF8023 000A00'.
    """
    line = (
        f"{directory}.{name}  "
        f"{load_addr:06X} {exec_addr:06X} {length:06X}"
    )

    if locked:
        line += " L"

    return line
