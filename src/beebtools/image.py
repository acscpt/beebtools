# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Disc image protocols and format auto-detection.

Defines the DiscSide and DiscImage structural typing Protocols that
DFSSide/ADFSSide and DFSImage/ADFSImage satisfy without inheriting.
Upper layers (disc.py, cli.py) type-hint against these Protocols to
work uniformly with either format.

Also provides openImage() which detects the filing system format from
the file extension and delegates to the appropriate format-specific
opener, and createImage() which creates a blank formatted disc image
from the extension. This keeps the DFS and ADFS modules independent of
each other.

Supported formats:
    .ssd  -- DFS single-sided
    .dsd  -- DFS double-sided interleaved
    .adf  -- ADFS single-sided (old map, small directory)
    .adl  -- ADFS double-sided (old map, small directory)
"""

from typing import Any, Iterator, List, Optional, Protocol, runtime_checkable

from .entry import DiscCatalogue, DiscEntry, DiscFile

from .adfs import (
    ADFSImage, ADFSFormatError, openAdfsImage,
    createAdfsImage, ADFS_S_SECTORS, ADFS_M_SECTORS, ADFS_L_SECTORS,
)
from .boot import BootOption
from .dfs import DFSImage, DFSFormatError, openDiscImage, createDiscImage


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

    def writeCatalogue(self, catalogue: DiscCatalogue) -> None:
        """Write a modified catalogue back to the disc image.

        Encodes the catalogue fields and writes them to the appropriate
        sectors. Clears any cached catalogue so the next read re-parses.
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


def createImage(
    output_path: str,
    tracks: int = 80,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
) -> DiscImage:
    """Create a blank formatted disc image.

    Format is determined from the output path extension:
        .ssd  -- DFS single-sided
        .dsd  -- DFS double-sided interleaved
        .adf  -- ADFS (40-track: ADFS-S 160K, 80-track: ADFS-M 320K)
        .adl  -- ADFS-L 640K

    Args:
        output_path: Path whose extension determines the format.
        tracks:      Number of tracks (40 or 80). Controls DFS track
                     count and ADFS image size for .adf files.
        title:       Disc title (up to 12 characters).
        boot_option: Boot option (0-3).

    Returns:
        A blank DFSImage or ADFSImage depending on the extension.

    Raises:
        DFSFormatError: If the extension is unrecognised.
    """
    ext = _extractExtension(output_path)

    if ext in _DFS_EXTENSIONS:
        # Create a DFS image. DSD flag determined by extension.
        is_dsd = (ext == ".dsd")
        return createDiscImage(
            tracks=tracks, is_dsd=is_dsd,
            title=title, boot_option=boot_option,
        )

    if ext in _ADFS_EXTENSIONS:
        # Map tracks and extension to the correct ADFS sector count.
        if ext == ".adl":
            total_sectors = ADFS_L_SECTORS
        elif tracks == 40:
            total_sectors = ADFS_S_SECTORS
        else:
            total_sectors = ADFS_M_SECTORS
        return createAdfsImage(
            total_sectors=total_sectors,
            title=title, boot_option=boot_option,
        )

    raise DFSFormatError(
        f"Unrecognised disc image extension '{ext}'. "
        f"Expected .ssd, .dsd, .adf, or .adl"
    )
