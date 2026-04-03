# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Disc image protocols and format auto-detection.

Defines the DiscSide and DiscImage structural typing Protocols that
DFSSide/ADFSSide and DFSImage/ADFSImage satisfy without inheriting.
Upper layers (disc.py, cli.py) type-hint against these Protocols to
work uniformly with either format.

Also provides openImage() which detects the filing system format from
the file extension and delegates to the appropriate format-specific
opener. This keeps the DFS and ADFS modules independent of each other.

Supported formats:
    .ssd  -- DFS single-sided
    .dsd  -- DFS double-sided interleaved
    .adf  -- ADFS single-sided (old map, small directory)
    .adl  -- ADFS double-sided (old map, small directory)
"""

from typing import Any, Iterator, List, Optional, Protocol, runtime_checkable

from .entry import DiscCatalogue, DiscEntry, DiscFile

from .adfs import ADFSImage, ADFSFormatError, openAdfsImage
from .dfs import DFSImage, DFSFormatError, openDiscImage


# -----------------------------------------------------------------------
# Protocols
# -----------------------------------------------------------------------

@runtime_checkable
class DiscSide(Protocol):
    """Structural Protocol for one side of a disc image.

    Both DFSSide and ADFSSide satisfy this Protocol. Upper layers
    can type-hint against DiscSide to work uniformly with either
    format without importing the concrete classes.
    """

    @property
    def side(self) -> int:
        """Side number (0 or 1)."""
        ...

    def readCatalogue(self) -> DiscCatalogue:
        """Parse and return the disc catalogue.

        Returns a catalogue with title, cycle, boot_option, disc_size,
        and entries attributes.
        """
        ...

    def readFile(self, entry: DiscEntry) -> bytes:
        """Read the contents of a file from disc."""
        ...

    def writeFile(self, entry: DiscEntry, data: bytes) -> None:
        """Write file data to the sectors allocated for an entry."""
        ...

    def freeSpace(self) -> int:
        """Return the amount of free space on this side."""
        ...

    def addFile(self, spec: DiscFile) -> DiscEntry:
        """Add a file to this disc side.

        Returns the catalogue entry created for the new file.
        """
        ...

    def deleteFile(self, path: str) -> None:
        """Remove a file from the catalogue by its full path."""
        ...

    def __iter__(self) -> Iterator[DiscEntry]: ...

    def __len__(self) -> int: ...

    def __getitem__(self, key: str) -> DiscEntry: ...

    def __contains__(self, key: object) -> bool: ...

    def __repr__(self) -> str: ...


@runtime_checkable
class DiscImage(Protocol):
    """Structural Protocol for a complete disc image.

    Both DFSImage and ADFSImage satisfy this Protocol. Provides
    access to the per-side readers and serialization.
    """

    @property
    def sides(self) -> List[DiscSide]:
        """List of side readers, one per available disc side."""
        ...

    def serialize(self) -> bytes:
        """Return the disc image as immutable bytes."""
        ...

    def __iter__(self) -> Iterator[Any]: ...

    def __len__(self) -> int: ...

    def __getitem__(self, index: int) -> DiscSide: ...

    def __enter__(self) -> "DiscImage": ...

    def __exit__(self, *exc: object) -> None: ...

    def __repr__(self) -> str: ...


# Extension-to-format mapping. Extensions are matched case-insensitively.
_DFS_EXTENSIONS = {".ssd", ".dsd"}
_ADFS_EXTENSIONS = {".adf", ".adl"}


def openImage(path: str) -> DiscImage:
    """Open a disc image file, detecting the format automatically.

    Format is inferred from the file extension:
        .ssd / .dsd  -- Acorn DFS
        .adf / .adl  -- Acorn ADFS (old map, small directory)

    Args:
        path: Path to a disc image file.

    Returns:
        A DFSImage or ADFSImage depending on the detected format.

    Raises:
        DFSFormatError: If the image format cannot be determined.
        ADFSFormatError: If an ADFS image is structurally invalid.
        FileNotFoundError: If the path does not exist.
    """
    ext = _extractExtension(path)

    if ext in _DFS_EXTENSIONS:
        return openDiscImage(path)

    if ext in _ADFS_EXTENSIONS:
        return openAdfsImage(path)

    raise DFSFormatError(
        f"Unrecognised disc image extension '{ext}'. "
        f"Expected .ssd, .dsd, .adf, or .adl"
    )


def _extractExtension(path: str) -> str:
    """Extract the lowercase file extension from a path.

    Handles compound extensions like .gz by returning only the final
    extension component.
    """
    # Find the last dot in the filename portion (not directory separators).
    name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = name.rfind(".")

    if dot < 0:
        return ""

    return name[dot:].lower()
