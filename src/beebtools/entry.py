# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Shared types for disc image entries, sides, and images.

Defines the hierarchy that every disc format engine builds on:

    DiscEntry     -- one catalogue entry (file or directory)
    DiscCatalogue -- a full disc catalogue
    DiscFile      -- file content and metadata transport object
    DiscSide      -- one side of a disc image (read and mutation API)
    DiscImage     -- a complete disc image container
    FileType      -- classification of extracted file content

This is a Contracts-layer module - no internal imports beyond boot.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum, IntFlag
from typing import Iterator, List, Optional, Tuple, Union

from .boot import BootOption


# -----------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------

class DiscError(Exception):
    """Base exception for all beebtools disc operations."""


class DiscFormatError(DiscError):
    """Raised when disc image data is corrupt or unreadable."""


# -----------------------------------------------------------------------
# File classification
# -----------------------------------------------------------------------

class FileType(Enum):
    """Classification of an extracted disc file by content and metadata.

    Produced by `classifyFileType()` and carried on `ExtractedFile`
    and `CatalogueEntry`. The `.value` of each member is the
    historical display string used by the CLI.

    Members:

        BASIC     -- Valid tokenized BBC BASIC program. Exec address
                     is a standard BASIC entry point (low word is
                     &801F, &8023, or &802B) and the bytes parse as
                     a complete tokenized program.

        BASIC_MC  -- BASIC program with trailing machine code. The
                     tokenized BASIC portion ends before the end of
                     the file; the remainder is a binary payload
                     (usually machine code that the BASIC portion
                     acts as a loader for).

        BASIC_ISH -- Looks like BASIC along one axis (metadata or
                     content) but not the other. Two real-world
                     cases fall into this bucket:

                     1. Standard BASIC exec address, but the bytes
                        are not tokenized. Usually a corrupt file,
                        or a hand-authored file saved with a BASIC
                        exec address by mistake.

                     2. Non-standard exec address, but the bytes
                        ARE valid tokenized BASIC. Usually a
                        deliberately-marked "include" file - a
                        BASIC snippet meant to be merged into
                        another program via a programmatic loading mechanism 
                        or loaded with LOAD rather than run directly.

                     Case 2 cannot be produced from the BASIC
                     prompt - SAVE always stamps a standard BASIC
                     exec. It requires *SAVE with explicit exec
                     and load addresses, e.g.

                         *SAVE name 1900 +A3 380E7 30E00

                     LOAD "name" still works on such files (LOAD
                     ignores the exec address and streams bytes
                     into PAGE); *RUN and CHAIN do not.

        TEXT      -- Plain ASCII text file.

        BINARY    -- Everything else: machine code, data, graphics,
                     or anything the classifier does not recognise.
    """
    BASIC     = "BASIC"
    BASIC_MC  = "BASIC+MC"
    BASIC_ISH = "BASIC?"
    TEXT      = "TEXT"
    BINARY    = "BINARY"

    def __str__(self) -> str:
        """Return the display string (e.g. 'BASIC', 'BASIC+MC').

        Overrides Enum's default `"FileType.BASIC"` repr-style
        output so that `f"{ft}"` and `str(ft)` render the same
        short labels the CLI and library users expect.
        """
        return self.value


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
    start_sector: int

    @property
    def accessByte(self) -> int:
        """Return the 8-bit access byte for this entry.

        Default implementation returns 0x08 if locked, 0x00 otherwise.
        Format engines with richer access bits (ADFS) override this
        to return the full access byte from the directory entry.
        """

        return 0x08 if self.locked else 0x00

    @property
    @abstractmethod
    def accessFlags(self) -> IntFlag:
        """Return the entry's access bits as a format-specific IntFlag.

        The concrete instance is the format's own ``IntFlag`` subclass.
        Callers at higher layers treat the value as the abstract base
        and round-trip it through :meth:`DiscSide.applyAccess` without
        needing to know the concrete subclass.
        """

    @property
    @abstractmethod
    def accessString(self) -> str:
        """Return the entry's access bits as a human-readable string.

        The format chooses its own letter vocabulary and ordering.
        Empty string when no bits are set. Intended for display only;
        not parsed back as input.
        """

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
    Exposes a tracks count derived from the sector count.
    """

    title: str
    cycle: int
    boot_option: BootOption
    disc_size: int
    entries: Tuple[DiscEntry, ...]

    @property
    @abstractmethod
    def tracks(self) -> int:
        """Number of tracks represented by disc_size.

        Computed as `disc_size // sectors_per_track`, where
        sectors_per_track is a format-specific constant. For example,
        a format laid out as 10 sectors per track returns
        `disc_size // 10`.
        """


# -----------------------------------------------------------------------
# File data transport
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class DiscFile:
    """Transport object for file content and metadata.

    Bundles everything needed to add a file to a disc image into a
    single value. Format engines extract what they need from path:
    DFS splits it into directory and name, ADFS uses it directly.

    When ``start_sector`` is set, format engines that support placed
    writes use the exact sector rather than running free-space
    allocation. This preserves the original on-disc layout for
    byte-exact rebuilds and for round-tripping copy-protected discs
    that declare overlapping sector allocations (typically Level 9
    games). A value of None means the engine picks a sector normally.

    ``access`` carries the full format-native access byte when the
    caller has one (typically from an ``.inf`` sidecar). When None,
    format engines fall back to their sensible default - ADFS uses
    owner R+W (plus L if ``locked``); DFS uses L bit 3 if ``locked``.
    ``locked`` is kept for callers that only care about the lock flag.
    """

    path: str
    data: bytes
    load_addr: int = 0
    exec_addr: int = 0
    locked: bool = False
    start_sector: Optional[int] = None
    access: Optional[int] = None


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

    @abstractmethod
    def applyAccess(self, entry: 'DiscEntry', access: Union[IntFlag, str]) -> None:
        """Apply an access change to an entry on disc.

        Accepts two input shapes:

        * An ``IntFlag`` instance of this format's own access-flags
          subclass. Treated as an absolute replacement: the flag
          value becomes the entry's new on-disc access byte.

        * A ``str`` spec in the format's own grammar. The spec is
          parsed and composed against the entry's current access
          byte to produce the new value.

        Format-specific invariants are enforced here. Invalid input
        raises ``DiscError``; soft errors emit ``BeebToolsWarning``
        and are stripped from the effective value. Passing an
        ``IntFlag`` of the wrong format-subclass raises
        ``ValueError``.

        The entry is rewritten to disc as a side effect.
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

    def __iter__(self) -> Iterator[DiscEntry]:
        """Yield catalogue entries for this side.

        Default implementation iterates the tuple returned by
        `readCatalogue()`.
        """
        return iter(self.readCatalogue().entries)

    def __len__(self) -> int:
        """Number of catalogue entries on this side.

        Default implementation returns the length of the tuple
        returned by `readCatalogue()`.
        """
        return len(self.readCatalogue().entries)

    def __getitem__(self, key: str) -> DiscEntry:
        """Look up a catalogue entry by full path.

        Default implementation scans `readCatalogue().entries` and
        raises `KeyError` if no entry matches.
        """
        for entry in self.readCatalogue().entries:
            if entry.fullName == key:
                return entry

        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        """True if an entry with the given full path exists.

        Default implementation scans `readCatalogue().entries`.
        Non-string keys always return False.
        """
        if not isinstance(key, str):
            return False

        return any(e.fullName == key for e in self.readCatalogue().entries)


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

    def save(self, path: str) -> int:
        """Serialize the disc image and write it to disk.

        Default implementation calls serialize() and writes the
        resulting bytes to the given path. Returns the number of
        bytes written.
        """
        data = self.serialize()

        with open(path, "wb") as f:
            f.write(data)

        return len(data)

    def __iter__(self) -> Iterator[DiscSide]:
        """Yield each side of the disc image.

        Default implementation iterates `self.sides`.
        """
        return iter(self.sides)

    def __len__(self) -> int:
        """Number of sides (1 for SSD/ADFS, 2 for DSD/ADL).

        Default implementation returns the length of `self.sides`.
        """
        return len(self.sides)

    def __getitem__(self, index: int) -> DiscSide:
        """Return the side at the given index.

        Default implementation indexes into `self.sides`.
        """
        return self.sides[index]

    def __enter__(self) -> "DiscImage":
        """Enter a context manager block. Returns self."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit a context manager block. No-op for in-memory images."""
        pass
