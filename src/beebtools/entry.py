# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Shared contracts for disc image entries and operations.

Defines the structural typing Protocol that both DFSEntry and ADFSEntry
satisfy, plus the common exception hierarchy and the DiscFile transport
object used by addFile().

This is a Layer 0 module - no internal imports.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# -----------------------------------------------------------------------
# Entry Protocol
# -----------------------------------------------------------------------

@runtime_checkable
class DiscEntry(Protocol):
    """Structural Protocol for disc catalogue entries.

    Both DFSEntry and ADFSEntry satisfy this Protocol without
    inheriting from it. Upper layers (disc.py, cli.py) can type-hint
    against DiscEntry to work uniformly with either format.
    """

    name: str
    load_addr: int
    exec_addr: int
    length: int
    locked: bool

    @property
    def fullName(self) -> str: ...

    @property
    def isBasic(self) -> bool: ...

    @property
    def isDirectory(self) -> bool: ...


# -----------------------------------------------------------------------
# File data transport
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class DiscFile:
    """Transport object for file content and metadata.

    Bundles everything needed to add a file to a disc image into a
    single value. Format engines extract what they need from path -
    DFS splits it into directory + name, ADFS uses it directly.
    """

    path: str
    data: bytes
    load_addr: int = 0
    exec_addr: int = 0
    locked: bool = False


# -----------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------

class DiscError(Exception):
    """Base exception for all beebtools disc operations."""


class DiscFormatError(DiscError):
    """Raised when disc image data is corrupt or unreadable."""


# -----------------------------------------------------------------------
# Shared utilities
# -----------------------------------------------------------------------

def isBasicExecAddr(exec_addr: int) -> bool:
    """True if the execution address matches a BBC BASIC entry point.

    Checks for 0x801F, 0x8023, and 0x802B with the top two address
    bits masked off (I/O processor flag).
    """
    return (exec_addr & 0xFFFF) in (0x801F, 0x8023, 0x802B)
