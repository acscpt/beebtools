# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Shared types for disc image entries, sides, and images.

Defines the hierarchy that every disc format engine builds on:

    DiscEntry     -- one catalogue entry (file or directory)
    DiscCatalogue -- a full disc catalogue
    DiscFile      -- file content and metadata transport object
    DiscSide      -- one side of a disc image (read and mutation API)
    DiscImage     -- a complete disc image container

This is a Contracts-layer module - no internal imports beyond boot.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, List, Tuple

from .boot import BootOption


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


# -----------------------------------------------------------------------
# DiscEntry ABC
# -----------------------------------------------------------------------

class DiscEntry(ABC):
    """One entry in a disc catalogue: a file or a directory.

    Carries the filename, parent directory, load and execution
    addresses, length in bytes, and lock flag. Exposes a full path,
    a BBC BASIC detection flag, a directory flag, and an
    os.fspath() hook for host-filesystem conversion.
    """

    name: str
    directory: str
    load_addr: int
    exec_addr: int
    length: int
    locked: bool

    @property
    @abstractmethod
    def fullName(self) -> str:
        """Return the full path (directory + name) for this entry."""

    @property
    @abstractmethod
    def isBasic(self) -> bool:
        """True if this entry looks like a BBC BASIC program."""

    @property
    @abstractmethod
    def isDirectory(self) -> bool:
        """True if this entry is a directory rather than a file."""

    @abstractmethod
    def __fspath__(self) -> str:
        """Return a host-filesystem-safe path for os.fspath() support."""


# -----------------------------------------------------------------------
# DiscCatalogue ABC
# -----------------------------------------------------------------------

class DiscCatalogue(ABC):
    """A full disc catalogue for one side of a disc.

    Carries the disc title, catalogue cycle number, boot option,
    total disc size in sectors, and the tuple of catalogue entries.
    Exposes a tracks count computed from the sector count.
    """

    title: str
    cycle: int
    boot_option: BootOption
    disc_size: int
    entries: Tuple[DiscEntry, ...]
    tracks: int


# -----------------------------------------------------------------------
# File data transport
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class DiscFile:
    """Transport object for file content and metadata.

    Bundles everything needed to add a file to a disc image into a
    single value. Format engines extract what they need from path:
    DFS splits it into directory and name, ADFS uses it directly.
    """

    path: str
    data: bytes
    load_addr: int = 0
    exec_addr: int = 0
    locked: bool = False


# -----------------------------------------------------------------------
# DiscSide ABC
# -----------------------------------------------------------------------

class DiscSide(ABC):
    """One side of a disc image.

    Provides catalogue read and write, file read and write, free
    space reporting, file add, delete, update, rename, subdirectory
    creation, and compaction. mkdir and compact raise DiscError by
    default; a subclass overrides them only when the operation is
    actually supported for the format.
    """

    @property
    @abstractmethod
    def side(self) -> int:
        """Side number (0 or 1) this reader represents."""

    @property
    @abstractmethod
    def maxTitleLength(self) -> int:
        """Maximum number of characters allowed in a disc title."""

    @abstractmethod
    def readCatalogue(self) -> DiscCatalogue:
        """Parse and return the disc catalogue for this side."""

    @abstractmethod
    def writeCatalogue(self, catalogue: DiscCatalogue) -> None:
        """Write a modified catalogue back to the disc image.

        Encodes catalogue fields and writes them to the appropriate
        sectors. Clears any cached catalogue so the next read re-parses.
        """

    @abstractmethod
    def readFile(self, entry: DiscEntry) -> bytes:
        """Read the contents of a file from disc."""

    @abstractmethod
    def writeFile(self, entry: DiscEntry, data: bytes) -> None:
        """Write file data to the sectors allocated for an entry."""

    @abstractmethod
    def freeSpace(self) -> int:
        """Return the amount of free space on this side."""

    @abstractmethod
    def addFile(self, spec: DiscFile) -> DiscEntry:
        """Add a file to this disc side.

        Returns the catalogue entry created for the new file.
        """

    @abstractmethod
    def deleteFile(self, path: str) -> None:
        """Remove a file from the catalogue by its full path."""

    @abstractmethod
    def updateEntry(self, path: str, updated: DiscEntry) -> None:
        """Replace a catalogue entry with an updated version."""

    @abstractmethod
    def renameFile(self, old_path: str, new_path: str) -> None:
        """Rename a file in the catalogue.

        Both paths must be fully qualified. The file data is not moved.
        """

    def mkdir(self, path: str) -> None:
        """Create a subdirectory at the given path.

        Default implementation raises DiscError. Format engines that
        support subdirectories (e.g. ADFS) override this method.
        """
        raise DiscError("Subdirectories are not supported on this disc format")

    def compact(self) -> int:
        """Defragment file storage by closing gaps between files.

        Default implementation raises DiscError. Format engines that
        support compaction (e.g. DFS) override this method. Returns
        the number of bytes freed when implemented.
        """
        raise DiscError("Compaction is not supported on this disc format")

    @abstractmethod
    def __iter__(self) -> Iterator[DiscEntry]:
        """Yield catalogue entries for this side."""

    @abstractmethod
    def __len__(self) -> int:
        """Number of catalogue entries on this side."""

    @abstractmethod
    def __getitem__(self, key: str) -> DiscEntry:
        """Look up a catalogue entry by full path."""

    @abstractmethod
    def __contains__(self, key: object) -> bool:
        """True if an entry with the given full path exists."""


# -----------------------------------------------------------------------
# DiscImage ABC
# -----------------------------------------------------------------------

class DiscImage(ABC):
    """A complete disc image container.

    Owns the backing bytes for a disc image and exposes one DiscSide
    view per physical side. Supports iteration, indexing, and length
    over the sides, serialization back to bytes, and use as a
    context manager (enter returns self, exit is a no-op since
    in-memory images have no resources to release).
    """

    @property
    @abstractmethod
    def sides(self) -> List[DiscSide]:
        """List of side readers, one per available disc side."""

    @abstractmethod
    def serialize(self) -> bytes:
        """Return the disc image as immutable bytes for writing to a file."""

    @abstractmethod
    def __iter__(self) -> Iterator[DiscSide]:
        """Yield each side of the disc image."""

    @abstractmethod
    def __len__(self) -> int:
        """Number of sides (1 for SSD/ADFS, 2 for DSD/ADL)."""

    @abstractmethod
    def __getitem__(self, index: int) -> DiscSide:
        """Return the side at the given index."""

    def __enter__(self) -> "DiscImage":
        """Enter a context manager block. Returns self."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit a context manager block. No-op for in-memory images."""
        pass
